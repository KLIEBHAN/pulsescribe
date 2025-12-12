"""Welcome/Setup Window für WhisperGo.

Zeigt Onboarding-Informationen, API-Key-Setup und Feature-Übersicht.
Erscheint beim ersten Start und kann über Menubar aufgerufen werden.
"""

import os

from utils.preferences import (
    get_api_key,
    get_env_setting,
    get_show_welcome_on_startup,
    remove_env_setting,
    save_api_key,
    save_env_setting,
    set_onboarding_seen,
    set_show_welcome_on_startup,
)
from utils.hotkey import KEY_CODE_MAP

# Window-Konfiguration
WELCOME_WIDTH = 500
WELCOME_HEIGHT = 825  # Erhöht für 4 API-Key-Felder
WELCOME_PADDING = 20
CARD_PADDING = 16
CARD_CORNER_RADIUS = 12
CARD_SPACING = 12

# Verfügbare Optionen für Dropdowns
MODE_OPTIONS = ["deepgram", "openai", "groq", "local"]
REFINE_PROVIDER_OPTIONS = ["groq", "openai", "openrouter"]
LANGUAGE_OPTIONS = ["auto", "de", "en", "es", "fr", "it", "pt", "nl", "pl", "ru", "zh"]
LOCAL_BACKEND_OPTIONS = ["whisper", "faster", "auto"]
LOCAL_MODEL_OPTIONS = ["default", "turbo", "large", "medium", "small", "base", "tiny"]
HOTKEY_MODE_OPTIONS = ["toggle", "hold"]


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


class WelcomeController:
    """Welcome/Setup Window für WhisperGo."""

    def __init__(self, hotkey: str, config: dict):
        self.hotkey = hotkey
        self.config = config
        self._window = None
        self._content_view = None
        self._on_start_callback = None
        self._on_settings_changed_callback = None

        # UI-Referenzen (werden in _build_* Methoden gesetzt)
        self._startup_checkbox = None
        self._hotkey_field = None
        self._record_btn = None
        self._mode_popup = None
        self._lang_popup = None
        self._refine_checkbox = None
        self._provider_popup = None
        self._model_field = None
        self._hotkey_mode_popup = None
        self._local_backend_popup = None
        self._local_model_popup = None
        self._local_backend_label = None
        self._local_model_label = None
        self._mode_changed_handler = None
        self._save_btn = None
        self._restart_handler = None
        self._record_handler = None
        self._hotkey_recording = False
        self._hotkey_monitor = None
        # API-Key-Felder werden dynamisch via setattr gesetzt:
        # _{provider}_field, _{provider}_status für deepgram, groq, openai, openrouter

        self._build_window()

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
        self._window.setTitle_("WhisperGo Setup")
        self._window.setReleasedWhenClosed_(False)

        # Visual Effect View (HUD-Material)
        content_frame = NSMakeRect(0, 0, WELCOME_WIDTH, WELCOME_HEIGHT)
        visual_effect = NSVisualEffectView.alloc().initWithFrame_(content_frame)
        visual_effect.setMaterial_(13)  # HUD Window
        visual_effect.setBlendingMode_(0)
        visual_effect.setState_(1)
        self._window.setContentView_(visual_effect)
        self._content_view = visual_effect

        # Sections von oben nach unten
        y_pos = WELCOME_HEIGHT - WELCOME_PADDING

        y_pos = self._build_header(y_pos)
        y_pos = self._build_hotkey_card(y_pos)
        y_pos = self._build_api_card(y_pos)
        y_pos = self._build_settings_card(y_pos)
        self._build_footer()

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
        title.setStringValue_("🎤 WhisperGo")
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

    def _build_hotkey_card(self, y: int) -> int:
        """Erstellt Hotkey-Karte mit Eingabefeld und Restart-Button."""
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSPopUpButton,
            NSTextAlignmentCenter,
            NSTextField,
        )
        import objc  # type: ignore[import-not-found]

        # 2 Zeilen: Hotkey + Hotkey Mode
        card_height = 113
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height - CARD_SPACING

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        self._content_view.addSubview_(card)

        # Section-Titel
        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                WELCOME_PADDING + CARD_PADDING, card_y + card_height - 28, 200, 18
            )
        )
        title.setStringValue_("⌨️ Hotkey")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        self._content_view.addSubview_(title)

        # Beschreibung
        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                WELCOME_PADDING + CARD_PADDING, card_y + card_height - 46, 350, 14
            )
        )
        desc.setStringValue_("Press to start/stop recording")
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.5))
        self._content_view.addSubview_(desc)

        # Hotkey-Eingabefeld (mit Platz für Record + Restart Button)
        button_width = 60
        button_spacing = 8
        buttons_total = button_width * 2 + button_spacing
        field_width = card_width - 2 * CARD_PADDING - buttons_total
        field_y = card_y + 42
        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(WELCOME_PADDING + CARD_PADDING, field_y, field_width, 24)
        )
        current_hotkey = get_env_setting("WHISPER_GO_HOTKEY") or self.hotkey
        field.setStringValue_(current_hotkey.upper())
        field.setFont_(NSFont.systemFontOfSize_(13))
        field.setAlignment_(NSTextAlignmentCenter)
        self._content_view.addSubview_(field)
        self._hotkey_field = field

        # Record-Button
        record_x = (
            WELCOME_WIDTH
            - WELCOME_PADDING
            - CARD_PADDING
            - (button_width * 2 + button_spacing)
        )
        record_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(record_x, field_y, button_width, 24)
        )
        record_btn.setTitle_("Record")
        record_btn.setBezelStyle_(NSBezelStyleRounded)
        record_btn.setFont_(NSFont.systemFontOfSize_(11))

        record_handler = _RecordHotkeyHandler.alloc().initWithController_(self)
        record_btn.setTarget_(record_handler)
        record_btn.setAction_(
            objc.selector(record_handler.recordHotkey_, signature=b"v@:@")
        )
        self._record_handler = record_handler
        self._record_btn = record_btn
        self._content_view.addSubview_(record_btn)

        # Restart-Button
        restart_x = record_x + button_width + button_spacing
        restart_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(restart_x, field_y, button_width, 24)
        )
        restart_btn.setTitle_("Restart")
        restart_btn.setBezelStyle_(NSBezelStyleRounded)
        restart_btn.setFont_(NSFont.systemFontOfSize_(11))

        restart_handler = _RestartButtonHandler.alloc().initWithController_(self)
        restart_btn.setTarget_(restart_handler)
        restart_btn.setAction_(
            objc.selector(restart_handler.restartApp_, signature=b"v@:@")
        )
        self._restart_handler = restart_handler
        self._content_view.addSubview_(restart_btn)

        # Hotkey Mode Dropdown (unter Hotkey)
        base_x = WELCOME_PADDING + CARD_PADDING
        label_width = 110
        control_x = base_x + label_width + 8
        control_width = card_width - 2 * CARD_PADDING - label_width - 8

        self._add_setting_label(base_x, card_y + 12, "Hotkey Mode:")
        hotkey_mode_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, card_y + 10, control_width, 22)
        )
        hotkey_mode_popup.setFont_(NSFont.systemFontOfSize_(11))
        for m in HOTKEY_MODE_OPTIONS:
            hotkey_mode_popup.addItemWithTitle_(m)
        current_hotkey_mode = get_env_setting("WHISPER_GO_HOTKEY_MODE") or "toggle"
        if current_hotkey_mode not in HOTKEY_MODE_OPTIONS:
            current_hotkey_mode = "toggle"
        hotkey_mode_popup.selectItemWithTitle_(current_hotkey_mode)
        self._hotkey_mode_popup = hotkey_mode_popup
        self._content_view.addSubview_(hotkey_mode_popup)

        return card_y - CARD_SPACING

    def _build_api_card(self, y: int) -> int:
        """Erstellt API-Konfigurationskarte."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        # 4 API Keys: Deepgram, Groq, OpenAI, OpenRouter
        card_height = 265
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        self._content_view.addSubview_(card)

        # Section-Titel
        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                WELCOME_PADDING + CARD_PADDING, card_y + card_height - 30, 200, 18
            )
        )
        title.setStringValue_("🔑 API Keys")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        self._content_view.addSubview_(title)

        # API Key Zeilen (von oben nach unten)
        row_y = card_y + card_height - 70
        row_spacing = 54

        # Deepgram (required for transcription)
        self._build_api_row_compact(row_y, "Deepgram", "DEEPGRAM_API_KEY", "deepgram")
        row_y -= row_spacing

        # Groq (for refine)
        self._build_api_row_compact(row_y, "Groq", "GROQ_API_KEY", "groq")
        row_y -= row_spacing

        # OpenAI (for refine)
        self._build_api_row_compact(row_y, "OpenAI", "OPENAI_API_KEY", "openai")
        row_y -= row_spacing

        # OpenRouter (for refine)
        self._build_api_row_compact(
            row_y, "OpenRouter", "OPENROUTER_API_KEY", "openrouter"
        )

        return card_y - CARD_SPACING

    def _build_api_row_compact(
        self, y: int, label_text: str, key_name: str, provider: str
    ) -> None:
        """Erstellt kompakte API-Key-Zeile (ohne Save-Button, mit Copy/Paste)."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSMakeRect,
            NSTextField,
        )

        base_x = WELCOME_PADDING + CARD_PADDING

        # Label
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(base_x, y + 22, 120, 14))
        label.setStringValue_(label_text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        self._content_view.addSubview_(label)

        # Textfeld (normales NSTextField für Copy/Paste Support)
        field_width = WELCOME_WIDTH - 2 * WELCOME_PADDING - 2 * CARD_PADDING - 24
        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, y, field_width, 22)
        )
        field.setPlaceholderString_("Enter API key...")
        field.setFont_(NSFont.systemFontOfSize_(11))

        existing_key = get_api_key(key_name) or os.getenv(key_name)
        if existing_key:
            field.setStringValue_(existing_key)

        self._content_view.addSubview_(field)

        # Status-Indicator
        has_key = bool(existing_key) or self.config.get(f"{provider}_key", False)
        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x + field_width + 6, y + 2, 18, 18)
        )
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_(13))
        status.setStringValue_("✓" if has_key else "✗")
        status.setTextColor_(
            _get_color(51, 217, 178) if has_key else _get_color(255, 82, 82, 0.7)
        )
        self._content_view.addSubview_(status)

        # Referenzen speichern für Save
        setattr(self, f"_{provider}_field", field)
        setattr(self, f"_{provider}_status", status)

    def _build_settings_card(self, y: int) -> int:
        """Erstellt Settings-Karte mit konfigurierbaren Optionen."""
        from AppKit import (  # type: ignore[import-not-found]
            NSButton,
            NSButtonTypeSwitch,
            NSColor,
            NSFont,
            NSFontWeightSemibold,
            NSMakeRect,
            NSPopUpButton,
            NSTextField,
        )
        import objc  # type: ignore[import-not-found]

        card_height = 248
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        self._content_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        label_width = 110
        control_x = base_x + label_width + 8
        control_width = card_width - 2 * CARD_PADDING - label_width - 8

        # Section-Titel
        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 28, 200, 18)
        )
        title.setStringValue_("⚙️ Settings")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        self._content_view.addSubview_(title)

        row_height = 28
        current_y = card_y + card_height - 58

        # --- Mode Dropdown ---
        self._add_setting_label(base_x, current_y, "Mode:")
        mode_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        mode_popup.setFont_(NSFont.systemFontOfSize_(11))
        for mode in MODE_OPTIONS:
            mode_popup.addItemWithTitle_(mode)
        current_mode = (
            get_env_setting("WHISPER_GO_MODE") or self.config.get("mode") or "deepgram"
        )
        if current_mode in MODE_OPTIONS:
            mode_popup.selectItemWithTitle_(current_mode)
        # Update visibility when mode changes
        mode_changed_handler = _ModeChangedHandler.alloc().initWithController_(self)
        mode_popup.setTarget_(mode_changed_handler)
        mode_popup.setAction_(
            objc.selector(mode_changed_handler.modeChanged_, signature=b"v@:@")
        )
        self._mode_changed_handler = mode_changed_handler
        self._mode_popup = mode_popup
        self._content_view.addSubview_(mode_popup)
        current_y -= row_height

        # --- Local Backend Dropdown ---
        self._local_backend_label = self._add_setting_label(
            base_x, current_y, "Local Backend:"
        )
        local_backend_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        local_backend_popup.setFont_(NSFont.systemFontOfSize_(11))
        for backend in LOCAL_BACKEND_OPTIONS:
            local_backend_popup.addItemWithTitle_(backend)
        current_backend = get_env_setting("WHISPER_GO_LOCAL_BACKEND") or "whisper"
        if current_backend not in LOCAL_BACKEND_OPTIONS:
            current_backend = "whisper"
        local_backend_popup.selectItemWithTitle_(current_backend)
        self._local_backend_popup = local_backend_popup
        self._content_view.addSubview_(local_backend_popup)
        current_y -= row_height

        # --- Local Model Dropdown ---
        self._local_model_label = self._add_setting_label(
            base_x, current_y, "Local Model:"
        )
        local_model_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        local_model_popup.setFont_(NSFont.systemFontOfSize_(11))
        for m in LOCAL_MODEL_OPTIONS:
            local_model_popup.addItemWithTitle_(m)
        current_local_model = get_env_setting("WHISPER_GO_LOCAL_MODEL") or "default"
        if current_local_model not in LOCAL_MODEL_OPTIONS and current_local_model:
            local_model_popup.addItemWithTitle_(current_local_model)
        local_model_popup.selectItemWithTitle_(current_local_model)
        self._local_model_popup = local_model_popup
        self._content_view.addSubview_(local_model_popup)
        current_y -= row_height

        # --- Language Dropdown ---
        self._add_setting_label(base_x, current_y, "Language:")
        lang_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        lang_popup.setFont_(NSFont.systemFontOfSize_(11))
        for lang in LANGUAGE_OPTIONS:
            lang_popup.addItemWithTitle_(lang)
        current_lang = (
            get_env_setting("WHISPER_GO_LANGUAGE")
            or self.config.get("language")
            or "auto"
        )
        if current_lang in LANGUAGE_OPTIONS:
            lang_popup.selectItemWithTitle_(current_lang)
        self._lang_popup = lang_popup
        self._content_view.addSubview_(lang_popup)
        current_y -= row_height

        # --- Refine Checkbox ---
        self._add_setting_label(base_x, current_y, "Refine:")
        refine_checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        refine_checkbox.setButtonType_(NSButtonTypeSwitch)
        refine_checkbox.setTitle_("Enable LLM post-processing")
        refine_checkbox.setFont_(NSFont.systemFontOfSize_(11))
        refine_enabled = get_env_setting("WHISPER_GO_REFINE")
        if refine_enabled is None:
            refine_enabled = self.config.get("refine", False)
        else:
            refine_enabled = refine_enabled.lower() == "true"
        refine_checkbox.setState_(1 if refine_enabled else 0)
        self._refine_checkbox = refine_checkbox
        self._content_view.addSubview_(refine_checkbox)
        current_y -= row_height

        # --- Refine Provider Dropdown ---
        self._add_setting_label(base_x, current_y, "Refine Provider:")
        provider_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        provider_popup.setFont_(NSFont.systemFontOfSize_(11))
        for provider in REFINE_PROVIDER_OPTIONS:
            provider_popup.addItemWithTitle_(provider)
        current_provider = (
            get_env_setting("WHISPER_GO_REFINE_PROVIDER")
            or self.config.get("refine_provider")
            or "groq"
        )
        if current_provider in REFINE_PROVIDER_OPTIONS:
            provider_popup.selectItemWithTitle_(current_provider)
        self._provider_popup = provider_popup
        self._content_view.addSubview_(provider_popup)
        current_y -= row_height

        # --- Refine Model Textfeld (volle Breite ohne Save-Button) ---
        self._add_setting_label(base_x, current_y, "Refine Model:")
        model_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        model_field.setFont_(NSFont.systemFontOfSize_(11))
        model_field.setPlaceholderString_("e.g. llama-3.3-70b-versatile")
        current_model = (
            get_env_setting("WHISPER_GO_REFINE_MODEL")
            or self.config.get("refine_model")
            or "openai/gpt-oss-120b"  # Default Model
        )
        model_field.setStringValue_(current_model)
        self._model_field = model_field
        self._content_view.addSubview_(model_field)

        self._update_local_settings_visibility()

        return card_y - CARD_SPACING

    def _add_setting_label(self, x: int, y: int, text: str):
        """Erstellt ein Label für eine Einstellung."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSMakeRect,
            NSTextField,
        )

        label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y + 2, 110, 16))
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        self._content_view.addSubview_(label)
        return label

    def _update_local_settings_visibility(self) -> None:
        """Blendet Local-spezifische Einstellungen je nach Mode ein/aus."""
        if not self._mode_popup:
            return
        is_local = self._mode_popup.titleOfSelectedItem() == "local"
        for view in (
            self._local_backend_label,
            self._local_backend_popup,
            self._local_model_label,
            self._local_model_popup,
        ):
            if view is not None:
                view.setHidden_(not is_local)

    def _build_footer(self) -> None:
        """Erstellt Footer mit Checkbox, Save-Button und Start-Button."""
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSButtonTypeSwitch,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
        )
        import objc  # type: ignore[import-not-found]

        footer_y = WELCOME_PADDING

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

        # Save & Apply Button (Mitte)
        save_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(WELCOME_PADDING + 140, footer_y + 2, 100, 28)
        )
        save_btn.setTitle_("Save & Apply")
        save_btn.setBezelStyle_(NSBezelStyleRounded)
        save_btn.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))

        save_handler = _SaveAllHandler.alloc().initWithController_(self)
        save_btn.setTarget_(save_handler)
        save_btn.setAction_(objc.selector(save_handler.saveAll_, signature=b"v@:@"))
        self._save_all_handler = save_handler
        self._save_btn = save_btn
        self._content_view.addSubview_(save_btn)

        # Start-Button (prominent, rechts)
        start_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(WELCOME_WIDTH - WELCOME_PADDING - 140, footer_y, 140, 32)
        )
        start_btn.setTitle_("Start WhisperGo")
        start_btn.setBezelStyle_(NSBezelStyleRounded)
        start_btn.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))

        start_handler = _StartButtonHandler.alloc().initWithController_(self)
        start_btn.setTarget_(start_handler)
        start_btn.setAction_(objc.selector(start_handler.startApp_, signature=b"v@:@"))
        self._start_handler = start_handler

        self._content_view.addSubview_(start_btn)

    def set_on_start_callback(self, callback) -> None:
        """Setzt Callback für Start-Button."""
        self._on_start_callback = callback

    def set_on_settings_changed(self, callback) -> None:
        """Setzt Callback der aufgerufen wird wenn Settings gespeichert werden."""
        self._on_settings_changed_callback = callback

    def show(self) -> None:
        """Zeigt Window (nicht-modal)."""
        if self._window:
            self._window.makeKeyAndOrderFront_(None)
            self._window.center()
            from AppKit import NSApp  # type: ignore[import-not-found]

            NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Schließt Window und markiert Onboarding als gesehen."""
        set_onboarding_seen(True)
        if self._window:
            self._window.close()

    def _handle_start(self) -> None:
        """Handler für Start-Button."""
        set_onboarding_seen(True)
        if self._on_start_callback:
            self._on_start_callback()
        self.close()

    def _save_all_settings(self) -> None:
        """Speichert alle Einstellungen in die .env Datei."""
        import logging

        log = logging.getLogger(__name__)

        # Hotkey
        if self._hotkey_field:
            hotkey = self._hotkey_field.stringValue().strip()
            if hotkey:
                save_env_setting("WHISPER_GO_HOTKEY", hotkey.lower())

        # API Keys (alle 4 Provider)
        api_keys = [
            ("deepgram", "DEEPGRAM_API_KEY"),
            ("groq", "GROQ_API_KEY"),
            ("openai", "OPENAI_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
        ]
        for provider, env_key in api_keys:
            field = getattr(self, f"_{provider}_field", None)
            status = getattr(self, f"_{provider}_status", None)
            if field and status:
                key = field.stringValue().strip()
                if key:
                    save_api_key(env_key, key)
                    status.setStringValue_("✓")
                    status.setTextColor_(_get_color(51, 217, 178))
                else:
                    status.setStringValue_("✗")
                    status.setTextColor_(_get_color(255, 82, 82, 0.7))

        # Mode
        if self._mode_popup:
            mode = self._mode_popup.titleOfSelectedItem()
            if mode:
                save_env_setting("WHISPER_GO_MODE", mode)

        # Hotkey Mode
        if self._hotkey_mode_popup:
            hotkey_mode = self._hotkey_mode_popup.titleOfSelectedItem()
            if hotkey_mode == "toggle":
                remove_env_setting("WHISPER_GO_HOTKEY_MODE")
            elif hotkey_mode:
                save_env_setting("WHISPER_GO_HOTKEY_MODE", hotkey_mode)

        # Local Backend
        if self._local_backend_popup:
            backend = self._local_backend_popup.titleOfSelectedItem()
            if backend == "whisper":
                remove_env_setting("WHISPER_GO_LOCAL_BACKEND")
            elif backend:
                save_env_setting("WHISPER_GO_LOCAL_BACKEND", backend)

        # Local Model
        if self._local_model_popup:
            local_model = self._local_model_popup.titleOfSelectedItem()
            if local_model == "default":
                remove_env_setting("WHISPER_GO_LOCAL_MODEL")
            elif local_model:
                save_env_setting("WHISPER_GO_LOCAL_MODEL", local_model)

        # Language
        if self._lang_popup:
            lang = self._lang_popup.titleOfSelectedItem()
            if lang == "auto":
                remove_env_setting("WHISPER_GO_LANGUAGE")
            elif lang:
                save_env_setting("WHISPER_GO_LANGUAGE", lang)

        # Refine
        if self._refine_checkbox:
            enabled = self._refine_checkbox.state() == 1
            save_env_setting("WHISPER_GO_REFINE", "true" if enabled else "false")

        # Refine Provider
        if self._provider_popup:
            provider = self._provider_popup.titleOfSelectedItem()
            if provider:
                save_env_setting("WHISPER_GO_REFINE_PROVIDER", provider)

        # Refine Model
        if self._model_field:
            model = self._model_field.stringValue().strip()
            if model:
                save_env_setting("WHISPER_GO_REFINE_MODEL", model)
            else:
                remove_env_setting("WHISPER_GO_REFINE_MODEL")

        log.info("All settings saved to .env file")

        # Callback aufrufen damit Daemon Settings neu lädt
        if self._on_settings_changed_callback:
            self._on_settings_changed_callback()

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
    # Hotkey Recording
    # =============================================================================

    def _toggle_hotkey_recording(self) -> None:
        """Startet/stoppt Hotkey-Aufnahme im Setup-Panel."""
        if self._hotkey_recording:
            self._stop_hotkey_recording()
        else:
            self._start_hotkey_recording()

    def _start_hotkey_recording(self) -> None:
        from AppKit import (  # type: ignore[import-not-found]
            NSEvent,
            NSEventMaskKeyDown,
            NSEventMaskFlagsChanged,
            NSEventTypeFlagsChanged,
        )

        if not self._hotkey_field or not self._record_btn:
            return
        self._hotkey_recording = True
        self._record_btn.setTitle_("Press…")
        self._hotkey_field.setStringValue_("")
        self._hotkey_field.setPlaceholderString_("Press desired hotkey…")

        reverse_map = {v: k for k, v in KEY_CODE_MAP.items()}

        def event_to_hotkey_string(event):
            from AppKit import (  # type: ignore[import-not-found]
                NSEventModifierFlagCommand,
                NSEventModifierFlagShift,
                NSEventModifierFlagOption,
                NSEventModifierFlagControl,
            )

            keycode = int(event.keyCode())

            # Ignore pure modifier flag changes (except Fn/CapsLock)
            if event.type() == NSEventTypeFlagsChanged and keycode not in (63, 57):
                return None

            if keycode == 63:
                key = "fn"
            elif keycode == 57:
                key = "capslock"
            else:
                key = reverse_map.get(keycode)
            if not key:
                chars = event.charactersIgnoringModifiers()
                if chars:
                    key = chars.lower()
            if not key:
                return None

            flags = int(event.modifierFlags())
            mods = []
            if flags & NSEventModifierFlagControl:
                mods.append("ctrl")
            if flags & NSEventModifierFlagOption:
                mods.append("alt")
            if flags & NSEventModifierFlagShift:
                mods.append("shift")
            if flags & NSEventModifierFlagCommand:
                mods.append("cmd")

            return "+".join(mods + [key]) if mods else key

        def handler(event):
            if not self._hotkey_recording:
                return event
            hotkey_str = event_to_hotkey_string(event)
            if hotkey_str:
                self._hotkey_field.setStringValue_(hotkey_str.upper())
                self._stop_hotkey_recording()
                return None
            return event

        mask = NSEventMaskKeyDown | NSEventMaskFlagsChanged
        self._hotkey_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, handler
        )

    def _stop_hotkey_recording(self) -> None:
        from AppKit import NSEvent  # type: ignore[import-not-found]

        self._hotkey_recording = False
        if self._record_btn:
            self._record_btn.setTitle_("Record")
        if self._hotkey_field:
            self._hotkey_field.setPlaceholderString_(None)
        if self._hotkey_monitor is not None:
            try:
                NSEvent.removeMonitor_(self._hotkey_monitor)
            except Exception:
                pass
            self._hotkey_monitor = None


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


def _create_start_handler_class():
    """Erstellt NSObject-Subklasse für Start-Button."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class StartButtonHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(StartButtonHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def startApp_(self, _sender) -> None:
            self._controller._handle_start()

    return StartButtonHandler


_CheckboxHandler = _create_checkbox_handler_class()
_StartButtonHandler = _create_start_handler_class()


def _create_save_all_handler_class():
    """Erstellt NSObject-Subklasse für Save & Apply Button."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class SaveAllHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(SaveAllHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def saveAll_(self, _sender) -> None:
            self._controller._save_all_settings()

    return SaveAllHandler


_SaveAllHandler = _create_save_all_handler_class()


def _create_restart_handler_class():
    """Erstellt NSObject-Subklasse für Restart Button."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class RestartHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(RestartHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def restartApp_(self, _sender) -> None:
            self._controller._restart_application()

    return RestartHandler


_RestartButtonHandler = _create_restart_handler_class()


def _create_record_hotkey_handler_class():
    """Erstellt NSObject-Subklasse für Record-Hotkey Button."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class RecordHotkeyHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(RecordHotkeyHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def recordHotkey_(self, _sender) -> None:
            self._controller._toggle_hotkey_recording()

    return RecordHotkeyHandler


_RecordHotkeyHandler = _create_record_hotkey_handler_class()


def _create_mode_changed_handler_class():
    """Erstellt NSObject-Subklasse für Mode-Dropdown-Änderungen."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class ModeChangedHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(ModeChangedHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def modeChanged_(self, _sender) -> None:
            self._controller._update_local_settings_visibility()

    return ModeChangedHandler


_ModeChangedHandler = _create_mode_changed_handler_class()
