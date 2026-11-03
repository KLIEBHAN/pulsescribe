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
from ui import settings_theme as theme
from ui.onboarding_copy import (
    FAST_API_KEY_PROMPT,
    HOTKEY_CUSTOM_KEY,
    build_two_hotkeys_hint,
    hotkey_choice_title,
    select_hotkey_choice,
    MODE_API_KEYS,
    STEP_NAV_LABELS,
    build_hotkey_summary,
    build_provider_summary,
    build_ready_text,
    build_test_summary,
)
from ui.styles_windows import (
    LANGUAGE_OPTIONS,
    SETTINGS_COLORS as COLORS,
    badge_stylesheet,
    get_pynput_key_map,
    get_wizard_stylesheet,
    ui_font,
)
from utils.onboarding import (
    ONBOARDING_ORDER,
    OnboardingChoice,
    OnboardingStep,
    next_step,
    prev_step,
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

WIZARD_WIDTH = 640
WIZARD_HEIGHT = 520
SIDEBAR_WIDTH = 150
PADDING = 24
FOOTER_HEIGHT = 56
CHOICE_ROW_HEIGHT = 62

# Sidebar entries, page titles and subtitles per step.
WIZARD_STEPS = ONBOARDING_ORDER[:-1]
STEP_TITLES = {
    OnboardingStep.CHOOSE_GOAL: (
        "Welcome to PulseScribe",
        "Choose how speech is transcribed. You can change it in Settings.",
    ),
    OnboardingStep.PERMISSIONS: (
        "Permissions",
        "PulseScribe only needs the microphone on Windows.",
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
# (choice, title, description, tag)
GOAL_CHOICES = (
    (
        OnboardingChoice.FAST,
        "Fast",
        "Lowest latency. Uses Deepgram streaming when available.",
        "Recommended",
    ),
    (
        OnboardingChoice.PRIVATE,
        "Private",
        "Transcribes locally with Whisper. Audio stays on this PC.",
        None,
    ),
    (
        OnboardingChoice.ADVANCED,
        "Advanced",
        "Keeps your current providers. Fine-tune them in Settings.",
        None,
    ),
)
DEFAULT_WINDOWS_TOGGLE_HOTKEY = "ctrl+alt+r"
DEFAULT_WINDOWS_HOLD_HOTKEY = "ctrl+win"
# Hotkey rows: (key, slot, hotkey, description); "Custom" records a Toggle.
HOTKEY_CHOICES = (
    (
        "hold_ctrl_win",
        "hold",
        DEFAULT_WINDOWS_HOLD_HOTKEY,
        "Hold while you speak, release to transcribe.",
    ),
    (
        "toggle_ctrl_alt_r",
        "toggle",
        DEFAULT_WINDOWS_TOGGLE_HOTKEY,
        "Press once to start, again to stop.",
    ),
    ("toggle_f19", "toggle", "f19", "Press once to start, again to stop."),
)
FAST_API_KEY_ENTERED_TEXT = "Key entered. Fast mode will use Deepgram."
FAST_API_KEY_HINT = "Needed for Fast mode. A stored Groq key works too."

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
    "groq": "Groq (Cloud, fast)",
    "openai": "OpenAI Whisper (Cloud)",
    "local": "Local Whisper (Private, offline)",
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

# Practice step: transcript box text per test state (errors add their detail).
TEST_TRANSCRIPT_TEXTS = {
    "pending": "Your text will appear here.",
    "connecting": "Getting ready… The first test can take a moment.",
    "recording": "Listening… say a short sentence, then click Stop.",
    "processing": "Transcribing…",
    "no_speech": "No speech detected. Try again with a short sentence.",
    "cancelled": "Cancelled. Start again whenever you're ready.",
    "skipped": "Cancelled. Start again whenever you're ready.",
}
# Practice step: transcript badge per test state; idle states hide it.
TEST_STATUS_BADGES = {
    "recording": "Listening",
    "processing": "Processing",
    "passed": "Worked",
    "no_speech": "No speech",
    "error": "Error",
}
TEST_ERROR_NO_CONNECTION = "No connection to PulseScribe"
TEST_ERROR_NO_RESULT = "PulseScribe sent no final result"


# =============================================================================
# Helper Functions
# =============================================================================


def _create_label(text: str, role: str = "body", color: str | None = None) -> QLabel:
    """Label in a type-scale role, optionally colored with a theme token."""
    label = QLabel(text)
    label.setFont(ui_font(role))
    if color:
        label.setStyleSheet(f"color: {color};")
    return label


def _create_choice_row(title: str, description: str, tag: str | None = None):
    """Selectable row: radio, title (+ outlined tag) and a muted description.

    Returns ``(button, ring, dot, description_label)``; the button is checkable,
    and ``_set_choice_row_selected`` marks it.
    """
    button = QPushButton()
    button.setObjectName("choiceRow")
    button.setCheckable(True)
    button.setFixedHeight(CHOICE_ROW_HEIGHT)
    button.setAccessibleName(f"{title}: {description}")
    row = QHBoxLayout(button)
    row.setContentsMargins(16, 0, 12, 0)
    row.setSpacing(14)

    ring = QFrame()
    ring.setObjectName("radioRing")
    ring.setFixedSize(14, 14)
    ring_layout = QVBoxLayout(ring)
    ring_layout.setContentsMargins(3, 3, 3, 3)
    dot = QFrame()
    dot.setObjectName("radioDot")
    dot.setFixedSize(6, 6)
    dot.setVisible(False)
    ring_layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignCenter)
    row.addWidget(ring, 0, Qt.AlignmentFlag.AlignVCenter)

    text = QVBoxLayout()
    text.setSpacing(2)
    text.addStretch()
    title_row = QHBoxLayout()
    title_row.setSpacing(8)
    title_label = QLabel(title)
    title_font = ui_font("sidebar_title")
    title_font.setWeight(QFont.Weight.DemiBold)
    title_label.setFont(title_font)
    title_row.addWidget(title_label)
    if tag:
        tag_label = _create_label(tag, "caption")
        tag_label.setObjectName("choiceTag")
        tag_label.setFixedHeight(18)
        title_row.addWidget(tag_label)
    title_row.addStretch()
    text.addLayout(title_row)
    desc_label = _create_label(description, "body", COLORS["text_secondary"])
    text.addWidget(desc_label)
    text.addStretch()
    row.addLayout(text, 1)

    for child in (ring, dot, title_label, desc_label, *button.findChildren(QLabel)):
        child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return button, ring, dot, desc_label


def _set_choice_row_selected(row, selected: bool) -> None:
    """Marks a choice row (fill, light left edge, filled radio)."""
    button, ring, dot, _desc = row
    button.setChecked(selected)
    dot.setVisible(selected)
    if ring.property("selected") != selected:
        ring.setProperty("selected", selected)
        ring.style().unpolish(ring)
        ring.style().polish(ring)


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
    # isHidden(), not isVisible(): steps render before the dialog is shown.
    return set_widget_visible_if_changed(widget, visible, prefer_hidden_state=True)


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


def _normalize_test_error_message(error: str | None) -> str:
    """Practice error shown in the transcript box: what happened and what to try."""
    detail = " ".join((error or "").split())
    detail_lower = detail.lower()
    # Daemon errors are German ("Bereits in Aufnahme", ...); match them by keyword.
    if not detail:
        return "The test could not finish. Try again."
    if TEST_ERROR_NO_CONNECTION.lower() in detail_lower:
        return "No connection to PulseScribe. Check that it is running, then try again."
    if "bereits in aufnahme" in detail_lower or "busy" in detail_lower:
        return "PulseScribe is still busy with another recording. Try again in a moment."
    if TEST_ERROR_NO_RESULT.lower() in detail_lower:
        return "PulseScribe sent no result yet. Try again."
    if "mikro" in detail_lower or "microphone" in detail_lower:
        return "The microphone is not available. Check the input device in Windows, then try again."
    if "aufnahme konnte nicht gestartet werden" in detail_lower:
        return "The test recording could not start. Try again."
    if detail[0].islower():
        detail = detail[0].upper() + detail[1:]
    return f"{detail} Try again."


def _build_test_status_text(state: str | None, *, error: str | None = None) -> str:
    """Transcript badge text for a test state ("" hides the badge)."""
    return TEST_STATUS_BADGES.get((state or "pending").strip().lower(), "")


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


def _build_test_transcript_text(state: str | None, *, error: str | None = None) -> str:
    normalized = (state or "pending").strip().lower()
    if normalized == "error":
        return _normalize_test_error_message(error)
    return TEST_TRANSCRIPT_TEXTS.get(normalized, TEST_TRANSCRIPT_TEXTS["pending"])


def _set_badge(label, text: str, tone: str) -> None:
    """Shows ``text`` as a tinted badge in ``tone`` ("ok", "warn", "err", "muted")."""
    _set_widget_text_if_changed(label, text)
    _set_widget_stylesheet_if_changed(label, badge_stylesheet(tone))


def _build_microphone_summary(outcome: str | None) -> tuple[str, str, str]:
    """Review's Permissions row: Windows can only confirm the mic by recording."""
    if (outcome or "").strip().lower() == "passed":
        return "The microphone worked in the practice test", "Granted", "ok"
    return "Windows asks for microphone access on first use", "Not checked", "muted"


def _build_test_start_button_text(outcome: str | None, *, started_once: bool) -> str:
    normalized = (outcome or "pending").strip().lower()
    if normalized in {"passed", "no_speech", "error", "cancelled", "skipped"} or started_once:
        return "Try again"
    return "Start practice"


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
        self._choice_buttons: dict[OnboardingChoice, tuple] = {}  # choice -> row widgets
        # step -> (entry button, marker label, name label)
        self._sidebar_entries: dict[OnboardingStep, tuple] = {}
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
        self._skip_btn: QPushButton | None = None
        self._page_title: QLabel | None = None
        self._page_subtitle: QLabel | None = None

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
        self._hotkey_status_label: QLabel | None = None
        self._hotkey_hint_label: QLabel | None = None
        self._hotkey_rows: dict[str, tuple] = {}  # row key -> row widgets
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
            self._set_test_status(_build_test_status_text("passed"), "success")
            self._update_navigation()

    # -------------------------------------------------------------------------
    # UI Setup
    # -------------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle("PulseScribe Setup")
        self.setFixedSize(WIZARD_WIDTH, WIZARD_HEIGHT)
        self.setStyleSheet(get_wizard_stylesheet())
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self._page_title = _create_label("", "page_title")
        self._page_title.setContentsMargins(PADDING, 20, PADDING, 0)
        column.addWidget(self._page_title)
        self._page_subtitle = _create_label("", "body", COLORS["text_secondary"])
        self._page_subtitle.setContentsMargins(PADDING, 2, PADDING, 16)
        column.addWidget(self._page_subtitle)

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
        column.addWidget(self._stack, 1)

        # Footer: quiet Settings link left, text buttons and primary right.
        footer = QWidget()
        footer.setObjectName("saveBar")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer.setFixedHeight(FOOTER_HEIGHT)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(PADDING - 10, 0, PADDING, 0)
        footer_layout.setSpacing(4)

        settings_link = QPushButton("Open Settings…")
        settings_link.setObjectName("textButton")
        settings_link.clicked.connect(self._open_settings_after)
        footer_layout.addWidget(settings_link)
        footer_layout.addStretch()

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setObjectName("textButton")
        self._skip_btn.clicked.connect(self._skip_test)
        footer_layout.addWidget(self._skip_btn)

        self._back_btn = QPushButton("Back")
        self._back_btn.setObjectName("textButton")
        self._back_btn.clicked.connect(self._go_back)
        footer_layout.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next")
        self._next_btn.setObjectName("primary")
        self._next_btn.setMinimumWidth(96)
        self._next_btn.clicked.connect(self._go_next)
        footer_layout.addSpacing(8)
        footer_layout.addWidget(self._next_btn)
        column.addWidget(footer)

        root.addLayout(column, 1)

        # Show current step
        self._show_step(self._step)

    def _build_sidebar(self) -> QWidget:
        """Left column: app name and one entry per step with a progress marker."""
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 20, 10, 20)
        layout.setSpacing(2)

        name = _create_label("PulseScribe", "sidebar_title")
        name.setContentsMargins(10, 0, 10, 0)
        layout.addWidget(name)
        caption = _create_label("Setup", "sidebar_caption", COLORS["text_secondary"])
        caption.setContentsMargins(10, 0, 10, 0)
        layout.addWidget(caption)
        layout.addSpacing(24)

        for step in WIZARD_STEPS:
            entry = QPushButton()
            entry.setObjectName("stepEntry")
            entry.setFixedHeight(35)
            entry.setAccessibleName(STEP_NAV_LABELS[step])
            entry.clicked.connect(lambda _checked=False, s=step: self._go_to_earlier_step(s))
            row = QHBoxLayout(entry)
            row.setContentsMargins(10, 0, 6, 0)
            row.setSpacing(10)
            marker = _create_label("", "caption")
            marker.setFixedSize(18, 18)
            marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = _create_label(STEP_NAV_LABELS[step], "sidebar_item")
            for child in (marker, label):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                row.addWidget(child)
            row.addStretch()
            layout.addWidget(entry)
            self._sidebar_entries[step] = (entry, marker, label)
        layout.addStretch()
        return sidebar

    def _update_sidebar(self, step: OnboardingStep) -> None:
        """Marks done steps with a check and highlights the current one."""
        entries = getattr(self, "_sidebar_entries", None)
        if not entries or step not in WIZARD_STEPS:
            return
        current = WIZARD_STEPS.index(step)
        for index, entry_step in enumerate(WIZARD_STEPS):
            entry, marker, label = entries[entry_step]
            active = index == current
            done = index < current
            if entry.property("current") != active:
                entry.setProperty("current", active)
                entry.style().unpolish(entry)
                entry.style().polish(entry)
            _set_widget_text_if_changed(marker, "✓" if done else str(index + 1))
            if active:
                marker_style = (
                    f"background-color: {theme.PRIMARY_BG}; color: {theme.PRIMARY_TEXT};"
                    f" border: 1px solid {theme.PRIMARY_BG};"
                )
            else:
                marker_style = (
                    f"background-color: transparent;"
                    f" color: {theme.NAV if done else theme.MUTED};"
                    f" border: 1px solid {theme.SECONDARY_BORDER};"
                )
            _set_widget_stylesheet_if_changed(
                marker, marker_style + f" border-radius: {theme.RADIUS}px;"
            )
            _set_widget_stylesheet_if_changed(
                label, f"color: {theme.ACTIVE_TEXT if active else theme.NAV};"
            )
            font = ui_font("sidebar_item")
            if active:
                font.setWeight(QFont.Weight.DemiBold)
            label.setFont(font)

    def _go_to_earlier_step(self, step: OnboardingStep) -> None:
        """Reopens an earlier step from the sidebar; later steps stay locked."""
        current = self._step if self._step in WIZARD_STEPS else OnboardingStep.CHEAT_SHEET
        if WIZARD_STEPS.index(step) < WIZARD_STEPS.index(current):
            self._stop_hotkey_recording()
            self._show_step(step)

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
        layout.setContentsMargins(PADDING, 0, PADDING, 12)
        layout.setSpacing(0)

        rows = QFrame()
        rows.setObjectName("card")  # flat section: top rule
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(0)
        for choice, title, desc, tag in GOAL_CHOICES:
            row = _create_choice_row(title, desc, tag)
            row[0].clicked.connect(lambda _checked=False, c=choice: self._select_choice(c))
            self._choice_buttons[choice] = row
            rows_layout.addWidget(row[0])
        layout.addWidget(rows)

        # Restore previous choice
        if self._choice:
            self._select_choice(self._choice, save=False)

        layout.addSpacing(18)

        # Language: label above the combo box, hint to the right.
        layout.addWidget(_create_label("Language", "caption", theme.NAV))
        layout.addSpacing(4)
        lang_row = QHBoxLayout()
        lang_row.setSpacing(12)
        self._lang_combo = QComboBox()
        self._lang_combo.setMinimumWidth(180)
        self._lang_combo.setMinimumHeight(30)
        for language in LANGUAGE_OPTIONS:
            self._lang_combo.addItem(_format_language_label(language), language)
        current_lang = self._get_cached_env_setting("PULSESCRIBE_LANGUAGE") or "auto"
        current_index = self._lang_combo.findData(current_lang)
        if current_index >= 0:
            self._lang_combo.setCurrentIndex(current_index)
        self._lang_combo.currentIndexChanged.connect(self._on_language_combo_changed)
        lang_row.addWidget(self._lang_combo)
        lang_row.addWidget(
            _create_label("Automatic detects it from speech.", "body", COLORS["text_secondary"])
        )
        lang_row.addStretch()
        layout.addLayout(lang_row)

        # API Key input (shown when Fast is selected without existing key)
        api_container = QWidget()
        api_container.setVisible(False)
        api_layout = QVBoxLayout(api_container)
        api_layout.setContentsMargins(0, 14, 0, 0)
        api_layout.setSpacing(4)
        api_layout.addWidget(_create_label("Deepgram API key", "caption", theme.NAV))

        api_field = QLineEdit()
        api_field.setEchoMode(QLineEdit.EchoMode.Password)
        api_field.setPlaceholderText("Paste your Deepgram key")
        api_field.setMinimumHeight(30)
        api_field.textChanged.connect(self._on_api_key_input_changed)
        api_layout.addWidget(api_field)

        api_status = _create_label("", "caption", COLORS["text_secondary"])
        api_status.setWordWrap(True)
        api_layout.addWidget(api_status)

        layout.addWidget(api_container)

        self._api_key_container = api_container
        self._api_key_field = api_field
        self._api_key_status = api_status

        layout.addStretch()

        return widget

    def _build_permissions_step(self) -> QWidget:
        """Build the permissions step: Windows only needs the microphone."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, 12)
        layout.setSpacing(0)

        row = QWidget()
        row.setObjectName("reviewRow")  # flat row: top and bottom rules
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setStyleSheet(f"QWidget#reviewRow {{ border-top: 1px solid {theme.LINE}; }}")
        row.setFixedHeight(57)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addStretch()
        title = _create_label("Microphone", "body")
        title_font = title.font()
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        text.addWidget(title)
        text.addWidget(_create_label("Required to record your voice.", "body", COLORS["text_secondary"]))
        text.addStretch()
        row_layout.addLayout(text, 1)
        self._mic_status_label = _create_label("", "badge")
        row_layout.addWidget(self._mic_status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        mic_btn = QPushButton("Windows settings…")
        mic_btn.setToolTip("Open the Windows microphone privacy settings")
        mic_btn.clicked.connect(self._open_mic_settings)
        row_layout.addWidget(mic_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(row)

        layout.addSpacing(14)
        hint = _create_label(
            "Windows asks for access the first time PulseScribe records. "
            "The practice test in the next step checks it.",
            "body",
            COLORS["text_secondary"],
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

        return widget

    def _build_hotkey_step(self) -> QWidget:
        """Build the Hotkey step: one choice among presets or a recorded shortcut."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, 12)
        layout.setSpacing(0)

        rows = QFrame()
        rows.setObjectName("card")  # flat section: top rule
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(0)
        for key, kind, hotkey, description in HOTKEY_CHOICES:
            row = _create_choice_row(
                hotkey_choice_title(kind, _format_hotkey_for_display(hotkey)), description
            )
            preset = (hotkey, None) if kind == "toggle" else (None, hotkey)
            row[0].clicked.connect(
                lambda _checked=False, p=preset: self._apply_hotkey_preset(*p)
            )
            self._hotkey_rows[key] = row
            rows_layout.addWidget(row[0])

        # Custom: records a Toggle shortcut into the field on the row.
        # The description wraps and the field takes the remaining width, so
        # neither is clipped with wider system fonts (the field uses the system
        # font, which follows the Windows text size).
        custom = _create_choice_row("Custom", "Your own shortcut.")
        custom[3].setWordWrap(True)
        custom[0].clicked.connect(lambda _checked=False: self._toggle_hotkey_capture("toggle"))
        self._toggle_input = QLineEdit()
        self._toggle_input.setReadOnly(True)
        self._toggle_input.setMinimumWidth(120)
        self._toggle_input.setPlaceholderText("Not set")
        self._toggle_input.mousePressEvent = lambda _event: self._toggle_hotkey_capture("toggle")
        self._toggle_record_btn = QPushButton("Record")
        self._toggle_record_btn.setFixedWidth(76)
        self._toggle_record_btn.clicked.connect(
            lambda _checked=False: self._toggle_hotkey_capture("toggle")
        )
        custom_layout = custom[0].layout()
        custom_layout.addWidget(self._toggle_input, 1, Qt.AlignmentFlag.AlignVCenter)
        custom_layout.addWidget(self._toggle_record_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._hotkey_rows[HOTKEY_CUSTOM_KEY] = custom
        rows_layout.addWidget(custom[0])
        layout.addWidget(rows)

        layout.addSpacing(14)
        self._hotkey_hint_label = _create_label("", "body", COLORS["text_secondary"])
        self._hotkey_hint_label.setWordWrap(True)
        layout.addWidget(self._hotkey_hint_label)
        self._hotkey_status_label = _create_label("", "body", COLORS["text_secondary"])
        self._hotkey_status_label.setWordWrap(True)
        layout.addWidget(self._hotkey_status_label)
        layout.addStretch()

        self._sync_hotkey_rows()
        self._update_hotkey_capture_ui()
        return widget

    def _sync_hotkey_rows(self) -> None:
        """Marks the row matching the configured hotkey and shows the two-hotkey hint."""
        rows = getattr(self, "_hotkey_rows", None)
        if not rows:
            return
        toggle, hold = self._get_cached_hotkeys()
        selected = select_hotkey_choice(
            toggle.lower(),
            hold.lower(),
            ((key, kind, hotkey) for key, kind, hotkey, _desc in HOTKEY_CHOICES),
        )
        for key, row in rows.items():
            _set_choice_row_selected(row, key == selected)
        if not self._recording_field:
            _set_widget_text_if_changed(
                self._toggle_input, toggle if selected == HOTKEY_CUSTOM_KEY else ""
            )
        _set_widget_text_if_changed(
            getattr(self, "_hotkey_hint_label", None),
            build_two_hotkeys_hint(
                _format_hotkey_for_display(toggle), _format_hotkey_for_display(hold)
            ),
        )

    def _build_test_dictation_step(self) -> QWidget:
        """Build the Practice step: Start/Stop, transcript box with badge, latency option."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, 12)
        layout.setSpacing(0)

        # The test runs through Start/Stop only: on Windows the hotkey still
        # starts a real dictation that pastes, so the prompt names the buttons.
        self._test_hotkey_label = _create_label(
            "Click Start practice and say a short sentence, then click Stop.", "sidebar_item"
        )
        self._test_hotkey_label.setWordWrap(True)
        layout.addWidget(self._test_hotkey_label)
        layout.addSpacing(14)

        btn_row = QHBoxLayout()
        self._test_start_btn = QPushButton("Start practice")
        self._test_start_btn.setToolTip("Starts a safe test recording in PulseScribe")
        self._test_start_btn.setMinimumWidth(132)
        self._test_start_btn.clicked.connect(self._start_ipc_test)
        btn_row.addWidget(self._test_start_btn)
        self._test_stop_btn = QPushButton("Stop")
        self._test_stop_btn.setMinimumWidth(132)
        self._test_stop_btn.clicked.connect(self._stop_ipc_test)
        self._test_stop_btn.setVisible(False)
        btn_row.addWidget(self._test_stop_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addSpacing(18)

        header = QHBoxLayout()
        header.addWidget(_create_label("Transcript", "caption", theme.NAV))
        header.addStretch()
        self._test_status_label = _create_label("", "badge")
        self._test_status_label.setVisible(False)
        header.addWidget(self._test_status_label)
        layout.addLayout(header)
        layout.addSpacing(6)

        self._test_transcript = QPlainTextEdit()
        self._test_transcript.setReadOnly(True)
        self._test_transcript.setFixedHeight(132)
        self._test_transcript.setPlainText(TEST_TRANSCRIPT_TEXTS["pending"])
        layout.addWidget(self._test_transcript)
        layout.addSpacing(14)

        # Optionaler Snappy-Toggle direkt nach dem Test, damit man erst die
        # Erkennungsqualität prüfen und nur dann kürzere Puffer aktivieren kann.
        layout.addWidget(self._build_snappy_option())
        layout.addStretch()

        return widget

    def _build_snappy_option(self) -> QWidget:
        """Toggle für das Windows-Latenz-Preset (snappy ist Default).

        Bewusst im Test-Schritt platziert: Wer beim Test abgeschnittene
        Wortenden bemerkt, kann hier auf die konservativen ``safe``-Puffer
        wechseln. Der adaptive Stop-Tail schützt Wortenden allerdings bereits
        unabhängig vom Preset.
        """
        option = QWidget()
        option_layout = QVBoxLayout(option)
        option_layout.setContentsMargins(0, 0, 0, 0)
        option_layout.setSpacing(2)
        is_snappy = (
            (
                self._get_cached_env_setting("PULSESCRIBE_WINDOWS_LATENCY_PRESET")
                or "snappy"
            )
            .strip()
            .lower()
            == "snappy"
        )
        self._snappy_checkbox = QCheckBox("Fast transitions (shorter buffers, default)")
        self._snappy_checkbox.setFont(ui_font("body"))
        self._snappy_checkbox.setChecked(is_snappy)
        self._snappy_checkbox.toggled.connect(self._on_snappy_toggled)
        option_layout.addWidget(self._snappy_checkbox)
        hint = _create_label(
            "Turn off only if the test cuts off your last word. "
            "Takes full effect after PulseScribe restarts.",
            "caption",
            COLORS["text_secondary"],
        )
        hint.setWordWrap(True)
        option_layout.addWidget(hint)
        return option

    def _on_snappy_toggled(self, checked: bool) -> None:
        """Persist the Windows latency preset choice from the onboarding toggle.

        Snappy ist der Default: Aktivieren entfernt den Override, Deaktivieren
        setzt explizit das konservative ``safe``-Preset.
        """
        self._apply_env_updates(
            {"PULSESCRIBE_WINDOWS_LATENCY_PRESET": None if checked else "safe"}
        )

    def _build_cheat_sheet_step(self) -> QWidget:
        """Build the Review step: one row per part of the setup, then the hotkey line."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(PADDING, 0, PADDING, 12)
        layout.setSpacing(0)

        rows = QFrame()
        rows.setObjectName("card")  # flat section: top rule
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(0)
        for key, title in (
            ("mode", "Provider"),
            ("hotkeys", "Hotkey"),
            ("permissions", "Permissions"),
            ("test", "Practice test"),
        ):
            row = QWidget()
            row.setObjectName("reviewRow")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row.setFixedHeight(56)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addStretch()
            title_label = _create_label(title, "body")
            title_font = title_label.font()
            title_font.setWeight(QFont.Weight.DemiBold)
            title_label.setFont(title_font)
            text.addWidget(title_label)
            detail = _create_label("", "body", COLORS["text_secondary"])
            text.addWidget(detail)
            text.addStretch()
            row_layout.addLayout(text, 1)
            badge = _create_label("", "badge")
            row_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
            rows_layout.addWidget(row)
            self._summary_labels[key] = detail
            self._summary_labels[f"{key}_badge"] = badge
        layout.addWidget(rows)

        layout.addSpacing(24)
        ready = _create_label("", "sidebar_item")
        ready.setWordWrap(True)
        layout.addWidget(ready)
        self._summary_labels["ready"] = ready

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
        self._update_page_header(step)
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

    def _update_page_header(self, step: OnboardingStep) -> None:
        """Page title, subtitle and sidebar markers for ``step``."""
        title, subtitle = STEP_TITLES.get(step, ("Setup", ""))
        _set_widget_text_if_changed(getattr(self, "_page_title", None), title)
        _set_widget_text_if_changed(getattr(self, "_page_subtitle", None), subtitle)
        self._update_sidebar(step)

    def _run_step_enter_actions(self, step: OnboardingStep) -> None:
        if step == OnboardingStep.PERMISSIONS:
            self._start_mic_check()
        elif step == OnboardingStep.HOTKEY:
            self._ensure_default_hotkeys()
        elif step == OnboardingStep.TEST_DICTATION:
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

        _set_widget_visible_if_changed(
            getattr(self, "_skip_btn", None), self._step == OnboardingStep.TEST_DICTATION
        )

        # Next button text and state
        if self._step == OnboardingStep.CHEAT_SHEET:
            _set_widget_text_if_changed(self._next_btn, "Finish")
        else:
            _set_widget_text_if_changed(self._next_btn, "Next")

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
                self._set_hotkey_status("Choose a hotkey to continue.", "warning")
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
        _set_widget_visible_if_changed(self._test_start_btn, False)
        stop_btn = getattr(self, "_test_stop_btn", None)
        _set_widget_text_if_changed(stop_btn, "Cancel")
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
            self._update_navigation()
            return

        self._ipc_stop_requested = True
        self._ipc_recording_polls_after_stop = 0
        self._ipc_client.send_command(CMD_STOP_TEST)
        _set_timer_interval_if_supported(self._ipc_poll_timer, IPC_POLL_INTERVAL_MS)

        self._test_outcome = "processing"
        self._set_test_status(_build_test_status_text("processing"), "text_secondary")
        self._set_test_transcript_text(_build_test_transcript_text("processing"))
        stop_btn = getattr(self, "_test_stop_btn", None)
        _set_widget_text_if_changed(stop_btn, "Stopping…")
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
                TEST_ERROR_NO_RESULT,
            )
        else:
            self._on_ipc_test_complete("", TEST_ERROR_NO_CONNECTION)

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
            error = response.get("error", "")
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
        _set_widget_text_if_changed(stop_btn, "Stop")

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
        self._set_test_transcript_text(_build_test_transcript_text("error", error=error))
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
        self._update_navigation()

    def _set_test_status(self, text: str, color_key: str) -> None:
        """Shows the transcript badge tinted for ``color_key``; empty text hides it."""
        label = self._test_status_label
        if label is None:
            return
        _set_widget_visible_if_changed(label, bool(text))
        if text:
            _set_badge(label, text, theme.badge_tone(color_key))

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
        _set_widget_text_if_changed(self._test_stop_btn, "Stop")

    def _complete(self) -> None:
        """Complete the wizard."""
        set_onboarding_step(OnboardingStep.DONE)
        set_onboarding_seen(True)
        self.completed.emit()
        self.accept()

    def _open_settings_after(self) -> None:
        """Finishes setup and asks the running app to open Settings.

        The wizard runs as its own process, so it asks the app over IPC; without
        a running app (wizard started on its own) it only finishes.
        """
        try:
            from utils.ipc import CMD_OPEN_SETTINGS, IPCClient

            (self._ipc_client or IPCClient()).send_command(CMD_OPEN_SETTINGS)
        except Exception as exc:
            logger.warning(f"Could not ask PulseScribe to open Settings: {exc}")
        self._complete()

    # -------------------------------------------------------------------------
    # Step: Choose Goal
    # -------------------------------------------------------------------------

    def _select_choice(self, choice: OnboardingChoice, save: bool = True) -> None:
        """Handle choice selection."""
        self._choice = choice

        # Update row states
        for c, row in self._choice_buttons.items():
            _set_choice_row_selected(row, c == choice)

        # Show/hide API key input based on choice
        if self._api_key_container:
            show_api = choice == OnboardingChoice.FAST and not self._has_api_key()
            _set_widget_visible_if_changed(self._api_key_container, show_api)
            if show_api and self._api_key_status:
                _set_widget_text_if_changed(self._api_key_status, FAST_API_KEY_HINT)
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
            _set_widget_text_if_changed(self._api_key_status, FAST_API_KEY_PROMPT)

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
                _set_widget_text_if_changed(self._api_key_status, FAST_API_KEY_ENTERED_TEXT)
                _set_widget_stylesheet_if_changed(
                    self._api_key_status, f"color: {COLORS['success']};"
                )
            else:
                _set_widget_text_if_changed(self._api_key_status, FAST_API_KEY_HINT)
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
        """Shows the microphone badge; Windows offers no read-only permission check."""
        _detail, badge, tone = _build_microphone_summary(getattr(self, "_test_outcome", "pending"))
        _set_badge(self._mic_status_label, badge, tone)

    def _open_mic_settings(self) -> None:
        """Open Windows microphone settings."""
        try:
            # os.startfile ist die sichere Windows-API (kein shell=True nötig)
            import os

            os.startfile("ms-settings:privacy-microphone")
        except Exception as e:
            logger.warning(f"Konnte Einstellungen nicht öffnen: {e}")
            _set_badge(self._mic_status_label, "Can't open", "warn")

    # -------------------------------------------------------------------------
    # Step: Hotkey
    # -------------------------------------------------------------------------

    def _ensure_default_hotkeys(self) -> None:
        """Preselects the first row (Hold Ctrl+Win) on a first run without hotkeys."""
        toggle, hold = self._get_cached_hotkeys()

        # Bestehende Nutzerkonfiguration niemals überschreiben.
        if toggle or hold:
            return

        changed = self._persist_hotkeys(None, DEFAULT_WINDOWS_HOLD_HOTKEY)
        self._set_hotkey_status(
            "Preselected the recommended hotkey. Pick another row to change it.",
            "text_secondary",
        )
        self._sync_hotkey_rows()
        if changed:
            self.settings_changed.emit()

    def _toggle_hotkey_capture(self, field: str) -> None:
        if self._recording_field == field:
            if self._hotkey_recorded:
                self._stop_hotkey_recording(save=True)
            else:
                self._set_hotkey_status("Press the shortcut you want first.", "warning")
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
                _set_widget_text_if_changed(record_btn, "Save" if is_active else "Record")
                _set_widget_enabled_if_changed(record_btn, not other_active)
            if clear_btn is not None:
                _set_widget_enabled_if_changed(clear_btn, active_field is None)

    def _start_hotkey_recording(self, field: str) -> None:
        """Start recording a hotkey."""
        self._stop_hotkey_recording()
        self._begin_hotkey_recording_state(field)
        self._prepare_hotkey_recording_input(field)

        self._set_hotkey_status(
            "Press the shortcut, then Enter or Save. Esc cancels.",
            "text_secondary",
        )
        self._update_hotkey_capture_ui()

        available, key_map = get_pynput_key_map()
        if not available:
            logger.warning("pynput nicht verfügbar, nutze Qt-Fallback")
            self._activate_qt_hotkey_fallback(
                "The Win key may not be detected while recording."
            )
            return

        try:
            self._start_pynput_hotkey_listener(key_map)
        except Exception as e:
            logger.warning(f"pynput Listener fehlgeschlagen: {e}, nutze Qt-Fallback")
            self._hotkey_listener = None
            self._activate_qt_hotkey_fallback(
                "The Win key may not be detected while recording."
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
            _set_widget_text_if_changed(input_field, "Press keys…")
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
        self._set_hotkey_status(message_prefix, "warning")
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
        """Saves a recorded hotkey as the only one (the other slot is cleared)."""
        toggle_raw, hold_raw = (hotkey, None) if field == "toggle" else (None, hotkey)
        toggle, hold, error = self._get_hotkey_validation_result(toggle_raw, hold_raw)
        if error:
            self._set_hotkey_status(error, "error")
            return False

        changed = self._persist_hotkeys(toggle, hold)

        self._set_hotkey_status("Saved. You can try it in the next step.", "success")
        if changed:
            self.settings_changed.emit()
        self._sync_hotkey_rows()
        self._update_navigation()
        return True

    def _apply_hotkey_preset(self, toggle: str | None, hold: str | None) -> None:
        """Saves a preset row; the slot it leaves empty is cleared."""
        if self._recording_field:
            self._stop_hotkey_recording(save=False)

        normalized_toggle, normalized_hold, error = self._get_hotkey_validation_result(
            toggle, hold
        )
        if error:
            self._set_hotkey_status(error, "error")
            return

        changed = self._persist_hotkeys(normalized_toggle, normalized_hold)

        self._set_hotkey_status("Saved. You can try it in the next step.", "success")
        if changed:
            self.settings_changed.emit()
        self._sync_hotkey_rows()
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
            return "", "", f"Toggle hotkey is not valid: {toggle_error}"

        hold, hold_error = normalize_windows_hotkey(hold_raw)
        if hold_error:
            return "", "", f"Hold hotkey is not valid: {hold_error}"

        if toggle and self._is_modifier_only_hotkey(toggle):
            return (
                "",
                "",
                "A Toggle hotkey needs at least one key besides Ctrl, Alt, Shift or Win.",
            )

        if toggle and hold and toggle == hold:
            return "", "", "Toggle and Hold can't use the same hotkey."
        if toggle and hold and hotkeys_conflict(toggle, hold):
            return (
                "",
                "",
                "Toggle and Hold can't overlap (e.g. ctrl+win and ctrl+win+r).",
            )

        return toggle, hold, None

    def _set_hotkey_status(self, text: str, color_key: str) -> None:
        if self._hotkey_status_label:
            _set_widget_text_if_changed(self._hotkey_status_label, text)
            _set_widget_stylesheet_if_changed(
                self._hotkey_status_label, f"color: {COLORS[color_key]};"
            )

    # -------------------------------------------------------------------------
    # Step: Cheat Sheet
    # -------------------------------------------------------------------------

    def _update_summary(self) -> None:
        """Fills the Review rows (detail + badge) and the closing hotkey line."""
        import os

        labels = self._summary_labels
        mode = (self._get_cached_env_setting("PULSESCRIBE_MODE") or "deepgram").strip().lower()
        key_name = MODE_API_KEYS.get(mode)
        has_key = bool(
            key_name and (self._get_cached_api_key(key_name) or os.getenv(key_name))
        )
        toggle, hold = self._get_cached_hotkeys()
        toggle_text = _format_hotkey_for_display(toggle)
        hold_text = _format_hotkey_for_display(hold)
        outcome = getattr(self, "_test_outcome", "pending")
        rows = {
            "mode": build_provider_summary(_format_mode_label(mode), mode, has_key),
            "hotkeys": build_hotkey_summary(toggle_text, hold_text),
            "permissions": _build_microphone_summary(outcome),
            "test": build_test_summary(outcome),
        }
        for key, (detail, badge, tone) in rows.items():
            _set_widget_text_if_changed(labels.get(key), detail)
            _set_badge(labels.get(f"{key}_badge"), badge, tone)
        _set_widget_text_if_changed(labels.get("ready"), build_ready_text(toggle_text, hold_text))

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
            self._set_hotkey_status("Press the shortcut you want first.", "warning")
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
