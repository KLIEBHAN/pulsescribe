"""Windows Onboarding Wizard (PySide6).

Standalone first-run wizard analogous to macOS OnboardingWizardController.
Guides new users through: Goal Selection → Permissions → Hotkey → Test → Summary.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Callable

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.styles_windows import (
    CARD_PADDING,
    COLORS,
    DEFAULT_FONT_FAMILY,
    LANGUAGE_OPTIONS,
    get_pynput_key_map,
    get_wizard_stylesheet,
)
from utils.onboarding import (
    OnboardingChoice,
    OnboardingStep,
    next_step,
    prev_step,
    step_index,
    total_steps,
)
from utils.preferences import (
    env_file_exists,
    get_api_key,
    get_env_setting,
    get_onboarding_choice,
    get_onboarding_step,
    remove_env_setting,
    save_api_key,
    save_env_setting,
    set_onboarding_choice,
    set_onboarding_seen,
    set_onboarding_step,
)

logger = logging.getLogger("pulsescribe.onboarding")

# =============================================================================
# Window Constants
# =============================================================================

WIZARD_WIDTH = 520
WIZARD_HEIGHT = 580
PADDING = 24
FOOTER_HEIGHT = 60

# IPC Test Dictation: Timeout after 10 seconds of no daemon response
# (50 polls × 200ms interval = 10 seconds)
IPC_POLL_INTERVAL_MS = 200
IPC_MAX_POLLS_BEFORE_TIMEOUT = 50


# =============================================================================
# Helper Functions
# =============================================================================


def _create_card() -> tuple[QFrame, QVBoxLayout]:
    """Creates a styled card container."""
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
    layout.setSpacing(12)
    return card, layout


def _create_choice_button(title: str, description: str) -> QPushButton:
    """Creates a choice button with title and description."""
    btn = QPushButton()
    btn.setObjectName("choice")
    btn.setCheckable(True)
    btn.setText(f"{title}\n{description}")
    btn.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
    return btn


def _create_section_title(text: str) -> QLabel:
    """Creates a section title label."""
    label = QLabel(text)
    label.setFont(QFont(DEFAULT_FONT_FAMILY, 14, QFont.Weight.Bold))
    return label


def _create_description(text: str) -> QLabel:
    """Creates a description label."""
    label = QLabel(text)
    label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
    label.setStyleSheet(f"color: {COLORS['text_secondary']};")
    label.setWordWrap(True)
    return label


# =============================================================================
# Onboarding Wizard
# =============================================================================


class OnboardingWizardWindows(QDialog):
    """Windows Onboarding Wizard."""

    # Signals
    settings_changed = Signal()
    completed = Signal()

    def __init__(self, parent: QWidget | None = None, *, persist_progress: bool = True):
        super().__init__(parent)
        self._persist_progress = persist_progress

        # Callbacks for test dictation (optional integration)
        self._on_test_start: Callable[[], None] | None = None
        self._on_test_stop: Callable[[], None] | None = None

        # Determine initial step
        has_env = env_file_exists()
        saved_step = get_onboarding_step() if persist_progress and has_env else None
        saved_choice = get_onboarding_choice() if persist_progress and has_env else None

        if saved_step and saved_step != OnboardingStep.CHOOSE_GOAL:
            self._step = saved_step
            self._choice = saved_choice
            if self._step == OnboardingStep.DONE:
                self._step = OnboardingStep.CHEAT_SHEET
        else:
            self._step = OnboardingStep.CHOOSE_GOAL
            self._choice = None

        # UI state
        self._choice_buttons: dict[OnboardingChoice, QPushButton] = {}
        self._lang_combo: QComboBox | None = None
        self._api_key_container: QWidget | None = None
        self._api_key_field: QLineEdit | None = None
        self._api_key_status: QLabel | None = None
        self._toggle_input: QLineEdit | None = None
        self._hold_input: QLineEdit | None = None
        self._mic_status_label: QLabel | None = None
        self._test_transcript: QPlainTextEdit | None = None
        self._test_status_label: QLabel | None = None
        self._test_successful = False
        self._summary_labels: dict[str, QLabel] = {}

        # Hotkey recording state
        self._recording_field: str | None = None  # "toggle" or "hold"
        self._hotkey_listener = None
        self._pressed_keys: set = set()

        # Navigation buttons
        self._back_btn: QPushButton | None = None
        self._next_btn: QPushButton | None = None
        self._progress_label: QLabel | None = None

        # Mic check timer
        self._mic_timer: QTimer | None = None

        # IPC test dictation state
        self._ipc_client = None
        self._ipc_test_cmd_id: str | None = None
        self._ipc_poll_timer: QTimer | None = None
        self._ipc_poll_count: int = 0
        self._test_start_btn: QPushButton | None = None
        self._test_stop_btn: QPushButton | None = None
        self._test_notice: QLabel | None = None

        self._setup_ui()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def set_test_dictation_callbacks(
        self, *, start: Callable[[], None], stop: Callable[[], None]
    ) -> None:
        """Set callbacks for test dictation integration."""
        self._on_test_start = start
        self._on_test_stop = stop

    def update_test_transcript(self, text: str) -> None:
        """Update the test dictation transcript (called by daemon)."""
        if self._test_transcript:
            self._test_transcript.setPlainText(text)
            if text.strip():
                self._test_successful = True
                if self._test_status_label:
                    self._test_status_label.setText("Transkription erfolgreich!")
                    self._test_status_label.setStyleSheet(
                        f"color: {COLORS['success']};"
                    )
                self._update_navigation()

    # -------------------------------------------------------------------------
    # UI Setup
    # -------------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle("PulseScribe Setup")
        self.setFixedSize(WIZARD_WIDTH, WIZARD_HEIGHT)
        self.setStyleSheet(get_wizard_stylesheet())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header with progress
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(PADDING, PADDING, PADDING, 12)

        title = QLabel("PulseScribe Setup")
        title.setFont(QFont(DEFAULT_FONT_FAMILY, 18, QFont.Weight.Bold))
        header_layout.addWidget(title)

        self._progress_label = QLabel()
        self._progress_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        self._progress_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        header_layout.addWidget(self._progress_label)

        main_layout.addWidget(header)

        # Content area (stacked widget for steps)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_choose_goal_step())
        self._stack.addWidget(self._build_permissions_step())
        self._stack.addWidget(self._build_hotkey_step())
        self._stack.addWidget(self._build_test_dictation_step())
        self._stack.addWidget(self._build_cheat_sheet_step())
        main_layout.addWidget(self._stack, 1)

        # Footer with navigation
        footer = QWidget()
        footer.setFixedHeight(FOOTER_HEIGHT)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(PADDING, 0, PADDING, PADDING)

        self._back_btn = QPushButton("Zurück")
        self._back_btn.clicked.connect(self._go_back)
        footer_layout.addWidget(self._back_btn)

        footer_layout.addStretch()

        self._next_btn = QPushButton("Weiter")
        self._next_btn.setObjectName("primary")
        self._next_btn.clicked.connect(self._go_next)
        footer_layout.addWidget(self._next_btn)

        main_layout.addWidget(footer)

        # Show current step
        self._show_step(self._step)

    def _build_choose_goal_step(self) -> QWidget:
        """Build the goal selection step."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, PADDING)
        layout.setSpacing(16)

        layout.addWidget(_create_section_title("Wie möchtest du PulseScribe nutzen?"))
        layout.addWidget(
            _create_description(
                "Wähle eine Option basierend auf deinen Prioritäten. "
                "Du kannst die Einstellungen später jederzeit ändern."
            )
        )

        # Choice buttons
        choices = [
            (
                OnboardingChoice.FAST,
                "Schnell",
                "Cloud-basiert (Deepgram/Groq) – minimale Latenz",
            ),
            (
                OnboardingChoice.PRIVATE,
                "Privat",
                "Lokal (Whisper) – keine Daten verlassen deinen PC",
            ),
            (
                OnboardingChoice.ADVANCED,
                "Erweitert",
                "Manuelle Konfiguration – volle Kontrolle",
            ),
        ]

        for choice, title, desc in choices:
            btn = _create_choice_button(title, desc)
            btn.clicked.connect(lambda checked, c=choice: self._select_choice(c))
            self._choice_buttons[choice] = btn
            layout.addWidget(btn)

        # Restore previous choice
        if self._choice:
            self._select_choice(self._choice, save=False)

        layout.addSpacing(8)

        # Language selection
        lang_row = QHBoxLayout()
        lang_label = QLabel("Sprache:")
        lang_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        lang_row.addWidget(lang_label)

        self._lang_combo = QComboBox()
        self._lang_combo.addItems(LANGUAGE_OPTIONS)
        current_lang = get_env_setting("PULSESCRIBE_LANGUAGE") or "auto"
        if current_lang in LANGUAGE_OPTIONS:
            self._lang_combo.setCurrentText(current_lang)
        self._lang_combo.currentTextChanged.connect(self._on_language_changed)
        lang_row.addWidget(self._lang_combo)
        lang_row.addStretch()

        layout.addLayout(lang_row)

        # API Key input (shown when Fast is selected without existing key)
        api_container = QWidget()
        api_container.setVisible(False)
        api_layout = QVBoxLayout(api_container)
        api_layout.setContentsMargins(0, 8, 0, 0)
        api_layout.setSpacing(4)

        api_label = QLabel("Deepgram API Key:")
        api_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        api_layout.addWidget(api_label)

        api_row = QHBoxLayout()
        api_field = QLineEdit()
        api_field.setEchoMode(QLineEdit.EchoMode.Password)
        api_field.setPlaceholderText("dg-...")
        api_field.setFont(QFont(DEFAULT_FONT_FAMILY, 11))
        api_row.addWidget(api_field, 1)

        api_status = QLabel("")
        api_status.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        api_status.setStyleSheet(f"color: {COLORS['text_secondary']};")
        api_row.addWidget(api_status)

        api_layout.addLayout(api_row)
        layout.addWidget(api_container)

        self._api_key_container = api_container
        self._api_key_field = api_field
        self._api_key_status = api_status

        layout.addStretch()

        return widget

    def _build_permissions_step(self) -> QWidget:
        """Build the permissions step (simplified for Windows: only microphone)."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, PADDING)
        layout.setSpacing(16)

        layout.addWidget(_create_section_title("Berechtigungen"))
        layout.addWidget(
            _create_description(
                "PulseScribe benötigt Zugriff auf dein Mikrofon für die Spracherkennung."
            )
        )

        # Microphone card
        card, card_layout = _create_card()

        mic_row = QHBoxLayout()
        mic_icon = QLabel("🎤")
        mic_icon.setFont(QFont(DEFAULT_FONT_FAMILY, 16))
        mic_row.addWidget(mic_icon)

        mic_text = QVBoxLayout()
        mic_title = QLabel("Mikrofon")
        mic_title.setFont(QFont(DEFAULT_FONT_FAMILY, 11, QFont.Weight.Bold))
        mic_text.addWidget(mic_title)

        self._mic_status_label = QLabel("Wird geprüft...")
        self._mic_status_label.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        self._mic_status_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        mic_text.addWidget(self._mic_status_label)

        mic_row.addLayout(mic_text, 1)

        mic_btn = QPushButton("Einstellungen öffnen")
        mic_btn.clicked.connect(self._open_mic_settings)
        mic_row.addWidget(mic_btn)

        card_layout.addLayout(mic_row)
        layout.addWidget(card)

        # Info text
        info = QLabel(
            "Hinweis: Unter Windows sind keine weiteren Berechtigungen erforderlich. "
            "Hotkeys funktionieren systemweit ohne zusätzliche Einstellungen."
        )
        info.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        info.setStyleSheet(f"color: {COLORS['text_hint']};")
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addStretch()

        return widget

    def _build_hotkey_step(self) -> QWidget:
        """Build the hotkey configuration step."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, PADDING)
        layout.setSpacing(16)

        layout.addWidget(_create_section_title("Hotkey-Konfiguration"))
        layout.addWidget(
            _create_description(
                "Konfiguriere die Tastenkombinationen zum Starten der Aufnahme."
            )
        )

        # Hotkey card
        card, card_layout = _create_card()

        # Toggle hotkey
        toggle_row = QHBoxLayout()
        toggle_label = QLabel("Toggle-Hotkey:")
        toggle_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        toggle_label.setMinimumWidth(120)
        toggle_row.addWidget(toggle_label)

        self._toggle_input = QLineEdit()
        self._toggle_input.setPlaceholderText("Klicken zum Aufnehmen...")
        self._toggle_input.setReadOnly(True)
        self._toggle_input.setText(get_env_setting("PULSESCRIBE_TOGGLE_HOTKEY") or "")
        self._toggle_input.mousePressEvent = lambda e: self._start_hotkey_recording(
            "toggle"
        )
        toggle_row.addWidget(self._toggle_input, 1)

        toggle_clear = QPushButton("×")
        toggle_clear.setFixedWidth(32)
        toggle_clear.clicked.connect(lambda: self._clear_hotkey("toggle"))
        toggle_row.addWidget(toggle_clear)

        card_layout.addLayout(toggle_row)

        # Hold hotkey
        hold_row = QHBoxLayout()
        hold_label = QLabel("Hold-Hotkey:")
        hold_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        hold_label.setMinimumWidth(120)
        hold_row.addWidget(hold_label)

        self._hold_input = QLineEdit()
        self._hold_input.setPlaceholderText("Klicken zum Aufnehmen...")
        self._hold_input.setReadOnly(True)
        self._hold_input.setText(get_env_setting("PULSESCRIBE_HOLD_HOTKEY") or "")
        self._hold_input.mousePressEvent = lambda e: self._start_hotkey_recording(
            "hold"
        )
        hold_row.addWidget(self._hold_input, 1)

        hold_clear = QPushButton("×")
        hold_clear.setFixedWidth(32)
        hold_clear.clicked.connect(lambda: self._clear_hotkey("hold"))
        hold_row.addWidget(hold_clear)

        card_layout.addLayout(hold_row)

        # Hotkey hints
        hint = QLabel(
            "Toggle: Drücken-Sprechen-Drücken | Hold: Halten-Sprechen-Loslassen"
        )
        hint.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        hint.setStyleSheet(f"color: {COLORS['text_hint']};")
        card_layout.addWidget(hint)

        layout.addWidget(card)

        # Presets
        presets_label = QLabel("Schnellauswahl:")
        presets_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10, QFont.Weight.Bold))
        layout.addWidget(presets_label)

        presets_row = QHBoxLayout()

        preset_f19 = QPushButton("F19 Toggle")
        preset_f19.clicked.connect(lambda: self._apply_hotkey_preset("f19", None))
        presets_row.addWidget(preset_f19)

        preset_ctrl_alt = QPushButton("Ctrl+Alt+R / Space")
        preset_ctrl_alt.clicked.connect(
            lambda: self._apply_hotkey_preset("ctrl+alt+r", "ctrl+alt+space")
        )
        presets_row.addWidget(preset_ctrl_alt)

        preset_f13 = QPushButton("F13 Toggle")
        preset_f13.clicked.connect(lambda: self._apply_hotkey_preset("f13", None))
        presets_row.addWidget(preset_f13)

        presets_row.addStretch()
        layout.addLayout(presets_row)

        layout.addStretch()

        return widget

    def _build_test_dictation_step(self) -> QWidget:
        """Build the test dictation step."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, PADDING)
        layout.setSpacing(16)

        layout.addWidget(_create_section_title("Teste die Diktierfunktion"))
        layout.addWidget(
            _create_description(
                "Drücke deinen Hotkey und sprich etwas. Der transkribierte Text erscheint unten."
            )
        )

        # Hotkey reminder
        toggle = get_env_setting("PULSESCRIBE_TOGGLE_HOTKEY")
        hold = get_env_setting("PULSESCRIBE_HOLD_HOTKEY")
        hotkey_text = []
        if toggle:
            hotkey_text.append(f"Toggle: {toggle}")
        if hold:
            hotkey_text.append(f"Hold: {hold}")

        hotkey_label = QLabel(
            " | ".join(hotkey_text) if hotkey_text else "Kein Hotkey konfiguriert"
        )
        hotkey_label.setFont(QFont(DEFAULT_FONT_FAMILY, 11, QFont.Weight.Bold))
        hotkey_label.setStyleSheet(f"color: {COLORS['accent']};")
        layout.addWidget(hotkey_label)

        # Transcript area
        card, card_layout = _create_card()

        self._test_status_label = QLabel("Warte auf Hotkey...")
        self._test_status_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        self._test_status_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        card_layout.addWidget(self._test_status_label)

        self._test_transcript = QPlainTextEdit()
        self._test_transcript.setPlaceholderText(
            "Transkribierter Text erscheint hier..."
        )
        self._test_transcript.setReadOnly(True)
        self._test_transcript.setMinimumHeight(120)
        card_layout.addWidget(self._test_transcript)

        layout.addWidget(card)

        # Test buttons (IPC-based when daemon is running)
        btn_row = QHBoxLayout()

        self._test_start_btn = QPushButton("Test starten")
        self._test_start_btn.clicked.connect(self._start_ipc_test)
        btn_row.addWidget(self._test_start_btn)

        self._test_stop_btn = QPushButton("Stoppen")
        self._test_stop_btn.clicked.connect(self._stop_ipc_test)
        self._test_stop_btn.setVisible(False)
        btn_row.addWidget(self._test_stop_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Info text (shown when daemon not running)
        self._test_notice = QLabel(
            "Tipp: Starte PulseScribe im Hintergrund, um den Test hier durchzuführen."
        )
        self._test_notice.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        self._test_notice.setStyleSheet(f"color: {COLORS['text_secondary']};")
        self._test_notice.setWordWrap(True)
        layout.addWidget(self._test_notice)

        # Skip link
        skip_btn = QPushButton("Überspringen →")
        skip_btn.setFlat(True)
        skip_btn.setStyleSheet(
            f"color: {COLORS['text_secondary']}; text-decoration: underline; border: none;"
        )
        skip_btn.clicked.connect(self._skip_test)
        layout.addWidget(skip_btn, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addStretch()

        return widget

    def _build_cheat_sheet_step(self) -> QWidget:
        """Build the summary/cheat sheet step."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, PADDING)
        layout.setSpacing(16)

        layout.addWidget(_create_section_title("Alles bereit!"))
        layout.addWidget(
            _create_description(
                "PulseScribe ist konfiguriert. Hier eine Zusammenfassung deiner Einstellungen:"
            )
        )

        # Summary card
        card, card_layout = _create_card()

        # Provider/Mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Modus:"))
        self._summary_labels["mode"] = QLabel()
        self._summary_labels["mode"].setStyleSheet(f"color: {COLORS['accent']};")
        mode_row.addWidget(self._summary_labels["mode"], 1)
        card_layout.addLayout(mode_row)

        # Hotkeys
        hotkey_row = QHBoxLayout()
        hotkey_row.addWidget(QLabel("Hotkeys:"))
        self._summary_labels["hotkeys"] = QLabel()
        self._summary_labels["hotkeys"].setStyleSheet(f"color: {COLORS['accent']};")
        self._summary_labels["hotkeys"].setWordWrap(True)
        hotkey_row.addWidget(self._summary_labels["hotkeys"], 1)
        card_layout.addLayout(hotkey_row)

        # Language
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Sprache:"))
        self._summary_labels["language"] = QLabel()
        self._summary_labels["language"].setStyleSheet(f"color: {COLORS['accent']};")
        lang_row.addWidget(self._summary_labels["language"], 1)
        card_layout.addLayout(lang_row)

        layout.addWidget(card)

        # Ready message
        ready = QLabel("Du kannst jetzt mit dem Diktieren beginnen!")
        ready.setFont(QFont(DEFAULT_FONT_FAMILY, 11))
        ready.setStyleSheet(f"color: {COLORS['success']};")
        layout.addWidget(ready)

        # Open settings button
        settings_btn = QPushButton("Einstellungen öffnen...")
        settings_btn.clicked.connect(self._open_settings_after)
        layout.addWidget(settings_btn)

        layout.addStretch()

        return widget

    # -------------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------------

    def _show_step(self, step: OnboardingStep) -> None:
        """Show the specified step."""
        # Stop mic timer when leaving PERMISSIONS step
        if (
            self._step == OnboardingStep.PERMISSIONS
            and step != OnboardingStep.PERMISSIONS
        ):
            if self._mic_timer:
                self._mic_timer.stop()

        # Stop IPC polling when leaving TEST_DICTATION step
        if (
            self._step == OnboardingStep.TEST_DICTATION
            and step != OnboardingStep.TEST_DICTATION
        ):
            self._stop_ipc_polling()

        self._step = step

        # Update stack index
        step_indices = {
            OnboardingStep.CHOOSE_GOAL: 0,
            OnboardingStep.PERMISSIONS: 1,
            OnboardingStep.HOTKEY: 2,
            OnboardingStep.TEST_DICTATION: 3,
            OnboardingStep.CHEAT_SHEET: 4,
        }
        self._stack.setCurrentIndex(step_indices.get(step, 0))

        # Update progress label
        if self._progress_label:
            idx = step_index(step)
            total = total_steps()
            self._progress_label.setText(f"Schritt {idx} von {total}")

        # Step-specific actions
        if step == OnboardingStep.PERMISSIONS:
            self._start_mic_check()
        elif step == OnboardingStep.CHEAT_SHEET:
            self._update_summary()

        self._update_navigation()

        # Persist progress
        if self._persist_progress:
            set_onboarding_step(step)

    def _update_navigation(self) -> None:
        """Update navigation button states."""
        if not self._back_btn or not self._next_btn:
            return

        # Back button
        self._back_btn.setVisible(self._step != OnboardingStep.CHOOSE_GOAL)

        # Next button text and state
        if self._step == OnboardingStep.CHEAT_SHEET:
            self._next_btn.setText("Fertig")
        else:
            self._next_btn.setText("Weiter")

        # Enable/disable based on step requirements
        can_advance = self._can_advance()
        self._next_btn.setEnabled(can_advance)

    def _can_advance(self) -> bool:
        """Check if user can advance to next step."""
        if self._step == OnboardingStep.CHOOSE_GOAL:
            if self._choice is None:
                return False
            # Fast mode requires API key
            if self._choice == OnboardingChoice.FAST:
                return self._has_api_key()
            return True
        elif self._step == OnboardingStep.HOTKEY:
            # At least one hotkey must be configured
            toggle = get_env_setting("PULSESCRIBE_TOGGLE_HOTKEY")
            hold = get_env_setting("PULSESCRIBE_HOLD_HOTKEY")
            return bool(toggle or hold)
        return True

    def _go_next(self) -> None:
        """Navigate to next step."""
        self._stop_hotkey_recording()

        # Save API key when leaving CHOOSE_GOAL with FAST mode
        if (
            self._step == OnboardingStep.CHOOSE_GOAL
            and self._choice == OnboardingChoice.FAST
        ):
            if self._api_key_field:
                entered_key = self._api_key_field.text().strip()
                if entered_key:
                    save_api_key("DEEPGRAM_API_KEY", entered_key)
            set_onboarding_choice(self._choice)

        if self._step == OnboardingStep.CHEAT_SHEET:
            self._complete()
        else:
            self._show_step(next_step(self._step))

    def _go_back(self) -> None:
        """Navigate to previous step."""
        self._stop_hotkey_recording()
        self._show_step(prev_step(self._step))

    def _skip_test(self) -> None:
        """Skip the test dictation step."""
        self._show_step(OnboardingStep.CHEAT_SHEET)

    # -------------------------------------------------------------------------
    # IPC Test Dictation
    # -------------------------------------------------------------------------
    #
    # The wizard runs as a separate subprocess from the daemon.
    # To test dictation, we communicate via JSON files:
    #   1. Wizard sends CMD_START_TEST → daemon starts recording
    #   2. Daemon sends STATUS_RECORDING → wizard shows "speak now"
    #   3. User clicks stop → wizard sends CMD_STOP_TEST
    #   4. Daemon sends STATUS_DONE + transcript → wizard displays result

    def _start_ipc_test(self) -> None:
        """Request the daemon to start recording via IPC."""
        from utils.ipc import CMD_START_TEST, IPCClient

        if self._ipc_client is None:
            self._ipc_client = IPCClient()

        self._ipc_test_cmd_id = self._ipc_client.send_command(CMD_START_TEST)
        self._ipc_poll_count = 0

        # Show "connecting" state while waiting for daemon acknowledgment
        self._set_test_status("Verbinde mit PulseScribe...", "text_secondary")
        self._test_start_btn and self._test_start_btn.setVisible(False)
        self._test_stop_btn and self._test_stop_btn.setVisible(True)
        self._test_notice and self._test_notice.setVisible(False)

        # Poll for daemon response every 200ms
        if self._ipc_poll_timer is None:
            self._ipc_poll_timer = QTimer(self)
            self._ipc_poll_timer.timeout.connect(self._poll_ipc_response)
        self._ipc_poll_timer.start(IPC_POLL_INTERVAL_MS)

        logger.debug(f"IPC test started (cmd_id={self._ipc_test_cmd_id})")

    def _stop_ipc_test(self) -> None:
        """Stop test dictation via IPC."""
        from utils.ipc import CMD_STOP_TEST

        if self._ipc_client and self._ipc_test_cmd_id:
            self._ipc_client.send_command(CMD_STOP_TEST)

        if self._test_status_label:
            self._test_status_label.setText("Wird gestoppt...")

    def _poll_ipc_response(self) -> None:
        """Poll for IPC response from daemon."""
        from utils.ipc import (
            STATUS_DONE,
            STATUS_ERROR,
            STATUS_RECORDING,
            STATUS_STOPPED,
        )

        if not self._ipc_client or not self._ipc_test_cmd_id:
            return

        response = self._ipc_client.poll_response(self._ipc_test_cmd_id)
        if not response:
            self._ipc_poll_count += 1
            if self._ipc_poll_count >= IPC_MAX_POLLS_BEFORE_TIMEOUT:
                self._stop_ipc_polling()
                self._on_ipc_test_complete("", "Keine Verbindung zu PulseScribe")
            return

        status = response.get("status")
        logger.debug(f"IPC response: {status}")

        if status == STATUS_RECORDING:
            if self._test_status_label:
                self._test_status_label.setText("Aufnahme läuft... Sprich jetzt!")
                self._test_status_label.setStyleSheet(f"color: {COLORS['accent']};")

        elif status == STATUS_DONE:
            self._stop_ipc_polling()
            transcript = response.get("transcript", "")
            self._on_ipc_test_complete(transcript, None)

        elif status == STATUS_ERROR:
            self._stop_ipc_polling()
            error = response.get("error", "Unbekannter Fehler")
            self._on_ipc_test_complete("", error)

        elif status == STATUS_STOPPED:
            self._stop_ipc_polling()
            self._reset_test_ui()

    def _stop_ipc_polling(self) -> None:
        """Stop polling and clean up IPC state."""
        if self._ipc_poll_timer:
            self._ipc_poll_timer.stop()
        if self._ipc_client:
            self._ipc_client.clear_response()
        self._ipc_test_cmd_id = None

    def _on_ipc_test_complete(self, transcript: str, error: str | None) -> None:
        """Display test result based on outcome."""
        self._reset_test_ui()

        if error:
            self._show_test_error(error)
        elif transcript.strip():
            self._show_test_success(transcript)
        else:
            self._show_test_no_speech()

    def _show_test_error(self, error: str) -> None:
        """Display error state with troubleshooting hint."""
        self._test_transcript and self._test_transcript.clear()
        self._set_test_status(f"Fehler: {error}", "error")
        if self._test_notice:
            self._test_notice.setVisible(True)
            self._test_notice.setText(
                "Tipp: Stelle sicher, dass PulseScribe im Hintergrund läuft."
            )

    def _show_test_success(self, transcript: str) -> None:
        """Display successful transcription."""
        self._test_transcript and self._test_transcript.setPlainText(transcript)
        self._set_test_status("Erfolgreich!", "success")
        self._test_successful = True
        self._update_navigation()

    def _show_test_no_speech(self) -> None:
        """Display "no speech detected" state."""
        self._test_transcript and self._test_transcript.clear()
        self._set_test_status("Keine Sprache erkannt. Nochmal versuchen?", "warning")

    def _set_test_status(self, text: str, color_key: str) -> None:
        """Update the test status label with colored text."""
        if self._test_status_label:
            self._test_status_label.setText(text)
            self._test_status_label.setStyleSheet(f"color: {COLORS[color_key]};")

    def _reset_test_ui(self) -> None:
        """Show start button, hide stop button."""
        self._test_start_btn and self._test_start_btn.setVisible(True)
        self._test_stop_btn and self._test_stop_btn.setVisible(False)

    def _complete(self) -> None:
        """Complete the wizard."""
        set_onboarding_step(OnboardingStep.DONE)
        set_onboarding_seen(True)
        self.completed.emit()
        self.accept()

    def _open_settings_after(self) -> None:
        """Mark for opening settings after completion."""
        self._complete()

    # -------------------------------------------------------------------------
    # Step: Choose Goal
    # -------------------------------------------------------------------------

    def _select_choice(self, choice: OnboardingChoice, save: bool = True) -> None:
        """Handle choice selection."""
        self._choice = choice

        # Update button states
        for c, btn in self._choice_buttons.items():
            btn.setChecked(c == choice)

        # Show/hide API key input based on choice
        if self._api_key_container:
            show_api = choice == OnboardingChoice.FAST and not self._has_api_key()
            self._api_key_container.setVisible(show_api)
            if show_api and self._api_key_status:
                self._api_key_status.setText("Erforderlich für Fast-Modus")

        if save:
            set_onboarding_choice(choice)
            self._apply_choice_preset(choice)
            self.settings_changed.emit()

        self._update_navigation()

    def _has_api_key(self) -> bool:
        """Check if a Deepgram API key exists (entered or saved)."""
        import os

        entered_key = ""
        if self._api_key_field:
            entered_key = self._api_key_field.text().strip()
        return bool(
            entered_key
            or get_api_key("DEEPGRAM_API_KEY")
            or os.getenv("DEEPGRAM_API_KEY")
        )

    def _apply_choice_preset(self, choice: OnboardingChoice) -> None:
        """Apply the preset for the selected choice."""
        import os

        from utils.presets import (
            apply_local_preset_to_env,
            default_local_preset_private,
        )

        if choice == OnboardingChoice.FAST:
            # Save entered API key if present
            if self._api_key_field:
                entered_key = self._api_key_field.text().strip()
                if entered_key:
                    save_api_key("DEEPGRAM_API_KEY", entered_key)

            # Check for API keys
            has_deepgram = bool(
                get_api_key("DEEPGRAM_API_KEY") or os.getenv("DEEPGRAM_API_KEY")
            )
            has_groq = bool(get_api_key("GROQ_API_KEY") or os.getenv("GROQ_API_KEY"))

            if has_deepgram:
                save_env_setting("PULSESCRIBE_MODE", "deepgram")
                if self._api_key_container:
                    self._api_key_container.setVisible(False)
            elif has_groq:
                save_env_setting("PULSESCRIBE_MODE", "groq")
                if self._api_key_container:
                    self._api_key_container.setVisible(False)
            else:
                # Show API key input, don't apply preset yet
                if self._api_key_container:
                    self._api_key_container.setVisible(True)
                    if self._api_key_status:
                        self._api_key_status.setText("Erforderlich für Fast-Modus")

        elif choice == OnboardingChoice.PRIVATE:
            save_env_setting("PULSESCRIBE_MODE", "local")
            apply_local_preset_to_env(default_local_preset_private())
        # ADVANCED: No automatic configuration

    def _on_language_changed(self, lang: str) -> None:
        """Handle language selection change."""
        if lang == "auto":
            remove_env_setting("PULSESCRIBE_LANGUAGE")
        else:
            save_env_setting("PULSESCRIBE_LANGUAGE", lang)
        self.settings_changed.emit()

    # -------------------------------------------------------------------------
    # Step: Permissions
    # -------------------------------------------------------------------------

    def _start_mic_check(self) -> None:
        """Start periodic microphone permission check."""
        self._check_mic_permission()

        if self._mic_timer is None:
            self._mic_timer = QTimer(self)
            self._mic_timer.timeout.connect(self._check_mic_permission)

        timer = self._mic_timer
        timer.start(2000)  # Check every 2 seconds

    def _check_mic_permission(self) -> None:
        """Check microphone permission status."""
        if not self._mic_status_label:
            return

        # On Windows, we can't directly check mic permission without attempting recording.
        # We'll show a generic "ready" status and let the user test in the next step.
        self._mic_status_label.setText("Bereit (wird beim Test geprüft)")
        self._mic_status_label.setStyleSheet(f"color: {COLORS['success']};")

    def _open_mic_settings(self) -> None:
        """Open Windows microphone settings."""
        try:
            # os.startfile ist die sichere Windows-API (kein shell=True nötig)
            import os
            os.startfile("ms-settings:privacy-microphone")
        except Exception as e:
            logger.warning(f"Konnte Einstellungen nicht öffnen: {e}")

    # -------------------------------------------------------------------------
    # Step: Hotkey
    # -------------------------------------------------------------------------

    def _start_hotkey_recording(self, field: str) -> None:
        """Start recording a hotkey."""
        self._stop_hotkey_recording()
        self._recording_field = field

        input_field = self._toggle_input if field == "toggle" else self._hold_input
        if input_field:
            input_field.setText("Drücke Tastenkombination...")
            input_field.setStyleSheet(f"border-color: {COLORS['accent']};")

        self._pressed_keys = set()

        available, key_map = get_pynput_key_map()
        if not available:
            logger.warning("pynput nicht verfügbar")
            return

        from pynput import keyboard

        def on_press(key):
            key_name = key_map.get(key)
            if key_name:
                self._pressed_keys.add(key_name)
            elif hasattr(key, "char") and key.char:
                self._pressed_keys.add(key.char.lower())
            elif hasattr(key, "vk") and key.vk:
                # Function keys
                if 112 <= key.vk <= 135:  # F1-F24
                    self._pressed_keys.add(f"f{key.vk - 111}")

        def on_release(key):
            if self._pressed_keys:
                # Build hotkey string
                modifiers = []
                main_key = None
                for k in sorted(self._pressed_keys):
                    if k in ("ctrl", "alt", "shift", "win"):
                        modifiers.append(k)
                    else:
                        main_key = k

                if main_key:
                    hotkey = "+".join(modifiers + [main_key])
                elif len(modifiers) >= 2:
                    hotkey = "+".join(modifiers)
                else:
                    return  # Not a valid hotkey

                # Apply hotkey and stop listener
                QTimer.singleShot(0, lambda: self._apply_recorded_hotkey(hotkey))
                if self._hotkey_listener:
                    self._hotkey_listener.stop()

        self._hotkey_listener = keyboard.Listener(
            on_press=on_press, on_release=on_release
        )
        self._hotkey_listener.start()

    def _stop_hotkey_recording(self) -> None:
        """Stop hotkey recording."""
        if self._hotkey_listener:
            self._hotkey_listener.stop()
            self._hotkey_listener = None

        self._recording_field = None
        self._pressed_keys = set()

        # Reset input styles
        for input_field in (self._toggle_input, self._hold_input):
            if input_field:
                input_field.setStyleSheet("")

    def _apply_recorded_hotkey(self, hotkey: str) -> None:
        """Apply a recorded hotkey."""
        field = self._recording_field
        self._stop_hotkey_recording()

        if field == "toggle":
            if self._toggle_input:
                self._toggle_input.setText(hotkey)
            save_env_setting("PULSESCRIBE_TOGGLE_HOTKEY", hotkey)
        elif field == "hold":
            if self._hold_input:
                self._hold_input.setText(hotkey)
            save_env_setting("PULSESCRIBE_HOLD_HOTKEY", hotkey)

        self.settings_changed.emit()
        self._update_navigation()

    def _clear_hotkey(self, field: str) -> None:
        """Clear a hotkey."""
        if field == "toggle":
            if self._toggle_input:
                self._toggle_input.setText("")
            remove_env_setting("PULSESCRIBE_TOGGLE_HOTKEY")
        elif field == "hold":
            if self._hold_input:
                self._hold_input.setText("")
            remove_env_setting("PULSESCRIBE_HOLD_HOTKEY")

        self.settings_changed.emit()
        self._update_navigation()

    def _apply_hotkey_preset(self, toggle: str | None, hold: str | None) -> None:
        """Apply a hotkey preset."""
        if toggle:
            if self._toggle_input:
                self._toggle_input.setText(toggle)
            save_env_setting("PULSESCRIBE_TOGGLE_HOTKEY", toggle)
        else:
            if self._toggle_input:
                self._toggle_input.setText("")
            remove_env_setting("PULSESCRIBE_TOGGLE_HOTKEY")

        if hold:
            if self._hold_input:
                self._hold_input.setText(hold)
            save_env_setting("PULSESCRIBE_HOLD_HOTKEY", hold)
        else:
            if self._hold_input:
                self._hold_input.setText("")
            remove_env_setting("PULSESCRIBE_HOLD_HOTKEY")

        self.settings_changed.emit()
        self._update_navigation()

    # -------------------------------------------------------------------------
    # Step: Cheat Sheet
    # -------------------------------------------------------------------------

    def _update_summary(self) -> None:
        """Update the summary labels."""
        # Mode
        mode = get_env_setting("PULSESCRIBE_MODE") or "deepgram"
        if "mode" in self._summary_labels:
            self._summary_labels["mode"].setText(mode.capitalize())

        # Hotkeys
        toggle = get_env_setting("PULSESCRIBE_TOGGLE_HOTKEY")
        hold = get_env_setting("PULSESCRIBE_HOLD_HOTKEY")
        hotkey_parts = []
        if toggle:
            hotkey_parts.append(f"Toggle: {toggle}")
        if hold:
            hotkey_parts.append(f"Hold: {hold}")
        if "hotkeys" in self._summary_labels:
            self._summary_labels["hotkeys"].setText(
                " | ".join(hotkey_parts) if hotkey_parts else "Nicht konfiguriert"
            )

        # Language
        lang = get_env_setting("PULSESCRIBE_LANGUAGE") or "auto"
        if "language" in self._summary_labels:
            self._summary_labels["language"].setText(lang)

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Handle window close."""
        self._stop_hotkey_recording()
        self._stop_ipc_polling()
        if self._mic_timer:
            self._mic_timer.stop()
        super().closeEvent(event)

    def reject(self) -> None:
        """Handle ESC key or Cancel - ensures proper cleanup."""
        self._stop_hotkey_recording()
        self._stop_ipc_polling()
        if self._mic_timer:
            self._mic_timer.stop()
        super().reject()


# =============================================================================
# Standalone Test
# =============================================================================


def main():
    """Test the wizard standalone."""
    app = QApplication(sys.argv)
    wizard = OnboardingWizardWindows()
    wizard.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
