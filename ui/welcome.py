"""Welcome/Setup Window für PulseScribe.

Zeigt Onboarding-Informationen, API-Key-Setup und Feature-Übersicht.
Erscheint beim ersten Start und kann über Menubar aufgerufen werden.
"""

import os

from config import LOG_FILE
from ui.hotkey_format import (
    format_hotkey_for_display,
    normalize_hotkey_text,
)
from ui.hotkey_card import HotkeyCard
from ui.logs_panel_feedback import (
    build_logs_empty_state_text,
    build_logs_load_error_text,
    build_logs_manual_refresh_feedback,
    build_logs_open_error_feedback,
    build_logs_open_feedback,
    build_transcripts_clear_feedback,
    build_transcripts_count_text,
    build_transcripts_hint_text,
    build_transcripts_load_error_text,
)
from ui.prompt_editor_feedback import build_prompt_editor_state_feedback
from ui.provider_settings import build_provider_api_key_status
from ui.secondary_settings_feedback import (
    build_display_settings_feedback,
    build_refine_model_guidance,
    build_refine_settings_feedback,
    get_refine_provider_default_model,
    get_refine_provider_label,
    normalize_refine_provider,
)
from ui.settings_apply_feedback import (
    build_save_apply_change_hint,
    build_settings_loaded_feedback,
    build_settings_saved_feedback,
    build_unsaved_settings_feedback,
)
from ui.vocabulary_feedback import (
    build_vocabulary_editor_feedback,
    build_vocabulary_save_feedback,
)
from utils.env import parse_bool
from utils.hotkey_recording import HotkeyRecorder
from utils.local_backend import (
    get_local_advanced_ui_state,
    normalize_local_backend,
)
from utils.log_tail import (
    get_file_signature,
    read_file_tail_text_with_signature,
    read_file_text_from_offset,
    should_auto_refresh_logs,
)
from utils.presets import LOCAL_PRESET_BASE, LOCAL_PRESETS, LOCAL_PRESET_OPTIONS
from utils.settings_env_updates import SettingsEnvUpdateBuilder
from utils.transcript_view_logic import (
    build_transcript_payload,
    should_append_transcript_delta_in_place,
)
from utils.preferences import (
    apply_hotkey_setting,
    get_env_setting,
    get_show_welcome_on_startup,
    read_env_file,
    set_onboarding_seen,
    set_show_welcome_on_startup,
    update_env_settings,
)
from utils.vocabulary import (
    analyze_vocabulary_text,
    load_vocabulary,
    save_vocabulary_state,
    split_vocabulary_text,
)
from utils.custom_prompts import (
    PROMPT_EDITOR_CONTEXT_OPTIONS,
    build_prompt_overrides_from_editor_state,
    get_prompt_editor_context_description,
    get_prompt_editor_context_label,
    normalize_prompt_editor_context,
)

# Window-Konfiguration
WELCOME_WIDTH = 600
WELCOME_HEIGHT = 825  # Höhe für Tabbed Setup
WELCOME_PADDING = 20
FOOTER_HEIGHT = 60
CARD_PADDING = 16
CARD_CORNER_RADIUS = 12
CARD_SPACING = 12

# Verfügbare Optionen für Dropdowns
MODE_OPTIONS = ["deepgram", "openai", "groq", "local"]
REFINE_PROVIDER_OPTIONS = ["groq", "openai", "openrouter", "gemini"]
LANGUAGE_OPTIONS = ["auto", "de", "en", "es", "fr", "it", "pt", "nl", "pl", "ru", "zh"]
LOCAL_BACKEND_OPTIONS = ["auto", "whisper", "faster", "mlx", "lightning"]
LOCAL_MODEL_OPTIONS = [
    "default",
    "turbo",  # Multilingual, best speed/quality
    "large",  # Multilingual, highest quality
    "large-v3",  # Multilingual, best for Lightning preset
    "medium",
    "small",
    "base",
    "tiny",
    # English-only (distilled, faster but ONLY English!)
    "large-en",
    "medium-en",
    "small-en",
]
DEVICE_OPTIONS = ["auto", "mps", "cpu", "cuda"]
BOOL_OVERRIDE_OPTIONS = ["default", "true", "false"]
WARMUP_OPTIONS = ["auto", "true", "false"]
LOCAL_FP16_ENV_KEY = "PULSESCRIBE_FP16"
LEGACY_LOCAL_FP16_ENV_KEY = "PULSESCRIBE_LOCAL_FP16"
API_KEY_PROVIDERS = [
    ("deepgram", "Deepgram", "DEEPGRAM_API_KEY"),
    ("groq", "Groq", "GROQ_API_KEY"),
    ("openai", "OpenAI", "OPENAI_API_KEY"),
    ("openrouter", "OpenRouter", "OPENROUTER_API_KEY"),
    ("gemini", "Gemini", "GEMINI_API_KEY"),
]
PROVIDER_LABELS = {provider: label for provider, label, _env_key in API_KEY_PROVIDERS}
PROVIDER_ENV_KEYS = {provider: env_key for provider, _label, env_key in API_KEY_PROVIDERS}
MODE_API_KEY_PROVIDERS = {
    "deepgram": "deepgram",
    "groq": "groq",
    "openai": "openai",
}
API_KEY_PLACEHOLDERS = {
    "deepgram": "dg-...",
    "groq": "gsk_...",
    "openai": "sk-...",
    "openrouter": "sk-or-...",
    "gemini": "AIza...",
}
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
    "backspace": "Delete",
    "delete": "Delete",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
}
API_KEY_CARD_TOP_INSET = 92
API_KEY_CARD_BOTTOM_INSET = 54
API_KEY_ROW_SPACING = 54
WELCOME_LOG_MAX_CHARS = 15_000
TRANSCRIPTS_VIEW_MAX_ENTRIES = 50
INCREMENTAL_LOG_APPEND_MAX_BYTES = 64_000
INCREMENTAL_TRANSCRIPT_APPEND_MAX_BYTES = 64_000
LOG_TRUNCATED_PREFIX = "... (truncated)\n\n"
LOGS_AUTO_REFRESH_ACTIVE_INTERVAL_S = 2.0
LOGS_AUTO_REFRESH_BACKOFF_INTERVAL_S = 4.0
LOGS_AUTO_REFRESH_IDLE_INTERVAL_S = 8.0
LOGS_AUTO_REFRESH_INTERVALS_S = (
    LOGS_AUTO_REFRESH_ACTIVE_INTERVAL_S,
    LOGS_AUTO_REFRESH_BACKOFF_INTERVAL_S,
    LOGS_AUTO_REFRESH_IDLE_INTERVAL_S,
)


def _bool_override_from_env(*keys: str) -> str:
    """Return default/true/false from the first recognized env override."""
    for key in keys:
        raw = get_env_setting(key)
        if raw is None:
            continue
        normalized = raw.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return "true"
        if normalized in ("0", "false", "no", "off"):
            return "false"
    return "default"


def _get_color(r: int, g: int, b: int, a: float = 1.0):
    """Erstellt NSColor aus RGB-Werten."""
    from AppKit import NSColor  # type: ignore[import-not-found]

    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


def _create_card(x: int, y: int, width: int, height: int):
    """Erstellt eine Karten-Box mit abgerundetem Hintergrund."""
    from AppKit import NSBox, NSColor  # type: ignore[import-not-found]
    from Foundation import NSMakeRect  # type: ignore[import-not-found]

    card = NSBox.alloc().initWithFrame_(NSMakeRect(x, y, width, height))
    card.setBoxType_(4)  # Custom
    card.setBorderType_(0)  # None
    card.setFillColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.06))
    card.setCornerRadius_(CARD_CORNER_RADIUS)
    card.setContentViewMargins_((0, 0))
    return card


def _is_env_enabled_default_true(key: str) -> bool:
    """Check if an env flag is enabled, defaulting to True if not set."""
    from utils.preferences import get_env_setting

    value = get_env_setting(key)
    if value is None:
        return True
    return value.lower() not in ("false", "0", "no", "off")


def _get_api_card_height() -> int:
    """Return a card height that fits all configured API key rows."""
    row_count = len(API_KEY_PROVIDERS)
    if row_count == 0:
        return API_KEY_CARD_TOP_INSET + API_KEY_CARD_BOTTOM_INSET
    return (
        API_KEY_CARD_TOP_INSET
        + API_KEY_CARD_BOTTOM_INSET
        + API_KEY_ROW_SPACING * (row_count - 1)
    )


def _set_hidden_if_changed(view, hidden: bool) -> None:
    """Avoid redundant hide/show mutations on AppKit views."""
    if view is None:
        return
    current_hidden = getattr(view, "isHidden", None)
    if callable(current_hidden):
        try:
            if bool(current_hidden()) == bool(hidden):
                return
        except Exception:
            pass
    try:
        view.setHidden_(hidden)
    except Exception:
        pass


def _set_string_value_if_changed(field, value: str) -> bool:
    """Avoid redundant AppKit text updates when the rendered text is unchanged."""
    if field is None:
        return False
    current_value = getattr(field, "stringValue", None)
    if callable(current_value):
        try:
            if str(current_value()) == value:
                return False
        except Exception:
            pass
    try:
        field.setStringValue_(value)
        return True
    except Exception:
        return False



def _set_text_view_string_if_changed(text_view, value: str) -> bool:
    """Avoid redundant NSTextView content replacements on context switches."""
    if text_view is None:
        return False
    current_value = getattr(text_view, "string", None)
    if callable(current_value):
        try:
            if str(current_value()) == value:
                return False
        except Exception:
            pass
    try:
        text_view.setString_(value)
        return True
    except Exception:
        return False


def _normalize_hotkey_text(value: str | None) -> str:
    return normalize_hotkey_text(value)



def _format_hotkey_for_display(value: str | None) -> str:
    return format_hotkey_for_display(
        value,
        HOTKEY_TOKEN_LABELS,
        omit_empty_parts=False,
    )



def _status_color(level: str):
    if level == "success":
        return _get_color(51, 217, 178)
    if level == "warning":
        return _get_color(255, 177, 66, 0.95)
    if level == "error":
        return _get_color(255, 82, 82, 0.9)
    return _get_color(255, 255, 255, 0.6)



def _apply_status_text(field, text: str, color: str = "text_secondary") -> None:
    if field is None:
        return
    _set_string_value_if_changed(field, text)
    try:
        field.setTextColor_(_status_color(color))
    except Exception:
        pass



def _set_tooltip_if_supported(view, text: str) -> None:
    if view is None:
        return
    setter = getattr(view, "setToolTip_", None)
    if callable(setter):
        try:
            setter(text)
        except Exception:
            pass



def _build_welcome_provider_guidance_text(
    mode: str | None,
    *,
    required_key_present: bool,
) -> str:
    current_mode = (mode or "deepgram").strip().lower() or "deepgram"
    provider = PROVIDER_LABELS.get(current_mode, current_mode.capitalize())

    if current_mode == "local":
        return (
            "Local dictation works without a cloud API key. Keep cloud keys empty unless "
            "you want a cloud provider for transcription or refinement later."
        )

    if required_key_present:
        return (
            f"{provider} is ready for transcription. Other provider keys can stay empty "
            "until you switch modes or use them for refinement."
        )

    return (
        f"{provider} is selected for transcription. Add its API key below before you start "
        "dictation; the other keys are optional for now."
    )



def _build_welcome_api_key_status(
    provider: str,
    *,
    mode: str | None,
    configured: bool,
) -> tuple[str, str]:
    return build_provider_api_key_status(
        provider,
        mode=mode,
        configured=configured,
        required_provider_by_mode=MODE_API_KEY_PROVIDERS,
    )



def _build_welcome_api_key_tooltip(provider: str, *, mode: str | None) -> str:
    provider_label = PROVIDER_LABELS.get(provider, provider.capitalize())
    current_mode = (mode or "deepgram").strip().lower() or "deepgram"

    if current_mode == "local":
        return (
            f"{provider_label} is not needed for local dictation. Add it only if you want "
            "to switch to a cloud provider later."
        )

    if provider == MODE_API_KEY_PROVIDERS.get(current_mode):
        return f"{provider_label} is required for the currently selected transcription mode."

    return (
        f"Optional. Save a {provider_label} key here if you want to switch providers or "
        "use it for refinement later."
    )



def _build_setup_hotkey_info(
    toggle_hotkey: str | None,
    hold_hotkey: str | None,
    fallback_hotkey: str | None = None,
) -> str:
    """Return a concise setup summary for the active hotkey configuration."""

    toggle = _format_hotkey_for_display(toggle_hotkey)
    hold = _format_hotkey_for_display(hold_hotkey)
    fallback_raw = _normalize_hotkey_text(fallback_hotkey)
    fallback = _format_hotkey_for_display(fallback_hotkey)

    hotkey_parts = []
    if toggle:
        hotkey_parts.append(f"Toggle: {toggle}")
    if hold:
        hotkey_parts.append(f"Hold: {hold}")
    if hotkey_parts:
        return " • ".join(hotkey_parts)

    if fallback and fallback_raw.lower() not in {"(nicht konfiguriert)", "(not configured)"}:
        return f"Hotkey: {fallback}"

    return "No hotkey configured"



def _build_setup_try_it_content(
    toggle_hotkey: str | None,
    hold_hotkey: str | None,
    fallback_hotkey: str | None = None,
) -> tuple[str, str, str, str]:
    hotkey_info = _build_setup_hotkey_info(toggle_hotkey, hold_hotkey, fallback_hotkey)
    toggle = _format_hotkey_for_display(toggle_hotkey)
    hold = _format_hotkey_for_display(hold_hotkey)
    fallback = _format_hotkey_for_display(fallback_hotkey)
    active_key = toggle or fallback
    hint = (
        "Need permissions? Accessibility enables pasting; Input Monitoring enables "
        "global hotkeys."
    )

    if hold and toggle:
        return (
            hotkey_info,
            f"Hold {hold} for push-to-talk, or press {toggle} once to start and again to stop.",
            hint,
            "Change Hotkeys…",
        )

    if hold:
        return (
            hotkey_info,
            f"Hold {hold} while speaking, then release it to transcribe into the frontmost app.",
            hint,
            "Change Hotkeys…",
        )

    if active_key:
        return (
            hotkey_info,
            f"Press {active_key} to start dictation, then use it again to stop and paste the result.",
            hint,
            "Change Hotkeys…",
        )

    return (
        hotkey_info,
        "No hotkey yet. Open Hotkeys to record one before you test dictation.",
        hint,
        "Set up Hotkeys…",
    )


class WelcomeController:
    """Welcome/Setup Window für PulseScribe."""

    def __init__(self, hotkey: str, config: dict):
        self.hotkey = hotkey
        self.config = config
        self._window = None
        self._content_view = None
        self._on_start_callback = None
        self._on_settings_changed_callback = None

        # UI-Referenzen (werden in _build_* Methoden gesetzt)
        self._startup_checkbox = None
        self._mode_popup = None
        self._lang_popup = None
        self._refine_checkbox = None
        self._clipboard_restore_checkbox = None
        self._provider_popup = None
        self._model_field = None
        self._refine_model_help_label = None
        self._refine_status_label = None
        self._display_status_label = None
        self._local_backend_popup = None
        self._local_model_popup = None
        self._local_backend_label = None
        self._local_model_label = None
        self._local_preset_popup = None
        self._local_preset_changed_handler = None
        self._device_popup = None
        self._warmup_popup = None
        self._local_fast_popup = None
        self._fp16_popup = None
        self._beam_size_field = None
        self._best_of_field = None
        self._temperature_field = None
        self._compute_type_field = None
        self._cpu_threads_field = None
        self._num_workers_field = None
        self._without_timestamps_popup = None
        self._vad_filter_popup = None
        # Lightning-specific settings
        self._lightning_header = None
        self._lightning_batch_label = None
        self._lightning_batch_slider = None
        self._lightning_batch_value_label = None
        self._lightning_batch_handler = None
        self._lightning_quant_label = None
        self._lightning_quant_popup = None
        self._backend_changed_handler = None
        self._advanced_general_views = ()
        # Streaming toggle (Deepgram)
        self._streaming_label = None
        self._streaming_checkbox = None
        # Display toggles
        self._overlay_checkbox = None
        self._dock_icon_checkbox = None
        self._rtf_checkbox = None
        self._tab_view = None
        self._tab_builders: dict[str, tuple[object, object, int]] = {}
        self._built_tabs: set[str] = set()
        self._tab_delegate = None
        self._saved_settings_signature: tuple | None = None
        self._saved_dock_icon_enabled: bool | None = None
        self._saved_refine_settings_state: tuple[bool, str, str] | None = None
        self._saved_display_settings_state: tuple[bool, ...] | None = None
        self._vocab_text_view = None
        self._vocab_warning_label = None
        self._vocab_text_change_handler = None
        self._loaded_vocabulary_keywords: list[str] | None = None
        self._logs_text_view = None
        self._logs_scroll_view = None
        self._logs_refresh_handler = None
        self._logs_auto_refresh_handler = None
        self._logs_auto_checkbox = None
        self._logs_auto_refresh_timer = None
        self._logs_auto_refresh_interval_seconds: float | None = None
        self._logs_auto_refresh_step = 0
        self._logs_finder_handler = None
        self._last_logs_text = None
        self._last_logs_signature = None
        self._last_logs_chunks = None
        self._last_logs_truncated = False
        self._last_transcripts_text = None
        self._last_transcripts_signature = None
        self._last_transcripts_entries = None
        self._last_transcripts_blocks = None
        self._transcripts_view_built = False
        self._transcripts_layout_metrics = None
        self._transcripts_view_seen = False
        # Logs/Transcripts segmented control
        self._logs_segment_control = None
        self._logs_segment_handler = None
        self._logs_container = None
        self._transcripts_container = None
        self._active_logs_segment = 0
        self._transcripts_text_view = None
        self._transcripts_scroll_view = None
        self._transcripts_count_label = None
        self._last_transcripts_count_text: str | None = None
        self._transcripts_clear_handler = None
        self._mode_changed_handler = None
        self._save_btn = None
        self._restart_handler = None
        self._prompts_defaults_data: dict | None = None
        self._prompts_loaded_data: dict | None = None
        self._prompts_text_view = None
        self._prompts_hint_label = None
        self._prompts_state_label = None
        self._prompts_status_label = None
        self._prompts_reset_btn = None
        self._prompts_text_change_handler = None
        self._prompt_text_cache: dict[str, str] = {}
        self._prompt_text_cache_source: dict | None = None
        self._prompt_defaults_text_cache: dict[str, str] = {}
        self._prompt_defaults_text_cache_source: dict | None = None
        self._hotkey_card: HotkeyCard | None = None
        self._hotkey_recorder = HotkeyRecorder()
        # Setup/Onboarding Tab
        self._setup_action_handlers = []
        self._setup_permissions_card = None
        self._setup_preset_status_label = None
        self._setup_try_hotkey_label = None
        self._setup_try_body_label = None
        self._setup_try_hint_label = None
        self._setup_try_change_button = None
        self._onboarding_wizard_callback = None
        self._env_settings_cache: dict[str, str] = read_env_file()
        self._provider_guidance_label = None
        self._footer_status_label = None
        # API-Key-Felder werden dynamisch via setattr gesetzt:
        # _{provider}_field, _{provider}_status für alle Einträge aus API_KEY_PROVIDERS

        self._build_window()

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
        cache = getattr(self, "_env_settings_cache", None) or {}
        return (
            (cache.get("PULSESCRIBE_TOGGLE_HOTKEY") or "").strip(),
            (cache.get("PULSESCRIBE_HOLD_HOTKEY") or "").strip(),
        )

    def _get_cached_env_setting(self, key: str) -> str | None:
        cache = getattr(self, "_env_settings_cache", None)
        if cache is not None:
            return cache.get(key)
        return get_env_setting(key)

    def _setup_edit_menu(self) -> None:
        """Erstellt Edit-Menü für CMD+V/C/X/A in TextViews.

        NSTextView braucht ein Edit-Menü in der Menüleiste, damit die
        Standard-Shortcuts (Copy, Paste, etc.) funktionieren.
        """
        from AppKit import (  # type: ignore[import-not-found]
            NSApp,
            NSMenu,
            NSMenuItem,
        )

        # Prüfen ob Edit-Menü bereits existiert
        main_menu = NSApp.mainMenu()
        if main_menu is None:
            main_menu = NSMenu.alloc().init()
            NSApp.setMainMenu_(main_menu)

        # Prüfen ob Edit-Menü schon vorhanden
        for i in range(main_menu.numberOfItems()):
            item = main_menu.itemAtIndex_(i)
            if item.title() == "Edit":
                return  # Bereits vorhanden

        # Edit-Menü erstellen
        edit_menu = NSMenu.alloc().initWithTitle_("Edit")

        # Undo/Redo
        undo_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Undo", "undo:", "z"
        )
        edit_menu.addItem_(undo_item)

        redo_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Redo", "redo:", "Z"
        )
        edit_menu.addItem_(redo_item)

        edit_menu.addItem_(NSMenuItem.separatorItem())

        # Cut/Copy/Paste
        cut_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Cut", "cut:", "x"
        )
        edit_menu.addItem_(cut_item)

        copy_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Copy", "copy:", "c"
        )
        edit_menu.addItem_(copy_item)

        paste_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Paste", "paste:", "v"
        )
        edit_menu.addItem_(paste_item)

        edit_menu.addItem_(NSMenuItem.separatorItem())

        # Select All
        select_all_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Select All", "selectAll:", "a"
        )
        edit_menu.addItem_(select_all_item)

        # Edit-Menü zur Menüleiste hinzufügen
        edit_menu_item = NSMenuItem.alloc().init()
        edit_menu_item.setTitle_("Edit")
        edit_menu_item.setSubmenu_(edit_menu)
        main_menu.addItem_(edit_menu_item)

    def _build_window(self) -> None:
        """Erstellt das Welcome Window mit allen Sections."""
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
        x = (screen_frame.size.width - WELCOME_WIDTH) / 2
        y = (screen_frame.size.height - WELCOME_HEIGHT) / 2

        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, WELCOME_WIDTH, WELCOME_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        self._window.setTitle_("PulseScribe Settings")
        self._window.setReleasedWhenClosed_(False)

        # Edit-Menü für CMD+V/C/X/A in TextViews
        self._setup_edit_menu()

        # Visual Effect View (HUD-Material)
        content_frame = NSMakeRect(0, 0, WELCOME_WIDTH, WELCOME_HEIGHT)
        visual_effect = NSVisualEffectView.alloc().initWithFrame_(content_frame)
        visual_effect.setMaterial_(13)  # HUD Window
        visual_effect.setBlendingMode_(0)
        visual_effect.setState_(1)
        self._window.setContentView_(visual_effect)
        self._content_view = visual_effect

        # Header + Tabs
        y_pos = WELCOME_HEIGHT - WELCOME_PADDING
        header_bottom = self._build_header(y_pos)
        self._build_tabs(header_bottom)
        self._build_footer()
        self._saved_settings_signature = self._get_current_settings_signature()
        self._saved_dock_icon_enabled = self._get_current_dock_icon_enabled()
        text, color = build_settings_loaded_feedback()
        self._set_footer_status(text, color)

    def _build_header(self, y: int) -> int:
        """Erstellt zentrierten Header."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightBold,
            NSFontWeightLight,
            NSMakeRect,
            NSTextAlignmentCenter,
            NSTextField,
        )

        # App-Titel groß
        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, y - 36, WELCOME_WIDTH, 36)
        )
        title.setStringValue_("🎤 PulseScribe")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setAlignment_(NSTextAlignmentCenter)
        title.setFont_(NSFont.systemFontOfSize_weight_(28, NSFontWeightBold))
        title.setTextColor_(NSColor.whiteColor())
        self._content_view.addSubview_(title)

        # Untertitel
        subtitle = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, y - 58, WELCOME_WIDTH, 18)
        )
        subtitle.setStringValue_("Voice-to-text for macOS")
        subtitle.setBezeled_(False)
        subtitle.setDrawsBackground_(False)
        subtitle.setEditable_(False)
        subtitle.setSelectable_(False)
        subtitle.setAlignment_(NSTextAlignmentCenter)
        subtitle.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightLight))
        subtitle.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        self._content_view.addSubview_(subtitle)

        return y - 72

    # =============================================================================
    # Tabs
    # =============================================================================

    def _build_tabs(self, header_bottom: int) -> None:
        """Erstellt die Tab-View und lädt Inhalte nur bei Bedarf."""
        from AppKit import (  # type: ignore[import-not-found]
            NSFont,
            NSTabView,
        )
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        tab_y = WELCOME_PADDING + FOOTER_HEIGHT
        tab_height = max(200, header_bottom - tab_y - CARD_SPACING)

        tab_view = NSTabView.alloc().initWithFrame_(
            NSMakeRect(0, tab_y, WELCOME_WIDTH, tab_height)
        )
        try:
            tab_view.setDrawsBackground_(False)
        except Exception:
            pass
        tab_view.setFont_(NSFont.systemFontOfSize_(12))
        self._content_view.addSubview_(tab_view)
        self._tab_view = tab_view
        # Content-Rect berücksichtigt die Tab-Bar Höhe/Insets
        try:
            content_height = tab_view.contentRect().size.height
        except Exception:
            content_height = tab_height

        self._tab_builders = {}
        self._built_tabs = set()

        lazy_tabs_supported = _TabSelectionHandler is not None
        if lazy_tabs_supported:
            delegate = _TabSelectionHandler.alloc().initWithController_(self)
            self._tab_delegate = delegate
            try:
                tab_view.setDelegate_(delegate)
            except Exception:
                lazy_tabs_supported = False
                self._tab_delegate = None

        tab_specs = [
            ("Setup", self._build_setup_tab),
            ("Hotkeys", self._build_hotkeys_tab),
            ("Providers", self._build_providers_tab),
            ("Advanced", self._build_advanced_tab),
            ("Refine", self._build_refine_tab),
            ("Prompts", self._build_prompts_tab),
            ("Vocabulary", self._build_vocabulary_tab),
            ("Logs", self._build_logs_tab),
            ("About", self._build_about_tab),
        ]
        for label, builder in tab_specs:
            self._add_tab(
                tab_view,
                label,
                builder,
                content_height,
                build_immediately=not lazy_tabs_supported,
            )

        if lazy_tabs_supported:
            self._ensure_selected_tab_built()

    def _add_tab(
        self,
        tab_view,
        label: str,
        builder,
        tab_height: int,
        *,
        build_immediately: bool,
    ) -> None:
        from AppKit import NSTabViewItem, NSView  # type: ignore[import-not-found]
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        item = NSTabViewItem.alloc().initWithIdentifier_(label)
        item.setLabel_(label)
        content = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, WELCOME_WIDTH, tab_height)
        )
        item.setView_(content)
        tab_view.addTabViewItem_(item)
        self._tab_builders[label] = (builder, content, tab_height)
        if build_immediately:
            self._ensure_tab_built(label)

    def _is_tab_built(self, label: str) -> bool:
        return label in getattr(self, "_built_tabs", set())

    def _ensure_tab_built(self, label: str | None) -> bool:
        if not label or self._is_tab_built(label):
            return False

        builder_entry = getattr(self, "_tab_builders", {}).get(label)
        if builder_entry is None:
            return False

        builder, content, tab_height = builder_entry
        builder(content, tab_height)
        self._built_tabs.add(label)
        return True

    def _ensure_selected_tab_built(self) -> bool:
        if self._tab_view is None:
            return False
        try:
            selected_item = self._tab_view.selectedTabViewItem()
        except Exception:
            return False
        if selected_item is None:
            return False
        try:
            label = str(selected_item.identifier())
        except Exception:
            return False
        return self._ensure_tab_built(label)

    def _build_setup_tab(self, parent_view, tab_height: int) -> None:
        """Setup overview + shortcuts (wizard lives in a separate window)."""
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSFont,
            NSFontWeightMedium,
            NSMakeRect,
        )
        import objc  # type: ignore[import-not-found]

        # "Run Setup Wizard" shortcut
        wizard_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(WELCOME_WIDTH - WELCOME_PADDING - 180, tab_height - 36, 180, 26)
        )
        wizard_btn.setTitle_("Run Setup Wizard…")
        wizard_btn.setBezelStyle_(NSBezelStyleRounded)
        wizard_btn.setFont_(NSFont.systemFontOfSize_weight_(12, NSFontWeightMedium))
        wizard_handler = _SetupActionHandler.alloc().initWithController_action_(
            self, "open_onboarding_wizard"
        )
        wizard_btn.setTarget_(wizard_handler)
        wizard_btn.setAction_(
            objc.selector(wizard_handler.performAction_, signature=b"v@:@")
        )
        self._setup_action_handlers.append(wizard_handler)
        parent_view.addSubview_(wizard_btn)

        y_pos = tab_height - 52
        y_pos = self._build_setup_permissions_card(y_pos, parent_view)
        y_pos = self._build_setup_recommended_card(y_pos, parent_view)
        self._build_setup_howto_card(y_pos, parent_view)
        self._refresh_setup_permissions()

    def _open_privacy_settings(self, anchor: str) -> None:
        """Öffnet System Settings → Privacy & Security (best effort)."""
        from utils.permissions import open_privacy_settings

        open_privacy_settings(anchor, window=self._window)

    def _open_onboarding_wizard(self) -> None:
        if callable(self._onboarding_wizard_callback):
            try:
                self._onboarding_wizard_callback()
            except Exception:
                pass

    def _handle_setup_permission_action(self, action: str) -> bool:
        from utils.permissions import (
            check_accessibility_permission,
            check_input_monitoring_permission,
            check_microphone_permission,
            get_microphone_permission_state,
        )

        if action == "perm_mic":
            mic_state = get_microphone_permission_state()
            if mic_state == "not_determined":
                check_microphone_permission(show_alert=False, request=True)
            else:
                self._open_privacy_settings("Privacy_Microphone")
            self._kick_setup_permission_auto_refresh()
            return True

        if action == "perm_access":
            check_accessibility_permission(show_alert=False, request=True)
            self._open_privacy_settings("Privacy_Accessibility")
            self._kick_setup_permission_auto_refresh()
            return True

        if action == "perm_input":
            check_input_monitoring_permission(show_alert=False, request=True)
            self._open_privacy_settings("Privacy_ListenEvent")
            self._kick_setup_permission_auto_refresh()
            return True

        return False

    def _handle_setup_preset_action(self, action: str) -> bool:
        preset_actions = {
            "apply_mlx_large_preset": (
                "macOS: MLX Balanced (large)",
                "MLX Large",
            ),
            "apply_mlx_turbo_preset": (
                "macOS: MLX Fast (turbo)",
                "MLX Turbo",
            ),
            "apply_lightning_preset": (
                "macOS: Lightning Fast (large-v3)",
                "Lightning",
            ),
        }
        preset = preset_actions.get(action)
        if preset is None:
            return False

        preset_name, label = preset
        self._apply_local_preset(preset_name)
        _apply_status_text(
            self._setup_preset_status_label,
            f"{label} selected. {build_save_apply_change_hint()}",
            "success",
        )
        return True

    def _goto_hotkeys_tab(self) -> None:
        # Tab index 1 = Hotkeys (Setup=0, Hotkeys=1, Providers=2, ...)
        if self._tab_view is not None:
            self._tab_view.selectTabViewItemAtIndex_(1)
            self._ensure_selected_tab_built()

    def _handle_setup_action(self, action: str) -> None:
        if action == "open_onboarding_wizard":
            self._open_onboarding_wizard()
            return

        if self._handle_setup_permission_action(action):
            return

        if self._handle_setup_preset_action(action):
            return

        if action == "goto_hotkeys_tab":
            self._goto_hotkeys_tab()
            return

    def _refresh_setup_permissions(self) -> None:
        card = self._setup_permissions_card
        if card is None:
            return
        try:
            card.refresh()
        except Exception:
            pass

    def _stop_setup_permission_auto_refresh(self) -> None:
        card = self._setup_permissions_card
        if card is None:
            return
        try:
            card.stop_auto_refresh()
        except Exception:
            pass

    def _kick_setup_permission_auto_refresh(self) -> None:
        card = self._setup_permissions_card
        if card is None:
            return
        try:
            card.kick_auto_refresh()
        except Exception:
            pass

    def _current_mode(self) -> str:
        mode_popup = getattr(self, "_mode_popup", None)
        if mode_popup:
            selected = mode_popup.titleOfSelectedItem()
            if selected:
                return str(selected).strip().lower() or "deepgram"
        cached_mode = self._get_cached_env_setting("PULSESCRIBE_MODE")
        config = getattr(self, "config", {}) or {}
        return (cached_mode or config.get("mode") or "deepgram").strip().lower()

    def _get_welcome_api_key_states(self) -> dict[str, bool]:
        api_key_states: dict[str, bool] = {}
        for provider, _label, env_key in API_KEY_PROVIDERS:
            field = getattr(self, f"_{provider}_field", None)
            raw_value = ""
            if field is not None:
                try:
                    raw_value = field.stringValue().strip()
                except Exception:
                    raw_value = ""
            if not raw_value:
                raw_value = (
                    self._get_cached_env_setting(env_key)
                    or os.getenv(env_key)
                    or ""
                ).strip()
            api_key_states[provider] = bool(raw_value)
        return api_key_states

    def _refresh_provider_key_statuses(self) -> None:
        api_key_states = self._get_welcome_api_key_states()
        mode = self._current_mode()
        required_provider = MODE_API_KEY_PROVIDERS.get(mode)
        required_key_present = bool(
            required_provider and api_key_states.get(required_provider)
        )

        guidance_label = getattr(self, "_provider_guidance_label", None)
        if guidance_label is not None:
            _apply_status_text(
                guidance_label,
                _build_welcome_provider_guidance_text(
                    mode,
                    required_key_present=required_key_present,
                ),
                "text_secondary",
            )

        for provider, _label, _env_key in API_KEY_PROVIDERS:
            status = getattr(self, f"_{provider}_status", None)
            if status is None:
                continue
            status_text, color = _build_welcome_api_key_status(
                provider,
                mode=mode,
                configured=api_key_states.get(provider, False),
            )
            _apply_status_text(status, status_text, color)
            tooltip = _build_welcome_api_key_tooltip(provider, mode=mode)
            _set_tooltip_if_supported(getattr(self, f"_{provider}_field", None), tooltip)
            _set_tooltip_if_supported(status, tooltip)

        self._refresh_footer_settings_hint()

    def _refresh_setup_try_card(self) -> None:
        hotkey_label = getattr(self, "_setup_try_hotkey_label", None)
        body_label = getattr(self, "_setup_try_body_label", None)
        hint_label = getattr(self, "_setup_try_hint_label", None)
        change_button = getattr(self, "_setup_try_change_button", None)
        if not (hotkey_label and body_label and hint_label and change_button):
            return

        toggle_hotkey, hold_hotkey = self._get_cached_hotkeys()
        hotkey_info, body_text, hint_text, button_title = _build_setup_try_it_content(
            toggle_hotkey,
            hold_hotkey,
            getattr(self, "hotkey", None),
        )
        _set_string_value_if_changed(hotkey_label, hotkey_info)
        _set_string_value_if_changed(body_label, body_text)
        _set_string_value_if_changed(hint_label, hint_text)
        try:
            if str(change_button.title()) != button_title:
                change_button.setTitle_(button_title)
        except Exception:
            try:
                change_button.setTitle_(button_title)
            except Exception:
                pass

    def _select_tab(self, label: str) -> None:
        if self._tab_view is None:
            return
        try:
            self._tab_view.selectTabViewItemWithIdentifier_(label)
            self._ensure_tab_built(label)
        except Exception:
            pass

    def _build_setup_permissions_card(self, y: int, parent_view=None) -> int:
        import objc  # type: ignore[import-not-found]
        from ui.permissions_card import PERMISSIONS_DESCRIPTION, PermissionsCard

        parent_view = parent_view or self._content_view

        card_height = 236  # Fits 3-line description, 3 rows, and summary
        card_y = y - card_height - CARD_SPACING

        def bind_action(btn, action: str) -> None:
            handler = _SetupActionHandler.alloc().initWithController_action_(
                self, action
            )
            btn.setTarget_(handler)
            btn.setAction_(objc.selector(handler.performAction_, signature=b"v@:@"))
            self._setup_action_handlers.append(handler)

        self._setup_permissions_card = PermissionsCard.build(
            parent_view=parent_view,
            window_width=WELCOME_WIDTH,
            card_y=card_y,
            card_height=card_height,
            outer_padding=WELCOME_PADDING,
            inner_padding=CARD_PADDING,
            title="Permissions",
            description=PERMISSIONS_DESCRIPTION,
            bind_action=bind_action,
        )

        return card_y - CARD_SPACING

    def _build_setup_recommended_card(self, y: int, parent_view=None) -> int:
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

        parent_view = parent_view or self._content_view

        card_height = 140
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height - CARD_SPACING
        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 28, 320, 18)
        )
        title.setStringValue_("⚡ Recommended (Apple Silicon)")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                base_x, card_y + card_height - 46, card_width - 2 * CARD_PADDING, 14
            )
        )
        desc.setStringValue_(
            "One click presets for fast local dictation (MLX/Lightning)."
        )
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        btn_w = 110
        btn_h = 28
        btn_y = card_y + 56
        btn1 = NSButton.alloc().initWithFrame_(NSMakeRect(base_x, btn_y, btn_w, btn_h))
        btn1.setTitle_("MLX Large")
        btn1.setBezelStyle_(NSBezelStyleRounded)
        btn1.setFont_(NSFont.systemFontOfSize_(12))
        h1 = _SetupActionHandler.alloc().initWithController_action_(
            self, "apply_mlx_large_preset"
        )
        btn1.setTarget_(h1)
        btn1.setAction_(objc.selector(h1.performAction_, signature=b"v@:@"))
        self._setup_action_handlers.append(h1)
        parent_view.addSubview_(btn1)

        btn2 = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x + btn_w + 8, btn_y, btn_w, btn_h)
        )
        btn2.setTitle_("MLX Turbo")
        btn2.setBezelStyle_(NSBezelStyleRounded)
        btn2.setFont_(NSFont.systemFontOfSize_(12))
        h2 = _SetupActionHandler.alloc().initWithController_action_(
            self, "apply_mlx_turbo_preset"
        )
        btn2.setTarget_(h2)
        btn2.setAction_(objc.selector(h2.performAction_, signature=b"v@:@"))
        self._setup_action_handlers.append(h2)
        parent_view.addSubview_(btn2)

        btn3 = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x + 2 * (btn_w + 8), btn_y, btn_w, btn_h)
        )
        btn3.setTitle_("⚡ Lightning")
        btn3.setBezelStyle_(NSBezelStyleRounded)
        btn3.setFont_(NSFont.systemFontOfSize_(12))
        h3 = _SetupActionHandler.alloc().initWithController_action_(
            self, "apply_lightning_preset"
        )
        btn3.setTarget_(h3)
        btn3.setAction_(objc.selector(h3.performAction_, signature=b"v@:@"))
        self._setup_action_handlers.append(h3)
        parent_view.addSubview_(btn3)

        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 18, card_width - 2 * CARD_PADDING, 30)
        )
        status.setStringValue_(f"Choose a preset, then {build_save_apply_change_hint()}")
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_(11))
        status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        try:
            status.setLineBreakMode_(0)
            status.setUsesSingleLineMode_(False)
        except Exception:
            pass
        parent_view.addSubview_(status)
        self._setup_preset_status_label = status

        return card_y - CARD_SPACING

    def _build_setup_howto_card(self, y: int, parent_view=None) -> int:
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

        parent_view = parent_view or self._content_view

        hotkey_info, body_text, hint_text, button_title = _build_setup_try_it_content(
            self._get_cached_env_setting("PULSESCRIBE_TOGGLE_HOTKEY"),
            self._get_cached_env_setting("PULSESCRIBE_HOLD_HOTKEY"),
            self.hotkey,
        )

        card_height = 105
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height - CARD_SPACING
        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        content_w = card_width - 2 * CARD_PADDING

        # Titel
        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 28, 80, 18)
        )
        title.setStringValue_("🎤 Try it")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        # Hotkey-Info rechts neben Titel
        hotkey_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x + 80, card_y + card_height - 28, content_w - 80, 18)
        )
        hotkey_label.setStringValue_(hotkey_info)
        hotkey_label.setBezeled_(False)
        hotkey_label.setDrawsBackground_(False)
        hotkey_label.setEditable_(False)
        hotkey_label.setSelectable_(False)
        hotkey_label.setFont_(NSFont.systemFontOfSize_(11))
        hotkey_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(hotkey_label)
        self._setup_try_hotkey_label = hotkey_label

        # Beschreibung
        body = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 60, content_w, 28)
        )
        body.setStringValue_(body_text)
        body.setBezeled_(False)
        body.setDrawsBackground_(False)
        body.setEditable_(False)
        body.setSelectable_(False)
        body.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        body.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        parent_view.addSubview_(body)
        self._setup_try_body_label = body

        # Footer-Zeile: Button links, Hint rechts
        footer_y = card_y + 12

        change_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x, footer_y, 130, 24)
        )
        change_btn.setTitle_(button_title)
        change_btn.setBezelStyle_(NSBezelStyleRounded)
        change_btn.setFont_(NSFont.systemFontOfSize_(11))
        h_change = _SetupActionHandler.alloc().initWithController_action_(
            self, "goto_hotkeys_tab"
        )
        change_btn.setTarget_(h_change)
        change_btn.setAction_(objc.selector(h_change.performAction_, signature=b"v@:@"))
        self._setup_action_handlers.append(h_change)
        parent_view.addSubview_(change_btn)
        self._setup_try_change_button = change_btn

        hint = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x + 140, footer_y + 5, content_w - 140, 14)
        )
        hint.setStringValue_(hint_text)
        hint.setBezeled_(False)
        hint.setDrawsBackground_(False)
        hint.setEditable_(False)
        hint.setSelectable_(False)
        hint.setFont_(NSFont.systemFontOfSize_(10))
        hint.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.45))
        parent_view.addSubview_(hint)
        self._setup_try_hint_label = hint

        return card_y - CARD_SPACING

    def _build_hotkeys_tab(self, parent_view, tab_height: int) -> None:
        y_pos = tab_height - WELCOME_PADDING
        self._build_hotkey_card(y_pos, parent_view)

    def _build_providers_tab(self, parent_view, tab_height: int) -> None:
        y_pos = tab_height - WELCOME_PADDING
        y_pos = self._build_settings_card(y_pos, parent_view)
        self._build_api_card(y_pos, parent_view)

    def _build_advanced_tab(self, parent_view, tab_height: int) -> None:
        y_pos = tab_height - WELCOME_PADDING
        self._build_advanced_local_card(y_pos, parent_view)

    def _build_refine_tab(self, parent_view, tab_height: int) -> None:
        y_pos = tab_height - WELCOME_PADDING
        self._build_refine_card(y_pos, parent_view, tab_height)

    def _build_prompts_tab(self, parent_view, tab_height: int) -> None:
        y_pos = tab_height - WELCOME_PADDING
        self._build_prompts_card(y_pos, parent_view, tab_height)

    def _build_vocabulary_tab(self, parent_view, tab_height: int) -> None:
        y_pos = tab_height - WELCOME_PADDING
        self._build_vocabulary_card(y_pos, parent_view, tab_height)

    def _build_logs_tab(self, parent_view, tab_height: int) -> None:
        y_pos = tab_height - WELCOME_PADDING
        self._build_logs_card(y_pos, parent_view, tab_height)

    def _build_about_tab(self, parent_view, tab_height: int) -> None:
        y_pos = tab_height - WELCOME_PADDING
        self._build_about_card(y_pos, parent_view)

    def _build_hotkey_card(self, y: int, parent_view=None) -> int:
        """Erstellt Hotkey-Karte mit HotkeyCard-Komponente."""
        import objc  # type: ignore[import-not-found]

        card_height = 220  # Erhöht für Preset-Buttons
        card_y = y - card_height - CARD_SPACING
        parent_view = parent_view or self._content_view

        def bind_action(btn, action: str) -> None:
            # Route preset/record actions to _handle_hotkey_action
            handler = _HotkeyActionHandler.alloc().initWithController_action_(
                self, action
            )
            btn.setTarget_(handler)
            btn.setAction_(objc.selector(handler.performAction_, signature=b"v@:@"))
            self._setup_action_handlers.append(handler)

        self._hotkey_card = HotkeyCard.build(
            parent_view=parent_view,
            window_width=WELCOME_WIDTH,
            card_y=card_y,
            card_height=card_height,
            outer_padding=WELCOME_PADDING,
            inner_padding=CARD_PADDING,
            title="⌨️ Hotkeys",
            description="Press to start/stop recording.\nChanges apply immediately.",
            bind_action=bind_action,
            hotkey_recorder=self._hotkey_recorder,
            on_hotkey_change=self._apply_hotkey_change,
            on_after_change=self._on_settings_changed,
            get_current_hotkeys=self._get_cached_hotkeys,
            show_presets=True,
            show_hint=True,
        )

        return card_y - CARD_SPACING

    def _build_api_card(self, y: int, parent_view=None) -> int:
        """Erstellt API-Konfigurationskarte."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        parent_view = parent_view or self._content_view

        card_height = _get_api_card_height()
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        content_width = card_width - 2 * CARD_PADDING

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 30, 200, 18)
        )
        title.setStringValue_("🔑 API Keys")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        guidance = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 50, content_width, 16)
        )
        guidance.setStringValue_("")
        guidance.setBezeled_(False)
        guidance.setDrawsBackground_(False)
        guidance.setEditable_(False)
        guidance.setSelectable_(False)
        guidance.setFont_(NSFont.systemFontOfSize_(11))
        guidance.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(guidance)
        self._provider_guidance_label = guidance

        row_y = card_y + card_height - API_KEY_CARD_TOP_INSET
        for provider, label_text, key_name in API_KEY_PROVIDERS:
            self._build_api_row_compact(
                row_y,
                label_text,
                key_name,
                provider,
                parent_view,
            )
            row_y -= API_KEY_ROW_SPACING

        self._refresh_provider_key_statuses()
        return card_y - CARD_SPACING

    def _build_api_row_compact(
        self, y: int, label_text: str, key_name: str, provider: str, parent_view=None
    ) -> None:
        """Erstellt kompakte API-Key-Zeile mit verständlichem Inline-Status."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSMakeRect,
            NSTextField,
        )
        import objc  # type: ignore[import-not-found]

        parent_view = parent_view or self._content_view

        base_x = WELCOME_PADDING + CARD_PADDING
        status_width = 84

        label = NSTextField.alloc().initWithFrame_(NSMakeRect(base_x, y + 22, 120, 14))
        label.setStringValue_(label_text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        parent_view.addSubview_(label)

        field_width = (
            WELCOME_WIDTH - 2 * WELCOME_PADDING - 2 * CARD_PADDING - status_width - 8
        )
        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, y, field_width, 22)
        )
        field.setPlaceholderString_(API_KEY_PLACEHOLDERS.get(provider, "Enter API key..."))
        field.setFont_(NSFont.systemFontOfSize_(11))

        existing_key = self._get_cached_env_setting(key_name) or os.getenv(key_name)
        if existing_key:
            field.setStringValue_(existing_key)

        if _SimpleHandler is not None:
            field_handler = _SimpleHandler.alloc().initWithController_method_(
                self, "_refresh_provider_key_statuses"
            )
            try:
                field.setTarget_(field_handler)
                field.setAction_(
                    objc.selector(field_handler.performAction_, signature=b"v@:@")
                )
            except Exception:
                field_handler = None
            setattr(self, f"_{provider}_field_handler", field_handler)

        parent_view.addSubview_(field)

        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x + field_width + 8, y + 2, status_width, 18)
        )
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_(11))
        parent_view.addSubview_(status)

        setattr(self, f"_{provider}_field", field)
        setattr(self, f"_{provider}_status", status)
        _set_tooltip_if_supported(field, _build_welcome_api_key_tooltip(provider, mode=None))
        _set_tooltip_if_supported(status, _build_welcome_api_key_tooltip(provider, mode=None))

    def _bind_control_simple_handler(
        self,
        control,
        method_name: str,
        handler_attr: str,
    ) -> None:
        if _SimpleHandler is None:
            return

        import objc  # type: ignore[import-not-found]

        handler = _SimpleHandler.alloc().initWithController_method_(self, method_name)
        control.setTarget_(handler)
        control.setAction_(objc.selector(handler.performAction_, signature=b"v@:@"))
        setattr(self, handler_attr, handler)

    def _create_settings_popup(
        self,
        x: int,
        y: int,
        width: int,
        options,
        *,
        selected_title: str | None = None,
        include_custom_selection: bool = False,
    ):
        from AppKit import NSFont, NSMakeRect, NSPopUpButton  # type: ignore[import-not-found]

        popup = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(x, y, width, 22))
        popup.setFont_(NSFont.systemFontOfSize_(11))
        known_options = tuple(options)
        for option in known_options:
            popup.addItemWithTitle_(option)
        if include_custom_selection and selected_title and selected_title not in known_options:
            popup.addItemWithTitle_(selected_title)
        if selected_title in known_options or (include_custom_selection and selected_title):
            popup.selectItemWithTitle_(selected_title)
        return popup

    def _build_mode_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
    ) -> None:
        self._add_setting_label(base_x, y, "Mode:", parent_view)
        current_mode = (
            self._get_cached_env_setting("PULSESCRIBE_MODE")
            or self.config.get("mode")
            or "deepgram"
        )
        mode_popup = self._create_settings_popup(
            control_x,
            y,
            control_width,
            MODE_OPTIONS,
            selected_title=current_mode if current_mode in MODE_OPTIONS else None,
        )
        self._bind_control_simple_handler(
            mode_popup,
            "_update_all_visibility",
            "_mode_changed_handler",
        )
        self._mode_popup = mode_popup
        parent_view.addSubview_(mode_popup)

    def _build_local_backend_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
    ) -> None:
        self._local_backend_label = self._add_setting_label(
            base_x, y, "Local Backend:", parent_view
        )
        current_backend = normalize_local_backend(
            self._get_cached_env_setting("PULSESCRIBE_LOCAL_BACKEND")
        )
        if current_backend not in LOCAL_BACKEND_OPTIONS:
            current_backend = "auto"
        local_backend_popup = self._create_settings_popup(
            control_x,
            y,
            control_width,
            LOCAL_BACKEND_OPTIONS,
            selected_title=current_backend,
        )
        self._local_backend_popup = local_backend_popup
        parent_view.addSubview_(local_backend_popup)
        self._bind_control_simple_handler(
            local_backend_popup,
            "_update_local_settings_visibility",
            "_backend_changed_handler",
        )

    def _build_local_model_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
    ) -> None:
        self._local_model_label = self._add_setting_label(
            base_x, y, "Local Model:", parent_view
        )
        current_local_model = (
            self._get_cached_env_setting("PULSESCRIBE_LOCAL_MODEL") or "default"
        )
        local_model_popup = self._create_settings_popup(
            control_x,
            y,
            control_width,
            LOCAL_MODEL_OPTIONS,
            selected_title=current_local_model,
            include_custom_selection=True,
        )
        self._bind_control_simple_handler(
            local_model_popup,
            "_refresh_footer_settings_hint",
            "_local_model_changed_handler",
        )
        self._local_model_popup = local_model_popup
        parent_view.addSubview_(local_model_popup)

    def _build_language_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
    ) -> None:
        self._add_setting_label(base_x, y, "Language:", parent_view)
        current_lang = (
            self._get_cached_env_setting("PULSESCRIBE_LANGUAGE")
            or self.config.get("language")
            or "auto"
        )
        lang_popup = self._create_settings_popup(
            control_x,
            y,
            control_width,
            LANGUAGE_OPTIONS,
            selected_title=current_lang if current_lang in LANGUAGE_OPTIONS else None,
        )
        self._bind_control_simple_handler(
            lang_popup,
            "_refresh_footer_settings_hint",
            "_lang_changed_handler",
        )
        self._lang_popup = lang_popup
        parent_view.addSubview_(lang_popup)

    def _build_streaming_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
    ) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSButton,
            NSButtonTypeSwitch,
            NSFont,
            NSMakeRect,
        )

        self._streaming_label = self._add_setting_label(
            base_x, y, "Streaming:", parent_view
        )
        streaming_checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(control_x, y, control_width, 22)
        )
        streaming_checkbox.setButtonType_(NSButtonTypeSwitch)
        streaming_checkbox.setTitle_("WebSocket (low latency)")
        streaming_checkbox.setFont_(NSFont.systemFontOfSize_(11))
        streaming_enabled = _is_env_enabled_default_true("PULSESCRIBE_STREAMING")
        streaming_checkbox.setState_(1 if streaming_enabled else 0)
        self._bind_control_simple_handler(
            streaming_checkbox,
            "_refresh_footer_settings_hint",
            "_streaming_changed_handler",
        )
        self._streaming_checkbox = streaming_checkbox
        parent_view.addSubview_(streaming_checkbox)

    def _build_settings_card(self, y: int, parent_view=None) -> int:
        """Erstellt Provider-Einstellungen (Mode/Local/Language)."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        parent_view = parent_view or self._content_view

        card_height = 200  # Increased for Streaming toggle
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        label_width = 110
        control_x = base_x + label_width + 8
        control_width = card_width - 2 * CARD_PADDING - label_width - 8

        # Section-Titel
        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 28, 200, 18)
        )
        title.setStringValue_("🧩 Providers")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        row_height = 28
        current_y = card_y + card_height - 58

        self._build_mode_setting_row(
            base_x, current_y, control_x, control_width, parent_view
        )
        current_y -= row_height

        self._build_local_backend_setting_row(
            base_x, current_y, control_x, control_width, parent_view
        )
        current_y -= row_height

        self._build_local_model_setting_row(
            base_x, current_y, control_x, control_width, parent_view
        )
        current_y -= row_height

        self._build_language_setting_row(
            base_x, current_y, control_x, control_width, parent_view
        )
        current_y -= row_height

        self._build_streaming_setting_row(
            base_x, current_y, control_x, control_width, parent_view
        )

        self._update_all_visibility()

        return card_y - CARD_SPACING

    def _build_advanced_local_header(
        self,
        base_x: int,
        card_y: int,
        card_height: int,
        control_width: int,
        parent_view,
    ) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 28, 320, 18)
        )
        title.setStringValue_("⚙️ Advanced (Local)")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 46, control_width, 14)
        )
        desc.setStringValue_("Optional expert overrides for local dictation.")
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 64, control_width, 14)
        )
        status.setStringValue_("")
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_(10))
        status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55))
        parent_view.addSubview_(status)
        self._advanced_local_status_label = status

    def _advanced_popup_value_from_env(
        self,
        env_key: str,
        options,
        *,
        default: str = "auto",
    ) -> str:
        value = (self._get_cached_env_setting(env_key) or default).strip().lower()
        return value if value in options else default

    def _build_popup_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
        *,
        label_text: str,
        options,
        selected_title: str,
        popup_attr: str,
    ):
        label = self._add_setting_label(base_x, y, label_text, parent_view)
        popup = self._create_settings_popup(
            control_x,
            y,
            control_width,
            options,
            selected_title=selected_title,
        )
        setattr(self, popup_attr, popup)
        parent_view.addSubview_(popup)
        return label, popup

    def _build_text_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
        *,
        label_text: str,
        placeholder: str,
        env_key: str,
        field_attr: str,
    ):
        from AppKit import NSFont, NSMakeRect, NSTextField  # type: ignore[import-not-found]

        label = self._add_setting_label(base_x, y, label_text, parent_view)
        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, y, control_width, 22)
        )
        field.setFont_(NSFont.systemFontOfSize_(11))
        field.setPlaceholderString_(placeholder)
        field.setStringValue_(self._get_cached_env_setting(env_key) or "")
        setattr(self, field_attr, field)
        parent_view.addSubview_(field)
        return label, field

    def _build_preset_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
    ):
        label, popup = self._build_popup_setting_row(
            base_x,
            y,
            control_x,
            control_width,
            parent_view,
            label_text="Preset:",
            options=LOCAL_PRESET_OPTIONS,
            selected_title="(none)",
            popup_attr="_local_preset_popup",
        )
        self._bind_control_simple_handler(
            popup,
            "_apply_selected_local_preset",
            "_local_preset_changed_handler",
        )
        return label, popup

    def _build_bool_override_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
        *,
        label_text: str,
        popup_attr: str,
        env_key: str,
        legacy_env_key: str | None = None,
    ):
        selected_title = _bool_override_from_env(env_key, legacy_env_key)
        return self._build_popup_setting_row(
            base_x,
            y,
            control_x,
            control_width,
            parent_view,
            label_text=label_text,
            options=BOOL_OVERRIDE_OPTIONS,
            selected_title=selected_title,
            popup_attr=popup_attr,
        )

    def _build_advanced_general_rows(
        self,
        base_x: int,
        current_y: int,
        control_x: int,
        control_width: int,
        parent_view,
        row_height: int,
    ) -> tuple[tuple, int]:
        views = []
        row_builders = (
            lambda y: self._build_preset_setting_row(
                base_x, y, control_x, control_width, parent_view
            ),
            lambda y: self._build_popup_setting_row(
                base_x,
                y,
                control_x,
                control_width,
                parent_view,
                label_text="Device:",
                options=DEVICE_OPTIONS,
                selected_title=self._advanced_popup_value_from_env(
                    "PULSESCRIBE_DEVICE", DEVICE_OPTIONS
                ),
                popup_attr="_device_popup",
            ),
            lambda y: self._build_popup_setting_row(
                base_x,
                y,
                control_x,
                control_width,
                parent_view,
                label_text="Warmup:",
                options=WARMUP_OPTIONS,
                selected_title=self._advanced_popup_value_from_env(
                    "PULSESCRIBE_LOCAL_WARMUP", WARMUP_OPTIONS
                ),
                popup_attr="_warmup_popup",
            ),
            lambda y: self._build_bool_override_setting_row(
                base_x,
                y,
                control_x,
                control_width,
                parent_view,
                label_text="Fast:",
                popup_attr="_local_fast_popup",
                env_key="PULSESCRIBE_LOCAL_FAST",
            ),
            lambda y: self._build_bool_override_setting_row(
                base_x,
                y,
                control_x,
                control_width,
                parent_view,
                label_text="FP16:",
                popup_attr="_fp16_popup",
                env_key=LOCAL_FP16_ENV_KEY,
                legacy_env_key=LEGACY_LOCAL_FP16_ENV_KEY,
            ),
            lambda y: self._build_text_setting_row(
                base_x,
                y,
                control_x,
                control_width,
                parent_view,
                label_text="Beam size:",
                placeholder="default",
                env_key="PULSESCRIBE_LOCAL_BEAM_SIZE",
                field_attr="_beam_size_field",
            ),
            lambda y: self._build_text_setting_row(
                base_x,
                y,
                control_x,
                control_width,
                parent_view,
                label_text="Best of:",
                placeholder="default",
                env_key="PULSESCRIBE_LOCAL_BEST_OF",
                field_attr="_best_of_field",
            ),
            lambda y: self._build_text_setting_row(
                base_x,
                y,
                control_x,
                control_width,
                parent_view,
                label_text="Temperature:",
                placeholder="e.g. 0.0 or 0.0,0.2,0.4",
                env_key="PULSESCRIBE_LOCAL_TEMPERATURE",
                field_attr="_temperature_field",
            ),
        )
        for build_row in row_builders:
            views.extend(build_row(current_y))
            current_y -= row_height
        return tuple(views), current_y

    def _build_faster_whisper_rows(
        self,
        base_x: int,
        current_y: int,
        control_x: int,
        control_width: int,
        parent_view,
        row_height: int,
    ) -> int:
        field_rows = (
            (
                "_compute_type_label",
                "Compute type:",
                "default (e.g. int8, int8_float16)",
                "PULSESCRIBE_LOCAL_COMPUTE_TYPE",
                "_compute_type_field",
            ),
            (
                "_cpu_threads_label",
                "CPU threads:",
                "0 = auto",
                "PULSESCRIBE_LOCAL_CPU_THREADS",
                "_cpu_threads_field",
            ),
            (
                "_num_workers_label",
                "Workers:",
                "1",
                "PULSESCRIBE_LOCAL_NUM_WORKERS",
                "_num_workers_field",
            ),
        )
        for label_attr, label_text, placeholder, env_key, field_attr in field_rows:
            label, _field = self._build_text_setting_row(
                base_x,
                current_y,
                control_x,
                control_width,
                parent_view,
                label_text=label_text,
                placeholder=placeholder,
                env_key=env_key,
                field_attr=field_attr,
            )
            setattr(self, label_attr, label)
            current_y -= row_height

        popup_rows = (
            (
                "_without_timestamps_label",
                "No timestamps:",
                "_without_timestamps_popup",
                "PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS",
            ),
            (
                "_vad_filter_label",
                "VAD filter:",
                "_vad_filter_popup",
                "PULSESCRIBE_LOCAL_VAD_FILTER",
            ),
        )
        for label_attr, label_text, popup_attr, env_key in popup_rows:
            label, _popup = self._build_bool_override_setting_row(
                base_x,
                current_y,
                control_x,
                control_width,
                parent_view,
                label_text=label_text,
                popup_attr=popup_attr,
                env_key=env_key,
            )
            setattr(self, label_attr, label)
            current_y -= row_height
        return current_y

    def _build_lightning_header(
        self,
        base_x: int,
        current_y: int,
        control_width: int,
        label_width: int,
        parent_view,
    ) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSMakeRect,
            NSTextField,
        )

        lightning_header = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, current_y, control_width + label_width, 14)
        )
        lightning_header.setStringValue_("⚡ Lightning Whisper MLX")
        lightning_header.setBezeled_(False)
        lightning_header.setDrawsBackground_(False)
        lightning_header.setEditable_(False)
        lightning_header.setSelectable_(False)
        lightning_header.setFont_(
            NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium)
        )
        lightning_header.setTextColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6)
        )
        self._lightning_header = lightning_header
        parent_view.addSubview_(lightning_header)

    def _lightning_batch_value_from_env(self) -> int:
        current_batch = self._get_cached_env_setting("PULSESCRIBE_LIGHTNING_BATCH_SIZE")
        try:
            return int(current_batch) if current_batch else 12
        except ValueError:
            return 12

    def _build_lightning_batch_row(
        self,
        base_x: int,
        current_y: int,
        control_x: int,
        control_width: int,
        parent_view,
    ) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSMakeRect,
            NSSlider,
            NSTextField,
        )
        import objc  # type: ignore[import-not-found]

        self._lightning_batch_label = self._add_setting_label(
            base_x, current_y, "Batch size:", parent_view
        )
        batch_slider = NSSlider.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width - 40, 22)
        )
        batch_slider.setMinValue_(4)
        batch_slider.setMaxValue_(24)
        batch_slider.setIntValue_(self._lightning_batch_value_from_env())
        batch_slider.setNumberOfTickMarks_(6)
        batch_slider.setAllowsTickMarkValuesOnly_(True)
        self._lightning_batch_slider = batch_slider
        parent_view.addSubview_(batch_slider)

        batch_value_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x + control_width - 35, current_y, 35, 22)
        )
        batch_value_label.setStringValue_(str(int(batch_slider.intValue())))
        batch_value_label.setBezeled_(False)
        batch_value_label.setDrawsBackground_(False)
        batch_value_label.setEditable_(False)
        batch_value_label.setSelectable_(False)
        batch_value_label.setFont_(NSFont.systemFontOfSize_(11))
        batch_value_label.setTextColor_(NSColor.whiteColor())
        self._lightning_batch_value_label = batch_value_label
        parent_view.addSubview_(batch_value_label)

        batch_handler = _SliderHandler.alloc().initWithLabel_(batch_value_label)
        batch_slider.setTarget_(batch_handler)
        batch_slider.setAction_(
            objc.selector(batch_handler.sliderChanged_, signature=b"v@:@")
        )
        self._lightning_batch_handler = batch_handler

    def _lightning_quant_index_from_env(self) -> int:
        current_quant = (
            (self._get_cached_env_setting("PULSESCRIBE_LIGHTNING_QUANT") or "")
            .strip()
            .lower()
        )
        if current_quant == "4bit":
            return 2
        if current_quant == "8bit":
            return 1
        return 0

    def _build_lightning_quant_row(
        self,
        base_x: int,
        current_y: int,
        control_x: int,
        control_width: int,
        parent_view,
    ) -> None:
        self._lightning_quant_label = self._add_setting_label(
            base_x, current_y, "Quantization:", parent_view
        )
        quant_popup = self._create_settings_popup(
            control_x,
            current_y,
            control_width,
            ("none (best quality)", "8bit", "4bit (smallest memory)"),
        )
        quant_popup.selectItemAtIndex_(self._lightning_quant_index_from_env())
        self._lightning_quant_popup = quant_popup
        parent_view.addSubview_(quant_popup)

    def _build_lightning_rows(
        self,
        base_x: int,
        current_y: int,
        control_x: int,
        control_width: int,
        label_width: int,
        parent_view,
        row_height: int,
    ) -> int:
        self._build_lightning_header(
            base_x, current_y, control_width, label_width, parent_view
        )
        current_y -= row_height
        self._build_lightning_batch_row(
            base_x, current_y, control_x, control_width, parent_view
        )
        current_y -= row_height
        self._build_lightning_quant_row(
            base_x, current_y, control_x, control_width, parent_view
        )
        return current_y

    def _build_advanced_local_card(self, y: int, parent_view=None) -> int:
        """Erweiterte Local-Performance Settings (macOS-tuned)."""
        parent_view = parent_view or self._content_view

        card_height = 550  # Increased for Lightning settings
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        label_width = 110
        control_x = base_x + label_width + 8
        control_width = card_width - 2 * CARD_PADDING - label_width - 8

        self._build_advanced_local_header(
            base_x, card_y, card_height, control_width, parent_view
        )

        row_height = 28
        current_y = card_y + card_height - 96

        self._advanced_general_views, current_y = self._build_advanced_general_rows(
            base_x,
            current_y,
            control_x,
            control_width,
            parent_view,
            row_height,
        )

        current_y = self._build_faster_whisper_rows(
            base_x,
            current_y,
            control_x,
            control_width,
            parent_view,
            row_height,
        )
        self._build_lightning_rows(
            base_x,
            current_y,
            control_x,
            control_width,
            label_width,
            parent_view,
            row_height,
        )

        # Set initial visibility based on current mode/backend
        self._update_all_visibility()

        return card_y - CARD_SPACING

    def _get_current_refine_settings_state(self) -> tuple[bool, str, str]:
        refine_checkbox = getattr(self, "_refine_checkbox", None)
        provider_popup = getattr(self, "_provider_popup", None)
        model_field = getattr(self, "_model_field", None)
        provider = provider_popup.titleOfSelectedItem() if provider_popup else "groq"
        model = model_field.stringValue().strip() if model_field else ""
        return (
            bool(refine_checkbox.state() == 1) if refine_checkbox else False,
            normalize_refine_provider(provider),
            model,
        )

    def _get_current_display_settings_state(self) -> tuple[bool, bool, bool, bool]:
        overlay = getattr(self, "_overlay_checkbox", None)
        rtf = getattr(self, "_rtf_checkbox", None)
        clipboard = getattr(self, "_clipboard_restore_checkbox", None)
        dock = getattr(self, "_dock_icon_checkbox", None)
        return (
            bool(overlay.state() == 1) if overlay else True,
            bool(rtf.state() == 1) if rtf else False,
            bool(clipboard.state() == 1) if clipboard else False,
            bool(dock.state() == 1) if dock else True,
        )

    def _get_current_dock_icon_enabled(self) -> bool:
        checkbox = getattr(self, "_dock_icon_checkbox", None)
        if checkbox is not None:
            try:
                return bool(checkbox.state() == 1)
            except Exception:
                pass
        cache = getattr(self, "_env_settings_cache", None) or {}
        value = cache.get("PULSESCRIBE_DOCK_ICON")
        if value is None:
            return True
        try:
            return bool(parse_bool(value))
        except Exception:
            return True

    def _get_current_settings_signature(self) -> tuple:
        cache = getattr(self, "_env_settings_cache", None) or {}
        config = getattr(self, "config", None) or {}
        api_keys = tuple(
            (
                env_key,
                (
                    getattr(self, f"_{provider}_field", None).stringValue().strip()
                    if getattr(self, f"_{provider}_field", None) is not None
                    else str(cache.get(env_key) or os.getenv(env_key) or "").strip()
                ),
            )
            for provider, _label, env_key in API_KEY_PROVIDERS
        )
        mode_popup = getattr(self, "_mode_popup", None)
        lang_popup = getattr(self, "_lang_popup", None)
        backend_popup = getattr(self, "_local_backend_popup", None)
        model_popup = getattr(self, "_local_model_popup", None)
        streaming_checkbox = getattr(self, "_streaming_checkbox", None)
        refine_checkbox = getattr(self, "_refine_checkbox", None)
        provider_popup = getattr(self, "_provider_popup", None)
        refine_model_field = getattr(self, "_model_field", None)
        overlay_checkbox = getattr(self, "_overlay_checkbox", None)
        rtf_checkbox = getattr(self, "_rtf_checkbox", None)
        clipboard_checkbox = getattr(self, "_clipboard_restore_checkbox", None)
        dock_checkbox = getattr(self, "_dock_icon_checkbox", None)

        mode = (
            mode_popup.titleOfSelectedItem()
            if mode_popup is not None
            else (cache.get("PULSESCRIBE_MODE") or config.get("mode") or "deepgram")
        )
        lang = (
            lang_popup.titleOfSelectedItem()
            if lang_popup is not None
            else (cache.get("PULSESCRIBE_LANGUAGE") or config.get("language") or "auto")
        )
        backend = (
            backend_popup.titleOfSelectedItem()
            if backend_popup is not None
            else normalize_local_backend(cache.get("PULSESCRIBE_LOCAL_BACKEND"))
        )
        local_model = (
            model_popup.titleOfSelectedItem()
            if model_popup is not None
            else (cache.get("PULSESCRIBE_LOCAL_MODEL") or "default")
        )
        if streaming_checkbox is not None:
            streaming = bool(streaming_checkbox.state() == 1)
        else:
            streaming_raw = cache.get("PULSESCRIBE_STREAMING")
            streaming = (
                bool(parse_bool(streaming_raw)) if streaming_raw is not None else True
            )

        if refine_checkbox is not None:
            refine_enabled = bool(refine_checkbox.state() == 1)
        else:
            refine_raw = cache.get("PULSESCRIBE_REFINE")
            refine_enabled = (
                bool(parse_bool(refine_raw))
                if refine_raw is not None
                else bool(config.get("refine", False))
            )
        refine_provider = normalize_refine_provider(
            provider_popup.titleOfSelectedItem()
            if provider_popup is not None
            else (
                cache.get("PULSESCRIBE_REFINE_PROVIDER")
                or config.get("refine_provider")
                or "groq"
            )
        )
        refine_model = (
            refine_model_field.stringValue().strip()
            if refine_model_field is not None
            else (
                cache.get("PULSESCRIBE_REFINE_MODEL")
                or config.get("refine_model")
                or "openai/gpt-oss-120b"
            )
        )

        overlay_enabled = (
            bool(overlay_checkbox.state() == 1)
            if overlay_checkbox is not None
            else (
                bool(parse_bool(cache.get("PULSESCRIBE_OVERLAY")))
                if cache.get("PULSESCRIBE_OVERLAY") is not None
                else True
            )
        )
        rtf_enabled = (
            bool(rtf_checkbox.state() == 1)
            if rtf_checkbox is not None
            else bool(parse_bool(cache.get("PULSESCRIBE_SHOW_RTF") or "false"))
        )
        clipboard_enabled = (
            bool(clipboard_checkbox.state() == 1)
            if clipboard_checkbox is not None
            else bool(
                parse_bool(cache.get("PULSESCRIBE_CLIPBOARD_RESTORE") or "false")
            )
        )
        dock_enabled = (
            bool(dock_checkbox.state() == 1)
            if dock_checkbox is not None
            else (
                bool(parse_bool(cache.get("PULSESCRIBE_DOCK_ICON")))
                if cache.get("PULSESCRIBE_DOCK_ICON") is not None
                else True
            )
        )

        return (
            mode,
            lang,
            backend,
            local_model,
            streaming,
            refine_enabled,
            refine_provider,
            refine_model,
            overlay_enabled,
            rtf_enabled,
            clipboard_enabled,
            dock_enabled,
            api_keys,
        )

    def _refresh_footer_settings_hint(self) -> None:
        saved_signature = getattr(self, "_saved_settings_signature", None)
        relaunch_required = (
            getattr(self, "_saved_dock_icon_enabled", None)
            is not None
            and self._get_current_dock_icon_enabled()
            != getattr(self, "_saved_dock_icon_enabled", None)
        )
        if saved_signature is None or self._get_current_settings_signature() == saved_signature:
            text, color = build_settings_loaded_feedback()
        else:
            text, color = build_unsaved_settings_feedback(
                relaunch_required=relaunch_required
            )
        self._set_footer_status(text, color)

    def _update_refine_model_affordances(self) -> None:
        _enabled, provider_key, model_text = self._get_current_refine_settings_state()
        provider_label = get_refine_provider_label(provider_key)
        default_model = get_refine_provider_default_model(provider_key)
        field = getattr(self, "_model_field", None)
        popup = getattr(self, "_provider_popup", None)
        if popup is not None:
            _set_tooltip_if_supported(
                popup,
                "Choose which provider should clean up transcript text after transcription. The matching API key is managed on the Providers tab.",
            )
        if field is not None:
            try:
                field.setPlaceholderString_(f"Optional — default: {default_model}")
            except Exception:
                pass
            _set_tooltip_if_supported(
                field,
                f"Leave this empty to use {provider_label}'s default refine model ({default_model}). Enter a custom model only if you want to override it.",
            )
        _apply_status_text(
            getattr(self, "_refine_model_help_label", None),
            build_refine_model_guidance(provider_key, model_text),
            "text_secondary",
        )

    def _refresh_refine_settings_feedback(self) -> None:
        self._update_refine_model_affordances()
        enabled, provider_key, model_text = self._get_current_refine_settings_state()
        text, color = build_refine_settings_feedback(
            refine_enabled=enabled,
            provider=provider_key,
            model=model_text,
            saved_state=getattr(self, "_saved_refine_settings_state", None),
        )
        _apply_status_text(getattr(self, "_refine_status_label", None), text, color)

    def _refresh_display_settings_feedback(self) -> None:
        overlay_enabled, rtf_enabled, clipboard_restore_enabled, dock_icon_enabled = (
            self._get_current_display_settings_state()
        )
        text, color = build_display_settings_feedback(
            overlay_enabled=overlay_enabled,
            rtf_enabled=rtf_enabled,
            clipboard_restore_enabled=clipboard_restore_enabled,
            dock_icon_enabled=dock_icon_enabled,
            saved_state=getattr(self, "_saved_display_settings_state", None),
        )
        _apply_status_text(getattr(self, "_display_status_label", None), text, color)

    def _refresh_secondary_settings_feedback(self) -> None:
        self._refresh_refine_settings_feedback()
        self._refresh_display_settings_feedback()
        self._refresh_footer_settings_hint()

    def _build_refine_card_header(
        self,
        base_x: int,
        card_y: int,
        card_height: int,
        content_width: int,
        parent_view,
    ) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 28, 220, 18)
        )
        title.setStringValue_("✨ Refine & Output")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 56, content_width, 28)
        )
        desc.setStringValue_(
            "Choose whether PulseScribe cleans up transcript text after dictation, what it shows on screen, and whether your clipboard gets restored after pasting."
        )
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        try:
            desc.setLineBreakMode_(0)
            desc.setUsesSingleLineMode_(False)
        except Exception:
            pass
        parent_view.addSubview_(desc)

    def _add_refine_section_label(
        self,
        text: str,
        base_x: int,
        y_pos: int,
        content_width: int,
        parent_view,
    ):
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, y_pos, content_width, 14)
        )
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(NSFont.systemFontOfSize_weight_(10, NSFontWeightSemibold))
        label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55))
        parent_view.addSubview_(label)
        return label

    def _build_switch_setting_row(
        self,
        base_x: int,
        y: int,
        control_x: int,
        control_width: int,
        parent_view,
        *,
        label_text: str,
        title: str,
        enabled: bool,
        tooltip: str,
        attr_name: str,
    ):
        from AppKit import (  # type: ignore[import-not-found]
            NSButton,
            NSButtonTypeSwitch,
            NSFont,
            NSMakeRect,
        )

        self._add_setting_label(base_x, y, label_text, parent_view)
        checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(control_x, y, control_width, 22)
        )
        checkbox.setButtonType_(NSButtonTypeSwitch)
        checkbox.setTitle_(title)
        checkbox.setFont_(NSFont.systemFontOfSize_(11))
        checkbox.setState_(1 if enabled else 0)
        _set_tooltip_if_supported(checkbox, tooltip)
        setattr(self, attr_name, checkbox)
        parent_view.addSubview_(checkbox)
        return checkbox

    def _refine_enabled_from_settings(self) -> bool:
        refine_enabled = self._get_cached_env_setting("PULSESCRIBE_REFINE")
        if refine_enabled is None:
            return bool(self.config.get("refine", False))
        return bool(parse_bool(refine_enabled))

    def _current_refine_provider_selection(self) -> str | None:
        current_provider = (
            self._get_cached_env_setting("PULSESCRIBE_REFINE_PROVIDER")
            or self.config.get("refine_provider")
            or "groq"
        )
        current_provider = normalize_refine_provider(current_provider)
        return current_provider if current_provider in REFINE_PROVIDER_OPTIONS else None

    def _current_refine_model_text(self) -> str:
        return (
            self._get_cached_env_setting("PULSESCRIBE_REFINE_MODEL")
            or self.config.get("refine_model")
            or "openai/gpt-oss-120b"
        )

    def _build_wrapping_status_label(
        self,
        base_x: int,
        y: int,
        width: int,
        height: int,
        parent_view,
    ):
        from AppKit import NSFont, NSMakeRect, NSTextField  # type: ignore[import-not-found]

        label = NSTextField.alloc().initWithFrame_(NSMakeRect(base_x, y, width, height))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(NSFont.systemFontOfSize_(10))
        try:
            label.setLineBreakMode_(0)
            label.setUsesSingleLineMode_(False)
        except Exception:
            pass
        parent_view.addSubview_(label)
        return label

    def _build_refine_cleanup_controls(
        self,
        base_x: int,
        current_y: int,
        control_x: int,
        control_width: int,
        content_width: int,
        parent_view,
        row_height: int,
    ) -> tuple[list, int]:
        from AppKit import NSFont, NSMakeRect, NSTextField  # type: ignore[import-not-found]

        self._add_refine_section_label(
            "Transcript cleanup", base_x, current_y, content_width, parent_view
        )
        current_y -= 20

        refine_checkbox = self._build_switch_setting_row(
            base_x,
            current_y,
            control_x,
            control_width,
            parent_view,
            label_text="Refine:",
            title="Clean up transcript text with an LLM",
            enabled=self._refine_enabled_from_settings(),
            tooltip="Use an LLM to improve punctuation, formatting, and spoken commands after transcription.",
            attr_name="_refine_checkbox",
        )
        current_y -= row_height

        self._add_setting_label(base_x, current_y, "Provider:", parent_view)
        provider_popup = self._create_settings_popup(
            control_x,
            current_y,
            control_width,
            REFINE_PROVIDER_OPTIONS,
            selected_title=self._current_refine_provider_selection(),
        )
        self._provider_popup = provider_popup
        parent_view.addSubview_(provider_popup)
        current_y -= row_height

        self._add_setting_label(base_x, current_y, "Model:", parent_view)
        model_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        model_field.setFont_(NSFont.systemFontOfSize_(11))
        model_field.setStringValue_(self._current_refine_model_text())
        self._model_field = model_field
        parent_view.addSubview_(model_field)
        current_y -= 24

        self._refine_model_help_label = self._build_wrapping_status_label(
            base_x, current_y, content_width, 28, parent_view
        )
        current_y -= 34

        self._refine_status_label = self._build_wrapping_status_label(
            base_x, current_y, content_width, 30, parent_view
        )
        current_y -= 38
        return [refine_checkbox, provider_popup, model_field], current_y

    def _env_enabled_default_false(self, env_key: str) -> bool:
        value = self._get_cached_env_setting(env_key)
        return bool(parse_bool(value)) if value else False

    def _rtf_enabled_from_settings(self) -> bool:
        value = self._get_cached_env_setting("PULSESCRIBE_SHOW_RTF")
        return value is not None and value.lower() in ("true", "1", "yes", "on")

    def _build_visual_output_controls(
        self,
        base_x: int,
        current_y: int,
        control_x: int,
        control_width: int,
        content_width: int,
        parent_view,
        row_height: int,
    ) -> tuple[list, int]:
        self._add_refine_section_label(
            "Visual & paste behavior", base_x, current_y, content_width, parent_view
        )
        current_y -= 20

        controls = [
            self._build_switch_setting_row(
                base_x,
                current_y,
                control_x,
                control_width,
                parent_view,
                label_text="Overlay:",
                title="Show recording overlay",
                enabled=_is_env_enabled_default_true("PULSESCRIBE_OVERLAY"),
                tooltip="Show a floating overlay while recording so you can see dictation status.",
                attr_name="_overlay_checkbox",
            )
        ]
        current_y -= row_height

        controls.append(
            self._build_switch_setting_row(
                base_x,
                current_y,
                control_x,
                control_width,
                parent_view,
                label_text="Clipboard:",
                title="Restore clipboard after paste",
                enabled=self._env_enabled_default_false(
                    "PULSESCRIBE_CLIPBOARD_RESTORE"
                ),
                tooltip="Put your previous clipboard text back after auto-paste. Helpful if you frequently reuse clipboard contents.",
                attr_name="_clipboard_restore_checkbox",
            )
        )
        current_y -= row_height

        controls.append(
            self._build_switch_setting_row(
                base_x,
                current_y,
                control_x,
                control_width,
                parent_view,
                label_text="Dock Icon:",
                title="Show in the Dock",
                enabled=_is_env_enabled_default_true("PULSESCRIBE_DOCK_ICON"),
                tooltip="Show or hide the Dock icon. This change only appears after you relaunch PulseScribe.",
                attr_name="_dock_icon_checkbox",
            )
        )
        current_y -= row_height

        controls.append(
            self._build_switch_setting_row(
                base_x,
                current_y,
                control_x,
                control_width,
                parent_view,
                label_text="Performance:",
                title="Show transcription speed after each result",
                enabled=self._rtf_enabled_from_settings(),
                tooltip="Display the Real-Time Factor after each transcription. Useful for performance tuning; most people can leave this off.",
                attr_name="_rtf_checkbox",
            )
        )
        current_y -= 30

        self._display_status_label = self._build_wrapping_status_label(
            base_x, current_y, content_width, 34, parent_view
        )
        return controls, current_y

    def _wire_secondary_settings_controls(self, controls: tuple) -> None:
        if _SimpleHandler is None:
            self._secondary_settings_change_handler = None
            return

        import objc  # type: ignore[import-not-found]

        change_handler = _SimpleHandler.alloc().initWithController_method_(
            self, "_refresh_secondary_settings_feedback"
        )
        action = objc.selector(change_handler.performAction_, signature=b"v@:@")
        for control in controls:
            try:
                control.setTarget_(change_handler)
                control.setAction_(action)
            except Exception:
                continue
        model_field = getattr(self, "_model_field", None)
        try:
            model_field.setSendsActionOnEndEditing_(True)
        except Exception:
            pass
        self._secondary_settings_change_handler = change_handler

    def _build_refine_card(
        self, y: int, parent_view=None, tab_height: int | None = None
    ) -> int:
        """Erstellt Refine-Einstellungen."""
        parent_view = parent_view or self._content_view

        max_height = (tab_height - 2 * WELCOME_PADDING) if tab_height else 360
        card_height = min(352, max_height)
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        label_width = 110
        control_x = base_x + label_width + 8
        control_width = card_width - 2 * CARD_PADDING - label_width - 8
        content_width = card_width - 2 * CARD_PADDING

        self._build_refine_card_header(
            base_x, card_y, card_height, content_width, parent_view
        )

        row_height = 28
        current_y = card_y + card_height - 88

        refine_controls, current_y = self._build_refine_cleanup_controls(
            base_x,
            current_y,
            control_x,
            control_width,
            content_width,
            parent_view,
            row_height,
        )
        display_controls, _current_y = self._build_visual_output_controls(
            base_x,
            current_y,
            control_x,
            control_width,
            content_width,
            parent_view,
            row_height,
        )
        self._wire_secondary_settings_controls(
            tuple(refine_controls + display_controls)
        )

        self._saved_refine_settings_state = self._get_current_refine_settings_state()
        self._saved_display_settings_state = self._get_current_display_settings_state()
        self._refresh_secondary_settings_feedback()

        return card_y - CARD_SPACING

    def _build_prompts_card_header(
        self,
        base_x: int,
        card_y: int,
        card_height: int,
        content_width: int,
        parent_view,
    ) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 28, 260, 18)
        )
        title.setStringValue_("📝 Custom Prompts")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 46, content_width, 14)
        )
        desc.setStringValue_(
            "Fine-tune Refine prompts by context. Switch contexts freely, then use Save & Apply when you're ready."
        )
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

    def _build_prompts_context_controls(
        self,
        base_x: int,
        row_y: int,
        parent_view,
    ) -> tuple:
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSMakeRect,
            NSPopUpButton,
            NSTextField,
        )

        context_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, row_y, 60, 22)
        )
        context_label.setStringValue_("Context:")
        context_label.setBezeled_(False)
        context_label.setDrawsBackground_(False)
        context_label.setEditable_(False)
        context_label.setSelectable_(False)
        context_label.setFont_(NSFont.systemFontOfSize_(11))
        context_label.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(context_label)

        context_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(base_x + 65, row_y, 140, 22)
        )
        context_popup.setFont_(NSFont.systemFontOfSize_(11))
        for _context_key, label in PROMPT_EDITOR_CONTEXT_OPTIONS:
            context_popup.addItemWithTitle_(label)
        parent_view.addSubview_(context_popup)
        self._prompts_context_popup = context_popup

        reset_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x + 215, row_y, 100, 22)
        )
        reset_btn.setTitle_("Reset to Default")
        reset_btn.setBezelStyle_(NSBezelStyleRounded)
        reset_btn.setFont_(NSFont.systemFontOfSize_(10))
        _set_tooltip_if_supported(
            reset_btn,
            "Load the built-in text for the selected context. Save & Apply if you want to keep that change.",
        )
        parent_view.addSubview_(reset_btn)
        self._prompts_reset_btn = reset_btn
        return context_popup, reset_btn

    def _build_prompts_editor(
        self,
        base_x: int,
        scroll_y: int,
        content_width: int,
        scroll_height: int,
        parent_view,
    ):
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelBorder,
            NSColor,
            NSFont,
            NSMakeRect,
            NSScrollView,
            NSTextView,
        )

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(base_x, scroll_y, content_width, scroll_height)
        )
        scroll.setBorderType_(NSBezelBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        try:
            scroll.setDrawsBackground_(False)
        except Exception:
            pass

        text_view = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_width, scroll_height)
        )
        text_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11, 0.0))
        text_view.setTextColor_(NSColor.whiteColor())
        try:
            text_view.setDrawsBackground_(False)
        except Exception:
            pass
        text_view.setVerticallyResizable_(True)
        text_view.setHorizontallyResizable_(False)
        text_view.setAllowsUndo_(True)
        _set_tooltip_if_supported(
            text_view,
            "Edit the selected prompt context here. Draft changes stay in this window until you click Save & Apply.",
        )
        tc = text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)

        text_view.setString_(self._get_prompt_editor_text_for_context("default"))
        scroll.setDocumentView_(text_view)
        parent_view.addSubview_(scroll)
        self._prompts_text_view = text_view
        return text_view

    def _build_prompt_footer_label(
        self,
        base_x: int,
        y: int,
        content_width: int,
        parent_view,
        *,
        text: str = "",
        font_weight: float | None = None,
        color=None,
        height: int = 14,
    ):
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSMakeRect,
            NSTextField,
        )

        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, y, content_width, height)
        )
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        if font_weight is None:
            label.setFont_(NSFont.systemFontOfSize_(10))
        else:
            label.setFont_(NSFont.systemFontOfSize_weight_(10, font_weight))
        label.setTextColor_(color or NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(label)
        return label

    def _build_prompts_footer_labels(
        self,
        base_x: int,
        card_y: int,
        content_width: int,
        parent_view,
    ) -> None:
        from AppKit import NSColor, NSFontWeightMedium  # type: ignore[import-not-found]

        self._prompts_hint_label = self._build_prompt_footer_label(
            base_x,
            card_y + 54,
            content_width,
            parent_view,
            text=get_prompt_editor_context_description("default"),
            font_weight=NSFontWeightMedium,
            color=NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.5),
        )
        self._prompts_state_label = self._build_prompt_footer_label(
            base_x,
            card_y + 34,
            content_width,
            parent_view,
            color=NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6),
        )
        self._prompts_status_label = self._build_prompt_footer_label(
            base_x,
            card_y + 16,
            content_width,
            parent_view,
            color=_get_color(76, 175, 80, 0.9),
            height=16,
        )

    def _handle_prompt_context_change(self) -> None:
        old_ctx = self._prompts_current_context
        self._prompts_cache[old_ctx] = str(self._prompts_text_view.string())

        new_ctx = normalize_prompt_editor_context(
            str(self._prompts_context_popup.titleOfSelectedItem())
        )
        self._prompts_current_context = new_ctx
        _set_string_value_if_changed(
            self._prompts_hint_label,
            get_prompt_editor_context_description(new_ctx),
        )
        _set_string_value_if_changed(self._prompts_status_label, "")

        if new_ctx in self._prompts_cache:
            text = self._prompts_cache[new_ctx]
        else:
            text = self._get_prompt_editor_text_for_context(new_ctx)
        _set_text_view_string_if_changed(self._prompts_text_view, text)
        self._refresh_prompt_editor_feedback()

    def _handle_prompt_reset_to_default(self) -> None:
        ctx = normalize_prompt_editor_context(
            str(self._prompts_context_popup.titleOfSelectedItem())
        )
        text = self._get_prompt_editor_text_for_context(ctx, defaults=True)
        _set_text_view_string_if_changed(self._prompts_text_view, text)
        self._prompts_cache[ctx] = text
        _set_string_value_if_changed(
            self._prompts_status_label,
            f"Restored default {get_prompt_editor_context_label(ctx)}. {build_save_apply_change_hint()}",
        )
        self._refresh_prompt_editor_feedback()

    def _wire_prompt_editor_handlers(self, context_popup, reset_btn, text_view) -> None:
        self._bind_control_simple_handler(
            context_popup,
            "_handle_prompt_context_change",
            "_prompts_context_handler",
        )
        self._bind_control_simple_handler(
            reset_btn,
            "_handle_prompt_reset_to_default",
            "_prompts_reset_handler",
        )

        if _TextChangeHandler is not None:
            prompts_change_handler = _TextChangeHandler.alloc().initWithController_method_(
                self, "_on_prompt_editor_text_changed"
            )
            try:
                text_view.setDelegate_(prompts_change_handler)
            except Exception:
                prompts_change_handler = None
            self._prompts_text_change_handler = prompts_change_handler
        else:
            self._prompts_text_change_handler = None

    def _build_prompts_card(
        self, y: int, parent_view=None, tab_height: int | None = None
    ) -> int:
        """Erstellt Custom Prompts Editor."""
        parent_view = parent_view or self._content_view

        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        max_height = (tab_height - 2 * WELCOME_PADDING) if tab_height else 500
        card_height = min(500, max_height)
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        content_width = card_width - 2 * CARD_PADDING

        self._build_prompts_card_header(
            base_x, card_y, card_height, content_width, parent_view
        )
        context_popup, reset_btn = self._build_prompts_context_controls(
            base_x, card_y + card_height - 76, parent_view
        )
        text_view = self._build_prompts_editor(
            base_x,
            card_y + 80,
            content_width,
            card_height - 170,
            parent_view,
        )
        self._build_prompts_footer_labels(
            base_x, card_y, content_width, parent_view
        )

        # Cache für unsaved changes
        self._prompts_cache: dict[str, str] = {}
        self._prompts_current_context = "default"

        self._wire_prompt_editor_handlers(context_popup, reset_btn, text_view)

        self._refresh_prompt_editor_feedback()
        return card_y - CARD_SPACING

    def _build_vocabulary_card(
        self, y: int, parent_view=None, tab_height: int | None = None
    ) -> int:
        """Erstellt Vocabulary/Keywords Editor."""
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

        parent_view = parent_view or self._content_view

        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        max_height = (tab_height - 2 * WELCOME_PADDING) if tab_height else 420
        card_height = min(420, max_height)
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        content_width = card_width - 2 * CARD_PADDING

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 28, 260, 18)
        )
        title.setStringValue_("📚 Vocabulary / Keywords")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 46, content_width, 14)
        )
        desc.setStringValue_(
            "Add names, product terms, and jargon that PulseScribe should recognize more reliably. Changes are saved with Save & Apply and stay local to this device."
        )
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        scroll_y = card_y + 48
        scroll_height = card_height - 96
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(base_x, scroll_y, content_width, scroll_height)
        )
        scroll.setBorderType_(NSBezelBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        try:
            scroll.setDrawsBackground_(False)
        except Exception:
            pass

        text_view = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_width, scroll_height)
        )
        text_view.setFont_(NSFont.systemFontOfSize_(12))
        text_view.setTextColor_(NSColor.whiteColor())
        try:
            text_view.setDrawsBackground_(False)
        except Exception:
            pass
        text_view.setVerticallyResizable_(True)
        text_view.setHorizontallyResizable_(False)
        text_view.setAllowsUndo_(True)  # CMD+Z / CMD+Shift+Z
        _set_tooltip_if_supported(
            text_view,
            "Add one keyword or phrase per line. Commas also work. Duplicate entries are merged automatically when you save.",
        )
        tc = text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)

        keywords = self._get_loaded_vocabulary_keywords()
        text_view.setString_("\n".join(str(k) for k in keywords))
        scroll.setDocumentView_(text_view)
        parent_view.addSubview_(scroll)

        self._vocab_text_view = text_view

        warning = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 12, content_width, 28)
        )
        warning.setBezeled_(False)
        warning.setDrawsBackground_(False)
        warning.setEditable_(False)
        warning.setSelectable_(False)
        warning.setFont_(NSFont.systemFontOfSize_(10))
        try:
            warning.setLineBreakMode_(0)
            warning.setUsesSingleLineMode_(False)
        except Exception:
            pass
        parent_view.addSubview_(warning)
        self._vocab_warning_label = warning

        if _TextChangeHandler is not None:
            vocab_change_handler = _TextChangeHandler.alloc().initWithController_method_(
                self, "_update_vocabulary_warning"
            )
            try:
                text_view.setDelegate_(vocab_change_handler)
            except Exception:
                vocab_change_handler = None
            self._vocab_text_change_handler = vocab_change_handler
        else:
            self._vocab_text_change_handler = None

        self._update_vocabulary_warning()

        return card_y - CARD_SPACING

    def _get_prompt_defaults_data(self) -> dict:
        cached = getattr(self, "_prompts_defaults_data", None)
        if cached is None:
            from utils.custom_prompts import get_defaults

            cached = get_defaults()
            self._prompts_defaults_data = cached
        return cached

    def _get_loaded_prompts_data(self, *, force: bool = False) -> dict:
        cached = getattr(self, "_prompts_loaded_data", None)
        if force or cached is None:
            from utils.custom_prompts import load_custom_prompts

            cached = load_custom_prompts()
            self._prompts_loaded_data = cached
        return cached

    def _prompt_editor_context_key(self, context: str | None) -> str:
        return normalize_prompt_editor_context(context)

    def _get_prompt_text_cache(
        self,
        data: dict,
        *,
        defaults: bool = False,
    ) -> dict[str, str]:
        cache_attr = "_prompt_defaults_text_cache" if defaults else "_prompt_text_cache"
        source_attr = (
            "_prompt_defaults_text_cache_source"
            if defaults
            else "_prompt_text_cache_source"
        )
        cached_source = getattr(self, source_attr, None)
        cached_texts = getattr(self, cache_attr, None)
        if data is not cached_source or not isinstance(cached_texts, dict):
            cached_texts = {}
            setattr(self, cache_attr, cached_texts)
            setattr(self, source_attr, data)
        return cached_texts

    def _get_prompt_editor_text_for_context(
        self,
        context: str,
        *,
        defaults: bool = False,
    ) -> str:
        from utils.custom_prompts import get_prompt_editor_text

        prompt_data = (
            self._get_prompt_defaults_data()
            if defaults
            else self._get_loaded_prompts_data()
        )
        return get_prompt_editor_text(
            self._prompt_editor_context_key(context),
            data=prompt_data,
            text_cache=self._get_prompt_text_cache(prompt_data, defaults=defaults),
        )

    def _current_prompt_editor_text(self) -> str:
        text_view = getattr(self, "_prompts_text_view", None)
        if text_view is None:
            return ""
        try:
            return str(text_view.string() or "")
        except Exception:
            return ""

    def _get_saved_prompt_editor_text_for_context(self, context: str) -> str:
        return self._get_prompt_editor_text_for_context(
            self._prompt_editor_context_key(context)
        )

    def _refresh_prompt_editor_feedback(self) -> None:
        context = getattr(self, "_prompts_current_context", "default")
        feedback = build_prompt_editor_state_feedback(
            context,
            self._current_prompt_editor_text(),
            saved_text=self._get_saved_prompt_editor_text_for_context(context),
            default_text=self._get_prompt_editor_text_for_context(context, defaults=True),
        )
        _apply_status_text(
            getattr(self, "_prompts_state_label", None),
            feedback.text,
            feedback.color,
        )
        reset_btn = getattr(self, "_prompts_reset_btn", None)
        if reset_btn is not None:
            try:
                enabled_getter = getattr(reset_btn, "isEnabled", None)
                current_enabled = (
                    bool(enabled_getter()) if callable(enabled_getter) else None
                )
                if current_enabled != feedback.reset_enabled:
                    reset_btn.setEnabled_(feedback.reset_enabled)
            except Exception:
                pass

    def _on_prompt_editor_text_changed(self) -> None:
        context = getattr(self, "_prompts_current_context", None)
        if not context:
            return
        if not hasattr(self, "_prompts_cache"):
            self._prompts_cache = {}
        self._prompts_cache[context] = self._current_prompt_editor_text()
        _set_string_value_if_changed(getattr(self, "_prompts_status_label", None), "")
        self._refresh_prompt_editor_feedback()

    def _get_loaded_vocabulary_keywords(self, *, force: bool = False) -> list[str]:
        cached = getattr(self, "_loaded_vocabulary_keywords", None)
        if force or cached is None:
            cached = list(load_vocabulary().get("keywords", []))
            self._loaded_vocabulary_keywords = cached
        return list(cached)

    def _build_logs_card(
        self, y: int, parent_view=None, tab_height: int | None = None
    ) -> int:
        """Erstellt Logs/Transcripts Tab mit Segmented Control."""
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelBorder,
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSMakeRect,
            NSScrollView,
            NSSegmentedControl,
            NSSegmentStyleTexturedRounded,
            NSTextField,
            NSTextView,
            NSSwitchButton,
            NSView,
        )
        import objc  # type: ignore[import-not-found]

        parent_view = parent_view or self._content_view

        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        max_height = (tab_height - 2 * WELCOME_PADDING) if tab_height else 420
        card_height = min(420, max_height)
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        content_width = card_width - 2 * CARD_PADDING

        # Segmented Control: Logs | Transcripts
        segment_y = card_y + card_height - 30
        segment = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect(base_x, segment_y, 170, 22)
        )
        segment.setSegmentCount_(2)
        segment.setLabel_forSegment_("Logs", 0)
        segment.setLabel_forSegment_("Transcripts", 1)
        segment.setWidth_forSegment_(72, 0)
        segment.setWidth_forSegment_(98, 1)
        segment.setSelectedSegment_(0)
        try:
            segment.setSegmentStyle_(NSSegmentStyleTexturedRounded)
        except Exception:
            pass
        segment_handler = _LogsSegmentHandler.alloc().initWithController_(self)
        segment.setTarget_(segment_handler)
        segment.setAction_(
            objc.selector(segment_handler.segmentChanged_, signature=b"v@:@")
        )
        self._logs_segment_control = segment
        self._logs_segment_handler = segment_handler
        self._active_logs_segment = 0
        _set_tooltip_if_supported(
            segment,
            "Switch between the live log view and local transcript history.",
        )
        parent_view.addSubview_(segment)

        # Content area dimensions
        content_y = card_y + 16
        content_height = card_height - 56
        transcripts_scroll_height = content_height - 58

        # ===== LOGS CONTAINER =====
        logs_container = NSView.alloc().initWithFrame_(
            NSMakeRect(base_x, content_y, content_width, content_height)
        )
        self._logs_container = logs_container
        parent_view.addSubview_(logs_container)

        # Auto-refresh Checkbox (in logs container header)
        refresh_btn_w = 74
        open_btn_w = 86
        btn_spacing = 6
        auto_checkbox_w = 112
        actions_left_x = content_width - (
            auto_checkbox_w + open_btn_w + refresh_btn_w + btn_spacing * 3
        )
        auto_checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(max(0, actions_left_x), content_height - 22, auto_checkbox_w, 20)
        )
        auto_checkbox.setButtonType_(NSSwitchButton)
        auto_checkbox.setTitle_("Auto-refresh")
        auto_checkbox.setFont_(NSFont.systemFontOfSize_(10))
        auto_checkbox.setState_(1)
        auto_handler = _LogsAutoRefreshHandler.alloc().initWithController_(self)
        auto_checkbox.setTarget_(auto_handler)
        auto_checkbox.setAction_(
            objc.selector(auto_handler.toggleAutoRefresh_, signature=b"v@:@")
        )
        self._logs_auto_refresh_handler = auto_handler
        self._logs_auto_checkbox = auto_checkbox
        _set_tooltip_if_supported(
            auto_checkbox,
            "Automatically refresh logs or transcript history while this tab is visible.",
        )
        logs_container.addSubview_(auto_checkbox)

        # Open Logs Button
        finder_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                content_width - open_btn_w - refresh_btn_w - btn_spacing,
                content_height - 24,
                open_btn_w,
                22,
            )
        )
        finder_btn.setTitle_("Open Logs")
        finder_btn.setBezelStyle_(NSBezelStyleRounded)
        finder_btn.setFont_(NSFont.systemFontOfSize_(11))
        finder_handler = _OpenLogsInFinderHandler.alloc().initWithController_(self)
        finder_btn.setTarget_(finder_handler)
        finder_btn.setAction_(
            objc.selector(finder_handler.openInFinder_, signature=b"v@:@")
        )
        self._logs_finder_handler = finder_handler
        _set_tooltip_if_supported(
            finder_btn,
            "Reveal the current log file in Finder, or open the logs folder if no log exists yet.",
        )
        logs_container.addSubview_(finder_btn)

        # Refresh Button
        refresh_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(content_width - refresh_btn_w, content_height - 24, refresh_btn_w, 22)
        )
        refresh_btn.setTitle_("Refresh")
        refresh_btn.setBezelStyle_(NSBezelStyleRounded)
        refresh_btn.setFont_(NSFont.systemFontOfSize_(11))
        refresh_handler = _SimpleHandler.alloc().initWithController_method_(
            self, "_refresh_logs_on_demand"
        )
        refresh_btn.setTarget_(refresh_handler)
        refresh_btn.setAction_(
            objc.selector(refresh_handler.performAction_, signature=b"v@:@")
        )
        self._logs_refresh_handler = refresh_handler
        _set_tooltip_if_supported(refresh_btn, "Refresh the visible log output now.")
        logs_container.addSubview_(refresh_btn)

        # Log-Pfad
        path_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, content_height - 20, max(140, actions_left_x - 8), 14)
        )
        path_label.setStringValue_(str(LOG_FILE))
        path_label.setBezeled_(False)
        path_label.setDrawsBackground_(False)
        path_label.setEditable_(False)
        path_label.setSelectable_(True)
        path_label.setFont_(NSFont.systemFontOfSize_(9))
        path_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.4))
        _set_tooltip_if_supported(path_label, f"Current log file path:\n{LOG_FILE}")
        logs_container.addSubview_(path_label)

        # Logs ScrollView
        scroll_height = content_height - 32
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_width, scroll_height)
        )
        scroll.setBorderType_(NSBezelBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        try:
            scroll.setDrawsBackground_(False)
        except Exception:
            pass
        self._logs_scroll_view = scroll

        text_view = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_width, scroll_height)
        )
        text_view.setFont_(NSFont.userFixedPitchFontOfSize_(10))
        text_view.setTextColor_(NSColor.whiteColor())
        try:
            text_view.setDrawsBackground_(False)
        except Exception:
            pass
        text_view.setEditable_(False)
        text_view.setSelectable_(True)
        text_view.setVerticallyResizable_(True)
        text_view.setHorizontallyResizable_(False)
        _set_tooltip_if_supported(
            text_view,
            "Shows the newest PulseScribe log output. Manual refresh keeps your current scroll position when possible.",
        )
        tc = text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)
        initial_logs_text = self._get_logs_text()
        text_view.setString_(initial_logs_text)
        self._set_logs_cache(initial_logs_text)
        initial_logs_signature = getattr(self, "_pending_logs_signature", None)
        if initial_logs_signature is None and LOG_FILE.exists():
            initial_logs_signature = get_file_signature(LOG_FILE)
        self._last_logs_signature = initial_logs_signature
        self._pending_logs_signature = None
        scroll.setDocumentView_(text_view)
        logs_container.addSubview_(scroll)
        self._logs_text_view = text_view

        # ===== TRANSCRIPTS CONTAINER =====
        transcripts_container = NSView.alloc().initWithFrame_(
            NSMakeRect(base_x, content_y, content_width, content_height)
        )
        _set_hidden_if_changed(transcripts_container, True)
        self._transcripts_container = transcripts_container
        self._transcripts_view_built = False
        self._transcripts_layout_metrics = {
            "content_width": content_width,
            "content_height": content_height,
            "scroll_height": transcripts_scroll_height,
            "button_width": refresh_btn_w,
            "button_spacing": btn_spacing,
        }
        parent_view.addSubview_(transcripts_container)

        # Initial scroll and auto-refresh
        self._scroll_logs_to_bottom()
        self._start_logs_auto_refresh()

        return card_y - CARD_SPACING

    def _ensure_transcripts_view_built(self) -> bool:
        """Build the transcripts panel only when it is actually opened."""
        if getattr(self, "_transcripts_view_built", False):
            return False

        container = getattr(self, "_transcripts_container", None)
        metrics = getattr(self, "_transcripts_layout_metrics", None)
        if container is None or not metrics:
            return False

        from AppKit import (  # type: ignore[import-not-found]
            NSBezelBorder,
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSMakeRect,
            NSScrollView,
            NSTextField,
            NSTextView,
        )
        import objc  # type: ignore[import-not-found]

        content_width = metrics["content_width"]
        content_height = metrics["content_height"]
        scroll_height = metrics["scroll_height"]
        btn_spacing = metrics["button_spacing"]
        refresh_btn_w = 74
        clear_btn_w = 104

        clear_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(content_width - clear_btn_w, content_height - 24, clear_btn_w, 22)
        )
        clear_btn.setTitle_("Clear History…")
        clear_btn.setBezelStyle_(NSBezelStyleRounded)
        clear_btn.setFont_(NSFont.systemFontOfSize_(11))
        clear_handler = _ClearTranscriptsHandler.alloc().initWithController_(self)
        clear_btn.setTarget_(clear_handler)
        clear_btn.setAction_(
            objc.selector(clear_handler.clearTranscripts_, signature=b"v@:@")
        )
        self._transcripts_clear_handler = clear_handler
        _set_tooltip_if_supported(
            clear_btn,
            "Permanently remove the local transcript history after confirmation.",
        )
        container.addSubview_(clear_btn)

        refresh_t_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                content_width - clear_btn_w - refresh_btn_w - btn_spacing,
                content_height - 24,
                refresh_btn_w,
                22,
            )
        )
        refresh_t_btn.setTitle_("Refresh")
        refresh_t_btn.setBezelStyle_(NSBezelStyleRounded)
        refresh_t_btn.setFont_(NSFont.systemFontOfSize_(11))
        refresh_t_btn.setTarget_(clear_handler)
        refresh_t_btn.setAction_(
            objc.selector(clear_handler.refreshTranscripts_, signature=b"v@:@")
        )
        _set_tooltip_if_supported(
            refresh_t_btn,
            "Refresh transcript history now.",
        )
        container.addSubview_(refresh_t_btn)

        count_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                0,
                content_height - 20,
                max(140, content_width - clear_btn_w - refresh_btn_w - btn_spacing - 8),
                14,
            )
        )
        count_label.setStringValue_(build_transcripts_count_text(0))
        count_label.setBezeled_(False)
        count_label.setDrawsBackground_(False)
        count_label.setEditable_(False)
        count_label.setSelectable_(False)
        count_label.setFont_(NSFont.systemFontOfSize_(11))
        count_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        _set_tooltip_if_supported(
            count_label,
            "Shows how many recent transcript entries are available locally.",
        )
        container.addSubview_(count_label)
        self._transcripts_count_label = count_label

        hint_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, scroll_height + 4, content_width, 24)
        )
        hint_label.setStringValue_(build_transcripts_hint_text(0))
        hint_label.setBezeled_(False)
        hint_label.setDrawsBackground_(False)
        hint_label.setEditable_(False)
        hint_label.setSelectable_(False)
        hint_label.setFont_(NSFont.systemFontOfSize_(10))
        hint_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.45))
        try:
            hint_label.setUsesSingleLineMode_(False)
        except Exception:
            pass
        _set_tooltip_if_supported(
            hint_label,
            "Transcript history stays local to this Mac unless you export it yourself.",
        )
        container.addSubview_(hint_label)
        self._transcripts_hint_label = hint_label
        self._transcripts_clear_btn = clear_btn

        t_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_width, scroll_height)
        )
        t_scroll.setBorderType_(NSBezelBorder)
        t_scroll.setHasVerticalScroller_(True)
        t_scroll.setHasHorizontalScroller_(False)
        try:
            t_scroll.setDrawsBackground_(False)
        except Exception:
            pass
        self._transcripts_scroll_view = t_scroll

        t_text_view = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_width, scroll_height)
        )
        t_text_view.setFont_(NSFont.systemFontOfSize_(11))
        t_text_view.setTextColor_(NSColor.whiteColor())
        try:
            t_text_view.setDrawsBackground_(False)
        except Exception:
            pass
        t_text_view.setEditable_(False)
        t_text_view.setSelectable_(True)
        t_text_view.setVerticallyResizable_(True)
        t_text_view.setHorizontallyResizable_(False)
        _set_tooltip_if_supported(
            t_text_view,
            "Shows the newest local transcript history. Refresh reloads the history without changing any dictation settings.",
        )
        tc = t_text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)

        initial_transcripts_text, entry_count = self._get_transcripts_payload()
        t_text_view.setString_(initial_transcripts_text)
        self._last_transcripts_text = initial_transcripts_text
        initial_transcripts_signature = getattr(
            self,
            "_pending_transcripts_signature",
            None,
        )
        if initial_transcripts_signature is None:
            initial_transcripts_signature = self._get_transcripts_signature()
        self._last_transcripts_signature = initial_transcripts_signature
        self._pending_transcripts_signature = None
        self._last_transcripts_entries = getattr(
            self,
            "_pending_transcripts_entries",
            [],
        )
        self._last_transcripts_blocks = getattr(
            self,
            "_pending_transcripts_blocks",
            [],
        )
        self._last_transcripts_count_text = None
        self._update_transcripts_meta_state(entry_count)
        t_scroll.setDocumentView_(t_text_view)
        container.addSubview_(t_scroll)
        self._transcripts_text_view = t_text_view
        self._transcripts_view_built = True
        return True

    def _get_transcripts_payload(self) -> tuple[str, int]:
        """Lädt und formatiert die Transkript-Historie inkl. Eintragszahl."""
        from utils.history import (
            format_transcript_entries_for_welcome,
            format_transcripts_for_welcome,
            get_recent_transcripts_with_signature,
        )

        requested_signature = getattr(
            self,
            "_requested_transcripts_signature",
            Ellipsis,
        )

        try:
            entries, signature = get_recent_transcripts_with_signature(
                count=TRANSCRIPTS_VIEW_MAX_ENTRIES,
                signature=requested_signature,
            )
            self._pending_transcripts_had_error = False
            self._pending_transcripts_signature = signature
            blocks = format_transcript_entries_for_welcome(entries)
            (
                transcript_text,
                self._pending_transcripts_entries,
                self._pending_transcripts_blocks,
                entry_count,
            ) = build_transcript_payload(
                entries,
                blocks=blocks,
                empty_text=format_transcripts_for_welcome([]),
            )
            return transcript_text, entry_count
        except Exception as e:
            self._pending_transcripts_had_error = True
            self._pending_transcripts_signature = None
            self._pending_transcripts_entries = []
            self._pending_transcripts_blocks = []
            return build_transcripts_load_error_text(e), 0

    def _get_transcripts_text(self) -> str:
        """Lädt und formatiert die Transkript-Historie."""
        text, _ = self._get_transcripts_payload()
        return text

    def _get_transcripts_signature(self):
        """Liefert eine Dateisignatur für die Transcript-History oder None."""
        try:
            from utils.history import HISTORY_FILE

            return get_file_signature(HISTORY_FILE)
        except Exception:
            return None

    def _refresh_transcripts(self, *, scroll_to_bottom: bool = False) -> bool:
        """Aktualisiert die Transkript-Anzeige mit scroll-schonendem Verhalten."""
        if (
            self._transcripts_text_view is None
            and not self._ensure_transcripts_view_built()
        ):
            return False

        if self._transcripts_text_view:
            try:
                signature = self._get_transcripts_signature()
                previous_signature = getattr(self, "_last_transcripts_signature", None)
                if signature is not None and signature == previous_signature:
                    if scroll_to_bottom:
                        self._scroll_transcripts_to_bottom()
                    return False

                if self._try_append_transcripts_delta(
                    signature,
                    scroll_to_bottom=scroll_to_bottom,
                ):
                    return True

                self._requested_transcripts_signature = signature
                try:
                    transcript_text, entry_count = self._get_transcripts_payload()
                finally:
                    self._requested_transcripts_signature = Ellipsis
                resolved_signature = getattr(
                    self,
                    "_pending_transcripts_signature",
                    None,
                )
                if resolved_signature is None:
                    resolved_signature = signature
                self._pending_transcripts_signature = None
                return self._apply_transcripts_payload(
                    transcript_text,
                    resolved_signature,
                    entry_count,
                    transcript_entries=getattr(
                        self,
                        "_pending_transcripts_entries",
                        getattr(self, "_last_transcripts_entries", None),
                    ),
                    transcript_blocks=getattr(
                        self,
                        "_pending_transcripts_blocks",
                        getattr(self, "_last_transcripts_blocks", None),
                    ),
                    scroll_to_bottom=scroll_to_bottom,
                )
            except Exception:
                return False
        return False

    def _transcript_delta_size_window(
        self,
        signature,
        previous_entries,
    ) -> tuple[int, int] | None:
        if (
            self._transcripts_text_view is None
            or signature is None
            or previous_entries is None
        ):
            return None

        previous_signature = self._last_transcripts_signature
        if previous_signature is None:
            return None

        previous_size = int(previous_signature[1])
        current_size = int(signature[1])
        growth = current_size - previous_size
        if growth <= 0 or growth > INCREMENTAL_TRANSCRIPT_APPEND_MAX_BYTES:
            return None
        return previous_size, current_size

    def _read_merged_transcript_delta(
        self,
        previous_entries,
        previous_size: int,
    ):
        from utils.history import (
            merge_recent_transcript_entries,
            read_transcripts_from_offset,
        )

        appended_entries = read_transcripts_from_offset(
            previous_size,
            max_bytes=INCREMENTAL_TRANSCRIPT_APPEND_MAX_BYTES,
        )
        if not appended_entries:
            return None

        merged_entries = merge_recent_transcript_entries(
            previous_entries,
            appended_entries,
            max_entries=TRANSCRIPTS_VIEW_MAX_ENTRIES,
        )
        if not merged_entries:
            return None

        entries_trimmed = len(merged_entries) < (
            len(previous_entries) + len(appended_entries)
        )
        return appended_entries, merged_entries, entries_trimmed

    def _build_transcript_delta_blocks(
        self,
        *,
        appended_entries,
        merged_entries,
    ) -> tuple[list[str], list[str]] | None:
        from utils.history import format_transcript_entries_for_welcome

        appended_blocks = format_transcript_entries_for_welcome(
            appended_entries,
            newest_first=False,
        )
        if not appended_blocks:
            return None

        previous_blocks = getattr(self, "_last_transcripts_blocks", None)
        if isinstance(previous_blocks, list):
            merged_blocks = [*previous_blocks, *appended_blocks][
                -TRANSCRIPTS_VIEW_MAX_ENTRIES:
            ]
        else:
            merged_blocks = format_transcript_entries_for_welcome(
                merged_entries,
                newest_first=False,
            )
        if not merged_blocks:
            return None
        return appended_blocks, merged_blocks

    def _append_transcript_delta_in_place(
        self,
        appended_blocks: list[str],
        *,
        signature,
        merged_entries,
        merged_blocks: list[str],
    ) -> bool:
        appended_text = "\n\n".join(appended_blocks)
        if not appended_text:
            return False

        separator = "\n\n" if self._last_transcripts_text else ""
        try:
            text_storage = self._transcripts_text_view.textStorage()
            if text_storage is None:
                return False
            text_storage.beginEditing()
            try:
                text_storage.mutableString().appendString_(
                    f"{separator}{appended_text}"
                )
            finally:
                text_storage.endEditing()
        except Exception:
            return False

        self._last_transcripts_text = (
            f"{self._last_transcripts_text or ''}{separator}{appended_text}"
        )
        self._last_transcripts_entries = merged_entries
        self._last_transcripts_blocks = merged_blocks
        self._last_transcripts_signature = signature
        self._update_transcripts_meta_state(len(merged_entries))
        self._scroll_transcripts_to_bottom()
        return True

    def _try_append_transcripts_delta(
        self,
        signature,
        *,
        scroll_to_bottom: bool = False,
    ) -> bool:
        """Refresh transcript history from an append-only delta when possible."""
        previous_entries = getattr(self, "_last_transcripts_entries", None)
        size_window = self._transcript_delta_size_window(signature, previous_entries)
        if size_window is None:
            return False

        previous_size, _current_size = size_window
        delta = self._read_merged_transcript_delta(
            previous_entries,
            previous_size,
        )
        if delta is None:
            return False
        appended_entries, merged_entries, entries_trimmed = delta
        can_append_in_place = should_append_transcript_delta_in_place(
            previous_entries,
            entries_trimmed=entries_trimmed,
            last_text=self._last_transcripts_text,
            scroll_to_bottom=scroll_to_bottom,
            is_near_bottom=self._is_transcripts_near_bottom(),
        )

        blocks = self._build_transcript_delta_blocks(
            appended_entries=appended_entries,
            merged_entries=merged_entries,
        )
        if blocks is None:
            return False
        appended_blocks, merged_blocks = blocks

        if can_append_in_place:
            return self._append_transcript_delta_in_place(
                appended_blocks,
                signature=signature,
                merged_entries=merged_entries,
                merged_blocks=merged_blocks,
            )

        self._apply_transcripts_payload(
            "\n\n".join(merged_blocks),
            signature,
            len(merged_entries),
            transcript_entries=merged_entries,
            transcript_blocks=merged_blocks,
            scroll_to_bottom=scroll_to_bottom,
        )
        return True

    def _apply_transcripts_payload(
        self,
        transcript_text: str,
        signature,
        entry_count: int,
        *,
        transcript_entries=None,
        transcript_blocks=None,
        scroll_to_bottom: bool = False,
    ) -> bool:
        """Apply transcript text updates while preserving scroll position."""
        count_changed = self._update_transcripts_meta_state(
            entry_count,
            has_load_error=bool(getattr(self, "_pending_transcripts_had_error", False)),
        )
        if transcript_text == self._last_transcripts_text:
            if transcript_entries is not None:
                self._last_transcripts_entries = transcript_entries
            if transcript_blocks is not None:
                self._last_transcripts_blocks = transcript_blocks
            self._last_transcripts_signature = signature
            if scroll_to_bottom:
                self._scroll_transcripts_to_bottom()
            return count_changed

        previous_y = 0.0
        if self._transcripts_scroll_view:
            clip_view = self._transcripts_scroll_view.contentView()
            if clip_view is not None:
                previous_y = clip_view.documentVisibleRect().origin.y

        was_near_bottom = self._is_transcripts_near_bottom()
        self._transcripts_text_view.setString_(transcript_text)
        self._last_transcripts_text = transcript_text
        self._last_transcripts_signature = signature
        if transcript_entries is not None:
            self._last_transcripts_entries = transcript_entries
        if transcript_blocks is not None:
            self._last_transcripts_blocks = transcript_blocks

        if scroll_to_bottom or was_near_bottom:
            self._scroll_transcripts_to_bottom()
            return True

        self._restore_transcripts_scroll_position(previous_y)
        return True

    def _scroll_transcripts_to_bottom(self) -> None:
        """Scrollt die Transcripts-Ansicht ans Ende (neueste unten)."""
        if self._transcripts_text_view:
            try:
                length = len(self._transcripts_text_view.string())
                self._transcripts_text_view.scrollRangeToVisible_((length, 0))
            except Exception:
                pass

    def _is_transcripts_near_bottom(self, tolerance: float = 24.0) -> bool:
        """Prüft, ob die Transcripts-Ansicht aktuell nahe am Ende ist."""
        if not self._transcripts_scroll_view or not self._transcripts_text_view:
            return True

        try:
            clip_view = self._transcripts_scroll_view.contentView()
            if clip_view is None:
                return True
            visible = clip_view.documentVisibleRect()
            doc_height = self._transcripts_text_view.frame().size.height
            max_y = max(0.0, doc_height - visible.size.height)
            return visible.origin.y >= (max_y - max(0.0, tolerance))
        except Exception:
            return True

    def _restore_transcripts_scroll_position(self, previous_y: float) -> None:
        """Stellt die vorherige vertikale Transcript-Scroll-Position wieder her."""
        if not self._transcripts_scroll_view or not self._transcripts_text_view:
            return

        try:
            from Foundation import NSMakePoint  # type: ignore[import-not-found]

            clip_view = self._transcripts_scroll_view.contentView()
            if clip_view is None:
                return
            visible = clip_view.documentVisibleRect()
            doc_height = self._transcripts_text_view.frame().size.height
            max_y = max(0.0, doc_height - visible.size.height)
            target_y = max(0.0, min(previous_y, max_y))
            clip_view.scrollToPoint_(NSMakePoint(0.0, target_y))
            self._transcripts_scroll_view.reflectScrolledClipView_(clip_view)
        except Exception:
            pass

    def _update_transcripts_meta_state(
        self,
        entry_count: int,
        *,
        has_load_error: bool = False,
    ) -> bool:
        count_changed = self._update_transcripts_count_label(entry_count)
        hint_changed = self._update_transcripts_hint_label(entry_count)
        clear_changed = self._update_transcripts_clear_button(
            entry_count,
            has_load_error=has_load_error,
        )
        return count_changed or hint_changed or clear_changed

    def _update_transcripts_count_label(self, entry_count: int) -> bool:
        count_label = getattr(self, "_transcripts_count_label", None)
        if not count_label:
            return False

        try:
            label_text = build_transcripts_count_text(entry_count)
            if getattr(self, "_last_transcripts_count_text", None) == label_text:
                return False
            if not _set_string_value_if_changed(count_label, label_text):
                return False
            self._last_transcripts_count_text = label_text
            return True
        except Exception:
            return False

    def _update_transcripts_hint_label(self, entry_count: int) -> bool:
        hint_label = getattr(self, "_transcripts_hint_label", None)
        if hint_label is None:
            return False

        try:
            return _set_string_value_if_changed(
                hint_label,
                build_transcripts_hint_text(entry_count),
            )
        except Exception:
            return False

    def _update_transcripts_clear_button(
        self,
        entry_count: int,
        *,
        has_load_error: bool = False,
    ) -> bool:
        clear_btn = getattr(self, "_transcripts_clear_btn", None)
        if clear_btn is None:
            return False

        should_enable = bool(has_load_error or entry_count > 0)
        try:
            current_enabled = None
            enabled_getter = getattr(clear_btn, "isEnabled", None)
            if callable(enabled_getter):
                current_enabled = bool(enabled_getter())
            else:
                current_enabled = getattr(clear_btn, "enabled", None)
            if current_enabled == should_enable:
                return False
            clear_btn.setEnabled_(should_enable)
            return True
        except Exception:
            return False

    def _refresh_logs_on_demand(self) -> bool:
        changed = bool(self._refresh_logs(scroll_to_bottom=True))
        text, color = build_logs_manual_refresh_feedback(changed=changed, view="logs")
        self._set_footer_status(text, color)
        return changed

    def _refresh_transcripts_on_demand(self) -> bool:
        changed = bool(self._refresh_transcripts(scroll_to_bottom=True))
        text, color = build_logs_manual_refresh_feedback(
            changed=changed,
            view="transcripts",
        )
        self._set_footer_status(text, color)
        return changed

    def _should_clear_transcripts_without_dialog(self) -> bool:
        """Allow transcript-history clearing when no modal confirmation is available."""
        return True

    def _clear_transcripts(self) -> None:
        """Löscht die Transkript-Historie nach Bestätigung."""
        try:
            from AppKit import NSAlert, NSAlertFirstButtonReturn  # type: ignore[import-not-found]
        except ImportError:
            if not self._should_clear_transcripts_without_dialog():
                return
        else:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Clear Transcript History")
            alert.setInformativeText_(
                "This permanently removes the local transcript history. "
                "This cannot be undone."
            )
            alert.addButtonWithTitle_("Clear")
            alert.addButtonWithTitle_("Cancel")
            if alert.runModal() != NSAlertFirstButtonReturn:
                return

        from utils.history import clear_history

        success = bool(clear_history())
        if not success:
            text, color = build_transcripts_clear_feedback(success=False)
            self._set_footer_status(text, color)
            return

        self._refresh_transcripts(scroll_to_bottom=True)
        text, color = build_transcripts_clear_feedback(success=True)
        self._set_footer_status(text, color)

    def _switch_logs_segment(self, segment_index: int) -> None:
        """Wechselt zwischen Logs und Transcripts Ansicht."""
        if self._logs_container and self._transcripts_container:
            if getattr(self, "_active_logs_segment", None) == segment_index:
                return
            self._active_logs_segment = segment_index
            if segment_index == 0:  # Logs
                _set_hidden_if_changed(self._logs_container, False)
                _set_hidden_if_changed(self._transcripts_container, True)
                self._refresh_logs(scroll_to_bottom=True)
            else:  # Transcripts
                self._ensure_transcripts_view_built()
                _set_hidden_if_changed(self._logs_container, True)
                _set_hidden_if_changed(self._transcripts_container, False)
                should_scroll_to_bottom = not getattr(
                    self, "_transcripts_view_seen", False
                )
                self._refresh_transcripts(scroll_to_bottom=should_scroll_to_bottom)
                self._transcripts_view_seen = True
            self._update_logs_auto_refresh_state(reset_cadence=True)

    def _is_logs_view_active(self) -> bool:
        """True, wenn das Logs-Segment aktiv ist."""
        if self._logs_segment_control:
            try:
                return int(self._logs_segment_control.selectedSegment()) == 0
            except Exception:
                return True
        return True

    def _is_logs_tab_active(self) -> bool:
        """True, wenn der Haupttab 'Logs' ausgewählt ist."""
        if not self._tab_view:
            return False
        try:
            selected_item = self._tab_view.selectedTabViewItem()
            if selected_item is None:
                return False
            identifier = selected_item.identifier()
            return str(identifier) == "Logs"
        except Exception:
            return False

    def _is_window_visible_for_logs(self) -> bool:
        """True nur wenn das Fenster sichtbar und nicht minimiert ist."""
        if not self._window:
            return False
        try:
            return bool(self._window.isVisible()) and not bool(
                self._window.isMiniaturized()
            )
        except Exception:
            return False

    def _is_logs_auto_refresh_enabled(self) -> bool:
        """Return the effective checkbox state for the logs auto-refresh timer."""
        checkbox = getattr(self, "_logs_auto_checkbox", None)
        return bool(checkbox and checkbox.state())

    def _should_run_logs_auto_refresh(self, *, enabled: bool | None = None) -> bool:
        """Return whether the active logs/transcripts view should refresh now."""
        effective_enabled = (
            self._is_logs_auto_refresh_enabled() if enabled is None else bool(enabled)
        )
        return should_auto_refresh_logs(
            enabled=effective_enabled,
            is_logs_tab_active=self._is_logs_tab_active(),
            logs_view_index=0 if self._is_logs_view_active() else 1,
            is_window_visible=self._is_window_visible_for_logs(),
            allow_transcripts=True,
        )

    def _get_logs_auto_refresh_interval_seconds(self) -> float:
        """Return the current active refresh cadence for logs/transcripts."""
        step = max(
            0,
            min(
                int(getattr(self, "_logs_auto_refresh_step", 0)),
                len(LOGS_AUTO_REFRESH_INTERVALS_S) - 1,
            ),
        )
        return LOGS_AUTO_REFRESH_INTERVALS_S[step]

    def _set_logs_auto_refresh_step(self, step: int) -> None:
        """Clamp and store the adaptive auto-refresh backoff step."""
        self._logs_auto_refresh_step = max(
            0,
            min(int(step), len(LOGS_AUTO_REFRESH_INTERVALS_S) - 1),
        )

    def _reset_logs_auto_refresh_cadence(self) -> None:
        """Reset active logs/transcripts polling to the fastest cadence."""
        self._set_logs_auto_refresh_step(0)

    def _note_logs_auto_refresh_result(self, *, changed: bool) -> None:
        """Back off polling when nothing visible changed and reset on updates."""
        next_step = (
            0
            if changed
            else min(
                int(getattr(self, "_logs_auto_refresh_step", 0)) + 1,
                len(LOGS_AUTO_REFRESH_INTERVALS_S) - 1,
            )
        )
        self._set_logs_auto_refresh_step(next_step)

    def _get_desired_logs_auto_refresh_interval_seconds(
        self,
        *,
        enabled: bool | None = None,
    ) -> float | None:
        """Return the desired timer interval or ``None`` when auto-refresh is off."""
        effective_enabled = (
            self._is_logs_auto_refresh_enabled() if enabled is None else bool(enabled)
        )
        if not effective_enabled:
            return None
        if self._should_run_logs_auto_refresh(enabled=effective_enabled):
            return self._get_logs_auto_refresh_interval_seconds()
        return LOGS_AUTO_REFRESH_IDLE_INTERVAL_S

    def _schedule_logs_auto_refresh_timer(self, interval_seconds: float) -> None:
        """Create the repeating AppKit timer for logs/transcripts refreshes."""
        from Foundation import NSTimer  # type: ignore[import-not-found]

        self._logs_auto_refresh_interval_seconds = interval_seconds
        self._logs_auto_refresh_timer = (
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                interval_seconds,
                True,
                self._handle_logs_auto_refresh_tick,
            )
        )

    def _refresh_active_logs_view(self) -> bool:
        """Refresh the currently visible logs/transcripts sub-view."""
        if self._is_logs_view_active():
            return self._refresh_logs(scroll_to_bottom=False)
        return self._refresh_transcripts(scroll_to_bottom=False)

    def _handle_logs_auto_refresh_tick(self, _timer) -> None:
        """Drive one adaptive auto-refresh timer tick."""
        enabled = self._is_logs_auto_refresh_enabled()
        changed = False
        if self._should_run_logs_auto_refresh(enabled=enabled):
            changed = self._refresh_active_logs_view()
            self._note_logs_auto_refresh_result(changed=changed)

        desired_interval = self._get_desired_logs_auto_refresh_interval_seconds(
            enabled=enabled
        )
        current_interval = getattr(self, "_logs_auto_refresh_interval_seconds", None)
        if desired_interval is None:
            self._stop_logs_auto_refresh()
            return
        if desired_interval == current_interval:
            return
        self._stop_logs_auto_refresh()
        self._schedule_logs_auto_refresh_timer(desired_interval)

    def _update_logs_auto_refresh_state(self, *, reset_cadence: bool = False) -> None:
        """Synchronize the logs auto-refresh timer with current UI state."""
        logs_auto_checkbox = getattr(self, "_logs_auto_checkbox", None)
        if not logs_auto_checkbox and not self._is_tab_built("Logs"):
            return
        if reset_cadence:
            self._reset_logs_auto_refresh_cadence()

        desired_interval = self._get_desired_logs_auto_refresh_interval_seconds()
        current_interval = getattr(self, "_logs_auto_refresh_interval_seconds", None)
        if desired_interval is None:
            self._stop_logs_auto_refresh()
            return
        if getattr(self, "_logs_auto_refresh_timer", None) and current_interval == desired_interval:
            return
        self._stop_logs_auto_refresh()
        self._schedule_logs_auto_refresh_timer(desired_interval)

    def _open_logs_in_finder(self) -> None:
        """Reveal the current log file or open the logs folder when it is missing."""
        import subprocess

        found_log = bool(LOG_FILE.exists())
        try:
            if found_log:
                result = subprocess.run(["open", "-R", str(LOG_FILE)], check=False)
            else:
                result = subprocess.run(["open", str(LOG_FILE.parent)], check=False)

            if getattr(result, "returncode", 1) == 0:
                text, color = build_logs_open_feedback(
                    found_log=found_log,
                    destination="Finder",
                )
            else:
                text, color = build_logs_open_error_feedback("Finder")
            self._set_footer_status(text, color)
        except Exception:
            text, color = build_logs_open_error_feedback("Finder")
            self._set_footer_status(text, color)

    def _get_logs_text(self, max_chars: int = WELCOME_LOG_MAX_CHARS) -> str:
        """Liest einen Ausschnitt der aktuellen Log-Datei."""
        try:
            if not LOG_FILE.exists():
                self._pending_logs_signature = None
                return build_logs_empty_state_text(LOG_FILE)
            log_text, signature = read_file_tail_text_with_signature(
                LOG_FILE,
                max_chars=max_chars,
                errors="ignore",
                truncated_prefix=LOG_TRUNCATED_PREFIX,
            )
            self._pending_logs_signature = signature
            return log_text
        except Exception as e:
            self._pending_logs_signature = None
            return build_logs_load_error_text(e)

    def _is_logs_near_bottom(self, tolerance: float = 24.0) -> bool:
        """Prüft, ob die Logs-Ansicht aktuell nahe am Ende ist."""
        if not self._logs_scroll_view or not self._logs_text_view:
            return True

        try:
            clip_view = self._logs_scroll_view.contentView()
            if clip_view is None:
                return True
            visible = clip_view.documentVisibleRect()
            doc_height = self._logs_text_view.frame().size.height
            max_y = max(0.0, doc_height - visible.size.height)
            return visible.origin.y >= (max_y - max(0.0, tolerance))
        except Exception:
            return True

    def _restore_logs_scroll_position(self, previous_y: float) -> None:
        """Stellt die vorherige vertikale Scroll-Position best effort wieder her."""
        if not self._logs_scroll_view or not self._logs_text_view:
            return

        try:
            from Foundation import NSMakePoint  # type: ignore[import-not-found]

            clip_view = self._logs_scroll_view.contentView()
            if clip_view is None:
                return
            visible = clip_view.documentVisibleRect()
            doc_height = self._logs_text_view.frame().size.height
            max_y = max(0.0, doc_height - visible.size.height)
            target_y = max(0.0, min(previous_y, max_y))
            clip_view.scrollToPoint_(NSMakePoint(0.0, target_y))
            self._logs_scroll_view.reflectScrolledClipView_(clip_view)
        except Exception:
            pass

    def _split_logs_text_cache(self, log_text: str) -> tuple[list[str], bool]:
        """Split the rendered log payload into cacheable visible chunks."""
        if not log_text:
            return [], False

        prefix = LOG_TRUNCATED_PREFIX
        if prefix and log_text.startswith(prefix):
            visible_text = log_text[len(prefix) :]
            return ([visible_text] if visible_text else []), True
        return [log_text], False

    def _compose_logs_text_from_cache(
        self,
        log_chunks: list[str],
        *,
        truncated: bool,
        max_chars: int = WELCOME_LOG_MAX_CHARS,
    ) -> str:
        """Compose the rendered log payload from cached visible chunks."""
        visible_text = "".join(chunk for chunk in log_chunks if chunk)
        prefix = LOG_TRUNCATED_PREFIX if truncated else ""
        if not prefix or len(prefix) >= max_chars:
            return visible_text[-max_chars:]
        if not truncated and len(visible_text) <= max_chars:
            return visible_text

        visible_budget = max_chars - len(prefix)
        if visible_budget <= 0:
            return visible_text[-max_chars:]
        if len(visible_text) <= visible_budget:
            return f"{prefix}{visible_text}"
        return f"{prefix}{visible_text[-visible_budget:]}"

    def _set_logs_cache(self, log_text: str) -> None:
        """Update cached rendered logs plus the visible chunk representation."""
        chunks, truncated = self._split_logs_text_cache(log_text)
        self._last_logs_text = log_text
        self._last_logs_chunks = chunks
        self._last_logs_truncated = truncated

    def _get_cached_logs_chunks(self) -> tuple[list[str], bool]:
        """Return cached log chunks, deriving them from rendered text if needed."""
        cached_chunks = getattr(self, "_last_logs_chunks", None)
        if isinstance(cached_chunks, list):
            return list(cached_chunks), bool(getattr(self, "_last_logs_truncated", False))
        return self._split_logs_text_cache(self._last_logs_text or "")

    def _apply_logs_payload(
        self,
        log_text: str,
        signature,
        *,
        log_chunks: list[str] | None = None,
        log_truncated: bool | None = None,
        scroll_to_bottom: bool = False,
    ) -> bool:
        """Apply log text updates while preserving scroll position."""
        if log_text == self._last_logs_text:
            self._last_logs_signature = signature
            if log_chunks is not None:
                self._last_logs_chunks = list(log_chunks)
            if log_truncated is not None:
                self._last_logs_truncated = log_truncated
            if scroll_to_bottom:
                self._scroll_logs_to_bottom()
            return False

        previous_y = 0.0
        if self._logs_scroll_view:
            clip_view = self._logs_scroll_view.contentView()
            if clip_view is not None:
                previous_y = clip_view.documentVisibleRect().origin.y

        was_near_bottom = self._is_logs_near_bottom()
        self._logs_text_view.setString_(log_text)
        if log_chunks is not None and log_truncated is not None:
            self._last_logs_text = log_text
            self._last_logs_chunks = list(log_chunks)
            self._last_logs_truncated = log_truncated
        else:
            self._set_logs_cache(log_text)
        self._last_logs_signature = signature

        if scroll_to_bottom or was_near_bottom:
            self._scroll_logs_to_bottom()
            return True

        self._restore_logs_scroll_position(previous_y)
        return True

    def _log_delta_size_window(self, signature) -> tuple[int, int] | None:
        if (
            self._logs_text_view is None
            or signature is None
            or self._last_logs_text is None
        ):
            return None

        previous_signature = getattr(self, "_last_logs_signature", None)
        if previous_signature is None:
            return None

        previous_size = int(previous_signature[1])
        current_size = int(signature[1])
        growth = current_size - previous_size
        if growth <= 0 or growth > INCREMENTAL_LOG_APPEND_MAX_BYTES:
            return None
        return previous_size, current_size

    def _merge_log_delta_chunks(
        self,
        appended_text: str,
    ) -> tuple[str, list[str], bool, bool]:
        previous_chunks, was_truncated = self._get_cached_logs_chunks()
        merged_chunks = [*previous_chunks, appended_text]

        visible_budget = WELCOME_LOG_MAX_CHARS
        prefix = LOG_TRUNCATED_PREFIX
        if prefix and len(prefix) < WELCOME_LOG_MAX_CHARS:
            visible_budget = WELCOME_LOG_MAX_CHARS - len(prefix)
        visible_budget = max(0, visible_budget)

        trimmed_existing = False
        total_visible_length = sum(len(chunk) for chunk in merged_chunks)
        while merged_chunks and total_visible_length > visible_budget:
            overflow = total_visible_length - visible_budget
            first_chunk = merged_chunks[0]
            trimmed_existing = True
            if overflow >= len(first_chunk):
                total_visible_length -= len(first_chunk)
                merged_chunks.pop(0)
                continue
            merged_chunks[0] = first_chunk[overflow:]
            total_visible_length -= overflow
            break

        merged_truncated = was_truncated or trimmed_existing
        merged_text = self._compose_logs_text_from_cache(
            merged_chunks,
            truncated=merged_truncated,
        )
        return merged_text, merged_chunks, merged_truncated, trimmed_existing

    def _append_log_delta_in_place(
        self,
        appended_text: str,
        *,
        signature,
        merged_text: str,
        merged_chunks: list[str],
        merged_truncated: bool,
    ) -> bool:
        try:
            text_storage = self._logs_text_view.textStorage()
            if text_storage is None:
                return False
            text_storage.beginEditing()
            try:
                text_storage.mutableString().appendString_(appended_text)
            finally:
                text_storage.endEditing()
        except Exception:
            return False

        self._last_logs_text = merged_text
        self._last_logs_chunks = merged_chunks
        self._last_logs_truncated = merged_truncated
        self._last_logs_signature = signature
        self._scroll_logs_to_bottom()
        return True

    def _try_append_logs_delta(
        self,
        signature,
        *,
        scroll_to_bottom: bool,
    ) -> bool:
        """Refresh logs from an append-only delta when the file only grows."""
        size_window = self._log_delta_size_window(signature)
        if size_window is None:
            return False

        previous_size, _current_size = size_window
        appended_text = read_file_text_from_offset(
            LOG_FILE,
            start_offset=previous_size,
            errors="ignore",
            max_bytes=INCREMENTAL_LOG_APPEND_MAX_BYTES,
        )
        if not appended_text:
            return False

        merged_text, merged_chunks, merged_truncated, trimmed_existing = (
            self._merge_log_delta_chunks(appended_text)
        )
        previous_text = self._last_logs_text
        if merged_text == previous_text:
            self._last_logs_signature = signature
            self._last_logs_chunks = merged_chunks
            self._last_logs_truncated = merged_truncated
            if scroll_to_bottom:
                self._scroll_logs_to_bottom()
            return True

        can_append_in_place = (
            not trimmed_existing
            and (scroll_to_bottom or self._is_logs_near_bottom())
        )

        if can_append_in_place:
            return self._append_log_delta_in_place(
                appended_text,
                signature=signature,
                merged_text=merged_text,
                merged_chunks=merged_chunks,
                merged_truncated=merged_truncated,
            )

        self._apply_logs_payload(
            merged_text,
            signature,
            log_chunks=merged_chunks,
            log_truncated=merged_truncated,
            scroll_to_bottom=scroll_to_bottom,
        )
        return True

    def _refresh_logs(self, *, scroll_to_bottom: bool = True) -> bool:
        """Aktualisiert die Log-Anzeige mit scroll-schonendem Verhalten."""
        if self._logs_text_view:
            try:
                signature = get_file_signature(LOG_FILE)
                previous_signature = getattr(self, "_last_logs_signature", None)
                if signature is not None and signature == previous_signature:
                    if scroll_to_bottom:
                        self._scroll_logs_to_bottom()
                    return False

                previous_text = self._last_logs_text
                if self._try_append_logs_delta(
                    signature,
                    scroll_to_bottom=scroll_to_bottom,
                ):
                    return self._last_logs_text != previous_text

                log_text = self._get_logs_text()
                resolved_signature = getattr(self, "_pending_logs_signature", None)
                if resolved_signature is None:
                    resolved_signature = signature
                self._pending_logs_signature = None
                return self._apply_logs_payload(
                    log_text,
                    resolved_signature,
                    scroll_to_bottom=scroll_to_bottom,
                )
            except Exception:
                return False
        return False

    def _scroll_logs_to_bottom(self) -> None:
        """Scrollt die Log-Ansicht ans Ende."""
        if self._logs_text_view:
            try:
                length = len(self._logs_text_view.string())
                self._logs_text_view.scrollRangeToVisible_((length, 0))
            except Exception:
                pass

    def _start_logs_auto_refresh(self, *, reset_cadence: bool = True) -> None:
        """Startet den adaptiven Auto-Refresh Timer für Logs und Transcripts."""
        self._update_logs_auto_refresh_state(reset_cadence=reset_cadence)

    def _stop_logs_auto_refresh(self) -> None:
        """Stoppt den Auto-Refresh Timer."""
        if hasattr(self, "_logs_auto_refresh_timer") and self._logs_auto_refresh_timer:
            try:
                self._logs_auto_refresh_timer.invalidate()
            except Exception:
                pass
            self._logs_auto_refresh_timer = None
        self._logs_auto_refresh_interval_seconds = None

    def _build_about_card(self, y: int, parent_view=None) -> int:
        """Erstellt About Tab mit umfassender App-Beschreibung."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        parent_view = parent_view or self._content_view

        card_height = 380
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        content_width = card_width - 2 * CARD_PADDING
        current_y = card_y + card_height - 28

        def add_title(text: str, y_pos: int, size: int = 13) -> int:
            label = NSTextField.alloc().initWithFrame_(
                NSMakeRect(base_x, y_pos, content_width, 18)
            )
            label.setStringValue_(text)
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setFont_(NSFont.systemFontOfSize_weight_(size, NSFontWeightSemibold))
            label.setTextColor_(NSColor.whiteColor())
            parent_view.addSubview_(label)
            return y_pos - 20

        def add_text(text: str, y_pos: int, height: int = 36) -> int:
            label = NSTextField.alloc().initWithFrame_(
                NSMakeRect(base_x, y_pos - height + 16, content_width, height)
            )
            label.setStringValue_(text)
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
            label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.8))
            try:
                label.setLineBreakMode_(0)
                label.setUsesSingleLineMode_(False)
            except Exception:
                pass
            parent_view.addSubview_(label)
            return y_pos - height

        # Haupttitel
        current_y = add_title("PulseScribe", current_y, 14)

        # Tagline
        current_y = add_text(
            "Ultra-fast voice input for macOS. "
            "Transcribes audio using multiple providers or local Whisper with ultra-low latency.",
            current_y,
            32,
        )

        current_y -= 8

        # Features Section
        current_y = add_title("✨ Features", current_y, 12)
        current_y = add_text(
            "• Real-time Streaming (Deepgram, ~300ms latency)\n"
            "• Multiple Providers: Deepgram, OpenAI, Groq, Local Whisper\n"
            "• LLM Post-processing: Grammar, punctuation, voice commands\n"
            "• Context Awareness: Adapts style to active app (email/chat/code)\n"
            "• Custom Vocabulary for names and technical terms\n"
            "• Visual Feedback: Menu bar status + animated overlay",
            current_y,
            80,
        )

        current_y -= 8

        # Providers Section
        current_y = add_title("🚀 Providers", current_y, 12)
        current_y = add_text(
            "• Deepgram: ~300ms ⚡ WebSocket streaming (recommended)\n"
            "• Groq: ~1s, Whisper on LPU hardware\n"
            "• OpenAI: ~2-3s, GPT-4o Transcribe, highest quality\n"
            "• Local: Offline via Whisper/MLX/Faster-Whisper",
            current_y,
            56,
        )

        current_y -= 8

        # Links Section
        current_y = add_title("📁 Resources", current_y, 12)
        hint = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, current_y - 32, content_width, 32)
        )
        hint.setStringValue_(
            "Config: ~/.pulsescribe/\n" "GitHub: github.com/KLIEBHAN/pulsescribe"
        )
        hint.setBezeled_(False)
        hint.setDrawsBackground_(False)
        hint.setEditable_(False)
        hint.setSelectable_(True)  # Selectable für Copy
        hint.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        hint.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        try:
            hint.setLineBreakMode_(0)
            hint.setUsesSingleLineMode_(False)
        except Exception:
            pass
        parent_view.addSubview_(hint)

        return card_y - CARD_SPACING

    def _parse_keywords_text(self, raw: str) -> list[str]:
        """Parst Keywords aus Multiline/Comma Input."""
        return split_vocabulary_text(raw)

    def _get_current_keywords(self) -> list[str]:
        if not self._vocab_text_view:
            return []
        try:
            raw = str(self._vocab_text_view.string() or "")
        except Exception:
            raw = ""
        return self._parse_keywords_text(raw)

    def _update_vocabulary_warning(self) -> None:
        label = getattr(self, "_vocab_warning_label", None)
        if not label:
            return
        raw_text = ""
        text_view = getattr(self, "_vocab_text_view", None)
        if text_view is not None:
            try:
                raw_text = str(text_view.string() or "")
            except Exception:
                raw_text = ""
        text, color = build_vocabulary_editor_feedback(
            raw_text,
            saved_keywords=self._get_loaded_vocabulary_keywords(),
        )
        try:
            label.setStringValue_(text)
            label.setTextColor_(_status_color(color))
        except Exception:
            pass

    def _add_setting_label(self, x: int, y: int, text: str, parent_view=None):
        """Erstellt ein Label für eine Einstellung."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSMakeRect,
            NSTextField,
        )

        parent_view = parent_view or self._content_view
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y + 2, 110, 16))
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        parent_view.addSubview_(label)
        return label

    def _update_local_settings_visibility(self) -> None:
        """Blendet Local-spezifische Einstellungen je nach Mode ein/aus."""
        if not self._mode_popup:
            return
        mode = self._mode_popup.titleOfSelectedItem()
        is_local = mode == "local"
        backend = (
            self._local_backend_popup.titleOfSelectedItem()
            if self._local_backend_popup
            else "auto"
        )
        state = get_local_advanced_ui_state(mode, backend)

        for view in (
            self._local_backend_label,
            self._local_backend_popup,
            self._local_model_label,
            self._local_model_popup,
        ):
            _set_hidden_if_changed(view, not is_local)

        for view in getattr(self, "_advanced_general_views", ()):
            _set_hidden_if_changed(view, not state.show_general)

        _set_string_value_if_changed(
            getattr(self, "_advanced_local_status_label", None),
            state.guidance,
        )

        for view in (
            getattr(self, "_compute_type_label", None),
            getattr(self, "_compute_type_field", None),
            getattr(self, "_cpu_threads_label", None),
            getattr(self, "_cpu_threads_field", None),
            getattr(self, "_num_workers_label", None),
            getattr(self, "_num_workers_field", None),
            getattr(self, "_without_timestamps_label", None),
            getattr(self, "_without_timestamps_popup", None),
            getattr(self, "_vad_filter_label", None),
            getattr(self, "_vad_filter_popup", None),
        ):
            _set_hidden_if_changed(view, not state.show_faster)

        for view in (
            self._lightning_header,
            self._lightning_batch_label,
            self._lightning_batch_slider,
            self._lightning_batch_value_label,
            self._lightning_quant_label,
            self._lightning_quant_popup,
        ):
            _set_hidden_if_changed(view, not state.show_lightning)

    def _update_streaming_visibility(self) -> None:
        """Blendet Streaming-Toggle nur bei Deepgram ein."""
        mode_popup = getattr(self, "_mode_popup", None)
        if not mode_popup:
            return
        is_deepgram = mode_popup.titleOfSelectedItem() == "deepgram"
        for view in (
            getattr(self, "_streaming_label", None),
            getattr(self, "_streaming_checkbox", None),
        ):
            _set_hidden_if_changed(view, not is_deepgram)

    def _update_all_visibility(self) -> None:
        """Update all mode-dependent visibility settings."""
        self._update_local_settings_visibility()
        self._update_streaming_visibility()
        self._refresh_provider_key_statuses()
        self._refresh_setup_try_card()
        self._refresh_footer_settings_hint()

    def _apply_selected_local_preset(self) -> None:
        """Wendet das aktuell gewählte Local-Preset auf die UI an."""
        if not self._local_preset_popup:
            return
        preset = self._local_preset_popup.titleOfSelectedItem()
        if not preset or preset == "(none)":
            return
        self._apply_local_preset(preset)

    def _select_popup_title(self, popup, title: str) -> None:
        if popup is None or not title:
            return
        try:
            popup.selectItemWithTitle_(title)
        except Exception:
            # Falls Custom Value fehlt, als Item hinzufügen.
            try:
                popup.addItemWithTitle_(title)
                popup.selectItemWithTitle_(title)
            except Exception:
                pass

    def _set_preset_field_value(self, field, value: str) -> None:
        if field is None:
            return
        try:
            field.setStringValue_(value)
        except Exception:
            pass

    def _set_preset_slider_value(self, slider, label, value: str) -> None:
        if slider is None:
            return
        try:
            slider.setIntValue_(int(value))
        except Exception:
            return
        if label is None:
            return
        try:
            label.setStringValue_(str(int(slider.intValue())))
        except Exception:
            pass

    def _set_lightning_quant_popup(self, popup, value: str) -> None:
        if popup is None:
            return
        normalized = (value or "none").strip().lower()
        try:
            if normalized == "4bit":
                popup.selectItemAtIndex_(2)
            elif normalized == "8bit":
                popup.selectItemAtIndex_(1)
            else:
                popup.selectItemAtIndex_(0)
        except Exception:
            pass

    def _local_preset_values(self, preset: str) -> dict[str, str] | None:
        preset_values = LOCAL_PRESETS.get(preset)
        if not preset_values:
            return None

        values = dict(LOCAL_PRESET_BASE)
        values.update(preset_values)
        return values

    def _apply_local_preset_values(self, values: dict[str, str]) -> None:
        self._select_popup_title(
            self._local_backend_popup, values.get("local_backend", "")
        )
        self._select_popup_title(self._local_model_popup, values.get("local_model", ""))
        self._select_popup_title(self._device_popup, values.get("device", "auto"))
        self._select_popup_title(self._warmup_popup, values.get("warmup", "auto"))
        self._select_popup_title(
            self._local_fast_popup, values.get("local_fast", "default")
        )
        self._select_popup_title(self._fp16_popup, values.get("fp16", "default"))
        self._set_preset_field_value(self._beam_size_field, values.get("beam_size", ""))
        self._set_preset_field_value(self._best_of_field, values.get("best_of", ""))
        self._set_preset_field_value(
            self._temperature_field, values.get("temperature", "")
        )
        self._set_preset_field_value(
            self._compute_type_field, values.get("compute_type", "")
        )
        self._set_preset_field_value(
            self._cpu_threads_field, values.get("cpu_threads", "")
        )
        self._set_preset_field_value(
            self._num_workers_field, values.get("num_workers", "")
        )
        self._select_popup_title(
            self._without_timestamps_popup,
            values.get("without_timestamps", "default"),
        )
        self._select_popup_title(
            self._vad_filter_popup, values.get("vad_filter", "default")
        )
        self._set_preset_slider_value(
            self._lightning_batch_slider,
            self._lightning_batch_value_label,
            values.get("lightning_batch_size", "12"),
        )
        self._set_lightning_quant_popup(
            self._lightning_quant_popup,
            values.get("lightning_quant", "none"),
        )

    def _apply_local_preset(self, preset: str) -> None:
        """Setzt empfohlene Settings (UI-only; Speichern via 'Save & Apply')."""
        self._ensure_tab_built("Providers")
        self._ensure_tab_built("Advanced")

        # Immer Local Mode aktivieren (sonst sind Backend/Model hidden)
        self._select_popup_title(self._mode_popup, "local")
        self._update_all_visibility()

        values = self._local_preset_values(preset)
        if values is None:
            return

        self._apply_local_preset_values(values)
        self._update_all_visibility()
        self._refresh_footer_settings_hint()
        return

    def _build_footer(self) -> None:
        """Erstellt Footer mit Checkbox, Save-Button und Start-Button."""
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSButtonTypeSwitch,
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )
        import objc  # type: ignore[import-not-found]

        footer_y = WELCOME_PADDING

        # Button layout (right-aligned)
        btn_w = 132
        btn_h = 32
        btn_spacing = 10
        btn_font_size = 13

        # Checkbox (links unten)
        checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(WELCOME_PADDING, footer_y + 6, 130, 18)
        )
        checkbox.setButtonType_(NSButtonTypeSwitch)
        checkbox.setTitle_("Show at startup")
        checkbox.setFont_(NSFont.systemFontOfSize_(11))
        checkbox.setState_(1 if get_show_welcome_on_startup() else 0)

        checkbox_handler = _CheckboxHandler.alloc().init()
        checkbox.setTarget_(checkbox_handler)
        checkbox.setAction_(
            objc.selector(checkbox_handler.toggleStartup_, signature=b"v@:@")
        )
        self._checkbox_handler = checkbox_handler
        self._content_view.addSubview_(checkbox)
        self._startup_checkbox = checkbox

        # Save & Apply Button (rechts, links vom Close-Button)
        right_edge = WELCOME_WIDTH - WELCOME_PADDING
        close_x = right_edge - btn_w
        save_x = close_x - btn_spacing - btn_w

        status_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(WELCOME_PADDING, footer_y + 36, WELCOME_WIDTH - 2 * WELCOME_PADDING, 32)
        )
        status_label.setStringValue_("")
        status_label.setBezeled_(False)
        status_label.setDrawsBackground_(False)
        status_label.setEditable_(False)
        status_label.setSelectable_(False)
        status_label.setFont_(NSFont.systemFontOfSize_(10))
        status_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        try:
            status_label.setLineBreakMode_(0)
            status_label.setUsesSingleLineMode_(False)
        except Exception:
            pass
        self._content_view.addSubview_(status_label)
        self._footer_status_label = status_label

        save_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(save_x, footer_y, btn_w, btn_h)
        )
        save_btn.setTitle_("Save & Apply")
        save_btn.setBezelStyle_(NSBezelStyleRounded)
        save_btn.setFont_(
            NSFont.systemFontOfSize_weight_(btn_font_size, NSFontWeightMedium)
        )

        save_handler = _SimpleHandler.alloc().initWithController_method_(
            self, "_save_all_settings"
        )
        save_btn.setTarget_(save_handler)
        save_btn.setAction_(
            objc.selector(save_handler.performAction_, signature=b"v@:@")
        )
        self._save_all_handler = save_handler
        self._save_btn = save_btn
        _set_tooltip_if_supported(save_btn, "Save the current settings shown in this window.")
        self._content_view.addSubview_(save_btn)

        # Start-Button (prominent, rechts)
        start_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(close_x, footer_y, btn_w, btn_h)
        )
        start_btn.setTitle_("Close")
        start_btn.setBezelStyle_(NSBezelStyleRounded)
        start_btn.setFont_(
            NSFont.systemFontOfSize_weight_(btn_font_size, NSFontWeightSemibold)
        )

        start_handler = _SimpleHandler.alloc().initWithController_method_(
            self, "_handle_start"
        )
        start_btn.setTarget_(start_handler)
        start_btn.setAction_(
            objc.selector(start_handler.performAction_, signature=b"v@:@")
        )
        self._start_handler = start_handler
        _set_tooltip_if_supported(start_btn, "Close this settings window.")

        self._content_view.addSubview_(start_btn)

    def _set_footer_status(self, text: str, color: str = "text_secondary") -> None:
        _apply_status_text(getattr(self, "_footer_status_label", None), text, color)

    def set_on_start_callback(self, callback) -> None:
        """Setzt Callback für Start-Button."""
        self._on_start_callback = callback

    def set_on_settings_changed(self, callback) -> None:
        """Setzt Callback der aufgerufen wird wenn Settings gespeichert werden."""
        self._on_settings_changed_callback = callback

    def _on_settings_changed(self) -> None:
        """Wrapper für Settings-Changed-Callback (für HotkeyCard)."""
        if self._on_settings_changed_callback:
            self._on_settings_changed_callback()

    def set_onboarding_wizard_callback(self, callback) -> None:
        """Setzt Callback zum Öffnen des separaten Setup-Wizards."""
        self._onboarding_wizard_callback = callback

    def show(self) -> None:
        """Zeigt Window (nicht-modal)."""
        if self._window:
            self._ensure_selected_tab_built()
            self._window.makeKeyAndOrderFront_(None)
            self._window.center()
            from AppKit import NSApp  # type: ignore[import-not-found]

            NSApp.activateIgnoringOtherApps_(True)
            self._start_logs_auto_refresh()

    def hide(self) -> None:
        """Versteckt Window temporär (ohne zu schließen)."""
        self._stop_hotkey_recording(cancelled=True)
        if self._window:
            self._window.orderOut_(None)
        self._stop_setup_permission_auto_refresh()
        self._stop_logs_auto_refresh()

    def close(self) -> None:
        """Schließt Window und markiert Onboarding als gesehen."""
        set_onboarding_seen(True)
        self._stop_hotkey_recording(cancelled=True)
        self._stop_setup_permission_auto_refresh()
        self._stop_logs_auto_refresh()
        if self._window:
            self._window.close()

    def _handle_start(self) -> None:
        """Handler für Start-Button."""
        set_onboarding_seen(True)
        if self._on_start_callback:
            self._on_start_callback()
        self.close()

    def _build_env_updates_from_controls(self, log) -> dict[str, str | None]:
        """Read current settings controls and build normalized `.env` updates."""
        builder = SettingsEnvUpdateBuilder(log)
        self._add_api_key_env_updates(builder)
        self._add_general_env_updates(builder)
        self._add_local_option_env_updates(builder)
        self._add_decode_env_updates(builder)
        self._add_lightning_env_updates(builder)
        self._add_streaming_env_updates(builder)
        self._add_refine_display_env_updates(builder)
        return builder.build()

    def _add_api_key_env_updates(self, builder: SettingsEnvUpdateBuilder) -> None:
        for provider, _label, env_key in API_KEY_PROVIDERS:
            field = getattr(self, f"_{provider}_field", None)
            if field is not None:
                builder.set_optional(env_key, field.stringValue())

    def _add_general_env_updates(self, builder: SettingsEnvUpdateBuilder) -> None:
        if self._mode_popup:
            builder.set_present(
                "PULSESCRIBE_MODE",
                self._mode_popup.titleOfSelectedItem(),
            )

        if self._local_backend_popup:
            builder.set_local_backend(
                "PULSESCRIBE_LOCAL_BACKEND",
                self._local_backend_popup.titleOfSelectedItem(),
            )

        if self._local_model_popup:
            builder.set_optional(
                "PULSESCRIBE_LOCAL_MODEL",
                self._local_model_popup.titleOfSelectedItem(),
                remove_when={"default"},
            )

        if self._lang_popup:
            builder.set_optional(
                "PULSESCRIBE_LANGUAGE",
                self._lang_popup.titleOfSelectedItem(),
                remove_when={"auto"},
            )

    def _add_local_option_env_updates(
        self,
        builder: SettingsEnvUpdateBuilder,
    ) -> None:
        if self._device_popup:
            self._add_popup_optional_env_update(
                builder,
                "PULSESCRIBE_DEVICE",
                self._device_popup,
                remove_when={"auto"},
                lower=True,
            )

        if self._warmup_popup:
            self._add_popup_optional_env_update(
                builder,
                "PULSESCRIBE_LOCAL_WARMUP",
                self._warmup_popup,
                remove_when={"auto"},
                lower=True,
            )

        self._add_popup_optional_env_update(
            builder,
            "PULSESCRIBE_LOCAL_FAST",
            self._local_fast_popup,
            remove_when={"default"},
            lower=True,
        )
        if self._fp16_popup:
            self._add_popup_optional_env_update(
                builder,
                LOCAL_FP16_ENV_KEY,
                self._fp16_popup,
                remove_when={"default"},
                lower=True,
            )
            builder.remove_key(LEGACY_LOCAL_FP16_ENV_KEY)

    def _add_popup_optional_env_update(
        self,
        builder: SettingsEnvUpdateBuilder,
        key: str,
        popup,
        *,
        remove_when: set[str],
        lower: bool = False,
    ) -> None:
        if popup is None:
            return
        builder.set_optional(
            key,
            popup.titleOfSelectedItem(),
            remove_when=remove_when,
            lower=lower,
        )

    def _add_decode_env_updates(self, builder: SettingsEnvUpdateBuilder) -> None:
        int_fields = (
            ("PULSESCRIBE_LOCAL_BEAM_SIZE", self._beam_size_field),
            ("PULSESCRIBE_LOCAL_BEST_OF", self._best_of_field),
            ("PULSESCRIBE_LOCAL_CPU_THREADS", self._cpu_threads_field),
            ("PULSESCRIBE_LOCAL_NUM_WORKERS", self._num_workers_field),
        )
        for key, field in int_fields:
            if field is not None:
                builder.set_optional_int(key, field.stringValue())

        str_fields = (
            ("PULSESCRIBE_LOCAL_TEMPERATURE", self._temperature_field),
            ("PULSESCRIBE_LOCAL_COMPUTE_TYPE", self._compute_type_field),
        )
        for key, field in str_fields:
            if field is not None:
                builder.set_optional(key, field.stringValue())

        self._add_popup_optional_env_update(
            builder,
            "PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS",
            self._without_timestamps_popup,
            remove_when={"default"},
            lower=True,
        )
        self._add_popup_optional_env_update(
            builder,
            "PULSESCRIBE_LOCAL_VAD_FILTER",
            self._vad_filter_popup,
            remove_when={"default"},
            lower=True,
        )

    @staticmethod
    def _lightning_quant_value_from_index(index: int) -> str:
        if index == 1:
            return "8bit"
        if index >= 2:
            return "4bit"
        return "none"

    def _add_lightning_env_updates(
        self,
        builder: SettingsEnvUpdateBuilder,
    ) -> None:
        if self._lightning_batch_slider:
            builder.set_lightning_batch(
                "PULSESCRIBE_LIGHTNING_BATCH_SIZE",
                int(self._lightning_batch_slider.intValue()),
            )

        if self._lightning_quant_popup:
            builder.set_optional(
                "PULSESCRIBE_LIGHTNING_QUANT",
                self._lightning_quant_value_from_index(
                    int(self._lightning_quant_popup.indexOfSelectedItem())
                ),
                remove_when={"none"},
            )

    def _add_streaming_env_updates(
        self,
        builder: SettingsEnvUpdateBuilder,
    ) -> None:
        if self._streaming_checkbox and self._mode_popup:
            is_deepgram = self._mode_popup.titleOfSelectedItem() == "deepgram"
            if is_deepgram:
                builder.set_enabled_default_true(
                    "PULSESCRIBE_STREAMING",
                    self._streaming_checkbox.state() == 1,
                )

    def _add_refine_display_env_updates(
        self,
        builder: SettingsEnvUpdateBuilder,
    ) -> None:
        if self._refine_checkbox:
            builder.set_bool_string(
                "PULSESCRIBE_REFINE",
                self._refine_checkbox.state() == 1,
            )

        if self._clipboard_restore_checkbox:
            builder.set_enabled_default_false(
                "PULSESCRIBE_CLIPBOARD_RESTORE",
                self._clipboard_restore_checkbox.state() == 1,
            )

        if self._overlay_checkbox:
            builder.set_enabled_default_true(
                "PULSESCRIBE_OVERLAY",
                self._overlay_checkbox.state() == 1,
            )

        if self._dock_icon_checkbox:
            builder.set_enabled_default_true(
                "PULSESCRIBE_DOCK_ICON",
                self._dock_icon_checkbox.state() == 1,
            )

        if self._rtf_checkbox:
            builder.set_enabled_default_false(
                "PULSESCRIBE_SHOW_RTF",
                self._rtf_checkbox.state() == 1,
            )

        if self._provider_popup:
            builder.set_present(
                "PULSESCRIBE_REFINE_PROVIDER",
                self._provider_popup.titleOfSelectedItem(),
            )

        if self._model_field:
            builder.set_optional(
                "PULSESCRIBE_REFINE_MODEL",
                self._model_field.stringValue(),
            )

    def _save_all_settings(self) -> None:
        """Speichert alle Einstellungen in die .env Datei.

        Note: Hotkeys werden direkt via HotkeyCard gespeichert (nicht hier).
        """
        import logging

        log = logging.getLogger(__name__)

        previous_dock_state = getattr(self, "_saved_dock_icon_enabled", None)
        env_updates = self._build_env_updates_from_controls(log)
        self._apply_env_updates(env_updates)
        self._refresh_provider_key_statuses()

        # Vocabulary / Keywords
        if self._vocab_text_view:
            raw_keywords_text = str(self._vocab_text_view.string() or "")
            analysis = analyze_vocabulary_text(raw_keywords_text)
            existing_keywords = self._get_loaded_vocabulary_keywords()
            keywords_changed = analysis.keywords != existing_keywords
            try:
                if keywords_changed:
                    vocab_data, _warnings, _signature = save_vocabulary_state(
                        analysis.keywords
                    )
                    normalized_keywords = list(vocab_data.get("keywords", []))
                    self._loaded_vocabulary_keywords = list(normalized_keywords)
                    _set_text_view_string_if_changed(
                        self._vocab_text_view,
                        "\n".join(normalized_keywords),
                    )
                    text, color = build_vocabulary_save_feedback(
                        raw_keywords_text,
                        unchanged=False,
                    )
                    _apply_status_text(self._vocab_warning_label, text, color)
                    log.info(f"Saved {len(normalized_keywords)} vocabulary keywords")
                else:
                    text, color = build_vocabulary_save_feedback(
                        raw_keywords_text,
                        unchanged=True,
                    )
                    _apply_status_text(self._vocab_warning_label, text, color)
            except Exception as e:
                log.warning(f"Could not save vocabulary: {e}")
                _apply_status_text(
                    self._vocab_warning_label,
                    f"Could not save the custom vocabulary: {e}",
                    "error",
                )

        # Custom Prompts speichern
        self._save_custom_prompts()

        log.info("All settings saved to .env file")

        current_dock_state = self._get_current_dock_icon_enabled()
        dock_changed = (
            previous_dock_state is not None and previous_dock_state != current_dock_state
        )
        self._saved_settings_signature = self._get_current_settings_signature()
        self._saved_dock_icon_enabled = current_dock_state
        self._saved_refine_settings_state = self._get_current_refine_settings_state()
        self._saved_display_settings_state = self._get_current_display_settings_state()
        self._refresh_secondary_settings_feedback()

        # Callback aufrufen damit Daemon Settings neu lädt
        if self._on_settings_changed_callback:
            self._on_settings_changed_callback()

        text, color = build_settings_saved_feedback(
            relaunch_required=dock_changed,
        )
        self._set_footer_status(text, color)

        # Visuelles Feedback: Button-Text kurz ändern
        if hasattr(self, "_save_btn") and self._save_btn:
            self._save_btn.setTitle_("✓ Saved!")
            # Nach 1.5 Sekunden zurücksetzen
            from Foundation import NSTimer  # type: ignore[import-not-found]

            def reset_title():
                if hasattr(self, "_save_btn") and self._save_btn:
                    self._save_btn.setTitle_("Save & Apply")

            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                1.5, False, lambda _: reset_title()
            )

    def _save_custom_prompts(self) -> None:
        """Persist prompt-editor drafts while preserving untouched saved overrides."""
        import logging

        log = logging.getLogger(__name__)

        if not self._cache_current_prompt_editor_draft():
            return

        existing_overrides, data_to_save = self._build_custom_prompt_save_payloads()
        if data_to_save == existing_overrides:
            self._handle_custom_prompts_unchanged(log)
            return

        if data_to_save:
            self._persist_custom_prompt_overrides(data_to_save, log)
            return

        self._reset_custom_prompts_to_defaults(log)

    def _cache_current_prompt_editor_draft(self) -> bool:
        text_view = getattr(self, "_prompts_text_view", None)
        if not text_view:
            return False

        if not hasattr(self, "_prompts_cache"):
            self._prompts_cache = {}

        current_ctx = getattr(self, "_prompts_current_context", "default")
        current_text = str(text_view.string())
        self._prompts_cache[current_ctx] = current_text
        return True

    def _build_custom_prompt_save_payloads(self) -> tuple[dict, dict]:
        from utils.custom_prompts import filter_overrides_for_storage

        defaults = self._get_prompt_defaults_data()
        existing = self._get_loaded_prompts_data()
        existing_overrides = filter_overrides_for_storage(existing, defaults=defaults)
        drafts = getattr(self, "_prompts_cache", {})
        data_to_save = build_prompt_overrides_from_editor_state(
            existing=existing,
            drafts=drafts,
            contexts=set(drafts.keys()),
            defaults=defaults,
        )
        return existing_overrides, data_to_save

    def _set_prompts_status_text(self, text: str) -> None:
        label = getattr(self, "_prompts_status_label", None)
        if label:
            label.setStringValue_(text)

    def _handle_custom_prompts_unchanged(self, log) -> None:
        log.info("Custom prompts unchanged, skipped prompts.toml rewrite")
        self._set_prompts_status_text("✓ Prompts unchanged")
        self._refresh_prompt_editor_feedback()

    def _persist_custom_prompt_overrides(self, data_to_save: dict, log) -> None:
        from utils.custom_prompts import save_custom_prompts_state

        try:
            self._prompts_loaded_data = save_custom_prompts_state(data_to_save)
            log.info(f"Saved custom prompts for: {self._custom_prompt_saved_items(data_to_save)}")
            self._set_prompts_status_text("✓ Prompts saved")
        except Exception as e:
            log.warning(f"Could not save custom prompts: {e}")
            self._set_prompts_status_text(f"Error: {e}")
        self._refresh_prompt_editor_feedback()

    def _custom_prompt_saved_items(self, data_to_save: dict) -> list[str]:
        saved_items = sorted(data_to_save.get("prompts", {}).keys())
        if "voice_commands" in data_to_save:
            saved_items.append("Voice Commands")
        if "app_contexts" in data_to_save:
            saved_items.append("App Mappings")
        return saved_items

    def _reset_custom_prompts_to_defaults(self, log) -> None:
        from utils.custom_prompts import reset_to_defaults

        reset_to_defaults()
        self._get_loaded_prompts_data(force=True)
        log.info("All prompts reset to defaults, removed prompts.toml")
        self._set_prompts_status_text("✓ Reset to defaults")
        self._refresh_prompt_editor_feedback()

    def _restart_application(self) -> None:
        """Speichert Settings und startet die Applikation neu."""
        import logging
        import subprocess
        import sys

        from AppKit import NSApp  # type: ignore[import-not-found]

        log = logging.getLogger(__name__)

        # Erst alle Settings speichern
        self._save_all_settings()
        log.info("Restarting application...")

        # Kurze Verzögerung für UI-Feedback, dann Neustart
        from Foundation import NSTimer  # type: ignore[import-not-found]

        def do_restart():
            # Neuen Prozess starten (detached)
            python = sys.executable
            subprocess.Popen(
                [python] + sys.argv,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Aktuellen Prozess beenden
            NSApp.terminate_(None)

        # Kleine Verzögerung damit "Saved!" noch angezeigt wird
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.5, False, lambda _: do_restart()
        )

    # =============================================================================
    # Hotkey Recording (delegated to HotkeyCard)
    # =============================================================================

    def _handle_hotkey_action(self, action: str) -> None:
        """Handles actions from HotkeyCard (presets and recording)."""
        if not self._hotkey_card:
            return

        # Preset buttons
        if action in ("hotkey_f19_toggle", "hotkey_fn_hold", "hotkey_opt_space"):
            preset = action.replace("hotkey_", "")
            self._hotkey_card.apply_preset(preset)
            return

        # Record buttons
        if action.startswith("record_hotkey:"):
            kind = action.split(":", 1)[1].strip().lower()
            if kind in ("toggle", "hold"):
                self._hotkey_card.toggle_recording(kind)

    def _apply_hotkey_change(self, kind: str, hotkey_str: str) -> bool:
        from utils.hotkey_validation import validate_hotkey_change
        from utils.permissions import is_permission_related_message

        normalized, level, message = validate_hotkey_change(kind, hotkey_str)
        if level == "error":
            # No permission-related popups: the Setup → Permissions card covers this.
            if not is_permission_related_message(message):
                from utils.alerts import show_error_alert

                show_error_alert(
                    "Ungültiger Hotkey",
                    message or "Hotkey konnte nicht gesetzt werden.",
                )
            if self._hotkey_card:
                self._hotkey_card.set_status("error", message or "")
            return False

        apply_hotkey_setting(kind, normalized)
        cache = getattr(self, "_env_settings_cache", None)
        if cache is not None:
            if kind == "hold":
                cache["PULSESCRIBE_HOLD_HOTKEY"] = normalized
            else:
                cache["PULSESCRIBE_TOGGLE_HOTKEY"] = normalized
            cache.pop("PULSESCRIBE_HOTKEY", None)
            cache.pop("PULSESCRIBE_HOTKEY_MODE", None)

        self._refresh_setup_try_card()

        if callable(self._on_settings_changed_callback):
            try:
                self._on_settings_changed_callback()
            except Exception:
                pass

        if self._hotkey_card:
            if level == "warning":
                self._hotkey_card.set_status("warning", message or "")
            else:
                self._hotkey_card.set_status("ok", "✓ Saved")
        return True

    def _stop_hotkey_recording(self, *, cancelled: bool = False) -> None:
        if self._hotkey_card:
            self._hotkey_card.stop_recording(cancelled=cancelled)


# =============================================================================
# Objective-C Handler Klassen
# =============================================================================


def _create_checkbox_handler_class():
    """Erstellt NSObject-Subklasse für Checkbox."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class CheckboxHandler(NSObject):
        @objc.signature(b"v@:@")
        def toggleStartup_(self, sender) -> None:
            set_show_welcome_on_startup(sender.state() == 1)

    return CheckboxHandler


def _create_simple_handler_class():
    """Generic NSObject handler that calls a controller method by name.

    Usage:
        handler = _SimpleHandler.alloc().initWithController_method_(ctrl, "_refresh_logs")
        btn.setTarget_(handler)
        btn.setAction_(objc.selector(handler.performAction_, signature=b"v@:@"))
    """
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class SimpleHandler(NSObject):
        def initWithController_method_(self, controller, method_name):
            self = objc.super(SimpleHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            self._method_name = method_name
            return self

        @objc.signature(b"v@:@")
        def performAction_(self, _sender) -> None:
            method = getattr(self._controller, self._method_name, None)
            if method:
                method()

    return SimpleHandler


try:
    _SimpleHandler = _create_simple_handler_class()
    _CheckboxHandler = _create_checkbox_handler_class()
except Exception:
    # Keep module import-safe for helper tests on systems without PyObjC/Foundation.
    _SimpleHandler = None
    _CheckboxHandler = None


def _create_slider_handler_class():
    """Erstellt NSObject-Subklasse für Slider Value-Updates.

    Usage:
        handler = _SliderHandler.alloc().initWithLabel_(value_label)
        slider.setTarget_(handler)
        slider.setAction_(objc.selector(handler.sliderChanged_, signature=b"v@:@"))
    """
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class SliderHandler(NSObject):
        def initWithLabel_(self, label):
            self = objc.super(SliderHandler, self).init()
            if self is None:
                return None
            self._label = label
            return self

        @objc.signature(b"v@:@")
        def sliderChanged_(self, sender) -> None:
            if self._label:
                self._label.setStringValue_(str(int(sender.intValue())))

    return SliderHandler


try:
    _SliderHandler = _create_slider_handler_class()
except Exception:
    _SliderHandler = None


def _create_logs_auto_refresh_handler_class():
    """Erstellt NSObject-Subklasse für Auto-Refresh Checkbox (no-op target)."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class LogsAutoRefreshHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(LogsAutoRefreshHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def toggleAutoRefresh_(self, _sender) -> None:
            self._controller._update_logs_auto_refresh_state(reset_cadence=True)

    return LogsAutoRefreshHandler


try:
    _LogsAutoRefreshHandler = _create_logs_auto_refresh_handler_class()
except Exception:
    _LogsAutoRefreshHandler = None


def _create_open_logs_in_finder_handler_class():
    """Erstellt NSObject-Subklasse für Open in Finder Button."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class OpenLogsInFinderHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(OpenLogsInFinderHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def openInFinder_(self, _sender) -> None:
            self._controller._open_logs_in_finder()

    return OpenLogsInFinderHandler


try:
    _OpenLogsInFinderHandler = _create_open_logs_in_finder_handler_class()
except Exception:
    _OpenLogsInFinderHandler = None


def _create_setup_action_handler_class():
    """Erstellt NSObject-Subklasse für Setup/Onboarding Buttons."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class SetupActionHandler(NSObject):
        def initWithController_action_(self, controller, action):
            self = objc.super(SetupActionHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            self._action = action
            return self

        @objc.signature(b"v@:@")
        def performAction_(self, _sender) -> None:
            self._controller._handle_setup_action(self._action)

    return SetupActionHandler


try:
    _SetupActionHandler = _create_setup_action_handler_class()
except Exception:
    _SetupActionHandler = None


def _create_hotkey_action_handler_class():
    """Erstellt NSObject-Subklasse für HotkeyCard Buttons."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class HotkeyActionHandler(NSObject):
        def initWithController_action_(self, controller, action):
            self = objc.super(HotkeyActionHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            self._action = action
            return self

        @objc.signature(b"v@:@")
        def performAction_(self, _sender) -> None:
            self._controller._handle_hotkey_action(self._action)

    return HotkeyActionHandler


try:
    _HotkeyActionHandler = _create_hotkey_action_handler_class()
except Exception:
    _HotkeyActionHandler = None


def _create_text_change_handler_class():
    """Erstellt NSObject-Delegate für NSTextView-Textänderungen."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class TextChangeHandler(NSObject):
        def initWithController_method_(self, controller, method_name):
            self = objc.super(TextChangeHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            self._method_name = method_name
            return self

        @objc.signature(b"v@:@")
        def textDidChange_(self, _notification) -> None:
            method = getattr(self._controller, self._method_name, None)
            if callable(method):
                method()

    return TextChangeHandler


try:
    _TextChangeHandler = _create_text_change_handler_class()
except Exception:
    _TextChangeHandler = None


def _create_tab_selection_handler_class():
    """Erstellt NSObject-Delegate für Lazy-Building beim Tab-Wechsel."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class TabSelectionHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(TabSelectionHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@@")
        def tabView_didSelectTabViewItem_(self, _tab_view, tab_view_item) -> None:
            try:
                label = str(tab_view_item.identifier())
            except Exception:
                label = None
            self._controller._ensure_tab_built(label)
            self._controller._update_logs_auto_refresh_state(
                reset_cadence=(label == "Logs")
            )

    return TabSelectionHandler


try:
    _TabSelectionHandler = _create_tab_selection_handler_class()
except Exception:
    _TabSelectionHandler = None


def _create_logs_segment_handler_class():
    """Erstellt NSObject-Subklasse für Logs/Transcripts Segmented Control."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class LogsSegmentHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(LogsSegmentHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def segmentChanged_(self, sender) -> None:
            segment = sender.selectedSegment()
            self._controller._switch_logs_segment(segment)

    return LogsSegmentHandler


try:
    _LogsSegmentHandler = _create_logs_segment_handler_class()
except Exception:
    _LogsSegmentHandler = None


def _create_clear_transcripts_handler_class():
    """Erstellt NSObject-Subklasse für Clear/Refresh Transcripts Buttons."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class ClearTranscriptsHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(ClearTranscriptsHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def clearTranscripts_(self, _sender) -> None:
            self._controller._clear_transcripts()

        @objc.signature(b"v@:@")
        def refreshTranscripts_(self, _sender) -> None:
            self._controller._refresh_transcripts_on_demand()

    return ClearTranscriptsHandler


try:
    _ClearTranscriptsHandler = _create_clear_transcripts_handler_class()
except Exception:
    _ClearTranscriptsHandler = None
