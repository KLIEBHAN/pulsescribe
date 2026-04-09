import sys
import types

from ui.permissions_card import PermissionCardWidgets, PermissionsCard


class _FakeNSColor:
    @staticmethod
    def colorWithCalibratedWhite_alpha_(white: float, alpha: float):
        return ("gray", white, alpha)

    @staticmethod
    def colorWithSRGBRed_green_blue_alpha_(
        red: float,
        green: float,
        blue: float,
        alpha: float,
    ):
        return ("rgb", red, green, blue, alpha)


class _FakeField:
    def __init__(self) -> None:
        self.text_calls = 0
        self.color_calls = 0
        self.text = ""
        self.color = None

    def setStringValue_(self, text: str) -> None:
        self.text = text
        self.text_calls += 1

    def setTextColor_(self, color) -> None:
        self.color = color
        self.color_calls += 1


class _FakeButton:
    def __init__(self) -> None:
        self.title_calls = 0
        self.enabled_calls = 0
        self.hidden_calls = 0
        self.title = ""
        self.enabled = True
        self.hidden = False

    def setTitle_(self, title: str) -> None:
        self.title = title
        self.title_calls += 1

    def setEnabled_(self, enabled: bool) -> None:
        self.enabled = enabled
        self.enabled_calls += 1

    def setHidden_(self, hidden: bool) -> None:
        self.hidden = hidden
        self.hidden_calls += 1


def _install_fake_appkit(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace(NSColor=_FakeNSColor))


class _UnstableNSColor:
    @staticmethod
    def colorWithCalibratedWhite_alpha_(white: float, alpha: float):
        return ("gray", white, alpha, object())

    @staticmethod
    def colorWithSRGBRed_green_blue_alpha_(
        red: float,
        green: float,
        blue: float,
        alpha: float,
    ):
        return ("rgb", red, green, blue, alpha, object())


def _build_card() -> PermissionsCard:
    return PermissionsCard(
        widgets=PermissionCardWidgets(
            mic_status=_FakeField(),
            mic_action=_FakeButton(),
            input_status=_FakeField(),
            input_action=_FakeButton(),
            access_status=_FakeField(),
            access_action=_FakeButton(),
        )
    )


def test_refresh_skips_duplicate_widget_updates(monkeypatch) -> None:
    import utils.permissions as permissions_mod

    _install_fake_appkit(monkeypatch)
    monkeypatch.setattr(
        permissions_mod,
        "get_microphone_permission_state",
        lambda: "authorized",
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_accessibility_permission",
        lambda: False,
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_input_monitoring_permission",
        lambda: False,
    )

    card = _build_card()

    assert card.refresh() is False
    assert card.refresh() is False

    assert card._widgets.mic_status.text_calls == 1
    assert card._widgets.mic_status.color_calls == 1
    assert card._widgets.mic_action.title_calls == 1
    assert card._widgets.mic_action.enabled_calls == 1
    assert card._widgets.mic_action.hidden_calls == 1

    assert card._widgets.access_status.text_calls == 1
    assert card._widgets.access_action.title_calls == 1
    assert card._widgets.input_status.text_calls == 1
    assert card._widgets.input_action.title_calls == 1


def test_refresh_skips_duplicate_updates_with_fresh_color_objects(monkeypatch) -> None:
    import utils.permissions as permissions_mod

    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        types.SimpleNamespace(NSColor=_UnstableNSColor),
    )
    monkeypatch.setattr(
        permissions_mod,
        "get_microphone_permission_state",
        lambda: "authorized",
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_accessibility_permission",
        lambda: False,
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_input_monitoring_permission",
        lambda: False,
    )

    card = _build_card()

    assert card.refresh() is False
    assert card.refresh() is False

    assert card._widgets.mic_status.color_calls == 1
    assert card._widgets.access_status.color_calls == 1
    assert card._widgets.input_status.color_calls == 1


def test_refresh_calls_after_refresh_only_when_permissions_change(monkeypatch) -> None:
    import utils.permissions as permissions_mod

    _install_fake_appkit(monkeypatch)
    states = {
        "mic": "authorized",
        "access": False,
        "input": False,
    }
    monkeypatch.setattr(
        permissions_mod,
        "get_microphone_permission_state",
        lambda: states["mic"],
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_accessibility_permission",
        lambda: states["access"],
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_input_monitoring_permission",
        lambda: states["input"],
    )

    refresh_calls: list[str] = []
    card = PermissionsCard(
        widgets=PermissionCardWidgets(
            mic_status=_FakeField(),
            mic_action=_FakeButton(),
            input_status=_FakeField(),
            input_action=_FakeButton(),
            access_status=_FakeField(),
            access_action=_FakeButton(),
        ),
        after_refresh=lambda: refresh_calls.append("refresh"),
    )

    assert card.refresh() is False
    assert card.refresh() is False

    states["access"] = True
    assert card.refresh() is False

    assert refresh_calls == ["refresh", "refresh"]


def test_kick_auto_refresh_avoids_timer_when_permissions_are_already_granted(
    monkeypatch,
) -> None:
    import utils.permissions as permissions_mod

    _install_fake_appkit(monkeypatch)
    monkeypatch.setitem(
        sys.modules,
        "Foundation",
        types.SimpleNamespace(
            NSTimer=types.SimpleNamespace(
                scheduledTimerWithTimeInterval_repeats_block_=lambda *_args, **_kwargs: (
                    _ for _ in ()
                ).throw(AssertionError("timer should not start when nothing can change"))
            )
        ),
    )
    monkeypatch.setattr(
        permissions_mod,
        "get_microphone_permission_state",
        lambda: "authorized",
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_accessibility_permission",
        lambda: True,
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_input_monitoring_permission",
        lambda: True,
    )

    card = _build_card()
    card.kick_auto_refresh()

    assert card._refresh_timer is None
    assert card._refresh_ticks == 0


def test_kick_auto_refresh_uses_backoff_for_stable_permission_state(monkeypatch) -> None:
    import utils.permissions as permissions_mod

    _install_fake_appkit(monkeypatch)
    monkeypatch.setattr(
        permissions_mod,
        "get_microphone_permission_state",
        lambda: "authorized",
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_accessibility_permission",
        lambda: False,
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_input_monitoring_permission",
        lambda: False,
    )

    intervals: list[float] = []
    callbacks: list[object] = []

    class _FakeTimer:
        def invalidate(self) -> None:
            return None

    def _schedule(interval, repeats, callback):
        intervals.append(interval)
        callbacks.append(callback)
        assert repeats is False
        return _FakeTimer()

    monkeypatch.setitem(
        sys.modules,
        "Foundation",
        types.SimpleNamespace(
            NSTimer=types.SimpleNamespace(
                scheduledTimerWithTimeInterval_repeats_block_=_schedule
            )
        ),
    )

    card = _build_card()
    card.kick_auto_refresh(ticks=4)

    assert intervals == [0.5]

    callbacks.pop(0)(None)
    assert intervals == [0.5, 1.0]

    callbacks.pop(0)(None)
    assert intervals == [0.5, 1.0, 2.0]

    callbacks.pop(0)(None)
    assert intervals == [0.5, 1.0, 2.0, 4.0]

    callbacks.pop(0)(None)
    assert card._refresh_timer is None
    assert card._refresh_ticks == 0


def test_kick_auto_refresh_resets_backoff_after_permission_change(monkeypatch) -> None:
    import utils.permissions as permissions_mod

    _install_fake_appkit(monkeypatch)
    states = {"mic": "authorized", "access": False, "input": False}
    monkeypatch.setattr(
        permissions_mod,
        "get_microphone_permission_state",
        lambda: states["mic"],
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_accessibility_permission",
        lambda: states["access"],
    )
    monkeypatch.setattr(
        permissions_mod,
        "has_input_monitoring_permission",
        lambda: states["input"],
    )

    intervals: list[float] = []
    callbacks: list[object] = []

    class _FakeTimer:
        def invalidate(self) -> None:
            return None

    def _schedule(interval, repeats, callback):
        intervals.append(interval)
        callbacks.append(callback)
        assert repeats is False
        return _FakeTimer()

    monkeypatch.setitem(
        sys.modules,
        "Foundation",
        types.SimpleNamespace(
            NSTimer=types.SimpleNamespace(
                scheduledTimerWithTimeInterval_repeats_block_=_schedule
            )
        ),
    )

    card = _build_card()
    card.kick_auto_refresh(ticks=4)

    callbacks.pop(0)(None)
    assert intervals == [0.5, 1.0]

    states["access"] = True
    callbacks.pop(0)(None)
    assert intervals == [0.5, 1.0, 0.5]
