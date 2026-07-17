"""Windows Onboarding Wizard (PySide6).

Standalone first-run wizard analogous to macOS OnboardingWizardController.
Guides new users through: Goal Selection → Permissions → Hotkey → Test → Summary.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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

from ui.hotkey_format import format_hotkey_for_display
from ui.qt_widget_state import (
    get_widget_enabled,
    get_widget_stylesheet,
    get_widget_text,
    get_widget_visible,
    set_widget_enabled_if_changed,
    set_widget_stylesheet_if_changed,
    set_widget_text_if_changed,
    set_widget_visible_if_changed,
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
    read_env_file,
    set_onboarding_choice,
    set_onboarding_seen,
    set_onboarding_step,
    update_env_settings,
)
from utils.hotkey_windows import hotkeys_conflict, normalize_windows_hotkey

logger = logging.getLogger("pulsescribe.onboarding")

# =============================================================================
# Window Constants
# =============================================================================

WIZARD_WIDTH = 520
WIZARD_HEIGHT = 580
PADDING = 24
FOOTER_HEIGHT = 60

# IPC Test Dictation
# Schneller Fail bei fehlender Verbindung (kein RECORDING-Ack):
#   15 polls × 200ms = 3s
IPC_CONNECT_MAX_POLLS_BEFORE_TIMEOUT = 15
# Timeout nach RECORDING-Ack (Finale Antwort darf länger dauern):
#   50 polls × 200ms = 10s
IPC_POLL_INTERVAL_MS = 200
IPC_RECORDING_IDLE_POLL_INTERVAL_MS = 350
IPC_MAX_POLLS_BEFORE_TIMEOUT = 50
# If daemon keeps reporting STATUS_RECORDING after stop, treat it as stale to
# avoid a long/hanging "recording..." spinner in the wizard.
#   30 polls × 200ms = 6s
IPC_RECORDING_STALE_POLLS_AFTER_STOP = 30
DEFAULT_WINDOWS_TOGGLE_HOTKEY = "ctrl+alt+r"
DEFAULT_WINDOWS_HOLD_HOTKEY = "ctrl+win"

LANGUAGE_LABELS = {
    "auto": "Automatisch",
    "de": "Deutsch",
    "en": "Englisch",
    "es": "Spanisch",
    "fr": "Französisch",
    "it": "Italienisch",
    "pt": "Portugiesisch",
    "nl": "Niederländisch",
    "pl": "Polnisch",
    "ru": "Russisch",
    "zh": "Chinesisch",
}

MODE_LABELS = {
    "deepgram": "Deepgram (Cloud, schnell)",
    "groq": "Groq (Cloud, schnell)",
    "openai": "OpenAI (Cloud)",
    "local": "Lokal / Whisper (privat)",
}

HOTKEY_TOKEN_LABELS = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Win",
    "space": "Space",
    "tab": "Tab",
    "enter": "Enter",
    "esc": "Esc",
    "backspace": "Backspace",
    "delete": "Delete",
    "home": "Home",
    "end": "End",
    "pageup": "Page Up",
    "pagedown": "Page Down",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "capslock": "Caps Lock",
}

TEST_TRANSCRIPT_DEFAULT_TEXT = (
    "Hier erscheint dein Testtext.\n"
    "Während dieses Schritts wird nichts eingefügt."
)
TEST_TRANSCRIPT_CONNECTING_TEXT = (
    "Die Testaufnahme wird vorbereitet.\n"
    "Beim ersten Versuch kann das einen Moment dauern."
)
TEST_TRANSCRIPT_RECORDING_TEXT = (
    "Aufnahme läuft.\n"
    "Sprich jetzt einen kurzen Satz und stoppe die Aufnahme danach."
)
TEST_TRANSCRIPT_PROCESSING_TEXT = (
    "Die Aufnahme wird ausgewertet.\n"
    "Der erkannte Text erscheint gleich hier."
)
TEST_TRANSCRIPT_NO_SPEECH_TEXT = (
    "Es wurde keine Sprache erkannt.\n"
    "Versuche es erneut mit einem kurzen Satz wie „Hallo, dies ist ein Test.“"
)
TEST_TRANSCRIPT_ERROR_TEXT = (
    "Der Test konnte nicht abgeschlossen werden.\n"
    "Prüfe den Hinweis darunter und versuche es erneut."
)
TEST_TRANSCRIPT_CANCELLED_TEXT = (
    "Der Test wurde abgebrochen.\n"
    "Du kannst ihn jederzeit erneut starten."
)


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


def _get_widget_text(widget) -> str | None:
    return get_widget_text(widget)


def _set_widget_text_if_changed(widget, text: str) -> bool:
    return set_widget_text_if_changed(widget, text)


def _get_widget_stylesheet(widget) -> str | None:
    return get_widget_stylesheet(widget)


def _set_widget_stylesheet_if_changed(widget, style: str) -> bool:
    return set_widget_stylesheet_if_changed(widget, style)


def _get_widget_visible(widget) -> bool | None:
    return get_widget_visible(widget)


def _set_widget_visible_if_changed(widget, visible: bool) -> bool:
    return set_widget_visible_if_changed(widget, visible)


def _get_widget_enabled(widget) -> bool | None:
    return get_widget_enabled(widget)


def _set_widget_enabled_if_changed(widget, enabled: bool) -> bool:
    return set_widget_enabled_if_changed(widget, enabled)


def _set_timer_interval_if_supported(timer, interval_ms: int) -> bool:
    if timer is None:
        return False
    getter = getattr(timer, "interval", None)
    if callable(getter):
        try:
            if int(getter()) == interval_ms:
                return False
        except (TypeError, ValueError):
            pass
    else:
        current = getattr(timer, "interval", None)
        if isinstance(current, int) and current == interval_ms:
            return False
    setter = getattr(timer, "setInterval", None)
    if not callable(setter):
        return False
    setter(interval_ms)
    return True


def _set_plain_text_if_changed(widget, text: str) -> bool:
    if widget is None:
        return False

    getter = getattr(widget, "toPlainText", None)
    if callable(getter):
        try:
            if str(getter()) == text:
                return False
        except TypeError:
            pass
    else:
        current = getattr(widget, "value", None)
        if isinstance(current, str) and current == text:
            return False

    widget.setPlainText(text)
    return True


def _format_mode_label(mode: str | None) -> str:
    normalized = (mode or "").strip().lower()
    if not normalized:
        normalized = "deepgram"
    return MODE_LABELS.get(normalized, normalized.capitalize())


def _format_language_label(language: str | None) -> str:
    normalized = (language or "").strip().lower()
    if not normalized:
        normalized = "auto"
    return LANGUAGE_LABELS.get(normalized, normalized.upper())


def _format_hotkey_for_display(hotkey: str | None) -> str:
    return format_hotkey_for_display(
        hotkey,
        HOTKEY_TOKEN_LABELS,
        omit_empty_parts=True,
    )


def _format_hotkey_summary_text(
    toggle: str, hold: str, *, separator: str = " • "
) -> str:
    parts: list[str] = []
    if toggle:
        parts.append(f"Toggle: {_format_hotkey_for_display(toggle)}")
    if hold:
        parts.append(f"Hold: {_format_hotkey_for_display(hold)}")
    return separator.join(parts)


def _normalize_test_error_message(error: str | None) -> str:
    detail = " ".join((error or "").split())
    if not detail:
        return "Der Test konnte nicht abgeschlossen werden."

    detail_lower = detail.lower()
    if "keine verbindung" in detail_lower:
        return "Keine Verbindung zu PulseScribe."
    if "bereits in aufnahme" in detail_lower or "busy" in detail_lower:
        return "PulseScribe verarbeitet gerade noch eine andere Aufnahme."
    if "keine finale antwort" in detail_lower:
        return "PulseScribe hat noch kein fertiges Ergebnis zurückgegeben."
    if "mikro" in detail_lower:
        return "Das Mikrofon ist für den Test gerade nicht verfügbar."
    if "aufnahme konnte nicht gestartet werden" in detail_lower:
        return "Die Testaufnahme konnte nicht gestartet werden."

    if detail[0].islower():
        detail = detail[0].upper() + detail[1:]
    return detail


def _build_test_status_text(state: str | None, *, error: str | None = None) -> str:
    normalized = (state or "pending").strip().lower()
    if normalized == "connecting":
        return "Verbinde mit PulseScribe…"
    if normalized == "recording":
        return "Aufnahme läuft — sprich jetzt."
    if normalized == "processing":
        return "Verarbeite Testaufnahme…"
    if normalized == "passed":
        return "Erfolgreich — du kannst jetzt fortfahren."
    if normalized == "no_speech":
        return "Keine Sprache erkannt."
    if normalized == "error":
        return f"Fehler — {_normalize_test_error_message(error)}"
    if normalized == "cancelled":
        return "Test abgebrochen."
    if normalized == "skipped":
        return "Test übersprungen."
    return "Bereit für einen sicheren Test."


def _build_test_notice_feedback(
    state: str | None,
    *,
    error: str | None = None,
) -> tuple[str, str]:
    normalized = (state or "pending").strip().lower()
    if normalized == "error":
        return _build_test_error_notice_feedback(error)
    return _TEST_NOTICE_FEEDBACK.get(normalized, _DEFAULT_TEST_NOTICE_FEEDBACK)


_TEST_NOTICE_FEEDBACK = {
    "connecting": (
        "PulseScribe wird im Hintergrund kontaktiert. Beim ersten Test kann das ein paar Sekunden dauern.",
        "text_secondary",
    ),
    "recording": (
        "Sprich jetzt einen kurzen Satz. Der Text wird nur hier im Assistenten angezeigt.",
        "accent",
    ),
    "processing": (
        "Die Aufnahme wird gerade ausgewertet. Warte kurz auf das Ergebnis hier im Assistenten.",
        "text_secondary",
    ),
    "passed": (
        "Alles gut: Nichts wurde eingefügt. Mit „Weiter“ kommst du zur Zusammenfassung.",
        "success",
    ),
    "no_speech": (
        "Tipp: Prüfe Mikrofonabstand und Eingabegerät und versuche es danach erneut.",
        "warning",
    ),
    "cancelled": (
        "Kein Problem — starte den Test erneut, sobald PulseScribe bereit ist.",
        "text_secondary",
    ),
    "skipped": (
        "Du kannst den Test später jederzeit erneut im Setup durchführen.",
        "text_secondary",
    ),
}

_DEFAULT_TEST_NOTICE_FEEDBACK = (
    "Ablauf: Test starten, einen kurzen Satz sprechen und die Aufnahme danach wieder stoppen.",
    "text_secondary",
)


def _build_test_error_notice_feedback(error: str | None) -> tuple[str, str]:
    detail_lower = " ".join((error or "").split()).lower()
    if "keine verbindung" in detail_lower:
        return (
            "Prüfe, ob PulseScribe im Hintergrund läuft, und starte den Test danach erneut.",
            "warning",
        )
    if "bereits in aufnahme" in detail_lower or "busy" in detail_lower:
        return (
            "Warte kurz, bis die aktuelle Aufnahme beendet ist, und versuche es dann erneut.",
            "warning",
        )
    if "mikro" in detail_lower:
        return (
            "Prüfe den Mikrofonzugriff und das richtige Eingabegerät in Windows und versuche es dann erneut.",
            "warning",
        )
    return (
        "Prüfe, ob PulseScribe läuft und dein Mikrofon verfügbar ist, und versuche es dann erneut.",
        "warning",
    )


def _normalize_env_updates(
    updates: dict[str, str | None],
) -> dict[str, str | None]:
    return {
        key: None if value is None else str(value)
        for key, value in updates.items()
    }


def _env_updates_changed(
    cache: dict[str, str],
    updates: dict[str, str | None],
) -> bool:
    return any(_env_update_changed(cache, key, value) for key, value in updates.items())


def _env_update_changed(
    cache: dict[str, str],
    key: str,
    value: str | None,
) -> bool:
    current = cache.get(key)
    if value is None:
        return current is not None
    return current != value


def _build_test_transcript_text(state: str | None) -> str:
    normalized = (state or "pending").strip().lower()
    if normalized == "connecting":
        return TEST_TRANSCRIPT_CONNECTING_TEXT
    if normalized == "recording":
        return TEST_TRANSCRIPT_RECORDING_TEXT
    if normalized == "processing":
        return TEST_TRANSCRIPT_PROCESSING_TEXT
    if normalized == "no_speech":
        return TEST_TRANSCRIPT_NO_SPEECH_TEXT
    if normalized == "error":
        return TEST_TRANSCRIPT_ERROR_TEXT
    if normalized in {"cancelled", "skipped"}:
        return TEST_TRANSCRIPT_CANCELLED_TEXT
    return TEST_TRANSCRIPT_DEFAULT_TEXT


def _build_test_summary_feedback(outcome: str | None) -> tuple[str, str]:
    normalized = (outcome or "pending").strip().lower()
    if normalized == "passed":
        return "Erfolgreich geprüft", "success"
    if normalized == "skipped":
        return "Übersprungen", "text_secondary"
    if normalized == "cancelled":
        return "Abgebrochen", "text_secondary"
    if normalized == "error":
        return "Benötigt Aufmerksamkeit", "warning"
    if normalized == "no_speech":
        return "Bitte erneut testen", "warning"
    if normalized in {"connecting", "recording", "processing"}:
        return "Läuft gerade", "warning"
    return "Noch nicht getestet", "text_secondary"


def _build_test_start_button_text(outcome: str | None, *, started_once: bool) -> str:
    normalized = (outcome or "pending").strip().lower()
    if normalized == "passed":
        return "Nochmal testen"
    if normalized in {"no_speech", "error", "cancelled", "skipped"} or started_once:
        return "Erneut testen"
    return "Test starten"


# =============================================================================
# Onboarding Wizard
# =============================================================================


class OnboardingWizardWindows(QDialog):
    """Windows Onboarding Wizard."""

    # Signals
    settings_changed = Signal()
    completed = Signal()
    _hotkey_field_update = Signal(str, str)  # field, value (thread-safe)

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
        self._toggle_record_btn: QPushButton | None = None
        self._hold_record_btn: QPushButton | None = None
        self._toggle_clear_btn: QPushButton | None = None
        self._hold_clear_btn: QPushButton | None = None
        self._mic_status_label: QLabel | None = None
        self._test_transcript: QPlainTextEdit | None = None
        self._test_status_label: QLabel | None = None
        self._test_hotkey_label: QLabel | None = None
        self._test_successful = False
        self._test_started_once = False
        self._test_outcome = "pending"
        self._summary_labels: dict[str, QLabel] = {}
        self._stack: QStackedWidget | None = None
        self._step_widgets: dict[OnboardingStep, QWidget] = {}
        self._step_builders: dict[OnboardingStep, Callable[[], QWidget]] = {}

        # Hotkey recording state
        self._recording_field: str | None = None  # "toggle" or "hold"
        self._hotkey_listener = None
        self._using_qt_grab = False
        self._pressed_keys: set = set()
        self._pressed_keys_lock = threading.Lock()  # Thread-safe access
        self._hotkey_recorded = False  # True if user pressed any key during recording
        self._hotkey_field_update.connect(self._set_hotkey_field_text)

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
        self._ipc_seen_recording: bool = False
        self._ipc_stop_requested: bool = False
        self._ipc_recording_polls_after_stop: int = 0
        self._ipc_last_status: str | None = None
        self._test_start_btn: QPushButton | None = None
        self._test_stop_btn: QPushButton | None = None
        self._test_notice: QLabel | None = None
        self._hotkey_status_label: QLabel | None = None
        self._is_closed = False
        self._last_test_transcript_text = ""
        self._last_hotkey_preview_by_field: dict[str, str] = {}
        self._last_test_hotkey_summary: str | None = None
        self._last_hotkey_validation_input: tuple[str, str] | None = None
        self._last_hotkey_validation_result: tuple[str, str, str | None] | None = None
        self._env_settings_cache: dict[str, str] = read_env_file()
        self._fast_choice_requires_reapply = False

        self._setup_ui()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def _refresh_env_settings_cache(self) -> dict[str, str]:
        cache = read_env_file()
        self._env_settings_cache = cache
        return cache

    def _get_cached_env_setting(self, key_name: str) -> str | None:
        cache = getattr(self, "_env_settings_cache", None)
        if cache is None:
            return get_env_setting(key_name)
        return cache.get(key_name)

    def _apply_env_updates(self, updates: dict[str, str | None]) -> bool:
        normalized_updates = _normalize_env_updates(updates)
        cache = getattr(self, "_env_settings_cache", None)
        if cache is not None and not _env_updates_changed(cache, normalized_updates):
            return False

        update_env_settings(normalized_updates)
        self._apply_updates_to_env_cache(normalized_updates)
        return True

    def _apply_updates_to_env_cache(self, updates: dict[str, str | None]) -> None:
        cache = getattr(self, "_env_settings_cache", None)
        if cache is None:
            return
        for key, value in updates.items():
            if value is None:
                cache.pop(key, None)
            else:
                cache[key] = value

    def _cache_api_key(self, key_name: str, value: str | None) -> None:
        cache = getattr(self, "_env_settings_cache", None)
        if cache is None:
            return
        if value is None:
            cache.pop(key_name, None)
        else:
            cache[key_name] = value

    def _get_cached_api_key(self, key_name: str) -> str | None:
        cache = getattr(self, "_env_settings_cache", None)
        if cache is None:
            return get_api_key(key_name)
        return cache.get(key_name)

    def _get_cached_hotkeys(self) -> tuple[str, str]:
        return (
            (self._get_cached_env_setting("PULSESCRIBE_TOGGLE_HOTKEY") or "").strip(),
            (self._get_cached_env_setting("PULSESCRIBE_HOLD_HOTKEY") or "").strip(),
        )

    def _get_hotkey_validation_result(
        self, toggle_raw: str | None, hold_raw: str | None
    ) -> tuple[str, str, str | None]:
        cache_key = ((toggle_raw or "").strip(), (hold_raw or "").strip())
        if getattr(self, "_last_hotkey_validation_input", None) != cache_key:
            self._last_hotkey_validation_input = cache_key
            self._last_hotkey_validation_result = self._validate_hotkey_pair(
                toggle_raw, hold_raw
            )
        result = self._last_hotkey_validation_result
        if result is None:
            result = self._validate_hotkey_pair(toggle_raw, hold_raw)
            self._last_hotkey_validation_result = result
        return result

    def _persist_hotkeys(self, toggle: str | None, hold: str | None) -> bool:
        return self._apply_env_updates(
            {
                "PULSESCRIBE_TOGGLE_HOTKEY": toggle or None,
                "PULSESCRIBE_HOLD_HOTKEY": hold or None,
            }
        )

    def set_test_dictation_callbacks(
        self, *, start: Callable[[], None], stop: Callable[[], None]
    ) -> None:
        """Set callbacks for test dictation integration."""
        self._on_test_start = start
        self._on_test_stop = stop

    def update_test_transcript(self, text: str) -> None:
        """Update the test dictation transcript (called by daemon)."""
        if self._set_test_transcript_text(text) and text.strip():
            self._test_successful = True
            self._test_outcome = "passed"
            self._set_test_status("Transkription erfolgreich!", "success")
            self._set_test_notice(*_build_test_notice_feedback("passed"))
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
        self._step_builders = {
            OnboardingStep.CHOOSE_GOAL: self._build_choose_goal_step,
            OnboardingStep.PERMISSIONS: self._build_permissions_step,
            OnboardingStep.HOTKEY: self._build_hotkey_step,
            OnboardingStep.TEST_DICTATION: self._build_test_dictation_step,
            OnboardingStep.CHEAT_SHEET: self._build_cheat_sheet_step,
        }
        self._ensure_step_widget(self._step)
        main_layout.addWidget(self._stack, 1)

        # Footer with navigation
        footer = QWidget()
        footer.setMinimumHeight(FOOTER_HEIGHT)
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

    def _is_step_widget_built(self, step: OnboardingStep | None) -> bool:
        if step == OnboardingStep.DONE:
            step = OnboardingStep.CHEAT_SHEET
        return step in self._step_widgets

    def _ensure_step_widget(self, step: OnboardingStep | None) -> QWidget | None:
        if step is None:
            return None
        if step == OnboardingStep.DONE:
            step = OnboardingStep.CHEAT_SHEET

        widget = self._step_widgets.get(step)
        if widget is not None:
            return widget

        builder = self._step_builders.get(step)
        if builder is None:
            return None

        widget = builder()
        self._step_widgets[step] = widget
        if self._stack is not None:
            self._stack.addWidget(widget)
        return widget

    def _build_choose_goal_step(self) -> QWidget:
        """Build the goal selection step."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, PADDING)
        layout.setSpacing(16)

        layout.addWidget(_create_section_title("Wie möchtest du PulseScribe nutzen?"))
        layout.addWidget(
            _create_description(
                "Wir richten dir eine sinnvolle Startkonfiguration ein. "
                "Alles lässt sich später jederzeit in den Einstellungen anpassen."
            )
        )

        # Choice buttons
        choices = [
            (
                OnboardingChoice.FAST,
                "Schnell",
                "Empfohlen für den Alltag – Cloud-basiert und besonders reaktionsschnell",
            ),
            (
                OnboardingChoice.PRIVATE,
                "Privat",
                "Lokal mit Whisper – Audio bleibt auf deinem Gerät",
            ),
            (
                OnboardingChoice.ADVANCED,
                "Erweitert",
                "Du entscheidest alles selbst – für individuelle Setups",
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
        lang_label = QLabel("Erkennungssprache:")
        lang_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        lang_row.addWidget(lang_label)

        self._lang_combo = QComboBox()
        for language in LANGUAGE_OPTIONS:
            self._lang_combo.addItem(_format_language_label(language), language)
        current_lang = self._get_cached_env_setting("PULSESCRIBE_LANGUAGE") or "auto"
        current_index = self._lang_combo.findData(current_lang)
        if current_index >= 0:
            self._lang_combo.setCurrentIndex(current_index)
        self._lang_combo.currentIndexChanged.connect(self._on_language_combo_changed)
        lang_row.addWidget(self._lang_combo)
        lang_row.addStretch()

        layout.addLayout(lang_row)

        lang_hint = QLabel(
            "Automatisch erkennt die Sprache beim Sprechen. Wähle eine feste Sprache nur, "
            "wenn du fast immer dieselbe Sprache diktierst."
        )
        lang_hint.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        lang_hint.setStyleSheet(f"color: {COLORS['text_hint']};")
        lang_hint.setWordWrap(True)
        layout.addWidget(lang_hint)

        # API Key input (shown when Fast is selected without existing key)
        api_container = QWidget()
        api_container.setVisible(False)
        api_layout = QVBoxLayout(api_container)
        api_layout.setContentsMargins(0, 8, 0, 0)
        api_layout.setSpacing(6)

        api_label = QLabel("Deepgram API-Key für den Fast-Modus")
        api_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        api_layout.addWidget(api_label)

        api_field = QLineEdit()
        api_field.setEchoMode(QLineEdit.EchoMode.Password)
        api_field.setPlaceholderText("dg-...")
        api_field.setFont(QFont(DEFAULT_FONT_FAMILY, 11))
        api_field.textChanged.connect(self._on_api_key_input_changed)
        api_layout.addWidget(api_field)

        api_status = QLabel("")
        api_status.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        api_status.setStyleSheet(f"color: {COLORS['text_secondary']};")
        api_status.setWordWrap(True)
        api_layout.addWidget(api_status)

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
                "Windows fragt den Mikrofonzugriff bei Bedarf an. "
                "Im nächsten Schritt kannst du direkt eine sichere Testaufnahme starten."
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

        self._mic_status_label = QLabel("Noch nicht geprüft")
        self._mic_status_label.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        self._mic_status_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        self._mic_status_label.setWordWrap(True)
        mic_text.addWidget(self._mic_status_label)

        mic_row.addLayout(mic_text, 1)

        mic_btn = QPushButton("Windows öffnen")
        mic_btn.clicked.connect(self._open_mic_settings)
        mic_row.addWidget(mic_btn)

        card_layout.addLayout(mic_row)
        layout.addWidget(card)

        # Info text
        info = QLabel(
            "Unter Windows ist normalerweise nur der Mikrofonzugriff relevant. "
            "Falls der Test fehlschlägt, prüfe Standardmikrofon, Datenschutz-Einstellungen "
            "und ob PulseScribe im Hintergrund läuft."
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
                "Lege einen oder zwei Hotkeys fest. Toggle ist ideal für längere Diktate, "
                "Hold für schnelles Push-to-talk."
            )
        )

        # Hotkey card
        card, card_layout = _create_card()

        def add_hotkey_row(
            label_text: str,
            hint_text: str,
            field_kind: str,
            current_value: str,
        ) -> tuple[QLineEdit, QPushButton, QPushButton]:
            row_layout = QVBoxLayout()
            row_layout.setSpacing(4)

            controls = QHBoxLayout()
            controls.setSpacing(8)

            label = QLabel(label_text)
            label.setFont(QFont(DEFAULT_FONT_FAMILY, 10, QFont.Weight.Bold))
            label.setMinimumWidth(120)
            controls.addWidget(label)

            input_field = QLineEdit()
            input_field.setPlaceholderText("Noch nicht gesetzt")
            input_field.setReadOnly(True)
            input_field.setToolTip("Zum Aufzeichnen klicken oder die Schaltfläche verwenden")
            input_field.setText(current_value)
            input_field.mousePressEvent = lambda _event, k=field_kind: self._toggle_hotkey_capture(
                k
            )
            controls.addWidget(input_field, 1)

            record_btn = QPushButton("Aufnehmen")
            record_btn.setToolTip("Hotkey aufzeichnen")
            record_btn.clicked.connect(
                lambda _checked=False, k=field_kind: self._toggle_hotkey_capture(k)
            )
            controls.addWidget(record_btn)

            clear_btn = QPushButton("Entfernen")
            clear_btn.setToolTip("Gespeicherten Hotkey löschen")
            clear_btn.clicked.connect(
                lambda _checked=False, k=field_kind: self._clear_hotkey(k)
            )
            controls.addWidget(clear_btn)

            row_layout.addLayout(controls)

            hint = QLabel(hint_text)
            hint.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
            hint.setStyleSheet(f"color: {COLORS['text_hint']};")
            hint.setWordWrap(True)
            row_layout.addWidget(hint)

            card_layout.addLayout(row_layout)
            return input_field, record_btn, clear_btn

        (
            self._toggle_input,
            self._toggle_record_btn,
            self._toggle_clear_btn,
        ) = add_hotkey_row(
            "Toggle-Hotkey",
            "Einmal drücken → sprechen → erneut drücken. Gut für längere Diktate.",
            "toggle",
            self._get_cached_env_setting("PULSESCRIBE_TOGGLE_HOTKEY") or "",
        )
        (
            self._hold_input,
            self._hold_record_btn,
            self._hold_clear_btn,
        ) = add_hotkey_row(
            "Hold-Hotkey",
            "Gedrückt halten → sprechen → loslassen. Hold darf nur Modifier enthalten, z. B. ctrl+win.",
            "hold",
            self._get_cached_env_setting("PULSESCRIBE_HOLD_HOTKEY") or "",
        )

        hint = QLabel(
            "Während der Aufzeichnung: Enter speichert, Esc bricht ab. "
            "Du kannst Toggle und Hold parallel aktivieren."
        )
        hint.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        hint.setStyleSheet(f"color: {COLORS['text_hint']};")
        hint.setWordWrap(True)
        card_layout.addWidget(hint)

        self._hotkey_status_label = QLabel("")
        self._hotkey_status_label.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        self._hotkey_status_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        self._hotkey_status_label.setWordWrap(True)
        card_layout.addWidget(self._hotkey_status_label)

        layout.addWidget(card)

        # Presets
        presets_label = QLabel("Schnellauswahl:")
        presets_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10, QFont.Weight.Bold))
        layout.addWidget(presets_label)

        presets_row = QHBoxLayout()

        preset_f19 = QPushButton("F19 (Toggle)")
        preset_f19.setToolTip("Setzt nur den Toggle-Hotkey auf F19")
        preset_f19.clicked.connect(lambda: self._apply_hotkey_preset("f19", None))
        presets_row.addWidget(preset_f19)

        preset_ctrl_alt = QPushButton("Ctrl+Alt-Set")
        preset_ctrl_alt.setToolTip(
            "Toggle: Ctrl+Alt+R • Hold: Ctrl+Alt+Space"
        )
        preset_ctrl_alt.clicked.connect(
            lambda: self._apply_hotkey_preset("ctrl+alt+r", "ctrl+alt+space")
        )
        presets_row.addWidget(preset_ctrl_alt)

        preset_f13 = QPushButton("F13 (Toggle)")
        preset_f13.setToolTip("Setzt nur den Toggle-Hotkey auf F13")
        preset_f13.clicked.connect(lambda: self._apply_hotkey_preset("f13", None))
        presets_row.addWidget(preset_f13)

        presets_row.addStretch()
        layout.addLayout(presets_row)

        layout.addStretch()
        self._update_hotkey_capture_ui()

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
                "Starte eine sichere Testaufnahme direkt im Assistenten. "
                "Der erkannte Text wird nur hier angezeigt und nicht eingefügt."
            )
        )

        # Hotkey reminder
        self._test_hotkey_label = QLabel()
        self._test_hotkey_label.setFont(
            QFont(DEFAULT_FONT_FAMILY, 10, QFont.Weight.Bold)
        )
        self._test_hotkey_label.setStyleSheet(f"color: {COLORS['accent']};")
        self._test_hotkey_label.setWordWrap(True)
        self._refresh_test_hotkey_label()
        layout.addWidget(self._test_hotkey_label)

        # Transcript area
        card, card_layout = _create_card()

        self._test_status_label = QLabel(_build_test_status_text("pending"))
        self._test_status_label.setFont(QFont(DEFAULT_FONT_FAMILY, 10))
        self._test_status_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        self._test_status_label.setWordWrap(True)
        card_layout.addWidget(self._test_status_label)

        self._test_transcript = QPlainTextEdit()
        self._test_transcript.setPlaceholderText(
            "Sobald eine Testaufnahme abgeschlossen ist, erscheint der erkannte Text hier."
        )
        self._test_transcript.setReadOnly(True)
        self._test_transcript.setMinimumHeight(120)
        self._test_transcript.setPlainText(TEST_TRANSCRIPT_DEFAULT_TEXT)
        card_layout.addWidget(self._test_transcript)

        layout.addWidget(card)

        # Optionaler Snappy-Toggle direkt nach dem Test, damit man erst die
        # Erkennungsqualität prüfen und nur dann kürzere Puffer aktivieren kann.
        layout.addWidget(self._build_snappy_option())

        # Test buttons (IPC-based when daemon is running)
        btn_row = QHBoxLayout()

        self._test_start_btn = QPushButton("Test starten")
        self._test_start_btn.setToolTip("Startet eine sichere Testaufnahme im Hintergrunddienst")
        self._test_start_btn.clicked.connect(self._start_ipc_test)
        btn_row.addWidget(self._test_start_btn)

        self._test_stop_btn = QPushButton("Aufnahme stoppen")
        self._test_stop_btn.clicked.connect(self._stop_ipc_test)
        self._test_stop_btn.setVisible(False)
        btn_row.addWidget(self._test_stop_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Info text (shown when daemon not running)
        notice_text, notice_color = _build_test_notice_feedback("pending")
        self._test_notice = QLabel(notice_text)
        self._test_notice.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        self._test_notice.setStyleSheet(f"color: {COLORS[notice_color]};")
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

    def _build_snappy_option(self) -> QWidget:
        """Toggle für das Windows-Latenz-Preset (snappy ist Default).

        Bewusst im Test-Schritt platziert: Wer beim Test abgeschnittene
        Wortenden bemerkt, kann hier auf die konservativen ``safe``-Puffer
        wechseln. Der adaptive Stop-Tail schützt Wortenden allerdings bereits
        unabhängig vom Preset.
        """
        card, card_layout = _create_card()

        title = QLabel("Schnelle Übergänge (Windows)")
        title.setFont(QFont(DEFAULT_FONT_FAMILY, 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {COLORS['text']};")
        title.setWordWrap(True)
        card_layout.addWidget(title)

        is_snappy = (
            (
                self._get_cached_env_setting("PULSESCRIBE_WINDOWS_LATENCY_PRESET")
                or "snappy"
            )
            .strip()
            .lower()
            == "snappy"
        )
        self._snappy_checkbox = QCheckBox(
            "Snappy-Preset verwenden (kürzere Puffer, Standard)"
        )
        self._snappy_checkbox.setChecked(is_snappy)
        self._snappy_checkbox.toggled.connect(self._on_snappy_toggled)
        card_layout.addWidget(self._snappy_checkbox)

        hint = QLabel(
            "Kurze Aufnahme-/Finalize-Puffer für reaktionsschnelle Übergänge "
            "(Standard). Wortenden schützt der adaptive Stop-Nachlauf "
            "automatisch. Deaktiviere dies nur, falls beim Test oben trotzdem "
            "das letzte Wort abgeschnitten wird – dann gelten längere, "
            "konservative Puffer. Wirkt vollständig nach einem Neustart des "
            "Dienstes."
        )
        hint.setFont(QFont(DEFAULT_FONT_FAMILY, 9))
        hint.setStyleSheet(f"color: {COLORS['text_secondary']};")
        hint.setWordWrap(True)
        card_layout.addWidget(hint)

        return card

    def _on_snappy_toggled(self, checked: bool) -> None:
        """Persist the Windows latency preset choice from the onboarding toggle.

        Snappy ist der Default: Aktivieren entfernt den Override, Deaktivieren
        setzt explizit das konservative ``safe``-Preset.
        """
        self._apply_env_updates(
            {"PULSESCRIBE_WINDOWS_LATENCY_PRESET": None if checked else "safe"}
        )

    def _build_cheat_sheet_step(self) -> QWidget:
        """Build the summary/cheat sheet step."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, PADDING)
        layout.setSpacing(16)

        layout.addWidget(_create_section_title("Alles bereit!"))
        layout.addWidget(
            _create_description(
                "Hier ist deine Startkonfiguration. Du kannst später alles jederzeit in den Einstellungen ändern."
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

        test_row = QHBoxLayout()
        test_row.addWidget(QLabel("Diktiertest:"))
        self._summary_labels["test"] = QLabel()
        self._summary_labels["test"].setWordWrap(True)
        test_row.addWidget(self._summary_labels["test"], 1)
        card_layout.addLayout(test_row)

        layout.addWidget(card)

        # Ready message
        ready = QLabel(
            "Du kannst jetzt in jeder App diktieren. PulseScribe transkribiert deine Sprache und fügt den Text danach automatisch ein."
        )
        ready.setFont(QFont(DEFAULT_FONT_FAMILY, 11))
        ready.setStyleSheet(f"color: {COLORS['success']};")
        ready.setWordWrap(True)
        layout.addWidget(ready)

        # Open settings button
        settings_btn = QPushButton("Weitere Einstellungen öffnen...")
        settings_btn.clicked.connect(self._open_settings_after)
        layout.addWidget(settings_btn)

        layout.addStretch()

        return widget

    # -------------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------------

    def _show_step(self, step: OnboardingStep) -> None:
        """Show the specified step."""
        self._run_step_leave_actions(step)
        self._step = step
        self._show_step_widget(step)
        self._update_progress_label(step)
        self._run_step_enter_actions(step)
        self._update_navigation()
        self._persist_step_progress(step)

    def _run_step_leave_actions(self, next_step_value: OnboardingStep) -> None:
        if self._step == OnboardingStep.PERMISSIONS and next_step_value != self._step:
            self._stop_mic_timer()
        if self._step == OnboardingStep.TEST_DICTATION and next_step_value != self._step:
            self._cancel_ipc_test_if_running()
            self._stop_ipc_polling()
            self._reset_test_ui()

    def _stop_mic_timer(self) -> None:
        if self._mic_timer:
            self._mic_timer.stop()

    def _show_step_widget(self, step: OnboardingStep) -> None:
        current_widget = self._ensure_step_widget(step)
        if self._stack is not None and current_widget is not None:
            self._stack.setCurrentWidget(current_widget)

    def _update_progress_label(self, step: OnboardingStep) -> None:
        if self._progress_label:
            idx = step_index(step)
            total = total_steps()
            _set_widget_text_if_changed(
                self._progress_label, f"Schritt {idx} von {total}"
            )

    def _run_step_enter_actions(self, step: OnboardingStep) -> None:
        if step == OnboardingStep.PERMISSIONS:
            self._start_mic_check()
        elif step == OnboardingStep.HOTKEY:
            self._ensure_default_hotkeys()
        elif step == OnboardingStep.TEST_DICTATION:
            self._refresh_test_hotkey_label()
            self._refresh_test_start_button_label()
        elif step == OnboardingStep.CHEAT_SHEET:
            self._update_summary()

    def _persist_step_progress(self, step: OnboardingStep) -> None:
        if self._persist_progress:
            set_onboarding_step(step)

    def _update_navigation(self) -> None:
        """Update navigation button states."""
        if not self._back_btn or not self._next_btn:
            return

        # Back button
        _set_widget_visible_if_changed(
            self._back_btn, self._step != OnboardingStep.CHOOSE_GOAL
        )

        # Next button text and state
        if self._step == OnboardingStep.CHEAT_SHEET:
            _set_widget_text_if_changed(self._next_btn, "Fertig")
        else:
            _set_widget_text_if_changed(self._next_btn, "Weiter")

        # Enable/disable based on step requirements
        can_advance = self._can_advance()
        _set_widget_enabled_if_changed(self._next_btn, can_advance)

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
            toggle, hold = self._get_cached_hotkeys()
            if not (toggle or hold):
                self._set_hotkey_status(
                    "Mindestens ein Hotkey muss gesetzt sein.", "warning"
                )
                return False

            _, _, error = self._get_hotkey_validation_result(toggle, hold)
            if error:
                self._set_hotkey_status(error, "error")
                return False

            self._set_hotkey_status("", "text_secondary")
            return True
        if self._step == OnboardingStep.TEST_DICTATION:
            return bool(self._test_successful)
        return True

    def _go_next(self) -> None:
        """Navigate to next step."""
        self._stop_hotkey_recording()

        # Save API key when leaving CHOOSE_GOAL with FAST mode
        if (
            self._step == OnboardingStep.CHOOSE_GOAL
            and self._choice == OnboardingChoice.FAST
        ):
            # Re-apply after potential key entry to ensure mode is actually persisted.
            changed = False
            if self._fast_choice_requires_reapply:
                changed = self._apply_choice_preset(self._choice)
            set_onboarding_choice(self._choice)
            if changed:
                self.settings_changed.emit()

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
        if not self._test_successful:
            self._test_outcome = "skipped"
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
        if self._ipc_test_cmd_id is not None:
            logger.debug("IPC test already running; ignoring duplicate start request")
            return

        from utils.ipc import CMD_START_TEST, IPCClient

        if self._ipc_client is None:
            self._ipc_client = IPCClient()

        # Vorherigen Testinhalt löschen, damit keine stale Ergebnisse sichtbar bleiben.
        self._set_test_transcript_text(_build_test_transcript_text("connecting"))

        # A retry must earn success again; otherwise a stale pass keeps "Weiter"
        # enabled even after a later failed/no-speech attempt.
        self._test_started_once = True
        self._test_successful = False
        self._test_outcome = "connecting"
        self._update_navigation()

        self._ipc_test_cmd_id = self._ipc_client.send_command(CMD_START_TEST)
        self._ipc_poll_count = 0
        self._ipc_seen_recording = False
        self._ipc_stop_requested = False
        self._ipc_recording_polls_after_stop = 0
        self._ipc_last_status = None

        # Show "connecting" state while waiting for daemon acknowledgment
        self._set_test_status(_build_test_status_text("connecting"), "text_secondary")
        self._set_test_notice(*_build_test_notice_feedback("connecting"))
        _set_widget_visible_if_changed(self._test_start_btn, False)
        stop_btn = getattr(self, "_test_stop_btn", None)
        _set_widget_text_if_changed(stop_btn, "Abbrechen")
        _set_widget_visible_if_changed(stop_btn, True)
        _set_widget_enabled_if_changed(stop_btn, True)

        # Poll for daemon response every 200ms
        if self._ipc_poll_timer is None:
            self._ipc_poll_timer = QTimer(self)
            self._ipc_poll_timer.timeout.connect(self._poll_ipc_response)
        self._ipc_poll_timer.start(IPC_POLL_INTERVAL_MS)

        logger.debug(f"IPC test started (cmd_id={self._ipc_test_cmd_id})")

    def _cancel_ipc_test_if_running(self) -> None:
        """Best-effort stop request when leaving test dictation unexpectedly."""
        if not self._ipc_client or not self._ipc_test_cmd_id:
            return

        try:
            from utils.ipc import CMD_STOP_TEST

            self._ipc_client.send_command(CMD_STOP_TEST)
        except Exception as e:
            logger.debug(f"IPC stop request on cleanup failed: {e}")

    def _stop_ipc_test(self) -> None:
        """Stop test dictation via IPC."""
        from utils.ipc import CMD_STOP_TEST

        if not self._ipc_client or not self._ipc_test_cmd_id:
            return

        if not self._ipc_seen_recording:
            try:
                self._ipc_client.send_command(CMD_STOP_TEST)
            except Exception as e:
                logger.debug(f"IPC cancel request failed: {e}")
            self._stop_ipc_polling()
            self._reset_test_ui()
            self._test_successful = False
            self._test_outcome = "cancelled"
            self._set_test_status(_build_test_status_text("cancelled"), "text_secondary")
            self._set_test_transcript_text(_build_test_transcript_text("cancelled"))
            self._set_test_notice(*_build_test_notice_feedback("cancelled"))
            self._update_navigation()
            return

        self._ipc_stop_requested = True
        self._ipc_recording_polls_after_stop = 0
        self._ipc_client.send_command(CMD_STOP_TEST)
        _set_timer_interval_if_supported(self._ipc_poll_timer, IPC_POLL_INTERVAL_MS)

        self._test_outcome = "processing"
        self._set_test_status(_build_test_status_text("processing"), "text_secondary")
        self._set_test_transcript_text(_build_test_transcript_text("processing"))
        self._set_test_notice(*_build_test_notice_feedback("processing"))
        stop_btn = getattr(self, "_test_stop_btn", None)
        _set_widget_text_if_changed(stop_btn, "Wird gestoppt…")
        _set_widget_enabled_if_changed(stop_btn, False)

    def _poll_ipc_response(self) -> None:
        """Poll for IPC response from daemon."""
        if not self._ipc_client or not self._ipc_test_cmd_id:
            return

        response = self._ipc_client.poll_response(self._ipc_test_cmd_id)
        if not response:
            self._handle_missing_ipc_response()
            return

        self._handle_ipc_response(response)

    def _handle_missing_ipc_response(self) -> None:
        if self._ipc_seen_recording and not self._ipc_stop_requested:
            _set_timer_interval_if_supported(
                self._ipc_poll_timer, IPC_RECORDING_IDLE_POLL_INTERVAL_MS
            )
        self._ipc_poll_count += 1
        if self._ipc_poll_count >= self._ipc_timeout_limit():
            self._handle_ipc_poll_timeout()

    def _ipc_timeout_limit(self) -> int:
        if self._ipc_seen_recording:
            return IPC_MAX_POLLS_BEFORE_TIMEOUT
        return IPC_CONNECT_MAX_POLLS_BEFORE_TIMEOUT

    def _handle_ipc_poll_timeout(self) -> None:
        saw_recording = self._ipc_seen_recording
        stop_requested = self._ipc_stop_requested
        self._stop_ipc_polling()
        if stop_requested and saw_recording:
            self._on_ipc_test_complete("", None)
        elif saw_recording:
            self._on_ipc_test_complete(
                "",
                "Keine finale Antwort von PulseScribe. Bitte erneut versuchen.",
            )
        else:
            self._on_ipc_test_complete("", "Keine Verbindung zu PulseScribe")

    def _handle_ipc_response(self, response: dict[str, object]) -> None:
        from utils.ipc import (
            STATUS_DONE,
            STATUS_ERROR,
            STATUS_RECORDING,
            STATUS_STOPPED,
        )

        status = response.get("status")
        logger.debug(f"IPC response: {status}")
        self._ipc_poll_count = 0

        if status == STATUS_RECORDING:
            self._handle_ipc_recording_status(status)
        elif status == STATUS_DONE:
            self._stop_ipc_polling()
            transcript = response.get("transcript", "")
            self._on_ipc_test_complete(transcript, None)
        elif status == STATUS_ERROR:
            self._stop_ipc_polling()
            error = response.get("error", "Unbekannter Fehler")
            self._on_ipc_test_complete("", error)
        elif status == STATUS_STOPPED:
            self._handle_ipc_stopped_status()

    def _handle_ipc_recording_status(self, status: object) -> None:
        self._ipc_seen_recording = True
        stop_btn = getattr(self, "_test_stop_btn", None)
        _set_widget_enabled_if_changed(stop_btn, True)
        if not self._ipc_stop_requested:
            _set_timer_interval_if_supported(
                self._ipc_poll_timer, IPC_RECORDING_IDLE_POLL_INTERVAL_MS
            )

        if status != self._ipc_last_status:
            self._show_ipc_recording_state(stop_btn)
            self._ipc_last_status = str(status)

        if self._ipc_stop_requested:
            self._handle_recording_status_after_stop()

    def _show_ipc_recording_state(self, stop_btn) -> None:
        self._test_outcome = "recording"
        self._set_test_status(_build_test_status_text("recording"), "accent")
        self._set_test_transcript_text(_build_test_transcript_text("recording"))
        self._set_test_notice(*_build_test_notice_feedback("recording"))
        _set_widget_text_if_changed(stop_btn, "Aufnahme stoppen")

    def _handle_recording_status_after_stop(self) -> None:
        self._ipc_recording_polls_after_stop += 1
        if self._ipc_recording_polls_after_stop < IPC_RECORDING_STALE_POLLS_AFTER_STOP:
            return
        self._stop_ipc_polling()
        self._on_ipc_test_complete("", None)

    def _handle_ipc_stopped_status(self) -> None:
        self._stop_ipc_polling()
        self._reset_test_ui()
        self._test_successful = False
        self._test_outcome = "cancelled"
        self._set_test_status(_build_test_status_text("cancelled"), "text_secondary")
        self._set_test_transcript_text(_build_test_transcript_text("cancelled"))
        self._set_test_notice(*_build_test_notice_feedback("cancelled"))
        self._update_navigation()

    def _stop_ipc_polling(self) -> None:
        """Stop polling and clean up IPC state."""
        if self._ipc_poll_timer:
            self._ipc_poll_timer.stop()
        if self._ipc_client:
            self._ipc_client.clear_response()
        self._ipc_test_cmd_id = None
        self._ipc_poll_count = 0
        self._ipc_seen_recording = False
        self._ipc_stop_requested = False
        self._ipc_recording_polls_after_stop = 0
        self._ipc_last_status = None

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
        self._test_successful = False
        self._test_outcome = "error"
        self._set_test_status(
            _build_test_status_text("error", error=error),
            "error",
        )
        self._set_test_transcript_text(_build_test_transcript_text("error"))
        self._set_test_notice(*_build_test_notice_feedback("error", error=error))
        self._update_navigation()

    def _show_test_success(self, transcript: str) -> None:
        """Display successful transcription."""
        self._set_test_transcript_text(transcript)
        self._set_test_status(
            _build_test_status_text("passed"),
            "success",
        )
        self._test_successful = True
        self._test_outcome = "passed"
        self._set_test_notice(*_build_test_notice_feedback("passed"))
        self._update_navigation()

    def _show_test_no_speech(self) -> None:
        """Display "no speech detected" state."""
        self._test_successful = False
        self._test_outcome = "no_speech"
        self._set_test_status(
            _build_test_status_text("no_speech"),
            "warning",
        )
        self._set_test_transcript_text(_build_test_transcript_text("no_speech"))
        self._set_test_notice(*_build_test_notice_feedback("no_speech"))
        self._update_navigation()

    def _set_test_status(self, text: str, color_key: str) -> None:
        """Update the test status label with colored text."""
        if self._test_status_label is None:
            return
        _set_widget_text_if_changed(self._test_status_label, text)
        _set_widget_stylesheet_if_changed(
            self._test_status_label, f"color: {COLORS[color_key]};"
        )

    def _set_test_notice(self, text: str, color_key: str) -> None:
        notice = getattr(self, "_test_notice", None)
        if notice is None:
            return
        _set_widget_visible_if_changed(notice, True)
        _set_widget_text_if_changed(notice, text)
        _set_widget_stylesheet_if_changed(notice, f"color: {COLORS[color_key]};")

    def _refresh_test_start_button_label(self) -> None:
        label = _build_test_start_button_text(
            getattr(self, "_test_outcome", "pending"),
            started_once=bool(getattr(self, "_test_started_once", False)),
        )
        _set_widget_text_if_changed(getattr(self, "_test_start_btn", None), label)

    def _reset_test_ui(self) -> None:
        """Show start button, hide stop button and restore adaptive button labels."""
        _set_widget_visible_if_changed(self._test_start_btn, True)
        self._refresh_test_start_button_label()
        _set_widget_visible_if_changed(self._test_stop_btn, False)
        _set_widget_enabled_if_changed(self._test_stop_btn, True)
        _set_widget_text_if_changed(self._test_stop_btn, "Aufnahme stoppen")

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
            _set_widget_visible_if_changed(self._api_key_container, show_api)
            if show_api and self._api_key_status:
                _set_widget_text_if_changed(
                    self._api_key_status,
                    "Erforderlich für Fast-Modus: Deepgram-Key einfügen. Ein vorhandener Groq-Key wird automatisch erkannt.",
                )
                _set_widget_stylesheet_if_changed(
                    self._api_key_status, f"color: {COLORS['text_secondary']};"
                )
                self._focus_api_key_field()

        if save:
            set_onboarding_choice(choice)
            changed = self._apply_choice_preset(choice)
            if changed:
                self.settings_changed.emit()

        self._update_navigation()

    def _has_api_key(self) -> bool:
        """Check if a cloud fast-mode API key exists (entered or saved)."""
        import os

        entered_key = ""
        if self._api_key_field:
            entered_key = self._api_key_field.text().strip()
        return bool(
            entered_key
            or self._get_cached_api_key("DEEPGRAM_API_KEY")
            or os.getenv("DEEPGRAM_API_KEY")
            or self._get_cached_api_key("GROQ_API_KEY")
            or os.getenv("GROQ_API_KEY")
        )

    def _apply_choice_preset(self, choice: OnboardingChoice) -> bool:
        """Apply the preset for the selected choice."""
        if choice == OnboardingChoice.FAST:
            return self._apply_fast_choice_preset()
        if choice == OnboardingChoice.PRIVATE:
            return self._apply_private_choice_preset()
        return False

    def _apply_fast_choice_preset(self) -> bool:
        pending_updates = self._collect_fast_choice_api_key_update()
        if self._has_deepgram_fast_key(pending_updates):
            return self._apply_deepgram_fast_choice(pending_updates)
        if self._has_groq_fast_key():
            return self._apply_groq_fast_choice()
        self._show_fast_api_key_required()
        return False

    def _collect_fast_choice_api_key_update(self) -> dict[str, str | None]:
        if not self._api_key_field:
            return {}
        entered_key = self._api_key_field.text().strip()
        if not entered_key:
            return {}
        if self._get_cached_api_key("DEEPGRAM_API_KEY") == entered_key:
            return {}
        return {"DEEPGRAM_API_KEY": entered_key}

    def _has_deepgram_fast_key(self, pending_updates: dict[str, str | None]) -> bool:
        import os

        return bool(
            pending_updates.get("DEEPGRAM_API_KEY")
            or self._get_cached_api_key("DEEPGRAM_API_KEY")
            or os.getenv("DEEPGRAM_API_KEY")
        )

    def _has_groq_fast_key(self) -> bool:
        import os

        return bool(self._get_cached_api_key("GROQ_API_KEY") or os.getenv("GROQ_API_KEY"))

    def _apply_deepgram_fast_choice(self, updates: dict[str, str | None]) -> bool:
        pending_updates = dict(updates)
        if self._get_cached_env_setting("PULSESCRIBE_MODE") != "deepgram":
            pending_updates["PULSESCRIBE_MODE"] = "deepgram"
        changed = self._apply_env_updates(pending_updates) if pending_updates else False
        if "DEEPGRAM_API_KEY" in pending_updates:
            self._cache_api_key("DEEPGRAM_API_KEY", pending_updates["DEEPGRAM_API_KEY"])
        self._hide_fast_api_key_prompt()
        return changed

    def _apply_groq_fast_choice(self) -> bool:
        changed = (
            self._apply_env_updates({"PULSESCRIBE_MODE": "groq"})
            if self._get_cached_env_setting("PULSESCRIBE_MODE") != "groq"
            else False
        )
        self._hide_fast_api_key_prompt()
        return changed

    def _hide_fast_api_key_prompt(self) -> None:
        if self._api_key_container:
            _set_widget_visible_if_changed(self._api_key_container, False)
        self._fast_choice_requires_reapply = False

    def _show_fast_api_key_required(self) -> None:
        if not self._api_key_container:
            return
        _set_widget_visible_if_changed(self._api_key_container, True)
        if self._api_key_status:
            _set_widget_text_if_changed(
                self._api_key_status,
                "Erforderlich für Fast-Modus: Deepgram-Key einfügen. Ein vorhandener Groq-Key wird automatisch erkannt.",
            )

    def _apply_private_choice_preset(self) -> bool:
        from utils.presets import apply_local_preset_to_env, default_local_preset_private

        changed = apply_local_preset_to_env(default_local_preset_private())
        self._refresh_env_settings_cache()
        return changed

    def _focus_api_key_field(self) -> None:
        field = self._api_key_field
        if field is None:
            return
        try:
            QTimer.singleShot(0, field.setFocus)
        except Exception:
            try:
                field.setFocus()
            except Exception:
                pass

    def _on_language_combo_changed(self, index: int) -> None:
        combo = self._lang_combo
        if combo is None:
            return
        selected = combo.itemData(index)
        self._on_language_changed(str(selected or "auto"))

    def _on_language_changed(self, lang: str) -> None:
        """Handle language selection change."""
        changed = self._apply_env_updates(
            {"PULSESCRIBE_LANGUAGE": None if lang == "auto" else lang}
        )
        if changed:
            self.settings_changed.emit()

    def _on_api_key_input_changed(self, text: str) -> None:
        """Update API key status + navigation while typing."""
        self._fast_choice_requires_reapply = bool(text.strip())
        if self._api_key_status:
            if text.strip():
                _set_widget_text_if_changed(
                    self._api_key_status,
                    "✓ API-Key erkannt – Fast-Modus nutzt Deepgram.",
                )
                _set_widget_stylesheet_if_changed(
                    self._api_key_status, f"color: {COLORS['success']};"
                )
            else:
                _set_widget_text_if_changed(
                    self._api_key_status,
                    "Erforderlich für Fast-Modus: Deepgram-Key einfügen. Ein vorhandener Groq-Key wird automatisch erkannt.",
                )
                _set_widget_stylesheet_if_changed(
                    self._api_key_status, f"color: {COLORS['text_secondary']};"
                )
        self._update_navigation()

    # -------------------------------------------------------------------------
    # Step: Permissions
    # -------------------------------------------------------------------------

    def _start_mic_check(self) -> None:
        """Update mic status once for the current step.

        On Windows we do not have a reliable read-only API for microphone
        permissions, so periodic polling only causes unnecessary UI wakeups.
        """
        self._check_mic_permission()
        if self._mic_timer:
            self._mic_timer.stop()

    def _check_mic_permission(self) -> None:
        """Check microphone permission status."""
        if not self._mic_status_label:
            return

        # On Windows, we can't directly check mic permission without attempting recording.
        # We'll show a generic "ready" status and let the user test in the next step.
        _set_widget_text_if_changed(
            self._mic_status_label, "Wird im nächsten Schritt per Testaufnahme geprüft"
        )
        _set_widget_stylesheet_if_changed(
            self._mic_status_label, f"color: {COLORS['success']};"
        )

    def _open_mic_settings(self) -> None:
        """Open Windows microphone settings."""
        try:
            # os.startfile ist die sichere Windows-API (kein shell=True nötig)
            import os

            os.startfile("ms-settings:privacy-microphone")
            if self._mic_status_label is not None:
                _set_widget_text_if_changed(
                    self._mic_status_label,
                    "Windows-Mikrofoneinstellungen wurden geöffnet",
                )
                _set_widget_stylesheet_if_changed(
                    self._mic_status_label, f"color: {COLORS['text_secondary']};"
                )
        except Exception as e:
            logger.warning(f"Konnte Einstellungen nicht öffnen: {e}")
            if self._mic_status_label is not None:
                _set_widget_text_if_changed(
                    self._mic_status_label,
                    "Konnte Windows-Mikrofoneinstellungen nicht öffnen",
                )
                _set_widget_stylesheet_if_changed(
                    self._mic_status_label, f"color: {COLORS['warning']};"
                )

    # -------------------------------------------------------------------------
    # Step: Hotkey
    # -------------------------------------------------------------------------

    def _ensure_default_hotkeys(self) -> None:
        """Setzt empfohlene Hotkeys beim Erstlauf, falls beide fehlen."""
        toggle, hold = self._get_cached_hotkeys()

        # Bestehende Nutzerkonfiguration niemals überschreiben.
        if toggle or hold:
            return

        toggle = DEFAULT_WINDOWS_TOGGLE_HOTKEY
        hold = DEFAULT_WINDOWS_HOLD_HOTKEY
        changed = self._persist_hotkeys(toggle, hold)

        if self._toggle_input:
            self._toggle_input.setText(toggle)
        if self._hold_input:
            self._hold_input.setText(hold)

        self._set_hotkey_status(
            "Standard gesetzt: "
            f"Toggle {_format_hotkey_for_display(toggle)}, "
            f"Hold {_format_hotkey_for_display(hold)}.",
            "text_secondary",
        )
        self._refresh_test_hotkey_label()
        if changed:
            self.settings_changed.emit()

    def _toggle_hotkey_capture(self, field: str) -> None:
        if self._recording_field == field:
            if self._hotkey_recorded:
                self._stop_hotkey_recording(save=True)
            else:
                self._set_hotkey_status(
                    "Drücke zuerst die gewünschte Tastenkombination.", "warning"
                )
            return
        self._start_hotkey_recording(field)

    def _update_hotkey_capture_ui(self) -> None:
        active_field = self._recording_field
        button_rows = (
            (
                "toggle",
                getattr(self, "_toggle_record_btn", None),
                getattr(self, "_toggle_clear_btn", None),
            ),
            (
                "hold",
                getattr(self, "_hold_record_btn", None),
                getattr(self, "_hold_clear_btn", None),
            ),
        )
        for field_name, record_btn, clear_btn in button_rows:
            is_active = active_field == field_name
            other_active = active_field is not None and not is_active
            if record_btn is not None:
                _set_widget_text_if_changed(
                    record_btn, "Speichern" if is_active else "Aufnehmen"
                )
                _set_widget_enabled_if_changed(record_btn, not other_active)
            if clear_btn is not None:
                _set_widget_enabled_if_changed(clear_btn, active_field is None)

    def _start_hotkey_recording(self, field: str) -> None:
        """Start recording a hotkey."""
        self._stop_hotkey_recording()
        self._begin_hotkey_recording_state(field)
        self._prepare_hotkey_recording_input(field)

        self._set_hotkey_status(
            "Drücke die gewünschte Tastenkombination und bestätige mit Enter oder „Speichern“. Esc bricht ab.",
            "text_secondary",
        )
        self._update_hotkey_capture_ui()

        available, key_map = get_pynput_key_map()
        if not available:
            logger.warning("pynput nicht verfügbar, nutze Qt-Fallback")
            self._activate_qt_hotkey_fallback(
                "pynput nicht verfügbar: Win-Taste evtl. nicht erkennbar."
            )
            return

        try:
            self._start_pynput_hotkey_listener(key_map)
        except Exception as e:
            logger.warning(f"pynput Listener fehlgeschlagen: {e}, nutze Qt-Fallback")
            self._hotkey_listener = None
            self._activate_qt_hotkey_fallback(
                "pynput Listener fehlgeschlagen: Win-Taste evtl. nicht erkennbar."
            )

    def _begin_hotkey_recording_state(self, field: str) -> None:
        self._recording_field = field
        self._hotkey_recorded = False
        self._using_qt_grab = False
        self._clear_hotkey_preview(field)

    def _clear_hotkey_preview(self, field: str) -> None:
        last_hotkey_preview_by_field = getattr(
            self, "_last_hotkey_preview_by_field", None
        )
        if last_hotkey_preview_by_field is None:
            last_hotkey_preview_by_field = {}
            self._last_hotkey_preview_by_field = last_hotkey_preview_by_field
        last_hotkey_preview_by_field.pop(field, None)

    def _prepare_hotkey_recording_input(self, field: str) -> None:
        input_field = self._hotkey_input_for_field(field)
        if input_field:
            _set_widget_text_if_changed(input_field, "Tastenkombination drücken…")
            input_field.setStyleSheet(f"border-color: {COLORS['accent']};")

        with self._pressed_keys_lock:
            self._pressed_keys.clear()

    def _hotkey_input_for_field(self, field: str | None):
        if field == "toggle":
            return self._toggle_input
        if field == "hold":
            return self._hold_input
        return None

    def _start_pynput_hotkey_listener(self, key_map: dict) -> None:
        from pynput import keyboard

        self._hotkey_listener = keyboard.Listener(
            on_press=lambda key: self._on_pynput_hotkey_press(key, key_map),
            on_release=lambda key: self._on_pynput_hotkey_release(key, key_map),
        )
        self._hotkey_listener.start()
        self.setFocus()

    def _on_pynput_hotkey_press(self, key, key_map: dict) -> None:
        if self._is_closed:
            return
        key_name = self._pynput_key_to_string(key, key_map)
        if not key_name or key_name in ("enter", "return", "esc", "escape"):
            return
        with self._pressed_keys_lock:
            self._pressed_keys.add(key_name)
        self._hotkey_recorded = True
        self._update_hotkey_field_from_pressed_keys()

    def _on_pynput_hotkey_release(self, key, key_map: dict) -> None:
        if self._is_closed:
            return
        key_name = self._pynput_key_to_string(key, key_map)
        if key_name:
            with self._pressed_keys_lock:
                self._pressed_keys.discard(key_name)

    def _activate_qt_hotkey_fallback(self, message_prefix: str) -> None:
        """Aktiviert Qt-Keyboard-Capture als Fallback."""
        self._using_qt_grab = True
        self.grabKeyboard()
        self._set_hotkey_status(f"{message_prefix} (Qt-Fallback aktiv).", "warning")
        self.setFocus()

    def _pynput_key_to_string(self, key, key_map: dict) -> str:
        """Convert pynput key to string."""
        # Known keys from map
        if key in key_map:
            return key_map[key]

        # F-keys (f1-f24)
        if hasattr(key, "name") and key.name:
            name = key.name
            if name.startswith("f") and len(name) > 1 and name[1:].isdigit():
                return name.lower()

        # Normal characters
        if hasattr(key, "char") and key.char:
            return key.char.lower()

        # Other named keys
        if hasattr(key, "name") and key.name:
            return key.name.lower()

        return ""

    def _update_hotkey_field_from_pressed_keys(self) -> None:
        """Update the hotkey field based on pressed keys."""
        if not self._recording_field:
            return
        recording_field = self._recording_field

        with self._pressed_keys_lock:
            if not self._pressed_keys:
                return
            pressed_copy = set(self._pressed_keys)

        # Sort: modifiers first, then other keys
        modifiers = []
        keys = []
        for k in pressed_copy:
            if k in ("ctrl", "alt", "shift", "win"):
                modifiers.append(k)
            else:
                keys.append(k)

        # Stable order for modifiers
        modifier_order = ["ctrl", "alt", "shift", "win"]
        sorted_modifiers = [m for m in modifier_order if m in modifiers]

        hotkey_str = "+".join(sorted_modifiers + sorted(keys))
        last_hotkey_preview_by_field = getattr(
            self, "_last_hotkey_preview_by_field", None
        )
        if last_hotkey_preview_by_field is None:
            last_hotkey_preview_by_field = {}
            self._last_hotkey_preview_by_field = last_hotkey_preview_by_field
        if last_hotkey_preview_by_field.get(recording_field) == hotkey_str:
            return
        last_hotkey_preview_by_field[recording_field] = hotkey_str

        # Thread-safe UI update via signal
        self._hotkey_field_update.emit(recording_field, hotkey_str)

    def _set_hotkey_field_text(self, field: str, hotkey_str: str) -> None:
        """Set text in the active hotkey field (thread-safe slot)."""
        if self._is_closed:
            return
        if self._recording_field != field:
            return
        if field == "toggle" and self._toggle_input:
            _set_widget_text_if_changed(self._toggle_input, hotkey_str)
        elif field == "hold" and self._hold_input:
            _set_widget_text_if_changed(self._hold_input, hotkey_str)

    def _stop_hotkey_recording(self, save: bool = False) -> None:
        """Stop hotkey recording."""
        self._stop_active_hotkey_listener()
        field = self._recording_field
        if field:
            self._clear_hotkey_preview(field)
        self._persist_or_restore_hotkey_field(field, save=save)
        self._reset_hotkey_recording_state()
        self._reset_hotkey_input_styles()
        self._update_hotkey_capture_ui()

    def _stop_active_hotkey_listener(self) -> None:
        if self._hotkey_listener:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
        if self._using_qt_grab:
            self.releaseKeyboard()
            self._using_qt_grab = False

    def _persist_or_restore_hotkey_field(
        self,
        field: str | None,
        *,
        save: bool,
    ) -> None:
        input_field = self._hotkey_input_for_field(field)
        if field is None or input_field is None:
            return
        if save and self._hotkey_recorded:
            self._save_recorded_hotkey_or_restore(field, input_field)
            return
        self._restore_hotkey_field_from_cache(field, input_field)

    def _save_recorded_hotkey_or_restore(self, field: str, input_field) -> None:
        hotkey = input_field.text().strip()
        if not hotkey:
            return
        if not self._save_hotkey(field, hotkey):
            self._restore_hotkey_field_from_cache(field, input_field)

    def _restore_hotkey_field_from_cache(self, field: str, input_field) -> None:
        env_key = self._hotkey_env_key(field)
        _set_widget_text_if_changed(
            input_field, self._get_cached_env_setting(env_key) or ""
        )

    @staticmethod
    def _hotkey_env_key(field: str) -> str:
        if field == "toggle":
            return "PULSESCRIBE_TOGGLE_HOTKEY"
        return "PULSESCRIBE_HOLD_HOTKEY"

    def _reset_hotkey_recording_state(self) -> None:
        self._recording_field = None
        self._hotkey_recorded = False
        with self._pressed_keys_lock:
            self._pressed_keys.clear()

    def _reset_hotkey_input_styles(self) -> None:
        for inp in (self._toggle_input, self._hold_input):
            if inp:
                inp.setStyleSheet("")

    def _save_hotkey(self, field: str, hotkey: str) -> bool:
        """Save a hotkey to settings. Called by _stop_hotkey_recording."""
        toggle_raw = (
            hotkey
            if field == "toggle"
            else self._get_cached_env_setting("PULSESCRIBE_TOGGLE_HOTKEY")
        )
        hold_raw = (
            hotkey
            if field == "hold"
            else self._get_cached_env_setting("PULSESCRIBE_HOLD_HOTKEY")
        )
        toggle, hold, error = self._get_hotkey_validation_result(toggle_raw, hold_raw)
        if error:
            self._set_hotkey_status(error, "error")
            return False

        changed = self._persist_hotkeys(toggle, hold)

        self._set_hotkey_status(
            "✓ Hotkey gespeichert – du kannst ihn im nächsten Schritt testen.",
            "success",
        )
        if changed:
            self.settings_changed.emit()
        self._refresh_test_hotkey_label()
        self._update_navigation()
        return True

    def _clear_hotkey(self, field: str) -> None:
        """Clear a hotkey."""
        # Falls gerade eine Aufnahme läuft, zuerst sauber abbrechen
        # (Listener/Keyboard-Grab), bevor der Wert geleert wird.
        if self._recording_field:
            self._stop_hotkey_recording(save=False)

        if field == "toggle":
            if self._toggle_input:
                _set_widget_text_if_changed(self._toggle_input, "")
            changed = self._persist_hotkeys(None, self._get_cached_hotkeys()[1])
        elif field == "hold":
            if self._hold_input:
                _set_widget_text_if_changed(self._hold_input, "")
            changed = self._persist_hotkeys(self._get_cached_hotkeys()[0], None)
        else:
            changed = False

        self._set_hotkey_status(
            "Hotkey entfernt. Mindestens ein Hotkey sollte aktiv bleiben.",
            "text_secondary",
        )
        if changed:
            self.settings_changed.emit()
        self._refresh_test_hotkey_label()
        self._update_navigation()

    def _apply_hotkey_preset(self, toggle: str | None, hold: str | None) -> None:
        """Apply a hotkey preset."""
        if self._recording_field:
            self._stop_hotkey_recording(save=False)

        normalized_toggle, normalized_hold, error = self._get_hotkey_validation_result(
            toggle, hold
        )
        if error:
            self._set_hotkey_status(error, "error")
            return

        if normalized_toggle:
            if self._toggle_input:
                _set_widget_text_if_changed(self._toggle_input, normalized_toggle)
        else:
            if self._toggle_input:
                _set_widget_text_if_changed(self._toggle_input, "")

        if normalized_hold:
            if self._hold_input:
                _set_widget_text_if_changed(self._hold_input, normalized_hold)
        else:
            if self._hold_input:
                _set_widget_text_if_changed(self._hold_input, "")

        changed = self._persist_hotkeys(normalized_toggle, normalized_hold)

        self._set_hotkey_status(
            "✓ Preset angewendet – du kannst die Werte bei Bedarf noch anpassen.",
            "success",
        )
        if changed:
            self.settings_changed.emit()
        self._refresh_test_hotkey_label()
        self._update_navigation()

    @staticmethod
    def _is_modifier_only_hotkey(hotkey: str) -> bool:
        parts = [part for part in hotkey.split("+") if part]
        modifiers = {"ctrl", "alt", "shift", "win"}
        return bool(parts) and all(part in modifiers for part in parts)

    def _validate_hotkey_pair(
        self, toggle_raw: str | None, hold_raw: str | None
    ) -> tuple[str, str, str | None]:
        toggle, toggle_error = normalize_windows_hotkey(toggle_raw)
        if toggle_error:
            return "", "", f"Toggle-Hotkey ungültig: {toggle_error}"

        hold, hold_error = normalize_windows_hotkey(hold_raw)
        if hold_error:
            return "", "", f"Hold-Hotkey ungültig: {hold_error}"

        if toggle and self._is_modifier_only_hotkey(toggle):
            return (
                "",
                "",
                "Toggle-Hotkey braucht mindestens eine Nicht-Modifier-Taste.",
            )

        if toggle and hold and toggle == hold:
            return "", "", "Toggle und Hold dürfen nicht identisch sein."
        if toggle and hold and hotkeys_conflict(toggle, hold):
            return (
                "",
                "",
                "Toggle und Hold dürfen sich nicht überlappen "
                "(z. B. ctrl+win und ctrl+win+r).",
            )

        return toggle, hold, None

    def _set_hotkey_status(self, text: str, color_key: str) -> None:
        if self._hotkey_status_label:
            _set_widget_text_if_changed(self._hotkey_status_label, text)
            _set_widget_stylesheet_if_changed(
                self._hotkey_status_label, f"color: {COLORS[color_key]};"
            )

    def _refresh_test_hotkey_label(self) -> None:
        """Aktualisiert den Hotkey-Hinweis im Test-Schritt."""
        if not self._test_hotkey_label:
            return

        toggle, hold = self._get_cached_hotkeys()
        summary_text = _format_hotkey_summary_text(toggle, hold, separator="\n")
        if summary_text:
            summary_text = f"Gespeicherte Hotkeys:\n{summary_text}"
        else:
            summary_text = "Noch kein Hotkey konfiguriert"
        if getattr(self, "_last_test_hotkey_summary", None) == summary_text:
            return
        self._last_test_hotkey_summary = summary_text
        _set_widget_text_if_changed(self._test_hotkey_label, summary_text)

    # -------------------------------------------------------------------------
    # Step: Cheat Sheet
    # -------------------------------------------------------------------------

    def _update_summary(self) -> None:
        """Update the summary labels."""
        # Mode
        mode = self._get_cached_env_setting("PULSESCRIBE_MODE") or "deepgram"
        if "mode" in self._summary_labels:
            _set_widget_text_if_changed(
                self._summary_labels["mode"], _format_mode_label(mode)
            )

        # Hotkeys
        toggle, hold = self._get_cached_hotkeys()
        if "hotkeys" in self._summary_labels:
            _set_widget_text_if_changed(
                self._summary_labels["hotkeys"],
                _format_hotkey_summary_text(toggle, hold) or "Nicht konfiguriert",
            )

        # Language
        lang = self._get_cached_env_setting("PULSESCRIBE_LANGUAGE") or "auto"
        if "language" in self._summary_labels:
            _set_widget_text_if_changed(
                self._summary_labels["language"], _format_language_label(lang)
            )

        if "test" in self._summary_labels:
            test_text, test_color_key = _build_test_summary_feedback(
                getattr(self, "_test_outcome", "pending")
            )
            _set_widget_text_if_changed(self._summary_labels["test"], test_text)
            _set_widget_stylesheet_if_changed(
                self._summary_labels["test"], f"color: {COLORS[test_color_key]};"
            )

    def _set_test_transcript_text(self, text: str) -> bool:
        editor = getattr(self, "_test_transcript", None)
        if editor is None:
            self._last_test_transcript_text = text or ""
            return False

        next_text = text or ""
        previous_text = self._previous_test_transcript_text(editor)
        if next_text == previous_text:
            return False

        if not self._try_append_test_transcript_delta(
            editor,
            previous_text,
            next_text,
        ):
            self._replace_test_transcript_text(editor, next_text)

        self._last_test_transcript_text = next_text
        return True

    def _previous_test_transcript_text(self, editor) -> str:
        previous_text = getattr(self, "_last_test_transcript_text", None)
        if previous_text is not None:
            return previous_text
        current_text = getattr(editor, "toPlainText", None)
        if callable(current_text):
            try:
                return str(current_text())
            except TypeError:
                return ""
        return str(getattr(editor, "value", "") or "")

    @staticmethod
    def _try_append_test_transcript_delta(
        editor,
        previous_text: str,
        next_text: str,
    ) -> bool:
        if not previous_text or not next_text.startswith(previous_text):
            return False

        delta = next_text[len(previous_text):]
        move_cursor = getattr(editor, "moveCursor", None)
        insert_plain_text = getattr(editor, "insertPlainText", None)
        if not delta or not callable(move_cursor) or not callable(insert_plain_text):
            return False

        try:
            from PySide6.QtGui import QTextCursor

            move_cursor(QTextCursor.MoveOperation.End)
            insert_plain_text(delta)
            return True
        except Exception:
            return False

    def _replace_test_transcript_text(self, editor, next_text: str) -> None:
        if next_text:
            _set_plain_text_if_changed(editor, next_text)
            return
        self._clear_test_transcript_text(editor)

    @staticmethod
    def _clear_test_transcript_text(editor) -> None:
        clear = getattr(editor, "clear", None)
        if not callable(clear):
            _set_plain_text_if_changed(editor, "")
            return

        current_text = getattr(editor, "toPlainText", None)
        if callable(current_text):
            try:
                if current_text():
                    clear()
            except TypeError:
                clear()
        elif getattr(editor, "value", ""):
            clear()

    # -------------------------------------------------------------------------
    # Keyboard Events
    # -------------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        """Handle key press for hotkey recording confirmation."""
        if self._recording_field:
            self._handle_hotkey_recording_key_event(event)
            return

        super().keyPressEvent(event)

    def _handle_hotkey_recording_key_event(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._stop_hotkey_recording(save=False)
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm_hotkey_recording(event)
            return

        if self._using_qt_grab:
            self._capture_qt_hotkey_event(event)
        event.accept()

    def _confirm_hotkey_recording(self, event) -> None:
        if not self._hotkey_recorded:
            self._set_hotkey_status(
                "Drücke zuerst die gewünschte Tastenkombination.",
                "warning",
            )
        self._stop_hotkey_recording(save=True)
        event.accept()

    def _capture_qt_hotkey_event(self, event) -> None:
        is_auto_repeat = getattr(event, "isAutoRepeat", lambda: False)()
        if is_auto_repeat:
            return

        hotkey_str = "+".join(self._qt_hotkey_parts(event))
        if hotkey_str:
            self._hotkey_recorded = True
        if self._recording_field:
            self._set_hotkey_field_text(self._recording_field, hotkey_str)

    def _qt_hotkey_parts(self, event) -> list[str]:
        parts = self._qt_modifier_parts(event.modifiers())
        key_name = self._qt_key_to_string(event.key())
        if key_name and key_name not in ("ctrl", "alt", "shift", "win"):
            parts.append(key_name)
        return parts

    @staticmethod
    def _qt_modifier_parts(modifiers) -> list[str]:
        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("win")
        return parts

    def _qt_key_to_string(self, key: int) -> str:
        """Convert Qt key code into normalized hotkey token."""
        special_keys = {
            Qt.Key.Key_Space: "space",
            Qt.Key.Key_Tab: "tab",
            Qt.Key.Key_Backspace: "backspace",
            Qt.Key.Key_Delete: "delete",
            Qt.Key.Key_Home: "home",
            Qt.Key.Key_End: "end",
            Qt.Key.Key_PageUp: "pageup",
            Qt.Key.Key_PageDown: "pagedown",
            Qt.Key.Key_Up: "up",
            Qt.Key.Key_Down: "down",
            Qt.Key.Key_Left: "left",
            Qt.Key.Key_Right: "right",
            Qt.Key.Key_Control: "ctrl",
            Qt.Key.Key_Alt: "alt",
            Qt.Key.Key_Shift: "shift",
            Qt.Key.Key_Meta: "win",
        }
        if key in special_keys:
            return special_keys[key]

        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
            return f"f{key - Qt.Key.Key_F1 + 1}"

        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(ord("a") + key - Qt.Key.Key_A)

        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(ord("0") + key - Qt.Key.Key_0)

        return ""

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Handle window close."""
        self._is_closed = True
        self._stop_hotkey_recording()
        self._cancel_ipc_test_if_running()
        self._stop_ipc_polling()
        if self._mic_timer:
            self._mic_timer.stop()
        super().closeEvent(event)

    def reject(self) -> None:
        """Handle ESC key or Cancel - ensures proper cleanup."""
        self._is_closed = True
        self._stop_hotkey_recording()
        self._cancel_ipc_test_if_running()
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
