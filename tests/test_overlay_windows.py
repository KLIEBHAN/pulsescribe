from pathlib import Path

from ui.overlay_windows import WindowsOverlayController


class _FakeRoot:
    def after(self, _ms: int, _callback) -> None:
        return

    def withdraw(self) -> None:
        return


def _make_controller(interim_file: Path) -> WindowsOverlayController:
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._interim_file = interim_file
    controller._state = "RECORDING"
    controller._last_interim_text = ""
    controller._last_interim_mtime_ns = None
    return controller


def test_poll_interim_file_reads_only_when_file_changes(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("hello", encoding="utf-8")
    controller = _make_controller(interim_file)
    seen_texts: list[str] = []
    controller._handle_interim_text = seen_texts.append

    controller._poll_interim_file()
    controller._poll_interim_file()

    assert seen_texts == ["hello"]

    interim_file.write_text("hello again", encoding="utf-8")
    controller._poll_interim_file()

    assert seen_texts == ["hello", "hello again"]
