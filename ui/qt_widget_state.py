"""Small Qt widget state helpers that avoid duplicate no-op updates."""

from __future__ import annotations


def get_widget_text(widget) -> str | None:
    if widget is None:
        return None

    getter = getattr(widget, "text", None)
    if callable(getter):
        try:
            return str(getter())
        except TypeError:
            pass

    value = getattr(widget, "text", None)
    if isinstance(value, str):
        return value
    return None


def set_widget_text_if_changed(widget, text: str) -> bool:
    if widget is None:
        return False
    if get_widget_text(widget) == text:
        return False
    widget.setText(text)
    return True


def get_widget_stylesheet(widget) -> str | None:
    if widget is None:
        return None

    getter = getattr(widget, "styleSheet", None)
    if callable(getter):
        try:
            return str(getter())
        except TypeError:
            pass

    value = getattr(widget, "style", None)
    if isinstance(value, str):
        return value
    return None


def set_widget_stylesheet_if_changed(widget, style: str) -> bool:
    if widget is None:
        return False
    if get_widget_stylesheet(widget) == style:
        return False
    widget.setStyleSheet(style)
    return True


def get_widget_visible(widget, *, prefer_hidden_state: bool = False) -> bool | None:
    if widget is None:
        return None

    if prefer_hidden_state:
        hidden_getter = getattr(widget, "isHidden", None)
        if callable(hidden_getter):
            try:
                return not bool(hidden_getter())
            except TypeError:
                pass

    getter = getattr(widget, "isVisible", None)
    if callable(getter):
        try:
            return bool(getter())
        except TypeError:
            pass

    value = getattr(widget, "visible", None)
    if isinstance(value, bool):
        return value
    return None


def set_widget_visible_if_changed(
    widget,
    visible: bool,
    *,
    prefer_hidden_state: bool = False,
) -> bool:
    if widget is None:
        return False
    if get_widget_visible(widget, prefer_hidden_state=prefer_hidden_state) == visible:
        return False
    widget.setVisible(visible)
    return True


def get_widget_enabled(widget) -> bool | None:
    if widget is None:
        return None

    getter = getattr(widget, "isEnabled", None)
    if callable(getter):
        try:
            return bool(getter())
        except TypeError:
            pass

    value = getattr(widget, "enabled", None)
    if isinstance(value, bool):
        return value
    return None


def set_widget_enabled_if_changed(widget, enabled: bool) -> bool:
    if widget is None:
        return False
    if get_widget_enabled(widget) == enabled:
        return False
    widget.setEnabled(enabled)
    return True


__all__ = [
    "get_widget_enabled",
    "get_widget_stylesheet",
    "get_widget_text",
    "get_widget_visible",
    "set_widget_enabled_if_changed",
    "set_widget_stylesheet_if_changed",
    "set_widget_text_if_changed",
    "set_widget_visible_if_changed",
]
