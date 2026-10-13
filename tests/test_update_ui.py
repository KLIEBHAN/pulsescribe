from dataclasses import replace
from unittest.mock import Mock

import pytest

from ui import updates
from utils.update_flow import UpdateState
from utils.updates import UpdateOffer


def ready_flow():
    state = UpdateState(
        "1.3.0",
        status="ready",
        offer=UpdateOffer("1.4.0", "Notes", "installer", 1, "a" * 64),
        hint="Restart",
    )
    return Mock(state=state)


def test_busy_dictation_never_restarts(monkeypatch):
    dialog = Mock(return_value=True)
    monkeypatch.setattr(updates, "show_update_message", dialog)
    flow, quit_app = ready_flow(), Mock()
    updates.activate_update(flow, lambda: False, quit_app)
    flow.install.assert_not_called()
    quit_app.assert_not_called()


def test_dictation_started_during_dialog_never_restarts(monkeypatch):
    monkeypatch.setattr(updates, "show_update_message", Mock(return_value=True))
    idle = iter([True, False])
    flow, quit_app = ready_flow(), Mock()
    updates.activate_update(flow, lambda: next(idle), quit_app)
    flow.install.assert_not_called()
    quit_app.assert_not_called()


@pytest.mark.parametrize("confirmed", [False, True])
def test_restart_requires_confirmation_and_orderly_quit(monkeypatch, confirmed):
    monkeypatch.setattr(updates, "show_update_message", Mock(return_value=confirmed))
    flow, quit_app = ready_flow(), Mock()
    calls = []
    flow.install.side_effect = lambda: calls.append("install")
    quit_app.side_effect = lambda: calls.append("quit")
    updates.activate_update(flow, lambda: True, quit_app)
    assert calls == (["install", "quit"] if confirmed else [])


def test_failed_helper_launch_keeps_app_running(monkeypatch):
    monkeypatch.setattr(updates, "show_update_message", Mock(return_value=True))
    flow, quit_app = ready_flow(), Mock()
    flow.install.side_effect = OSError("no helper")
    updates.activate_update(flow, lambda: True, quit_app)
    quit_app.assert_not_called()


def test_automatic_feedback_stays_quiet(monkeypatch):
    dialog = Mock()
    monkeypatch.setattr(updates, "show_update_message", dialog)
    state = UpdateState("1.3.0", status="error", error="offline")
    updates.update_feedback(state)
    dialog.assert_not_called()
    updates.update_feedback(replace(state, manual=True))
    dialog.assert_called_once()
