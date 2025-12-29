"""File-based IPC for Windows subprocess communication.

Why file-based IPC?
- The onboarding wizard runs as a separate subprocess (for PyInstaller compatibility)
- Named pipes and sockets add Windows-specific complexity
- JSON files provide a simple, debuggable, dependency-free mechanism

Protocol:
    Wizard                          Daemon
       │                               │
       │──── write command.json ──────►│
       │                               │ (polls every 200ms)
       │◄─── write response.json ─────│
       │ (polls every 200ms)           │
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from config import USER_CONFIG_DIR

logger = logging.getLogger("pulsescribe.ipc")

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# IPC file locations (in ~/.pulsescribe/)
IPC_COMMAND_FILE = USER_CONFIG_DIR / "ipc_command.json"
IPC_RESPONSE_FILE = USER_CONFIG_DIR / "ipc_response.json"

# Polling interval for both client and server
POLL_INTERVAL_SECONDS = 0.2

# -----------------------------------------------------------------------------
# Protocol Constants
# -----------------------------------------------------------------------------

# Commands (Wizard → Daemon)
CMD_START_TEST = "start_test"
CMD_STOP_TEST = "stop_test"

# Response statuses (Daemon → Wizard)
STATUS_RECORDING = "recording"  # Microphone activated, ready for speech
STATUS_DONE = "done"  # Transcription complete, transcript included
STATUS_ERROR = "error"  # Something went wrong, error message included
STATUS_STOPPED = "stopped"  # User cancelled the recording


# -----------------------------------------------------------------------------
# File I/O Helpers
# -----------------------------------------------------------------------------


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically using tmp-file-then-rename pattern.

    Why atomic writes?
    Both processes poll files continuously. A non-atomic write could
    result in the reader seeing a partial/corrupt JSON file.
    """
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(data), encoding="utf-8")
        tmp_path.replace(path)  # Atomic on most filesystems
    except Exception as e:
        logger.warning(f"IPC atomic write failed: {e}")
        path.write_text(json.dumps(data), encoding="utf-8")


def _safe_read(path: Path) -> dict | None:
    """Read JSON file, returning None if missing, empty, or corrupt.

    Silently handles all error cases since missing/corrupt files are
    normal during the polling lifecycle (file may not exist yet, or
    may be mid-write).
    """
    try:
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return json.loads(content)
    except Exception as e:
        logger.debug(f"IPC read failed: {e}")
    return None


# -----------------------------------------------------------------------------
# IPCClient - Used by the Wizard subprocess
# -----------------------------------------------------------------------------


class IPCClient:
    """Sends commands to daemon and polls for responses.

    Usage:
        client = IPCClient()
        cmd_id = client.send_command(CMD_START_TEST)
        # ... poll in a timer loop ...
        response = client.poll_response(cmd_id)
        if response:
            handle_response(response)
    """

    def __init__(self) -> None:
        self._last_cmd_id: str | None = None

    def send_command(self, command: str) -> str:
        """Write a command to the IPC file for the daemon to pick up.

        Returns a short UUID to correlate with the eventual response.
        """
        cmd_id = str(uuid.uuid4())[:8]  # Short ID for log readability
        _atomic_write(
            IPC_COMMAND_FILE,
            {
                "id": cmd_id,
                "command": command,
                "timestamp": time.time(),
            },
        )
        self._last_cmd_id = cmd_id
        logger.debug(f"IPC command sent: {command} (id={cmd_id})")
        return cmd_id

    def poll_response(self, cmd_id: str, timeout: float = 0.1) -> dict | None:
        """Check if daemon has written a response for our command.

        Returns the response dict if available, None otherwise.
        Caller should poll this repeatedly (e.g., via QTimer).
        """
        response = _safe_read(IPC_RESPONSE_FILE)
        if response and response.get("id") == cmd_id:
            return response
        return None

    def clear_response(self) -> None:
        """Delete response file after processing (cleanup)."""
        try:
            if IPC_RESPONSE_FILE.exists():
                IPC_RESPONSE_FILE.unlink()
        except Exception:
            pass  # Best-effort cleanup


# -----------------------------------------------------------------------------
# IPCServer - Used by the Daemon process
# -----------------------------------------------------------------------------


class IPCServer:
    """Polls for wizard commands and dispatches to handler callback.

    Runs a background thread that checks for new commands every 200ms.
    When a command arrives, it invokes the callback and manages the
    response lifecycle.

    Usage:
        def handle_command(cmd_id: str, command: str) -> None:
            if command == CMD_START_TEST:
                start_recording()
                server.send_response(cmd_id, STATUS_RECORDING)

        server = IPCServer(on_command=handle_command)
        server.start()
        # ... later ...
        server.stop()
    """

    def __init__(self, on_command: Callable[[str, str], None]) -> None:
        """Create server with command handler callback.

        The callback receives (cmd_id, command) and should call
        send_response() to communicate results back to the wizard.
        """
        self._on_command = on_command
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_processed_id: str | None = None  # Prevents duplicate processing

    def start(self) -> None:
        """Start background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("IPC server started")

    def stop(self) -> None:
        """Stop polling and clean up IPC files."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._cleanup_files()
        logger.info("IPC server stopped")

    def send_response(
        self,
        cmd_id: str,
        status: str,
        transcript: str = "",
        error: str | None = None,
    ) -> None:
        """Write response for wizard to pick up.

        Call this from your command handler to communicate status
        changes (recording started, done, error) back to the wizard.
        """
        _atomic_write(
            IPC_RESPONSE_FILE,
            {
                "id": cmd_id,
                "status": status,
                "transcript": transcript,
                "error": error,
                "timestamp": time.time(),
            },
        )
        logger.debug(f"IPC response sent: {status} (id={cmd_id})")

    def _poll_loop(self) -> None:
        """Background thread: check for commands, dispatch to handler."""
        while self._running:
            self._process_pending_command()
            time.sleep(POLL_INTERVAL_SECONDS)

    def _process_pending_command(self) -> None:
        """Check for and process a single pending command."""
        try:
            command = _safe_read(IPC_COMMAND_FILE)
            if not command:
                return

            cmd_id = command.get("id")
            cmd_type = command.get("command")

            # Skip if already processed (deduplication)
            if not cmd_id or not cmd_type or cmd_id == self._last_processed_id:
                return

            self._last_processed_id = cmd_id
            logger.debug(f"IPC command received: {cmd_type} (id={cmd_id})")

            # Remove command file to prevent re-processing on next poll
            self._delete_file(IPC_COMMAND_FILE)

            # Dispatch to handler
            self._invoke_handler(cmd_id, cmd_type)

        except Exception as e:
            logger.debug(f"IPC poll error: {e}")

    def _invoke_handler(self, cmd_id: str, cmd_type: str) -> None:
        """Call the command handler, sending error response on failure."""
        try:
            self._on_command(cmd_id, cmd_type)
        except Exception as e:
            logger.error(f"IPC command handler error: {e}")
            self.send_response(cmd_id, STATUS_ERROR, error=str(e))

    def _delete_file(self, path: Path) -> None:
        """Delete file if it exists (best-effort)."""
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def _cleanup_files(self) -> None:
        """Remove IPC files on shutdown."""
        self._delete_file(IPC_COMMAND_FILE)
        self._delete_file(IPC_RESPONSE_FILE)
