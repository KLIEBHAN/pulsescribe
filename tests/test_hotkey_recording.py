from __future__ import annotations

import sys
from types import SimpleNamespace

import utils.hotkey_recording as hotkey_recording


class _FakeField:
    def __init__(self, value: str = "") -> None:
        self._value = value
        self.placeholder: str | None = None

    def stringValue(self) -> str:
        return self._value

    def setStringValue_(self, value: str) -> None:
        self._value = value

    def setPlaceholderString_(self, value: str | None) -> None:
        self.placeholder = value


class _FakeButton:
    def __init__(self, title: str = "Record") -> None:
        self.title = title

    def setTitle_(self, value: str) -> None:
        self.title = value


def test_start_restores_state_when_local_monitor_setup_fails(monkeypatch) -> None:
    recorder = hotkey_recording.HotkeyRecorder()
    field = _FakeField("option+space")
    primary_button = _FakeButton()
    secondary_button = _FakeButton("Other")

    def _raise_monitor_error(**_kwargs):
        raise RuntimeError("monitor unavailable")

    monkeypatch.setattr(
        hotkey_recording,
        "add_local_hotkey_monitor",
        _raise_monitor_error,
    )

    recorder.start(
        field=field,
        button=primary_button,
        buttons_to_reset=[primary_button, secondary_button],
        on_hotkey=lambda _hotkey: True,
    )

    assert recorder.recording is False
    assert field.stringValue() == "option+space"
    assert field.placeholder is None
    assert primary_button.title == "Record"
    assert secondary_button.title == "Record"


def test_stop_resets_state_when_monitor_removal_fails(monkeypatch) -> None:
    recorder = hotkey_recording.HotkeyRecorder()
    field = _FakeField("option+space")
    primary_button = _FakeButton("Press…")
    secondary_button = _FakeButton("Press…")

    recorder._recording = True
    recorder._monitor = object()
    recorder._target_field = field
    recorder._prev_value = "option+space"
    recorder._buttons_to_reset = [primary_button, secondary_button]

    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(
            NSEvent=SimpleNamespace(
                removeMonitor_=lambda _monitor: (_ for _ in ()).throw(
                    RuntimeError("remove failed")
                )
            )
        ),
    )

    recorder.stop(cancelled=True)

    assert recorder.recording is False
    assert recorder._monitor is None
    assert field.stringValue() == "option+space"
    assert field.placeholder is None
    assert primary_button.title == "Record"
    assert secondary_button.title == "Record"


def test_accepted_hotkey_is_shown_with_format_value(monkeypatch) -> None:
    recorder = hotkey_recording.HotkeyRecorder()
    field = _FakeField()
    button = _FakeButton()
    callbacks: dict[str, object] = {}

    def _fake_monitor(*, on_hotkey, on_cancel):
        callbacks["on_hotkey"] = on_hotkey
        return None

    monkeypatch.setattr(hotkey_recording, "add_local_hotkey_monitor", _fake_monitor)

    recorder.start(
        field=field,
        button=button,
        buttons_to_reset=[button],
        on_hotkey=lambda _hotkey: True,
        format_value=lambda hotkey: f"<{hotkey}>",
    )
    callbacks["on_hotkey"]("ctrl+d")

    assert field.stringValue() == "<ctrl+d>"
    assert recorder.recording is False
