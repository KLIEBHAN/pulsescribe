"""Welcome/Setup Window für PulseScribe.

Zeigt Onboarding-Informationen, API-Key-Setup und Feature-Übersicht.
Erscheint beim ersten Start und kann über Menubar aufgerufen werden.
"""

import os

from config import LOG_FILE
from ui.hotkey_card import HotkeyCard
from utils.env import parse_bool
from utils.hotkey_recording import HotkeyRecorder
from utils.presets import LOCAL_PRESET_BASE, LOCAL_PRESETS, LOCAL_PRESET_OPTIONS
from utils.preferences import (
    apply_hotkey_setting,
    get_api_key,
    get_env_setting,
    get_show_welcome_on_startup,
    remove_env_setting,
    save_api_key,
    save_env_setting,
    set_onboarding_seen,
    set_show_welcome_on_startup,
)
from utils.vocabulary import load_vocabulary, save_vocabulary

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
REFINE_PROVIDER_OPTIONS = ["groq", "openai", "openrouter"]
LANGUAGE_OPTIONS = ["auto", "de", "en", "es", "fr", "it", "pt", "nl", "pl", "ru", "zh"]
LOCAL_BACKEND_OPTIONS = ["whisper", "faster", "mlx", "lightning", "auto"]
LOCAL_MODEL_OPTIONS = [
    "default",
    "turbo",  # Multilingual, best speed/quality
    "large",  # Multilingual, highest quality
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
        # Streaming toggle (Deepgram)
        self._streaming_label = None
        self._streaming_checkbox = None
        # Display toggles
        self._overlay_checkbox = None
        self._dock_icon_checkbox = None
        self._tab_view = None
        self._vocab_text_view = None
        self._vocab_warning_label = None
        self._logs_text_view = None
        self._logs_scroll_view = None
        self._logs_refresh_handler = None
        self._logs_auto_refresh_handler = None
        self._logs_auto_checkbox = None
        self._logs_auto_refresh_timer = None
        self._logs_finder_handler = None
        # Logs/Transcripts segmented control
        self._logs_segment_control = None
        self._logs_segment_handler = None
        self._logs_container = None
        self._transcripts_container = None
        self._transcripts_text_view = None
        self._transcripts_scroll_view = None
        self._transcripts_clear_handler = None
        self._mode_changed_handler = None
        self._save_btn = None
        self._restart_handler = None
        self._hotkey_card: HotkeyCard | None = None
        self._hotkey_recorder = HotkeyRecorder()
        # Setup/Onboarding Tab
        self._setup_action_handlers = []
        self._setup_permissions_card = None
        self._setup_preset_status_label = None
        self._onboarding_wizard_callback = None
        # API-Key-Felder werden dynamisch via setattr gesetzt:
        # _{provider}_field, _{provider}_status für deepgram, groq, openai, openrouter

        self._build_window()

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
        """Erstellt Tab-View und baut alle Tab-Inhalte."""
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

        self._add_tab(tab_view, "Setup", self._build_setup_tab, content_height)
        self._add_tab(tab_view, "Hotkeys", self._build_hotkeys_tab, content_height)
        self._add_tab(tab_view, "Providers", self._build_providers_tab, content_height)
        self._add_tab(tab_view, "Advanced", self._build_advanced_tab, content_height)
        self._add_tab(tab_view, "Refine", self._build_refine_tab, content_height)
        self._add_tab(tab_view, "Prompts", self._build_prompts_tab, content_height)
        self._add_tab(
            tab_view, "Vocabulary", self._build_vocabulary_tab, content_height
        )
        self._add_tab(tab_view, "Logs", self._build_logs_tab, content_height)
        self._add_tab(tab_view, "About", self._build_about_tab, content_height)

    def _add_tab(self, tab_view, label: str, builder, tab_height: int) -> None:
        from AppKit import NSTabViewItem, NSView  # type: ignore[import-not-found]
        from Foundation import NSMakeRect  # type: ignore[import-not-found]

        item = NSTabViewItem.alloc().initWithIdentifier_(label)
        item.setLabel_(label)
        content = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, WELCOME_WIDTH, tab_height)
        )
        item.setView_(content)
        tab_view.addTabViewItem_(item)
        builder(content, tab_height)

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

        open_privacy_settings(anchor)

    def _handle_setup_action(self, action: str) -> None:
        from utils.permissions import (
            check_accessibility_permission,
            check_input_monitoring_permission,
            check_microphone_permission,
            get_microphone_permission_state,
        )

        if action == "open_onboarding_wizard":
            if callable(self._onboarding_wizard_callback):
                try:
                    self._onboarding_wizard_callback()
                except Exception:
                    pass
            return

        if action == "perm_mic":
            mic_state = get_microphone_permission_state()
            if mic_state == "not_determined":
                check_microphone_permission(show_alert=False, request=True)
            else:
                self._open_privacy_settings("Privacy_Microphone")
            self._kick_setup_permission_auto_refresh()
            return

        if action == "perm_access":
            check_accessibility_permission(show_alert=False, request=True)
            self._open_privacy_settings("Privacy_Accessibility")
            self._kick_setup_permission_auto_refresh()
            return

        if action == "perm_input":
            check_input_monitoring_permission(show_alert=False, request=True)
            self._open_privacy_settings("Privacy_ListenEvent")
            self._kick_setup_permission_auto_refresh()
            return

        if action == "apply_mlx_large_preset":
            self._apply_local_preset("macOS: MLX Balanced (large)")
            if self._setup_preset_status_label is not None:
                self._setup_preset_status_label.setStringValue_(
                    "Preset applied — click 'Save & Apply' to persist."
                )
            return

        if action == "apply_mlx_turbo_preset":
            self._apply_local_preset("macOS: MLX Fast (turbo)")
            if self._setup_preset_status_label is not None:
                self._setup_preset_status_label.setStringValue_(
                    "Preset applied — click 'Save & Apply' to persist."
                )
            return

        if action == "apply_lightning_preset":
            self._apply_local_preset("macOS: Lightning Fast (large-v3)")
            if self._setup_preset_status_label is not None:
                self._setup_preset_status_label.setStringValue_(
                    "Preset applied — click 'Save & Apply' to persist."
                )
            return

        if action == "goto_hotkeys_tab":
            # Tab index 1 = Hotkeys (Setup=0, Hotkeys=1, Providers=2, ...)
            if self._tab_view is not None:
                self._tab_view.selectTabViewItemAtIndex_(1)
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

    def _select_tab(self, label: str) -> None:
        if self._tab_view is None:
            return
        try:
            self._tab_view.selectTabViewItemWithIdentifier_(label)
        except Exception:
            pass

    def _build_setup_permissions_card(self, y: int, parent_view=None) -> int:
        import objc  # type: ignore[import-not-found]
        from ui.permissions_card import PERMISSIONS_DESCRIPTION, PermissionsCard

        parent_view = parent_view or self._content_view

        card_height = 206  # Extra height for 3-line description
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
            NSMakeRect(base_x, card_y + 26, card_width - 2 * CARD_PADDING, 18)
        )
        status.setStringValue_("")
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_(11))
        status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
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

        # Hotkeys aus .env auslesen
        toggle_hk = (get_env_setting("PULSESCRIBE_TOGGLE_HOTKEY") or "").strip().upper()
        hold_hk = (get_env_setting("PULSESCRIBE_HOLD_HOTKEY") or "").strip().upper()

        # Hotkey-Info aufbauen (beide anzeigen wenn gesetzt)
        hotkey_parts = []
        if toggle_hk:
            hotkey_parts.append(f"Toggle: {toggle_hk}")
        if hold_hk:
            hotkey_parts.append(f"Hold: {hold_hk}")

        if hotkey_parts:
            hotkey_info = " • ".join(hotkey_parts)
        else:
            hotkey_info = "No hotkey configured"

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

        # Beschreibung
        body = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 60, content_w, 28)
        )
        body.setStringValue_(
            "Press/hold your hotkey and speak. PulseScribe transcribes and "
            "pastes the text into the frontmost app."
        )
        body.setBezeled_(False)
        body.setDrawsBackground_(False)
        body.setEditable_(False)
        body.setSelectable_(False)
        body.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        body.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7))
        parent_view.addSubview_(body)

        # Footer-Zeile: Button links, Hint rechts
        footer_y = card_y + 12

        # "Change Hotkey" Button
        change_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x, footer_y, 130, 24)
        )
        change_btn.setTitle_("Change Hotkey…")
        change_btn.setBezelStyle_(NSBezelStyleRounded)
        change_btn.setFont_(NSFont.systemFontOfSize_(11))
        h_change = _SetupActionHandler.alloc().initWithController_action_(
            self, "goto_hotkeys_tab"
        )
        change_btn.setTarget_(h_change)
        change_btn.setAction_(objc.selector(h_change.performAction_, signature=b"v@:@"))
        self._setup_action_handlers.append(h_change)
        parent_view.addSubview_(change_btn)

        # Hint rechts vom Button
        hint = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x + 140, footer_y + 5, content_w - 140, 14)
        )
        hint.setStringValue_(
            "Paste fails? Grant Accessibility. Hotkeys fail? Grant Input Monitoring."
        )
        hint.setBezeled_(False)
        hint.setDrawsBackground_(False)
        hint.setEditable_(False)
        hint.setSelectable_(False)
        hint.setFont_(NSFont.systemFontOfSize_(10))
        hint.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.45))
        parent_view.addSubview_(hint)

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
        self._build_refine_card(y_pos, parent_view)

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

        # 4 API Keys: Deepgram, Groq, OpenAI, OpenRouter
        card_height = 265
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

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
        parent_view.addSubview_(title)

        # API Key Zeilen (von oben nach unten)
        row_y = card_y + card_height - 70
        row_spacing = 54

        # Deepgram (required for transcription)
        self._build_api_row_compact(
            row_y, "Deepgram", "DEEPGRAM_API_KEY", "deepgram", parent_view
        )
        row_y -= row_spacing

        # Groq (for refine)
        self._build_api_row_compact(row_y, "Groq", "GROQ_API_KEY", "groq", parent_view)
        row_y -= row_spacing

        # OpenAI (for refine)
        self._build_api_row_compact(
            row_y, "OpenAI", "OPENAI_API_KEY", "openai", parent_view
        )
        row_y -= row_spacing

        # OpenRouter (for refine)
        self._build_api_row_compact(
            row_y, "OpenRouter", "OPENROUTER_API_KEY", "openrouter", parent_view
        )

        return card_y - CARD_SPACING

    def _build_api_row_compact(
        self, y: int, label_text: str, key_name: str, provider: str, parent_view=None
    ) -> None:
        """Erstellt kompakte API-Key-Zeile (ohne Save-Button, mit Copy/Paste)."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSMakeRect,
            NSTextField,
        )

        parent_view = parent_view or self._content_view

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
        parent_view.addSubview_(label)

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

        parent_view.addSubview_(field)

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
        parent_view.addSubview_(status)

        # Referenzen speichern für Save
        setattr(self, f"_{provider}_field", field)
        setattr(self, f"_{provider}_status", status)

    def _build_settings_card(self, y: int, parent_view=None) -> int:
        """Erstellt Provider-Einstellungen (Mode/Local/Language)."""
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

        # --- Mode Dropdown ---
        self._add_setting_label(base_x, current_y, "Mode:", parent_view)
        mode_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        mode_popup.setFont_(NSFont.systemFontOfSize_(11))
        for mode in MODE_OPTIONS:
            mode_popup.addItemWithTitle_(mode)
        current_mode = (
            get_env_setting("PULSESCRIBE_MODE") or self.config.get("mode") or "deepgram"
        )
        if current_mode in MODE_OPTIONS:
            mode_popup.selectItemWithTitle_(current_mode)
        # Update visibility when mode changes
        mode_changed_handler = _SimpleHandler.alloc().initWithController_method_(
            self, "_update_all_visibility"
        )
        mode_popup.setTarget_(mode_changed_handler)
        mode_popup.setAction_(
            objc.selector(mode_changed_handler.performAction_, signature=b"v@:@")
        )
        self._mode_changed_handler = mode_changed_handler
        self._mode_popup = mode_popup
        parent_view.addSubview_(mode_popup)
        current_y -= row_height

        # --- Local Backend Dropdown ---
        self._local_backend_label = self._add_setting_label(
            base_x, current_y, "Local Backend:", parent_view
        )
        local_backend_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        local_backend_popup.setFont_(NSFont.systemFontOfSize_(11))
        for backend in LOCAL_BACKEND_OPTIONS:
            local_backend_popup.addItemWithTitle_(backend)
        current_backend = get_env_setting("PULSESCRIBE_LOCAL_BACKEND") or "whisper"
        if current_backend not in LOCAL_BACKEND_OPTIONS:
            current_backend = "whisper"
        local_backend_popup.selectItemWithTitle_(current_backend)
        self._local_backend_popup = local_backend_popup
        parent_view.addSubview_(local_backend_popup)

        # Update Lightning visibility when backend changes
        backend_handler = _SimpleHandler.alloc().initWithController_method_(
            self, "_update_local_settings_visibility"
        )
        local_backend_popup.setTarget_(backend_handler)
        local_backend_popup.setAction_(
            objc.selector(backend_handler.performAction_, signature=b"v@:@")
        )
        self._backend_changed_handler = backend_handler
        current_y -= row_height

        # --- Local Model Dropdown ---
        self._local_model_label = self._add_setting_label(
            base_x, current_y, "Local Model:", parent_view
        )
        local_model_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        local_model_popup.setFont_(NSFont.systemFontOfSize_(11))
        for m in LOCAL_MODEL_OPTIONS:
            local_model_popup.addItemWithTitle_(m)
        current_local_model = get_env_setting("PULSESCRIBE_LOCAL_MODEL") or "default"
        if current_local_model not in LOCAL_MODEL_OPTIONS and current_local_model:
            local_model_popup.addItemWithTitle_(current_local_model)
        local_model_popup.selectItemWithTitle_(current_local_model)
        self._local_model_popup = local_model_popup
        parent_view.addSubview_(local_model_popup)
        current_y -= row_height

        # --- Language Dropdown ---
        self._add_setting_label(base_x, current_y, "Language:", parent_view)
        lang_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        lang_popup.setFont_(NSFont.systemFontOfSize_(11))
        for lang in LANGUAGE_OPTIONS:
            lang_popup.addItemWithTitle_(lang)
        current_lang = (
            get_env_setting("PULSESCRIBE_LANGUAGE")
            or self.config.get("language")
            or "auto"
        )
        if current_lang in LANGUAGE_OPTIONS:
            lang_popup.selectItemWithTitle_(current_lang)
        self._lang_popup = lang_popup
        parent_view.addSubview_(lang_popup)
        current_y -= row_height

        # --- Streaming Toggle (Deepgram only) ---
        self._streaming_label = self._add_setting_label(
            base_x, current_y, "Streaming:", parent_view
        )
        streaming_checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        streaming_checkbox.setButtonType_(NSButtonTypeSwitch)
        streaming_checkbox.setTitle_("WebSocket (low latency)")
        streaming_checkbox.setFont_(NSFont.systemFontOfSize_(11))
        streaming_enabled = _is_env_enabled_default_true("PULSESCRIBE_STREAMING")
        streaming_checkbox.setState_(1 if streaming_enabled else 0)
        self._streaming_checkbox = streaming_checkbox
        parent_view.addSubview_(streaming_checkbox)

        self._update_all_visibility()

        return card_y - CARD_SPACING

    def _build_advanced_local_card(self, y: int, parent_view=None) -> int:
        """Erweiterte Local-Performance Settings (macOS-tuned)."""
        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSPopUpButton,
            NSSlider,
            NSTextField,
        )
        import objc  # type: ignore[import-not-found]

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
        desc.setStringValue_("Tweaks for local transcription (Whisper / Faster / MLX).")
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        row_height = 28
        current_y = card_y + card_height - 78

        def _bool_override_from_env(key: str) -> str:
            raw = get_env_setting(key)
            if raw is None:
                return "default"
            raw = raw.strip().lower()
            if raw in ("1", "true", "yes", "on"):
                return "true"
            if raw in ("0", "false", "no", "off"):
                return "false"
            return "default"

        # Preset (applies values, not persisted)
        self._add_setting_label(base_x, current_y, "Preset:", parent_view)
        preset_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        preset_popup.setFont_(NSFont.systemFontOfSize_(11))
        for preset in LOCAL_PRESET_OPTIONS:
            preset_popup.addItemWithTitle_(preset)
        preset_popup.selectItemWithTitle_("(none)")
        preset_handler = _SimpleHandler.alloc().initWithController_method_(
            self, "_apply_selected_local_preset"
        )
        preset_popup.setTarget_(preset_handler)
        preset_popup.setAction_(
            objc.selector(preset_handler.performAction_, signature=b"v@:@")
        )
        self._local_preset_changed_handler = preset_handler
        self._local_preset_popup = preset_popup
        parent_view.addSubview_(preset_popup)
        current_y -= row_height

        # Device (openai-whisper)
        self._add_setting_label(base_x, current_y, "Device:", parent_view)
        device_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        device_popup.setFont_(NSFont.systemFontOfSize_(11))
        for d in DEVICE_OPTIONS:
            device_popup.addItemWithTitle_(d)
        current_device = (
            (get_env_setting("PULSESCRIBE_DEVICE") or "auto").strip().lower()
        )
        if current_device not in DEVICE_OPTIONS:
            current_device = "auto"
        device_popup.selectItemWithTitle_(current_device)
        self._device_popup = device_popup
        parent_view.addSubview_(device_popup)
        current_y -= row_height

        # Warmup
        self._add_setting_label(base_x, current_y, "Warmup:", parent_view)
        warmup_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        warmup_popup.setFont_(NSFont.systemFontOfSize_(11))
        for v in WARMUP_OPTIONS:
            warmup_popup.addItemWithTitle_(v)
        warmup_env = get_env_setting("PULSESCRIBE_LOCAL_WARMUP")
        warmup_value = (warmup_env or "auto").strip().lower()
        if warmup_value not in WARMUP_OPTIONS:
            warmup_value = "auto"
        warmup_popup.selectItemWithTitle_(warmup_value)
        self._warmup_popup = warmup_popup
        parent_view.addSubview_(warmup_popup)
        current_y -= row_height

        # Fast mode
        self._add_setting_label(base_x, current_y, "Fast:", parent_view)
        fast_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        fast_popup.setFont_(NSFont.systemFontOfSize_(11))
        for v in BOOL_OVERRIDE_OPTIONS:
            fast_popup.addItemWithTitle_(v)
        fast_popup.selectItemWithTitle_(
            _bool_override_from_env("PULSESCRIBE_LOCAL_FAST")
        )
        self._local_fast_popup = fast_popup
        parent_view.addSubview_(fast_popup)
        current_y -= row_height

        # FP16 (openai-whisper; also used by MLX)
        self._add_setting_label(base_x, current_y, "FP16:", parent_view)
        fp16_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        fp16_popup.setFont_(NSFont.systemFontOfSize_(11))
        for v in BOOL_OVERRIDE_OPTIONS:
            fp16_popup.addItemWithTitle_(v)
        fp16_popup.selectItemWithTitle_(_bool_override_from_env("PULSESCRIBE_FP16"))
        self._fp16_popup = fp16_popup
        parent_view.addSubview_(fp16_popup)
        current_y -= row_height

        # Beam size
        self._add_setting_label(base_x, current_y, "Beam size:", parent_view)
        beam_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        beam_field.setFont_(NSFont.systemFontOfSize_(11))
        beam_field.setPlaceholderString_("default")
        beam_field.setStringValue_(get_env_setting("PULSESCRIBE_LOCAL_BEAM_SIZE") or "")
        self._beam_size_field = beam_field
        parent_view.addSubview_(beam_field)
        current_y -= row_height

        # Best of
        self._add_setting_label(base_x, current_y, "Best of:", parent_view)
        best_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        best_field.setFont_(NSFont.systemFontOfSize_(11))
        best_field.setPlaceholderString_("default")
        best_field.setStringValue_(get_env_setting("PULSESCRIBE_LOCAL_BEST_OF") or "")
        self._best_of_field = best_field
        parent_view.addSubview_(best_field)
        current_y -= row_height

        # Temperature
        self._add_setting_label(base_x, current_y, "Temperature:", parent_view)
        temp_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        temp_field.setFont_(NSFont.systemFontOfSize_(11))
        temp_field.setPlaceholderString_("e.g. 0.0 or 0.0,0.2,0.4")
        temp_field.setStringValue_(
            get_env_setting("PULSESCRIBE_LOCAL_TEMPERATURE") or ""
        )
        self._temperature_field = temp_field
        parent_view.addSubview_(temp_field)
        current_y -= row_height

        # faster-whisper compute type
        self._add_setting_label(base_x, current_y, "Compute type:", parent_view)
        compute_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        compute_field.setFont_(NSFont.systemFontOfSize_(11))
        compute_field.setPlaceholderString_("default (e.g. int8, int8_float16)")
        compute_field.setStringValue_(
            get_env_setting("PULSESCRIBE_LOCAL_COMPUTE_TYPE") or ""
        )
        self._compute_type_field = compute_field
        parent_view.addSubview_(compute_field)
        current_y -= row_height

        # CPU threads
        self._add_setting_label(base_x, current_y, "CPU threads:", parent_view)
        threads_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        threads_field.setFont_(NSFont.systemFontOfSize_(11))
        threads_field.setPlaceholderString_("0 = auto")
        threads_field.setStringValue_(
            get_env_setting("PULSESCRIBE_LOCAL_CPU_THREADS") or ""
        )
        self._cpu_threads_field = threads_field
        parent_view.addSubview_(threads_field)
        current_y -= row_height

        # Workers
        self._add_setting_label(base_x, current_y, "Workers:", parent_view)
        workers_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        workers_field.setFont_(NSFont.systemFontOfSize_(11))
        workers_field.setPlaceholderString_("1")
        workers_field.setStringValue_(
            get_env_setting("PULSESCRIBE_LOCAL_NUM_WORKERS") or ""
        )
        self._num_workers_field = workers_field
        parent_view.addSubview_(workers_field)
        current_y -= row_height

        # without_timestamps (faster-whisper)
        self._add_setting_label(base_x, current_y, "No timestamps:", parent_view)
        wt_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        wt_popup.setFont_(NSFont.systemFontOfSize_(11))
        for v in BOOL_OVERRIDE_OPTIONS:
            wt_popup.addItemWithTitle_(v)
        wt_popup.selectItemWithTitle_(
            _bool_override_from_env("PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS")
        )
        self._without_timestamps_popup = wt_popup
        parent_view.addSubview_(wt_popup)
        current_y -= row_height

        # VAD filter (faster-whisper)
        self._add_setting_label(base_x, current_y, "VAD filter:", parent_view)
        vad_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        vad_popup.setFont_(NSFont.systemFontOfSize_(11))
        for v in BOOL_OVERRIDE_OPTIONS:
            vad_popup.addItemWithTitle_(v)
        vad_popup.selectItemWithTitle_(
            _bool_override_from_env("PULSESCRIBE_LOCAL_VAD_FILTER")
        )
        self._vad_filter_popup = vad_popup
        parent_view.addSubview_(vad_popup)
        current_y -= row_height

        # --- Lightning Whisper MLX Settings ---
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
        current_y -= row_height

        # Batch Size Slider (4-24, default 12)
        self._lightning_batch_label = self._add_setting_label(
            base_x, current_y, "Batch size:", parent_view
        )
        batch_slider = NSSlider.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width - 40, 22)
        )
        batch_slider.setMinValue_(4)
        batch_slider.setMaxValue_(24)
        current_batch = get_env_setting("PULSESCRIBE_LIGHTNING_BATCH_SIZE")
        try:
            batch_val = int(current_batch) if current_batch else 12
        except ValueError:
            batch_val = 12  # Fallback to default if invalid
        batch_slider.setIntValue_(batch_val)
        batch_slider.setNumberOfTickMarks_(6)  # 4, 8, 12, 16, 20, 24
        batch_slider.setAllowsTickMarkValuesOnly_(True)
        self._lightning_batch_slider = batch_slider
        parent_view.addSubview_(batch_slider)

        # Value label next to slider
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

        # Slider change handler
        batch_handler = _SliderHandler.alloc().initWithLabel_(batch_value_label)
        batch_slider.setTarget_(batch_handler)
        batch_slider.setAction_(
            objc.selector(batch_handler.sliderChanged_, signature=b"v@:@")
        )
        self._lightning_batch_handler = batch_handler
        current_y -= row_height

        # Quantization Dropdown
        self._lightning_quant_label = self._add_setting_label(
            base_x, current_y, "Quantization:", parent_view
        )
        quant_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        quant_popup.setFont_(NSFont.systemFontOfSize_(11))
        quant_popup.addItemWithTitle_("none (best quality)")
        quant_popup.addItemWithTitle_("8bit")
        quant_popup.addItemWithTitle_("4bit (smallest memory)")
        current_quant = (
            (get_env_setting("PULSESCRIBE_LIGHTNING_QUANT") or "").strip().lower()
        )
        if current_quant == "4bit":
            quant_popup.selectItemAtIndex_(2)
        elif current_quant == "8bit":
            quant_popup.selectItemAtIndex_(1)
        else:
            quant_popup.selectItemAtIndex_(0)
        self._lightning_quant_popup = quant_popup
        parent_view.addSubview_(quant_popup)

        # Set initial visibility based on current mode/backend
        self._update_all_visibility()

        return card_y - CARD_SPACING

    def _build_refine_card(self, y: int, parent_view=None) -> int:
        """Erstellt Refine-Einstellungen."""
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

        parent_view = parent_view or self._content_view

        card_height = 230  # Increased for Overlay/Dock toggles
        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        label_width = 110
        control_x = base_x + label_width + 8
        control_width = card_width - 2 * CARD_PADDING - label_width - 8

        title = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 28, 200, 18)
        )
        title.setStringValue_("✨ Refine")
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.systemFontOfSize_weight_(13, NSFontWeightSemibold))
        title.setTextColor_(NSColor.whiteColor())
        parent_view.addSubview_(title)

        row_height = 28
        current_y = card_y + card_height - 58

        # Refine Checkbox
        self._add_setting_label(base_x, current_y, "Refine:", parent_view)
        refine_checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        refine_checkbox.setButtonType_(NSButtonTypeSwitch)
        refine_checkbox.setTitle_("Enable LLM post-processing")
        refine_checkbox.setFont_(NSFont.systemFontOfSize_(11))
        refine_enabled = get_env_setting("PULSESCRIBE_REFINE")
        if refine_enabled is None:
            refine_enabled = self.config.get("refine", False)
        else:
            refine_enabled = bool(parse_bool(refine_enabled))
        refine_checkbox.setState_(1 if refine_enabled else 0)
        self._refine_checkbox = refine_checkbox
        parent_view.addSubview_(refine_checkbox)
        current_y -= row_height

        # Clipboard Restore Checkbox
        self._add_setting_label(base_x, current_y, "Clipboard:", parent_view)
        clipboard_checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        clipboard_checkbox.setButtonType_(NSButtonTypeSwitch)
        clipboard_checkbox.setTitle_("Restore after paste")
        clipboard_checkbox.setFont_(NSFont.systemFontOfSize_(11))
        clipboard_restore = get_env_setting("PULSESCRIBE_CLIPBOARD_RESTORE")
        clipboard_restore = (
            bool(parse_bool(clipboard_restore)) if clipboard_restore else False
        )
        clipboard_checkbox.setState_(1 if clipboard_restore else 0)
        self._clipboard_restore_checkbox = clipboard_checkbox
        parent_view.addSubview_(clipboard_checkbox)
        current_y -= row_height

        # Refine Provider
        self._add_setting_label(base_x, current_y, "Refine Provider:", parent_view)
        provider_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        provider_popup.setFont_(NSFont.systemFontOfSize_(11))
        for provider in REFINE_PROVIDER_OPTIONS:
            provider_popup.addItemWithTitle_(provider)
        current_provider = (
            get_env_setting("PULSESCRIBE_REFINE_PROVIDER")
            or self.config.get("refine_provider")
            or "groq"
        )
        if current_provider in REFINE_PROVIDER_OPTIONS:
            provider_popup.selectItemWithTitle_(current_provider)
        self._provider_popup = provider_popup
        parent_view.addSubview_(provider_popup)
        current_y -= row_height

        # Refine Model
        self._add_setting_label(base_x, current_y, "Refine Model:", parent_view)
        model_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        model_field.setFont_(NSFont.systemFontOfSize_(11))
        model_field.setPlaceholderString_("e.g. openai/gpt-oss-120b")
        current_model = (
            get_env_setting("PULSESCRIBE_REFINE_MODEL")
            or self.config.get("refine_model")
            or "openai/gpt-oss-120b"
        )
        model_field.setStringValue_(current_model)
        self._model_field = model_field
        parent_view.addSubview_(model_field)
        current_y -= row_height

        # --- Display Settings ---
        # Overlay Toggle
        self._add_setting_label(base_x, current_y, "Overlay:", parent_view)
        overlay_checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        overlay_checkbox.setButtonType_(NSButtonTypeSwitch)
        overlay_checkbox.setTitle_("Show subtitle overlay")
        overlay_checkbox.setFont_(NSFont.systemFontOfSize_(11))
        overlay_enabled = _is_env_enabled_default_true("PULSESCRIBE_OVERLAY")
        overlay_checkbox.setState_(1 if overlay_enabled else 0)
        self._overlay_checkbox = overlay_checkbox
        parent_view.addSubview_(overlay_checkbox)
        current_y -= row_height

        # Dock Icon Toggle
        self._add_setting_label(base_x, current_y, "Dock Icon:", parent_view)
        dock_checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(control_x, current_y, control_width, 22)
        )
        dock_checkbox.setButtonType_(NSButtonTypeSwitch)
        dock_checkbox.setTitle_("Show in Dock (restart required)")
        dock_checkbox.setFont_(NSFont.systemFontOfSize_(11))
        dock_enabled = _is_env_enabled_default_true("PULSESCRIBE_DOCK_ICON")
        dock_checkbox.setState_(1 if dock_enabled else 0)
        self._dock_icon_checkbox = dock_checkbox
        parent_view.addSubview_(dock_checkbox)

        return card_y - CARD_SPACING

    def _build_prompts_card(
        self, y: int, parent_view=None, tab_height: int | None = None
    ) -> int:
        """Erstellt Custom Prompts Editor."""
        from AppKit import (  # type: ignore[import-not-found]
            NSBezelBorder,
            NSBezelStyleRounded,
            NSButton,
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSMakeRect,
            NSPopUpButton,
            NSScrollView,
            NSTextField,
            NSTextView,
        )
        import objc  # type: ignore[import-not-found]

        from utils.custom_prompts import (
            KNOWN_CONTEXTS,
            format_app_mappings,
            get_custom_app_contexts,
            get_custom_prompt_for_context,
            get_custom_voice_commands,
            get_defaults,
        )

        parent_view = parent_view or self._content_view

        card_width = WELCOME_WIDTH - 2 * WELCOME_PADDING
        max_height = (tab_height - 2 * WELCOME_PADDING) if tab_height else 500
        card_height = min(500, max_height)
        card_y = y - card_height

        card = _create_card(WELCOME_PADDING, card_y, card_width, card_height)
        parent_view.addSubview_(card)

        base_x = WELCOME_PADDING + CARD_PADDING
        content_width = card_width - 2 * CARD_PADDING

        # Titel
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

        # Beschreibung
        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + card_height - 46, content_width, 14)
        )
        desc.setStringValue_(
            "Edit prompts for LLM post-processing. Changes saved to ~/.pulsescribe/prompts.toml"
        )
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        desc.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        parent_view.addSubview_(desc)

        # Context-Auswahl + Reset-Button Zeile
        row_y = card_y + card_height - 76
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
        # Kontexte aus KNOWN_CONTEXTS + Voice Commands + App Mappings
        for ctx in [*KNOWN_CONTEXTS, "── Voice Commands", "── App Mappings"]:
            context_popup.addItemWithTitle_(ctx)
        parent_view.addSubview_(context_popup)
        self._prompts_context_popup = context_popup

        # Reset-Button (rechts vom Dropdown mit Abstand)
        reset_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(base_x + 215, row_y, 100, 22)
        )
        reset_btn.setTitle_("Reset to Default")
        reset_btn.setBezelStyle_(NSBezelStyleRounded)
        reset_btn.setFont_(NSFont.systemFontOfSize_(10))
        parent_view.addSubview_(reset_btn)
        self._prompts_reset_btn = reset_btn

        # Prompt-Editor (NSTextView in NSScrollView)
        scroll_y = card_y + 80
        scroll_height = card_height - 170
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
        text_view.setAllowsUndo_(True)  # CMD+Z / CMD+Shift+Z
        tc = text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)

        # Initial laden
        current_prompt = get_custom_prompt_for_context("default")
        text_view.setString_(current_prompt)
        scroll.setDocumentView_(text_view)
        parent_view.addSubview_(scroll)
        self._prompts_text_view = text_view

        # Hinweis-Label
        hint_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 54, content_width, 14)
        )
        hint_label.setStringValue_(
            "💡 Use dropdown to edit Voice Commands or App Mappings"
        )
        hint_label.setBezeled_(False)
        hint_label.setDrawsBackground_(False)
        hint_label.setEditable_(False)
        hint_label.setSelectable_(False)
        hint_label.setFont_(NSFont.systemFontOfSize_weight_(10, NSFontWeightMedium))
        hint_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.5))
        parent_view.addSubview_(hint_label)

        # Status-Label
        status_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 16, content_width, 16)
        )
        status_label.setStringValue_("")
        status_label.setBezeled_(False)
        status_label.setDrawsBackground_(False)
        status_label.setEditable_(False)
        status_label.setSelectable_(False)
        status_label.setFont_(NSFont.systemFontOfSize_(10))
        status_label.setTextColor_(_get_color(76, 175, 80, 0.9))
        parent_view.addSubview_(status_label)
        self._prompts_status_label = status_label

        # Cache für unsaved changes
        self._prompts_cache: dict[str, str] = {}
        self._prompts_current_context = "default"

        # Action Handlers
        def on_context_change(_sender) -> None:
            # Aktuellen Prompt speichern
            old_ctx = self._prompts_current_context
            self._prompts_cache[old_ctx] = str(self._prompts_text_view.string())

            # Neuen Kontext laden
            new_ctx = str(self._prompts_context_popup.titleOfSelectedItem())
            self._prompts_current_context = new_ctx

            # Aus Cache oder frisch laden
            if new_ctx in self._prompts_cache:
                self._prompts_text_view.setString_(self._prompts_cache[new_ctx])
            elif new_ctx == "── Voice Commands":
                # Voice Commands laden
                vc = get_custom_voice_commands()
                self._prompts_text_view.setString_(vc)
            elif new_ctx == "── App Mappings":
                # App Mappings als Text laden
                mappings = get_custom_app_contexts()
                self._prompts_text_view.setString_(format_app_mappings(mappings))
            else:
                prompt = get_custom_prompt_for_context(new_ctx)
                self._prompts_text_view.setString_(prompt)

        def on_reset(_sender) -> None:
            ctx = str(self._prompts_context_popup.titleOfSelectedItem())
            defaults = get_defaults()

            if ctx == "── Voice Commands":
                # Voice Commands auf Default zurücksetzen
                default_vc = defaults["voice_commands"]["instruction"]
                self._prompts_text_view.setString_(default_vc)
                self._prompts_cache[ctx] = default_vc
                self._prompts_status_label.setStringValue_("Reset Voice Commands")
            elif ctx == "── App Mappings":
                # App Mappings auf Default zurücksetzen
                default_mappings = defaults["app_contexts"]
                text = format_app_mappings(default_mappings)
                self._prompts_text_view.setString_(text)
                self._prompts_cache[ctx] = text
                self._prompts_status_label.setStringValue_("Reset App Mappings")
            else:
                default_prompt = defaults["prompts"].get(ctx, {}).get("prompt", "")
                self._prompts_text_view.setString_(default_prompt)
                self._prompts_cache[ctx] = default_prompt
                self._prompts_status_label.setStringValue_(f"Reset '{ctx}' to default")

        # Handler-Klasse für ObjC
        class _PromptsActionHandler(objc.lookUpClass("NSObject")):
            def initWithCallback_(self, callback):
                self = objc.super(_PromptsActionHandler, self).init()
                if self is None:
                    return None
                self._callback = callback
                return self

            def performAction_(self, sender):
                self._callback(sender)

        context_handler = _PromptsActionHandler.alloc().initWithCallback_(
            on_context_change
        )
        context_popup.setTarget_(context_handler)
        context_popup.setAction_(
            objc.selector(context_handler.performAction_, signature=b"v@:@")
        )
        self._prompts_context_handler = context_handler

        reset_handler = _PromptsActionHandler.alloc().initWithCallback_(on_reset)
        reset_btn.setTarget_(reset_handler)
        reset_btn.setAction_(
            objc.selector(reset_handler.performAction_, signature=b"v@:@")
        )
        self._prompts_reset_handler = reset_handler

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
            "One keyword per line (or comma-separated). Used by Local and Deepgram."
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
        tc = text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)

        keywords = load_vocabulary().get("keywords", [])
        text_view.setString_("\n".join(str(k) for k in keywords))
        scroll.setDocumentView_(text_view)
        parent_view.addSubview_(scroll)

        self._vocab_text_view = text_view

        warning = NSTextField.alloc().initWithFrame_(
            NSMakeRect(base_x, card_y + 16, content_width, 16)
        )
        warning.setBezeled_(False)
        warning.setDrawsBackground_(False)
        warning.setEditable_(False)
        warning.setSelectable_(False)
        warning.setFont_(NSFont.systemFontOfSize_weight_(11, NSFontWeightMedium))
        warning.setTextColor_(_get_color(255, 193, 7, 0.9))
        parent_view.addSubview_(warning)
        self._vocab_warning_label = warning
        self._update_vocabulary_warning()

        return card_y - CARD_SPACING

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
            NSMakeRect(base_x, segment_y, 180, 22)
        )
        segment.setSegmentCount_(2)
        segment.setLabel_forSegment_("🪵 Logs", 0)
        segment.setLabel_forSegment_("📝 Transcripts", 1)
        segment.setWidth_forSegment_(85, 0)
        segment.setWidth_forSegment_(95, 1)
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
        parent_view.addSubview_(segment)

        # Content area dimensions
        content_y = card_y + 16
        content_height = card_height - 56

        # ===== LOGS CONTAINER =====
        logs_container = NSView.alloc().initWithFrame_(
            NSMakeRect(base_x, content_y, content_width, content_height)
        )
        self._logs_container = logs_container
        parent_view.addSubview_(logs_container)

        # Auto-refresh Checkbox (in logs container header)
        auto_checkbox = NSButton.alloc().initWithFrame_(
            NSMakeRect(content_width - 230, content_height - 22, 100, 20)
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
        logs_container.addSubview_(auto_checkbox)

        # Finder Button
        btn_w = 65
        btn_spacing = 4
        finder_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                content_width - btn_w * 2 - btn_spacing, content_height - 24, btn_w, 22
            )
        )
        finder_btn.setTitle_("Finder")
        finder_btn.setBezelStyle_(NSBezelStyleRounded)
        finder_btn.setFont_(NSFont.systemFontOfSize_(11))
        finder_handler = _OpenLogsInFinderHandler.alloc().initWithController_(self)
        finder_btn.setTarget_(finder_handler)
        finder_btn.setAction_(
            objc.selector(finder_handler.openInFinder_, signature=b"v@:@")
        )
        self._logs_finder_handler = finder_handler
        logs_container.addSubview_(finder_btn)

        # Refresh Button
        refresh_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(content_width - btn_w, content_height - 24, btn_w, 22)
        )
        refresh_btn.setTitle_("Refresh")
        refresh_btn.setBezelStyle_(NSBezelStyleRounded)
        refresh_btn.setFont_(NSFont.systemFontOfSize_(11))
        refresh_handler = _SimpleHandler.alloc().initWithController_method_(
            self, "_refresh_logs"
        )
        refresh_btn.setTarget_(refresh_handler)
        refresh_btn.setAction_(
            objc.selector(refresh_handler.performAction_, signature=b"v@:@")
        )
        self._logs_refresh_handler = refresh_handler
        logs_container.addSubview_(refresh_btn)

        # Log-Pfad
        path_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, content_height - 20, content_width - 240, 14)
        )
        path_label.setStringValue_(str(LOG_FILE))
        path_label.setBezeled_(False)
        path_label.setDrawsBackground_(False)
        path_label.setEditable_(False)
        path_label.setSelectable_(True)
        path_label.setFont_(NSFont.systemFontOfSize_(9))
        path_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.4))
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
        tc = text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)
        text_view.setString_(self._get_logs_text())
        scroll.setDocumentView_(text_view)
        logs_container.addSubview_(scroll)
        self._logs_text_view = text_view

        # ===== TRANSCRIPTS CONTAINER =====
        transcripts_container = NSView.alloc().initWithFrame_(
            NSMakeRect(base_x, content_y, content_width, content_height)
        )
        transcripts_container.setHidden_(True)  # Initially hidden
        self._transcripts_container = transcripts_container
        parent_view.addSubview_(transcripts_container)

        # Clear History Button
        clear_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(content_width - btn_w, content_height - 24, btn_w, 22)
        )
        clear_btn.setTitle_("Clear")
        clear_btn.setBezelStyle_(NSBezelStyleRounded)
        clear_btn.setFont_(NSFont.systemFontOfSize_(11))
        clear_handler = _ClearTranscriptsHandler.alloc().initWithController_(self)
        clear_btn.setTarget_(clear_handler)
        clear_btn.setAction_(
            objc.selector(clear_handler.clearTranscripts_, signature=b"v@:@")
        )
        self._transcripts_clear_handler = clear_handler
        transcripts_container.addSubview_(clear_btn)

        # Refresh Transcripts Button
        refresh_t_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                content_width - btn_w * 2 - btn_spacing, content_height - 24, btn_w, 22
            )
        )
        refresh_t_btn.setTitle_("Refresh")
        refresh_t_btn.setBezelStyle_(NSBezelStyleRounded)
        refresh_t_btn.setFont_(NSFont.systemFontOfSize_(11))
        refresh_t_btn.setTarget_(clear_handler)
        refresh_t_btn.setAction_(
            objc.selector(clear_handler.refreshTranscripts_, signature=b"v@:@")
        )
        transcripts_container.addSubview_(refresh_t_btn)

        # Transcripts count label
        count_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, content_height - 20, content_width - 150, 14)
        )
        count_label.setStringValue_("Recent transcriptions")
        count_label.setBezeled_(False)
        count_label.setDrawsBackground_(False)
        count_label.setEditable_(False)
        count_label.setSelectable_(False)
        count_label.setFont_(NSFont.systemFontOfSize_(11))
        count_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
        transcripts_container.addSubview_(count_label)

        # Transcripts ScrollView
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
        tc = t_text_view.textContainer()
        if tc is not None:
            tc.setWidthTracksTextView_(True)
        t_text_view.setString_(self._get_transcripts_text())
        t_scroll.setDocumentView_(t_text_view)
        transcripts_container.addSubview_(t_scroll)
        self._transcripts_text_view = t_text_view

        # Initial scroll and auto-refresh
        self._scroll_logs_to_bottom()
        self._start_logs_auto_refresh()

        return card_y - CARD_SPACING

    def _get_transcripts_text(self) -> str:
        """Lädt und formatiert die Transkript-Historie."""
        from utils.history import get_recent_transcripts

        try:
            entries = get_recent_transcripts(count=50)
            if not entries:
                return (
                    "No transcriptions yet.\n\nYour transcribed texts will appear here."
                )

            lines = []
            # Chronological order (oldest first, newest last) - like logs
            for entry in reversed(entries):
                ts = entry.get("timestamp", "")[:19].replace("T", " ")
                text = entry.get("text", "").strip()
                mode = entry.get("mode", "")
                lang = entry.get("language", "")

                header = f"[{ts}]"
                if mode or lang:
                    meta = " ".join(filter(None, [mode, lang]))
                    header += f" ({meta})"

                lines.append(header)
                lines.append(text)
                lines.append("")

            return "\n".join(lines)
        except Exception as e:
            return f"Could not load transcripts: {e}"

    def _refresh_transcripts(self) -> None:
        """Aktualisiert die Transkript-Anzeige."""
        if self._transcripts_text_view:
            try:
                self._transcripts_text_view.setString_(self._get_transcripts_text())
                self._scroll_transcripts_to_bottom()
            except Exception:
                pass

    def _scroll_transcripts_to_bottom(self) -> None:
        """Scrollt die Transcripts-Ansicht ans Ende (neueste unten)."""
        if self._transcripts_text_view:
            try:
                length = len(self._transcripts_text_view.string())
                self._transcripts_text_view.scrollRangeToVisible_((length, 0))
            except Exception:
                pass

    def _clear_transcripts(self) -> None:
        """Löscht die Transkript-Historie."""
        from utils.history import clear_history

        clear_history()
        self._refresh_transcripts()

    def _switch_logs_segment(self, segment_index: int) -> None:
        """Wechselt zwischen Logs und Transcripts Ansicht."""
        if self._logs_container and self._transcripts_container:
            if segment_index == 0:  # Logs
                self._logs_container.setHidden_(False)
                self._transcripts_container.setHidden_(True)
            else:  # Transcripts
                self._logs_container.setHidden_(True)
                self._transcripts_container.setHidden_(False)
                self._refresh_transcripts()

    def _get_logs_text(self, max_chars: int = 15000) -> str:
        """Liest einen Ausschnitt der aktuellen Log-Datei."""
        try:
            if not LOG_FILE.exists():
                return "No logs yet.\n\nLog file will appear at:\n" + str(LOG_FILE)
            text = LOG_FILE.read_text(encoding="utf-8", errors="ignore")
            if len(text) > max_chars:
                return "... (truncated)\n\n" + text[-max_chars:]
            return text
        except Exception as e:
            return f"Could not read logs: {e}"

    def _refresh_logs(self) -> None:
        """Aktualisiert die Log-Anzeige und scrollt nach unten."""
        if self._logs_text_view:
            try:
                self._logs_text_view.setString_(self._get_logs_text())
                self._scroll_logs_to_bottom()
            except Exception:
                pass

    def _scroll_logs_to_bottom(self) -> None:
        """Scrollt die Log-Ansicht ans Ende."""
        if self._logs_text_view:
            try:
                length = len(self._logs_text_view.string())
                self._logs_text_view.scrollRangeToVisible_((length, 0))
            except Exception:
                pass

    def _start_logs_auto_refresh(self) -> None:
        """Startet den Auto-Refresh Timer für Logs (alle 2 Sekunden)."""
        from Foundation import NSTimer  # type: ignore[import-not-found]

        self._stop_logs_auto_refresh()

        def tick(_timer) -> None:
            if self._logs_auto_checkbox and self._logs_auto_checkbox.state():
                self._refresh_logs()

        self._logs_auto_refresh_timer = (
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(2.0, True, tick)
        )

    def _stop_logs_auto_refresh(self) -> None:
        """Stoppt den Auto-Refresh Timer."""
        if hasattr(self, "_logs_auto_refresh_timer") and self._logs_auto_refresh_timer:
            try:
                self._logs_auto_refresh_timer.invalidate()
            except Exception:
                pass
            self._logs_auto_refresh_timer = None

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
        parts: list[str] = []
        for line in raw.splitlines():
            for chunk in line.split(","):
                kw = chunk.strip()
                if kw:
                    parts.append(kw)
        seen: set[str] = set()
        result: list[str] = []
        for kw in parts:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result

    def _get_current_keywords(self) -> list[str]:
        if not self._vocab_text_view:
            return []
        try:
            raw = str(self._vocab_text_view.string() or "")
        except Exception:
            raw = ""
        return self._parse_keywords_text(raw)

    def _update_vocabulary_warning(self) -> None:
        if not self._vocab_warning_label:
            return
        count = len(self._get_current_keywords())
        if count > 100:
            msg = f"Warning: {count} keywords. Deepgram: first 100, Local: first 50."
        elif count > 50:
            msg = f"Note: {count} keywords. Local uses first 50; Deepgram first 100."
        else:
            msg = f"{count} keywords"
        self._vocab_warning_label.setStringValue_(msg)

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
        is_local = self._mode_popup.titleOfSelectedItem() == "local"
        for view in (
            self._local_backend_label,
            self._local_backend_popup,
            self._local_model_label,
            self._local_model_popup,
        ):
            if view is not None:
                view.setHidden_(not is_local)

        # Lightning-Settings: nur sichtbar wenn mode=local UND backend=lightning
        is_lightning = False
        if is_local and self._local_backend_popup:
            is_lightning = (
                self._local_backend_popup.titleOfSelectedItem() == "lightning"
            )
        for view in (
            self._lightning_header,
            self._lightning_batch_label,
            self._lightning_batch_slider,
            self._lightning_batch_value_label,
            self._lightning_quant_label,
            self._lightning_quant_popup,
        ):
            if view is not None:
                view.setHidden_(not is_lightning)

    def _update_streaming_visibility(self) -> None:
        """Blendet Streaming-Toggle nur bei Deepgram ein."""
        if not self._mode_popup:
            return
        is_deepgram = self._mode_popup.titleOfSelectedItem() == "deepgram"
        for view in (self._streaming_label, self._streaming_checkbox):
            if view is not None:
                view.setHidden_(not is_deepgram)

    def _update_all_visibility(self) -> None:
        """Update all mode-dependent visibility settings."""
        self._update_local_settings_visibility()
        self._update_streaming_visibility()

    def _apply_selected_local_preset(self) -> None:
        """Wendet das aktuell gewählte Local-Preset auf die UI an."""
        if not self._local_preset_popup:
            return
        preset = self._local_preset_popup.titleOfSelectedItem()
        if not preset or preset == "(none)":
            return
        self._apply_local_preset(preset)

    def _apply_local_preset(self, preset: str) -> None:
        """Setzt empfohlene Settings (UI-only; Speichern via 'Save & Apply')."""

        def set_popup(popup, title: str) -> None:
            if popup is None or not title:
                return
            try:
                popup.selectItemWithTitle_(title)
            except Exception:
                # Falls Custom Value fehlt, als Item hinzufügen
                try:
                    popup.addItemWithTitle_(title)
                    popup.selectItemWithTitle_(title)
                except Exception:
                    pass

        def set_field(field, value: str) -> None:
            if field is None:
                return
            try:
                field.setStringValue_(value)
            except Exception:
                pass

        # Immer Local Mode aktivieren (sonst sind Backend/Model hidden)
        set_popup(self._mode_popup, "local")
        self._update_local_settings_visibility()

        preset_values = LOCAL_PRESETS.get(preset)
        if not preset_values:
            return

        values = dict(LOCAL_PRESET_BASE)
        values.update(preset_values)

        set_popup(self._local_backend_popup, values.get("local_backend", ""))
        set_popup(self._local_model_popup, values.get("local_model", ""))
        set_popup(self._device_popup, values.get("device", "auto"))
        set_popup(self._warmup_popup, values.get("warmup", "auto"))
        set_popup(self._local_fast_popup, values.get("local_fast", "default"))
        set_popup(self._fp16_popup, values.get("fp16", "default"))
        set_field(self._beam_size_field, values.get("beam_size", ""))
        set_field(self._best_of_field, values.get("best_of", ""))
        set_field(self._temperature_field, values.get("temperature", ""))
        set_field(self._compute_type_field, values.get("compute_type", ""))
        set_field(self._cpu_threads_field, values.get("cpu_threads", ""))
        set_field(self._num_workers_field, values.get("num_workers", ""))
        set_popup(
            self._without_timestamps_popup,
            values.get("without_timestamps", "default"),
        )
        set_popup(self._vad_filter_popup, values.get("vad_filter", "default"))
        return

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

        # Button layout (right-aligned)
        btn_w = 140
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

        self._content_view.addSubview_(start_btn)

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
            self._window.makeKeyAndOrderFront_(None)
            self._window.center()
            from AppKit import NSApp  # type: ignore[import-not-found]

            NSApp.activateIgnoringOtherApps_(True)

    def hide(self) -> None:
        """Versteckt Window temporär (ohne zu schließen)."""
        self._stop_hotkey_recording(cancelled=True)
        if self._window:
            self._window.orderOut_(None)
        self._stop_setup_permission_auto_refresh()

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

    def _save_all_settings(self) -> None:
        """Speichert alle Einstellungen in die .env Datei.

        Note: Hotkeys werden direkt via HotkeyCard gespeichert (nicht hier).
        """
        import logging

        log = logging.getLogger(__name__)

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
                save_env_setting("PULSESCRIBE_MODE", mode)

        # Local Backend
        if self._local_backend_popup:
            backend = self._local_backend_popup.titleOfSelectedItem()
            if backend == "whisper":
                remove_env_setting("PULSESCRIBE_LOCAL_BACKEND")
            elif backend:
                save_env_setting("PULSESCRIBE_LOCAL_BACKEND", backend)

        # Local Model
        if self._local_model_popup:
            local_model = self._local_model_popup.titleOfSelectedItem()
            if local_model == "default":
                remove_env_setting("PULSESCRIBE_LOCAL_MODEL")
            elif local_model:
                save_env_setting("PULSESCRIBE_LOCAL_MODEL", local_model)

        # Language
        if self._lang_popup:
            lang = self._lang_popup.titleOfSelectedItem()
            if lang == "auto":
                remove_env_setting("PULSESCRIBE_LANGUAGE")
            elif lang:
                save_env_setting("PULSESCRIBE_LANGUAGE", lang)

        # Local Device (openai-whisper)
        if self._device_popup:
            device = (self._device_popup.titleOfSelectedItem() or "").strip().lower()
            if not device or device == "auto":
                remove_env_setting("PULSESCRIBE_DEVICE")
            else:
                save_env_setting("PULSESCRIBE_DEVICE", device)

        # Local Warmup (auto/true/false)
        if self._warmup_popup:
            warmup = (self._warmup_popup.titleOfSelectedItem() or "").strip().lower()
            if not warmup or warmup == "auto":
                remove_env_setting("PULSESCRIBE_LOCAL_WARMUP")
            else:
                save_env_setting("PULSESCRIBE_LOCAL_WARMUP", warmup)

        def _save_bool_override(key: str, popup) -> None:
            if popup is None:
                return
            sel = (popup.titleOfSelectedItem() or "").strip().lower()
            if not sel or sel == "default":
                remove_env_setting(key)
            else:
                save_env_setting(key, sel)

        # Local Fast (default/true/false)
        _save_bool_override("PULSESCRIBE_LOCAL_FAST", self._local_fast_popup)

        # FP16 (default/true/false)
        _save_bool_override("PULSESCRIBE_FP16", self._fp16_popup)

        def _save_optional_int(key: str, field) -> None:
            if field is None:
                return
            raw = field.stringValue().strip()
            if not raw:
                remove_env_setting(key)
                return
            try:
                int(raw)
            except ValueError:
                log.warning(f"Invalid {key}={raw!r}, not saved")
                return
            save_env_setting(key, raw)

        def _save_optional_str(key: str, field) -> None:
            if field is None:
                return
            raw = field.stringValue().strip()
            if not raw:
                remove_env_setting(key)
                return
            save_env_setting(key, raw)

        # Decode overrides
        _save_optional_int("PULSESCRIBE_LOCAL_BEAM_SIZE", self._beam_size_field)
        _save_optional_int("PULSESCRIBE_LOCAL_BEST_OF", self._best_of_field)
        _save_optional_str("PULSESCRIBE_LOCAL_TEMPERATURE", self._temperature_field)

        # faster-whisper overrides
        _save_optional_str("PULSESCRIBE_LOCAL_COMPUTE_TYPE", self._compute_type_field)
        _save_optional_int("PULSESCRIBE_LOCAL_CPU_THREADS", self._cpu_threads_field)
        _save_optional_int("PULSESCRIBE_LOCAL_NUM_WORKERS", self._num_workers_field)
        _save_bool_override(
            "PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS", self._without_timestamps_popup
        )
        _save_bool_override("PULSESCRIBE_LOCAL_VAD_FILTER", self._vad_filter_popup)

        # Lightning Batch Size
        if self._lightning_batch_slider:
            batch_val = int(self._lightning_batch_slider.intValue())
            if batch_val == 12:  # Default
                remove_env_setting("PULSESCRIBE_LIGHTNING_BATCH_SIZE")
            else:
                save_env_setting("PULSESCRIBE_LIGHTNING_BATCH_SIZE", str(batch_val))

        # Lightning Quantization
        if self._lightning_quant_popup:
            quant_idx = self._lightning_quant_popup.indexOfSelectedItem()
            if quant_idx == 0:  # none (default)
                remove_env_setting("PULSESCRIBE_LIGHTNING_QUANT")
            elif quant_idx == 1:
                save_env_setting("PULSESCRIBE_LIGHTNING_QUANT", "8bit")
            else:
                save_env_setting("PULSESCRIBE_LIGHTNING_QUANT", "4bit")

        # Streaming (only relevant for Deepgram mode)
        if self._streaming_checkbox and self._mode_popup:
            is_deepgram = self._mode_popup.titleOfSelectedItem() == "deepgram"
            if is_deepgram:
                enabled = self._streaming_checkbox.state() == 1
                if enabled:  # Default is true
                    remove_env_setting("PULSESCRIBE_STREAMING")
                else:
                    save_env_setting("PULSESCRIBE_STREAMING", "false")

        # Refine
        if self._refine_checkbox:
            enabled = self._refine_checkbox.state() == 1
            save_env_setting("PULSESCRIBE_REFINE", "true" if enabled else "false")

        # Clipboard Restore
        if self._clipboard_restore_checkbox:
            enabled = self._clipboard_restore_checkbox.state() == 1
            if enabled:
                save_env_setting("PULSESCRIBE_CLIPBOARD_RESTORE", "true")
            else:
                remove_env_setting("PULSESCRIBE_CLIPBOARD_RESTORE")

        # Overlay
        if self._overlay_checkbox:
            enabled = self._overlay_checkbox.state() == 1
            if enabled:  # Default is true
                remove_env_setting("PULSESCRIBE_OVERLAY")
            else:
                save_env_setting("PULSESCRIBE_OVERLAY", "false")

        # Dock Icon
        if self._dock_icon_checkbox:
            enabled = self._dock_icon_checkbox.state() == 1
            if enabled:  # Default is true
                remove_env_setting("PULSESCRIBE_DOCK_ICON")
            else:
                save_env_setting("PULSESCRIBE_DOCK_ICON", "false")

        # Refine Provider
        if self._provider_popup:
            provider = self._provider_popup.titleOfSelectedItem()
            if provider:
                save_env_setting("PULSESCRIBE_REFINE_PROVIDER", provider)

        # Refine Model
        if self._model_field:
            model = self._model_field.stringValue().strip()
            if model:
                save_env_setting("PULSESCRIBE_REFINE_MODEL", model)
            else:
                remove_env_setting("PULSESCRIBE_REFINE_MODEL")

        # Vocabulary / Keywords
        if self._vocab_text_view:
            keywords = self._get_current_keywords()
            existing_keywords = load_vocabulary().get("keywords", [])
            if keywords != existing_keywords:
                try:
                    save_vocabulary(keywords)
                    log.info(f"Saved {len(keywords)} vocabulary keywords")
                except Exception as e:
                    log.warning(f"Could not save vocabulary: {e}")
            self._update_vocabulary_warning()

        # Custom Prompts speichern
        self._save_custom_prompts()

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

    def _save_custom_prompts(self) -> None:
        """Speichert Custom Prompts und Voice Commands in prompts.toml.

        WICHTIG: Lädt erst existierende Config und mergt Session-Änderungen,
        um bereits gespeicherte Custom-Prompts nicht zu verlieren.
        """
        import logging

        from utils.custom_prompts import (
            KNOWN_CONTEXTS,
            get_defaults,
            load_custom_prompts,
            parse_app_mappings,
            save_custom_prompts,
        )

        log = logging.getLogger(__name__)

        if not hasattr(self, "_prompts_text_view") or not self._prompts_text_view:
            return

        # Aktuellen Kontext in Cache speichern
        current_ctx = getattr(self, "_prompts_current_context", "default")
        current_text = str(self._prompts_text_view.string())
        if not hasattr(self, "_prompts_cache"):
            self._prompts_cache = {}
        self._prompts_cache[current_ctx] = current_text

        # Defaults und existierende Custom-Config laden
        defaults = get_defaults()
        existing = load_custom_prompts()

        # Mit existierenden Custom-Prompts starten, dann Session-Änderungen mergen
        merged_prompts: dict = {}
        for ctx in KNOWN_CONTEXTS:
            default_prompt = defaults["prompts"].get(ctx, {}).get("prompt", "")
            existing_prompt = existing["prompts"].get(ctx, {}).get("prompt", "")
            cached_prompt = self._prompts_cache.get(ctx)

            if cached_prompt is not None:
                # User hat diesen Kontext in dieser Session bearbeitet
                if cached_prompt != default_prompt:
                    merged_prompts[ctx] = {"prompt": cached_prompt}
                # else: User hat auf Default zurückgesetzt → nicht speichern
            elif existing_prompt != default_prompt:
                # User hat diesen Kontext nicht angefasst → existierenden behalten
                merged_prompts[ctx] = {"prompt": existing_prompt}

        # Voice Commands: gleiche Logik
        merged_vc: dict = {}
        vc_key = "── Voice Commands"
        cached_vc = self._prompts_cache.get(vc_key)
        default_vc = defaults["voice_commands"]["instruction"]
        existing_vc = existing["voice_commands"]["instruction"]

        if cached_vc is not None:
            # User hat Voice Commands in dieser Session bearbeitet
            if cached_vc != default_vc:
                merged_vc = {"instruction": cached_vc}
            # else: User hat auf Default zurückgesetzt → nicht speichern
        elif existing_vc != default_vc:
            # User hat nicht angefasst → existierenden behalten
            merged_vc = {"instruction": existing_vc}

        # App Mappings: Session > Existierend > Default
        merged_app_contexts: dict = {}
        app_key = "── App Mappings"
        cached_apps = self._prompts_cache.get(app_key)
        default_apps = defaults["app_contexts"]
        existing_apps = existing["app_contexts"]

        if cached_apps is not None:
            parsed_apps = parse_app_mappings(cached_apps)
            if parsed_apps != default_apps:
                merged_app_contexts = parsed_apps
        elif existing_apps != default_apps:
            merged_app_contexts = existing_apps

        # Speichern oder Datei löschen
        if merged_prompts or merged_vc or merged_app_contexts:
            # Es gibt Custom-Werte → speichern
            data_to_save: dict = {}
            if merged_prompts:
                data_to_save["prompts"] = merged_prompts
            if merged_vc:
                data_to_save["voice_commands"] = merged_vc
            if merged_app_contexts:
                data_to_save["app_contexts"] = merged_app_contexts

            try:
                save_custom_prompts(data_to_save)
                saved_items = list(merged_prompts.keys())
                if merged_vc:
                    saved_items.append("Voice Commands")
                if merged_app_contexts:
                    saved_items.append("App Mappings")
                log.info(f"Saved custom prompts for: {saved_items}")
                if (
                    hasattr(self, "_prompts_status_label")
                    and self._prompts_status_label
                ):
                    self._prompts_status_label.setStringValue_("✓ Prompts saved")
            except Exception as e:
                log.warning(f"Could not save custom prompts: {e}")
                if (
                    hasattr(self, "_prompts_status_label")
                    and self._prompts_status_label
                ):
                    self._prompts_status_label.setStringValue_(f"Error: {e}")
        else:
            # Alles auf Default → Datei löschen falls vorhanden
            from utils.custom_prompts import reset_to_defaults

            reset_to_defaults()
            log.info("All prompts reset to defaults, removed prompts.toml")
            if hasattr(self, "_prompts_status_label") and self._prompts_status_label:
                self._prompts_status_label.setStringValue_("✓ Reset to defaults")

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


_SimpleHandler = _create_simple_handler_class()

_CheckboxHandler = _create_checkbox_handler_class()


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


_SliderHandler = _create_slider_handler_class()


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
            # Checkbox-Zustand wird direkt im Timer-Callback geprüft
            pass

    return LogsAutoRefreshHandler


_LogsAutoRefreshHandler = _create_logs_auto_refresh_handler_class()


def _create_open_logs_in_finder_handler_class():
    """Erstellt NSObject-Subklasse für Open in Finder Button."""
    from Foundation import NSObject  # type: ignore[import-not-found]
    import objc  # type: ignore[import-not-found]
    import subprocess

    class OpenLogsInFinderHandler(NSObject):
        def initWithController_(self, controller):
            self = objc.super(OpenLogsInFinderHandler, self).init()
            if self is None:
                return None
            self._controller = controller
            return self

        @objc.signature(b"v@:@")
        def openInFinder_(self, _sender) -> None:
            try:
                subprocess.Popen(["open", "-R", str(LOG_FILE)])
            except Exception:
                pass

    return OpenLogsInFinderHandler


_OpenLogsInFinderHandler = _create_open_logs_in_finder_handler_class()


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


_SetupActionHandler = _create_setup_action_handler_class()


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


_HotkeyActionHandler = _create_hotkey_action_handler_class()


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


_LogsSegmentHandler = _create_logs_segment_handler_class()


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
            self._controller._refresh_transcripts()

    return ClearTranscriptsHandler


_ClearTranscriptsHandler = _create_clear_transcripts_handler_class()
