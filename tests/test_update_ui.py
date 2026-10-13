from dataclasses import replace
from unittest.mock import Mock

import pytest

from ui import updates
from ui.update_window_model import (
    PROGRESS_INDETERMINATE,
    UpdateView,
    build_update_view,
    reflow_release_notes,
)
from utils.update_flow import UpdateState
from utils.updates import UpdateOffer

OFFER = UpdateOffer("1.4.0", "Notes", "installer", 1, "a" * 64)


class FakeWindow:
    def __init__(self):
        self.calls: list[tuple] = []

    def show(self, view, *, activate):
        self.calls.append(("show", view, activate))

    def render(self, view):
        self.calls.append(("render", view))

    def hide(self):
        self.calls.append(("hide",))

    @property
    def last_view(self) -> UpdateView:
        return next(call[1] for call in reversed(self.calls) if len(call) > 1)


def make_ui(state, *, idle=True, install=None):
    flow = Mock(state=state)
    window = FakeWindow()
    install_if_idle = install or Mock(return_value=True)
    ui = updates.UpdateUI(
        flow,
        window,
        idle if callable(idle) else (lambda: idle),
        install_if_idle,
    )
    return ui, flow, window, install_if_idle


def ready_state(**changes):
    return replace(
        UpdateState("1.3.0", status="ready", offer=OFFER, hint="Restart"), **changes
    )


# --- view model -------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "headline", "action", "enabled", "progress"),
    [
        (UpdateState("1.3.0", status="checking"), "Checking", "", True, PROGRESS_INDETERMINATE),
        (
            UpdateState("1.3.0", status="downloading", offer=OFFER, percent=42),
            "Downloading PulseScribe 1.4.0… 42%",
            "install",
            False,
            42,
        ),
        (ready_state(), "PulseScribe 1.4.0 is ready", "install", True, None),
        (
            UpdateState("1.3.0", status="available", offer=OFFER),
            "PulseScribe 1.4.0 is available",
            "download",
            True,
            None,
        ),
        (UpdateState("1.3.0", status="error", error="offline"), "Update failed", "retry", True, None),
        (UpdateState("1.3.0"), "PulseScribe 1.3.0 is up to date", "", True, None),
    ],
)
def test_view_shows_each_update_step(state, headline, action, enabled, progress):
    view = build_update_view(state)

    assert view.headline.startswith(headline)
    assert view.primary_action == action
    assert view.primary_enabled is enabled
    assert view.progress == progress
    assert bool(view.notes) is (state.offer is not None)


def test_manual_mac_install_offers_the_installer():
    view = build_update_view(ready_state(automatic=False))

    assert view.primary_label == "Open Installer"


def test_view_round_trips_for_the_windows_window_process():
    view = build_update_view(ready_state(), "notice")

    assert UpdateView.from_dict(view.to_dict()) == view


def test_reflow_joins_wrapped_paragraphs_but_keeps_lists_and_code():
    text = (
        "Title line\n\nFirst sentence\ncontinues here.\n\n"
        "- item one\n  wraps\n- item two\n\n```bash\nxattr -dr a\nb\n```\n"
    )

    assert reflow_release_notes(text) == (
        "Title line\n\nFirst sentence continues here.\n\n"
        "- item one wraps\n- item two\n\n```bash\nxattr -dr a\nb\n```"
    )


# --- controller -------------------------------------------------------------


def test_automatic_update_opens_once_without_focus():
    ui, _flow, window, _install = make_ui(UpdateState("1.3.0"))
    downloading = UpdateState("1.3.0", status="downloading", offer=OFFER, percent=1)

    ui.publish(downloading)
    ui.publish(replace(downloading, percent=50))
    ui.publish(ready_state())

    assert [call[0] for call in window.calls] == ["show", "render", "render"]
    assert window.calls[0][2] is False  # never steals focus from dictation


def test_manual_or_failed_checks_do_not_auto_open():
    ui, _flow, window, _install = make_ui(UpdateState("1.3.0"))

    ui.publish(UpdateState("1.3.0", status="error", error="offline"))
    ui.publish(UpdateState("1.3.0", status="downloading", offer=OFFER, manual=True))
    ui.publish(UpdateState("1.3.0", status="available", offer=OFFER))  # same version

    assert all(call[0] == "render" for call in window.calls)


def test_menu_shows_checking_immediately_and_starts_a_manual_check():
    ui, flow, window, _install = make_ui(UpdateState("1.3.0"))

    ui.open()

    assert window.calls[0][0] == "show" and window.calls[0][2] is True
    assert window.calls[0][1].headline == "Checking for updates…"
    flow.check.assert_called_once_with(manual=True)


def test_menu_starts_the_offered_download():
    ui, flow, window, _install = make_ui(
        UpdateState("1.3.0", status="available", offer=OFFER)
    )

    ui.open()

    flow.download.assert_called_once()
    flow.check.assert_not_called()
    assert window.last_view.primary_action == "download"


@pytest.mark.parametrize(
    ("context", "offer", "expected"), [("download", OFFER, "download"), ("check", None, "check")]
)
def test_retry_repeats_the_failed_step(context, offer, expected):
    state = UpdateState("1.3.0", status="error", error="x", error_context=context, offer=offer)
    ui, flow, _window, _install = make_ui(state)

    ui.handle("retry")

    getattr(flow, expected).assert_called_once()


def test_install_while_dictating_shows_notice_and_never_installs():
    ui, _flow, window, install = make_ui(ready_state(), idle=False)

    ui.handle("install")

    install.assert_not_called()
    assert window.last_view.notice == updates.BUSY_NOTICE


def test_install_rejected_by_atomic_reservation_shows_notice():
    ui, _flow, window, install = make_ui(ready_state(), install=Mock(return_value=False))

    ui.handle("install")

    install.assert_called_once()
    assert window.last_view.notice == updates.BUSY_NOTICE


def test_failed_helper_launch_keeps_app_running_and_explains():
    ui, _flow, window, install = make_ui(
        ready_state(), install=Mock(side_effect=OSError("no helper"))
    )

    ui.handle("install")

    install.assert_called_once()
    assert "no helper" in window.last_view.notice


def test_install_quits_through_the_daemon_when_idle():
    ui, _flow, window, install = make_ui(ready_state())

    ui.handle("install")

    install.assert_called_once()
    assert window.calls == []


def test_notice_clears_when_the_status_changes():
    ui, _flow, window, _install = make_ui(ready_state(), idle=False)
    ui.handle("install")

    ui.publish(UpdateState("1.3.0", status="checking"))

    assert window.last_view.notice == ""


def test_close_hides_the_window():
    ui, _flow, window, _install = make_ui(UpdateState("1.3.0"))

    ui.handle("close")

    assert window.calls == [("hide",)]


def test_window_failure_falls_back_to_notification():
    flow = Mock(state=UpdateState("1.3.0"))
    window = Mock()
    window.show.side_effect = RuntimeError("no Qt")
    fallback = Mock()
    ui = updates.UpdateUI(flow, window, lambda: True, Mock(), on_window_error=fallback)

    ui.publish(UpdateState("1.3.0", status="downloading", offer=OFFER))

    fallback.assert_called_once()
    assert fallback.call_args.args[0].headline.startswith("Downloading")


def test_unavailable_window_process_falls_back_to_notification():
    flow = Mock(state=ready_state())
    fallback = Mock()
    ui = updates.UpdateUI(flow, FakeWindow(), lambda: True, Mock(), on_window_error=fallback)

    ui.handle("unavailable")

    fallback.assert_called_once()
    assert fallback.call_args.args[0].primary_action == "install"


def test_after_window_failure_later_actionable_states_use_the_fallback():
    flow = Mock(state=UpdateState("1.3.0"))
    fallback = Mock()
    window = FakeWindow()
    ui = updates.UpdateUI(flow, window, lambda: True, Mock(), on_window_error=fallback)
    downloading = UpdateState("1.3.0", status="downloading", offer=OFFER, percent=1)
    ui.publish(downloading)  # Auto-opens the window process ...
    ui.handle("unavailable")  # ... which dies before it is ready
    fallback.reset_mock()

    ui.publish(replace(downloading, percent=60))  # progress: no fallback spam
    ui.publish(ready_state())
    ui.publish(UpdateState("1.3.0", status="error", error="offline"))  # automatic: quiet

    assert [call.args[0].headline for call in fallback.call_args_list] == [
        "PulseScribe 1.4.0 is ready to install"
    ]


def test_manual_check_result_reaches_fallback_after_window_failure():
    flow = Mock(state=UpdateState("1.3.0"))
    fallback = Mock()
    ui = updates.UpdateUI(flow, FakeWindow(), lambda: True, Mock(), on_window_error=fallback)
    ui.open()
    ui.handle("unavailable")
    fallback.reset_mock()

    ui.publish(UpdateState("1.3.0", status="checking", manual=True))
    ui.publish(UpdateState("1.3.0", status="idle", manual=True))

    assert [call.args[0].headline for call in fallback.call_args_list] == [
        "PulseScribe 1.3.0 is up to date"
    ]


def test_install_errors_reach_the_fallback_when_the_window_failed():
    flow = Mock(state=ready_state())
    fallback = Mock()
    ui = updates.UpdateUI(
        flow, FakeWindow(), lambda: True, Mock(side_effect=OSError("no helper")),
        on_window_error=fallback,
    )
    ui.handle("unavailable")
    fallback.reset_mock()

    ui.handle("install")  # Confirmed in the native fallback

    fallback.assert_called_once()
    assert "no helper" in fallback.call_args.args[0].notice
