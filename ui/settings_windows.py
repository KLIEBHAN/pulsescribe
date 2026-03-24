"""Settings Window für PulseScribe (Windows).

PySide6-basiertes Settings-Fenster mit Dark Theme.
Portiert von ui/welcome.py (macOS AppKit).
"""

import logging
import os
import sys
import threading
import time
from typing import Callable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QDoubleValidator, QFont, QIntValidator
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
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.styles_windows import (
    CARD_PADDING,
    CARD_SPACING,
    COLORS,
    LANGUAGE_OPTIONS,
    get_pynput_key_map,
    get_settings_stylesheet,
)
from utils.preferences import (
    get_api_key,
    get_env_setting,
    get_show_welcome_on_startup,
    is_onboarding_complete,
    read_env_file,
    remove_env_setting,
    save_env_setting,
    set_api_key,
    set_onboarding_step,
    set_show_welcome_on_startup,
)
from utils.local_backend import normalize_local_backend, should_remove_local_backend_env
from utils.local_backend import get_cpu_threads_limit
from utils.hotkey_windows import hotkeys_conflict, normalize_windows_hotkey
from utils.log_tail import (
    clamp_scroll_value,
    get_file_signature,
    is_near_bottom,
    read_file_tail_lines,
    should_auto_refresh_logs,
)
from utils.onboarding import OnboardingStep
from utils.version import get_app_version
from utils.env import parse_bool

logger = logging.getLogger("pulsescribe.settings")

# =============================================================================
# Window-Konstanten (settings-spezifisch)
# =============================================================================

SETTINGS_WIDTH = 600
SETTINGS_HEIGHT = 700

# =============================================================================
# Dropdown-Optionen (identisch mit macOS)
# =============================================================================

MODE_OPTIONS = ["deepgram", "openai", "groq", "local"]
REFINE_PROVIDER_OPTIONS = ["groq", "openai", "openrouter", "gemini"]
LOCAL_BACKEND_OPTIONS = ["auto", "whisper", "faster", "mlx", "lightning"]
LOCAL_MODEL_OPTIONS = [
    "default",
    "turbo",
    "large",
    "large-v3",
    "medium",
    "small",
    "base",
    "tiny",
    "large-en",
    "medium-en",
    "small-en",
]
DEVICE_OPTIONS = ["auto", "cpu", "cuda"]
BOOL_OVERRIDE_OPTIONS = ["default", "true", "false"]
LIGHTNING_QUANT_OPTIONS = ["none", "8bit", "4bit"]
DEFAULT_WINDOWS_TOGGLE_HOTKEY = "ctrl+alt+r"
DEFAULT_WINDOWS_HOLD_HOTKEY = "ctrl+win"
LOCAL_FP16_ENV_KEY = "PULSESCRIBE_FP16"
LEGACY_LOCAL_FP16_ENV_KEY = "PULSESCRIBE_LOCAL_FP16"
MODE_API_KEY_MAP = {
    "deepgram": "DEEPGRAM_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
}
MODE_LABELS = {
    "deepgram": "Deepgram",
    "openai": "OpenAI",
    "groq": "Groq",
    "local": "Local Whisper",
}
WINDOWS_LOCAL_PRESET_BASE = {
    "mode": "local",
    "local_backend": "faster",
    "local_model": "turbo",
    "device": "auto",
    "compute_type": "default",
    "beam_size": "",
    "temperature": "",
    "best_of": "",
    "cpu_threads": "",
    "num_workers": "",
    "vad_filter": "default",
    "without_timestamps": "default",
    "fp16": "default",
    "lightning_batch_size": 12,
    "lightning_quant": "none",
}


# =============================================================================
# Helper Functions
# =============================================================================


def _env_bool_default(value: str | None, default: bool) -> bool:
    """Parst boolsche ENV-Werte robust mit Default-Fallback."""
    parsed = parse_bool(value)
    if parsed is None:
        return default
    return parsed


def _normalize_hotkey_text(value: str | None) -> str:
    return (value or "").strip()


def _build_setup_status(
    mode: str | None,
    *,
    toggle_hotkey: str | None,
    hold_hotkey: str | None,
    api_keys: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    """Ermittelt Setup-Status für die Windows-Settings-Übersicht."""
    current_mode = (mode or "deepgram").strip().lower() or "deepgram"
    toggle = _normalize_hotkey_text(toggle_hotkey)
    hold = _normalize_hotkey_text(hold_hotkey)
    configured_api_keys = api_keys or {}

    if not (toggle or hold):
        return (
            "Action Required",
            "Configure at least one Toggle or Hold hotkey before dictation can start.",
            "warning",
        )

    required_api_key = MODE_API_KEY_MAP.get(current_mode)
    if required_api_key and not _normalize_hotkey_text(
        configured_api_keys.get(required_api_key)
    ):
        provider = MODE_LABELS.get(current_mode, current_mode.capitalize())
        return (
            "Setup Incomplete",
            f"Add a {provider} API key in Providers to start dictation.",
            "warning",
        )

    if current_mode == "local":
        return (
            "Ready for Local Dictation",
            "Audio stays on this device and the current hotkeys are ready to use.",
            "success",
        )

    provider = MODE_LABELS.get(current_mode, current_mode.capitalize())
    return (
        "Ready to Dictate",
        f"{provider} is configured and at least one hotkey is available.",
        "success",
    )


def _build_setup_how_to_text(toggle_hotkey: str | None, hold_hotkey: str | None) -> str:
    """Erstellt How-to-Text basierend auf den aktuell konfigurierten Hotkeys."""
    toggle = _normalize_hotkey_text(toggle_hotkey)
    hold = _normalize_hotkey_text(hold_hotkey)

    if hold and toggle:
        return (
            f"1. Hold {hold} to use push-to-talk.\n"
            "2. Speak while holding the keys.\n"
            "3. Release the keys to stop and transcribe.\n"
            f"Alternative: press {toggle} once to start and again to stop."
        )
    if hold:
        return (
            f"1. Hold {hold} to start recording.\n"
            "2. Speak while holding the keys.\n"
            "3. Release the keys to stop and transcribe."
        )
    if toggle:
        return (
            f"1. Press {toggle} to start recording.\n"
            "2. Speak clearly.\n"
            f"3. Press {toggle} again to stop and transcribe."
        )
    return (
        "1. Configure a Toggle or Hold hotkey in the Hotkeys tab.\n"
        "2. Add the required provider API key if you use a cloud mode.\n"
        "3. Return here to verify the setup status."
    )


def create_card(
    title: str | None = None, description: str | None = None
) -> tuple[QFrame, QVBoxLayout]:
    """Erstellt eine Card mit optionalem Titel und Beschreibung."""
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
    layout.setSpacing(8)

    if title:
        title_label = QLabel(title)
        title_label.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        layout.addWidget(title_label)

    if description:
        desc_label = QLabel(description)
        desc_label.setFont(QFont("Segoe UI", 10))
        desc_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

    return card, layout


def create_label_row(
    label_text: str, widget: QWidget, hint: str | None = None
) -> QHBoxLayout:
    """Erstellt eine Zeile mit Label und Widget."""
    row = QHBoxLayout()
    row.setSpacing(12)

    label = QLabel(label_text)
    label.setFont(QFont("Segoe UI", 10))
    label.setMinimumWidth(120)
    row.addWidget(label)

    row.addWidget(widget, 1)

    if hint:
        hint_label = QLabel(hint)
        hint_label.setFont(QFont("Segoe UI", 9))
        hint_label.setStyleSheet(f"color: {COLORS['text_hint']};")
        row.addWidget(hint_label)

    return row


def create_status_label(text: str = "", color: str = "text") -> QLabel:
    """Erstellt ein Status-Label."""
    label = QLabel(text)
    label.setFont(QFont("Segoe UI", 10))
    label.setStyleSheet(f"color: {COLORS.get(color, color)};")
    return label


# =============================================================================
# Settings Window
# =============================================================================


class SettingsWindow(QDialog):
    """Settings Window für PulseScribe (Windows)."""

    # Signals
    settings_changed = Signal()
    closed = Signal()
    _hotkey_field_update = Signal(str, str)  # kind, value (thread-safe)

    def __init__(self, parent: QWidget | None = None, config: dict | None = None):
        super().__init__(parent)
        self.config = config or {}
        self._on_settings_changed_callback: Callable[[], None] | None = None

        # UI-Referenzen
        self._mode_combo: QComboBox | None = None
        self._lang_combo: QComboBox | None = None
        self._local_backend_combo: QComboBox | None = None
        self._local_model_combo: QComboBox | None = None
        self._streaming_checkbox: QCheckBox | None = None
        self._refine_checkbox: QCheckBox | None = None
        self._refine_provider_combo: QComboBox | None = None
        self._refine_model_field: QLineEdit | None = None
        self._overlay_checkbox: QCheckBox | None = None
        self._rtf_checkbox: QCheckBox | None = None
        self._clipboard_restore_checkbox: QCheckBox | None = None
        self._api_fields: dict[str, QLineEdit] = {}
        self._api_status: dict[str, QLabel] = {}
        self._last_logs_text: str | None = None
        self._last_logs_signature: tuple[int, int] | None = None
        self._last_transcripts_text: str | None = None
        self._last_transcripts_signature: tuple[int, int] | None = None
        self._setup_status_label: QLabel | None = None
        self._setup_status_detail_label: QLabel | None = None
        self._setup_howto_label: QLabel | None = None

        # Hotkey Recording State
        self._recording_hotkey_for: str | None = None
        self._hotkey_recording_previous: dict[str, str] = {"toggle": "", "hold": ""}
        self._pynput_listener = None  # pynput Keyboard Listener
        self._pressed_keys: set = set()  # Aktuell gedrückte Tasten
        self._pressed_keys_lock = threading.Lock()  # Thread-safe Zugriff
        self._using_qt_grab: bool = False  # Fallback wenn pynput nicht verfügbar
        self._is_closed: bool = False  # Verhindert Signal-Emission nach Close

        # Signal für Thread-safe UI-Updates verbinden
        self._hotkey_field_update.connect(self._set_hotkey_field_text)

        # Prompt Cache für Save & Apply
        self._prompts_cache: dict[str, str] = {}
        self._current_prompt_context: str = "default"

        self._setup_window()
        self._build_ui()
        self._load_settings()

    def _setup_window(self):
        """Konfiguriert das Fenster."""
        self.setWindowTitle("PulseScribe Settings")
        self.resize(SETTINGS_WIDTH, SETTINGS_HEIGHT)
        self.setMinimumSize(SETTINGS_WIDTH, SETTINGS_HEIGHT)
        self.setStyleSheet(get_settings_stylesheet())

        # Window Flags
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)

    def _build_ui(self):
        """Erstellt das UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = self._build_header()
        layout.addWidget(header)

        # Tab Widget
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs, 1)

        # Tabs hinzufügen
        self._tabs.addTab(self._build_setup_tab(), "Setup")
        self._tabs.addTab(self._build_hotkeys_tab(), "Hotkeys")
        self._tabs.addTab(self._build_providers_tab(), "Providers")
        self._tabs.addTab(self._build_advanced_tab(), "Advanced")
        self._tabs.addTab(self._build_refine_tab(), "Refine")
        self._tabs.addTab(self._build_prompts_tab(), "Prompts")
        self._tabs.addTab(self._build_vocabulary_tab(), "Vocabulary")
        self._tabs.addTab(self._build_logs_tab(), "Logs")
        self._tabs.addTab(self._build_about_tab(), "About")

        # Tab-Wechsel Handler für Auto-Load
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # Footer
        footer = self._build_footer()
        layout.addWidget(footer)

    def _build_header(self) -> QWidget:
        """Erstellt den Header."""
        header = QWidget()
        # Dynamic height to accommodate scaling and long text
        layout = QVBoxLayout(header)
        layout.setContentsMargins(20, 20, 20, 10)

        # Titel
        title = QLabel("🎤 PulseScribe")
        title.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Untertitel
        subtitle = QLabel("Voice-to-text for Windows")
        subtitle.setFont(QFont("Segoe UI", 11))
        subtitle.setStyleSheet(f"color: {COLORS['text_secondary']};")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        return header

    def _build_footer(self) -> QWidget:
        """Erstellt den Footer mit Checkbox links, Buttons rechts (wie macOS)."""
        footer = QWidget()
        footer.setMinimumHeight(60)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(20, 10, 20, 20)

        # Checkbox links (wie macOS Welcome Window)
        self._show_at_startup_checkbox = QCheckBox("Show at startup")
        self._show_at_startup_checkbox.setChecked(get_show_welcome_on_startup())
        self._show_at_startup_checkbox.stateChanged.connect(
            self._on_show_at_startup_changed
        )
        layout.addWidget(self._show_at_startup_checkbox)

        layout.addStretch()

        self._save_btn = QPushButton("Save && Apply")
        self._save_btn.setObjectName("primary")
        self._save_btn.clicked.connect(self._save_settings)
        layout.addWidget(self._save_btn)

        # Close-Button (rechts vom Save-Button, wie macOS)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)  # QDialog-konform
        layout.addWidget(self._close_btn)

        return footer

    # =========================================================================
    # Tab Builders
    # =========================================================================

    def _build_setup_tab(self) -> QWidget:
        """Setup-Tab: Übersicht und Quick-Start."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # Status Card
        card, card_layout = create_card("Status")

        self._setup_status_label = QLabel("Checking current setup…")
        self._setup_status_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        card_layout.addWidget(self._setup_status_label)

        self._setup_status_detail_label = QLabel("")
        self._setup_status_detail_label.setFont(QFont("Segoe UI", 10))
        self._setup_status_detail_label.setStyleSheet(
            f"color: {COLORS['text_secondary']};"
        )
        self._setup_status_detail_label.setWordWrap(True)
        card_layout.addWidget(self._setup_status_detail_label)

        layout.addWidget(card)

        # How-To Card
        card, card_layout = create_card(
            "📖 How to Use",
            "Guide based on your current mode and hotkeys.",
        )

        self._setup_howto_label = QLabel(
            "1. Configure your API keys in the Providers tab.\n"
            "2. Set up your preferred hotkey in the Hotkeys tab."
        )
        self._setup_howto_label.setFont(QFont("Segoe UI", 10))
        self._setup_howto_label.setWordWrap(True)
        card_layout.addWidget(self._setup_howto_label)

        layout.addWidget(card)

        # Local Mode Presets Card
        card, card_layout = create_card(
            "⚡ Local Mode Presets",
            "Quick-apply optimized settings for local transcription.",
        )

        presets_layout = QHBoxLayout()
        presets_layout.setSpacing(8)

        # Windows-optimized presets
        preset_btn_cuda = QPushButton("CUDA Fast")
        preset_btn_cuda.setToolTip("faster-whisper with CUDA (requires NVIDIA GPU)")
        preset_btn_cuda.clicked.connect(lambda: self._apply_local_preset("cuda_fast"))
        presets_layout.addWidget(preset_btn_cuda)

        preset_btn_cpu = QPushButton("CPU Fast")
        preset_btn_cpu.setToolTip("faster-whisper with CPU int8 optimization")
        preset_btn_cpu.clicked.connect(lambda: self._apply_local_preset("cpu_fast"))
        presets_layout.addWidget(preset_btn_cpu)

        preset_btn_quality = QPushButton("CPU Quality")
        preset_btn_quality.setToolTip("Higher quality transcription (slower)")
        preset_btn_quality.clicked.connect(
            lambda: self._apply_local_preset("cpu_quality")
        )
        presets_layout.addWidget(preset_btn_quality)

        presets_layout.addStretch()
        card_layout.addLayout(presets_layout)

        # Status Label
        self._preset_status = QLabel("")
        self._preset_status.setFont(QFont("Segoe UI", 9))
        card_layout.addWidget(self._preset_status)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_hotkeys_tab(self) -> QWidget:
        """Hotkeys-Tab: Hotkey-Konfiguration."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # Hotkey Card
        card, card_layout = create_card(
            "⌨️ Hotkeys", "Configure keyboard shortcuts for recording."
        )

        # Toggle Hotkey Row
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)
        toggle_label = QLabel("Toggle Hotkey:")
        toggle_label.setMinimumWidth(120)
        toggle_row.addWidget(toggle_label)

        self._toggle_hotkey_field = QLineEdit()
        self._toggle_hotkey_field.setPlaceholderText("e.g., ctrl+alt+r")
        self._toggle_hotkey_field.setReadOnly(True)
        toggle_row.addWidget(self._toggle_hotkey_field, 1)

        self._toggle_record_btn = QPushButton("Record")
        self._toggle_record_btn.setFixedWidth(80)
        self._toggle_record_btn.clicked.connect(
            lambda: self._start_hotkey_recording("toggle")
        )
        toggle_row.addWidget(self._toggle_record_btn)

        self._toggle_clear_btn = QPushButton("Clear")
        self._toggle_clear_btn.setFixedWidth(70)
        self._toggle_clear_btn.clicked.connect(
            lambda: self._clear_hotkey_field("toggle")
        )
        toggle_row.addWidget(self._toggle_clear_btn)

        card_layout.addLayout(toggle_row)

        # Hold Hotkey Row
        hold_row = QHBoxLayout()
        hold_row.setSpacing(8)
        hold_label = QLabel("Hold Hotkey:")
        hold_label.setMinimumWidth(120)
        hold_row.addWidget(hold_label)

        self._hold_hotkey_field = QLineEdit()
        self._hold_hotkey_field.setPlaceholderText("e.g., ctrl+alt+space")
        self._hold_hotkey_field.setReadOnly(True)
        hold_row.addWidget(self._hold_hotkey_field, 1)

        self._hold_record_btn = QPushButton("Record")
        self._hold_record_btn.setFixedWidth(80)
        self._hold_record_btn.clicked.connect(
            lambda: self._start_hotkey_recording("hold")
        )
        hold_row.addWidget(self._hold_record_btn)

        self._hold_clear_btn = QPushButton("Clear")
        self._hold_clear_btn.setFixedWidth(70)
        self._hold_clear_btn.clicked.connect(lambda: self._clear_hotkey_field("hold"))
        hold_row.addWidget(self._hold_clear_btn)

        card_layout.addLayout(hold_row)

        # Status Label für Recording
        self._hotkey_status = QLabel("")
        self._hotkey_status.setFont(QFont("Segoe UI", 9))
        card_layout.addWidget(self._hotkey_status)

        # Presets
        presets_label = QLabel("Presets:")
        presets_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        card_layout.addWidget(presets_label)

        presets_layout = QHBoxLayout()
        presets_layout.setSpacing(8)

        for preset_name, toggle_val, hold_val in [
            ("F19 Toggle", "f19", ""),
            ("Ctrl+Alt+R / Ctrl+Alt+Space", "ctrl+alt+r", "ctrl+alt+space"),
            ("F13 Toggle", "f13", ""),
        ]:
            btn = QPushButton(preset_name)
            btn.clicked.connect(
                lambda checked,
                t=toggle_val,
                h=hold_val: self._apply_hotkey_preset_pair(t, h)
            )
            presets_layout.addWidget(btn)

        presets_layout.addStretch()
        card_layout.addLayout(presets_layout)

        # Hint
        hint = QLabel(
            "💡 Hold hotkey: Push-to-talk mode. Toggle hotkey: Press to start/stop.\n"
            "Hold may use modifier-only combos (e.g. ctrl+win).\n"
            "Click 'Record' and press your desired key combination.\n"
            "Toggle/Hold must not overlap (e.g. ctrl+win and ctrl+win+r)."
        )
        hint.setFont(QFont("Segoe UI", 9))
        hint.setStyleSheet(f"color: {COLORS['text_hint']};")
        hint.setWordWrap(True)
        card_layout.addWidget(hint)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)

        return scroll

    def _build_providers_tab(self) -> QWidget:
        """Providers-Tab: Mode und API-Keys."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # Settings Card
        card, card_layout = create_card(
            "⚙️ Transcription Settings",
            "Configure the transcription provider and language.",
        )

        # Mode
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(MODE_OPTIONS)
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        card_layout.addLayout(create_label_row("Mode:", self._mode_combo))

        # Language
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(LANGUAGE_OPTIONS)
        card_layout.addLayout(create_label_row("Language:", self._lang_combo))

        # Local Backend Container (nur für local mode)
        self._local_backend_container = QWidget()
        backend_layout = QHBoxLayout(self._local_backend_container)
        backend_layout.setContentsMargins(0, 0, 0, 0)
        self._local_backend_combo = QComboBox()
        self._local_backend_combo.addItems(LOCAL_BACKEND_OPTIONS)
        backend_label = QLabel("Local Backend:")
        backend_label.setMinimumWidth(120)
        backend_layout.addWidget(backend_label)
        backend_layout.addWidget(self._local_backend_combo, 1)
        card_layout.addWidget(self._local_backend_container)

        # Local Model Container (nur für local mode)
        self._local_model_container = QWidget()
        model_layout = QHBoxLayout(self._local_model_container)
        model_layout.setContentsMargins(0, 0, 0, 0)
        self._local_model_combo = QComboBox()
        self._local_model_combo.addItems(LOCAL_MODEL_OPTIONS)
        model_label = QLabel("Local Model:")
        model_label.setMinimumWidth(120)
        model_layout.addWidget(model_label)
        model_layout.addWidget(self._local_model_combo, 1)
        card_layout.addWidget(self._local_model_container)

        # Streaming Container (nur für deepgram)
        self._streaming_container = QWidget()
        streaming_layout = QHBoxLayout(self._streaming_container)
        streaming_layout.setContentsMargins(0, 0, 0, 0)
        self._streaming_checkbox = QCheckBox("Enable WebSocket Streaming")
        streaming_layout.addWidget(self._streaming_checkbox)
        streaming_layout.addStretch()
        card_layout.addWidget(self._streaming_container)

        layout.addWidget(card)

        # API Keys Card
        card, card_layout = create_card(
            "🔑 API Keys", "Enter your API keys for cloud providers."
        )

        for provider, env_key in [
            ("Deepgram", "DEEPGRAM_API_KEY"),
            ("OpenAI", "OPENAI_API_KEY"),
            ("Groq", "GROQ_API_KEY"),
            ("OpenRouter", "OPENROUTER_API_KEY"),
            ("Gemini", "GEMINI_API_KEY"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(8)

            label = QLabel(f"{provider}:")
            label.setMinimumWidth(100)
            row.addWidget(label)

            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText(f"Enter {provider} API key...")
            field.textChanged.connect(self._refresh_setup_overview)
            self._api_fields[env_key] = field
            row.addWidget(field, 1)

            status = create_status_label()
            self._api_status[env_key] = status
            row.addWidget(status)

            card_layout.addLayout(row)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_advanced_tab(self) -> QWidget:
        """Advanced-Tab: Lokale Modell-Parameter."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # Local Settings Card
        card, card_layout = create_card(
            "🔧 Local Model Settings", "Advanced settings for local Whisper models."
        )

        # Device
        self._device_combo = QComboBox()
        self._device_combo.addItems(DEVICE_OPTIONS)
        card_layout.addLayout(create_label_row("Device:", self._device_combo))

        # Beam Size (Integer 1-10)
        self._beam_size_field = QLineEdit()
        self._beam_size_field.setPlaceholderText("5")
        self._beam_size_field.setValidator(QIntValidator(1, 20))
        card_layout.addLayout(
            create_label_row("Beam Size:", self._beam_size_field, "1-20")
        )

        # Temperature (Float 0.0-1.0)
        self._temperature_field = QLineEdit()
        self._temperature_field.setPlaceholderText("0.0")
        self._temperature_field.setValidator(QDoubleValidator(0.0, 1.0, 2))
        card_layout.addLayout(
            create_label_row("Temperature:", self._temperature_field, "0.0-1.0")
        )

        # Best Of (Integer)
        self._best_of_field = QLineEdit()
        self._best_of_field.setPlaceholderText("default")
        self._best_of_field.setValidator(QIntValidator(1, 10))
        card_layout.addLayout(create_label_row("Best Of:", self._best_of_field, "1-10"))

        self._advanced_local_settings_card = card
        layout.addWidget(card)

        # Faster-Whisper Card
        card, card_layout = create_card(
            "🚀 Faster-Whisper Settings", "Settings for faster-whisper backend."
        )

        # Compute Type
        self._compute_type_combo = QComboBox()
        self._compute_type_combo.addItems(
            ["default", "float16", "float32", "int8", "int8_float16"]
        )
        card_layout.addLayout(
            create_label_row("Compute Type:", self._compute_type_combo)
        )

        # CPU Threads
        cpu_threads_max = get_cpu_threads_limit(os.cpu_count())
        self._cpu_threads_field = QLineEdit()
        self._cpu_threads_field.setPlaceholderText("0 = auto")
        self._cpu_threads_field.setValidator(QIntValidator(0, cpu_threads_max))
        card_layout.addLayout(
            create_label_row(
                "CPU Threads:",
                self._cpu_threads_field,
                f"0-{cpu_threads_max} (0=auto)",
            )
        )

        # Num Workers
        self._num_workers_field = QLineEdit()
        self._num_workers_field.setPlaceholderText("1")
        self._num_workers_field.setValidator(QIntValidator(1, 8))
        card_layout.addLayout(
            create_label_row("Num Workers:", self._num_workers_field, "1-8")
        )

        # Boolean Overrides
        self._without_timestamps_combo = QComboBox()
        self._without_timestamps_combo.addItems(BOOL_OVERRIDE_OPTIONS)
        card_layout.addLayout(
            create_label_row("Without Timestamps:", self._without_timestamps_combo)
        )

        self._vad_filter_combo = QComboBox()
        self._vad_filter_combo.addItems(BOOL_OVERRIDE_OPTIONS)
        card_layout.addLayout(create_label_row("VAD Filter:", self._vad_filter_combo))

        self._fp16_combo = QComboBox()
        self._fp16_combo.addItems(BOOL_OVERRIDE_OPTIONS)
        card_layout.addLayout(create_label_row("FP16:", self._fp16_combo))

        self._advanced_faster_settings_card = card
        layout.addWidget(card)

        # Lightning Card
        card, card_layout = create_card(
            "⚡ Lightning Settings", "Settings for Lightning Whisper backend."
        )

        # Batch Size
        batch_layout = QHBoxLayout()
        batch_layout.setSpacing(12)

        batch_label = QLabel("Batch Size:")
        batch_label.setMinimumWidth(120)
        batch_layout.addWidget(batch_label)

        self._lightning_batch_slider = QSlider(Qt.Orientation.Horizontal)
        self._lightning_batch_slider.setRange(4, 32)
        self._lightning_batch_slider.setValue(12)
        self._lightning_batch_slider.valueChanged.connect(self._on_batch_size_changed)
        batch_layout.addWidget(self._lightning_batch_slider, 1)

        self._lightning_batch_value = QLabel("12")
        self._lightning_batch_value.setMinimumWidth(30)
        batch_layout.addWidget(self._lightning_batch_value)

        card_layout.addLayout(batch_layout)

        # Quantization
        self._lightning_quant_combo = QComboBox()
        self._lightning_quant_combo.addItems(LIGHTNING_QUANT_OPTIONS)
        card_layout.addLayout(
            create_label_row("Quantization:", self._lightning_quant_combo)
        )

        self._advanced_lightning_settings_card = card
        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_refine_tab(self) -> QWidget:
        """Refine-Tab: LLM-Nachbearbeitung."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # Refine Card
        card, card_layout = create_card(
            "✨ LLM Refinement",
            "Post-process transcriptions with AI for better formatting.",
        )

        self._refine_checkbox = QCheckBox("Enable LLM Refinement")
        card_layout.addWidget(self._refine_checkbox)

        # Provider
        self._refine_provider_combo = QComboBox()
        self._refine_provider_combo.addItems(REFINE_PROVIDER_OPTIONS)
        card_layout.addLayout(
            create_label_row("Provider:", self._refine_provider_combo)
        )

        # Model
        self._refine_model_field = QLineEdit()
        self._refine_model_field.setPlaceholderText("e.g., openai/gpt-4o")
        card_layout.addLayout(create_label_row("Model:", self._refine_model_field))

        layout.addWidget(card)

        # Display Card
        card, card_layout = create_card(
            "🖥️ Display Settings", "Configure visual feedback during transcription."
        )

        self._overlay_checkbox = QCheckBox("Show Overlay during recording")
        card_layout.addWidget(self._overlay_checkbox)

        self._rtf_checkbox = QCheckBox(
            "Show RTF (Real-Time Factor) after transcription"
        )
        card_layout.addWidget(self._rtf_checkbox)

        self._clipboard_restore_checkbox = QCheckBox("Restore clipboard after paste")
        card_layout.addWidget(self._clipboard_restore_checkbox)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_prompts_tab(self) -> QWidget:
        """Prompts-Tab: Custom Prompts."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # Prompts Card
        card, card_layout = create_card(
            "📝 Custom Prompts", "Customize prompts for different contexts."
        )

        # Context Selector
        self._prompt_context_combo = QComboBox()
        self._prompt_context_combo.addItems(
            ["default", "email", "chat", "code", "voice_commands", "app_mappings"]
        )
        self._prompt_context_combo.currentTextChanged.connect(
            self._on_prompt_context_changed
        )
        card_layout.addLayout(create_label_row("Context:", self._prompt_context_combo))

        # Prompt Editor
        self._prompt_editor = QPlainTextEdit()
        self._prompt_editor.setPlaceholderText("Custom prompt for this context...")
        self._prompt_editor.setMinimumHeight(200)
        card_layout.addWidget(self._prompt_editor)

        # Status Label
        self._prompt_status = QLabel("")
        self._prompt_status.setFont(QFont("Segoe UI", 9))
        card_layout.addWidget(self._prompt_status)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        reset_btn = QPushButton("Reset to Default")
        reset_btn.clicked.connect(self._reset_prompt_to_default)
        btn_layout.addWidget(reset_btn)

        save_prompt_btn = QPushButton("Save Prompt")
        save_prompt_btn.setObjectName("primary")
        save_prompt_btn.clicked.connect(self._save_current_prompt)
        btn_layout.addWidget(save_prompt_btn)

        card_layout.addLayout(btn_layout)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)

        # Initial load
        self._load_prompt_for_context("default")

        return scroll

    def _build_vocabulary_tab(self) -> QWidget:
        """Vocabulary-Tab: Custom Vocabulary."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # Vocabulary Card
        card, card_layout = create_card(
            "📚 Custom Vocabulary",
            "Add custom words and phrases to improve transcription accuracy.",
        )

        self._vocab_editor = QPlainTextEdit()
        self._vocab_editor.setPlaceholderText("One word/phrase per line...")
        self._vocab_editor.setMinimumHeight(250)
        card_layout.addWidget(self._vocab_editor)

        # Status Label
        self._vocab_status = QLabel("")
        self._vocab_status.setFont(QFont("Segoe UI", 9))
        card_layout.addWidget(self._vocab_status)

        # Hint
        vocab_hint = QLabel(
            "💡 Deepgram supports max 100 keywords, Local Whisper max 50."
        )
        vocab_hint.setFont(QFont("Segoe UI", 9))
        vocab_hint.setStyleSheet(f"color: {COLORS['text_hint']};")
        vocab_hint.setWordWrap(True)
        card_layout.addWidget(vocab_hint)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._load_vocabulary)
        btn_layout.addWidget(load_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save_vocabulary)
        btn_layout.addWidget(save_btn)

        card_layout.addLayout(btn_layout)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_logs_tab(self) -> QWidget:
        """Logs-Tab: Log-Viewer mit Transcripts."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QStackedWidget, QButtonGroup

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # Segment Control (Logs | Transcripts)
        segment_layout = QHBoxLayout()
        segment_layout.setSpacing(0)

        self._logs_btn = QPushButton("🪵 Logs")
        self._logs_btn.setCheckable(True)
        self._logs_btn.setChecked(True)
        self._logs_btn.setStyleSheet(
            f"""
            QPushButton {{ border-radius: 6px 0 0 6px; }}
            QPushButton:checked {{ background-color: {COLORS["accent"]}; }}
        """
        )
        self._logs_btn.clicked.connect(lambda: self._switch_logs_view(0))

        self._transcripts_btn = QPushButton("📝 Transcripts")
        self._transcripts_btn.setCheckable(True)
        self._transcripts_btn.setStyleSheet(
            f"""
            QPushButton {{ border-radius: 0 6px 6px 0; }}
            QPushButton:checked {{ background-color: {COLORS["accent"]}; }}
        """
        )
        self._transcripts_btn.clicked.connect(lambda: self._switch_logs_view(1))

        # Button Group für exklusive Auswahl
        self._logs_btn_group = QButtonGroup()
        self._logs_btn_group.addButton(self._logs_btn, 0)
        self._logs_btn_group.addButton(self._transcripts_btn, 1)

        segment_layout.addWidget(self._logs_btn)
        segment_layout.addWidget(self._transcripts_btn)
        segment_layout.addStretch()
        layout.addLayout(segment_layout)

        # Stacked Widget für Logs/Transcripts
        self._logs_stack = QStackedWidget()

        # === Logs Page ===
        logs_page = QWidget()
        logs_layout = QVBoxLayout(logs_page)
        logs_layout.setContentsMargins(0, 8, 0, 0)

        self._logs_viewer = QPlainTextEdit()
        self._logs_viewer.setReadOnly(True)
        self._logs_viewer.setMinimumHeight(300)
        self._logs_viewer.setPlaceholderText("Logs will appear here...")
        logs_layout.addWidget(self._logs_viewer)

        # Logs Buttons
        logs_btn_layout = QHBoxLayout()
        self._auto_refresh_checkbox = QCheckBox("Auto-refresh")
        self._auto_refresh_checkbox.stateChanged.connect(self._toggle_logs_auto_refresh)
        logs_btn_layout.addWidget(self._auto_refresh_checkbox)
        logs_btn_layout.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_logs)
        logs_btn_layout.addWidget(refresh_btn)

        open_btn = QPushButton("Open in Explorer")
        open_btn.clicked.connect(self._open_logs_folder)
        logs_btn_layout.addWidget(open_btn)

        logs_layout.addLayout(logs_btn_layout)
        self._logs_stack.addWidget(logs_page)

        # === Transcripts Page ===
        transcripts_page = QWidget()
        transcripts_layout = QVBoxLayout(transcripts_page)
        transcripts_layout.setContentsMargins(0, 8, 0, 0)

        self._transcripts_viewer = QPlainTextEdit()
        self._transcripts_viewer.setReadOnly(True)
        self._transcripts_viewer.setMinimumHeight(300)
        self._transcripts_viewer.setPlaceholderText(
            "Transcripts history will appear here..."
        )
        transcripts_layout.addWidget(self._transcripts_viewer)

        # Transcripts Status
        self._transcripts_status = QLabel("")
        self._transcripts_status.setFont(QFont("Segoe UI", 9))
        self._transcripts_status.setStyleSheet(f"color: {COLORS['text_secondary']};")
        transcripts_layout.addWidget(self._transcripts_status)

        # Transcripts Buttons
        transcripts_btn_layout = QHBoxLayout()
        transcripts_btn_layout.addStretch()

        refresh_t_btn = QPushButton("Refresh")
        refresh_t_btn.clicked.connect(self._refresh_transcripts)
        transcripts_btn_layout.addWidget(refresh_t_btn)

        clear_t_btn = QPushButton("Clear History")
        clear_t_btn.clicked.connect(self._clear_transcripts)
        transcripts_btn_layout.addWidget(clear_t_btn)

        transcripts_layout.addLayout(transcripts_btn_layout)
        self._logs_stack.addWidget(transcripts_page)

        layout.addWidget(self._logs_stack)

        scroll.setWidget(content)

        # Auto-Refresh Timer
        self._logs_refresh_timer = QTimer(self)
        self._logs_refresh_timer.timeout.connect(self._refresh_active_logs_view)

        return scroll

    def _build_about_tab(self) -> QWidget:
        """About-Tab: Version und Credits."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # About Card
        card, card_layout = create_card()

        # Logo/Title
        title = QLabel("🎤 PulseScribe")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title)

        # Version (dynamisch laden)
        version_str = self._get_version()
        version = QLabel(f"Version {version_str}")
        version.setFont(QFont("Segoe UI", 12))
        version.setStyleSheet(f"color: {COLORS['text_secondary']};")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(version)

        card_layout.addSpacing(20)

        # Description
        desc = QLabel(
            "Minimalistic voice-to-text for Windows.\nInspired by Wispr Flow."
        )
        desc.setFont(QFont("Segoe UI", 10))
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        card_layout.addWidget(desc)

        card_layout.addSpacing(20)

        # Links
        links = QLabel(
            '<a href="https://github.com/KLIEBHAN/pulsescribe" style="color: #007AFF;">GitHub</a> · '
            '<a href="https://github.com/KLIEBHAN/pulsescribe/tree/master/docs" style="color: #007AFF;">Documentation</a>'
        )
        links.setFont(QFont("Segoe UI", 10))
        links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        links.setOpenExternalLinks(True)
        card_layout.addWidget(links)

        layout.addWidget(card)
        layout.addStretch()

        return content

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _on_tab_changed(self, index: int):
        """Handler für Tab-Wechsel."""
        tab_name = self._tabs.tabText(index) if self._tabs else ""

        # Vocabulary automatisch laden
        if tab_name == "Vocabulary":
            self._load_vocabulary()

        # Logs automatisch laden
        if tab_name == "Logs":
            if (
                hasattr(self, "_logs_stack")
                and self._logs_stack
                and self._logs_stack.currentIndex() == 1
            ):
                self._refresh_transcripts()
            else:
                self._refresh_logs()

        self._update_logs_auto_refresh_state()

    def _on_mode_changed(self, mode: str):
        """Handler für Mode-Änderung."""
        is_local = mode == "local"
        is_deepgram = mode == "deepgram"

        # Local-spezifische Container ein-/ausblenden
        if hasattr(self, "_local_backend_container"):
            self._local_backend_container.setVisible(is_local)
        if hasattr(self, "_local_model_container"):
            self._local_model_container.setVisible(is_local)
        if hasattr(self, "_streaming_container"):
            self._streaming_container.setVisible(is_deepgram)
        if hasattr(self, "_advanced_local_settings_card"):
            self._advanced_local_settings_card.setVisible(is_local)
        if hasattr(self, "_advanced_faster_settings_card"):
            self._advanced_faster_settings_card.setVisible(is_local)
        if hasattr(self, "_advanced_lightning_settings_card"):
            self._advanced_lightning_settings_card.setVisible(is_local)

        self._refresh_setup_overview()

    def _refresh_setup_overview(self) -> None:
        """Aktualisiert Status- und How-to-Text im Setup-Tab."""
        status_label = getattr(self, "_setup_status_label", None)
        status_detail_label = getattr(self, "_setup_status_detail_label", None)
        howto_label = getattr(self, "_setup_howto_label", None)
        if not (status_label and status_detail_label and howto_label):
            return

        mode_combo = getattr(self, "_mode_combo", None)
        mode = mode_combo.currentText() if mode_combo else "deepgram"
        toggle = (
            self._toggle_hotkey_field.text()
            if hasattr(self, "_toggle_hotkey_field") and self._toggle_hotkey_field
            else ""
        )
        hold = (
            self._hold_hotkey_field.text()
            if hasattr(self, "_hold_hotkey_field") and self._hold_hotkey_field
            else ""
        )

        api_keys = {
            env_key: field.text().strip() if field else get_api_key(env_key) or ""
            for env_key, field in self._api_fields.items()
        }
        for env_key in MODE_API_KEY_MAP.values():
            api_keys.setdefault(env_key, get_api_key(env_key) or "")

        headline, detail, color_key = _build_setup_status(
            mode,
            toggle_hotkey=toggle,
            hold_hotkey=hold,
            api_keys=api_keys,
        )
        status_label.setText(headline)
        status_label.setStyleSheet(f"color: {COLORS[color_key]};")
        status_detail_label.setText(detail)
        howto_label.setText(_build_setup_how_to_text(toggle, hold))

    def _on_batch_size_changed(self, value: int):
        """Handler für Batch-Size Slider."""
        if self._lightning_batch_value:
            self._lightning_batch_value.setText(str(value))

    def _on_show_at_startup_changed(self, state: int):
        """Handler für Show at startup Checkbox."""
        set_show_welcome_on_startup(state == Qt.CheckState.Checked.value)

    def _apply_hotkey_preset(self, hotkey: str):
        """Wendet ein Hotkey-Preset an (nur Toggle)."""
        if self._recording_hotkey_for:
            self._stop_hotkey_recording(None)
        if self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText(hotkey)

    def _apply_hotkey_preset_pair(self, toggle: str, hold: str):
        """Wendet ein Hotkey-Preset-Paar an."""
        if self._recording_hotkey_for:
            self._stop_hotkey_recording(None)
        if self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText(toggle)
        if self._hold_hotkey_field:
            self._hold_hotkey_field.setText(hold)
        self._set_hotkey_status(
            f"Preset applied: Toggle={toggle or 'none'}, Hold={hold or 'none'}",
            "success",
        )
        self._refresh_setup_overview()

    def _start_hotkey_recording(self, kind: str):
        """Startet Hotkey-Recording für toggle oder hold."""
        if not hasattr(self, "_hotkey_recording_previous"):
            self._hotkey_recording_previous = {"toggle": "", "hold": ""}

        # Bei laufender Aufnahme vorherigen Feldwert wiederherstellen, damit
        # ein Wechsel zwischen Toggle/Hold nicht versehentlich den alten
        # Hotkey löscht.
        previous_kind = getattr(self, "_recording_hotkey_for", None)
        if previous_kind:
            previous_value = self._hotkey_recording_previous.get(previous_kind, "")
            if (
                previous_kind == "toggle"
                and hasattr(self, "_toggle_hotkey_field")
                and self._toggle_hotkey_field is not None
            ):
                self._toggle_hotkey_field.setText(previous_value)
            elif (
                previous_kind == "hold"
                and hasattr(self, "_hold_hotkey_field")
                and self._hold_hotkey_field is not None
            ):
                self._hold_hotkey_field.setText(previous_value)

        # Defensive cleanup: ensure previous low-level capture is fully stopped
        # before starting a new recording session.
        self._stop_pynput_listener()
        self._recording_hotkey_for = kind
        with self._pressed_keys_lock:
            self._pressed_keys.clear()

        active_field = None
        if kind == "toggle" and hasattr(self, "_toggle_hotkey_field"):
            active_field = self._toggle_hotkey_field
        elif kind == "hold" and hasattr(self, "_hold_hotkey_field"):
            active_field = self._hold_hotkey_field
        if active_field is not None:
            self._hotkey_recording_previous[kind] = active_field.text().strip()
            active_field.setText("")

        # Beide Buttons zunächst zurücksetzen (wichtig beim Wechsel zwischen
        # Toggle/Hold ohne vorherige Bestätigung).
        if hasattr(self, "_toggle_record_btn"):
            self._toggle_record_btn.setText("Record")
            self._toggle_record_btn.setStyleSheet("")
        if hasattr(self, "_hold_record_btn"):
            self._hold_record_btn.setText("Record")
            self._hold_record_btn.setStyleSheet("")

        # Button-Text ändern
        if kind == "toggle" and hasattr(self, "_toggle_record_btn"):
            self._toggle_record_btn.setText("Press key...")
            self._toggle_record_btn.setStyleSheet(
                f"background-color: {COLORS['accent']};"
            )
        elif kind == "hold" and hasattr(self, "_hold_record_btn"):
            self._hold_record_btn.setText("Press key...")
            self._hold_record_btn.setStyleSheet(
                f"background-color: {COLORS['accent']};"
            )

        self._set_hotkey_status(
            "Press your hotkey combination, then press Enter to confirm...", "warning"
        )

        # Low-level pynput Hook für Win-Taste (Qt kann sie nicht abfangen)
        self._start_pynput_listener()
        self.setFocus()

    def _start_pynput_listener(self):
        """Startet pynput Listener für Low-Level Key-Capture."""
        self._using_qt_grab = False
        available, _ = get_pynput_key_map()

        if not available:
            logger.warning("pynput nicht verfügbar, Fallback auf Qt grabKeyboard")
            self._using_qt_grab = True
            self.grabKeyboard()
            return

        try:
            from pynput import keyboard  # type: ignore[import-not-found]

            def on_press(key):
                if self._is_closed:
                    return
                key_str = self._pynput_key_to_string(key)
                if key_str and key_str not in ("enter", "return", "esc", "escape"):
                    with self._pressed_keys_lock:
                        self._pressed_keys.add(key_str)
                    self._update_hotkey_field_from_pressed_keys()

            def on_release(key):
                if self._is_closed:
                    return
                key_str = self._pynput_key_to_string(key)
                if key_str:
                    with self._pressed_keys_lock:
                        self._pressed_keys.discard(key_str)

            self._pynput_listener = keyboard.Listener(
                on_press=on_press, on_release=on_release
            )
            self._pynput_listener.start()
        except Exception as e:
            logger.warning(f"pynput Listener fehlgeschlagen: {e}, Fallback auf Qt")
            self._using_qt_grab = True
            self.grabKeyboard()

    def _stop_pynput_listener(self):
        """Stoppt pynput Listener oder gibt Qt Keyboard-Grab frei."""
        if self._pynput_listener:
            self._pynput_listener.stop()
            self._pynput_listener = None
        if self._using_qt_grab:
            self.releaseKeyboard()
            self._using_qt_grab = False
        with self._pressed_keys_lock:
            self._pressed_keys.clear()

    def _pynput_key_to_string(self, key) -> str:
        """Konvertiert pynput Key zu String (nutzt gecachten key_map)."""
        _, key_map = get_pynput_key_map()

        # Bekannte Tasten aus Cache
        if key in key_map:
            return key_map[key]

        # F-Tasten (f1-f24)
        if hasattr(key, "name") and key.name:
            name = key.name
            if name.startswith("f") and len(name) > 1 and name[1:].isdigit():
                return name.lower()

        # Normale Zeichen
        if hasattr(key, "char") and key.char:
            return key.char.lower()

        # Sonstige benannte Tasten
        if hasattr(key, "name") and key.name:
            return key.name.lower()

        return ""

    def _update_hotkey_field_from_pressed_keys(self):
        """Aktualisiert das Hotkey-Feld basierend auf gedrückten Tasten."""
        if self._is_closed or not self._recording_hotkey_for:
            return
        recording_kind = self._recording_hotkey_for

        # Thread-safe Kopie der gedrückten Tasten
        with self._pressed_keys_lock:
            if not self._pressed_keys:
                return
            pressed_copy = set(self._pressed_keys)

        # Sortiere: Modifier zuerst, dann andere Tasten
        modifiers = []
        keys = []
        for k in pressed_copy:
            if k in ("ctrl", "alt", "shift", "win"):
                modifiers.append(k)
            else:
                keys.append(k)

        # Stabile Reihenfolge für Modifier
        modifier_order = ["ctrl", "alt", "shift", "win"]
        sorted_modifiers = [m for m in modifier_order if m in modifiers]

        hotkey_str = "+".join(sorted_modifiers + sorted(keys))

        # UI-Update (Thread-safe via Signal, da pynput in eigenem Thread läuft)
        if not self._is_closed:
            self._hotkey_field_update.emit(recording_kind, hotkey_str)

    def _set_hotkey_field_text(self, kind: str, hotkey_str: str):
        """Setzt den Text im aktiven Hotkey-Feld (Thread-safe)."""
        if self._is_closed:
            return
        if self._recording_hotkey_for != kind:
            return
        if kind == "toggle" and self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText(hotkey_str)
        elif kind == "hold" and self._hold_hotkey_field:
            self._hold_hotkey_field.setText(hotkey_str)

    def _stop_hotkey_recording(self, hotkey_str: str | None = None):
        """Beendet Hotkey-Recording."""
        kind = self._recording_hotkey_for
        self._recording_hotkey_for = None
        previous_hotkey = (
            self._hotkey_recording_previous.get(kind, "") if kind else ""
        )

        # pynput Listener stoppen
        self._stop_pynput_listener()

        # Buttons zurücksetzen
        if hasattr(self, "_toggle_record_btn"):
            self._toggle_record_btn.setText("Record")
            self._toggle_record_btn.setStyleSheet("")
        if hasattr(self, "_hold_record_btn"):
            self._hold_record_btn.setText("Record")
            self._hold_record_btn.setStyleSheet("")

        target_field = None
        if kind == "toggle" and self._toggle_hotkey_field:
            target_field = self._toggle_hotkey_field
        elif kind == "hold" and self._hold_hotkey_field:
            target_field = self._hold_hotkey_field

        if hotkey_str and kind and target_field:
            # Hotkey in Feld setzen
            target_field.setText(hotkey_str)
            self._set_hotkey_status(f"✓ Recorded: {hotkey_str}", "success")
        elif hotkey_str == "" and kind:
            if target_field:
                target_field.setText(previous_hotkey)
            self._set_hotkey_status(
                "No key detected - previous hotkey kept.", "warning"
            )
        else:
            if target_field:
                target_field.setText(previous_hotkey)
            self._set_hotkey_status("Recording cancelled", "text_hint")

        self._refresh_setup_overview()

    def _clear_hotkey_field(self, kind: str) -> None:
        """Leert ein Hotkey-Feld, damit der Modus deaktiviert werden kann."""
        if self._recording_hotkey_for:
            self._stop_hotkey_recording(None)

        if kind == "toggle" and self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText("")
            self._set_hotkey_status(
                "Toggle hotkey cleared. Click Save & Apply to persist.", "text_hint"
            )
            self._refresh_setup_overview()
            return

        if kind == "hold" and self._hold_hotkey_field:
            self._hold_hotkey_field.setText("")
            self._set_hotkey_status(
                "Hold hotkey cleared. Click Save & Apply to persist.", "text_hint"
            )
            self._refresh_setup_overview()
            return

    def keyPressEvent(self, event):
        """Fängt Tastendruck für Hotkey-Recording ab."""
        if self._recording_hotkey_for:
            # Escape = Abbrechen
            if event.key() == Qt.Key.Key_Escape:
                self._stop_hotkey_recording(None)
                event.accept()
                return

            # Enter = Bestätigen
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # Aktuelles Feld auslesen
                if self._recording_hotkey_for == "toggle" and self._toggle_hotkey_field:
                    hotkey = self._toggle_hotkey_field.text()
                elif self._recording_hotkey_for == "hold" and self._hold_hotkey_field:
                    hotkey = self._hold_hotkey_field.text()
                else:
                    hotkey = None
                self._stop_hotkey_recording(hotkey)
                event.accept()
                return

            # Qt-Fallback: Hotkey aus Qt-Events bauen (wenn pynput nicht verfügbar)
            if self._using_qt_grab:
                is_auto_repeat = getattr(event, "isAutoRepeat", lambda: False)()
                if is_auto_repeat:
                    event.accept()
                    return

                parts = []
                modifiers = event.modifiers()
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    parts.append("ctrl")
                if modifiers & Qt.KeyboardModifier.AltModifier:
                    parts.append("alt")
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    parts.append("shift")
                if modifiers & Qt.KeyboardModifier.MetaModifier:
                    parts.append("win")

                key = event.key()
                key_name = self._qt_key_to_string(key)
                if key_name and key_name not in ("ctrl", "alt", "shift", "win", "meta"):
                    parts.append(key_name)

                hotkey_str = "+".join(parts) if parts else ""
                if self._recording_hotkey_for == "toggle" and self._toggle_hotkey_field:
                    self._toggle_hotkey_field.setText(hotkey_str)
                elif self._recording_hotkey_for == "hold" and self._hold_hotkey_field:
                    self._hold_hotkey_field.setText(hotkey_str)

            event.accept()
            return

        super().keyPressEvent(event)

    def _qt_key_to_string(self, key: int) -> str:
        """Konvertiert Qt Key zu String."""
        # Spezielle Tasten
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

        # F-Tasten
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
            return f"f{key - Qt.Key.Key_F1 + 1}"

        # Buchstaben
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(ord("a") + key - Qt.Key.Key_A)

        # Zahlen
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(ord("0") + key - Qt.Key.Key_0)

        return ""

    def _set_hotkey_status(self, text: str, color: str):
        """Setzt Hotkey-Status-Text."""
        if hasattr(self, "_hotkey_status") and self._hotkey_status:
            self._hotkey_status.setText(text)
            color_value = COLORS.get(color, COLORS["text"])
            self._hotkey_status.setStyleSheet(f"color: {color_value};")

    def _validate_hotkeys_for_save(self) -> tuple[str, str] | None:
        """Validiert Hotkeys vor dem Speichern und gibt normalisierte Werte zurück."""
        toggle_raw = (
            self._toggle_hotkey_field.text()
            if hasattr(self, "_toggle_hotkey_field") and self._toggle_hotkey_field
            else ""
        )
        hold_raw = (
            self._hold_hotkey_field.text()
            if hasattr(self, "_hold_hotkey_field") and self._hold_hotkey_field
            else ""
        )

        toggle, toggle_error = normalize_windows_hotkey(toggle_raw)
        if toggle_error:
            self._set_hotkey_status(f"Toggle hotkey invalid: {toggle_error}", "error")
            return None

        hold, hold_error = normalize_windows_hotkey(hold_raw)
        if hold_error:
            self._set_hotkey_status(f"Hold hotkey invalid: {hold_error}", "error")
            return None

        modifier_keys = {"ctrl", "alt", "shift", "win"}
        if toggle and all(part in modifier_keys for part in toggle.split("+")):
            self._set_hotkey_status(
                "Toggle hotkey must include at least one non-modifier key.",
                "error",
            )
            return None

        # Hold-to-talk erlaubt reine Modifier-Kombinationen (z. B. ctrl+win),
        # damit das Verhalten konsistent mit den Daemon-Defaults bleibt.
        if not toggle and not hold:
            self._set_hotkey_status(
                "Configure at least one hotkey (Toggle or Hold).",
                "error",
            )
            return None

        if toggle and hold and toggle == hold:
            self._set_hotkey_status(
                "Toggle and Hold must not use the same hotkey.", "error"
            )
            return None
        if toggle and hold and hotkeys_conflict(toggle, hold):
            self._set_hotkey_status(
                "Toggle and Hold must not overlap (e.g. ctrl+win vs ctrl+win+r).",
                "error",
            )
            return None

        if hasattr(self, "_toggle_hotkey_field") and self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText(toggle)
        if hasattr(self, "_hold_hotkey_field") and self._hold_hotkey_field:
            self._hold_hotkey_field.setText(hold)

        return toggle, hold

    # =========================================================================
    # Prompt Handlers
    # =========================================================================

    def _on_prompt_context_changed(self, context: str):
        """Lädt Prompt für gewählten Kontext."""
        # Aktuellen Prompt im Cache speichern
        if self._prompt_editor and self._current_prompt_context:
            self._prompts_cache[self._current_prompt_context] = (
                self._prompt_editor.toPlainText()
            )

        self._current_prompt_context = context
        self._load_prompt_for_context(context)

    def _load_prompt_for_context(self, context: str):
        """Lädt den Prompt-Text für einen Kontext."""
        # Unsaved Änderungen pro Kontext bevorzugen (verhindert Datenverlust
        # beim Tab-Wechsel und spart wiederholtes Disk-Laden).
        if context in self._prompts_cache:
            if self._prompt_editor:
                self._prompt_editor.setPlainText(self._prompts_cache[context])
                self._set_prompt_status("", "text")
            return

        try:
            from utils.custom_prompts import (
                load_custom_prompts,
                get_voice_commands,
                format_app_mappings,
                get_app_contexts,
            )

            if context == "voice_commands":
                text = get_voice_commands()
            elif context == "app_mappings":
                text = format_app_mappings(get_app_contexts())
            else:
                data = load_custom_prompts()
                prompts = data.get("prompts", {})
                text = prompts.get(context, {}).get("prompt", "")

            if self._prompt_editor:
                self._prompt_editor.setPlainText(text)
                self._prompts_cache[context] = text
                self._set_prompt_status("", "text")

        except Exception as e:
            logger.error(f"Prompt laden fehlgeschlagen: {e}")
            self._set_prompt_status(f"Error: {e}", "error")

    def _save_current_prompt(self):
        """Speichert den aktuellen Prompt."""
        try:
            from utils.custom_prompts import (
                filter_overrides_for_storage,
                load_custom_prompts,
                parse_app_mappings,
                save_custom_prompts,
            )

            context = (
                self._prompt_context_combo.currentText()
                if self._prompt_context_combo
                else "default"
            )
            text = self._prompt_editor.toPlainText() if self._prompt_editor else ""
            self._prompts_cache[context] = text

            # Aktuelle Daten laden
            data = load_custom_prompts()

            if context == "voice_commands":
                data["voice_commands"] = {"instruction": text}
            elif context == "app_mappings":
                data["app_contexts"] = parse_app_mappings(text)
            else:
                if "prompts" not in data:
                    data["prompts"] = {}
                data["prompts"][context] = {"prompt": text}

            save_custom_prompts(filter_overrides_for_storage(data))
            self._set_prompt_status("✓ Saved", "success")

        except Exception as e:
            logger.error(f"Prompt speichern fehlgeschlagen: {e}")
            self._set_prompt_status(f"Error: {e}", "error")

    def _reset_prompt_to_default(self):
        """Setzt aktuellen Prompt auf Default zurück."""
        try:
            from utils.custom_prompts import get_defaults, format_app_mappings

            context = (
                self._prompt_context_combo.currentText()
                if self._prompt_context_combo
                else "default"
            )
            defaults = get_defaults()

            if context == "voice_commands":
                text = defaults["voice_commands"]["instruction"]
            elif context == "app_mappings":
                text = format_app_mappings(defaults["app_contexts"])
            else:
                text = defaults["prompts"].get(context, {}).get("prompt", "")

            if self._prompt_editor:
                self._prompt_editor.setPlainText(text)
                self._prompts_cache[context] = text
                self._set_prompt_status("Reset to default (not saved)", "warning")

        except Exception as e:
            logger.error(f"Reset fehlgeschlagen: {e}")
            self._set_prompt_status(f"Error: {e}", "error")

    def _set_prompt_status(self, text: str, color: str):
        """Setzt Status-Text mit Farbe."""
        if self._prompt_status:
            self._prompt_status.setText(text)
            color_value = COLORS.get(color, COLORS["text"])
            self._prompt_status.setStyleSheet(f"color: {color_value};")

    def _save_all_prompts(self):
        """Speichert alle geänderten Prompts aus dem Cache."""
        try:
            # Aktuellen Editor-Inhalt zum Cache hinzufügen
            if self._prompt_editor and self._current_prompt_context:
                self._prompts_cache[self._current_prompt_context] = (
                    self._prompt_editor.toPlainText()
                )

            # Nichts zu speichern?
            if not self._prompts_cache:
                return

            from utils.custom_prompts import (
                filter_overrides_for_storage,
                load_custom_prompts,
                parse_app_mappings,
                save_custom_prompts,
            )

            # Aktuelle Daten laden
            data = load_custom_prompts()

            # Alle gecachten Prompts speichern
            for context, text in self._prompts_cache.items():
                if context == "voice_commands":
                    data["voice_commands"] = {"instruction": text}
                elif context == "app_mappings":
                    data["app_contexts"] = parse_app_mappings(text)
                else:
                    if "prompts" not in data:
                        data["prompts"] = {}
                    data["prompts"][context] = {"prompt": text}

            save_custom_prompts(filter_overrides_for_storage(data))
            logger.info(f"Prompts gespeichert: {list(self._prompts_cache.keys())}")

        except Exception as e:
            logger.error(f"Prompts speichern fehlgeschlagen: {e}")

    def _toggle_logs_auto_refresh(self, state: int):
        """Schaltet Auto-Refresh für Logs ein/aus."""
        del state
        self._update_logs_auto_refresh_state()

    def _switch_logs_view(self, index: int):
        """Wechselt zwischen Logs und Transcripts Ansicht."""
        if hasattr(self, "_logs_stack"):
            self._logs_stack.setCurrentIndex(index)
            if index == 1:  # Transcripts
                self._refresh_transcripts()
            else:
                self._refresh_logs()
        self._update_logs_auto_refresh_state()

    def _is_logs_tab_active(self) -> bool:
        """Prüft, ob der Logs-Tab aktuell sichtbar ist."""
        if not hasattr(self, "_tabs") or not self._tabs:
            return False
        current_index = self._tabs.currentIndex()
        if current_index < 0:
            return False
        return self._tabs.tabText(current_index) == "Logs"

    def _update_logs_auto_refresh_state(self) -> None:
        """Aktiviert Auto-Refresh nur wenn Logs tatsächlich sichtbar sind."""
        if not hasattr(self, "_logs_refresh_timer"):
            return

        logs_view_index = (
            self._logs_stack.currentIndex()
            if hasattr(self, "_logs_stack") and self._logs_stack
            else -1
        )
        enabled = bool(
            hasattr(self, "_auto_refresh_checkbox")
            and self._auto_refresh_checkbox
            and self._auto_refresh_checkbox.isChecked()
        )

        should_run = should_auto_refresh_logs(
            enabled=enabled,
            is_logs_tab_active=self._is_logs_tab_active(),
            logs_view_index=logs_view_index,
            is_window_visible=self._is_window_visible_for_logs(),
            allow_transcripts=True,
        )

        if should_run:
            if not self._logs_refresh_timer.isActive():
                self._logs_refresh_timer.start(2000)  # Alle 2 Sekunden
            return

        self._logs_refresh_timer.stop()

    def _is_window_visible_for_logs(self) -> bool:
        """True nur wenn Fenster sichtbar und nicht minimiert ist."""
        try:
            return self.isVisible() and (not self.isMinimized())
        except RuntimeError:
            # Kann während/kurz nach Teardown auftreten.
            return False

    def _refresh_transcripts(self):
        """Aktualisiert Transcripts-Anzeige."""
        try:
            from utils.history import (
                HISTORY_FILE,
                format_transcripts_for_display,
                get_recent_transcripts,
            )

            if not HISTORY_FILE.exists():
                self._last_transcripts_signature = None
                self._set_transcripts_text_if_changed("No transcripts yet.")
                if hasattr(self, "_transcripts_status"):
                    self._transcripts_status.setText("0 entries")
                    self._transcripts_status.setStyleSheet(
                        f"color: {COLORS['text_secondary']};"
                    )
                return

            signature = get_file_signature(HISTORY_FILE)
            if signature is not None and signature == self._last_transcripts_signature:
                return

            entries = get_recent_transcripts(50)  # Letzte 50 Einträge
            self._set_transcripts_text_if_changed(
                format_transcripts_for_display(entries)
            )
            self._last_transcripts_signature = signature

            if hasattr(self, "_transcripts_status"):
                self._transcripts_status.setText(f"{len(entries)} entries")
                self._transcripts_status.setStyleSheet(
                    f"color: {COLORS['text_secondary']};"
                )

        except Exception as e:
            logger.error(f"Transcripts laden fehlgeschlagen: {e}")
            if self._transcripts_viewer:
                self._transcripts_viewer.setPlainText(f"Error: {e}")

    def _refresh_active_logs_view(self) -> None:
        """Aktualisiert die aktuell sichtbare Ansicht im Logs-Tab."""
        current_index = (
            self._logs_stack.currentIndex()
            if hasattr(self, "_logs_stack") and self._logs_stack
            else 0
        )
        if current_index == 1:
            self._refresh_transcripts()
            return
        self._refresh_logs()

    def _set_transcripts_text_if_changed(self, text: str) -> None:
        """Aktualisiert den Transcript-Viewer nur bei Änderungen.

        Verhindert unnötige Re-Renders und erhält die Scroll-Position, wenn
        der Nutzer ältere Einträge betrachtet.
        """
        if not self._transcripts_viewer or text == self._last_transcripts_text:
            return

        scrollbar = self._transcripts_viewer.verticalScrollBar()
        previous_maximum = scrollbar.maximum()
        previous_value = scrollbar.value()
        was_at_bottom = is_near_bottom(previous_value, previous_maximum)

        self._transcripts_viewer.setPlainText(text)
        self._last_transcripts_text = text

        scrollbar = self._transcripts_viewer.verticalScrollBar()
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
            return

        scrollbar.setValue(clamp_scroll_value(previous_value, scrollbar.maximum()))

    def _clear_transcripts(self):
        """Löscht Transcripts-Historie."""
        try:
            from utils.history import clear_history

            clear_history()
            self._refresh_transcripts()
            if hasattr(self, "_transcripts_status"):
                self._transcripts_status.setText("History cleared")
                self._transcripts_status.setStyleSheet(f"color: {COLORS['success']};")

        except Exception as e:
            logger.error(f"Transcripts löschen fehlgeschlagen: {e}")

    def _get_version(self) -> str:
        """Gibt die aktuelle Version zurück."""
        return get_app_version(default="unknown")

    def _load_vocabulary(self):
        """Lädt Vocabulary aus Datei."""
        try:
            from utils.vocabulary import load_vocabulary, validate_vocabulary

            vocab = load_vocabulary()
            if self._vocab_editor:
                keywords = vocab.get("keywords", [])
                self._vocab_editor.setPlainText("\n".join(keywords))

                # Validierung und Warnungen
                warnings = validate_vocabulary()
                if warnings and hasattr(self, "_vocab_status"):
                    self._vocab_status.setText("⚠ " + "; ".join(warnings))
                    self._vocab_status.setStyleSheet(f"color: {COLORS['warning']};")
                elif hasattr(self, "_vocab_status"):
                    count = len(keywords)
                    self._vocab_status.setText(f"{count} keywords loaded")
                    self._vocab_status.setStyleSheet(
                        f"color: {COLORS['text_secondary']};"
                    )
        except Exception as e:
            logger.error(f"Vocabulary laden fehlgeschlagen: {e}")
            if hasattr(self, "_vocab_status"):
                self._vocab_status.setText(f"Error: {e}")
                self._vocab_status.setStyleSheet(f"color: {COLORS['error']};")

    def _save_vocabulary(self):
        """Speichert Vocabulary in Datei."""
        try:
            from utils.vocabulary import save_vocabulary, validate_vocabulary

            if self._vocab_editor:
                text = self._vocab_editor.toPlainText()
                keywords = [line.strip() for line in text.split("\n") if line.strip()]
                save_vocabulary(keywords)

                # Validierung nach Speichern
                warnings = validate_vocabulary()
                if warnings and hasattr(self, "_vocab_status"):
                    self._vocab_status.setText(
                        f"✓ Saved ({len(keywords)} keywords) - ⚠ " + "; ".join(warnings)
                    )
                    self._vocab_status.setStyleSheet(f"color: {COLORS['warning']};")
                elif hasattr(self, "_vocab_status"):
                    self._vocab_status.setText(f"✓ Saved ({len(keywords)} keywords)")
                    self._vocab_status.setStyleSheet(f"color: {COLORS['success']};")
        except Exception as e:
            logger.error(f"Vocabulary speichern fehlgeschlagen: {e}")
            if hasattr(self, "_vocab_status"):
                self._vocab_status.setText(f"Error: {e}")
                self._vocab_status.setStyleSheet(f"color: {COLORS['error']};")

    def _refresh_logs(self):
        """Aktualisiert Log-Anzeige."""
        try:
            from config import LOG_FILE

            if not self._logs_viewer:
                return

            if not LOG_FILE.exists():
                self._last_logs_signature = None
                self._set_logs_text_if_changed(
                    "No logs yet.\n\nLog file will appear here:\n" + str(LOG_FILE)
                )
                return

            signature = get_file_signature(LOG_FILE)
            if signature is not None and signature == self._last_logs_signature:
                return

            # Letzte 100 Zeilen (effizientes File-Tailing statt Full-Read)
            log_text = read_file_tail_lines(
                LOG_FILE,
                max_lines=100,
                errors="replace",
            )
            self._set_logs_text_if_changed(log_text)
            self._last_logs_signature = signature
        except Exception as e:
            logger.error(f"Logs laden fehlgeschlagen: {e}")

    def _set_logs_text_if_changed(self, text: str) -> None:
        """Aktualisiert den Log-Viewer nur bei Änderungen (vermeidet unnötiges Re-Render)."""
        if not self._logs_viewer or text == self._last_logs_text:
            return

        scrollbar = self._logs_viewer.verticalScrollBar()
        previous_maximum = scrollbar.maximum()
        previous_value = scrollbar.value()
        was_at_bottom = is_near_bottom(previous_value, previous_maximum)

        self._logs_viewer.setPlainText(text)
        self._last_logs_text = text

        scrollbar = self._logs_viewer.verticalScrollBar()
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
            return

        scrollbar.setValue(clamp_scroll_value(previous_value, scrollbar.maximum()))

    def _open_logs_folder(self):
        """Öffnet Logs-Ordner im Explorer."""
        try:
            import subprocess
            from config import LOG_FILE

            if LOG_FILE.exists():
                subprocess.run(["explorer", "/select,", str(LOG_FILE)], check=False)
            else:
                subprocess.run(["explorer", str(LOG_FILE.parent)], check=False)
        except Exception as e:
            logger.error(f"Explorer öffnen fehlgeschlagen: {e}")

    # =========================================================================
    # Settings Load/Save
    # =========================================================================

    def _load_settings(self):
        """Lädt aktuelle Settings in die UI."""
        # Mode
        mode = get_env_setting("PULSESCRIBE_MODE") or "deepgram"
        if self._mode_combo:
            idx = self._mode_combo.findText(mode)
            if idx >= 0:
                self._mode_combo.setCurrentIndex(idx)

        # Language
        lang = get_env_setting("PULSESCRIBE_LANGUAGE") or "auto"
        if self._lang_combo:
            idx = self._lang_combo.findText(lang)
            if idx >= 0:
                self._lang_combo.setCurrentIndex(idx)

        # Local Backend
        backend = normalize_local_backend(get_env_setting("PULSESCRIBE_LOCAL_BACKEND"))
        if self._local_backend_combo:
            idx = self._local_backend_combo.findText(backend)
            if idx >= 0:
                self._local_backend_combo.setCurrentIndex(idx)

        # Local Model
        model = get_env_setting("PULSESCRIBE_LOCAL_MODEL") or "default"
        if self._local_model_combo:
            idx = self._local_model_combo.findText(model)
            if idx >= 0:
                self._local_model_combo.setCurrentIndex(idx)

        # Streaming
        streaming = get_env_setting("PULSESCRIBE_STREAMING")
        if self._streaming_checkbox:
            self._streaming_checkbox.setChecked(
                _env_bool_default(streaming, default=True)
            )

        # Advanced: Device
        device = get_env_setting("PULSESCRIBE_DEVICE") or "auto"
        if hasattr(self, "_device_combo") and self._device_combo:
            idx = self._device_combo.findText(device)
            if idx >= 0:
                self._device_combo.setCurrentIndex(idx)

        # Advanced: Beam Size
        beam_size = get_env_setting("PULSESCRIBE_LOCAL_BEAM_SIZE") or ""
        if hasattr(self, "_beam_size_field") and self._beam_size_field:
            self._beam_size_field.setText(beam_size)

        # Advanced: Temperature
        temperature = get_env_setting("PULSESCRIBE_LOCAL_TEMPERATURE") or ""
        if hasattr(self, "_temperature_field") and self._temperature_field:
            self._temperature_field.setText(temperature)

        # Advanced: Best Of
        best_of = get_env_setting("PULSESCRIBE_LOCAL_BEST_OF") or ""
        if hasattr(self, "_best_of_field") and self._best_of_field:
            self._best_of_field.setText(best_of)

        # Faster-Whisper: Compute Type
        compute_type = get_env_setting("PULSESCRIBE_LOCAL_COMPUTE_TYPE") or "default"
        if hasattr(self, "_compute_type_combo") and self._compute_type_combo:
            idx = self._compute_type_combo.findText(compute_type)
            if idx >= 0:
                self._compute_type_combo.setCurrentIndex(idx)

        # Faster-Whisper: CPU Threads
        cpu_threads = get_env_setting("PULSESCRIBE_LOCAL_CPU_THREADS") or ""
        if hasattr(self, "_cpu_threads_field") and self._cpu_threads_field:
            self._cpu_threads_field.setText(cpu_threads)

        # Faster-Whisper: Num Workers
        num_workers = get_env_setting("PULSESCRIBE_LOCAL_NUM_WORKERS") or ""
        if hasattr(self, "_num_workers_field") and self._num_workers_field:
            self._num_workers_field.setText(num_workers)

        # Faster-Whisper: Without Timestamps
        without_ts = (
            get_env_setting("PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS") or "default"
        )
        if (
            hasattr(self, "_without_timestamps_combo")
            and self._without_timestamps_combo
        ):
            idx = self._without_timestamps_combo.findText(without_ts)
            if idx >= 0:
                self._without_timestamps_combo.setCurrentIndex(idx)

        # Faster-Whisper: VAD Filter
        vad = get_env_setting("PULSESCRIBE_LOCAL_VAD_FILTER") or "default"
        if hasattr(self, "_vad_filter_combo") and self._vad_filter_combo:
            idx = self._vad_filter_combo.findText(vad)
            if idx >= 0:
                self._vad_filter_combo.setCurrentIndex(idx)

        # FP16 override (canonical key) with legacy fallback for existing configs.
        fp16 = (
            get_env_setting(LOCAL_FP16_ENV_KEY)
            or get_env_setting(LEGACY_LOCAL_FP16_ENV_KEY)
            or "default"
        )
        if hasattr(self, "_fp16_combo") and self._fp16_combo:
            idx = self._fp16_combo.findText(fp16)
            if idx >= 0:
                self._fp16_combo.setCurrentIndex(idx)

        # Advanced: Lightning Batch Size
        batch_size = get_env_setting("PULSESCRIBE_LIGHTNING_BATCH_SIZE") or "12"
        if hasattr(self, "_lightning_batch_slider") and self._lightning_batch_slider:
            try:
                self._lightning_batch_slider.setValue(int(batch_size))
            except ValueError:
                self._lightning_batch_slider.setValue(12)

        # Advanced: Lightning Quantization
        quant = get_env_setting("PULSESCRIBE_LIGHTNING_QUANT") or "none"
        if hasattr(self, "_lightning_quant_combo") and self._lightning_quant_combo:
            idx = self._lightning_quant_combo.findText(quant)
            if idx >= 0:
                self._lightning_quant_combo.setCurrentIndex(idx)

        # Refine
        refine = get_env_setting("PULSESCRIBE_REFINE")
        if self._refine_checkbox:
            self._refine_checkbox.setChecked(_env_bool_default(refine, default=False))

        # Refine Provider
        provider = get_env_setting("PULSESCRIBE_REFINE_PROVIDER") or "groq"
        if self._refine_provider_combo:
            idx = self._refine_provider_combo.findText(provider)
            if idx >= 0:
                self._refine_provider_combo.setCurrentIndex(idx)

        # Refine Model
        refine_model = get_env_setting("PULSESCRIBE_REFINE_MODEL") or ""
        if self._refine_model_field:
            self._refine_model_field.setText(refine_model)

        # Overlay
        overlay = get_env_setting("PULSESCRIBE_OVERLAY")
        if self._overlay_checkbox:
            self._overlay_checkbox.setChecked(_env_bool_default(overlay, default=True))

        # RTF Display
        rtf = get_env_setting("PULSESCRIBE_SHOW_RTF")
        if self._rtf_checkbox:
            self._rtf_checkbox.setChecked(_env_bool_default(rtf, default=False))

        # Clipboard Restore
        clipboard_restore = get_env_setting("PULSESCRIBE_CLIPBOARD_RESTORE")
        if self._clipboard_restore_checkbox:
            self._clipboard_restore_checkbox.setChecked(
                _env_bool_default(clipboard_restore, default=False)
            )

        # Hotkeys
        env_values = read_env_file()
        toggle_raw = env_values.get("PULSESCRIBE_TOGGLE_HOTKEY")
        hold_raw = env_values.get("PULSESCRIBE_HOLD_HOTKEY")

        if toggle_raw is None and hold_raw is None:
            toggle = DEFAULT_WINDOWS_TOGGLE_HOTKEY
            hold = DEFAULT_WINDOWS_HOLD_HOTKEY
        else:
            toggle = (toggle_raw or "").strip()
            hold = (hold_raw or "").strip()

        if hasattr(self, "_toggle_hotkey_field") and self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText(toggle)

        if hasattr(self, "_hold_hotkey_field") and self._hold_hotkey_field:
            self._hold_hotkey_field.setText(hold)

        # API Keys
        for env_key, field in self._api_fields.items():
            value = get_api_key(env_key) or ""
            field.setText(value)
            # Status aktualisieren
            status = self._api_status.get(env_key)
            if status:
                if value:
                    status.setText("✓")
                    status.setStyleSheet(f"color: {COLORS['success']};")
                else:
                    status.setText("")

        # Mode-abhängige Sichtbarkeit
        self._on_mode_changed(mode)
        self._refresh_setup_overview()

    def _save_settings(self):
        """Speichert alle Settings."""
        try:
            validated_hotkeys = self._validate_hotkeys_for_save()
            if validated_hotkeys is None:
                return
            toggle_hotkey, hold_hotkey = validated_hotkeys

            # Mode
            if self._mode_combo:
                mode = self._mode_combo.currentText()
                save_env_setting("PULSESCRIBE_MODE", mode)

            # Language
            if self._lang_combo:
                lang = self._lang_combo.currentText()
                if lang == "auto":
                    remove_env_setting("PULSESCRIBE_LANGUAGE")
                else:
                    save_env_setting("PULSESCRIBE_LANGUAGE", lang)

            # Local Backend
            if self._local_backend_combo:
                backend = normalize_local_backend(self._local_backend_combo.currentText())
                if should_remove_local_backend_env(backend):
                    remove_env_setting("PULSESCRIBE_LOCAL_BACKEND")
                else:
                    save_env_setting("PULSESCRIBE_LOCAL_BACKEND", backend)

            # Local Model
            if self._local_model_combo:
                model = self._local_model_combo.currentText()
                if model == "default":
                    remove_env_setting("PULSESCRIBE_LOCAL_MODEL")
                else:
                    save_env_setting("PULSESCRIBE_LOCAL_MODEL", model)

            # Streaming
            if self._streaming_checkbox:
                if self._streaming_checkbox.isChecked():
                    remove_env_setting("PULSESCRIBE_STREAMING")  # Default is true
                else:
                    save_env_setting("PULSESCRIBE_STREAMING", "false")

            # Advanced: Device
            if hasattr(self, "_device_combo") and self._device_combo:
                device = self._device_combo.currentText()
                if device == "auto":
                    remove_env_setting("PULSESCRIBE_DEVICE")
                else:
                    save_env_setting("PULSESCRIBE_DEVICE", device)

            # Advanced: Beam Size
            if hasattr(self, "_beam_size_field") and self._beam_size_field:
                beam_size = self._beam_size_field.text().strip()
                if beam_size:
                    save_env_setting("PULSESCRIBE_LOCAL_BEAM_SIZE", beam_size)
                else:
                    remove_env_setting("PULSESCRIBE_LOCAL_BEAM_SIZE")

            # Advanced: Temperature
            if hasattr(self, "_temperature_field") and self._temperature_field:
                temperature = self._temperature_field.text().strip()
                if temperature:
                    save_env_setting("PULSESCRIBE_LOCAL_TEMPERATURE", temperature)
                else:
                    remove_env_setting("PULSESCRIBE_LOCAL_TEMPERATURE")

            # Advanced: Best Of
            if hasattr(self, "_best_of_field") and self._best_of_field:
                best_of = self._best_of_field.text().strip()
                if best_of:
                    save_env_setting("PULSESCRIBE_LOCAL_BEST_OF", best_of)
                else:
                    remove_env_setting("PULSESCRIBE_LOCAL_BEST_OF")

            # Faster-Whisper: Compute Type
            if hasattr(self, "_compute_type_combo") and self._compute_type_combo:
                compute_type = self._compute_type_combo.currentText()
                if compute_type == "default":
                    remove_env_setting("PULSESCRIBE_LOCAL_COMPUTE_TYPE")
                else:
                    save_env_setting("PULSESCRIBE_LOCAL_COMPUTE_TYPE", compute_type)

            # Faster-Whisper: CPU Threads
            if hasattr(self, "_cpu_threads_field") and self._cpu_threads_field:
                cpu_threads = self._cpu_threads_field.text().strip()
                if cpu_threads:
                    save_env_setting("PULSESCRIBE_LOCAL_CPU_THREADS", cpu_threads)
                else:
                    remove_env_setting("PULSESCRIBE_LOCAL_CPU_THREADS")

            # Faster-Whisper: Num Workers
            if hasattr(self, "_num_workers_field") and self._num_workers_field:
                num_workers = self._num_workers_field.text().strip()
                if num_workers:
                    save_env_setting("PULSESCRIBE_LOCAL_NUM_WORKERS", num_workers)
                else:
                    remove_env_setting("PULSESCRIBE_LOCAL_NUM_WORKERS")

            # Faster-Whisper: Without Timestamps
            if (
                hasattr(self, "_without_timestamps_combo")
                and self._without_timestamps_combo
            ):
                without_ts = self._without_timestamps_combo.currentText()
                if without_ts == "default":
                    remove_env_setting("PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS")
                else:
                    save_env_setting("PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS", without_ts)

            # Faster-Whisper: VAD Filter
            if hasattr(self, "_vad_filter_combo") and self._vad_filter_combo:
                vad = self._vad_filter_combo.currentText()
                if vad == "default":
                    remove_env_setting("PULSESCRIBE_LOCAL_VAD_FILTER")
                else:
                    save_env_setting("PULSESCRIBE_LOCAL_VAD_FILTER", vad)

            # Faster-Whisper: FP16
            if hasattr(self, "_fp16_combo") and self._fp16_combo:
                fp16 = self._fp16_combo.currentText()
                if fp16 == "default":
                    remove_env_setting(LOCAL_FP16_ENV_KEY)
                    remove_env_setting(LEGACY_LOCAL_FP16_ENV_KEY)
                else:
                    save_env_setting(LOCAL_FP16_ENV_KEY, fp16)
                    remove_env_setting(LEGACY_LOCAL_FP16_ENV_KEY)

            # Advanced: Lightning Batch Size
            if (
                hasattr(self, "_lightning_batch_slider")
                and self._lightning_batch_slider
            ):
                batch_size = self._lightning_batch_slider.value()
                if batch_size == 12:
                    remove_env_setting("PULSESCRIBE_LIGHTNING_BATCH_SIZE")  # Default
                else:
                    save_env_setting(
                        "PULSESCRIBE_LIGHTNING_BATCH_SIZE", str(batch_size)
                    )

            # Advanced: Lightning Quantization
            if hasattr(self, "_lightning_quant_combo") and self._lightning_quant_combo:
                quant = self._lightning_quant_combo.currentText()
                if quant == "none":
                    remove_env_setting("PULSESCRIBE_LIGHTNING_QUANT")
                else:
                    save_env_setting("PULSESCRIBE_LIGHTNING_QUANT", quant)

            # Refine
            if self._refine_checkbox:
                save_env_setting(
                    "PULSESCRIBE_REFINE",
                    "true" if self._refine_checkbox.isChecked() else "false",
                )

            # Refine Provider
            if self._refine_provider_combo:
                provider = self._refine_provider_combo.currentText()
                if provider == "groq":
                    remove_env_setting("PULSESCRIBE_REFINE_PROVIDER")
                else:
                    save_env_setting("PULSESCRIBE_REFINE_PROVIDER", provider)

            # Refine Model
            if self._refine_model_field:
                model = self._refine_model_field.text().strip()
                if model:
                    save_env_setting("PULSESCRIBE_REFINE_MODEL", model)
                else:
                    remove_env_setting("PULSESCRIBE_REFINE_MODEL")

            # Overlay
            if self._overlay_checkbox:
                if self._overlay_checkbox.isChecked():
                    remove_env_setting("PULSESCRIBE_OVERLAY")  # Default is true
                else:
                    save_env_setting("PULSESCRIBE_OVERLAY", "false")

            # RTF Display
            if self._rtf_checkbox:
                if self._rtf_checkbox.isChecked():
                    save_env_setting("PULSESCRIBE_SHOW_RTF", "true")
                else:
                    remove_env_setting("PULSESCRIBE_SHOW_RTF")  # Default is false

            # Clipboard Restore
            if self._clipboard_restore_checkbox:
                if self._clipboard_restore_checkbox.isChecked():
                    save_env_setting("PULSESCRIBE_CLIPBOARD_RESTORE", "true")
                else:
                    remove_env_setting("PULSESCRIBE_CLIPBOARD_RESTORE")

            # Hotkeys
            if hasattr(self, "_toggle_hotkey_field") and self._toggle_hotkey_field:
                if toggle_hotkey:
                    save_env_setting("PULSESCRIBE_TOGGLE_HOTKEY", toggle_hotkey)
                else:
                    remove_env_setting("PULSESCRIBE_TOGGLE_HOTKEY")

            if hasattr(self, "_hold_hotkey_field") and self._hold_hotkey_field:
                if hold_hotkey:
                    save_env_setting("PULSESCRIBE_HOLD_HOTKEY", hold_hotkey)
                else:
                    remove_env_setting("PULSESCRIBE_HOLD_HOTKEY")

            # API Keys
            for env_key, field in self._api_fields.items():
                value = field.text().strip()
                is_set = set_api_key(env_key, value)
                status = self._api_status.get(env_key)
                if status:
                    if is_set:
                        status.setText("✓")
                        status.setStyleSheet(f"color: {COLORS['success']};")
                    else:
                        status.setText("")
                        status.setStyleSheet(f"color: {COLORS['text_secondary']};")

            # Prompts speichern (aus Cache + aktuellem Editor)
            self._save_all_prompts()

            # Onboarding als abgeschlossen markieren (beim ersten Speichern)
            # Dies verhindert, dass Settings bei jedem Start erneut öffnet
            if not is_onboarding_complete():
                set_onboarding_step(OnboardingStep.DONE)
                logger.info("Onboarding als abgeschlossen markiert")

            logger.info("Settings gespeichert")
            self.settings_changed.emit()

            # Signal-Datei für Daemon-Reload erstellen
            # (Settings-Fenster läuft als separater Prozess, daher IPC via Datei)
            self._write_reload_signal()

            # Visual Save Feedback
            self._show_save_feedback()
            self._refresh_setup_overview()

            # Callback aufrufen (für Daemon-Reload, falls im gleichen Prozess)
            if self._on_settings_changed_callback:
                self._on_settings_changed_callback()

        except Exception as e:
            logger.error(f"Settings speichern fehlgeschlagen: {e}")
            # Error Feedback
            if hasattr(self, "_save_btn") and self._save_btn:
                self._save_btn.setText("❌ Error!")
                from PySide6.QtCore import QTimer

                QTimer.singleShot(1500, lambda: self._save_btn.setText("Save && Apply"))

    def _apply_local_preset(self, preset: str):
        """Wendet ein Local Mode Preset an (UI-only, ohne zu speichern)."""
        # Windows-optimierte Presets
        presets = {
            "cuda_fast": {
                "device": "cuda",
                "compute_type": "float16",
                "vad_filter": "true",
                "without_timestamps": "true",
            },
            "cpu_fast": {
                "device": "cpu",
                "compute_type": "int8",
                "cpu_threads": "0",
                "num_workers": "1",
                "vad_filter": "true",
                "without_timestamps": "true",
            },
            "cpu_quality": {
                "local_model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "beam_size": "5",
            },
        }

        preset_values = presets.get(preset)
        if not preset_values:
            return
        values = dict(WINDOWS_LOCAL_PRESET_BASE)
        values.update(preset_values)

        # UI-Felder aktualisieren
        if self._mode_combo:
            idx = self._mode_combo.findText(values.get("mode", "local"))
            if idx >= 0:
                self._mode_combo.setCurrentIndex(idx)
                self._on_mode_changed("local")  # Sichtbarkeit aktualisieren

        if self._local_backend_combo:
            idx = self._local_backend_combo.findText(
                values.get("local_backend", "faster")
            )
            if idx >= 0:
                self._local_backend_combo.setCurrentIndex(idx)

        if self._local_model_combo:
            idx = self._local_model_combo.findText(values.get("local_model", "turbo"))
            if idx >= 0:
                self._local_model_combo.setCurrentIndex(idx)

        if hasattr(self, "_device_combo") and self._device_combo:
            idx = self._device_combo.findText(values.get("device", "auto"))
            if idx >= 0:
                self._device_combo.setCurrentIndex(idx)

        if hasattr(self, "_compute_type_combo") and self._compute_type_combo:
            idx = self._compute_type_combo.findText(
                values.get("compute_type", "default")
            )
            if idx >= 0:
                self._compute_type_combo.setCurrentIndex(idx)

        if hasattr(self, "_beam_size_field") and self._beam_size_field:
            self._beam_size_field.setText(values.get("beam_size", ""))

        if hasattr(self, "_temperature_field") and self._temperature_field:
            self._temperature_field.setText(values.get("temperature", ""))

        if hasattr(self, "_best_of_field") and self._best_of_field:
            self._best_of_field.setText(values.get("best_of", ""))

        if hasattr(self, "_cpu_threads_field") and self._cpu_threads_field:
            self._cpu_threads_field.setText(values.get("cpu_threads", ""))

        if hasattr(self, "_num_workers_field") and self._num_workers_field:
            self._num_workers_field.setText(values.get("num_workers", ""))

        if hasattr(self, "_vad_filter_combo") and self._vad_filter_combo:
            idx = self._vad_filter_combo.findText(values.get("vad_filter", "default"))
            if idx >= 0:
                self._vad_filter_combo.setCurrentIndex(idx)

        if (
            hasattr(self, "_without_timestamps_combo")
            and self._without_timestamps_combo
        ):
            idx = self._without_timestamps_combo.findText(
                values.get("without_timestamps", "default")
            )
            if idx >= 0:
                self._without_timestamps_combo.setCurrentIndex(idx)

        if hasattr(self, "_fp16_combo") and self._fp16_combo:
            idx = self._fp16_combo.findText(values.get("fp16", "default"))
            if idx >= 0:
                self._fp16_combo.setCurrentIndex(idx)

        if hasattr(self, "_lightning_batch_slider") and self._lightning_batch_slider:
            self._lightning_batch_slider.setValue(
                int(values.get("lightning_batch_size", 12))
            )

        if hasattr(self, "_lightning_quant_combo") and self._lightning_quant_combo:
            idx = self._lightning_quant_combo.findText(
                values.get("lightning_quant", "none")
            )
            if idx >= 0:
                self._lightning_quant_combo.setCurrentIndex(idx)

        # Feedback
        if hasattr(self, "_preset_status") and self._preset_status:
            self._preset_status.setText(
                f"✓ '{preset}' preset applied — click 'Save & Apply' to persist."
            )
            self._preset_status.setStyleSheet(f"color: {COLORS['success']};")

    def _write_reload_signal(self):
        """Schreibt Signal-Datei für Daemon-Reload.

        Der Daemon prüft periodisch auf diese Datei und lädt Settings neu.
        Robuster als nur auf watchdog FileWatcher zu vertrauen.
        """
        try:
            from utils.preferences import ENV_FILE

            signal_file = ENV_FILE.parent / ".reload"
            signal_file.write_text(str(time.time()))
            logger.debug(f"Reload-Signal geschrieben: {signal_file}")
        except Exception as e:
            # Warning statt debug, damit Benutzer sieht wenn Reload nicht funktioniert
            logger.warning(
                f"Reload-Signal konnte nicht geschrieben werden: {e} - "
                "Daemon wird Änderungen erst nach Neustart übernehmen"
            )

    def _show_save_feedback(self):
        """Zeigt visuelles Feedback nach erfolgreichem Speichern."""
        if hasattr(self, "_save_btn") and self._save_btn:
            self._save_btn.setText("✓ Saved!")
            self._save_btn.setStyleSheet(
                f"""
                QPushButton#primary {{
                    background-color: {COLORS["success"]};
                    border-color: {COLORS["success"]};
                }}
            """
            )
            from PySide6.QtCore import QTimer

            QTimer.singleShot(1500, self._reset_save_button)

    def _reset_save_button(self):
        """Setzt Save-Button auf Originalzustand zurück."""
        if hasattr(self, "_save_btn") and self._save_btn:
            self._save_btn.setText("Save && Apply")
            self._save_btn.setStyleSheet("")  # Reset to default stylesheet

    # =========================================================================
    # Public API
    # =========================================================================

    def set_on_settings_changed(self, callback: Callable[[], None]):
        """Setzt Callback für Settings-Änderungen."""
        self._on_settings_changed_callback = callback

    def showEvent(self, event):
        super().showEvent(event)
        self._update_logs_auto_refresh_state()

    def hideEvent(self, event):
        self._update_logs_auto_refresh_state()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_logs_auto_refresh_state()

    def _cleanup_before_close(self) -> bool:
        """Führt einmaliges Cleanup für alle Schließpfade aus."""
        if getattr(self, "_is_closed", False):
            return False

        self._is_closed = True

        if hasattr(self, "_logs_refresh_timer"):
            self._logs_refresh_timer.stop()

        self._stop_pynput_listener()
        self._recording_hotkey_for = None
        return True

    def closeEvent(self, event):
        """Handler für Fenster schließen."""
        if self._cleanup_before_close():
            self.closed.emit()
        super().closeEvent(event)

    def reject(self):
        """ESC/Close-Button behandeln wie ein echtes Window-Close."""
        if self._cleanup_before_close():
            self.closed.emit()
        super().reject()


# =============================================================================
# Standalone Test
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SettingsWindow()
    window.show()
    sys.exit(app.exec())
