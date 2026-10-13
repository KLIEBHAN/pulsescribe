"""Daemon side of the Windows update window (see ``ui/update_window_qt.py``).

``WindowsUpdateWindow(launch, on_action)`` starts the window process on the
first ``show`` and forwards views over its stdin. ``launch(activate)`` must
return a ``subprocess.Popen`` with ``stdin`` and ``stdout`` pipes;
Writes go through a per-process writer thread, so callers (tray menu, update
worker) never block on a starting or hung window. ``on_action`` runs on a
reader thread for every button press, and receives
``unavailable`` when the current window process ends without the user closing
it (start failure or crash). All methods are thread-safe; ``close()`` ends the
process on purpose (shutdown, installs).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import weakref

from ui.update_window_model import UpdateView

logger = logging.getLogger("pulsescribe.updates")

ACTION_PREFIX = b"PULSESCRIBE-UPDATE "


class WindowsUpdateWindow:
    def __init__(self, launch, on_action):
        self._launch = launch
        self._on_action = on_action
        self._lock = threading.Lock()
        self._process = None
        self._sent: dict | None = None  # Last view sent to the current process
        self._outbox: queue.SimpleQueue | None = None
        self._closed = weakref.WeakSet()  # Processes this side ended on purpose

    def show(self, view: UpdateView, *, activate: bool) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                process = self._launch(activate)
                self._process = process
                self._sent = None
                self._outbox = queue.SimpleQueue()
                threading.Thread(
                    target=self._read_actions,
                    args=(process,),
                    name="UpdateWindowActions",
                    daemon=True,
                ).start()
                threading.Thread(
                    target=self._write_messages,
                    args=(process, self._outbox),
                    name="UpdateWindowWriter",
                    daemon=True,
                ).start()
            self._send_view(process, view, activate)

    def render(self, view: UpdateView) -> None:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                self._send_view(process, view, None)

    def hide(self) -> None:
        self.close()

    def close(self, timeout: float = 2.0) -> None:
        """Close stdin so the window process exits; terminate it if it hangs."""
        with self._lock:
            process, self._process = self._process, None
            outbox, self._outbox = self._outbox, None
            if process is None:
                return
            self._closed.add(process)  # Before any reader can see it gone
        if outbox is not None:
            outbox.put(None)  # The writer closes stdin; the window then exits
        else:
            _close_stdin(process)
        try:
            process.wait(timeout=timeout)
            return
        except Exception:
            process.terminate()
        try:
            # The installer must not find files still locked by this process.
            process.wait(timeout=timeout)
        except Exception:
            logger.warning("Update window process did not exit", exc_info=True)

    def _send_view(self, process, view: UpdateView, activate: bool | None) -> None:
        """Send only changed fields; download progress must not resend the notes."""
        full = view.to_dict()
        sent = self._sent
        changes = full if sent is None else {
            key: value for key, value in full.items() if sent.get(key) != value
        }
        if not changes and activate is None:
            return
        message = {"view": changes, "activate": activate}
        self._outbox.put((json.dumps(message) + "\n").encode("utf-8"))
        self._sent = full

    def _write_messages(self, process, outbox: queue.SimpleQueue) -> None:
        while (line := outbox.get()) is not None:
            try:
                process.stdin.write(line)
                process.stdin.flush()
            except (OSError, ValueError):
                logger.debug("Update window process is gone", exc_info=True)
                with self._lock:
                    if self._process is process:
                        self._process = None
                        self._outbox = None
                return
        _close_stdin(process)

    def _read_actions(self, process) -> None:
        closed_by_user = False
        try:
            for line in process.stdout:
                if not line.startswith(ACTION_PREFIX):
                    continue
                try:
                    action = json.loads(line[len(ACTION_PREFIX):]).get("action")
                except (ValueError, AttributeError):
                    continue
                if action == "ready" or not isinstance(action, str):
                    continue  # "ready" is the startup handshake checked in CI
                closed_by_user = closed_by_user or action == "close"
                with self._lock:
                    current = self._process is process
                if current:  # Buffered actions of a replaced window are stale
                    self._on_action(action)
        except (OSError, ValueError):
            pass
        with self._lock:
            # A newer window may already run; its own reader reports its fate.
            superseded = self._process is not None and self._process is not process
        if not closed_by_user and not superseded and process not in self._closed:
            logger.warning("Update window process ended unexpectedly")
            self._on_action("unavailable")


def _close_stdin(process) -> None:
    try:
        process.stdin.close()
    except (OSError, ValueError):
        pass


__all__ = ["WindowsUpdateWindow"]
