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

    def refresh(self) -> None:
        from AppKit import NSColor  # type: ignore[import-not-found]
        from utils.permissions import (
            get_microphone_permission_state,
            has_accessibility_permission,
            has_input_monitoring_permission,
        )

        ok_color = _get_color(120, 255, 150)
        warn_color = _get_color(255, 200, 90)
        err_color = _get_color(255, 120, 120)
        neutral_color = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6)

        def set_status(field, text: str, color) -> None:
            if field is None:
                return
            try:
                field.setStringValue_(text)
                field.setTextColor_(color)
            except Exception:
                pass

        def set_action(btn, *, title: str, enabled: bool, hidden: bool) -> None:
            if btn is None:
                return
            try:
                btn.setTitle_(title)
                btn.setEnabled_(enabled)
                btn.setHidden_(hidden)
            except Exception:
                pass

        mic_state = get_microphone_permission_state()
        if mic_state == "authorized":
            set_status(self._widgets.mic_status, "✅ Granted", ok_color)
            set_action(
                self._widgets.mic_action, title="Open", enabled=False, hidden=True
            )
        elif mic_state == "not_determined":
            set_status(self._widgets.mic_status, "⚠ Not requested yet", warn_color)
            set_action(
                self._widgets.mic_action, title="Request", enabled=True, hidden=False
            )
        elif mic_state in ("denied", "restricted"):
            set_status(self._widgets.mic_status, "❌ Denied", err_color)
            set_action(
                self._widgets.mic_action, title="Open", enabled=True, hidden=False
            )
        else:
            set_status(self._widgets.mic_status, "Unknown", neutral_color)
            set_action(
                self._widgets.mic_action, title="Open", enabled=True, hidden=False
            )

        acc_ok = has_accessibility_permission()
        set_status(
            self._widgets.access_status,
            "✅ Granted" if acc_ok else "⚠ Not granted",
            ok_color if acc_ok else warn_color,
        )
        set_action(
            self._widgets.access_action,
            title="Open",
            enabled=not acc_ok,
            hidden=bool(acc_ok),
        )

        input_ok = has_input_monitoring_permission()
        set_status(
            self._widgets.input_status,
            "✅ Granted" if input_ok else "⚠ Not granted",
            ok_color if input_ok else warn_color,
        )
        set_action(
            self._widgets.input_action,
            title="Open",
            enabled=not input_ok,
            hidden=bool(input_ok),
        )

        if callable(self._after_refresh):
            try:
                self._after_refresh()
            except Exception:
                pass

    def stop_auto_refresh(self) -> None:
        timer = self._refresh_timer
        self._refresh_timer = None
        self._refresh_ticks = 0
        if timer is not None:
            try:
                timer.invalidate()
            except Exception:
                pass

    def kick_auto_refresh(self, *, ticks: int = 30) -> None:
        """Refresh permission state for a short period (helps after opening System Settings)."""
        from Foundation import NSTimer  # type: ignore[import-not-found]

        self.stop_auto_refresh()
        self._refresh_ticks = max(1, int(ticks))
        self.refresh()

        def tick() -> None:
            if self._refresh_ticks <= 0:
                self.stop_auto_refresh()
                return
            self._refresh_ticks -= 1
            self.refresh()

        self._refresh_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1.0, True, lambda _timer: tick()
        )
