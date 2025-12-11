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

# Window-Konfiguration
WELCOME_WIDTH = 500
WELCOME_HEIGHT = 720
WELCOME_PADDING = 20
CARD_PADDING = 16
CARD_CORNER_RADIUS = 12
CARD_SPACING = 12

# Verfügbare Optionen für Dropdowns
MODE_OPTIONS = ["deepgram", "openai", "groq", "local"]
REFINE_PROVIDER_OPTIONS = ["groq", "openai", "openrouter"]
LANGUAGE_OPTIONS = ["auto", "de", "en", "es", "fr", "it", "pt", "nl", "pl", "ru", "zh"]


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
        self._deepgram_field = None
        self._groq_field = None
        self._deepgram_status = None
        self._groq_status = None
        self._startup_checkbox = None
        self._on_start_callback = None
        # Settings-Controls für Save-All
        self._hotkey_field = None
        self._mode_popup = None
        self._lang_popup = None
        self._refine_checkbox = None
        self._provider_popup = None
        self._model_field = None
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
        """Erstellt Hotkey-Karte mit Eingabefeld."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextAlignmentCenter,
            NSTextField,
        )

        card_height = 85
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
                WELCOME_PADDING + CARD_PADDING, card_y + card_height - 46, 300, 14
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

        # Hotkey-Eingabefeld (volle Breite ohne Save-Button)
        field_width = card_width - 2 * CARD_PADDING
        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(WELCOME_PADDING + CARD_PADDING, card_y + 12, field_width, 24)
        )
        current_hotkey = get_env_setting("WHISPER_GO_HOTKEY") or self.hotkey
        field.setStringValue_(current_hotkey.upper())
        field.setFont_(NSFont.systemFontOfSize_(13))
        field.setAlignment_(NSTextAlignmentCenter)
        self._content_view.addSubview_(field)
        self._hotkey_field = field

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

        card_height = 160
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

        # Deepgram-Zeile (mehr Abstand zum Titel)
        row_y = card_y + card_height - 70
        self._build_api_row_compact(row_y, "Deepgram", "DEEPGRAM_API_KEY", True)

        # Groq-Zeile (mehr Abstand zwischen den Zeilen)
        row_y -= 54
        self._build_api_row_compact(row_y, "Groq (optional)", "GROQ_API_KEY", False)

        return card_y - CARD_SPACING

    def _build_api_row_compact(
        self, y: int, label_text: str, key_name: str, is_deepgram: bool
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
        has_key = bool(existing_key) or self.config.get(
            "deepgram_key" if is_deepgram else "groq_key", False
        )
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

        if is_deepgram:
            self._deepgram_field = field
            self._deepgram_status = status
        else:
            self._groq_field = field
            self._groq_status = status

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

        card_height = 220
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
        self._mode_popup = mode_popup
        self._content_view.addSubview_(mode_popup)
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

        return card_y - CARD_SPACING

    def _add_setting_label(self, x: int, y: int, text: str) -> None:
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

        # API Keys
        if self._deepgram_field:
            key = self._deepgram_field.stringValue().strip()
            if key:
                save_api_key("DEEPGRAM_API_KEY", key)
                self._deepgram_status.setStringValue_("✓")
                self._deepgram_status.setTextColor_(_get_color(51, 217, 178))
            else:
                self._deepgram_status.setStringValue_("✗")
                self._deepgram_status.setTextColor_(_get_color(255, 82, 82, 0.7))

        if self._groq_field:
            key = self._groq_field.stringValue().strip()
            if key:
                save_api_key("GROQ_API_KEY", key)
                self._groq_status.setStringValue_("✓")
                self._groq_status.setTextColor_(_get_color(51, 217, 178))
            else:
                self._groq_status.setStringValue_("✗")
                self._groq_status.setTextColor_(_get_color(255, 82, 82, 0.7))

        # Mode
        if self._mode_popup:
            mode = self._mode_popup.titleOfSelectedItem()
            if mode:
                save_env_setting("WHISPER_GO_MODE", mode)

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
