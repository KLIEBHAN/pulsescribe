"""Shared Hotkey UI card (Wizard + Settings).

Keeps the Hotkey configuration layout and recording logic DRY across the app.
All AppKit/Foundation imports are intentionally local to keep module import safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from utils.hotkey_recording import HotkeyRecorder


HOTKEY_TOKEN_LABELS = {
    "cmd": "Command",
    "command": "Command",
    "ctrl": "Control",
    "control": "Control",
    "option": "Option",
    "opt": "Option",
    "alt": "Option",
    "shift": "Shift",
    "fn": "Fn",
    "space": "Space",
    "tab": "Tab",
    "enter": "Return",
    "return": "Return",
    "esc": "Esc",
    "escape": "Esc",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
}
HOTKEY_EMPTY_STATE_MESSAGE = "Choose a preset or use Record to save a custom hotkey."
HOTKEY_RECORD_PLACEHOLDER = "Press shortcut…"
HOTKEY_CANCELLED_MESSAGE = "Recording cancelled. Previous hotkey kept."


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



def _set_tooltip_if_supported(view, text: str | None) -> None:
    if view is None:
        return
    try:
        view.setToolTip_(text or "")
    except Exception:
        pass



def _kind_label(kind: str) -> str:
    return "Hold" if (kind or "").strip().lower() == "hold" else "Toggle"



def _build_field_tooltip(kind: str, value: str | None = None) -> str:
    label = _kind_label(kind)
    rendered = _format_hotkey_for_display(value)
    if rendered:
        return f"Current {label} hotkey: {rendered}. Use Record to change it."
    return f"No {label} hotkey saved yet. Choose a preset or use Record to add one."



def _build_record_button_tooltip(kind: str) -> str:
    label = _kind_label(kind)
    return f"Record a custom {label} hotkey. Press Esc to cancel."



def _build_preset_tooltip(preset: str) -> str:
    normalized = (preset or "").strip().lower()
    if normalized == "fn_hold":
        return "Use Fn / Globe as a Hold hotkey."
    if normalized == "opt_space":
        return "Use Option+Space as a Toggle hotkey."
    if normalized == "f19_toggle":
        return "Use F19 as a Toggle hotkey."
    return "Apply this hotkey preset."



def _build_recording_status(kind: str) -> str:
    label = _kind_label(kind)
    return f"Recording {label} hotkey — press shortcut, Esc cancels."



def _build_saved_status(kind: str, hotkey: str) -> str:
    label = _kind_label(kind)
    rendered = _format_hotkey_for_display(hotkey)
    if rendered:
        return f"{label} hotkey saved: {rendered}."
    return f"{label} hotkey saved."



def _build_failed_save_status(kind: str) -> str:
    label = _kind_label(kind)
    return f"{label} hotkey was not saved. Try another shortcut."



def _format_hotkey_for_display(value: str | None) -> str:
    hotkey = (value or "").strip().lower()
    if not hotkey:
        return ""

    display_parts: list[str] = []
    for part in hotkey.split("+"):
        display = HOTKEY_TOKEN_LABELS.get(part)
        if display is None:
            if part.startswith("f") and part[1:].isdigit():
                display = part.upper()
            elif len(part) == 1:
                display = part.upper()
            else:
                display = part.capitalize()
        display_parts.append(display)
    return "+".join(display_parts)


@dataclass
class HotkeyCardWidgets:
    toggle_field: object
    toggle_record_btn: object
    hold_field: object
    hold_record_btn: object
    status_label: object


class HotkeyCard:
    """A hotkey configuration card with preset buttons and custom recording."""

    def __init__(
        self,
        *,
        widgets: HotkeyCardWidgets,
        hotkey_recorder: "HotkeyRecorder",
        on_hotkey_change: Callable[[str, str], bool],
        on_after_change: Callable[[], None] | None = None,
        get_current_hotkeys: Callable[[], tuple[str, str]] | None = None,
    ) -> None:
        self._widgets = widgets
        self._recorder = hotkey_recorder
        self._on_hotkey_change = on_hotkey_change
        self._on_after_change = on_after_change
        self._get_current_hotkeys = get_current_hotkeys
        self._last_synced_values: tuple[str, str] | None = None
        self._last_status: tuple[str, str] | None = None

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
        hotkey_recorder: "HotkeyRecorder",
        on_hotkey_change: Callable[[str, str], bool],
        on_after_change: Callable[[], None] | None = None,
        get_current_hotkeys: Callable[[], tuple[str, str]] | None = None,
        show_presets: bool = True,
        show_hint: bool = True,
    ) -> "HotkeyCard":
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextAlignmentCenter,
            NSTextField,
        )

        card_x = outer_padding
        card_w = window_width - 2 * outer_padding
        card = _create_card(card_x, card_y, card_w, card_height)
        parent_view.addSubview_(card)

        base_x = outer_padding + inner_padding
        content_w = card_w - 2 * inner_padding
        card_top = card_y + card_height

        # Title
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

        # Description
        desc_h = 34
        desc_y = card_top - 28 - 6 - desc_h
        desc_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, desc_y, content_w, desc_h)
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

        # Preset buttons (optional)
        btn_h = 32
        current_y = desc_y - 8
        if show_presets:
            current_y -= btn_h

            def add_preset_btn(label: str, action: str, x_offset: int, width: int):
                btn = NSButton.alloc().initWithFrame_(
                    NSMakeRect(base_x + x_offset, current_y, width, btn_h)
                )
                btn.setTitle_(label)
                btn.setBezelStyle_(NSBezelStyleRounded)
                btn.setFont_(NSFont.systemFontOfSize_(11))
                bind_action(btn, action)
                _set_tooltip_if_supported(
                    btn,
                    _build_preset_tooltip(action.replace("hotkey_", "")),
                )
                parent_view.addSubview_(btn)

            # Calculate button widths (3 buttons with spacing)
            spacing = 8
            btn_w = (content_w - 2 * spacing) // 3
            add_preset_btn("Fn/Globe (Hold)", "hotkey_fn_hold", 0, btn_w)
            add_preset_btn(
                "Opt+Space (Toggle)", "hotkey_opt_space", btn_w + spacing, btn_w
            )
            add_preset_btn(
                "F19 (Toggle)", "hotkey_f19_toggle", 2 * (btn_w + spacing), btn_w
            )
            current_y -= 16

        # Custom hotkey rows
        label_w = 64
        record_w = 70
        field_x = base_x + label_w + 8
        field_w = max(100, content_w - label_w - 8 - record_w - 8)
        record_x = field_x + field_w + 8
        row_h = 24
        row_spacing = 10

        toggle_y = current_y - row_h
        hold_y = toggle_y - row_h - row_spacing

        def add_row(kind: str, title_text: str, y_pos: int) -> tuple[object, object]:
            label = NSTextField.alloc().initWithFrame_(
                NSMakeRect(base_x, y_pos + 4, label_w, 16)
            )
            label.setStringValue_(title_text)
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
            label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.75))
            parent_view.addSubview_(label)

            field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(field_x, y_pos, field_w, row_h)
            )
            field.setPlaceholderString_("Use Record…")
            field.setFont_(NSFont.systemFontOfSize_(13))
            field.setAlignment_(NSTextAlignmentCenter)
            field.setEditable_(False)
            field.setSelectable_(True)
            _set_tooltip_if_supported(field, _build_field_tooltip(kind))
            parent_view.addSubview_(field)

            rec_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(record_x, y_pos, record_w, row_h)
            )
            rec_btn.setTitle_("Record")
            rec_btn.setBezelStyle_(NSBezelStyleRounded)
            rec_btn.setFont_(NSFont.systemFontOfSize_(11))
            bind_action(rec_btn, f"record_hotkey:{kind}")
            _set_tooltip_if_supported(rec_btn, _build_record_button_tooltip(kind))
            parent_view.addSubview_(rec_btn)

            return field, rec_btn

        toggle_field, toggle_btn = add_row("toggle", "Toggle:", toggle_y)
        hold_field, hold_btn = add_row("hold", "Hold:", hold_y)

        # Hint (optional)
        hint_y = hold_y - row_spacing - 16
        if show_hint:
            hint = NSTextField.alloc().initWithFrame_(
                NSMakeRect(base_x, hint_y, content_w, 16)
            )
            hint.setStringValue_(
                "Toggle starts and stops with one press. Hold is push-to-talk and may need Input Monitoring."
            )
            hint.setBezeled_(False)
            hint.setDrawsBackground_(False)
            hint.setEditable_(False)
            hint.setSelectable_(False)
            hint.setFont_(NSFont.systemFontOfSize_(10))
            hint.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55))
            try:
                hint.setUsesSingleLineMode_(False)
            except Exception:
                pass
            parent_view.addSubview_(hint)
            hint_y -= 18

        # Status label
        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, hint_y, content_w, 16)
        )
        status.setStringValue_("")
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_(10))
        status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        try:
            status.setUsesSingleLineMode_(False)
        except Exception:
            pass
        _set_tooltip_if_supported(
            status,
            "Shows save, validation and recording feedback for the hotkey card.",
        )
        parent_view.addSubview_(status)

        widgets = HotkeyCardWidgets(
            toggle_field=toggle_field,
            toggle_record_btn=toggle_btn,
            hold_field=hold_field,
            hold_record_btn=hold_btn,
            status_label=status,
        )

        card_instance = cls(
            widgets=widgets,
            hotkey_recorder=hotkey_recorder,
            on_hotkey_change=on_hotkey_change,
            on_after_change=on_after_change,
            get_current_hotkeys=get_current_hotkeys,
        )
        card_instance.sync_from_env()
        return card_instance

    def sync_from_env(self) -> None:
        """Update fields from current .env settings."""
        if self._recorder.recording:
            return

        try:
            if callable(self._get_current_hotkeys):
                toggle, hold = self._get_current_hotkeys()
                toggle = (toggle or "").strip()
                hold = (hold or "").strip()
            else:
                from utils.preferences import get_env_setting

                toggle = (get_env_setting("PULSESCRIBE_TOGGLE_HOTKEY") or "").strip()
                hold = (get_env_setting("PULSESCRIBE_HOLD_HOTKEY") or "").strip()
            rendered_values = (
                _format_hotkey_for_display(toggle),
                _format_hotkey_for_display(hold),
            )
            if self._last_synced_values == rendered_values:
                return
            self._widgets.toggle_field.setStringValue_(rendered_values[0])
            self._widgets.hold_field.setStringValue_(rendered_values[1])
            _set_tooltip_if_supported(
                self._widgets.toggle_field,
                _build_field_tooltip("toggle", toggle),
            )
            _set_tooltip_if_supported(
                self._widgets.hold_field,
                _build_field_tooltip("hold", hold),
            )
            self._last_synced_values = rendered_values
            if not any(rendered_values) and self._last_status is None:
                self.set_status("info", HOTKEY_EMPTY_STATE_MESSAGE)
        except Exception:
            pass

    def set_status(self, level: str, message: str | None) -> None:
        """Update status label with message and color based on level."""
        if self._widgets.status_label is None or not message:
            return
        status = (level, message)
        if self._last_status == status:
            return

        color = _get_color(180, 180, 180)
        if level == "ok":
            color = _get_color(120, 255, 150)
        elif level == "warning":
            color = _get_color(255, 200, 90)
        elif level == "error":
            color = _get_color(255, 120, 120)

        try:
            self._widgets.status_label.setStringValue_(message)
            self._widgets.status_label.setTextColor_(color)
            self._last_status = status
        except Exception:
            pass

    def toggle_recording(self, kind: str) -> None:
        """Start or stop recording for toggle/hold hotkey."""
        if self._recorder.recording:
            self.stop_recording(cancelled=True)
            self.set_status("info", HOTKEY_CANCELLED_MESSAGE)
            return

        if kind == "toggle":
            field = self._widgets.toggle_field
            btn = self._widgets.toggle_record_btn
        elif kind == "hold":
            field = self._widgets.hold_field
            btn = self._widgets.hold_record_btn
        else:
            return

        buttons = [self._widgets.toggle_record_btn, self._widgets.hold_record_btn]
        self._recorder.start(
            field=field,
            button=btn,
            buttons_to_reset=buttons,
            on_hotkey=lambda hk: self._handle_recorded_hotkey(kind, hk),
            placeholder=HOTKEY_RECORD_PLACEHOLDER,
        )
        self.set_status("warning", _build_recording_status(kind))

    def stop_recording(self, *, cancelled: bool = False) -> None:
        """Stop any active hotkey recording."""
        self._recorder.stop(cancelled=cancelled)

    def apply_preset(self, preset: str) -> None:
        """Apply a preset hotkey configuration."""
        self.stop_recording(cancelled=True)
        if preset == "f19_toggle":
            self._apply_change("toggle", "f19")
        elif preset == "fn_hold":
            self._apply_change("hold", "fn")
        elif preset == "opt_space":
            self._apply_change("toggle", "option+space")

    def _handle_recorded_hotkey(self, kind: str, hotkey_str: str) -> None:
        """Handle a recorded hotkey."""
        self._apply_change(kind, hotkey_str)

    def _apply_change(self, kind: str, hotkey_str: str) -> None:
        """Apply a hotkey change and update UI."""
        previous_status = self._last_status
        success = self._on_hotkey_change(kind, hotkey_str)
        status_was_updated = self._last_status != previous_status

        if success:
            self.sync_from_env()
            if not status_was_updated:
                self.set_status("ok", _build_saved_status(kind, hotkey_str))
            if callable(self._on_after_change):
                try:
                    self._on_after_change()
                except Exception:
                    pass
            return

        if not status_was_updated:
            self.set_status("error", _build_failed_save_status(kind))
