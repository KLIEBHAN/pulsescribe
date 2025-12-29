"""File-based IPC for Windows subprocess communication.

Enables communication between the Windows daemon and the onboarding wizard subprocess
for test dictation functionality. Uses JSON files as a simple, dependency-free IPC mechanism.

Protocol:
- Wizard writes commands to ipc_command.json
- Daemon polls for commands, processes them, writes responses to ipc_response.json
- Wizard polls for responses matching its command ID
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

# IPC file locations
IPC_COMMAND_FILE = USER_CONFIG_DIR / "ipc_command.json"
IPC_RESPONSE_FILE = USER_CONFIG_DIR / "ipc_response.json"

# Commands
CMD_START_TEST = "start_test"
CMD_STOP_TEST = "stop_test"

# Response statuses
STATUS_RECORDING = "recording"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_STOPPED = "stopped"


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically to avoid partial reads."""
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(data), encoding="utf-8")
        tmp_path.replace(path)
    except Exception as e:
        logger.warning(f"IPC atomic write failed: {e}")
        # Fallback to direct write
        path.write_text(json.dumps(data), encoding="utf-8")


def _safe_read(path: Path) -> dict | None:
    """Read JSON safely, return None on any error."""
    try:
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return json.loads(content)
    except Exception as e:
        logger.debug(f"IPC read failed: {e}")
    return None


class IPCClient:
    """IPC client for the wizard subprocess.

    Sends commands to the daemon and polls for responses.
    """

    def __init__(self) -> None:
        self._last_cmd_id: str | None = None

    def send_command(self, command: str) -> str:
        """Send a command to the daemon.

        Args:
            command: Command to send (CMD_START_TEST or CMD_STOP_TEST)

        Returns:
            Command ID for tracking the response
        """
        cmd_id = str(uuid.uuid4())[:8]  # Short ID for readability
        data = {
            "id": cmd_id,
            "command": command,
            "timestamp": time.time(),
        }
        _atomic_write(IPC_COMMAND_FILE, data)
        self._last_cmd_id = cmd_id
        logger.debug(f"IPC command sent: {command} (id={cmd_id})")
        return cmd_id

    def poll_response(self, cmd_id: str, timeout: float = 0.1) -> dict | None:
        """Poll for a response matching the command ID.

        Args:
            cmd_id: Command ID to match
            timeout: Not used (kept for API compatibility)

        Returns:
            Response dict if found, None otherwise
        """
        response = _safe_read(IPC_RESPONSE_FILE)
        if response and response.get("id") == cmd_id:
            return response
        return None

    def clear_response(self) -> None:
        """Clear the response file after processing."""
        try:
            if IPC_RESPONSE_FILE.exists():
                IPC_RESPONSE_FILE.unlink()
        except Exception:
            pass


class IPCServer:
    """IPC server for the daemon.

    Polls for commands and invokes callbacks. Runs in a background thread.
    """

    def __init__(self, on_command: Callable[[str, str], None]) -> None:
        """Initialize the IPC server.

        Args:
            on_command: Callback(cmd_id, command) invoked when a command is received
        """
        self._on_command = on_command
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_processed_id: str | None = None
        self._poll_interval = 0.2  # 200ms

    def start(self) -> None:
        """Start the IPC server in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("IPC server started")

    def stop(self) -> None:
        """Stop the IPC server."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        # Clean up IPC files
        self._cleanup_files()
        logger.info("IPC server stopped")

    def send_response(
        self,
        cmd_id: str,
        status: str,
        transcript: str = "",
        error: str | None = None,
    ) -> None:
        """Send a response to the wizard.

        Args:
            cmd_id: Command ID this response is for
            status: Response status (STATUS_* constants)
            transcript: Transcribed text (for STATUS_DONE)
            error: Error message (for STATUS_ERROR)
        """
        data = {
            "id": cmd_id,
            "status": status,
            "transcript": transcript,
            "error": error,
            "timestamp": time.time(),
        }
        _atomic_write(IPC_RESPONSE_FILE, data)
        logger.debug(f"IPC response sent: {status} (id={cmd_id})")

    def _poll_loop(self) -> None:
        """Background thread polling for commands."""
        while self._running:
            try:
                command = _safe_read(IPC_COMMAND_FILE)
                if command:
                    cmd_id = command.get("id")
                    cmd_type = command.get("command")

                    # Only process new commands
                    if cmd_id and cmd_type and cmd_id != self._last_processed_id:
                        self._last_processed_id = cmd_id
                        logger.debug(f"IPC command received: {cmd_type} (id={cmd_id})")

                        # Clear command file to prevent re-processing
                        try:
                            IPC_COMMAND_FILE.unlink()
                        except Exception:
                            pass

                        # Invoke callback
                        if callable(self._on_command):
                            try:
                                self._on_command(cmd_id, cmd_type)
                            except Exception as e:
                                logger.error(f"IPC command handler error: {e}")
                                self.send_response(cmd_id, STATUS_ERROR, error=str(e))

            except Exception as e:
                logger.debug(f"IPC poll error: {e}")

            time.sleep(self._poll_interval)

    def _cleanup_files(self) -> None:
        """Remove IPC files on shutdown."""
        for path in (IPC_COMMAND_FILE, IPC_RESPONSE_FILE):
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
