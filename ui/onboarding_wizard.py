"""Standalone first-run onboarding wizard for PulseScribe."""

from __future__ import annotations

import os
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
from utils.onboarding_fast_choice import resolve_fast_choice_updates
from utils.permissions import (
    check_accessibility_permission,
    check_input_monitoring_permission,
    check_microphone_permission,
    get_microphone_permission_state,
)
from utils.preferences import (
    get_env_setting,
    get_onboarding_choice,
    get_onboarding_step,
    read_env_file,
    set_onboarding_choice,
    set_onboarding_step,
    set_onboarding_seen,
    update_env_settings,
)
from utils.presets import (
    apply_local_preset_to_env,
    default_local_preset_private,
)

WIZARD_WIDTH = 500
WIZARD_HEIGHT = 640
PADDING = 20
FOOTER_HEIGHT = 54
CARD_PADDING = 16
CARD_CORNER_RADIUS = 12

LANGUAGE_OPTIONS = ["auto", "de", "en", "es", "fr", "it", "pt", "nl", "pl", "ru", "zh"]
LANGUAGE_LABELS = {
    "auto": "Automatic",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "pl": "Polish",
    "ru": "Russian",
    "zh": "Chinese",
}
MODE_LABELS = {
    "deepgram": "Deepgram (Cloud, fastest)",
    "openai": "OpenAI Whisper (Cloud)",
    "groq": "Groq (Cloud, fast)",
    "local": "Local (Private, offline)",
}
HOTKEY_TOKEN_LABELS = {
    "command": "Command",
    "cmd": "Command",
    "control": "Control",
    "ctrl": "Control",
    "option": "Option",
    "opt": "Option",
    "alt": "Option",
    "shift": "Shift",
    "fn": "Fn",
    "space": "Space",
    "tab": "Tab",
    "enter": "Enter",
    "return": "Return",
    "esc": "Esc",
    "escape": "Esc",
    "capslock": "Caps Lock",
    "delete": "Delete",
    "backspace": "Backspace",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
}
TEST_DICTATION_EMPTY_TEXT = (
    "Your practice transcript will appear here.\n"
    "Nothing is pasted during this step."
)
TEST_DICTATION_STARTING_TEXT = (
    "Starting a safe practice dictation…\n"
    "PulseScribe is getting ready to listen only inside this window."
)
TEST_DICTATION_RECORDING_TEXT = (
    "Practice dictation is listening…\n"
    "Say a short sentence, then stop with your hotkey or the Stop button."
)
TEST_DICTATION_PROCESSING_TEXT = (
    "Practice dictation is finishing up…\n"
    "The result will appear here in a moment."
)
TEST_DICTATION_NO_SPEECH_TEXT = (
    "No speech was detected in the practice run.\n"
    "Try again with a short sentence."
)
TEST_DICTATION_ERROR_TEXT = (
    "The practice run could not be completed.\n"
    "Check the message above and try again."
)
TEST_DICTATION_CANCELLED_TEXT = (
    "The practice run was cancelled.\n"
    "You can start another safe test whenever you're ready."
)



def _format_language_label(language: str | None) -> str:
    normalized = (language or "auto").strip().lower()
    if not normalized:
        normalized = "auto"
    return LANGUAGE_LABELS.get(normalized, normalized.upper())



def _language_code_from_title(title: str | None) -> str:
    normalized = (title or "").strip()
    if not normalized:
        return "auto"
    for code, label in LANGUAGE_LABELS.items():
        if label == normalized:
            return code
    return normalized.lower()



def _format_hotkey_for_display(hotkey: str | None) -> str:
    normalized = (hotkey or "").strip()
    if not normalized:
        return ""

    display_parts: list[str] = []
    for raw_part in normalized.split("+"):
        part = raw_part.strip().lower()
        if not part:
            continue
        display = HOTKEY_TOKEN_LABELS.get(part)
        if display is None:
            if len(part) == 1 or (part.startswith("f") and part[1:].isdigit()):
                display = part.upper()
            else:
                display = part.replace("_", " ").title()
        display_parts.append(display)
    return "+".join(display_parts)



def _build_hotkey_summary_text(toggle: str, hold: str) -> tuple[str, bool]:
    parts: list[str] = []
    if toggle:
        parts.append(f"Toggle: {_format_hotkey_for_display(toggle)}")
    if hold:
        parts.append(f"Hold: {_format_hotkey_for_display(hold)}")
    if not parts:
        return "Choose a hotkey in the previous step", False
    return " • ".join(parts), True



def _build_permission_summary_text(
    mic_state: str,
    access_ok: bool,
    input_ok: bool,
) -> tuple[str, bool]:
    mic_ok = mic_state == "authorized"
    if not mic_ok:
        return "Microphone required", False
    if access_ok and input_ok:
        return "Microphone ready • Auto-paste and Hold hotkeys available", True
    if access_ok:
        return "Microphone ready • Auto-paste available", True
    if input_ok:
        return "Microphone ready • Hold hotkeys available", True
    return "Microphone ready • Optional extras still off", True



def _normalize_test_error_message(error: str | None) -> str:
    detail = " ".join((error or "").split())
    if not detail:
        return "PulseScribe couldn’t finish the practice test."

    detail_lower = detail.lower()
    if "already recording" in detail_lower or "busy" in detail_lower:
        return "PulseScribe is still busy with another dictation."
    if "microphone" in detail_lower or "permission" in detail_lower:
        return "Microphone access is unavailable for the practice test."
    if detail[0].islower():
        detail = detail[0].upper() + detail[1:]
    return detail



def _build_test_summary_text(outcome: str | None) -> tuple[str, str]:
    normalized = (outcome or "pending").strip().lower()
    if normalized == "passed":
        return "Completed", "ok"
    if normalized == "skipped":
        return "Skipped for now", "neutral"
    if normalized == "cancelled":
        return "Cancelled", "neutral"
    if normalized == "empty":
        return "Needs another try", "warn"
    if normalized == "error":
        return "Needs attention", "warn"
    if normalized in {"starting", "recording", "processing"}:
        return "In progress", "warn"
    return "Not run yet", "neutral"



def _build_test_status_text(outcome: str | None, *, error: str | None = None) -> str:
    normalized = (outcome or "pending").strip().lower()
    if normalized == "starting":
        return "Starting your practice dictation…"
    if normalized == "recording":
        return "Listening… say a short sentence."
    if normalized == "processing":
        return "Transcribing your practice dictation…"
    if normalized == "passed":
        return "✅ Practice dictation worked. You can continue."
    if normalized == "empty":
        return "No speech detected. Try again with a short sentence."
    if normalized == "cancelled":
        return "Practice dictation cancelled."
    if normalized == "skipped":
        return "Practice dictation skipped for now."
    if normalized == "error":
        detail = _normalize_test_error_message(error)
        return f"Couldn’t finish the practice test: {detail}"
    return "Use your hotkey or start the practice test below."



def _build_test_notice_feedback(
    outcome: str | None,
    *,
    error: str | None = None,
) -> tuple[str, str]:
    normalized = (outcome or "pending").strip().lower()
    if normalized == "starting":
        return (
            "PulseScribe is starting a safe practice run. Nothing will be pasted during this step.",
            "neutral",
        )
    if normalized == "recording":
        return (
            "Speak a short sentence, then stop with your hotkey or the Stop button.",
            "accent",
        )
    if normalized == "processing":
        return (
            "The recording has stopped. Wait a moment while PulseScribe prepares the result here.",
            "neutral",
        )
    if normalized == "passed":
        return (
            "Nothing was pasted. Continue when you're ready, or run one more quick check.",
            "success",
        )
    if normalized == "empty":
        return (
            "Try again with a slightly longer sentence or speak a little closer to the microphone.",
            "warn",
        )
    if normalized == "cancelled":
        return (
            "No problem — start another safe practice run whenever you're ready.",
            "neutral",
        )
    if normalized == "skipped":
        return (
            "You can always return to Setup & Settings and run the practice test later.",
            "neutral",
        )
    if normalized == "error":
        detail_lower = " ".join((error or "").split()).lower()
        if "already recording" in detail_lower or "busy" in detail_lower:
            return (
                "Wait for the current dictation to finish, then try the practice test again.",
                "warn",
            )
        if "microphone" in detail_lower or "permission" in detail_lower:
            return (
                "Check microphone access in the Permissions step, then try the practice test again.",
                "warn",
            )
        return (
            "Check the message above, then try the practice test again.",
            "warn",
        )
    return (
        "You can press your hotkey or use the buttons below. The transcript only appears here.",
        "neutral",
    )



def _build_test_primary_action_text(outcome: str | None) -> str:
    normalized = (outcome or "pending").strip().lower()
    if normalized == "passed":
        return "Run again"
    if normalized in {"empty", "error", "cancelled", "skipped"}:
        return "Try again"
    return "Start practice"



def _build_test_preview_text(outcome: str | None, transcript: str | None) -> str:
    cleaned = (transcript or "").strip()
    if cleaned:
        return cleaned
    normalized = (outcome or "pending").strip().lower()
    if normalized == "starting":
        return TEST_DICTATION_STARTING_TEXT
    if normalized == "recording":
        return TEST_DICTATION_RECORDING_TEXT
    if normalized == "processing":
        return TEST_DICTATION_PROCESSING_TEXT
    if normalized == "empty":
        return TEST_DICTATION_NO_SPEECH_TEXT
    if normalized == "error":
        return TEST_DICTATION_ERROR_TEXT
    if normalized in {"cancelled", "skipped"}:
        return TEST_DICTATION_CANCELLED_TEXT
    return TEST_DICTATION_EMPTY_TEXT



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
        self._step_builders: dict[OnboardingStep, Callable[[object, int], None]] = {}
        self._step_content_height = 0
        self._step_frame = None
        self._visible_step: OnboardingStep | None = None
        self._last_title_text: str | None = None
        self._last_progress_text: str | None = None
        self._last_back_hidden: bool | None = None
        self._last_next_title: str | None = None
        self._last_next_enabled: bool | None = None
        self._last_test_hotkey_text: str | None = None
        self._last_test_status_text: str | None = None
        self._last_test_status_level: str | None = None
        self._last_test_notice_text: str | None = None
        self._last_test_notice_level: str | None = None
        self._last_test_preview_text: str | None = None
        self._last_api_key_prompt_visible: bool | None = None
        self._last_api_key_prompt_message: str | None = None
        self._last_summary_provider_text: str | None = None
        self._last_summary_hotkey_text: str | None = None
        self._last_summary_hotkey_has_value: bool | None = None
        self._last_summary_perm_text: str | None = None
        self._last_summary_perm_mic_ok: bool | None = None
        self._last_summary_test_text: str | None = None
        self._last_summary_test_passed: bool | None = None
        self._test_outcome = "pending"
        self._env_settings_cache: dict[str, str] = read_env_file()

        # Permissions UI (shared component)
        self._permissions_card = None

        # Test dictation widgets/state
        self._test_status_label = None
        self._test_notice_label = None
        self._test_hotkey_label = None
        self._test_text_view = None
        self._test_start_btn = None
        self._test_stop_btn = None
        self._test_successful = False
        self._test_state = "idle"  # idle|starting|recording|stopping

        # Hotkey card (shared component)
        self._hotkey_card: HotkeyCard | None = None
        self._hotkey_recorder = HotkeyRecorder()

        # Summary step (dynamic labels)
        self._summary_provider_label = None
        self._summary_hotkey_label = None
        self._summary_perm_label = None
        self._summary_test_label = None

        # Language selector
        self._lang_popup = None

        # API key input (for Fast mode without existing key)
        self._api_key_container = None
        self._api_key_field = None
        self._api_key_status = None

        # Strong refs for ObjC handlers
        self._handler_refs: list[object] = []

        self._build_window()

    def _refresh_env_settings_cache(self) -> dict[str, str]:
        cache = read_env_file()
        self._env_settings_cache = cache
        return cache

    def _get_cached_env_setting(self, key_name: str) -> str | None:
        cache = getattr(self, "_env_settings_cache", None)
        if cache is None:
            return get_env_setting(key_name)
        return cache.get(key_name)

    def _get_cached_api_key(self, key_name: str) -> str | None:
        return self._get_cached_env_setting(key_name)

    def _apply_env_updates(self, updates: dict[str, str | None]) -> bool:
        normalized_updates = {
            key: (None if value is None else str(value))
            for key, value in updates.items()
        }
        cache = getattr(self, "_env_settings_cache", None)
        if cache is not None:
            changed = False
            for key, value in normalized_updates.items():
                current = cache.get(key)
                if value is None:
                    if current is not None:
                        changed = True
                        break
                elif current != value:
                    changed = True
                    break
            if not changed:
                return False

        update_env_settings(normalized_updates)

        if cache is not None:
            for key, value in normalized_updates.items():
                if value is None:
                    cache.pop(key, None)
                else:
                    cache[key] = value
        return True

    def _get_cached_hotkeys(self) -> tuple[str, str]:
        return (
            (self._get_cached_env_setting("PULSESCRIBE_TOGGLE_HOTKEY") or "").strip(),
            (self._get_cached_env_setting("PULSESCRIBE_HOLD_HOTKEY") or "").strip(),
        )

    def _read_permission_signature(self) -> tuple[str, bool, bool]:
        """Reuse the permissions card snapshot before re-querying OS helpers."""
        card = getattr(self, "_permissions_card", None)
        if card is not None:
            try:
                signature = card.get_cached_permission_signature()
            except Exception:
                signature = None
            if signature is not None:
                return signature

        from utils.permissions import get_permission_signature

        return get_permission_signature()

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
        try:
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
        except Exception:
            # Keep controller logic importable/testable on systems without PyObjC.
            return

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
        self._window.setTitle_("PulseScribe Setup Wizard")
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
        self._render()
        if self._step == OnboardingStep.PERMISSIONS:
            self._refresh_permissions()

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
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        content_top = WIZARD_HEIGHT - 90
        content_bottom = FOOTER_HEIGHT + 10
        content_h = max(200, content_top - content_bottom)
        self._step_content_height = content_h
        self._step_frame = NSMakeRect(0, content_bottom, WIZARD_WIDTH, content_h)
        self._step_builders = {
            OnboardingStep.CHOOSE_GOAL: self._build_step_choose_goal,
            OnboardingStep.PERMISSIONS: self._build_step_permissions,
            OnboardingStep.HOTKEY: self._build_step_hotkey,
            OnboardingStep.TEST_DICTATION: self._build_step_test_dictation,
            OnboardingStep.CHEAT_SHEET: self._build_step_cheat_sheet,
        }
        self._ensure_step_built(self._step)

    def _create_step_container(self):
        from AppKit import NSView  # type: ignore[import-not-found]

        frame = self._step_frame
        if frame is None:
            raise RuntimeError("step frame is not initialized")
        view = NSView.alloc().initWithFrame_(frame)
        view.setHidden_(True)
        return view

    def _is_step_built(self, step: OnboardingStep | None) -> bool:
        if step == OnboardingStep.DONE:
            step = OnboardingStep.CHEAT_SHEET
        return step in self._step_views

    def _ensure_step_built(self, step: OnboardingStep | None) -> bool:
        if step is None:
            return False
        if step == OnboardingStep.DONE:
            step = OnboardingStep.CHEAT_SHEET
        if step in self._step_views:
            return False

        builder = self._step_builders.get(step)
        if builder is None or self._content_view is None:
            return False

        view = self._create_step_container()
        self._content_view.addSubview_(view)
        self._step_views[step] = view
        builder(view, self._step_content_height)
        return True

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
            NSMakeRect(PADDING, y, 122, btn_h)
        )
        skip_btn.setTitle_("Open Settings…")
        skip_btn.setBezelStyle_(NSBezelStyleRounded)
        skip_btn.setFont_(NSFont.systemFontOfSize_(12))
        h_skip = _WizardActionHandler.alloc().initWithController_action_(self, "cancel")
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
        title.setStringValue_("Choose your setup")
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
            "Start with a recommended default. You can adjust providers, prompts and hotkeys later in Settings."
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
            "Best latency. Uses Deepgram streaming when available.",
            "choose_fast",
            start_y,
        )
        add_choice(
            "Private",
            "Runs locally on your Mac. Best for privacy.",
            "choose_private",
            start_y - 70,
        )
        add_choice(
            "Advanced",
            "Pick providers, models and refinement yourself.",
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
            NSMakeRect(base_x + 74, lang_y, 148, 22)
        )
        lang_popup.setFont_(NSFont.systemFontOfSize_(11))
        for lang in LANGUAGE_OPTIONS:
            lang_popup.addItemWithTitle_(_format_language_label(lang))
        current_lang = self._get_cached_env_setting("PULSESCRIBE_LANGUAGE") or "auto"
        if current_lang in LANGUAGE_OPTIONS:
            lang_popup.selectItemWithTitle_(_format_language_label(current_lang))
        self._lang_popup = lang_popup
        parent_view.addSubview_(lang_popup)

        lang_hint = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x + 228, lang_y + 3, btn_w - 228, 16)
        )
        lang_hint.setStringValue_("Automatic = detect from speech")
        lang_hint.setBezeled_(False)
        lang_hint.setDrawsBackground_(False)
        lang_hint.setEditable_(False)
        lang_hint.setSelectable_(False)
        lang_hint.setFont_(NSFont.systemFontOfSize_(10))
        lang_hint.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.5))
        parent_view.addSubview_(lang_hint)

        # API Key input (shown when Fast is selected without existing key)
        from AppKit import NSSecureTextField  # type: ignore[import-not-found]

        api_y = card_y - 96
        api_container = _create_card(PADDING, api_y, card_w, 86)
        api_container.setHidden_(True)
        parent_view.addSubview_(api_container)
        self._api_key_container = api_container

        api_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(CARD_PADDING, 58, card_w - 2 * CARD_PADDING, 16)
        )
        api_label.setStringValue_("Deepgram API key for Fast mode")
        api_label.setBezeled_(False)
        api_label.setDrawsBackground_(False)
        api_label.setEditable_(False)
        api_label.setSelectable_(False)
        api_label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        api_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        api_container.addSubview_(api_label)

        api_field = NSSecureTextField.alloc().initWithFrame_(
            NSMakeRect(CARD_PADDING, 28, card_w - 2 * CARD_PADDING, 22)
        )
        api_field.setFont_(NSFont.systemFontOfSize_(12))
        api_field.setPlaceholderString_("Paste your Deepgram key (dg-...)")
        enter_handler = _WizardActionHandler.alloc().initWithController_action_(
            self, "next"
        )
        api_field.setTarget_(enter_handler)
        api_field.setAction_(objc.selector(enter_handler.performAction_, signature=b"v@:@"))
        self._handler_refs.append(enter_handler)
        api_container.addSubview_(api_field)
        self._api_key_field = api_field

        api_status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(CARD_PADDING, 8, card_w - 2 * CARD_PADDING, 16)
        )
        api_status.setStringValue_("")
        api_status.setBezeled_(False)
        api_status.setDrawsBackground_(False)
        api_status.setEditable_(False)
        api_status.setSelectable_(False)
        api_status.setFont_(NSFont.systemFontOfSize_(10))
        api_status.setTextColor_(_get_color(255, 200, 90))
        api_container.addSubview_(api_status)
        self._api_key_status = api_status

    def _build_step_permissions(self, parent_view, content_h: int) -> None:
        import objc  # type: ignore[import-not-found]
        from ui.permissions_card import PERMISSIONS_DESCRIPTION, PermissionsCard

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
            description=PERMISSIONS_DESCRIPTION,
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
                "Choose how you want to start dictation.\n"
                "Toggle: press once to start and once to stop. Hold: hold while speaking."
            ),
            bind_action=bind_action,
            hotkey_recorder=self._hotkey_recorder,
            on_hotkey_change=self._apply_hotkey_change,
            on_after_change=self._render,
            get_current_hotkeys=self._get_cached_hotkeys,
            show_presets=True,
            show_hint=True,
        )

    def _build_step_test_dictation(self, parent_view, content_h: int) -> None:
        import objc  # type: ignore[import-not-found]
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
        title.setStringValue_("Practice dictation (safe)")
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
            "Nothing is pasted during this step.\n"
            "Use your hotkey or the buttons below; the transcript only appears here."
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

        status_h = 32
        status_y = hotkeys_y - 8 - status_h
        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, status_y, content_w, status_h)
        )
        status.setStringValue_("")
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_(11))
        status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        try:
            status.setUsesSingleLineMode_(False)
        except Exception:
            pass
        parent_view.addSubview_(status)
        self._test_status_label = status

        buttons_y = status_y - 8 - 24
        start_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x, buttons_y, 122, 24)
        )
        start_btn.setTitle_("Start practice")
        start_btn.setBezelStyle_(NSBezelStyleRounded)
        start_btn.setFont_(NSFont.systemFontOfSize_(11))
        h_start = _WizardActionHandler.alloc().initWithController_action_(
            self, "start_test"
        )
        start_btn.setTarget_(h_start)
        start_btn.setAction_(objc.selector(h_start.performAction_, signature=b"v@:@"))
        self._handler_refs.append(h_start)
        parent_view.addSubview_(start_btn)
        self._test_start_btn = start_btn

        stop_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x + 130, buttons_y, 96, 24)
        )
        stop_btn.setTitle_("Stop")
        stop_btn.setBezelStyle_(NSBezelStyleRounded)
        stop_btn.setFont_(NSFont.systemFontOfSize_(11))
        stop_btn.setHidden_(True)
        h_stop = _WizardActionHandler.alloc().initWithController_action_(
            self, "stop_test"
        )
        stop_btn.setTarget_(h_stop)
        stop_btn.setAction_(objc.selector(h_stop.performAction_, signature=b"v@:@"))
        self._handler_refs.append(h_stop)
        parent_view.addSubview_(stop_btn)
        self._test_stop_btn = stop_btn

        notice_h = 30
        notice_y = buttons_y - 8 - notice_h
        notice = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, notice_y, content_w, notice_h)
        )
        notice.setStringValue_("")
        notice.setBezeled_(False)
        notice.setDrawsBackground_(False)
        notice.setEditable_(False)
        notice.setSelectable_(False)
        notice.setFont_(NSFont.systemFontOfSize_(10))
        notice.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55))
        try:
            notice.setUsesSingleLineMode_(False)
        except Exception:
            pass
        parent_view.addSubview_(notice)
        self._test_notice_label = notice

        scroll_y = card_y + 18
        scroll_top = notice_y - 10
        scroll_h = max(120, int(scroll_top - scroll_y))
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
        text_view.setString_(TEST_DICTATION_EMPTY_TEXT)
        tc = text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)

        scroll.setDocumentView_(text_view)
        parent_view.addSubview_(scroll)
        self._test_text_view = text_view
        self._set_test_status("pending", _build_test_status_text("pending"))
        notice_text, notice_level = _build_test_notice_feedback("pending")
        self._set_test_notice(notice_level, notice_text)
        self._set_test_preview_text(TEST_DICTATION_EMPTY_TEXT)
        self._refresh_test_action_buttons()

        # Skip test link (top right of card, next to title)
        skip_btn_w = 92
        skip_test_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                base_x + content_w - skip_btn_w,
                card_y + card_h - 28,
                skip_btn_w,
                18,
            )
        )
        skip_test_btn.setTitle_("Skip for now")
        skip_test_btn.setBezelStyle_(0)
        skip_test_btn.setBordered_(False)
        skip_test_btn.setFont_(NSFont.systemFontOfSize_(11))
        try:
            skip_test_btn.setContentTintColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.5)
            )
        except Exception:
            pass
        h_skip = _WizardActionHandler.alloc().initWithController_action_(
            self, "skip_test"
        )
        skip_test_btn.setTarget_(h_skip)
        skip_test_btn.setAction_(
            objc.selector(h_skip.performAction_, signature=b"v@:@")
        )
        self._handler_refs.append(h_skip)
        parent_view.addSubview_(skip_test_btn)

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
        card_h = 312
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
        row_y -= row_h

        # Test row
        add_label(row_y, "Practice test:")
        self._summary_test_label = add_value(row_y)

        # Abschluss-Text
        ready_y = card_y + 58
        ready = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, ready_y, content_w, 58)
        )
        ready.setStringValue_(
            "You're ready. Press your hotkey anywhere to start dictating. "
            "PulseScribe will transcribe your speech and paste automatically when the needed permissions are enabled.\n\n"
            "You can change these defaults anytime from the menu bar icon."
        )
        ready.setBezeled_(False)
        ready.setDrawsBackground_(False)
        ready.setEditable_(False)
        ready.setSelectable_(False)
        ready.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        ready.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        parent_view.addSubview_(ready)

        more_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 22, 112, 16)
        )
        more_label.setStringValue_("Need to change something?")
        more_label.setBezeled_(False)
        more_label.setDrawsBackground_(False)
        more_label.setEditable_(False)
        more_label.setSelectable_(False)
        more_label.setFont_(NSFont.systemFontOfSize_(11))
        more_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(more_label)

        open_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x + 114, card_y + 18, 150, 24)
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
        ok_color = _get_color(120, 255, 150)
        warn_color = _get_color(255, 200, 90)

        # Provider
        mode = (self._get_cached_env_setting("PULSESCRIBE_MODE") or "deepgram").strip()
        mode_display = MODE_LABELS.get(mode, mode.title())

        if self._summary_provider_label:
            try:
                if self._last_summary_provider_text != mode_display:
                    self._summary_provider_label.setStringValue_(mode_display)
                    self._last_summary_provider_text = mode_display
            except Exception:
                pass

        # Hotkeys
        toggle_hk, hold_hk = self._get_cached_hotkeys()
        hotkey_display, has_hotkeys = _build_hotkey_summary_text(toggle_hk, hold_hk)

        if self._summary_hotkey_label:
            try:
                if self._last_summary_hotkey_text != hotkey_display:
                    self._summary_hotkey_label.setStringValue_(hotkey_display)
                    self._last_summary_hotkey_text = hotkey_display
                if self._last_summary_hotkey_has_value != has_hotkeys:
                    self._summary_hotkey_label.setTextColor_(
                        ok_color if has_hotkeys else warn_color
                    )
                    self._last_summary_hotkey_has_value = has_hotkeys
            except Exception:
                pass

        # Permissions
        mic_state, access_ok, input_ok = self._read_permission_signature()
        perm_display, mic_ok = _build_permission_summary_text(
            mic_state,
            access_ok,
            input_ok,
        )

        if self._summary_perm_label:
            try:
                if self._last_summary_perm_text != perm_display:
                    self._summary_perm_label.setStringValue_(perm_display)
                    self._last_summary_perm_text = perm_display
                if self._last_summary_perm_mic_ok != mic_ok:
                    self._summary_perm_label.setTextColor_(
                        ok_color if mic_ok else warn_color
                    )
                    self._last_summary_perm_mic_ok = mic_ok
            except Exception:
                pass

        # Practice test
        test_display, test_tone = _build_test_summary_text(
            getattr(self, "_test_outcome", "pending")
        )
        tone_color = {
            "ok": ok_color,
            "warn": warn_color,
            "neutral": _get_color(255, 255, 255, 0.65),
        }.get(test_tone, warn_color)
        summary_test_label = getattr(self, "_summary_test_label", None)
        if summary_test_label:
            try:
                if getattr(self, "_last_summary_test_text", None) != test_display:
                    summary_test_label.setStringValue_(test_display)
                    self._last_summary_test_text = test_display
                if getattr(self, "_last_summary_test_passed", None) != test_tone:
                    summary_test_label.setTextColor_(tone_color)
                    self._last_summary_test_passed = test_tone
            except Exception:
                pass

    # ---------------------------------------------------------------------
    # Actions + State
    # ---------------------------------------------------------------------

    def _wizard_title(self, step: OnboardingStep) -> str:
        titles = {
            OnboardingStep.CHOOSE_GOAL: "Welcome to PulseScribe",
            OnboardingStep.PERMISSIONS: "Permissions",
            OnboardingStep.HOTKEY: "Hotkey",
            OnboardingStep.TEST_DICTATION: "Practice dictation",
            OnboardingStep.CHEAT_SHEET: "Review your setup",
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

    def _focus_api_key_field(self) -> None:
        """Move keyboard focus into the Fast-mode API key field."""
        field = self._api_key_field
        if field is None:
            return
        try:
            if self._window is not None:
                self._window.makeFirstResponder_(field)
                return
        except Exception:
            pass
        try:
            field.becomeFirstResponder()
        except Exception:
            pass

    def _show_fast_api_key_prompt(self, message: str | None = None) -> None:
        """Reveal the Fast-mode API key prompt and focus the input."""
        last_visible = getattr(self, "_last_api_key_prompt_visible", None)
        if self._api_key_container is not None:
            try:
                if last_visible is not True:
                    self._api_key_container.setHidden_(False)
                    self._last_api_key_prompt_visible = True
            except Exception:
                pass
        if self._api_key_status is not None:
            prompt_message = (
                message
                or "Fast mode needs a Deepgram API key. Paste one to continue."
            )
            try:
                if getattr(self, "_last_api_key_prompt_message", None) != prompt_message:
                    self._api_key_status.setStringValue_(prompt_message)
                    self._last_api_key_prompt_message = prompt_message
            except Exception:
                pass
        self._focus_api_key_field()
        self._render()

    def _apply_selected_goal_choice(self) -> bool:
        """Persist the selected goal choice and advance when valid."""
        choice = self._choice
        if choice is None:
            return False

        if choice == OnboardingChoice.FAST:
            entered_key = ""
            if self._api_key_field:
                entered_key = self._api_key_field.stringValue().strip()

            resolution = resolve_fast_choice_updates(
                entered_deepgram_key=entered_key,
                cached_deepgram_key=self._get_cached_api_key("DEEPGRAM_API_KEY"),
                env_deepgram_key=os.getenv("DEEPGRAM_API_KEY"),
                cached_groq_key=self._get_cached_api_key("GROQ_API_KEY"),
                env_groq_key=os.getenv("GROQ_API_KEY"),
                current_mode=self._get_cached_env_setting("PULSESCRIBE_MODE"),
            )
            if resolution.should_prompt_for_api_key:
                self._show_fast_api_key_prompt()
                return False

            self._apply_env_updates(resolution.pending_updates)
            if self._api_key_container is not None:
                try:
                    if getattr(self, "_last_api_key_prompt_visible", None) is not False:
                        self._api_key_container.setHidden_(True)
                        self._last_api_key_prompt_visible = False
                except Exception:
                    pass
        elif choice == OnboardingChoice.PRIVATE:
            apply_local_preset_to_env(default_local_preset_private())

        set_onboarding_choice(choice)

        # Save selected language
        if self._lang_popup:
            selected_title = self._lang_popup.titleOfSelectedItem()
            lang = _language_code_from_title(selected_title)
            self._apply_env_updates(
                {
                    "PULSESCRIBE_LANGUAGE": (
                        lang if lang and lang != "auto" else None
                    )
                }
            )

        if self._on_settings_changed:
            try:
                self._on_settings_changed()
            except Exception:
                pass

        self._set_step(OnboardingStep.PERMISSIONS)
        return True

    def _can_advance(self) -> bool:
        if self._step == OnboardingStep.CHOOSE_GOAL:
            return self._choice is not None
        if self._step == OnboardingStep.PERMISSIONS:
            mic_state, _, _ = self._read_permission_signature()
            return mic_state not in ("denied", "restricted")
        if self._step == OnboardingStep.HOTKEY:
            toggle, hold = self._get_cached_hotkeys()
            return bool(toggle or hold)
        if self._step == OnboardingStep.TEST_DICTATION:
            return bool(self._test_successful)
        return True

    def _render(self) -> None:
        step = self._step
        if step == OnboardingStep.DONE:
            step = OnboardingStep.CHEAT_SHEET
        self._ensure_step_built(step)

        previous_visible_step = getattr(self, "_visible_step", None)
        if previous_visible_step is None:
            for s, view in self._step_views.items():
                try:
                    view.setHidden_(s != step)
                except Exception:
                    pass
        elif previous_visible_step != step:
            previous_view = self._step_views.get(previous_visible_step)
            if previous_view is not None:
                try:
                    previous_view.setHidden_(True)
                except Exception:
                    pass
            current_view = self._step_views.get(step)
            if current_view is not None:
                try:
                    current_view.setHidden_(False)
                except Exception:
                    pass
        self._visible_step = step

        if self._step_label is not None:
            title_text = self._wizard_title(step)
            try:
                if getattr(self, "_last_title_text", None) != title_text:
                    self._step_label.setStringValue_(title_text)
                    self._last_title_text = title_text
            except Exception:
                pass
        if self._progress_label is not None:
            idx = step_index(step)
            progress_text = f"Step {idx}/{total_steps()}"
            try:
                if getattr(self, "_last_progress_text", None) != progress_text:
                    self._progress_label.setStringValue_(progress_text)
                    self._last_progress_text = progress_text
            except Exception:
                pass

        if self._back_btn is not None:
            back_hidden = step == OnboardingStep.CHOOSE_GOAL
            try:
                if getattr(self, "_last_back_hidden", None) != back_hidden:
                    self._back_btn.setHidden_(back_hidden)
                    self._last_back_hidden = back_hidden
            except Exception:
                pass

        if self._next_btn is not None:
            title = "Finish" if step == OnboardingStep.CHEAT_SHEET else "Next"
            can_advance = bool(self._can_advance())
            try:
                if getattr(self, "_last_next_title", None) != title:
                    self._next_btn.setTitle_(title)
                    self._last_next_title = title
                if getattr(self, "_last_next_enabled", None) != can_advance:
                    self._next_btn.setEnabled_(can_advance)
                    self._last_next_enabled = can_advance
            except Exception:
                pass

        if step == OnboardingStep.HOTKEY:
            self._sync_hotkey_fields_from_env()
        if step == OnboardingStep.TEST_DICTATION:
            self._update_test_dictation_hotkeys()
            self._refresh_test_action_buttons()

    def _update_test_dictation_hotkeys(self) -> None:
        label = self._test_hotkey_label
        if label is None:
            return

        toggle, hold = self._get_cached_hotkeys()

        lines: list[str] = []
        if toggle:
            lines.append(
                "Toggle: "
                f"{_format_hotkey_for_display(toggle)} — press once to start and again to stop."
            )
        if hold:
            lines.append(
                f"Hold: {_format_hotkey_for_display(hold)} — hold while speaking, then release."
            )
        if not lines:
            lines.append("No hotkeys configured. Go back to choose one first.")

        label_text = "\n".join(lines)
        try:
            if getattr(self, "_last_test_hotkey_text", None) != label_text:
                label.setStringValue_(label_text)
                self._last_test_hotkey_text = label_text
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
            if self._step == OnboardingStep.CHOOSE_GOAL:
                self._apply_selected_goal_choice()
                return
            self._set_step(next_step(self._step))
            return

        if action == "cancel":
            self._complete(open_settings=True)
            return

        if action == "start_test":
            self._start_test_dictation()
            return

        if action == "stop_test":
            self._stop_test_dictation()
            return

        if action == "skip_test":
            # Skip the test dictation and go to next step
            if getattr(self, "_test_outcome", "pending") != "passed":
                self._test_outcome = "skipped"
            self._set_step(next_step(self._step))
            return

        if action == "open_settings":
            self._complete(open_settings=True)
            return

        # Choose goal
        if action in ("choose_fast", "choose_private", "choose_advanced"):
            if action == "choose_fast":
                self._choice = OnboardingChoice.FAST
            elif action == "choose_private":
                self._choice = OnboardingChoice.PRIVATE
            else:
                self._choice = OnboardingChoice.ADVANCED
            self._apply_selected_goal_choice()
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
            self._ensure_step_built(OnboardingStep.HOTKEY)
            preset = action.replace("hotkey_", "")  # f19_toggle, fn_hold, opt_space
            self._apply_hotkey_preset(preset)
            return

        if action.startswith("record_hotkey:"):
            self._ensure_step_built(OnboardingStep.HOTKEY)
            kind = action.split(":", 1)[1].strip().lower()
            if kind in ("toggle", "hold"):
                self._toggle_hotkey_recording(kind)
            return

        if action == "goto_hotkey":
            self._set_step(OnboardingStep.HOTKEY)
            return

    def _open_privacy_settings(self, anchor: str) -> None:
        from utils.permissions import open_privacy_settings

        open_privacy_settings(anchor, window=self._window)
        if self._window is None:
            return
        try:
            from AppKit import NSFloatingWindowLevel  # type: ignore[import-not-found]

            self._window.setLevel_(NSFloatingWindowLevel)
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

    def _apply_hotkey_preset(self, preset: str) -> bool:
        """Apply one preset even when the AppKit hotkey card is not available."""
        if self._hotkey_card:
            self._hotkey_card.apply_preset(preset)
            return True

        preset_map = {
            "f19_toggle": ("toggle", "f19"),
            "fn_hold": ("hold", "fn"),
            "opt_space": ("toggle", "option+space"),
        }
        target = preset_map.get(preset)
        if target is None:
            return False

        kind, hotkey = target
        toggle, hold = self._get_cached_hotkeys()
        if kind == "hold":
            hold = hotkey
        else:
            toggle = hotkey

        self._apply_env_updates(
            {
                "PULSESCRIBE_TOGGLE_HOTKEY": toggle or None,
                "PULSESCRIBE_HOLD_HOTKEY": hold or None,
                "PULSESCRIBE_HOTKEY": None,
                "PULSESCRIBE_HOTKEY_MODE": None,
            }
        )

        if self._on_settings_changed:
            try:
                self._on_settings_changed()
            except Exception:
                pass

        self._set_hotkey_status("ok", "✓ Saved")
        return True

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

        toggle, hold = self._get_cached_hotkeys()
        if kind == "hold":
            hold = normalized
        else:
            toggle = normalized

        self._apply_env_updates(
            {
                "PULSESCRIBE_TOGGLE_HOTKEY": toggle or None,
                "PULSESCRIBE_HOLD_HOTKEY": hold or None,
                "PULSESCRIBE_HOTKEY": None,
                "PULSESCRIBE_HOTKEY_MODE": None,
            }
        )

        if self._on_settings_changed:
            try:
                self._on_settings_changed()
            except Exception:
                pass

        if level == "warning":
            self._set_hotkey_status("warning", message)
        else:
            self._set_hotkey_status("ok", "✓ Saved")
        return True

    # ---------------------------------------------------------------------
    # Test dictation
    # ---------------------------------------------------------------------

    def _set_test_status(self, level: str, message: str) -> None:
        label = getattr(self, "_test_status_label", None)
        if label is None:
            return

        try:
            if getattr(self, "_last_test_status_text", None) != message:
                label.setStringValue_(message)
                self._last_test_status_text = message
        except Exception:
            pass

        try:
            colors = {
                "pending": _get_color(255, 255, 255, 0.6),
                "starting": _get_color(140, 220, 255),
                "recording": _get_color(140, 220, 255),
                "processing": _get_color(140, 220, 255),
                "passed": _get_color(120, 255, 150),
                "empty": _get_color(255, 200, 90),
                "cancelled": _get_color(255, 255, 255, 0.65),
                "skipped": _get_color(255, 255, 255, 0.65),
                "error": _get_color(255, 120, 120),
            }
            color = colors.get(level, colors["pending"])
        except Exception:
            color = None
        if color is None:
            return
        try:
            if getattr(self, "_last_test_status_level", None) != level:
                label.setTextColor_(color)
                self._last_test_status_level = level
        except Exception:
            pass

    def _set_test_notice(self, level: str, message: str) -> None:
        label = getattr(self, "_test_notice_label", None)
        if label is None:
            return

        try:
            if getattr(self, "_last_test_notice_text", None) != message:
                label.setStringValue_(message)
                self._last_test_notice_text = message
        except Exception:
            pass

        try:
            colors = {
                "neutral": _get_color(255, 255, 255, 0.6),
                "accent": _get_color(140, 220, 255),
                "success": _get_color(120, 255, 150),
                "warn": _get_color(255, 200, 90),
            }
            color = colors.get(level, colors["neutral"])
        except Exception:
            color = None
        if color is None:
            return
        try:
            if getattr(self, "_last_test_notice_level", None) != level:
                label.setTextColor_(color)
                self._last_test_notice_level = level
        except Exception:
            pass

    def _set_test_preview_text(self, text: str) -> None:
        view = getattr(self, "_test_text_view", None)
        if view is None:
            return
        try:
            if getattr(self, "_last_test_preview_text", None) != text:
                view.setString_(text)
                self._last_test_preview_text = text
        except Exception:
            pass

    def _refresh_test_action_buttons(self) -> None:
        start_btn = getattr(self, "_test_start_btn", None)
        stop_btn = getattr(self, "_test_stop_btn", None)
        if start_btn is None or stop_btn is None:
            return

        state = getattr(self, "_test_state", "idle")
        outcome = getattr(self, "_test_outcome", "pending")
        start_title = _build_test_primary_action_text(outcome)
        start_hidden = state in {"starting", "recording"}
        start_enabled = state != "stopping" and callable(
            getattr(self, "_on_test_dictation_start", None)
        )
        stop_hidden = state not in {"starting", "recording"}
        stop_title = "Cancel" if state == "starting" else "Stop"
        stop_enabled = callable(getattr(self, "_on_test_dictation_stop", None)) or callable(
            getattr(self, "_on_test_dictation_cancel", None)
        )

        if state == "stopping":
            start_title = "Working…"
            start_hidden = False
            start_enabled = False

        try:
            start_btn.setTitle_(start_title)
            start_btn.setHidden_(start_hidden)
            start_btn.setEnabled_(start_enabled)
            stop_btn.setTitle_(stop_title)
            stop_btn.setHidden_(stop_hidden)
            stop_btn.setEnabled_(stop_enabled)
        except Exception:
            pass

    def _start_test_dictation(self) -> None:
        if self._step != OnboardingStep.TEST_DICTATION:
            return
        if self._test_state in {"starting", "recording", "stopping"}:
            return
        start_callback = getattr(self, "_on_test_dictation_start", None)
        if not callable(start_callback):
            return

        self._test_successful = False
        self._test_state = "starting"
        self._test_outcome = "starting"
        self._set_test_status("starting", _build_test_status_text("starting"))
        notice_text, notice_level = _build_test_notice_feedback("starting")
        self._set_test_notice(notice_level, notice_text)
        self._set_test_preview_text(_build_test_preview_text("starting", None))
        self._refresh_test_action_buttons()
        self._render()

        try:
            start_callback()
        except Exception as exc:
            self.on_test_dictation_result("", error=str(exc))

    def _stop_test_dictation(self) -> None:
        if self._step != OnboardingStep.TEST_DICTATION:
            return

        cancel_callback = getattr(self, "_on_test_dictation_cancel", None)
        stop_callback = getattr(self, "_on_test_dictation_stop", None)

        if self._test_state == "starting" and callable(cancel_callback):
            self._test_successful = False
            self._test_state = "idle"
            self._test_outcome = "cancelled"
            self._set_test_status("cancelled", _build_test_status_text("cancelled"))
            notice_text, notice_level = _build_test_notice_feedback("cancelled")
            self._set_test_notice(notice_level, notice_text)
            self._set_test_preview_text(_build_test_preview_text("cancelled", None))
            self._refresh_test_action_buttons()
            self._render()
            try:
                cancel_callback()
            except Exception:
                pass
            return

        if self._test_state != "recording" or not callable(stop_callback):
            return

        self._test_state = "stopping"
        self._test_outcome = "processing"
        self._set_test_status("processing", _build_test_status_text("processing"))
        notice_text, notice_level = _build_test_notice_feedback("processing")
        self._set_test_notice(notice_level, notice_text)
        self._set_test_preview_text(_build_test_preview_text("processing", None))
        self._refresh_test_action_buttons()
        self._render()

        try:
            stop_callback()
        except Exception as exc:
            self.on_test_dictation_result("", error=str(exc))

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
            self._test_outcome = "recording"
            self._set_test_preview_text(_build_test_preview_text("recording", None))
            self._set_test_status("recording", _build_test_status_text("recording"))
            notice_text, notice_level = _build_test_notice_feedback("recording")
            self._set_test_notice(notice_level, notice_text)
            self._refresh_test_action_buttons()
            self._render()
            return

        if normalized in ("stopping", "processing"):
            if self._test_state == "stopping":
                return
            self._test_state = "stopping"
            self._test_outcome = "processing"
            self._set_test_preview_text(_build_test_preview_text("processing", None))
            self._set_test_status("processing", _build_test_status_text("processing"))
            notice_text, notice_level = _build_test_notice_feedback("processing")
            self._set_test_notice(notice_level, notice_text)
            self._refresh_test_action_buttons()
            self._render()
            return

    def on_test_dictation_result(
        self, transcript: str, error: str | None = None
    ) -> None:
        self._test_state = "idle"

        if error:
            self._test_successful = False
            self._test_outcome = "error"
            self._set_test_status("error", _build_test_status_text("error", error=error))
            notice_text, notice_level = _build_test_notice_feedback(
                "error", error=error
            )
            self._set_test_notice(notice_level, notice_text)
        else:
            cleaned = (transcript or "").strip()
            self._test_successful = bool(cleaned)
            self._test_outcome = "passed" if cleaned else "empty"
            self._set_test_status(
                self._test_outcome,
                _build_test_status_text(self._test_outcome),
            )
            notice_text, notice_level = _build_test_notice_feedback(self._test_outcome)
            self._set_test_notice(notice_level, notice_text)

        self._set_test_preview_text(
            _build_test_preview_text(self._test_outcome, transcript)
        )
        self._refresh_test_action_buttons()
        self._render()

    # ---------------------------------------------------------------------
    # Completion
    # ---------------------------------------------------------------------

    def _complete(self, *, open_settings: bool = False) -> None:
        """Completes the wizard and optionally opens settings.

        Args:
            open_settings: If True, opens the Settings window after closing.
                          Used by the footer secondary action and the summary button.
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


try:
    _WizardActionHandler = _create_wizard_action_handler_class()
except Exception:
    _WizardActionHandler = None
