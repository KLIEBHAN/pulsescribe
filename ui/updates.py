"""Native update dialogs shared by the menu bar and Windows tray."""

import sys

from utils.update_flow import UpdateFlow, UpdateState


def show_update_message(
    title: str,
    message: str,
    *,
    confirm: bool = False,
    action_label: str = "Restart and Install",
) -> bool:
    if sys.platform == "darwin":
        from AppKit import NSAlert, NSAlertFirstButtonReturn

        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_(action_label if confirm else "OK")
        if confirm:
            alert.addButtonWithTitle_("Later")
        return alert.runModal() == NSAlertFirstButtonReturn
    if sys.platform == "win32":
        import ctypes

        flags = 0x00000004 | 0x00000020 | 0x00000100 if confirm else 0x00000040
        return ctypes.windll.user32.MessageBoxW(None, message, title, flags) == (
            6 if confirm else 1
        )
    return False


def update_feedback(state: UpdateState) -> None:
    """Automatic checks remain quiet; explicit checks report their outcome."""
    if not state.manual:
        return
    if state.status == "idle":
        show_update_message(
            "PulseScribe Updates", f"PulseScribe {state.current} is up to date."
        )
    elif state.status == "error":
        show_update_message("PulseScribe Update Failed", state.error)


def activate_update(flow: UpdateFlow, is_idle, install_if_idle) -> None:
    """The menu action checks, retries, or confirms a prepared update."""
    state = flow.state
    if state.status == "ready" and state.offer:
        if not is_idle():
            show_update_message(
                "PulseScribe Updates",
                "Finish the current dictation before installing the update.",
            )
            return
        details = f"Installed: {state.current}\nUpdate: {state.offer.version}\n\n{state.offer.notes[:12000]}\n\n{state.hint}"
        if not show_update_message(
            f"PulseScribe {state.offer.version}",
            details,
            confirm=True,
            action_label="Restart and Install" if state.automatic else "Open Installer",
        ):
            return
        if not is_idle():
            show_update_message(
                "PulseScribe Updates",
                "Finish the current dictation before installing the update.",
            )
            return
        try:
            if not install_if_idle():
                show_update_message(
                    "PulseScribe Updates",
                    "Finish the current dictation before installing the update.",
                )
        except Exception as error:
            show_update_message("PulseScribe Update Failed", str(error))
            return
    elif state.status == "available" or (
        state.status == "error" and state.error_context == "download"
    ):
        flow.download()
    else:
        flow.check(manual=True)
