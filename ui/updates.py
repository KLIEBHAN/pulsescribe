"""Update window controller shared by the macOS menu bar and the Windows tray.

``UpdateUI`` receives every ``UpdateState`` from ``UpdateFlow.publish``, opens
the platform window once per newly found version (without taking focus),
serves the "Check for Updates…" menu action via ``open()`` and handles the
window buttons via ``handle(action)``.

The window object needs ``show(view, *, activate)``, ``render(view)`` (no-op
while hidden) and ``hide()``. A window that can fail (the Windows process)
also exposes ``failed``; while it is true, actionable states go to the
``on_window_error`` fallback instead.

Lock order: the Windows window may call into this controller while holding
its lock, so this controller never touches the window under ``self._lock``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

from ui.update_window_model import UpdateView, build_update_view
from utils.update_flow import UpdateFlow, UpdateState

logger = logging.getLogger("pulsescribe.updates")

BUSY_NOTICE = "Finish the current dictation before installing the update."
# Automatic checks open the window when a new version starts downloading
# (or, for manual macOS installs, becomes available).
AUTO_OPEN_STATUSES = ("available", "downloading", "ready")


class UpdateUI:
    def __init__(
        self,
        flow: UpdateFlow,
        window,
        is_idle,
        install_if_idle,
        *,
        on_window_error=None,
    ):
        self._flow = flow
        self._window = window
        self._is_idle = is_idle
        self._install_if_idle = install_if_idle
        self._on_window_error = on_window_error
        self._lock = threading.Lock()
        self._announced: set[str] = set()
        self._notice = ""
        self._last_status = flow.state.status

    def publish(self, state: UpdateState) -> None:
        """Render a new flow state; may run on any thread the platform allows."""
        window_failed = self._window_failed()  # Never under self._lock
        with self._lock:
            status_changed = state.status != self._last_status
            if status_changed:
                self._notice = ""
                self._last_status = state.status
            announce = False
            if state.offer is not None and state.offer.version not in self._announced:
                self._announced.add(state.offer.version)
                announce = not state.manual and state.status in AUTO_OPEN_STATUSES
            view = build_update_view(state, self._notice)
            report_fallback = (
                window_failed
                and status_changed
                and self._on_window_error is not None
                and (
                    state.status in ("available", "ready")
                    or (state.manual and state.status in ("idle", "error"))
                )
            )
        # Re-read outside the lock: a concurrent show() may have relaunched the
        # window meanwhile (its prompt would revalidate the action anyway).
        if report_fallback and self._window_failed():
            self._on_window_error(view)
        elif announce:
            self._show(view, activate=False)
        else:
            self._render(view)

    def open(self) -> None:
        """Menu action: show the window and start whatever the state offers next."""
        state = self._flow.state
        needs_check = state.status == "idle" or (
            state.status == "error" and state.error_context != "download"
        )
        if needs_check:
            self._show(build_update_view(replace(state, status="checking")), activate=True)
            self._flow.check(manual=True)
            return
        self._show(self.current_view(), activate=True)
        if state.status == "available":
            self._flow.download()

    def handle(self, action: str) -> None:
        """Window button (install, download, retry, close) or ``unavailable``."""
        if action == "install":
            self._install()
        elif action == "download":
            self._flow.download()
        elif action == "retry":
            state = self._flow.state
            if state.error_context == "download" and state.offer is not None:
                self._flow.download()
            else:
                self._render(build_update_view(replace(state, status="checking")))
                self._flow.check(manual=True)
        elif action == "close":
            self._window.hide()
        elif action == "unavailable":
            # Runs under the Windows window lock (which also set ``failed``):
            # never call the window here.
            if self._on_window_error is not None:
                self._on_window_error(self.current_view())

    def _install(self) -> None:
        if not self._is_idle():
            self._set_notice(BUSY_NOTICE)
            return
        try:
            installed = self._install_if_idle()
        except Exception as error:
            logger.warning("Update installation could not start", exc_info=True)
            self._set_notice(f"The update could not start: {error}")
            return
        if not installed:
            self._set_notice(BUSY_NOTICE)

    def _set_notice(self, notice: str) -> None:
        with self._lock:
            self._notice = notice
        view = self.current_view()
        if self._window_failed() and self._on_window_error is not None:
            self._on_window_error(view)  # The window is gone; explain natively
        else:
            self._render(view)

    def current_view(self) -> UpdateView:
        """The view for the flow's current state (fallbacks revalidate with it)."""
        with self._lock:
            return build_update_view(self._flow.state, self._notice)

    def _window_failed(self) -> bool:
        """The window owns this state, changed atomically with show()."""
        return bool(getattr(self._window, "failed", False))

    def _show(self, view: UpdateView, *, activate: bool) -> None:
        try:
            self._window.show(view, activate=activate)
        except Exception:
            logger.warning("Update window unavailable", exc_info=True)
            if self._on_window_error is not None:
                self._on_window_error(view)

    def _render(self, view: UpdateView) -> None:
        try:
            self._window.render(view)
        except Exception:
            logger.warning("Could not update the update window", exc_info=True)


__all__ = ["BUSY_NOTICE", "UpdateUI"]
