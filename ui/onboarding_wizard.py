"""Standalone first-run onboarding wizard for PulseScribe."""

from __future__ import annotations

import os
from typing import Callable

from ui import settings_theme as theme
from ui.hotkey_format import format_hotkey_for_display
from utils.onboarding import (
    ONBOARDING_ORDER,
    OnboardingChoice,
    OnboardingStep,
    coerce_onboarding_step,
    next_step,
    prev_step,
)
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

WIZARD_WIDTH = 640
WIZARD_HEIGHT = 520
SIDEBAR_WIDTH = 150
# Header, steps and footer are laid out in the area right of the sidebar.
CONTENT_WIDTH = WIZARD_WIDTH - SIDEBAR_WIDTH
PADDING = 24
HEADER_HEIGHT = 94  # page title + subtitle
FOOTER_HEIGHT = 56
CARD_PADDING = 0  # flat sections: content aligns with the page title
STEP_MARKER_SIZE = 18

# Sidebar entries, page titles and subtitles per step.
WIZARD_STEPS = ONBOARDING_ORDER[:-1]
STEP_NAV_LABELS = {
    OnboardingStep.CHOOSE_GOAL: "Setup",
    OnboardingStep.PERMISSIONS: "Permissions",
    OnboardingStep.HOTKEY: "Hotkey",
    OnboardingStep.TEST_DICTATION: "Practice",
    OnboardingStep.CHEAT_SHEET: "Review",
}
STEP_TITLES = {
    OnboardingStep.CHOOSE_GOAL: (
        "Welcome to PulseScribe",
        "Choose how speech is transcribed. You can change it in Settings.",
    ),
    OnboardingStep.PERMISSIONS: (
        "Permissions",
        "PulseScribe needs the microphone. The other permissions are optional.",
    ),
    OnboardingStep.HOTKEY: ("Hotkey", "Choose how you start and stop dictation."),
    OnboardingStep.TEST_DICTATION: (
        "Try it",
        "A safe test: nothing is pasted, the text only appears here.",
    ),
    OnboardingStep.CHEAT_SHEET: (
        "You're ready",
        "Your setup at a glance. You can change it anytime in Settings.",
    ),
}
# (choice, action, title, description, recommended)
GOAL_CHOICES = (
    (
        OnboardingChoice.FAST,
        "choose_fast",
        "Fast",
        "Lowest latency. Uses Deepgram streaming when available.",
        True,
    ),
    (
        OnboardingChoice.PRIVATE,
        "choose_private",
        "Private",
        "Transcribes locally on your Mac. Best for privacy.",
        False,
    ),
    (
        OnboardingChoice.ADVANCED,
        "choose_advanced",
        "Advanced",
        "Keeps your current providers. Fine-tune them in Settings.",
        False,
    ),
)
GOAL_ROW_HEIGHT = 62
# Hotkey rows: (key, action, slot, hotkey, description); "Custom" records a Toggle.
HOTKEY_CHOICES = (
    (
        "hold_fn",
        "hotkey_fn_hold",
        "hold",
        "fn",
        "Push to talk with the Fn (Globe) key. Needs Input Monitoring.",
    ),
    (
        "toggle_opt_space",
        "hotkey_opt_space",
        "toggle",
        "option+space",
        "Press once to start, again to stop.",
    ),
    (
        "toggle_f19",
        "hotkey_f19_toggle",
        "toggle",
        "f19",
        "Press once to start, again to stop.",
    ),
)
HOTKEY_PRESETS = {
    action.removeprefix("hotkey_"): (kind, hotkey)
    for _key, action, kind, hotkey, _description in HOTKEY_CHOICES
}
HOTKEY_CUSTOM_KEY = "custom"
HOTKEY_CUSTOM_DESCRIPTION = "Record your own shortcut to start and stop."
PERMISSIONS_HINT = "After you click Allow, the status refreshes on its own for about 20 seconds."
FAST_API_KEY_PROMPT = "Fast mode needs a Deepgram API key. Paste one to continue."
MODE_API_KEYS = {
    "deepgram": "DEEPGRAM_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
}

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
# Practice step: transcript box text and status badge per test outcome.
TEST_PREVIEW_TEXTS = {
    "pending": "Your text will appear here.",
    "starting": "Getting ready…",
    "recording": "Listening… say a short sentence.",
    "processing": "Transcribing…",
    "empty": "No speech detected. Try again with a short sentence.",
    "cancelled": "Cancelled. Start again whenever you're ready.",
    "skipped": "Cancelled. Start again whenever you're ready.",
}
TEST_BADGES = {
    "recording": ("Listening", "muted"),
    "processing": ("Processing", "muted"),
    "passed": ("Worked", "ok"),
    "empty": ("No speech", "warn"),
    "error": ("Error", "err"),
}



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
    return format_hotkey_for_display(
        hotkey,
        HOTKEY_TOKEN_LABELS,
        strip_parts=True,
        omit_empty_parts=True,
        title_unknown_parts=True,
    )



def _hotkey_choice_title(kind: str, hotkey: str) -> str:
    """Row and hint label such as "Hold Fn" or "Toggle Option+Space"."""
    return f"{'Hold' if kind == 'hold' else 'Toggle'} {_format_hotkey_for_display(hotkey)}"


def _selected_hotkey_choice(toggle: str, hold: str) -> str | None:
    """Hotkey row for a config; ``None`` when none or both hotkeys are set."""
    if bool(toggle) == bool(hold):
        return None
    kind, hotkey = ("toggle", toggle) if toggle else ("hold", hold)
    for key, _action, row_kind, row_hotkey, _description in HOTKEY_CHOICES:
        if (row_kind, row_hotkey) == (kind, hotkey):
            return key
    return HOTKEY_CUSTOM_KEY


def _build_provider_summary_text(mode: str, has_key: bool) -> tuple[str, str, str]:
    """Return ``(detail, badge, tone)`` for the Review step's Provider row."""
    detail = MODE_LABELS.get(mode, mode.title())
    if mode == "local" or has_key:
        return detail, "Ready", "ok"
    return detail, "No API key", "warn"


def _build_hotkey_summary_text(toggle: str, hold: str) -> tuple[str, str, str]:
    """Return ``(detail, badge, tone)`` for the Review step's Hotkey row."""
    parts: list[str] = []
    if toggle:
        parts.append(f"Toggle {_format_hotkey_for_display(toggle)}")
    if hold:
        parts.append(f"Hold {_format_hotkey_for_display(hold)}")
    if not parts:
        return "Choose one in the Hotkey step", "Not set", "warn"
    return " · ".join(parts), "Set", "ok"


def _build_permission_summary_text(
    mic_state: str,
    access_ok: bool,
    input_ok: bool,
) -> tuple[str, str, str]:
    """Return ``(detail, badge, tone)`` for the Review step's Permissions row."""
    if mic_state != "authorized":
        return "Microphone access is required", "Missing", "err"
    if access_ok and input_ok:
        return "Microphone, auto-paste and Hold hotkeys ready", "Granted", "ok"
    if access_ok:
        return "Microphone and auto-paste ready", "Granted", "ok"
    return "Microphone ready, auto-paste off", "Partial", "warn"


def _build_ready_text(toggle: str, hold: str) -> str:
    """Closing line of the Review step, naming the configured hotkey."""
    toggle_text = _format_hotkey_for_display(toggle)
    hold_text = _format_hotkey_for_display(hold)
    if hold_text and toggle_text:
        return f"Hold {hold_text} or press {toggle_text} in any app to dictate."
    if hold_text:
        return f"Hold {hold_text} in any app to dictate."
    if toggle_text:
        return f"Press {toggle_text} in any app to dictate, and again to stop."
    return "Set a hotkey in Settings to start dictating."



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



def _build_test_summary_text(outcome: str | None) -> tuple[str, str, str]:
    """Return ``(detail, badge, tone)`` for the Review step's Practice test row."""
    normalized = (outcome or "pending").strip().lower()
    if normalized == "passed":
        return "Your speech was transcribed", "Worked", "ok"
    if normalized == "skipped":
        return "Skipped for now", "Skipped", "muted"
    if normalized == "cancelled":
        return "The test was cancelled", "Cancelled", "muted"
    if normalized == "empty":
        return "No speech was detected", "No speech", "warn"
    if normalized == "error":
        return "The test could not finish", "Error", "err"
    if normalized in {"starting", "recording", "processing"}:
        return "The test is still running", "Running", "warn"
    return "Not run yet", "Not run", "muted"



def _build_test_primary_action_text(outcome: str | None) -> str:
    normalized = (outcome or "pending").strip().lower()
    if normalized in {"passed", "empty", "error", "cancelled", "skipped"}:
        return "Try again"
    return "Start practice"


def _build_test_preview_text(
    outcome: str | None, transcript: str | None, *, error: str | None = None
) -> str:
    """Transcript box text: the transcript, or a short line for the test state."""
    cleaned = (transcript or "").strip()
    if cleaned:
        return cleaned
    normalized = (outcome or "pending").strip().lower()
    if normalized == "error":
        return f"{_normalize_test_error_message(error)} Try again."
    return TEST_PREVIEW_TEXTS.get(normalized, TEST_PREVIEW_TEXTS["pending"])


def _build_test_prompt(toggle: str, hold: str) -> tuple[str, str, str]:
    """Practice prompt as ``(before, keycap, after)``; Hold wins when both exist."""
    if hold:
        return "Hold", _format_hotkey_for_display(hold), "and say a short sentence."
    if toggle:
        return (
            "Press",
            _format_hotkey_for_display(toggle),
            "and say a short sentence, then press it again.",
        )
    return "", "", "Click Start practice and say a short sentence."



def _get_color(r: int, g: int, b: int, a: float = 1.0):
    from AppKit import NSColor  # type: ignore[import-not-found]

    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


def _create_card(x: int, y: int, width: int, height: int):
    """Returns the 1 px rule that tops a flat section spanning this frame."""
    return theme.ns_rule(x, y + height - 1, width)


def _create_box(
    x: float,
    y: float,
    width: float,
    height: float,
    fill: str | None,
    *,
    radius: float = 0,
    border: str | None = None,
):
    """Returns an NSBox filled with the ``fill`` token, optionally with a 1 px border."""
    from AppKit import NSBox, NSColor  # type: ignore[import-not-found]
    from Foundation import NSMakeRect  # type: ignore[import-not-found]

    box = NSBox.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
    box.setBoxType_(4)  # Custom
    box.setBorderType_(1 if border else 0)  # Line / None
    box.setFillColor_(theme.ns_color(fill) if fill else NSColor.clearColor())
    if border:
        box.setBorderColor_(theme.ns_color(border))
        box.setBorderWidth_(1)
    box.setCornerRadius_(radius)
    box.setContentViewMargins_((0, 0))
    return box


def _create_label(
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    size: float = 13,
    weight: float | None = None,
    color: str = theme.TEXT,
):
    """Returns a static label in the system font, colored with a theme token."""
    from AppKit import NSFont, NSTextField  # type: ignore[import-not-found]
    from Foundation import NSMakeRect  # type: ignore[import-not-found]

    label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
    label.setStringValue_(text)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    label.setFont_(
        NSFont.systemFontOfSize_(size)
        if weight is None
        else NSFont.systemFontOfSize_weight_(size, weight)
    )
    label.setTextColor_(theme.ns_color(color))
    return label


def _set_button_text(
    button,
    title: str,
    color: str,
    *,
    size: float = 13,
    weight: float | None = None,
    indent: float = 0,
) -> None:
    """Gives a borderless button a colored title; ``indent`` left-aligns it."""
    from AppKit import (  # type: ignore[import-not-found]
        NSAttributedString,
        NSFont,
        NSFontAttributeName,
        NSFontWeightRegular,
        NSForegroundColorAttributeName,
        NSMutableParagraphStyle,
        NSParagraphStyleAttributeName,
    )

    attributes = {
        NSFontAttributeName: NSFont.systemFontOfSize_weight_(
            size, NSFontWeightRegular if weight is None else weight
        ),
        NSForegroundColorAttributeName: theme.ns_color(color),
    }
    if indent:
        style = NSMutableParagraphStyle.alloc().init()
        style.setFirstLineHeadIndent_(indent)
        style.setHeadIndent_(indent)
        attributes[NSParagraphStyleAttributeName] = style
    button.setAttributedTitle_(
        NSAttributedString.alloc().initWithString_attributes_(title, attributes)
    )


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
        self._subtitle_label = None
        self._back_btn = None
        self._next_btn = None
        self._next_fill = None
        self._skip_btn = None
        self._skip_test_btn = None
        # step -> (row background, marker box, marker label, name label)
        self._sidebar_entries: dict[OnboardingStep, tuple] = {}
        self._last_sidebar_step: OnboardingStep | None = None
        # choice -> (row background, left edge, radio ring, radio dot)
        self._goal_rows: dict[OnboardingChoice, tuple] = {}
        self._last_goal_choice: OnboardingChoice | None = None
        self._step_views: dict[OnboardingStep, object] = {}
        self._step_builders: dict[OnboardingStep, Callable[[object, int], None]] = {}
        self._step_content_height = 0
        self._step_frame = None
        self._visible_step: OnboardingStep | None = None
        self._last_title_text: str | None = None
        self._last_subtitle_text: str | None = None
        self._last_back_hidden: bool | None = None
        self._last_skip_test_hidden: bool | None = None
        self._last_next_title: str | None = None
        self._last_next_enabled: bool | None = None
        self._last_test_hotkey_text: tuple[str, str, str] | None = None
        self._last_test_status_level: tuple[str, str] | None = None
        self._last_test_preview_text: str | None = None
        self._last_api_key_prompt_visible: bool | None = None
        self._last_api_key_prompt_message: str | None = None
        self._last_summary_provider_text: str | None = None
        self._last_summary_hotkey_text: str | None = None
        self._last_summary_perm_text: str | None = None
        self._last_summary_test_text: str | None = None
        self._last_ready_text: str | None = None
        self._summary_badge_cache: dict[str, tuple[str, str]] = {}
        self._test_outcome = "pending"
        self._env_settings_cache: dict[str, str] = read_env_file()

        # Permissions UI (shared component)
        self._permissions_card = None

        # Test dictation widgets/state
        self._test_status_label = None  # transcript badge
        self._test_hotkey_label = None  # prompt views: before, keycap box, keycap, after
        self._test_text_view = None
        self._test_start_btn = None
        self._test_stop_btn = None
        self._test_successful = False
        self._test_state = "idle"  # idle|starting|recording|stopping

        # Hotkey rows (one choice) and custom recording
        self._hotkey_rows: dict = {}
        self._hotkey_record_btn = None
        self._hotkey_hint_label = None
        self._hotkey_status_label = None
        self._last_hotkey_choice_state: tuple[str, str] | None = None
        self._hotkey_recorder = HotkeyRecorder()

        # Summary step (dynamic labels: detail text + badge per row)
        self._summary_provider_label = None
        self._summary_hotkey_label = None
        self._summary_perm_label = None
        self._summary_test_label = None
        self._summary_badges: dict[str, object] = {}
        self._ready_label = None

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
                NSAppearance,
                NSAppearanceNameDarkAqua,
                NSBackingStoreBuffered,
                NSClosableWindowMask,
                NSFloatingWindowLevel,
                NSMakeRect,
                NSScreen,
                NSTitledWindowMask,
                NSView,
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
        self._window.setTitle_("PulseScribe Setup")
        self._window.setReleasedWhenClosed_(False)
        # Wizard always stays on top during setup
        self._window.setLevel_(NSFloatingWindowLevel)
        # Dark only, like Settings: solid background and dark native controls.
        self._window.setAppearance_(
            NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua)
        )

        content_view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, WIZARD_WIDTH, WIZARD_HEIGHT)
        )
        content_view.addSubview_(
            _create_box(0, 0, WIZARD_WIDTH, WIZARD_HEIGHT, theme.BG)
        )
        self._window.setContentView_(content_view)
        self._content_view = content_view

        self._build_sidebar()
        self._build_header()
        self._build_steps()
        self._build_footer()
        self._render()
        if self._step == OnboardingStep.PERMISSIONS:
            self._refresh_permissions()

    def _bind_action(self, control, action: str) -> None:
        """Routes the control's action to ``_handle_action(action)``."""
        import objc  # type: ignore[import-not-found]

        handler = _WizardActionHandler.alloc().initWithController_action_(self, action)
        control.setTarget_(handler)
        control.setAction_(objc.selector(handler.performAction_, signature=b"v@:@"))
        self._handler_refs.append(handler)

    def _build_sidebar(self) -> None:
        """Left column: app name and one entry per step with a progress marker."""
        from AppKit import (  # type: ignore[import-not-found]
            NSButton,
            NSFontWeightBold,
            NSFontWeightSemibold,
            NSTextAlignmentCenter,
        )
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        view = self._content_view
        view.addSubview_(
            _create_box(0, 0, SIDEBAR_WIDTH - 1, WIZARD_HEIGHT, theme.SIDEBAR_BG)
        )
        view.addSubview_(_create_box(SIDEBAR_WIDTH - 1, 0, 1, WIZARD_HEIGHT, theme.BORDER))

        inset = 10  # entry inset from the sidebar edge; text adds the same again
        text_x = 2 * inset
        text_w = SIDEBAR_WIDTH - 2 * text_x
        top = WIZARD_HEIGHT - 20
        view.addSubview_(
            _create_label(
                "PulseScribe", text_x, top - 20, text_w, 20, size=15, weight=NSFontWeightBold
            )
        )
        view.addSubview_(
            _create_label("Setup", text_x, top - 38, text_w, 17, size=12.5, color=theme.MUTED)
        )

        entry_h = 35
        entry_w = SIDEBAR_WIDTH - 2 * inset
        entry_y = top - 70
        marker_x = inset + 10
        for index, step in enumerate(WIZARD_STEPS, start=1):
            entry_y -= entry_h
            background = _create_box(inset, entry_y, entry_w, entry_h, None, radius=theme.RADIUS)
            view.addSubview_(background)
            marker_y = entry_y + (entry_h - STEP_MARKER_SIZE) / 2
            marker = _create_box(
                marker_x,
                marker_y,
                STEP_MARKER_SIZE,
                STEP_MARKER_SIZE,
                None,
                radius=theme.RADIUS,
                border=theme.SECONDARY_BORDER,
            )
            view.addSubview_(marker)
            marker_label = _create_label(
                str(index),
                marker_x,
                marker_y + 2,
                STEP_MARKER_SIZE,
                14,
                size=11,
                weight=NSFontWeightSemibold,
                color=theme.MUTED,
            )
            marker_label.setAlignment_(NSTextAlignmentCenter)
            view.addSubview_(marker_label)
            name_x = marker_x + STEP_MARKER_SIZE + 10
            name = _create_label(
                STEP_NAV_LABELS[step],
                name_x,
                entry_y + 9,
                inset + entry_w - name_x - 4,
                18,
                size=14,
                color=theme.NAV,
            )
            view.addSubview_(name)
            # Transparent click target: visited steps can be reopened.
            button = NSButton.alloc().initWithFrame_(
                NSMakeRect(inset, entry_y, entry_w, entry_h)
            )
            button.setBordered_(False)
            button.setTitle_("")
            button.setAccessibilityLabel_(STEP_NAV_LABELS[step])
            self._bind_action(button, f"goto:{step.value}")
            view.addSubview_(button)
            self._sidebar_entries[step] = (background, marker, marker_label, name)
            entry_y -= 2

    def _build_header(self) -> None:
        from AppKit import NSFontWeightBold  # type: ignore[import-not-found]

        x = SIDEBAR_WIDTH + PADDING
        width = CONTENT_WIDTH - 2 * PADDING
        title_y = WIZARD_HEIGHT - 20 - 30
        title = _create_label("", x, title_y, width, 30, size=24, weight=NSFontWeightBold)
        self._content_view.addSubview_(title)
        self._step_label = title

        subtitle = _create_label("", x, title_y - 22, width, 18, color=theme.MUTED)
        self._content_view.addSubview_(subtitle)
        self._subtitle_label = subtitle

    def _build_steps(self) -> None:
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        content_h = WIZARD_HEIGHT - HEADER_HEIGHT - FOOTER_HEIGHT
        self._step_content_height = content_h
        self._step_frame = NSMakeRect(SIDEBAR_WIDTH, FOOTER_HEIGHT, CONTENT_WIDTH, content_h)
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

    def _add_text_button(
        self, parent, x: float, y: float, width: float, height: float, title: str, action: str
    ):
        """Adds a borderless text button in the navigation color."""
        from AppKit import NSButton  # type: ignore[import-not-found]
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
        button.setBordered_(False)
        _set_button_text(button, title, theme.NAV)
        self._bind_action(button, action)
        parent.addSubview_(button)
        return button

    def _build_footer(self) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSButton,
            NSFontWeightMedium,
            NSView,
        )
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        footer = NSView.alloc().initWithFrame_(
            NSMakeRect(SIDEBAR_WIDTH, 0, CONTENT_WIDTH, FOOTER_HEIGHT)
        )
        footer.addSubview_(theme.ns_rule(0, FOOTER_HEIGHT - 1, CONTENT_WIDTH))
        self._content_view.addSubview_(footer)

        btn_h = 28
        y = (FOOTER_HEIGHT - btn_h) // 2
        next_w = 96
        next_x = CONTENT_WIDTH - PADDING - next_w

        # Light primary button: token fill box under a borderless button.
        next_fill = _create_box(
            next_x, y, next_w, btn_h, theme.PRIMARY_BG, radius=theme.RADIUS
        )
        footer.addSubview_(next_fill)
        next_btn = NSButton.alloc().initWithFrame_(NSMakeRect(next_x, y, next_w, btn_h))
        next_btn.setBordered_(False)
        _set_button_text(next_btn, "Next", theme.PRIMARY_TEXT, weight=NSFontWeightMedium)
        self._bind_action(next_btn, "next")
        footer.addSubview_(next_btn)
        self._next_fill = next_fill
        self._next_btn = next_btn

        back_w = 64
        back_x = next_x - 8 - back_w
        self._back_btn = self._add_text_button(footer, back_x, y, back_w, btn_h, "Back", "back")
        skip_w = 56
        self._skip_test_btn = self._add_text_button(
            footer, back_x - skip_w, y, skip_w, btn_h, "Skip", "skip_test"
        )
        self._skip_btn = self._add_text_button(
            footer, PADDING - 6, y, 116, btn_h, "Open Settings…", "cancel"
        )
        _set_button_text(self._skip_btn, "Open Settings…", theme.MUTED)
        self._skip_btn.setAlignment_(0)  # NSTextAlignmentLeft

    # ---------------------------------------------------------------------
    # Steps
    # ---------------------------------------------------------------------

    def _add_choice_rows(self, parent_view, top: float, rows) -> dict:
        """Adds selectable rows below ``top``; returns key -> row views.

        ``rows`` holds ``(key, action, title, description, tag)``; ``tag`` is an
        optional outlined label next to the title. Each row views tuple is
        ``(background, left edge, radio ring, radio dot, description label)``.
        """
        from AppKit import (  # type: ignore[import-not-found]
            NSButton,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextAlignmentCenter,
        )

        width = CONTENT_WIDTH - 2 * PADDING
        row_h = GOAL_ROW_HEIGHT
        views = {}
        parent_view.addSubview_(theme.ns_rule(PADDING, top - 1, width))
        for index, (key, action, title, description, tag) in enumerate(rows):
            y = top - (index + 1) * row_h
            background = _create_box(PADDING, y, width, row_h - 1, theme.SELECTED_ROW_BG)
            edge = _create_box(PADDING, y, 2, row_h - 1, theme.TEXT)
            ring_size = 14
            ring_x = PADDING + 16
            ring_y = y + (row_h - ring_size) / 2
            ring = _create_box(
                ring_x,
                ring_y,
                ring_size,
                ring_size,
                None,
                radius=ring_size / 2,
                border=theme.SECONDARY_BORDER,
            )
            dot = _create_box(
                ring_x + 4, ring_y + 4, ring_size - 8, ring_size - 8, theme.TEXT, radius=3
            )
            text_x = ring_x + ring_size + 14
            title_label = _create_label(
                title, text_x, y + 32, 240, 20, size=15, weight=NSFontWeightSemibold
            )
            title_label.sizeToFit()
            desc_label = _create_label(
                description, text_x, y + 12, width - (text_x - PADDING) - 8, 17,
                size=12.5, color=theme.MUTED,
            )
            for view in (background, edge, dot):
                view.setHidden_(True)  # shown by _sync_choice_rows when selected
            for view in (background, edge, ring, dot, title_label, desc_label):
                parent_view.addSubview_(view)
            if tag:
                tag_x = text_x + title_label.frame().size.width + 8
                parent_view.addSubview_(
                    _create_box(
                        tag_x, y + 34, 92, 18, None,
                        radius=theme.RADIUS, border=theme.SECONDARY_BORDER,
                    )
                )
                tag_label = _create_label(tag, tag_x, y + 36, 92, 14, size=11, color=theme.MUTED)
                tag_label.setAlignment_(NSTextAlignmentCenter)
                parent_view.addSubview_(tag_label)
            parent_view.addSubview_(theme.ns_rule(PADDING, y - 1, width))
            button = NSButton.alloc().initWithFrame_(NSMakeRect(PADDING, y, width, row_h))
            button.setBordered_(False)
            button.setTitle_("")
            button.setAccessibilityLabel_(f"{title}: {description}")
            self._bind_action(button, action)
            parent_view.addSubview_(button)
            views[key] = (background, edge, ring, dot, desc_label)
        return views

    @staticmethod
    def _sync_choice_rows(rows: dict, selected) -> None:
        """Marks the ``selected`` row (fill, light left edge, filled radio)."""
        for key, (background, edge, ring, dot, _desc) in rows.items():
            is_selected = key == selected
            try:
                background.setHidden_(not is_selected)
                edge.setHidden_(not is_selected)
                dot.setHidden_(not is_selected)
                ring.setBorderColor_(
                    theme.ns_color(theme.TEXT if is_selected else theme.SECONDARY_BORDER)
                )
            except Exception:
                pass

    def _build_step_choose_goal(self, parent_view, content_h: int) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSFont,
            NSMakeRect,
            NSPopUpButton,
            NSSecureTextField,
            NSView,
        )

        width = CONTENT_WIDTH - 2 * PADDING
        top = content_h
        self._goal_rows = self._add_choice_rows(
            parent_view,
            top,
            [
                (choice, action, title, description, "Recommended" if recommended else None)
                for choice, action, title, description, recommended in GOAL_CHOICES
            ],
        )
        self._last_goal_choice = None
        self._sync_goal_rows()

        # Language: label above the popup, hint to the right.
        lang_top = top - len(GOAL_CHOICES) * GOAL_ROW_HEIGHT - 20
        parent_view.addSubview_(
            _create_label("Language", PADDING, lang_top - 16, 200, 16, size=12, color=theme.NAV)
        )
        lang_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(PADDING - 3, lang_top - 46, 186, 26)
        )
        lang_popup.setFont_(NSFont.systemFontOfSize_(13))
        for lang in LANGUAGE_OPTIONS:
            lang_popup.addItemWithTitle_(_format_language_label(lang))
        current_lang = self._get_cached_env_setting("PULSESCRIBE_LANGUAGE") or "auto"
        if current_lang in LANGUAGE_OPTIONS:
            lang_popup.selectItemWithTitle_(_format_language_label(current_lang))
        self._lang_popup = lang_popup
        parent_view.addSubview_(lang_popup)
        parent_view.addSubview_(
            _create_label(
                "Automatic detects it from speech.",
                PADDING + 196, lang_top - 41, width - 196, 17,
                size=12, color=theme.MUTED,
            )
        )

        # Deepgram API key: shown while Fast is selected and no key is stored.
        api_h = 66
        api_container = NSView.alloc().initWithFrame_(
            NSMakeRect(PADDING, lang_top - 62 - api_h, width, api_h)
        )
        api_container.setHidden_(True)
        parent_view.addSubview_(api_container)
        self._api_key_container = api_container
        api_container.addSubview_(
            _create_label("Deepgram API key", 0, api_h - 16, width, 16, size=12, color=theme.NAV)
        )
        api_field = NSSecureTextField.alloc().initWithFrame_(
            NSMakeRect(0, api_h - 44, width, 24)
        )
        api_field.setFont_(NSFont.systemFontOfSize_(13))
        api_field.setPlaceholderString_("Paste your Deepgram key")
        self._bind_action(api_field, "next")
        api_container.addSubview_(api_field)
        self._api_key_field = api_field
        api_status = _create_label("", 0, 0, width, 16, size=12, color=theme.BADGE_TONES["warn"][1])
        api_container.addSubview_(api_status)
        self._api_key_status = api_status

    def _sync_goal_rows(self) -> None:
        """Marks the selected Setup row."""
        choice = getattr(self, "_choice", None)
        rows = getattr(self, "_goal_rows", None)
        if not rows or choice == getattr(self, "_last_goal_choice", None):
            return
        self._sync_choice_rows(rows, choice)
        self._last_goal_choice = choice

    def _build_step_permissions(self, parent_view, content_h: int) -> None:
        from ui.permissions_card import PERMISSION_ROWS, PermissionsCard

        width = CONTENT_WIDTH - 2 * PADDING
        row_h = 56
        self._permissions_card = PermissionsCard.build_rows(
            parent_view=parent_view,
            x=PADDING,
            top=content_h,
            width=width,
            bind_action=self._bind_action,
            after_refresh=self._render,
            row_height=row_h,
        )
        hint_y = content_h - len(PERMISSION_ROWS) * row_h - 32
        parent_view.addSubview_(
            _create_label(
                PERMISSIONS_HINT, PADDING, hint_y, width, 17, size=12, color=theme.MUTED
            )
        )

    def _build_step_hotkey(self, parent_view, content_h: int) -> None:
        width = CONTENT_WIDTH - 2 * PADDING
        rows = [
            (key, action, _hotkey_choice_title(kind, hotkey), description, None)
            for key, action, kind, hotkey, description in HOTKEY_CHOICES
        ]
        rows.append(
            (HOTKEY_CUSTOM_KEY, "record_hotkey:custom", "Custom", HOTKEY_CUSTOM_DESCRIPTION, None)
        )
        self._hotkey_rows = self._add_choice_rows(parent_view, content_h, rows)

        # Record button on the Custom row; the row's description shows the shortcut.
        from AppKit import NSButton, NSFont, NSMakeRect  # type: ignore[import-not-found]

        custom_y = content_h - len(rows) * GOAL_ROW_HEIGHT
        record_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(PADDING + width - 84, custom_y + (GOAL_ROW_HEIGHT - 26) / 2, 84, 26)
        )
        record_btn.setTitle_("Record")
        record_btn.setFont_(NSFont.systemFontOfSize_(13))
        theme.style_ns_outline_button(record_btn)
        self._bind_action(record_btn, "record_hotkey:custom")
        parent_view.addSubview_(record_btn)
        self._hotkey_record_btn = record_btn

        below_rows = custom_y - 16
        hint = _create_label("", PADDING, below_rows - 34, width, 34, size=12, color=theme.MUTED)
        parent_view.addSubview_(hint)
        self._hotkey_hint_label = hint
        status = _create_label("", PADDING, below_rows - 56, width, 17, size=12, color=theme.MUTED)
        parent_view.addSubview_(status)
        self._hotkey_status_label = status
        self._last_hotkey_choice_state = None
        self._sync_hotkey_fields_from_env()

    def _build_step_test_dictation(self, parent_view, content_h: int) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSButton,
            NSFont,
            NSFontWeightMedium,
            NSMakeRect,
            NSScrollView,
            NSTextAlignmentCenter,
            NSTextView,
        )

        width = CONTENT_WIDTH - 2 * PADDING
        top = content_h

        # Prompt line: "Hold [Fn] and say a short sentence."
        prompt_y = top - 24
        before = _create_label("", PADDING, prompt_y + 1, 0, 19, size=14)
        keycap_box = _create_box(
            PADDING, prompt_y, 0, 22, theme.INPUT_BG,
            radius=theme.RADIUS, border=theme.INPUT_BORDER,
        )
        keycap = _create_label(
            "", PADDING, prompt_y + 3, 0, 16, size=12.5, weight=NSFontWeightMedium
        )
        keycap.setAlignment_(NSTextAlignmentCenter)
        after = _create_label("", PADDING, prompt_y + 1, 0, 19, size=14)
        for view in (before, keycap_box, keycap, after):
            parent_view.addSubview_(view)
        self._test_hotkey_label = (before, keycap_box, keycap, after)

        # Start / Stop share one spot; only one is visible at a time.
        buttons_y = prompt_y - 18 - 28

        def add_button(title: str, action: str):
            button = NSButton.alloc().initWithFrame_(NSMakeRect(PADDING, buttons_y, 132, 28))
            button.setTitle_(title)
            button.setFont_(NSFont.systemFontOfSize_(13))
            theme.style_ns_outline_button(button)
            self._bind_action(button, action)
            parent_view.addSubview_(button)
            return button

        self._test_start_btn = add_button("Start practice", "start_test")
        self._test_stop_btn = add_button("Stop", "stop_test")
        self._test_stop_btn.setHidden_(True)

        # Transcript box with the status badge above its right edge.
        header_y = buttons_y - 24 - 20
        parent_view.addSubview_(
            _create_label("Transcript", PADDING, header_y + 2, 200, 16, size=12, color=theme.NAV)
        )
        badge = _create_label("", CONTENT_WIDTH - PADDING - 80, header_y, 80, 20)
        badge.setHidden_(True)
        parent_view.addSubview_(badge)
        self._test_status_label = badge

        box_h = 168
        box_y = header_y - 8 - box_h
        parent_view.addSubview_(
            _create_box(
                PADDING, box_y, width, box_h, theme.INPUT_BG,
                radius=theme.RADIUS, border=theme.INPUT_BORDER,
            )
        )
        inner = NSMakeRect(PADDING + 8, box_y + 6, width - 16, box_h - 12)
        scroll = NSScrollView.alloc().initWithFrame_(inner)
        scroll.setBorderType_(0)  # NSNoBorder
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setDrawsBackground_(False)
        text_view = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, inner.size.width, inner.size.height)
        )
        text_view.setFont_(NSFont.systemFontOfSize_(13))
        text_view.setTextColor_(theme.ns_color(theme.TEXT))
        text_view.setDrawsBackground_(False)
        text_view.setEditable_(False)
        text_view.setSelectable_(True)
        tc = text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)
        scroll.setDocumentView_(text_view)
        parent_view.addSubview_(scroll)
        self._test_text_view = text_view
        self._show_test_outcome(self._test_outcome, render=False)

    def _build_step_cheat_sheet(self, parent_view, content_h: int) -> None:
        from AppKit import NSFontWeightMedium  # type: ignore[import-not-found]

        width = CONTENT_WIDTH - 2 * PADDING
        row_h = 56
        badge_right = PADDING + width
        top = content_h
        parent_view.addSubview_(theme.ns_rule(PADDING, top - 1, width))
        rows = (
            ("provider", "Provider"),
            ("hotkey", "Hotkey"),
            ("perm", "Permissions"),
            ("test", "Practice test"),
        )
        for index, (key, title) in enumerate(rows):
            y = top - (index + 1) * row_h
            text_w = width - 140  # room for the widest badge
            parent_view.addSubview_(
                _create_label(title, PADDING, y + 28, text_w, 18, weight=NSFontWeightMedium)
            )
            detail = _create_label("", PADDING, y + 10, text_w, 17, size=12, color=theme.MUTED)
            parent_view.addSubview_(detail)
            badge = _create_label("", badge_right - 80, y + 18, 80, 20)
            parent_view.addSubview_(badge)
            parent_view.addSubview_(theme.ns_rule(PADDING, y - 1, width))
            setattr(self, f"_summary_{key}_label", detail)
            self._summary_badges[key] = badge

        ready_y = top - len(rows) * row_h - 40
        ready = _create_label("", PADDING, ready_y, width, 20, size=14)
        parent_view.addSubview_(ready)
        self._ready_label = ready

        # Initial update
        self._update_summary()

    def _update_summary(self) -> None:
        """Refreshes the Review rows and the closing hotkey line."""
        self._update_summary_provider()
        self._update_summary_hotkeys()
        self._update_summary_permissions()
        self._update_summary_test()

    def _update_summary_provider(self) -> None:
        mode = (self._get_cached_env_setting("PULSESCRIBE_MODE") or "deepgram").strip().lower()
        key_name = MODE_API_KEYS.get(mode)
        has_key = bool(
            key_name
            and (
                (self._get_cached_api_key(key_name) or "").strip()
                or (os.getenv(key_name) or "").strip()
            )
        )
        self._set_summary_row("provider", *_build_provider_summary_text(mode, has_key))

    def _update_summary_hotkeys(self) -> None:
        toggle_hk, hold_hk = self._get_cached_hotkeys()
        self._set_summary_row("hotkey", *_build_hotkey_summary_text(toggle_hk, hold_hk))
        self._set_cached_summary_label_text(
            getattr(self, "_ready_label", None),
            "_last_ready_text",
            _build_ready_text(toggle_hk, hold_hk),
        )

    def _update_summary_permissions(self) -> None:
        mic_state, access_ok, input_ok = self._read_permission_signature()
        self._set_summary_row(
            "perm", *_build_permission_summary_text(mic_state, access_ok, input_ok)
        )

    def _update_summary_test(self) -> None:
        self._set_summary_row(
            "test", *_build_test_summary_text(getattr(self, "_test_outcome", "pending"))
        )

    def _set_summary_row(self, key: str, detail: str, badge: str, tone: str) -> None:
        """Shows one Review row: muted ``detail`` and a ``badge`` in ``tone``."""
        self._set_cached_summary_label_text(
            getattr(self, f"_summary_{key}_label", None),
            f"_last_summary_{key}_text",
            detail,
        )
        field = getattr(self, "_summary_badges", {}).get(key)
        cache = getattr(self, "_summary_badge_cache", None)
        if cache is None:
            cache = self._summary_badge_cache = {}
        if field is None or cache.get(key) == (badge, tone):
            return
        try:
            theme.set_ns_badge(field, badge, tone, right_edge=CONTENT_WIDTH - PADDING)
            cache[key] = (badge, tone)
        except Exception:
            pass

    def _set_cached_summary_label_text(self, label, cache_attr: str, text: str) -> None:
        if not label:
            return
        try:
            if getattr(self, cache_attr, None) != text:
                label.setStringValue_(text)
                setattr(self, cache_attr, text)
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Actions + State
    # ---------------------------------------------------------------------

    def _wizard_title(self, step: OnboardingStep) -> tuple[str, str]:
        """Returns ``(title, subtitle)`` shown above the step content."""
        return STEP_TITLES.get(step, ("Setup", ""))

    def _persist_step(self, step: OnboardingStep) -> None:
        if self._persist_progress:
            set_onboarding_step(step)

    def _set_step(self, step: OnboardingStep) -> None:
        self._run_step_leave_actions(step)
        self._step = step
        self._persist_step(step)
        self._render()
        self._run_step_enter_actions(step)
        self._ensure_window_focus()

    def _run_step_leave_actions(self, next_step_value: OnboardingStep) -> None:
        if self._step == OnboardingStep.HOTKEY and next_step_value != self._step:
            self._stop_hotkey_recording(cancelled=True)
        if self._step == OnboardingStep.PERMISSIONS and next_step_value != self._step:
            self._stop_permission_auto_refresh()
        if self._step == OnboardingStep.TEST_DICTATION and next_step_value != self._step:
            self._leave_test_dictation_step()

    def _leave_test_dictation_step(self) -> None:
        # Cancel any active test dictation run and disable hotkey routing.
        # Use cancel (not stop) to discard pending results.
        self._call_optional_step_hook(self._on_test_dictation_cancel)
        self._call_optional_step_hook(self._on_disable_test_hotkey_mode)

    @staticmethod
    def _call_optional_step_hook(callback) -> None:
        if not callable(callback):
            return
        try:
            callback()
        except Exception:
            pass

    def _run_step_enter_actions(self, step: OnboardingStep) -> None:
        if step == OnboardingStep.PERMISSIONS:
            self._refresh_permissions()
        if step == OnboardingStep.CHEAT_SHEET:
            self._update_summary()
        if step == OnboardingStep.TEST_DICTATION:
            self._call_optional_step_hook(self._on_enable_test_hotkey_mode)

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
            prompt_message = message or FAST_API_KEY_PROMPT
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
            if not self._apply_fast_goal_choice():
                return False
        elif choice == OnboardingChoice.PRIVATE:
            self._apply_private_goal_choice()

        set_onboarding_choice(choice)
        self._persist_selected_language()
        self._notify_settings_changed()
        self._set_step(OnboardingStep.PERMISSIONS)
        return True

    def _apply_fast_goal_choice(self) -> bool:
        resolution = resolve_fast_choice_updates(
            entered_deepgram_key=self._entered_deepgram_api_key(),
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
        self._hide_fast_api_key_prompt()
        return True

    def _entered_deepgram_api_key(self) -> str:
        if self._api_key_field:
            return self._api_key_field.stringValue().strip()
        return ""

    def _hide_fast_api_key_prompt(self) -> None:
        if self._api_key_container is None:
            return
        try:
            if getattr(self, "_last_api_key_prompt_visible", None) is not False:
                self._api_key_container.setHidden_(True)
                self._last_api_key_prompt_visible = False
        except Exception:
            pass

    def _apply_private_goal_choice(self) -> None:
        apply_local_preset_to_env(default_local_preset_private())
        # The preset writes .env directly; keep the Review rows in sync.
        self._refresh_env_settings_cache()

    def _persist_selected_language(self) -> None:
        if not self._lang_popup:
            return
        selected_title = self._lang_popup.titleOfSelectedItem()
        lang = _language_code_from_title(selected_title)
        self._apply_env_updates(
            {"PULSESCRIBE_LANGUAGE": lang if lang and lang != "auto" else None}
        )

    def _notify_settings_changed(self) -> None:
        if not self._on_settings_changed:
            return
        try:
            self._on_settings_changed()
        except Exception:
            pass

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
        step = self._render_step()
        self._ensure_step_built(step)
        self._sync_visible_step(step)
        self._update_render_title(step)
        self._update_render_sidebar(step)
        self._update_render_navigation(step)
        self._run_render_step_updates(step)

    def _render_step(self) -> OnboardingStep:
        if self._step == OnboardingStep.DONE:
            return OnboardingStep.CHEAT_SHEET
        return self._step

    def _sync_visible_step(self, step: OnboardingStep) -> None:
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

    def _update_render_title(self, step: OnboardingStep) -> None:
        title_text, subtitle_text = self._wizard_title(step)
        self._set_cached_summary_label_text(
            getattr(self, "_step_label", None), "_last_title_text", title_text
        )
        self._set_cached_summary_label_text(
            getattr(self, "_subtitle_label", None), "_last_subtitle_text", subtitle_text
        )

    def _update_render_sidebar(self, step: OnboardingStep) -> None:
        """Marks done steps with a check and highlights the current one."""
        entries = getattr(self, "_sidebar_entries", None)
        if not entries or step == getattr(self, "_last_sidebar_step", None):
            return
        from AppKit import (  # type: ignore[import-not-found]
            NSFont,
            NSFontWeightRegular,
            NSFontWeightSemibold,
        )

        current = WIZARD_STEPS.index(step)
        for index, entry_step in enumerate(WIZARD_STEPS):
            background, marker, marker_label, name = entries[entry_step]
            active = index == current
            try:
                background.setFillColor_(
                    theme.ns_color(theme.ACTIVE_BG) if active else _get_color(0, 0, 0, 0)
                )
                marker.setFillColor_(
                    theme.ns_color(theme.PRIMARY_BG) if active else _get_color(0, 0, 0, 0)
                )
                marker.setBorderColor_(
                    theme.ns_color(theme.PRIMARY_BG if active else theme.SECONDARY_BORDER)
                )
                marker_label.setStringValue_("\u2713" if index < current else str(index + 1))
                marker_label.setTextColor_(
                    theme.ns_color(
                        theme.PRIMARY_TEXT
                        if active
                        else (theme.NAV if index < current else theme.MUTED)
                    )
                )
                name.setTextColor_(theme.ns_color(theme.ACTIVE_TEXT if active else theme.NAV))
                name.setFont_(
                    NSFont.systemFontOfSize_weight_(
                        14, NSFontWeightSemibold if active else NSFontWeightRegular
                    )
                )
            except Exception:
                pass
        self._last_sidebar_step = step

    def _update_render_navigation(self, step: OnboardingStep) -> None:
        if self._back_btn is not None:
            back_hidden = step == OnboardingStep.CHOOSE_GOAL
            try:
                if getattr(self, "_last_back_hidden", None) != back_hidden:
                    self._back_btn.setHidden_(back_hidden)
                    self._last_back_hidden = back_hidden
            except Exception:
                pass

        skip_test_btn = getattr(self, "_skip_test_btn", None)
        if skip_test_btn is not None:
            skip_hidden = step != OnboardingStep.TEST_DICTATION
            try:
                if getattr(self, "_last_skip_test_hidden", None) != skip_hidden:
                    skip_test_btn.setHidden_(skip_hidden)
                    self._last_skip_test_hidden = skip_hidden
            except Exception:
                pass

        if self._next_btn is not None:
            title = "Finish" if step == OnboardingStep.CHEAT_SHEET else "Next"
            can_advance = bool(self._can_advance())
            if (
                getattr(self, "_last_next_title", None) == title
                and getattr(self, "_last_next_enabled", None) == can_advance
            ):
                return
            try:
                from AppKit import NSFontWeightMedium  # type: ignore[import-not-found]

                # Incomplete step: the light primary button turns dark and muted.
                _set_button_text(
                    self._next_btn,
                    title,
                    theme.PRIMARY_TEXT if can_advance else theme.MUTED,
                    weight=NSFontWeightMedium,
                )
                self._next_btn.setEnabled_(can_advance)
                next_fill = getattr(self, "_next_fill", None)
                if next_fill is not None:
                    next_fill.setFillColor_(
                        theme.ns_color(theme.PRIMARY_BG if can_advance else theme.ACTIVE_BG)
                    )
                self._last_next_title = title
                self._last_next_enabled = can_advance
            except Exception:
                pass

    def _run_render_step_updates(self, step: OnboardingStep) -> None:
        if step == OnboardingStep.CHOOSE_GOAL:
            self._sync_goal_rows()
        if step == OnboardingStep.HOTKEY:
            self._sync_hotkey_fields_from_env()
        if step == OnboardingStep.TEST_DICTATION:
            self._update_test_dictation_hotkeys()
            self._refresh_test_action_buttons()

    def _update_test_dictation_hotkeys(self) -> None:
        """Lays out the Practice prompt with the configured hotkey as a keycap."""
        views = getattr(self, "_test_hotkey_label", None)
        if views is None:
            return
        prompt = _build_test_prompt(*self._get_cached_hotkeys())
        if getattr(self, "_last_test_hotkey_text", None) == prompt:
            return
        before, keycap_box, keycap, after = views
        before_text, key_text, after_text = prompt
        try:
            x = PADDING
            for label, text in ((before, before_text), (keycap, key_text), (after, after_text)):
                label.setStringValue_(text)
                label.sizeToFit()
            if before_text:
                before.setFrameOrigin_((x, before.frame().origin.y))
                x += before.frame().size.width + 4
            keycap_w = keycap.frame().size.width + 12
            for view in (keycap_box, keycap):
                view.setHidden_(not key_text)
            if key_text:
                keycap_box.setFrame_(
                    ((x, keycap_box.frame().origin.y), (keycap_w, keycap_box.frame().size.height))
                )
                keycap.setFrame_(
                    ((x, keycap.frame().origin.y), (keycap_w, keycap.frame().size.height))
                )
                x += keycap_w + 4
            after.setFrameOrigin_((x, after.frame().origin.y))
            self._last_test_hotkey_text = prompt
        except Exception:
            pass

    def _handle_action(self, action: str) -> None:
        simple_handler = self._simple_action_handlers().get(action)
        if simple_handler is not None:
            simple_handler()
            return
        if self._handle_goal_action(action):
            return
        if self._handle_permission_action(action):
            return
        self._handle_hotkey_action(action)

    def _simple_action_handlers(self) -> dict[str, Callable[[], None]]:
        return {
            "back": self._handle_back_action,
            "next": self._handle_next_action,
            "cancel": lambda: self._complete(open_settings=True),
            "start_test": self._start_test_dictation,
            "stop_test": self._stop_test_dictation,
            "skip_test": self._handle_skip_test_action,
            "open_settings": lambda: self._complete(open_settings=True),
        }

    def _handle_back_action(self) -> None:
        if self._step != OnboardingStep.CHOOSE_GOAL:
            self._set_step(prev_step(self._step))

    def _handle_next_action(self) -> None:
        if not self._can_advance():
            return
        if self._step == OnboardingStep.CHEAT_SHEET:
            self._complete(open_settings=False)
        elif self._step == OnboardingStep.CHOOSE_GOAL:
            self._apply_selected_goal_choice()
        else:
            self._set_step(next_step(self._step))

    def _handle_skip_test_action(self) -> None:
        if getattr(self, "_test_outcome", "pending") != "passed":
            self._test_outcome = "skipped"
        self._set_step(next_step(self._step))

    def _handle_goal_action(self, action: str) -> bool:
        choice_by_action = {
            "choose_fast": OnboardingChoice.FAST,
            "choose_private": OnboardingChoice.PRIVATE,
            "choose_advanced": OnboardingChoice.ADVANCED,
        }
        choice = choice_by_action.get(action)
        if choice is None:
            return False
        # Selecting a row only marks it; Next applies the choice.
        self._choice = choice
        if choice == OnboardingChoice.FAST and self._fast_choice_needs_api_key():
            self._show_fast_api_key_prompt()
        else:
            self._hide_fast_api_key_prompt()
            self._render()
        return True

    def _fast_choice_needs_api_key(self) -> bool:
        """True when neither a Deepgram nor a Groq key is stored or entered."""
        return resolve_fast_choice_updates(
            entered_deepgram_key=self._entered_deepgram_api_key(),
            cached_deepgram_key=self._get_cached_api_key("DEEPGRAM_API_KEY"),
            env_deepgram_key=os.getenv("DEEPGRAM_API_KEY"),
            cached_groq_key=self._get_cached_api_key("GROQ_API_KEY"),
            env_groq_key=os.getenv("GROQ_API_KEY"),
            current_mode=self._get_cached_env_setting("PULSESCRIBE_MODE"),
        ).should_prompt_for_api_key

    def _handle_permission_action(self, action: str) -> bool:
        if action == "perm_mic":
            self._handle_microphone_permission_action()
        elif action == "perm_access":
            check_accessibility_permission(show_alert=False, request=True)
            self._open_privacy_settings("Privacy_Accessibility")
        elif action == "perm_input":
            check_input_monitoring_permission(show_alert=False, request=True)
            self._open_privacy_settings("Privacy_ListenEvent")
        else:
            return False
        self._kick_permission_auto_refresh()
        return True

    def _handle_microphone_permission_action(self) -> None:
        mic_state = get_microphone_permission_state()
        if mic_state == "not_determined":
            check_microphone_permission(show_alert=False, request=True)
        else:
            self._open_privacy_settings("Privacy_Microphone")

    def _handle_hotkey_action(self, action: str) -> None:
        if action in ("hotkey_f19_toggle", "hotkey_fn_hold", "hotkey_opt_space"):
            self._ensure_step_built(OnboardingStep.HOTKEY)
            preset = action.replace("hotkey_", "")  # f19_toggle, fn_hold, opt_space
            self._apply_hotkey_preset(preset)
            return
        if action == "record_hotkey:custom":
            self._ensure_step_built(OnboardingStep.HOTKEY)
            self._toggle_hotkey_recording("toggle")
            return
        if action == "goto_hotkey":
            self._set_step(OnboardingStep.HOTKEY)
            return
        if action.startswith("goto:"):
            self._handle_sidebar_action(action.split(":", 1)[1])

    def _handle_sidebar_action(self, step_value: str) -> None:
        """Reopens an earlier step from the sidebar; later steps stay locked."""
        target = coerce_onboarding_step(step_value)
        current = self._render_step()
        if target not in WIZARD_STEPS or current not in WIZARD_STEPS:
            return
        if WIZARD_STEPS.index(target) < WIZARD_STEPS.index(current):
            self._set_step(target)

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
    # Hotkey (one choice: the wizard keeps a single Toggle or Hold hotkey)
    # ---------------------------------------------------------------------

    def _sync_hotkey_fields_from_env(self) -> None:
        """Marks the row matching the configured hotkey and shows the hint."""
        rows = getattr(self, "_hotkey_rows", None)
        if not rows:
            return
        toggle, hold = self._get_cached_hotkeys()
        state = (toggle.lower(), hold.lower())
        if state == getattr(self, "_last_hotkey_choice_state", None):
            return
        self._last_hotkey_choice_state = state
        selected = _selected_hotkey_choice(*state)
        self._sync_choice_rows(rows, selected)
        custom_desc = rows[HOTKEY_CUSTOM_KEY][4]
        hint_text = ""
        if toggle and hold:
            hint_text = (
                f"You use two hotkeys: {_hotkey_choice_title('toggle', toggle)} and "
                f"{_hotkey_choice_title('hold', hold)}.\n"
                "Pick a row to use just one, or manage both in Settings → General."
            )
        try:
            custom_desc.setStringValue_(
                _hotkey_choice_title("toggle" if toggle else "hold", toggle or hold)
                if selected == HOTKEY_CUSTOM_KEY
                else HOTKEY_CUSTOM_DESCRIPTION
            )
            self._hotkey_hint_label.setStringValue_(hint_text)
        except Exception:
            pass

    def _toggle_hotkey_recording(self, kind: str) -> None:
        """Records a custom shortcut into the Custom row (saved as Toggle)."""
        recorder = self._hotkey_recorder
        if recorder.recording:
            self._stop_hotkey_recording(cancelled=True)
            self._set_hotkey_status("info", "Recording cancelled. Previous hotkey kept.")
            return
        rows = getattr(self, "_hotkey_rows", None)
        if not rows:
            return
        recorder.start(
            field=rows[HOTKEY_CUSTOM_KEY][4],
            button=self._hotkey_record_btn,
            buttons_to_reset=[self._hotkey_record_btn],
            on_hotkey=lambda hotkey: self._apply_hotkey_change("toggle", hotkey),
            placeholder="Press a shortcut…",
            format_value=_format_hotkey_for_display,
        )
        self._set_hotkey_status("info", "Press the shortcut you want. Esc cancels.")

    def _apply_hotkey_preset(self, preset: str) -> bool:
        """Saves one preset row as the only hotkey."""
        target = HOTKEY_PRESETS.get(preset)
        if target is None:
            return False
        self._stop_hotkey_recording(cancelled=True)
        return self._apply_hotkey_change(*target)

    def _stop_hotkey_recording(self, *, cancelled: bool = False) -> None:
        recorder = getattr(self, "_hotkey_recorder", None)
        if recorder is not None and recorder.recording:
            recorder.stop(cancelled=cancelled)

    def _set_hotkey_status(self, level: str, message: str | None) -> None:
        label = getattr(self, "_hotkey_status_label", None)
        if label is None:
            return
        color = {
            "ok": theme.BADGE_TONES["ok"][1],
            "warning": theme.BADGE_TONES["warn"][1],
            "error": theme.BADGE_TONES["err"][1],
        }.get(level, theme.MUTED)
        try:
            label.setStringValue_(message or "")
            label.setTextColor_(theme.ns_color(color))
        except Exception:
            pass

    def _apply_hotkey_change(self, kind: str, hotkey_str: str) -> bool:
        """Saves ``hotkey_str`` to the ``kind`` slot and clears the other slot."""
        from utils.hotkey_validation import validate_hotkey_change

        normalized, level, message = validate_hotkey_change(
            kind, hotkey_str, replace_other=True
        )
        if level == "error":
            from utils.permissions import is_permission_related_message

            # No permission-related popups: the dedicated Permissions step covers this.
            if is_permission_related_message(message):
                message = "This hotkey needs Input Monitoring. Allow it in the Permissions step."
            else:
                from utils.alerts import show_error_alert

                show_error_alert(
                    "Ungültiger Hotkey",
                    message or "Hotkey konnte nicht gesetzt werden.",
                )
            self._set_hotkey_status("error", message)
            return False

        self._apply_env_updates(
            {
                "PULSESCRIBE_TOGGLE_HOTKEY": normalized if kind == "toggle" else None,
                "PULSESCRIBE_HOLD_HOTKEY": normalized if kind == "hold" else None,
                "PULSESCRIBE_HOTKEY": None,
                "PULSESCRIBE_HOTKEY_MODE": None,
            }
        )
        self._notify_settings_changed()

        if level == "warning":
            self._set_hotkey_status("warning", message)
        else:
            self._set_hotkey_status("ok", "Saved.")
        self._render()
        return True

    # ---------------------------------------------------------------------
    # Test dictation
    # ---------------------------------------------------------------------

    def _show_test_outcome(
        self,
        outcome: str,
        transcript: str | None = None,
        *,
        error: str | None = None,
        render: bool = True,
    ) -> None:
        """Shows a test state: badge, transcript box text and Start/Stop buttons."""
        self._test_outcome = outcome
        self._set_test_status(outcome)
        self._set_test_preview_text(
            _build_test_preview_text(outcome, transcript, error=error)
        )
        self._refresh_test_action_buttons()
        if render:
            self._render()

    def _set_test_status(self, outcome: str) -> None:
        """Shows the transcript badge for ``outcome``; idle states hide it."""
        badge = getattr(self, "_test_status_label", None)
        if badge is None:
            return
        state = TEST_BADGES.get(outcome)
        if getattr(self, "_last_test_status_level", None) == state:
            return
        try:
            badge.setHidden_(state is None)
            if state is not None:
                theme.set_ns_badge(badge, *state, right_edge=CONTENT_WIDTH - PADDING)
            self._last_test_status_level = state
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
        self._show_test_outcome("starting")

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
            self._show_test_outcome("cancelled")
            try:
                cancel_callback()
            except Exception:
                pass
            return

        if self._test_state != "recording" or not callable(stop_callback):
            return

        self._test_state = "stopping"
        self._show_test_outcome("processing")

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
            self._show_test_outcome("recording")
            return

        if normalized in ("stopping", "processing"):
            if self._test_state == "stopping":
                return
            self._test_state = "stopping"
            self._show_test_outcome("processing")

    def on_test_dictation_result(
        self, transcript: str, error: str | None = None
    ) -> None:
        self._test_state = "idle"
        if error:
            self._test_successful = False
            self._show_test_outcome("error", error=error)
            return
        cleaned = (transcript or "").strip()
        self._test_successful = bool(cleaned)
        self._show_test_outcome("passed" if cleaned else "empty", transcript)

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
