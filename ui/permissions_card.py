"""Shared Permissions UI card (Wizard + Settings).

Keeps the Permission layout and refresh logic DRY across the app.
All AppKit/Foundation imports are intentionally local to keep module import safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# Shared description text for permissions card (used in Wizard + Settings)
PERMISSIONS_DESCRIPTION = (
    "Microphone is required. Accessibility improves auto‑paste.\n"
    "Input Monitoring enables Hold + some global hotkeys.\n"
    "💡 Accessibility/Input Monitoring not working? Remove & re‑add the app."
)


def _get_color(r: int, g: int, b: int, a: float = 1.0):
    from AppKit import NSColor  # type: ignore[import-not-found]

    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


def _create_card(x: int, y: int, width: int, height: int, *, corner_radius: int = 12):
    from AppKit import NSBox, NSColor  # type: ignore[import-not-found]
    from Foundation import NSMakeRect  # type: ignore[import-not-found]

    card = NSBox.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
    card.setBoxType_(4)  # Custom
    card.setBorderType_(0)  # None
    card.setFillColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.06))
    card.setCornerRadius_(corner_radius)
    card.setContentViewMargins_((0, 0))
    return card


def _get_status_palette() -> dict[str, object]:
    palette = getattr(_get_status_palette, "_cache", None)
    if palette is None:
        from AppKit import NSColor  # type: ignore[import-not-found]

        palette = {
            "ok": _get_color(120, 255, 150),
            "warn": _get_color(255, 200, 90),
            "err": _get_color(255, 120, 120),
            "neutral": NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6),
        }
        _get_status_palette._cache = palette
    return palette


@dataclass
class PermissionCardWidgets:
    mic_status: object
    mic_action: object
    input_status: object
    input_action: object
    access_status: object
    access_action: object


class PermissionsCard:
    """A simple permission status card with 3 rows and auto-refresh."""

    _AUTO_REFRESH_INTERVALS = (0.5, 1.0, 2.0, 4.0)

    def __init__(
        self,
        *,
        widgets: PermissionCardWidgets,
        after_refresh: Callable[[], None] | None = None,
    ) -> None:
        self._widgets = widgets
        self._after_refresh = after_refresh
        self._refresh_timer = None
        self._refresh_ticks = 0
        self._refresh_interval_index = 0
        self._last_permission_signature: tuple[str, bool, bool] | None = None
        self._status_cache: dict[str, tuple[str, object]] = {}
        self._action_cache: dict[str, tuple[str, bool, bool]] = {}

    def get_cached_permission_signature(self) -> tuple[str, bool, bool] | None:
        """Return the most recent permission snapshot from refresh(), if any."""
        return self._last_permission_signature

    def _read_permission_signature(self) -> tuple[str, bool, bool]:
        from utils.permissions import (
            get_microphone_permission_state,
            has_accessibility_permission,
            has_input_monitoring_permission,
        )

        return (
            get_microphone_permission_state(),
            has_accessibility_permission(),
            has_input_monitoring_permission(),
        )

    @classmethod
    def build(
        cls,
        *,
        parent_view,
        window_width: int,
        card_y: int,
        card_height: int,
        outer_padding: int,
        inner_padding: int,
        title: str,
        description: str,
        bind_action: Callable[[object, str], None],
        after_refresh: Callable[[], None] | None = None,
    ) -> "PermissionsCard":
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        card_x = outer_padding
        card_w = window_width - 2 * outer_padding
        card = _create_card(card_x, card_y, card_w, card_height)
        parent_view.addSubview_(card)

        base_x = outer_padding + inner_padding
        right_edge = window_width - outer_padding - inner_padding

        card_top = card_y + card_height
        title_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_top - 28, 320, 18)
        )
        title_field.setStringValue_(title)
        title_field.setBezeled_(False)
        title_field.setDrawsBackground_(False)
        title_field.setEditable_(False)
        title_field.setSelectable_(False)
        title_field.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title_field.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title_field)

        desc_h = 48  # 3 lines of text at font size 11
        desc_y = (card_top - 28) - 6 - desc_h
        desc_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, desc_y, card_w - 2 * inner_padding, desc_h)
        )
        desc_field.setStringValue_(description)
        desc_field.setBezeled_(False)
        desc_field.setDrawsBackground_(False)
        desc_field.setEditable_(False)
        desc_field.setSelectable_(False)
        desc_field.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc_field.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        try:
            desc_field.setUsesSingleLineMode_(False)
        except Exception:
            pass
        parent_view.addSubview_(desc_field)

        label_w = 130
        status_x = base_x + label_w + 8
        btn_w = 90
        btn_h = 22
        btn_x = right_edge - btn_w
        status_w = max(80, btn_x - status_x - 8)

        def add_row(row_y: int, label_text: str, action: str) -> tuple[object, object]:
            label = NSTextField.alloc().initWithFrame_(
                NSMakeRect(base_x, row_y + 4, label_w, 16)
            )
            label.setStringValue_(label_text)
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
            label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.75))
            parent_view.addSubview_(label)

            status = NSTextField.alloc().initWithFrame_(
                NSMakeRect(status_x, row_y + 2, status_w, 18)
            )
            status.setStringValue_("…")
            status.setBezeled_(False)
            status.setDrawsBackground_(False)
            status.setEditable_(False)
            status.setSelectable_(False)
            status.setFont_(NSFont.systemFontOfSize_(11))
            status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
            parent_view.addSubview_(status)

            action_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(btn_x, row_y, btn_w, btn_h)
            )
            action_btn.setTitle_("Open")
            action_btn.setBezelStyle_(NSBezelStyleRounded)
            action_btn.setFont_(NSFont.systemFontOfSize_(11))
            bind_action(action_btn, action)
            parent_view.addSubview_(action_btn)

            return status, action_btn

        header_gap = 12
        row_y = desc_y - header_gap - btn_h
        mic_status, mic_btn = add_row(row_y, "Microphone", "perm_mic")
        input_status, input_btn = add_row(row_y - 32, "Input Monitoring", "perm_input")
        access_status, access_btn = add_row(row_y - 64, "Accessibility", "perm_access")

        widgets = PermissionCardWidgets(
            mic_status=mic_status,
            mic_action=mic_btn,
            input_status=input_status,
            input_action=input_btn,
            access_status=access_status,
            access_action=access_btn,
        )

        return cls(widgets=widgets, after_refresh=after_refresh)

    def _set_status_if_changed(
        self,
        key: str,
        field,
        text: str,
        color,
        *,
        cache_value: object | None = None,
    ) -> bool:
        if field is None:
            return False

        next_state = (text, cache_value if cache_value is not None else color)
        if self._status_cache.get(key) == next_state:
            return False

        try:
            field.setStringValue_(text)
            field.setTextColor_(color)
            self._status_cache[key] = next_state
            return True
        except Exception:
            return False

    def _set_action_if_changed(
        self,
        key: str,
        btn,
        *,
        title: str,
        enabled: bool,
        hidden: bool,
    ) -> bool:
        if btn is None:
            return False

        next_state = (title, enabled, hidden)
        if self._action_cache.get(key) == next_state:
            return False

        try:
            btn.setTitle_(title)
            btn.setEnabled_(enabled)
            btn.setHidden_(hidden)
            self._action_cache[key] = next_state
            return True
        except Exception:
            return False

    def refresh(self) -> bool:
        palette = _get_status_palette()
        changed = False

        mic_state, acc_ok, input_ok = self._read_permission_signature()
        self._last_permission_signature = (mic_state, acc_ok, input_ok)
        if mic_state == "authorized":
            changed |= self._set_status_if_changed(
                "mic_status",
                self._widgets.mic_status,
                "✅ Granted",
                palette["ok"],
                cache_value="ok",
            )
            changed |= self._set_action_if_changed(
                "mic_action",
                self._widgets.mic_action, title="Open", enabled=False, hidden=True
            )
        elif mic_state == "not_determined":
            changed |= self._set_status_if_changed(
                "mic_status",
                self._widgets.mic_status,
                "⚠ Not requested yet",
                palette["warn"],
                cache_value="warn",
            )
            changed |= self._set_action_if_changed(
                "mic_action",
                self._widgets.mic_action, title="Request", enabled=True, hidden=False
            )
        elif mic_state in ("denied", "restricted"):
            changed |= self._set_status_if_changed(
                "mic_status",
                self._widgets.mic_status,
                "❌ Denied",
                palette["err"],
                cache_value="err",
            )
            changed |= self._set_action_if_changed(
                "mic_action",
                self._widgets.mic_action, title="Open", enabled=True, hidden=False
            )
        else:
            changed |= self._set_status_if_changed(
                "mic_status",
                self._widgets.mic_status,
                "⚠ Status unavailable",
                palette["warn"],
                cache_value="warn",
            )
            changed |= self._set_action_if_changed(
                "mic_action",
                self._widgets.mic_action, title="Open", enabled=True, hidden=False
            )

        changed |= self._set_status_if_changed(
            "access_status",
            self._widgets.access_status,
            "✅ Granted" if acc_ok else "⚠ Not granted",
            palette["ok"] if acc_ok else palette["warn"],
            cache_value="ok" if acc_ok else "warn",
        )
        changed |= self._set_action_if_changed(
            "access_action",
            self._widgets.access_action,
            title="Open",
            enabled=not acc_ok,
            hidden=bool(acc_ok),
        )

        changed |= self._set_status_if_changed(
            "input_status",
            self._widgets.input_status,
            "✅ Granted" if input_ok else "⚠ Not granted",
            palette["ok"] if input_ok else palette["warn"],
            cache_value="ok" if input_ok else "warn",
        )
        changed |= self._set_action_if_changed(
            "input_action",
            self._widgets.input_action,
            title="Open",
            enabled=not input_ok,
            hidden=bool(input_ok),
        )

        if changed and callable(self._after_refresh):
            try:
                self._after_refresh()
            except Exception:
                pass

        return mic_state == "authorized" and acc_ok and input_ok

    def stop_auto_refresh(self) -> None:
        timer = self._refresh_timer
        self._refresh_timer = None
        self._refresh_ticks = 0
        if timer is not None:
            try:
                timer.invalidate()
            except Exception:
                pass

    def _schedule_auto_refresh(self) -> None:
        from Foundation import NSTimer  # type: ignore[import-not-found]

        interval = self._AUTO_REFRESH_INTERVALS[
            min(self._refresh_interval_index, len(self._AUTO_REFRESH_INTERVALS) - 1)
        ]
        self._refresh_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            interval, False, lambda _timer: self._run_auto_refresh_tick()
        )

    def _run_auto_refresh_tick(self) -> None:
        self._refresh_timer = None
        if self._refresh_ticks <= 0:
            self.stop_auto_refresh()
            return

        previous_signature = self._last_permission_signature
        self._refresh_ticks -= 1
        if self.refresh():
            self.stop_auto_refresh()
            return

        current_signature = self._last_permission_signature
        if current_signature != previous_signature:
            self._refresh_interval_index = 0
        else:
            self._refresh_interval_index += 1

        if self._refresh_ticks <= 0:
            self.stop_auto_refresh()
            return

        self._schedule_auto_refresh()

    def kick_auto_refresh(self, *, ticks: int = 8) -> None:
        """Refresh permission state for a short period (helps after opening System Settings)."""
        self.stop_auto_refresh()
        self._refresh_ticks = max(1, int(ticks))
        self._refresh_interval_index = 0
        if self.refresh():
            self.stop_auto_refresh()
            return
        self._schedule_auto_refresh()
