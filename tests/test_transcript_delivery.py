from unittest.mock import Mock

import pytest

from ui.daemon_status_feedback import build_daemon_status_hint, build_daemon_tray_title
from utils.state import AppState
from utils.transcript_delivery import COPIED, COPY_FAILED, PASTE_FAILED, TranscriptDelivery


@pytest.mark.parametrize(
    "auto_paste,paste_result,copy_result,expected",
    [
        (True, True, False, (True, "Transcript pasted")),
        (True, False, True, (False, PASTE_FAILED)),
        (True, False, False, (False, COPY_FAILED)),
        (True, RuntimeError("paste failed"), True, (False, PASTE_FAILED)),
        (True, False, RuntimeError("clipboard busy"), (False, COPY_FAILED)),
        (False, True, True, (True, COPIED)),
        (False, True, False, (False, COPY_FAILED)),
    ],
)
def test_delivery_reports_actual_outcome_and_keeps_text(
    monkeypatch, auto_paste, paste_result, copy_result, expected
):
    def operation(result):
        if isinstance(result, Exception):
            return Mock(side_effect=result)
        return Mock(return_value=result)

    paste = operation(paste_result)
    copy = operation(copy_result)
    monkeypatch.setattr("utils.hotkey.paste_transcript", paste)
    monkeypatch.setattr("whisper_platform.get_clipboard", lambda: Mock(copy=copy))
    delivery = TranscriptDelivery()

    assert delivery.deliver("Grüße\n世界", auto_paste=auto_paste) == expected
    assert delivery.text == "Grüße\n世界"
    assert paste.call_count == int(auto_paste)
    assert copy.call_count == int(not (auto_paste and paste_result is True))
    if copy.called:
        copy.assert_called_once_with(delivery.text)
    state = AppState.DONE if expected[0] else AppState.ERROR
    title = build_daemon_tray_title(state, expected[1])
    assert expected[1] in title
    if not expected[0]:
        assert "Copy Last Transcript" in build_daemon_status_hint(state, expected[1])


@pytest.mark.parametrize("copy_success", [False, True])
def test_explicit_copy_invalidates_pending_clipboard_restore(monkeypatch, copy_success):
    import types
    import utils.hotkey as hotkey

    workers = []
    def thread(**kwargs):
        return types.SimpleNamespace(start=lambda: workers.append(kwargs["target"]))

    monkeypatch.setattr(hotkey, "threading", types.SimpleNamespace(Thread=thread))
    monkeypatch.setattr(hotkey, "time", types.SimpleNamespace(sleep=lambda _delay: None))
    copy = Mock(return_value=copy_success)
    monkeypatch.setattr("whisper_platform.get_clipboard", lambda: Mock(copy=copy))
    restore = Mock()
    generation = hotkey._clipboard_restore_generation
    hotkey._schedule_clipboard_restore(delay_sec=1, restore_callback=restore, generation=generation)
    assert hotkey.copy_transcript("same text") is copy_success
    workers[0]()
    assert restore.call_count == int(not copy_success)
    restore.reset_mock()
    hotkey._schedule_clipboard_restore(
        delay_sec=1, restore_callback=restore, generation=hotkey._clipboard_restore_generation
    )
    workers[1]()
    restore.assert_called_once_with()


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_copy_during_paste_invalidates_restore_before_scheduling(monkeypatch, platform):
    import sys
    import utils.hotkey as hotkey

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(hotkey, "_should_restore_clipboard", lambda: True)
    monkeypatch.setattr("config.get_windows_paste_sync_seconds", lambda: 0)
    monkeypatch.setattr(hotkey, "_get_windows_clipboard_handler", lambda: None)
    monkeypatch.setattr(hotkey, "_get_windows_clipboard_text", lambda **_kwargs: "old")
    monkeypatch.setattr(hotkey, "_get_clipboard_text", lambda: "old")
    monkeypatch.setattr(hotkey, "_copy_windows_clipboard_text", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(hotkey, "_copy_macos_clipboard_text", lambda _text: True)
    monkeypatch.setattr("whisper_platform.get_clipboard", lambda: Mock(copy=Mock(return_value=True)))
    generation = hotkey._clipboard_restore_generation
    paste = lambda: hotkey.copy_transcript("new")
    monkeypatch.setattr(hotkey, "_paste_via_pynput", paste)
    monkeypatch.setattr(hotkey, "_paste_via_pynput_windows", paste)
    schedule = Mock()
    monkeypatch.setattr(hotkey, "_schedule_clipboard_restore", schedule)

    assert hotkey.paste_transcript("new") is True
    assert schedule.call_args.kwargs["generation"] == generation
    assert hotkey._clipboard_restore_generation == generation + 1


def test_explicit_copy_uses_latest_text_and_can_retry(monkeypatch):
    paste = Mock(return_value=True)
    copy = Mock(side_effect=[False, True])
    monkeypatch.setattr("utils.hotkey.paste_transcript", paste)
    monkeypatch.setattr("whisper_platform.get_clipboard", lambda: Mock(copy=copy))
    delivery = TranscriptDelivery()
    assert delivery.copy_last() == "No transcript to copy yet."
    copy.assert_not_called()
    delivery.deliver("old")
    delivery.deliver("latest")
    paste.reset_mock()

    assert delivery.copy_last() == "Could not copy. Try again."
    assert delivery.copy_last() == "Last transcript copied."
    assert [call.args for call in copy.call_args_list] == [("latest",), ("latest",)]
    paste.assert_not_called()
