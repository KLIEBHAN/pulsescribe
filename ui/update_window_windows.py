"""Daemon side of the Windows update window (see ``ui/update_window_qt.py``).

``WindowsUpdateWindow(launch, on_action)`` starts the window process on the
first ``show`` and forwards views over its stdin. ``launch(activate)`` must
return a ``subprocess.Popen`` with ``stdin`` and ``stdout`` pipes;
``on_action`` runs on a reader thread for every button press, and receives
``unavailable`` when the current window process ends without the user closing
it (start failure or crash). All methods are thread-safe; ``close()`` ends the
process on purpose (shutdown, installs).
"""

from __future__ import annotations

import json
import logging
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
        self._closed = weakref.WeakSet()  # Processes this side ended on purpose

    def show(self, view: UpdateView, *, activate: bool) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                process = self._launch(activate)
                self._process = process
                threading.Thread(
                    target=self._read_actions,
                    args=(process,),
                    name="UpdateWindowActions",
                    daemon=True,
                ).start()
            self._send(process, {"view": view.to_dict(), "activate": activate})

    def render(self, view: UpdateView) -> None:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                self._send(process, {"view": view.to_dict(), "activate": None})

    def hide(self) -> None:
        self.close()

    def close(self, timeout: float = 2.0) -> None:
        """Close stdin so the window process exits; terminate it if it hangs."""
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        self._closed.add(process)
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=timeout)
        except Exception:
            process.terminate()

    def _send(self, process, message: dict) -> None:
        try:
            process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            process.stdin.flush()
        except (OSError, ValueError):
            logger.debug("Update window process is gone", exc_info=True)
            if self._process is process:
                self._process = None

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
                self._on_action(action)
        except (OSError, ValueError):
            pass
        with self._lock:
            # A newer window may already run; its own reader reports its fate.
            superseded = self._process is not None and self._process is not process
        if not closed_by_user and not superseded and process not in self._closed:
            logger.warning("Update window process ended unexpectedly")
            self._on_action("unavailable")


__all__ = ["WindowsUpdateWindow"]
