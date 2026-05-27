from ui.qt_widget_state import (
    get_widget_visible,
    set_widget_enabled_if_changed,
    set_widget_stylesheet_if_changed,
    set_widget_text_if_changed,
    set_widget_visible_if_changed,
)


class _FakeWidget:
    def __init__(
        self,
        *,
        text: str = "",
        style: str = "",
        visible: bool = True,
        hidden: bool = False,
        enabled: bool = True,
    ) -> None:
        self._text = text
        self._style = style
        self._visible = visible
        self._hidden = hidden
        self._enabled = enabled
        self.text_calls = 0
        self.style_calls = 0
        self.visible_calls = 0
        self.enabled_calls = 0

    def text(self) -> str:
        return self._text

    def setText(self, value: str) -> None:
        self._text = value
        self.text_calls += 1

    def styleSheet(self) -> str:
        return self._style

    def setStyleSheet(self, value: str) -> None:
        self._style = value
        self.style_calls += 1

    def isVisible(self) -> bool:
        return self._visible

    def isHidden(self) -> bool:
        return self._hidden

    def setVisible(self, value: bool) -> None:
        self._visible = value
        self._hidden = not value
        self.visible_calls += 1

    def isEnabled(self) -> bool:
        return self._enabled

    def setEnabled(self, value: bool) -> None:
        self._enabled = value
        self.enabled_calls += 1


def test_widget_setters_skip_noop_updates() -> None:
    widget = _FakeWidget(text="Ready", style="color: white;", visible=True, enabled=True)

    assert set_widget_text_if_changed(widget, "Ready") is False
    assert set_widget_stylesheet_if_changed(widget, "color: white;") is False
    assert set_widget_visible_if_changed(widget, True) is False
    assert set_widget_enabled_if_changed(widget, True) is False

    assert widget.text_calls == 0
    assert widget.style_calls == 0
    assert widget.visible_calls == 0
    assert widget.enabled_calls == 0


def test_widget_setters_apply_changed_values() -> None:
    widget = _FakeWidget(text="Ready", style="color: white;", visible=True, enabled=True)

    assert set_widget_text_if_changed(widget, "Recording") is True
    assert set_widget_stylesheet_if_changed(widget, "color: red;") is True
    assert set_widget_visible_if_changed(widget, False) is True
    assert set_widget_enabled_if_changed(widget, False) is True

    assert widget.text_calls == 1
    assert widget.style_calls == 1
    assert widget.visible_calls == 1
    assert widget.enabled_calls == 1


def test_get_widget_visible_can_prefer_explicit_hidden_state() -> None:
    widget = _FakeWidget(visible=False, hidden=False)

    assert get_widget_visible(widget) is False
    assert get_widget_visible(widget, prefer_hidden_state=True) is True
