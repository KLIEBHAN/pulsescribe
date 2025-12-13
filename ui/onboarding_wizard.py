"""Standalone first-run onboarding wizard for WhisperGo."""

from __future__ import annotations

import os
import subprocess
from typing import Callable

from utils.onboarding import (
    OnboardingChoice,
    OnboardingStep,
    next_step,
    prev_step,
    step_index,
    total_steps,
)
from utils.permissions import (
    check_accessibility_permission,
    check_input_monitoring_permission,
    check_microphone_permission,
    get_microphone_permission_state,
)
from utils.preferences import (
    get_api_key,
    get_env_setting,
    get_onboarding_choice,
    get_onboarding_step,
    save_env_setting,
    remove_env_setting,
    set_onboarding_choice,
    set_onboarding_step,
    set_onboarding_seen,
)
from utils.presets import (
    apply_local_preset_to_env,
    default_local_preset_fast,
    default_local_preset_private,
)

WIZARD_WIDTH = 500
WIZARD_HEIGHT = 640
PADDING = 20
FOOTER_HEIGHT = 54
CARD_PADDING = 16
CARD_CORNER_RADIUS = 12


def _get_color(r: int, g: int, b: int, a: float = 1.0):
    from AppKit import NSColor  # type: ignore[import-not-found]

    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


def _create_card(x: int, y: int, width: int, height: int):
    from AppKit import NSBox, NSColor  # type: ignore[import-not-found]
    from Foundation import NSMakeRect  # type: ignore[import-not-found]

    card = NSBox.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
    card.setBoxType_(4)  # Custom
    card.setBorderType_(0)  # None
    card.setFillColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.06))
    card.setCornerRadius_(CARD_CORNER_RADIUS)
    card.setContentViewMargins_((0, 0))
    return card


class OnboardingWizardController:
    """Standalone onboarding wizard (separate window from Settings)."""

    def __init__(self, *, persist_progress: bool = True):
        self._persist_progress = persist_progress
        self._window = None
        self._content_view = None

        self._on_complete: Callable[[], None] | None = None
        self._on_settings_changed: Callable[[], None] | None = None
        self._on_test_dictation_start: Callable[[], None] | None = None
        self._on_test_dictation_stop: Callable[[], None] | None = None
        self._on_enable_test_hotkey_mode: Callable[[], None] | None = None
        self._on_disable_test_hotkey_mode: Callable[[], None] | None = None

        self._choice: OnboardingChoice | None = get_onboarding_choice()
        self._step: OnboardingStep = (
            get_onboarding_step() if persist_progress else OnboardingStep.CHOOSE_GOAL
        )
        if self._step == OnboardingStep.DONE:
            self._step = OnboardingStep.CHEAT_SHEET

        self._step_label = None
        self._progress_label = None
        self._back_btn = None
        self._next_btn = None
        self._skip_btn = None
        self._step_views: dict[OnboardingStep, object] = {}

        # Permissions status labels
        self._perm_mic_status_label = None
        self._perm_access_status_label = None
        self._perm_input_status_label = None

        # Test dictation widgets/state
        self._test_btn = None
        self._test_status_label = None
        self._test_text_view = None
        self._test_successful = False
        self._test_state = "idle"  # idle|recording|stopping

        # Hotkey recording widgets/state
        self._toggle_hotkey_field = None
        self._hold_hotkey_field = None
        self._toggle_record_btn = None
        self._hold_record_btn = None
        self._hotkey_recording = False
        self._hotkey_recording_kind: str | None = None
        self._record_target_field = None
        self._record_target_btn = None
        self._record_prev_value: str | None = None
        self._hotkey_monitor = None

        # Strong refs for ObjC handlers
        self._handler_refs: list[object] = []

        self._build_window()

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def set_on_complete(self, callback: Callable[[], None]) -> None:
        self._on_complete = callback

    def set_on_settings_changed(self, callback: Callable[[], None]) -> None:
        self._on_settings_changed = callback

    def set_test_dictation_callbacks(self, *, start: Callable[[], None], stop: Callable[[], None]) -> None:
        self._on_test_dictation_start = start
        self._on_test_dictation_stop = stop

    def set_test_hotkey_mode_callbacks(
        self, *, enable: Callable[[], None], disable: Callable[[], None]
    ) -> None:
        """Enable/disable routing the user hotkey to the test step."""
        self._on_enable_test_hotkey_mode = enable
        self._on_disable_test_hotkey_mode = disable
        if self._step == OnboardingStep.TEST_DICTATION and callable(enable):
            try:
                enable()
            except Exception:
                pass

    def show(self) -> None:
        if self._window:
            self._window.makeKeyAndOrderFront_(None)
            self._window.center()
            from AppKit import NSApp  # type: ignore[import-not-found]

            NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        self._stop_hotkey_recording()
        if callable(self._on_disable_test_hotkey_mode):
            try:
                self._on_disable_test_hotkey_mode()
            except Exception:
                pass
        if self._window:
            self._window.close()

    # ---------------------------------------------------------------------
    # Window + Layout
    # ---------------------------------------------------------------------

    def _build_window(self) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSMakeRect,
            NSScreen,
            NSTitledWindowMask,
            NSVisualEffectView,
            NSWindow,
        )

        screen = NSScreen.mainScreen()
        if not screen:
            return

        screen_frame = screen.frame()
        x = (screen_frame.size.width - WIZARD_WIDTH) / 2
        y = (screen_frame.size.height - WIZARD_HEIGHT) / 2

        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, WIZARD_WIDTH, WIZARD_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        self._window.setTitle_("WhisperGo Setup Wizard")
        self._window.setReleasedWhenClosed_(False)

        content_frame = NSMakeRect(0, 0, WIZARD_WIDTH, WIZARD_HEIGHT)
        visual_effect = NSVisualEffectView.alloc().initWithFrame_(content_frame)
        visual_effect.setMaterial_(13)  # HUD Window
        visual_effect.setBlendingMode_(0)
        visual_effect.setState_(1)
        self._window.setContentView_(visual_effect)
        self._content_view = visual_effect

        self._build_header()
        self._build_steps()
        self._build_footer()
        self._refresh_permissions()
        self._render()

    def _build_header(self) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextAlignmentLeft,
            NSTextField,
        )

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PADDING, WIZARD_HEIGHT - 54, WIZARD_WIDTH - 2 * PADDING, 22)
        )
        title.setStringValue_("")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setAlignment_(NSTextAlignmentLeft)
        title.setFont_(NSFont.systemFontOfSize_weight_(16, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        self._content_view.addSubview_(title)
        self._step_label = title

        progress = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PADDING, WIZARD_HEIGHT - 74, WIZARD_WIDTH - 2 * PADDING, 18)
        )
        progress.setStringValue_("")
        progress.setBezeled_(False)
        progress.setDrawsBackground_(False)
        progress.setEditable_(False)
        progress.setSelectable_(False)
        progress.setFont_(NSFont.systemFontOfSize_(11))
        progress.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        self._content_view.addSubview_(progress)
        self._progress_label = progress

    def _build_steps(self) -> None:
        from AppKit import NSView  # type: ignore[import-not-found]
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        content_top = WIZARD_HEIGHT - 90
        content_bottom = FOOTER_HEIGHT + 10
        content_h = max(200, content_top - content_bottom)
        frame = NSMakeRect(0, content_bottom, WIZARD_WIDTH, content_h)

        for step in (
            OnboardingStep.CHOOSE_GOAL,
            OnboardingStep.PERMISSIONS,
            OnboardingStep.HOTKEY,
            OnboardingStep.TEST_DICTATION,
            OnboardingStep.CHEAT_SHEET,
        ):
            view = NSView.alloc().initWithFrame_(frame)
            view.setHidden_(True)
            self._content_view.addSubview_(view)
            self._step_views[step] = view

        self._build_step_choose_goal(self._step_views[OnboardingStep.CHOOSE_GOAL], content_h)
        self._build_step_permissions(self._step_views[OnboardingStep.PERMISSIONS], content_h)
        self._build_step_hotkey(self._step_views[OnboardingStep.HOTKEY], content_h)
        self._build_step_test_dictation(self._step_views[OnboardingStep.TEST_DICTATION], content_h)
        self._build_step_cheat_sheet(self._step_views[OnboardingStep.CHEAT_SHEET], content_h)

    def _build_footer(self) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSFont,
            NSMakeRect,
        )
        import objc  # type: ignore[import-not-found]

        y = 14
        btn_h = 28
        btn_w = 90
        spacing = 10

        right = WIZARD_WIDTH - PADDING

        next_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(right - btn_w, y, btn_w, btn_h)
        )
        next_btn.setTitle_("Next")
        next_btn.setBezelStyle_(NSBezelStyleRounded)
        next_btn.setFont_(NSFont.systemFontOfSize_(12))
        h_next = _WizardActionHandler.alloc().initWithController_action_(self, "next")
        next_btn.setTarget_(h_next)
        next_btn.setAction_(objc.selector(h_next.performAction_, signature=b"v@:@"))
        self._handler_refs.append(h_next)
        self._content_view.addSubview_(next_btn)
        self._next_btn = next_btn

        back_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(right - btn_w * 2 - spacing, y, btn_w, btn_h)
        )
        back_btn.setTitle_("Back")
        back_btn.setBezelStyle_(NSBezelStyleRounded)
        back_btn.setFont_(NSFont.systemFontOfSize_(12))
        h_back = _WizardActionHandler.alloc().initWithController_action_(self, "back")
        back_btn.setTarget_(h_back)
        back_btn.setAction_(objc.selector(h_back.performAction_, signature=b"v@:@"))
        self._handler_refs.append(h_back)
        self._content_view.addSubview_(back_btn)
        self._back_btn = back_btn

        skip_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(PADDING, y, 120, btn_h)
        )
        skip_btn.setTitle_("Skip for now")
        skip_btn.setBezelStyle_(NSBezelStyleRounded)
        skip_btn.setFont_(NSFont.systemFontOfSize_(12))
        h_skip = _WizardActionHandler.alloc().initWithController_action_(self, "skip")
        skip_btn.setTarget_(h_skip)
        skip_btn.setAction_(objc.selector(h_skip.performAction_, signature=b"v@:@"))
        self._handler_refs.append(h_skip)
        self._content_view.addSubview_(skip_btn)
        self._skip_btn = skip_btn

    # ---------------------------------------------------------------------
    # Steps
    # ---------------------------------------------------------------------

    def _build_step_choose_goal(self, parent_view, content_h: int) -> None:
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
        import objc  # type: ignore[import-not-found]

        card_w = WIZARD_WIDTH - 2 * PADDING
        card_h = 270
        card_y = content_h - card_h - 10
        card = _create_card(PADDING, card_y, card_w, card_h)
        parent_view.addSubview_(card)

        base_x = PADDING + CARD_PADDING

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_h - 28, 320, 18)
        )
        title.setStringValue_("What do you want?")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_h - 46, card_w - 2 * CARD_PADDING, 14)
        )
        desc.setStringValue_("Pick a default — you can change everything later in Settings.")
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        btn_w = card_w - 2 * CARD_PADDING
        btn_h = 42
        start_y = card_y + card_h - 98

        def add_choice(label: str, subtitle: str, action: str, y_pos: int) -> None:
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(base_x, y_pos, btn_w, btn_h))
            btn.setTitle_(label)
            btn.setBezelStyle_(NSBezelStyleRounded)
            btn.setFont_(NSFont.systemFontOfSize_(13))
            h = _WizardActionHandler.alloc().initWithController_action_(self, action)
            btn.setTarget_(h)
            btn.setAction_(objc.selector(h.performAction_, signature=b"v@:@"))
            self._handler_refs.append(h)
            parent_view.addSubview_(btn)

            sub = NSTextField.alloc().initWithFrame_(
                NSMakeRect(base_x + 12, y_pos - 16, btn_w - 24, 14)
            )
            sub.setStringValue_(subtitle)
            sub.setBezeled_(False)
            sub.setDrawsBackground_(False)
            sub.setEditable_(False)
            sub.setSelectable_(False)
            sub.setFont_(NSFont.systemFontOfSize_weight_(10, NSFontWeightMedium))
            sub.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55))
            parent_view.addSubview_(sub)

        add_choice(
            "Fast",
            "Ultra low latency (Deepgram streaming if configured; otherwise fast local).",
            "choose_fast",
            start_y,
        )
        add_choice(
            "Private",
            "Local-only dictation (no audio leaves your Mac).",
            "choose_private",
            start_y - 70,
        )
        add_choice(
            "Advanced",
            "Configure providers, models and refine settings.",
            "choose_advanced",
            start_y - 140,
        )

    def _build_step_permissions(self, parent_view, content_h: int) -> None:
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
        import objc  # type: ignore[import-not-found]

        card_w = WIZARD_WIDTH - 2 * PADDING
        card_h = 250
        card_y = content_h - card_h - 10
        card = _create_card(PADDING, card_y, card_w, card_h)
        parent_view.addSubview_(card)

        base_x = PADDING + CARD_PADDING
        right_edge = WIZARD_WIDTH - PADDING - CARD_PADDING

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_h - 28, 320, 18)
        )
        title.setStringValue_("Permissions")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_h - 46, card_w - 2 * CARD_PADDING, 14)
        )
        desc.setStringValue_("Grant these once so dictation + auto‑paste work reliably.")
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        label_w = 130
        status_x = base_x + label_w + 8
        btn_w = 72
        btn_h = 22
        spacing = 8
        req_x = right_edge - btn_w
        open_x = req_x - spacing - btn_w
        status_w = max(80, open_x - status_x - 8)

        def add_row(
            row_y: int,
            label_text: str,
            status_attr: str,
            open_anchor: str,
            request_action: str,
        ) -> None:
            label = NSTextField.alloc().initWithFrame_(NSMakeRect(base_x, row_y + 4, label_w, 16))
            label.setStringValue_(label_text)
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
            label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.75))
            parent_view.addSubview_(label)

            status = NSTextField.alloc().initWithFrame_(NSMakeRect(status_x, row_y + 2, status_w, 18))
            status.setStringValue_("…")
            status.setBezeled_(False)
            status.setDrawsBackground_(False)
            status.setEditable_(False)
            status.setSelectable_(False)
            status.setFont_(NSFont.systemFontOfSize_(11))
            status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
            parent_view.addSubview_(status)
            setattr(self, status_attr, status)

            open_btn = NSButton.alloc().initWithFrame_(NSMakeRect(open_x, row_y, btn_w, btn_h))
            open_btn.setTitle_("Open")
            open_btn.setBezelStyle_(NSBezelStyleRounded)
            open_btn.setFont_(NSFont.systemFontOfSize_(11))
            h_open = _WizardActionHandler.alloc().initWithController_action_(self, f"open:{open_anchor}")
            open_btn.setTarget_(h_open)
            open_btn.setAction_(objc.selector(h_open.performAction_, signature=b"v@:@"))
            self._handler_refs.append(h_open)
            parent_view.addSubview_(open_btn)

            req_btn = NSButton.alloc().initWithFrame_(NSMakeRect(req_x, row_y, btn_w, btn_h))
            req_btn.setTitle_("Request")
            req_btn.setBezelStyle_(NSBezelStyleRounded)
            req_btn.setFont_(NSFont.systemFontOfSize_(11))
            h_req = _WizardActionHandler.alloc().initWithController_action_(self, request_action)
            req_btn.setTarget_(h_req)
            req_btn.setAction_(objc.selector(h_req.performAction_, signature=b"v@:@"))
            self._handler_refs.append(h_req)
            parent_view.addSubview_(req_btn)

        row_y = card_y + card_h - 84
        add_row(row_y, "Microphone", "_perm_mic_status_label", "Privacy_Microphone", "request_mic")
        add_row(row_y - 32, "Accessibility", "_perm_access_status_label", "Privacy_Accessibility", "request_access")
        add_row(row_y - 64, "Input Monitoring", "_perm_input_status_label", "Privacy_ListenEvent", "request_input")

        refresh_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 16, 120, 24)
        )
        refresh_btn.setTitle_("Refresh")
        refresh_btn.setBezelStyle_(NSBezelStyleRounded)
        refresh_btn.setFont_(NSFont.systemFontOfSize_(11))
        h_refresh = _WizardActionHandler.alloc().initWithController_action_(self, "refresh_perms")
        refresh_btn.setTarget_(h_refresh)
        refresh_btn.setAction_(objc.selector(h_refresh.performAction_, signature=b"v@:@"))
        self._handler_refs.append(h_refresh)
        parent_view.addSubview_(refresh_btn)

    def _build_step_hotkey(self, parent_view, content_h: int) -> None:
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
        import objc  # type: ignore[import-not-found]

        card_w = WIZARD_WIDTH - 2 * PADDING
        card_h = min(340, max(250, content_h - 20))
        card_y = content_h - card_h - 10
        card = _create_card(PADDING, card_y, card_w, card_h)
        parent_view.addSubview_(card)

        base_x = PADDING + CARD_PADDING
        content_w = card_w - 2 * CARD_PADDING

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_h - 28, 320, 18)
        )
        title.setStringValue_("Hotkey")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_h - 72, content_w, 34)
        )
        desc.setStringValue_(
            "Pick a hotkey.\nHotkey changes apply immediately."
        )
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        btn_w = content_w
        btn_h = 32
        btn_y = card_y + card_h - 120

        def add_btn(label: str, action: str, y_pos: int) -> None:
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(base_x, y_pos, btn_w, btn_h))
            btn.setTitle_(label)
            btn.setBezelStyle_(NSBezelStyleRounded)
            btn.setFont_(NSFont.systemFontOfSize_(12))
            h = _WizardActionHandler.alloc().initWithController_action_(self, action)
            btn.setTarget_(h)
            btn.setAction_(objc.selector(h.performAction_, signature=b"v@:@"))
            self._handler_refs.append(h)
            parent_view.addSubview_(btn)

        add_btn("Use F19 (Toggle)", "hotkey_f19_toggle", btn_y)
        add_btn("Use Fn/Globe (Hold)", "hotkey_fn_hold", btn_y - 42)
        add_btn("Use Option+Space (Toggle)", "hotkey_opt_space", btn_y - 84)

        # Custom hotkeys (record)
        label_w = 64
        record_w = 80
        field_x = base_x + label_w + 8
        field_w = max(120, btn_w - label_w - 8 - record_w - 8)
        record_x = field_x + field_w + 8

        toggle_y = card_y + 80
        hold_y = card_y + 46

        def add_row(kind: str, title_text: str, y_pos: int) -> tuple[object, object]:
            label = NSTextField.alloc().initWithFrame_(NSMakeRect(base_x, y_pos + 4, label_w, 16))
            label.setStringValue_(title_text)
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
            label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.75))
            parent_view.addSubview_(label)

            field = NSTextField.alloc().initWithFrame_(NSMakeRect(field_x, y_pos, field_w, 24))
            field.setPlaceholderString_("Record…")
            field.setFont_(NSFont.systemFontOfSize_(13))
            field.setAlignment_(NSTextAlignmentCenter)
            field.setEditable_(False)
            field.setSelectable_(True)
            parent_view.addSubview_(field)

            rec_btn = NSButton.alloc().initWithFrame_(NSMakeRect(record_x, y_pos, record_w, 24))
            rec_btn.setTitle_("Record")
            rec_btn.setBezelStyle_(NSBezelStyleRounded)
            rec_btn.setFont_(NSFont.systemFontOfSize_(11))
            h = _WizardActionHandler.alloc().initWithController_action_(self, f"record_hotkey:{kind}")
            rec_btn.setTarget_(h)
            rec_btn.setAction_(objc.selector(h.performAction_, signature=b"v@:@"))
            self._handler_refs.append(h)
            parent_view.addSubview_(rec_btn)

            return field, rec_btn

        self._toggle_hotkey_field, self._toggle_record_btn = add_row("toggle", "Toggle:", toggle_y)
        self._hold_hotkey_field, self._hold_record_btn = add_row("hold", "Hold:", hold_y)
        self._sync_hotkey_fields_from_env()

        hint = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 18, btn_w, 18)
        )
        hint.setStringValue_("Tip: Hold is push‑to‑talk (may require Input Monitoring).")
        hint.setBezeled_(False)
        hint.setDrawsBackground_(False)
        hint.setEditable_(False)
        hint.setSelectable_(False)
        hint.setFont_(NSFont.systemFontOfSize_(10))
        hint.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55))
        parent_view.addSubview_(hint)

    def _build_step_test_dictation(self, parent_view, content_h: int) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelBorder,
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSScrollView,
            NSTextField,
            NSTextView,
        )
        import objc  # type: ignore[import-not-found]

        card_w = WIZARD_WIDTH - 2 * PADDING
        card_h = min(380, content_h - 20)
        card_y = content_h - card_h - 10
        card = _create_card(PADDING, card_y, card_w, card_h)
        parent_view.addSubview_(card)

        base_x = PADDING + CARD_PADDING
        content_w = card_w - 2 * CARD_PADDING

        title = NSTextField.alloc().initWithFrame_(NSMakeRect(base_x, card_y + card_h - 28, 320, 18))
        title.setStringValue_("Test dictation (safe)")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        top_y = card_y + card_h
        desc_h = 34
        desc_y = (top_y - 28) - 6 - desc_h
        desc = NSTextField.alloc().initWithFrame_(NSMakeRect(base_x, desc_y, content_w, desc_h))
        desc.setStringValue_(
            "This will not auto‑paste.\nUse your hotkey to start/stop. Transcript appears here."
        )
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        controls_y = desc_y - 10 - 30
        test_btn = NSButton.alloc().initWithFrame_(NSMakeRect(base_x, controls_y, 170, 30))
        test_btn.setTitle_("Start Test")
        test_btn.setBezelStyle_(NSBezelStyleRounded)
        test_btn.setFont_(NSFont.systemFontOfSize_(12))
        h = _WizardActionHandler.alloc().initWithController_action_(self, "test_toggle")
        test_btn.setTarget_(h)
        test_btn.setAction_(objc.selector(h.performAction_, signature=b"v@:@"))
        self._handler_refs.append(h)
        parent_view.addSubview_(test_btn)
        self._test_btn = test_btn

        status = NSTextField.alloc().initWithFrame_(NSMakeRect(base_x + 180, controls_y + 5, content_w - 180, 20))
        status.setStringValue_("Press your hotkey to start")
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_(11))
        status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(status)
        self._test_status_label = status

        scroll_y = card_y + 18
        scroll_top = controls_y - 12
        scroll_h = max(140, int(scroll_top - scroll_y))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(base_x, scroll_y, content_w, scroll_h))
        scroll.setBorderType_(NSBezelBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        try:
            scroll.setDrawsBackground_(False)
        except Exception:
            pass

        text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, content_w, scroll_h))
        text_view.setFont_(NSFont.systemFontOfSize_(12))
        text_view.setTextColor_(NSColor.whiteColor())
        try:
            text_view.setDrawsBackground_(False)
        except Exception:
            pass
        text_view.setEditable_(False)
        text_view.setSelectable_(True)
        text_view.setString_("")
        tc = text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)

        scroll.setDocumentView_(text_view)
        parent_view.addSubview_(scroll)
        self._test_text_view = text_view

    def _build_step_cheat_sheet(self, parent_view, content_h: int) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        card_w = WIZARD_WIDTH - 2 * PADDING
        card_h = 240
        card_y = content_h - card_h - 10
        card = _create_card(PADDING, card_y, card_w, card_h)
        parent_view.addSubview_(card)

        base_x = PADDING + CARD_PADDING

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_h - 28, 320, 18)
        )
        title.setStringValue_("Cheat sheet")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        body = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 56, card_w - 2 * CARD_PADDING, 140)
        )
        body.setStringValue_(
            "• Press your hotkey and speak.\n"
            "• Release / toggle to stop.\n"
            "• WhisperGo copies + pastes into the frontmost app.\n\n"
            "If paste fails: grant Accessibility.\n"
            "If Fn/Hold hotkey fails: grant Input Monitoring."
        )
        body.setBezeled_(False)
        body.setDrawsBackground_(False)
        body.setEditable_(False)
        body.setSelectable_(False)
        body.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        body.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        parent_view.addSubview_(body)

    # ---------------------------------------------------------------------
    # Actions + State
    # ---------------------------------------------------------------------

    def _wizard_title(self, step: OnboardingStep) -> str:
        titles = {
            OnboardingStep.CHOOSE_GOAL: "Welcome to WhisperGo",
            OnboardingStep.PERMISSIONS: "Permissions",
            OnboardingStep.HOTKEY: "Hotkey",
            OnboardingStep.TEST_DICTATION: "Test dictation",
            OnboardingStep.CHEAT_SHEET: "All set",
        }
        return titles.get(step, "Setup")

    def _persist_step(self, step: OnboardingStep) -> None:
        if self._persist_progress:
            set_onboarding_step(step)

    def _set_step(self, step: OnboardingStep) -> None:
        if self._step == OnboardingStep.HOTKEY and step != OnboardingStep.HOTKEY:
            self._stop_hotkey_recording(cancelled=True)
        if (
            self._step == OnboardingStep.TEST_DICTATION
            and step != OnboardingStep.TEST_DICTATION
            and callable(self._on_disable_test_hotkey_mode)
        ):
            try:
                self._on_disable_test_hotkey_mode()
            except Exception:
                pass
        self._step = step
        self._persist_step(step)
        self._render()
        if step == OnboardingStep.TEST_DICTATION and callable(self._on_enable_test_hotkey_mode):
            try:
                self._on_enable_test_hotkey_mode()
            except Exception:
                pass

    def _can_advance(self) -> bool:
        if self._step == OnboardingStep.CHOOSE_GOAL:
            return self._choice is not None
        if self._step == OnboardingStep.PERMISSIONS:
            return get_microphone_permission_state() not in ("denied", "restricted")
        if self._step == OnboardingStep.HOTKEY:
            toggle = (get_env_setting("WHISPER_GO_TOGGLE_HOTKEY") or "").strip()
            hold = (get_env_setting("WHISPER_GO_HOLD_HOTKEY") or "").strip()
            return bool(toggle or hold)
        if self._step == OnboardingStep.TEST_DICTATION:
            return bool(self._test_successful)
        return True

    def _render(self) -> None:
        step = self._step
        if step == OnboardingStep.DONE:
            step = OnboardingStep.CHEAT_SHEET

        for s, view in self._step_views.items():
            try:
                view.setHidden_(s != step)
            except Exception:
                pass

        if self._step_label is not None:
            try:
                self._step_label.setStringValue_(self._wizard_title(step))
            except Exception:
                pass
        if self._progress_label is not None:
            try:
                idx = step_index(step)
                self._progress_label.setStringValue_(f"Step {idx}/{total_steps()}")
            except Exception:
                pass

        if self._back_btn is not None:
            try:
                self._back_btn.setHidden_(step == OnboardingStep.CHOOSE_GOAL)
            except Exception:
                pass

        if self._next_btn is not None:
            title = "Finish" if step == OnboardingStep.CHEAT_SHEET else "Next"
            try:
                self._next_btn.setTitle_(title)
                self._next_btn.setEnabled_(bool(self._can_advance()))
            except Exception:
                pass

        if step == OnboardingStep.HOTKEY:
            self._sync_hotkey_fields_from_env()

    def _handle_action(self, action: str) -> None:
        if action == "back":
            if self._step != OnboardingStep.CHOOSE_GOAL:
                self._set_step(prev_step(self._step))
            return

        if action == "next":
            if not self._can_advance():
                return
            if self._step == OnboardingStep.CHEAT_SHEET:
                self._complete()
                return
            self._set_step(next_step(self._step))
            if self._step == OnboardingStep.PERMISSIONS:
                self._refresh_permissions()
            return

        if action == "skip":
            self._complete()
            return

        # Choose goal
        if action in ("choose_fast", "choose_private", "choose_advanced"):
            if action == "choose_fast":
                self._choice = OnboardingChoice.FAST
                set_onboarding_choice(self._choice)
                if get_api_key("DEEPGRAM_API_KEY") or os.getenv("DEEPGRAM_API_KEY"):
                    save_env_setting("WHISPER_GO_MODE", "deepgram")
                else:
                    apply_local_preset_to_env(default_local_preset_fast())
            elif action == "choose_private":
                self._choice = OnboardingChoice.PRIVATE
                set_onboarding_choice(self._choice)
                apply_local_preset_to_env(default_local_preset_private())
            else:
                self._choice = OnboardingChoice.ADVANCED
                set_onboarding_choice(self._choice)

            if self._on_settings_changed:
                try:
                    self._on_settings_changed()
                except Exception:
                    pass

            self._set_step(OnboardingStep.PERMISSIONS)
            return

        # Permissions actions
        if action.startswith("open:"):
            anchor = action.split(":", 1)[1]
            self._open_privacy_settings(anchor)
            return
        if action == "request_mic":
            check_microphone_permission(show_alert=False, request=True)
            self._refresh_permissions()
            return
        if action == "request_access":
            check_accessibility_permission(show_alert=False, request=True)
            self._refresh_permissions()
            return
        if action == "request_input":
            check_input_monitoring_permission(show_alert=False, request=True)
            self._refresh_permissions()
            return
        if action == "refresh_perms":
            self._refresh_permissions()
            return

        # Hotkey presets (saved to .env, applied immediately)
        if action in ("hotkey_f19_toggle", "hotkey_fn_hold", "hotkey_opt_space"):
            self._stop_hotkey_recording(cancelled=True)
            if action == "hotkey_fn_hold":
                save_env_setting("WHISPER_GO_HOLD_HOTKEY", "fn")
                remove_env_setting("WHISPER_GO_TOGGLE_HOTKEY")
            elif action == "hotkey_opt_space":
                save_env_setting("WHISPER_GO_TOGGLE_HOTKEY", "option+space")
                remove_env_setting("WHISPER_GO_HOLD_HOTKEY")
            else:
                save_env_setting("WHISPER_GO_TOGGLE_HOTKEY", "f19")
                remove_env_setting("WHISPER_GO_HOLD_HOTKEY")

            # Remove legacy single-hotkey keys if present.
            remove_env_setting("WHISPER_GO_HOTKEY")
            remove_env_setting("WHISPER_GO_HOTKEY_MODE")

            if self._on_settings_changed:
                try:
                    self._on_settings_changed()
                except Exception:
                    pass
            self._sync_hotkey_fields_from_env()
            self._render()
            return

        if action.startswith("record_hotkey:"):
            kind = action.split(":", 1)[1].strip().lower()
            if kind in ("toggle", "hold"):
                self._toggle_hotkey_recording(kind)
            return

        if action == "test_toggle":
            self._toggle_test_dictation()
            return

    def _open_privacy_settings(self, anchor: str) -> None:
        url = f"x-apple.systempreferences:com.apple.preference.security?{anchor}"
        try:
            subprocess.Popen(["open", url])
        except Exception:
            pass

    def _refresh_permissions(self) -> None:
        from AppKit import NSColor  # type: ignore[import-not-found]

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

        mic_state = get_microphone_permission_state()
        if mic_state == "authorized":
            set_status(self._perm_mic_status_label, "✅ Granted", ok_color)
        elif mic_state == "not_determined":
            set_status(self._perm_mic_status_label, "⚠ Not requested yet", warn_color)
        elif mic_state in ("denied", "restricted"):
            set_status(self._perm_mic_status_label, "❌ Denied", err_color)
        else:
            set_status(self._perm_mic_status_label, "Unknown", neutral_color)

        acc_ok = check_accessibility_permission(show_alert=False)
        set_status(
            self._perm_access_status_label,
            "✅ Granted" if acc_ok else "⚠ Not granted",
            ok_color if acc_ok else warn_color,
        )

        input_ok = check_input_monitoring_permission(show_alert=False)
        set_status(
            self._perm_input_status_label,
            "✅ Granted" if input_ok else "⚠ Not granted",
            ok_color if input_ok else warn_color,
        )

        self._render()

    # ---------------------------------------------------------------------
    # Hotkey recording
    # ---------------------------------------------------------------------

    def _sync_hotkey_fields_from_env(self) -> None:
        if self._hotkey_recording:
            return
        try:
            toggle = (get_env_setting("WHISPER_GO_TOGGLE_HOTKEY") or "").strip()
            hold = (get_env_setting("WHISPER_GO_HOLD_HOTKEY") or "").strip()
            if self._toggle_hotkey_field is not None:
                self._toggle_hotkey_field.setStringValue_(toggle.upper())
            if self._hold_hotkey_field is not None:
                self._hold_hotkey_field.setStringValue_(hold.upper())
        except Exception:
            return

    def _toggle_hotkey_recording(self, kind: str) -> None:
        if self._hotkey_recording:
            self._stop_hotkey_recording(cancelled=True)
        else:
            self._start_hotkey_recording(kind)

    def _start_hotkey_recording(self, kind: str) -> None:
        from utils.hotkey_recording import add_local_hotkey_monitor

        if kind == "toggle":
            field = self._toggle_hotkey_field
            btn = self._toggle_record_btn
        elif kind == "hold":
            field = self._hold_hotkey_field
            btn = self._hold_record_btn
        else:
            return

        if field is None or btn is None:
            return

        self._record_target_field = field
        self._record_target_btn = btn
        self._hotkey_recording_kind = kind
        self._record_prev_value = str(field.stringValue() or "")
        self._hotkey_recording = True

        if self._toggle_record_btn is not None:
            self._toggle_record_btn.setTitle_("Record")
        if self._hold_record_btn is not None:
            self._hold_record_btn.setTitle_("Record")
        btn.setTitle_("Press…")

        try:
            field.setStringValue_("")
            field.setPlaceholderString_("Press desired hotkey…")
        except Exception:
            pass

        def on_hotkey(hotkey_str: str) -> None:
            if not self._hotkey_recording:
                return
            self._apply_recorded_hotkey(kind, hotkey_str)
            if self._record_target_field is not None:
                try:
                    self._record_target_field.setStringValue_(hotkey_str.upper())
                except Exception:
                    pass
            self._stop_hotkey_recording(cancelled=False)

        def on_cancel() -> None:
            self._stop_hotkey_recording(cancelled=True)

        self._hotkey_monitor = add_local_hotkey_monitor(
            on_hotkey=on_hotkey, on_cancel=on_cancel
        )

    def _stop_hotkey_recording(self, *, cancelled: bool = False) -> None:
        from AppKit import NSEvent  # type: ignore[import-not-found]

        if cancelled and self._record_target_field is not None and self._record_prev_value is not None:
            try:
                self._record_target_field.setStringValue_(self._record_prev_value)
            except Exception:
                pass

        self._hotkey_recording = False
        self._hotkey_recording_kind = None
        self._record_prev_value = None
        if self._toggle_record_btn is not None:
            self._toggle_record_btn.setTitle_("Record")
        if self._hold_record_btn is not None:
            self._hold_record_btn.setTitle_("Record")
        if self._record_target_field is not None:
            try:
                self._record_target_field.setPlaceholderString_(None)
            except Exception:
                pass
        self._record_target_field = None
        self._record_target_btn = None
        if self._hotkey_monitor is not None:
            try:
                NSEvent.removeMonitor_(self._hotkey_monitor)
            except Exception:
                pass
            self._hotkey_monitor = None

    def _apply_recorded_hotkey(self, kind: str, hotkey_str: str) -> None:
        value = (hotkey_str or "").strip().lower()
        if not value:
            return

        if kind == "hold":
            save_env_setting("WHISPER_GO_HOLD_HOTKEY", value)
        else:
            save_env_setting("WHISPER_GO_TOGGLE_HOTKEY", value)

        # Remove legacy single-hotkey keys if present.
        remove_env_setting("WHISPER_GO_HOTKEY")
        remove_env_setting("WHISPER_GO_HOTKEY_MODE")

        if self._on_settings_changed:
            try:
                self._on_settings_changed()
            except Exception:
                pass

        self._sync_hotkey_fields_from_env()
        self._render()

    # ---------------------------------------------------------------------
    # Test dictation
    # ---------------------------------------------------------------------

    def _toggle_test_dictation(self) -> None:
        if self._test_btn is None:
            return
        if self._test_state == "stopping":
            return
        if self._test_state == "recording":
            if callable(self._on_test_dictation_stop):
                try:
                    self._on_test_dictation_stop()
                except Exception:
                    pass
            self._test_state = "stopping"
            try:
                self._test_btn.setEnabled_(False)
            except Exception:
                pass
            if self._test_status_label is not None:
                self._test_status_label.setStringValue_("Processing…")
            return

        # Start
        self._test_successful = False
        self._test_state = "recording"
        self._render()
        if self._test_text_view is not None:
            try:
                self._test_text_view.setString_("")
            except Exception:
                pass
        if self._test_status_label is not None:
            self._test_status_label.setStringValue_("Listening…")
        self._test_btn.setTitle_("Stop")
        try:
            self._test_btn.setEnabled_(True)
        except Exception:
            pass

        if callable(self._on_test_dictation_start):
            try:
                self._on_test_dictation_start()
            except Exception as e:
                self._test_state = "idle"
                if self._test_status_label is not None:
                    self._test_status_label.setStringValue_(f"Error: {e}")
                self._test_btn.setTitle_("Start Test")
                try:
                    self._test_btn.setEnabled_(True)
                except Exception:
                    pass
        else:
            self._test_state = "idle"
            if self._test_status_label is not None:
                self._test_status_label.setStringValue_("Test dictation not available.")
            self._test_btn.setTitle_("Start Test")
            try:
                self._test_btn.setEnabled_(True)
            except Exception:
                pass

    def on_test_dictation_hotkey_state(self, state: str) -> None:
        """Keeps the test step UI in sync when the user uses the hotkey."""
        if self._step != OnboardingStep.TEST_DICTATION:
            return
        normalized = (state or "").strip().lower()
        if normalized == "recording":
            if self._test_state == "recording":
                return
            self._test_successful = False
            self._test_state = "recording"
            if self._test_text_view is not None:
                try:
                    self._test_text_view.setString_("")
                except Exception:
                    pass
            if self._test_status_label is not None:
                self._test_status_label.setStringValue_("Listening…")
            if self._test_btn is not None:
                try:
                    self._test_btn.setTitle_("Stop")
                    self._test_btn.setEnabled_(True)
                except Exception:
                    pass
            self._render()
            return

        if normalized in ("stopping", "processing"):
            if self._test_state != "recording":
                return
            self._test_state = "stopping"
            if self._test_btn is not None:
                try:
                    self._test_btn.setEnabled_(False)
                except Exception:
                    pass
            if self._test_status_label is not None:
                self._test_status_label.setStringValue_("Processing…")
            self._render()
            return

    def on_test_dictation_result(self, transcript: str, error: str | None = None) -> None:
        self._test_state = "idle"
        if self._test_btn is not None:
            try:
                self._test_btn.setTitle_("Start Test")
                self._test_btn.setEnabled_(True)
            except Exception:
                pass

        if error:
            self._test_successful = False
            if self._test_status_label is not None:
                self._test_status_label.setStringValue_(f"Error: {error}")
        else:
            cleaned = (transcript or "").strip()
            self._test_successful = bool(cleaned)
            if self._test_status_label is not None:
                self._test_status_label.setStringValue_(
                    "✅ Success" if cleaned else "No speech detected — try again."
                )

        if self._test_text_view is not None:
            try:
                self._test_text_view.setString_(transcript or "")
            except Exception:
                pass

        self._render()

    # ---------------------------------------------------------------------
    # Completion
    # ---------------------------------------------------------------------

    def _complete(self) -> None:
        # Persist completion for first-run flow.
        set_onboarding_step(OnboardingStep.DONE)
        set_onboarding_seen(True)
        try:
            self.close()
        finally:
            if self._on_complete:
                try:
                    self._on_complete()
                except Exception:
                    pass


def _create_wizard_action_handler_class():
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class WizardActionHandler(NSObject):
        def initWithController_action_(self, controller, action):
            self = objc.super(WizardActionHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            self._action = action
            return self

        @objc.signature(b"v@:@")
        def performAction_(self, _sender) -> None:
            self._controller._handle_action(self._action)

    return WizardActionHandler


_WizardActionHandler = _create_wizard_action_handler_class()
