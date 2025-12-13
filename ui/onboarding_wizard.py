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
from ui.hotkey_card import HotkeyCard
from utils.hotkey_recording import HotkeyRecorder
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
    apply_hotkey_setting,
    remove_env_setting,
    save_env_setting,
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

LANGUAGE_OPTIONS = ["auto", "de", "en", "es", "fr", "it", "pt", "nl", "pl", "ru", "zh"]


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
        self._on_test_dictation_cancel: Callable[[], None] | None = None
        self._on_enable_test_hotkey_mode: Callable[[], None] | None = None
        self._on_disable_test_hotkey_mode: Callable[[], None] | None = None

        # Determine initial step and choice:
        # - If .env doesn't exist → always start fresh (settings are gone)
        # - If persist_progress=True AND there's saved progress → continue
        # - Otherwise start fresh (first run or manual re-run from settings)
        from utils.preferences import env_file_exists

        has_env = env_file_exists()
        saved_step = get_onboarding_step() if persist_progress and has_env else None
        saved_choice = get_onboarding_choice() if persist_progress and has_env else None

        if saved_step and saved_step != OnboardingStep.CHOOSE_GOAL:
            # Continue from saved progress (user restarted mid-wizard)
            self._step = saved_step
            self._choice = saved_choice
            if self._step == OnboardingStep.DONE:
                self._step = OnboardingStep.CHEAT_SHEET
        else:
            # Fresh start: no .env, no saved progress, or manual re-run
            self._step = OnboardingStep.CHOOSE_GOAL
            self._choice = None

        self._step_label = None
        self._progress_label = None
        self._back_btn = None
        self._next_btn = None
        self._skip_btn = None
        self._step_views: dict[OnboardingStep, object] = {}

        # Permissions UI (shared component)
        self._permissions_card = None

        # Test dictation widgets/state
        self._test_status_label = None
        self._test_hotkey_label = None
        self._test_text_view = None
        self._test_successful = False
        self._test_state = "idle"  # idle|recording|stopping

        # Hotkey card (shared component)
        self._hotkey_card: HotkeyCard | None = None
        self._hotkey_recorder = HotkeyRecorder()

        # Summary step (dynamic labels)
        self._summary_provider_label = None
        self._summary_hotkey_label = None
        self._summary_perm_label = None

        # Language selector
        self._lang_popup = None

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

    def set_test_dictation_callbacks(
        self,
        *,
        start: Callable[[], None],
        stop: Callable[[], None],
        cancel: Callable[[], None] | None = None,
    ) -> None:
        self._on_test_dictation_start = start
        self._on_test_dictation_stop = stop
        self._on_test_dictation_cancel = cancel or stop  # Fallback to stop

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
        self._stop_permission_auto_refresh()
        if callable(self._on_disable_test_hotkey_mode):
            try:
                self._on_disable_test_hotkey_mode()
            except Exception:
                pass
        if self._window:
            self._window.close()

    def _ensure_window_focus(self) -> None:
        """Ensures the wizard window has keyboard focus after step changes."""
        if not self._window:
            return
        try:
            self._window.makeKeyAndOrderFront_(None)
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Window + Layout
    # ---------------------------------------------------------------------

    def _build_window(self) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSFloatingWindowLevel,
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
        # Wizard always stays on top during setup
        self._window.setLevel_(NSFloatingWindowLevel)

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

        self._build_step_choose_goal(
            self._step_views[OnboardingStep.CHOOSE_GOAL], content_h
        )
        self._build_step_permissions(
            self._step_views[OnboardingStep.PERMISSIONS], content_h
        )
        self._build_step_hotkey(self._step_views[OnboardingStep.HOTKEY], content_h)
        self._build_step_test_dictation(
            self._step_views[OnboardingStep.TEST_DICTATION], content_h
        )
        self._build_step_cheat_sheet(
            self._step_views[OnboardingStep.CHEAT_SHEET], content_h
        )

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

        skip_btn = NSButton.alloc().initWithFrame_(NSMakeRect(PADDING, y, 120, btn_h))
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
            NSPopUpButton,
            NSTextField,
        )
        import objc  # type: ignore[import-not-found]

        card_w = WIZARD_WIDTH - 2 * PADDING
        card_h = 310
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
            NSMakeRect(base_x, card_y + card_h - 72, card_w - 2 * CARD_PADDING, 34)
        )
        desc.setStringValue_(
            "Pick a default — you can change everything later in Settings."
        )
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
            btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(base_x, y_pos, btn_w, btn_h)
            )
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
            "Ultra low latency (Deepgram streaming if configured).",
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

        # Language selector
        lang_y = card_y + 16
        lang_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, lang_y + 2, 70, 18)
        )
        lang_label.setStringValue_("Language:")
        lang_label.setBezeled_(False)
        lang_label.setDrawsBackground_(False)
        lang_label.setEditable_(False)
        lang_label.setSelectable_(False)
        lang_label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        lang_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        parent_view.addSubview_(lang_label)

        lang_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(base_x + 74, lang_y, 120, 22)
        )
        lang_popup.setFont_(NSFont.systemFontOfSize_(11))
        for lang in LANGUAGE_OPTIONS:
            lang_popup.addItemWithTitle_(lang)
        current_lang = get_env_setting("WHISPER_GO_LANGUAGE") or "auto"
        if current_lang in LANGUAGE_OPTIONS:
            lang_popup.selectItemWithTitle_(current_lang)
        self._lang_popup = lang_popup
        parent_view.addSubview_(lang_popup)

        lang_hint = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x + 200, lang_y + 3, btn_w - 200, 16)
        )
        lang_hint.setStringValue_("auto = detect from speech")
        lang_hint.setBezeled_(False)
        lang_hint.setDrawsBackground_(False)
        lang_hint.setEditable_(False)
        lang_hint.setSelectable_(False)
        lang_hint.setFont_(NSFont.systemFontOfSize_(10))
        lang_hint.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.5))
        parent_view.addSubview_(lang_hint)

    def _build_step_permissions(self, parent_view, content_h: int) -> None:
        import objc  # type: ignore[import-not-found]
        from ui.permissions_card import PermissionsCard

        card_h = 250
        card_y = content_h - card_h - 10

        def bind_action(btn, action: str) -> None:
            h = _WizardActionHandler.alloc().initWithController_action_(self, action)
            btn.setTarget_(h)
            btn.setAction_(objc.selector(h.performAction_, signature=b"v@:@"))
            self._handler_refs.append(h)

        self._permissions_card = PermissionsCard.build(
            parent_view=parent_view,
            window_width=WIZARD_WIDTH,
            card_y=card_y,
            card_height=card_h,
            outer_padding=PADDING,
            inner_padding=CARD_PADDING,
            title="Permissions",
            description=(
                "Microphone is required. Accessibility improves auto‑paste.\n"
                "Input Monitoring enables Hold + some global hotkeys."
            ),
            bind_action=bind_action,
            after_refresh=self._render,
        )

    def _build_step_hotkey(self, parent_view, content_h: int) -> None:
        import objc  # type: ignore[import-not-found]

        card_h = min(300, max(250, content_h - 20))
        card_y = content_h - card_h - 10

        def bind_action(btn, action: str) -> None:
            h = _WizardActionHandler.alloc().initWithController_action_(self, action)
            btn.setTarget_(h)
            btn.setAction_(objc.selector(h.performAction_, signature=b"v@:@"))
            self._handler_refs.append(h)

        self._hotkey_card = HotkeyCard.build(
            parent_view=parent_view,
            window_width=WIZARD_WIDTH,
            card_y=card_y,
            card_height=card_h,
            outer_padding=PADDING,
            inner_padding=CARD_PADDING,
            title="Hotkey",
            description=(
                "Set your hotkey to start transcription.\n"
                "Toggle: Press → speak → press again.  Hold: Hold → speak → release."
            ),
            bind_action=bind_action,
            hotkey_recorder=self._hotkey_recorder,
            on_hotkey_change=self._apply_hotkey_change,
            on_after_change=self._render,
            show_presets=True,
            show_hint=True,
        )

    def _build_step_test_dictation(self, parent_view, content_h: int) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelBorder,
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSScrollView,
            NSTextField,
            NSTextView,
        )

        card_w = WIZARD_WIDTH - 2 * PADDING
        card_h = min(380, content_h - 20)
        card_y = content_h - card_h - 10
        card = _create_card(PADDING, card_y, card_w, card_h)
        parent_view.addSubview_(card)

        base_x = PADDING + CARD_PADDING
        content_w = card_w - 2 * CARD_PADDING

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_h - 28, 320, 18)
        )
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
        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, desc_y, content_w, desc_h)
        )
        desc.setStringValue_(
            "This will not auto‑paste.\n"
            "Use your hotkeys to start/stop. Transcript appears here."
        )
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        hotkeys_h = 46
        hotkeys_y = desc_y - 10 - hotkeys_h
        hotkeys = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, hotkeys_y, content_w, hotkeys_h)
        )
        hotkeys.setStringValue_("")
        hotkeys.setBezeled_(False)
        hotkeys.setDrawsBackground_(False)
        hotkeys.setEditable_(False)
        hotkeys.setSelectable_(False)
        hotkeys.setFont_(NSFont.systemFontOfSize_(11))
        hotkeys.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        try:
            hotkeys.setUsesSingleLineMode_(False)
        except Exception:
            pass
        parent_view.addSubview_(hotkeys)
        self._test_hotkey_label = hotkeys

        status_y = hotkeys_y - 8 - 18
        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, status_y, content_w, 18)
        )
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
        scroll_top = status_y - 12
        scroll_h = max(140, int(scroll_top - scroll_y))
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(base_x, scroll_y, content_w, scroll_h)
        )
        scroll.setBorderType_(NSBezelBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        try:
            scroll.setDrawsBackground_(False)
        except Exception:
            pass

        text_view = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w, scroll_h)
        )
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
        card_h = 280
        card_y = content_h - card_h - 10
        card = _create_card(PADDING, card_y, card_w, card_h)
        parent_view.addSubview_(card)

        base_x = PADDING + CARD_PADDING
        content_w = card_w - 2 * CARD_PADDING

        # Titel
        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_h - 28, content_w, 18)
        )
        title.setStringValue_("✅ Your configuration")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        row_h = 28
        label_w = 90
        value_x = base_x + label_w
        value_w = content_w - label_w
        row_y = card_y + card_h - 60

        def add_label(y: int, text: str):
            lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(base_x, y, label_w, 16))
            lbl.setStringValue_(text)
            lbl.setBezeled_(False)
            lbl.setDrawsBackground_(False)
            lbl.setEditable_(False)
            lbl.setSelectable_(False)
            lbl.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
            lbl.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
            parent_view.addSubview_(lbl)

        def add_value(y: int):
            val = NSTextField.alloc().initWithFrame_(
                NSMakeRect(value_x, y, value_w, 16)
            )
            val.setStringValue_("")
            val.setBezeled_(False)
            val.setDrawsBackground_(False)
            val.setEditable_(False)
            val.setSelectable_(False)
            val.setFont_(NSFont.systemFontOfSize_(11))
            val.setTextColor_(NSColor.whiteColor())
            parent_view.addSubview_(val)
            return val

        # Provider row
        add_label(row_y, "Provider:")
        self._summary_provider_label = add_value(row_y)
        row_y -= row_h

        # Hotkeys row
        add_label(row_y, "Hotkeys:")
        self._summary_hotkey_label = add_value(row_y)
        row_y -= row_h

        # Permissions row
        add_label(row_y, "Permissions:")
        self._summary_perm_label = add_value(row_y)

        # Abschluss-Text
        ready_y = card_y + 50
        ready = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, ready_y, content_w, 50)
        )
        ready.setStringValue_(
            "You're ready to go! Press your hotkey anywhere to start dictating. "
            "WhisperGo will transcribe your speech and paste automatically.\n\n"
            "You can change all settings anytime via the menu bar icon."
        )
        ready.setBezeled_(False)
        ready.setDrawsBackground_(False)
        ready.setEditable_(False)
        ready.setSelectable_(False)
        ready.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        ready.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        parent_view.addSubview_(ready)

        more_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 22, 95, 16)
        )
        more_label.setStringValue_("More settings:")
        more_label.setBezeled_(False)
        more_label.setDrawsBackground_(False)
        more_label.setEditable_(False)
        more_label.setSelectable_(False)
        more_label.setFont_(NSFont.systemFontOfSize_(11))
        more_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(more_label)

        open_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x + 96, card_y + 18, 150, 24)
        )
        open_btn.setTitle_("Open Settings…")
        open_btn.setBezelStyle_(NSBezelStyleRounded)
        open_btn.setFont_(NSFont.systemFontOfSize_(11))
        h_open = _WizardActionHandler.alloc().initWithController_action_(
            self, "open_settings"
        )
        open_btn.setTarget_(h_open)
        open_btn.setAction_(objc.selector(h_open.performAction_, signature=b"v@:@"))
        self._handler_refs.append(h_open)
        parent_view.addSubview_(open_btn)

        # Initial update
        self._update_summary()

    def _update_summary(self) -> None:
        """Aktualisiert die Summary-Labels mit aktuellen Werten."""
        from utils.permissions import (
            has_accessibility_permission,
            has_input_monitoring_permission,
        )

        ok_color = _get_color(120, 255, 150)
        warn_color = _get_color(255, 200, 90)

        # Provider
        mode = (get_env_setting("WHISPER_GO_MODE") or "deepgram").strip()
        mode_display = {
            "deepgram": "Deepgram (Cloud, fastest)",
            "openai": "OpenAI Whisper (Cloud)",
            "groq": "Groq (Cloud, fast)",
            "local": "Local (Private, offline)",
        }.get(mode, mode.title())

        if self._summary_provider_label:
            try:
                self._summary_provider_label.setStringValue_(mode_display)
            except Exception:
                pass

        # Hotkeys
        toggle_hk = (get_env_setting("WHISPER_GO_TOGGLE_HOTKEY") or "").strip().upper()
        hold_hk = (get_env_setting("WHISPER_GO_HOLD_HOTKEY") or "").strip().upper()
        hotkey_parts = []
        if toggle_hk:
            hotkey_parts.append(f"Toggle: {toggle_hk}")
        if hold_hk:
            hotkey_parts.append(f"Hold: {hold_hk}")
        hotkey_display = " • ".join(hotkey_parts) if hotkey_parts else "Not configured"

        if self._summary_hotkey_label:
            try:
                self._summary_hotkey_label.setStringValue_(hotkey_display)
                self._summary_hotkey_label.setTextColor_(
                    ok_color if hotkey_parts else warn_color
                )
            except Exception:
                pass

        # Permissions
        mic_ok = get_microphone_permission_state() == "authorized"
        access_ok = has_accessibility_permission()
        input_ok = has_input_monitoring_permission()
        perm_parts = []
        if mic_ok:
            perm_parts.append("🎤 Mic ✓")
        if access_ok:
            perm_parts.append("♿ Accessibility ✓")
        if input_ok:
            perm_parts.append("⌨️ Input ✓")
        perm_display = "  ".join(perm_parts) if perm_parts else "No permissions granted"

        if self._summary_perm_label:
            try:
                self._summary_perm_label.setStringValue_(perm_display)
                self._summary_perm_label.setTextColor_(
                    ok_color if mic_ok else warn_color
                )
            except Exception:
                pass

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
            self._step == OnboardingStep.PERMISSIONS
            and step != OnboardingStep.PERMISSIONS
        ):
            self._stop_permission_auto_refresh()
        if (
            self._step == OnboardingStep.TEST_DICTATION
            and step != OnboardingStep.TEST_DICTATION
        ):
            # Cancel any active test dictation run and disable hotkey routing.
            # Use cancel (not stop) to discard pending results.
            if callable(self._on_test_dictation_cancel):
                try:
                    self._on_test_dictation_cancel()
                except Exception:
                    pass
            if callable(self._on_disable_test_hotkey_mode):
                try:
                    self._on_disable_test_hotkey_mode()
                except Exception:
                    pass
        self._step = step
        self._persist_step(step)
        self._render()
        if step == OnboardingStep.PERMISSIONS:
            self._refresh_permissions()
        if step == OnboardingStep.CHEAT_SHEET:
            self._update_summary()
        if step == OnboardingStep.TEST_DICTATION and callable(
            self._on_enable_test_hotkey_mode
        ):
            try:
                self._on_enable_test_hotkey_mode()
            except Exception:
                pass
        # Ensure wizard window has focus after step change.
        self._ensure_window_focus()

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
        if step == OnboardingStep.TEST_DICTATION:
            self._update_test_dictation_hotkeys()

    def _update_test_dictation_hotkeys(self) -> None:
        label = self._test_hotkey_label
        if label is None:
            return

        toggle = (get_env_setting("WHISPER_GO_TOGGLE_HOTKEY") or "").strip()
        hold = (get_env_setting("WHISPER_GO_HOLD_HOTKEY") or "").strip()

        def disp(value: str) -> str:
            return (value or "").strip().upper()

        lines: list[str] = []
        if toggle:
            lines.append(
                f"Toggle: {disp(toggle)} — press once to start, press again to stop."
            )
        if hold:
            lines.append(f"Hold: {disp(hold)} — hold to record, release to stop.")
        if not lines:
            lines.append("No hotkeys configured. Go back and set a hotkey first.")

        try:
            label.setStringValue_("\n".join(lines))
        except Exception:
            pass

    def _handle_action(self, action: str) -> None:
        if action == "back":
            if self._step != OnboardingStep.CHOOSE_GOAL:
                self._set_step(prev_step(self._step))
            return

        if action == "next":
            if not self._can_advance():
                return
            if self._step == OnboardingStep.CHEAT_SHEET:
                self._complete(open_settings=False)
                return
            self._set_step(next_step(self._step))
            return

        if action == "skip":
            self._complete(open_settings=False)
            return

        if action == "open_settings":
            self._complete(open_settings=True)
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

            # Save selected language
            if self._lang_popup:
                lang = self._lang_popup.titleOfSelectedItem()
                if lang and lang != "auto":
                    save_env_setting("WHISPER_GO_LANGUAGE", lang)
                else:
                    remove_env_setting("WHISPER_GO_LANGUAGE")

            if self._on_settings_changed:
                try:
                    self._on_settings_changed()
                except Exception:
                    pass

            self._set_step(OnboardingStep.PERMISSIONS)
            return

        # Permissions actions
        if action == "perm_mic":
            mic_state = get_microphone_permission_state()
            if mic_state == "not_determined":
                check_microphone_permission(show_alert=False, request=True)
            else:
                self._open_privacy_settings("Privacy_Microphone")
            self._kick_permission_auto_refresh()
            return
        if action == "perm_access":
            check_accessibility_permission(show_alert=False, request=True)
            self._open_privacy_settings("Privacy_Accessibility")
            self._kick_permission_auto_refresh()
            return
        if action == "perm_input":
            check_input_monitoring_permission(show_alert=False, request=True)
            self._open_privacy_settings("Privacy_ListenEvent")
            self._kick_permission_auto_refresh()
            return

        # Hotkey presets (delegated to HotkeyCard)
        if action in ("hotkey_f19_toggle", "hotkey_fn_hold", "hotkey_opt_space"):
            if self._hotkey_card:
                preset = action.replace("hotkey_", "")  # f19_toggle, fn_hold, opt_space
                self._hotkey_card.apply_preset(preset)
            return

        if action.startswith("record_hotkey:"):
            kind = action.split(":", 1)[1].strip().lower()
            if kind in ("toggle", "hold"):
                self._toggle_hotkey_recording(kind)
            return

        if action == "goto_hotkey":
            self._set_step(OnboardingStep.HOTKEY)
            return

    def _open_privacy_settings(self, anchor: str) -> None:
        url = f"x-apple.systempreferences:com.apple.preference.security?{anchor}"
        try:
            # Temporarily lower window level so System Settings appears in front
            if self._window:
                from AppKit import NSNormalWindowLevel  # type: ignore[import-not-found]

                self._window.setLevel_(NSNormalWindowLevel)
            subprocess.Popen(["open", url])
        except Exception:
            pass

    def _refresh_permissions(self) -> None:
        card = self._permissions_card
        if card is None:
            return
        try:
            card.refresh()
        except Exception:
            pass

    def _stop_permission_auto_refresh(self) -> None:
        card = self._permissions_card
        if card is None:
            return
        try:
            card.stop_auto_refresh()
        except Exception:
            pass

    def _kick_permission_auto_refresh(self) -> None:
        card = self._permissions_card
        if card is None:
            return
        try:
            card.kick_auto_refresh()
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Hotkey recording (delegated to HotkeyCard)
    # ---------------------------------------------------------------------

    def _sync_hotkey_fields_from_env(self) -> None:
        if self._hotkey_card:
            self._hotkey_card.sync_from_env()

    def _toggle_hotkey_recording(self, kind: str) -> None:
        if self._hotkey_card:
            self._hotkey_card.toggle_recording(kind)

    def _stop_hotkey_recording(self, *, cancelled: bool = False) -> None:
        if self._hotkey_card:
            self._hotkey_card.stop_recording(cancelled=cancelled)

    def _set_hotkey_status(self, level: str, message: str | None) -> None:
        if self._hotkey_card:
            self._hotkey_card.set_status(level, message or "")

    def _apply_hotkey_change(self, kind: str, hotkey_str: str) -> bool:
        from utils.hotkey_validation import validate_hotkey_change

        normalized, level, message = validate_hotkey_change(kind, hotkey_str)
        if level == "error":
            from utils.permissions import is_permission_related_message

            # No permission-related popups: the dedicated Permissions step covers this.
            if not is_permission_related_message(message):
                from utils.alerts import show_error_alert

                show_error_alert(
                    "Ungültiger Hotkey",
                    message or "Hotkey konnte nicht gesetzt werden.",
                )
            self._set_hotkey_status("error", message)
            self._sync_hotkey_fields_from_env()
            self._render()
            return False

        apply_hotkey_setting(kind, normalized)

        if self._on_settings_changed:
            try:
                self._on_settings_changed()
            except Exception:
                pass

        if level == "warning":
            self._set_hotkey_status("warning", message)
        else:
            self._set_hotkey_status("ok", "✓ Saved")

        self._sync_hotkey_fields_from_env()
        self._render()
        return True

    # ---------------------------------------------------------------------
    # Test dictation
    # ---------------------------------------------------------------------

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
            self._render()
            return

        if normalized in ("stopping", "processing"):
            if self._test_state != "recording":
                return
            self._test_state = "stopping"
            if self._test_status_label is not None:
                self._test_status_label.setStringValue_("Processing…")
            self._render()
            return

    def on_test_dictation_result(
        self, transcript: str, error: str | None = None
    ) -> None:
        self._test_state = "idle"

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

    def _complete(self, *, open_settings: bool = False) -> None:
        """Completes the wizard and optionally opens settings.

        Args:
            open_settings: If True, opens the Settings window after closing.
                          Only True when user clicks "Open Settings...".
        """
        # Persist completion for first-run flow.
        set_onboarding_step(OnboardingStep.DONE)
        set_onboarding_seen(True)
        try:
            self.close()
        finally:
            if open_settings and self._on_complete:
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
