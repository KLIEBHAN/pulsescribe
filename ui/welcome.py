"""Welcome/Setup Window für WhisperGo.

Zeigt Onboarding-Informationen, API-Key-Setup und Feature-Übersicht.
Erscheint beim ersten Start und kann über Menubar aufgerufen werden.
"""

import os

from utils.preferences import (
    get_api_key,
    get_show_welcome_on_startup,
    save_api_key,
    set_onboarding_seen,
    set_show_welcome_on_startup,
)

# Window-Konfiguration
WELCOME_WIDTH = 480
WELCOME_HEIGHT = 640
WELCOME_PADDING = 20
CARD_PADDING = 16
CARD_CORNER_RADIUS = 12
CARD_SPACING = 14


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
        y_pos = self._build_info_cards(y_pos)
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
        """Erstellt Hotkey-Karte."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightBold,
            NSFontWeightMedium,
            NSMakeRect,
            NSTextAlignmentCenter,
            NSTextField,
        )

        card_height = 70
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height - CARD_SPACING

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        self._content_view.addSubview_(card)

        # Hotkey groß und zentriert
        hotkey_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(WELCOME_PADDING, card_y + 32, card_width, 28)
        )
        hotkey_label.setStringValue_(self.hotkey.upper())
        hotkey_label.setBezeled_(False)
        hotkey_label.setDrawsBackground_(False)
        hotkey_label.setEditable_(False)
        hotkey_label.setSelectable_(False)
        hotkey_label.setAlignment_(NSTextAlignmentCenter)
        hotkey_label.setFont_(NSFont.systemFontOfSize_weight_(22, NSFontWeightBold))
        hotkey_label.setTextColor_(_get_color(100, 200, 255))  # Hellblau
        self._content_view.addSubview_(hotkey_label)

        # Beschreibung
        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(WELCOME_PADDING, card_y + 12, card_width, 16)
        )
        desc.setStringValue_("Press to start/stop recording")
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setAlignment_(NSTextAlignmentCenter)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.5))
        self._content_view.addSubview_(desc)

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
        """Erstellt kompakte API-Key-Zeile."""
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSMakeRect,
            NSSecureTextField,
            NSTextField,
        )
        import objc  # type: ignore[import-not-found]

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

        # Textfeld
        field_width = WELCOME_WIDTH - 2 * WELCOME_PADDING - 2 * CARD_PADDING - 70
        field = NSSecureTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, y, field_width, 22)
        )
        field.setPlaceholderString_("Enter API key...")
        field.setFont_(NSFont.systemFontOfSize_(11))

        existing_key = get_api_key(key_name) or os.getenv(key_name)
        if existing_key:
            field.setStringValue_(existing_key)

        self._content_view.addSubview_(field)

        # Status
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

        # Save-Button
        save_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(WELCOME_WIDTH - WELCOME_PADDING - CARD_PADDING - 44, y, 44, 22)
        )
        save_btn.setTitle_("Save")
        save_btn.setBezelStyle_(NSBezelStyleRounded)
        save_btn.setFont_(NSFont.systemFontOfSize_(10))

        handler = _SaveButtonHandler.alloc().initWithKeyName_field_status_(
            key_name, field, status
        )
        save_btn.setTarget_(handler)
        save_btn.setAction_(objc.selector(handler.saveKey_, signature=b"v@:@"))

        if is_deepgram:
            self._deepgram_handler = handler
            self._deepgram_field = field
            self._deepgram_status = status
        else:
            self._groq_handler = handler
            self._groq_field = field
            self._groq_status = status

        self._content_view.addSubview_(save_btn)

    def _build_info_cards(self, y: int) -> int:
        """Erstellt Settings und Features nebeneinander."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSTextField,
        )

        card_height = 130
        card_width = (WELCOME_WIDTH - 2 * WELCOME_PADDING - CARD_SPACING) // 2
        card_y = y - card_height

        # Settings-Karte (links)
        settings_card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        self._content_view.addSubview_(settings_card)

        settings_title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                WELCOME_PADDING + 12, card_y + card_height - 26, card_width - 24, 16
            )
        )
        settings_title.setStringValue_("⚙️ Settings")
        settings_title.setBezeled_(False)
        settings_title.setDrawsBackground_(False)
        settings_title.setEditable_(False)
        settings_title.setSelectable_(False)
        settings_title.setFont_(
            NSFont.systemFontOfSize_weight_(12, NSFontWeightSemibold)
        )
        settings_title.setTextColor_(NSColor.whiteColor())
        self._content_view.addSubview_(settings_title)

        # Settings-Inhalt
        refine_status = "✓" if self.config.get("refine") else "✗"
        refine_color = (
            _get_color(51, 217, 178)
            if self.config.get("refine")
            else _get_color(255, 82, 82, 0.7)
        )
        settings_items = [
            (f"Refine: {refine_status}", refine_color),
            (f"Language: {self.config.get('language') or 'auto'}", None),
            (f"Provider: {(self.config.get('mode') or 'deepgram').title()}", None),
        ]

        item_y = card_y + card_height - 48
        for text, color in settings_items:
            item = NSTextField.alloc().initWithFrame_(
                NSMakeRect(WELCOME_PADDING + 12, item_y, card_width - 24, 14)
            )
            item.setStringValue_(text)
            item.setBezeled_(False)
            item.setDrawsBackground_(False)
            item.setEditable_(False)
            item.setSelectable_(False)
            item.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
            item.setTextColor_(
                color or NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7)
            )
            self._content_view.addSubview_(item)
            item_y -= 18

        # Features-Karte (rechts)
        features_x = WELCOME_PADDING + card_width + CARD_SPACING
        features_card = _create_card(features_x, card_y, card_width, card_height)
        self._content_view.addSubview_(features_card)

        features_title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(features_x + 12, card_y + card_height - 26, card_width - 24, 16)
        )
        features_title.setStringValue_("✨ Features")
        features_title.setBezeled_(False)
        features_title.setDrawsBackground_(False)
        features_title.setEditable_(False)
        features_title.setSelectable_(False)
        features_title.setFont_(
            NSFont.systemFontOfSize_weight_(12, NSFontWeightSemibold)
        )
        features_title.setTextColor_(NSColor.whiteColor())
        self._content_view.addSubview_(features_title)

        # Features-Inhalt
        features = [
            "~300ms latency",
            "LLM grammar fix",
            "Context-aware",
            "Voice commands",
        ]
        item_y = card_y + card_height - 48
        for feature in features:
            item = NSTextField.alloc().initWithFrame_(
                NSMakeRect(features_x + 12, item_y, card_width - 24, 14)
            )
            item.setStringValue_(f"• {feature}")
            item.setBezeled_(False)
            item.setDrawsBackground_(False)
            item.setEditable_(False)
            item.setSelectable_(False)
            item.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
            item.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
            self._content_view.addSubview_(item)
            item_y -= 18

        return card_y - CARD_SPACING

    def _build_footer(self) -> None:
        """Erstellt Footer mit Checkbox und Start-Button."""
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelStyleRounded,
            NSButton,
            NSButtonTypeSwitch,
            NSFont,
            NSFontWeightSemibold,
            NSMakeRect,
        )
        import objc  # type: ignore[import-not-found]

        footer_y = WELCOME_PADDING

        # Checkbox
        checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(WELCOME_PADDING, footer_y + 6, 140, 18)
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

        # Start-Button (prominent, mit Akzentfarbe)
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


# =============================================================================
# Objective-C Handler Klassen
# =============================================================================


def _create_save_handler_class():
    """Erstellt NSObject-Subklasse für Save-Button."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]

    class SaveButtonHandler(NSObject):
        def initWithKeyName_field_status_(self, key_name, field, status):
            self = objc.super(SaveButtonHandler, self).init()
            if self is None:
                return None
            self._key_name = key_name
            self._field = field
            self._status = status
            return self

        @objc.signature(b"v@:@")
        def saveKey_(self, _sender) -> None:
            value = self._field.stringValue()
            if value:
                save_api_key(self._key_name, value)
                self._status.setStringValue_("✓")
                self._status.setTextColor_(_get_color(51, 217, 178))
            else:
                self._status.setStringValue_("✗")
                self._status.setTextColor_(_get_color(255, 82, 82, 0.7))

    return SaveButtonHandler


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


_SaveButtonHandler = _create_save_handler_class()
_CheckboxHandler = _create_checkbox_handler_class()
_StartButtonHandler = _create_start_handler_class()
