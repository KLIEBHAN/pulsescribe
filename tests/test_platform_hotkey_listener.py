from __future__ import annotations

import sys
from types import SimpleNamespace

import whisper_platform.hotkey as platform_hotkey


class _FakeKeyboardModule:
    class Key:
        ctrl = object()
        space = object()

    @staticmethod
    def KeyCode_from_char(value: str):
        return value

    class Listener:
        def __init__(self, *, on_press, on_release):
            self._on_press = on_press
            self._on_release = on_release
            self.stopped = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stop(self) -> None:
            self.stopped = True

        def join(self) -> None:
            return None


class _RecordingListener:
    def __init__(self, *, on_press, on_release, sequence):
        self._on_press = on_press
        self._on_release = on_release
        self._sequence = sequence
        self.stopped = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def stop(self) -> None:
        self.stopped = True

    def join(self) -> None:
        for action, key in self._sequence:
            if action == "press":
                self._on_press(key)
            else:
                self._on_release(key)


def _install_fake_pynput(monkeypatch, keyboard_module) -> None:
    monkeypatch.setitem(sys.modules, "pynput", SimpleNamespace(keyboard=keyboard_module))


def test_windows_hotkey_listener_uses_shared_parser_for_special_keys(monkeypatch) -> None:
    keyboard_module = _FakeKeyboardModule
    _install_fake_pynput(monkeypatch, keyboard_module)

    parse_calls: list[tuple[str, object]] = []

    def _fake_parse(hotkey: str, keyboard) -> set[object]:
        parse_calls.append((hotkey, keyboard))
        return {keyboard.Key.ctrl, keyboard.Key.space}

    monkeypatch.setattr(platform_hotkey, "parse_windows_hotkey_for_pynput", _fake_parse)

    listener = platform_hotkey.WindowsHotkeyListener("ctrl+space", lambda: None)

    assert parse_calls == [("ctrl+space", keyboard_module)]
    assert listener._hotkey_keys == {keyboard_module.Key.ctrl, keyboard_module.Key.space}


def test_windows_hotkey_listener_invalid_hotkey_does_not_degrade_to_partial_match(
    monkeypatch,
) -> None:
    class _KeyboardWithListener(_FakeKeyboardModule):
        class Listener(_FakeKeyboardModule.Listener):
            def join(self) -> None:
                self._on_press(_KeyboardWithListener.Key.ctrl)
                self._on_release(_KeyboardWithListener.Key.ctrl)

    _install_fake_pynput(monkeypatch, _KeyboardWithListener)
    monkeypatch.setattr(
        platform_hotkey,
        "parse_windows_hotkey_for_pynput",
        lambda _hotkey, _keyboard: set(),
    )

    callback_calls: list[str] = []
    listener = platform_hotkey.WindowsHotkeyListener(
        "ctrl+space",
        lambda: callback_calls.append("fired"),
    )

    listener.run()

    assert callback_calls == []


def test_windows_hotkey_listener_fires_once_per_activation(monkeypatch) -> None:
    ctrl = object()
    r_key = object()
    x_key = object()

    class _KeyboardWithSequence:
        class Key:
            pass

        class Listener:
            def __init__(self, *, on_press, on_release):
                self._delegate = _RecordingListener(
                    on_press=on_press,
                    on_release=on_release,
                    sequence=[
                        ("press", ctrl),
                        ("press", r_key),
                        ("press", r_key),  # auto-repeat of the trigger key
                        ("press", x_key),  # unrelated key while combo still held
                        ("release", x_key),
                        ("release", r_key),
                        ("press", r_key),  # second full activation
                        ("release", r_key),
                        ("release", ctrl),
                    ],
                )

            def __enter__(self):
                return self._delegate.__enter__()

            def __exit__(self, exc_type, exc, tb):
                return self._delegate.__exit__(exc_type, exc, tb)

            def stop(self) -> None:
                self._delegate.stop()

            def join(self) -> None:
                self._delegate.join()

    _KeyboardWithSequence.Key.ctrl = ctrl

    _install_fake_pynput(monkeypatch, _KeyboardWithSequence)
    monkeypatch.setattr(
        platform_hotkey,
        "parse_windows_hotkey_for_pynput",
        lambda _hotkey, _keyboard: {ctrl, r_key},
    )

    callback_count = 0

    def _on_hotkey() -> None:
        nonlocal callback_count
        callback_count += 1

    listener = platform_hotkey.WindowsHotkeyListener("ctrl+r", _on_hotkey)

    listener.run()

    assert callback_count == 2
