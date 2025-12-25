"""Settings Window für PulseScribe (Windows).

PySide6-basiertes Settings-Fenster mit Dark Theme.
Portiert von ui/welcome.py (macOS AppKit).
"""

import logging
import sys
import threading
import time
from typing import Callable

from PySide6.QtCore import Qt, Signal
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

from utils.preferences import (
    get_api_key,
    get_env_setting,
    get_show_welcome_on_startup,
    is_onboarding_complete,
    remove_env_setting,
    save_api_key,
    save_env_setting,
    set_onboarding_step,
    set_show_welcome_on_startup,
)
from utils.onboarding import OnboardingStep

logger = logging.getLogger("pulsescribe.settings")

# =============================================================================
# pynput Key-Mapping (einmalig geladen für Performance)
# =============================================================================

# Cache für pynput-Verfügbarkeit und Key-Map
_pynput_available: bool | None = None
_pynput_key_map: dict | None = None


def _get_pynput_key_map() -> tuple[bool, dict]:
    """Lädt pynput einmalig und gibt (available, key_map) zurück."""
    global _pynput_available, _pynput_key_map

    if _pynput_available is not None:
        return _pynput_available, _pynput_key_map or {}

    try:
        from pynput import keyboard  # type: ignore[import-not-found]

        _pynput_key_map = {
            keyboard.Key.ctrl: "ctrl",
            keyboard.Key.ctrl_l: "ctrl",
            keyboard.Key.ctrl_r: "ctrl",
            keyboard.Key.alt: "alt",
            keyboard.Key.alt_l: "alt",
            keyboard.Key.alt_r: "alt",
            keyboard.Key.alt_gr: "alt",
            keyboard.Key.shift: "shift",
            keyboard.Key.shift_l: "shift",
            keyboard.Key.shift_r: "shift",
            keyboard.Key.cmd: "win",
            keyboard.Key.cmd_l: "win",
            keyboard.Key.cmd_r: "win",
            keyboard.Key.space: "space",
            keyboard.Key.tab: "tab",
            keyboard.Key.backspace: "backspace",
            keyboard.Key.delete: "delete",
            keyboard.Key.enter: "enter",
            keyboard.Key.esc: "esc",
        }
        _pynput_available = True
    except ImportError:
        _pynput_available = False
        _pynput_key_map = {}

    return _pynput_available, _pynput_key_map


# =============================================================================
# Window-Konstanten (analog zu macOS)
# =============================================================================

SETTINGS_WIDTH = 600
SETTINGS_HEIGHT = 700
CARD_PADDING = 16
CARD_CORNER_RADIUS = 12
CARD_SPACING = 12

# =============================================================================
# Dropdown-Optionen (identisch mit macOS)
# =============================================================================

MODE_OPTIONS = ["deepgram", "openai", "groq", "local"]
REFINE_PROVIDER_OPTIONS = ["groq", "openai", "openrouter"]
LANGUAGE_OPTIONS = ["auto", "de", "en", "es", "fr", "it", "pt", "nl", "pl", "ru", "zh"]
LOCAL_BACKEND_OPTIONS = ["whisper", "faster", "mlx", "lightning", "auto"]
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

# =============================================================================
# Farben (Dark Theme, angelehnt an macOS HUD)
# =============================================================================

COLORS = {
    "bg_window": "#1a1a1a",
    "bg_card": "rgba(255, 255, 255, 0.06)",
    "bg_input": "rgba(255, 255, 255, 0.08)",
    "border": "rgba(255, 255, 255, 0.15)",
    "text": "#ffffff",
    "text_secondary": "rgba(255, 255, 255, 0.6)",
    "text_hint": "rgba(255, 255, 255, 0.4)",
    "accent": "#007AFF",
    "success": "#4CAF50",
    "error": "#FF5252",
    "warning": "#FFC107",
}

# =============================================================================
# Stylesheet
# =============================================================================

STYLESHEET = f"""
QDialog {{
    background-color: {COLORS["bg_window"]};
}}

QTabWidget::pane {{
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    background-color: transparent;
    padding: 8px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {COLORS["text_secondary"]};
    padding: 8px 16px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}

QTabBar::tab:selected {{
    background-color: {COLORS["bg_card"]};
    color: {COLORS["text"]};
}}

QTabBar::tab:hover:!selected {{
    background-color: rgba(255, 255, 255, 0.03);
}}

QLabel {{
    color: {COLORS["text"]};
    background: transparent;
}}

QLineEdit {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 8px 12px;
    color: {COLORS["text"]};
    selection-background-color: {COLORS["accent"]};
}}

QLineEdit:focus {{
    border-color: {COLORS["accent"]};
}}

QLineEdit:disabled {{
    color: {COLORS["text_hint"]};
}}

QComboBox {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 8px 12px;
    color: {COLORS["text"]};
    min-width: 120px;
}}

QComboBox:focus {{
    border-color: {COLORS["accent"]};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {COLORS["text_secondary"]};
    margin-right: 8px;
}}

QComboBox QAbstractItemView {{
    background-color: #2a2a2a;
    border: 1px solid {COLORS["border"]};
    selection-background-color: {COLORS["accent"]};
    color: {COLORS["text"]};
}}

QCheckBox {{
    color: {COLORS["text"]};
    spacing: 8px;
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid {COLORS["border"]};
    background-color: {COLORS["bg_input"]};
}}

QCheckBox::indicator:checked {{
    background-color: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
}}

QPushButton {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 8px 16px;
    color: {COLORS["text"]};
    min-width: 80px;
}}

QPushButton:hover {{
    background-color: rgba(255, 255, 255, 0.12);
}}

QPushButton:pressed {{
    background-color: rgba(255, 255, 255, 0.08);
}}

QPushButton#primary {{
    background-color: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
}}

QPushButton#primary:hover {{
    background-color: #0066DD;
}}

QPlainTextEdit {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 8px;
    color: {COLORS["text"]};
    selection-background-color: {COLORS["accent"]};
}}

QPlainTextEdit:focus {{
    border-color: {COLORS["accent"]};
}}

QScrollArea {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background-color: transparent;
    width: 8px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: rgba(255, 255, 255, 0.3);
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QSlider::groove:horizontal {{
    background-color: {COLORS["bg_input"]};
    height: 4px;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background-color: {COLORS["accent"]};
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}}

QSlider::handle:horizontal:hover {{
    background-color: #0066DD;
}}

QFrame#card {{
    background-color: {COLORS["bg_card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: {CARD_CORNER_RADIUS}px;
}}
"""


# =============================================================================
# Helper Functions
# =============================================================================


def create_card(title: str | None = None, description: str | None = None) -> tuple[QFrame, QVBoxLayout]:
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


def create_label_row(label_text: str, widget: QWidget, hint: str | None = None) -> QHBoxLayout:
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
    _hotkey_field_update = Signal(str)  # Thread-safe hotkey field updates

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

        # Hotkey Recording State
        self._recording_hotkey_for: str | None = None
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
        self.setFixedSize(SETTINGS_WIDTH, SETTINGS_HEIGHT)
        self.setStyleSheet(STYLESHEET)

        # Window Flags
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )

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
        """Erstellt den Footer mit Save- und Close-Button."""
        footer = QWidget()
        footer.setFixedHeight(60)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(20, 10, 20, 20)

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
        card, card_layout = create_card(
            "✅ Status",
            "PulseScribe is ready to use."
        )

        status_label = QLabel("• Hotkey: Press to start/stop recording\n• Audio will be transcribed and pasted automatically")
        status_label.setFont(QFont("Segoe UI", 10))
        status_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        card_layout.addWidget(status_label)

        layout.addWidget(card)

        # How-To Card
        card, card_layout = create_card(
            "📖 How to Use",
            "Quick guide to get started."
        )

        instructions = QLabel(
            "1. Configure your API keys in the Providers tab\n"
            "2. Set up your preferred hotkey in the Hotkeys tab\n"
            "3. Press the hotkey to start recording\n"
            "4. Speak clearly, then press the hotkey again to stop\n"
            "5. The transcribed text will be pasted automatically"
        )
        instructions.setFont(QFont("Segoe UI", 10))
        instructions.setWordWrap(True)
        card_layout.addWidget(instructions)

        layout.addWidget(card)

        # Local Mode Presets Card
        card, card_layout = create_card(
            "⚡ Local Mode Presets",
            "Quick-apply optimized settings for local transcription."
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
        preset_btn_quality.clicked.connect(lambda: self._apply_local_preset("cpu_quality"))
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
            "⌨️ Hotkeys",
            "Configure keyboard shortcuts for recording."
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
        self._toggle_record_btn.clicked.connect(lambda: self._start_hotkey_recording("toggle"))
        toggle_row.addWidget(self._toggle_record_btn)

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
        self._hold_record_btn.clicked.connect(lambda: self._start_hotkey_recording("hold"))
        hold_row.addWidget(self._hold_record_btn)

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
            ("Ctrl+Alt+R / Space", "ctrl+alt+r", "ctrl+alt+space"),
            ("F13 Toggle", "f13", ""),
        ]:
            btn = QPushButton(preset_name)
            btn.clicked.connect(lambda checked, t=toggle_val, h=hold_val: self._apply_hotkey_preset_pair(t, h))
            presets_layout.addWidget(btn)

        presets_layout.addStretch()
        card_layout.addLayout(presets_layout)

        # Hint
        hint = QLabel("💡 Hold hotkey: Push-to-talk mode. Toggle hotkey: Press to start/stop.\n"
                      "Click 'Record' and press your desired key combination.")
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
            "Configure the transcription provider and language."
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
            "🔑 API Keys",
            "Enter your API keys for cloud providers."
        )

        for provider, env_key in [
            ("Deepgram", "DEEPGRAM_API_KEY"),
            ("OpenAI", "OPENAI_API_KEY"),
            ("Groq", "GROQ_API_KEY"),
            ("OpenRouter", "OPENROUTER_API_KEY"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(8)

            label = QLabel(f"{provider}:")
            label.setMinimumWidth(100)
            row.addWidget(label)

            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText(f"Enter {provider} API key...")
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
            "🔧 Local Model Settings",
            "Advanced settings for local Whisper models."
        )

        # Device
        self._device_combo = QComboBox()
        self._device_combo.addItems(DEVICE_OPTIONS)
        card_layout.addLayout(create_label_row("Device:", self._device_combo))

        # Beam Size (Integer 1-10)
        self._beam_size_field = QLineEdit()
        self._beam_size_field.setPlaceholderText("5")
        self._beam_size_field.setValidator(QIntValidator(1, 20))
        card_layout.addLayout(create_label_row("Beam Size:", self._beam_size_field, "1-20"))

        # Temperature (Float 0.0-1.0)
        self._temperature_field = QLineEdit()
        self._temperature_field.setPlaceholderText("0.0")
        self._temperature_field.setValidator(QDoubleValidator(0.0, 1.0, 2))
        card_layout.addLayout(create_label_row("Temperature:", self._temperature_field, "0.0-1.0"))

        # Best Of (Integer)
        self._best_of_field = QLineEdit()
        self._best_of_field.setPlaceholderText("default")
        self._best_of_field.setValidator(QIntValidator(1, 10))
        card_layout.addLayout(create_label_row("Best Of:", self._best_of_field, "1-10"))

        layout.addWidget(card)

        # Faster-Whisper Card
        card, card_layout = create_card(
            "🚀 Faster-Whisper Settings",
            "Settings for faster-whisper backend."
        )

        # Compute Type
        self._compute_type_combo = QComboBox()
        self._compute_type_combo.addItems(["default", "float16", "float32", "int8", "int8_float16"])
        card_layout.addLayout(create_label_row("Compute Type:", self._compute_type_combo))

        # CPU Threads
        self._cpu_threads_field = QLineEdit()
        self._cpu_threads_field.setPlaceholderText("auto")
        self._cpu_threads_field.setValidator(QIntValidator(1, 32))
        card_layout.addLayout(create_label_row("CPU Threads:", self._cpu_threads_field, "1-32"))

        # Num Workers
        self._num_workers_field = QLineEdit()
        self._num_workers_field.setPlaceholderText("1")
        self._num_workers_field.setValidator(QIntValidator(1, 8))
        card_layout.addLayout(create_label_row("Num Workers:", self._num_workers_field, "1-8"))

        # Boolean Overrides
        self._without_timestamps_combo = QComboBox()
        self._without_timestamps_combo.addItems(BOOL_OVERRIDE_OPTIONS)
        card_layout.addLayout(create_label_row("Without Timestamps:", self._without_timestamps_combo))

        self._vad_filter_combo = QComboBox()
        self._vad_filter_combo.addItems(BOOL_OVERRIDE_OPTIONS)
        card_layout.addLayout(create_label_row("VAD Filter:", self._vad_filter_combo))

        self._fp16_combo = QComboBox()
        self._fp16_combo.addItems(BOOL_OVERRIDE_OPTIONS)
        card_layout.addLayout(create_label_row("FP16:", self._fp16_combo))

        layout.addWidget(card)

        # Lightning Card
        card, card_layout = create_card(
            "⚡ Lightning Settings",
            "Settings for Lightning Whisper backend."
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
        card_layout.addLayout(create_label_row("Quantization:", self._lightning_quant_combo))

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
            "Post-process transcriptions with AI for better formatting."
        )

        self._refine_checkbox = QCheckBox("Enable LLM Refinement")
        card_layout.addWidget(self._refine_checkbox)

        # Provider
        self._refine_provider_combo = QComboBox()
        self._refine_provider_combo.addItems(REFINE_PROVIDER_OPTIONS)
        card_layout.addLayout(create_label_row("Provider:", self._refine_provider_combo))

        # Model
        self._refine_model_field = QLineEdit()
        self._refine_model_field.setPlaceholderText("e.g., openai/gpt-4o")
        card_layout.addLayout(create_label_row("Model:", self._refine_model_field))

        layout.addWidget(card)

        # Display Card
        card, card_layout = create_card(
            "🖥️ Display Settings",
            "Configure visual feedback during transcription."
        )

        self._overlay_checkbox = QCheckBox("Show Overlay during recording")
        card_layout.addWidget(self._overlay_checkbox)

        self._rtf_checkbox = QCheckBox("Show RTF (Real-Time Factor) after transcription")
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
            "📝 Custom Prompts",
            "Customize prompts for different contexts."
        )

        # Context Selector
        self._prompt_context_combo = QComboBox()
        self._prompt_context_combo.addItems(["default", "email", "chat", "code", "voice_commands", "app_mappings"])
        self._prompt_context_combo.currentTextChanged.connect(self._on_prompt_context_changed)
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
            "Add custom words and phrases to improve transcription accuracy."
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
        vocab_hint = QLabel("💡 Deepgram supports max 100 keywords, Local Whisper max 50.")
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
        self._logs_btn.setStyleSheet(f"""
            QPushButton {{ border-radius: 6px 0 0 6px; }}
            QPushButton:checked {{ background-color: {COLORS['accent']}; }}
        """)
        self._logs_btn.clicked.connect(lambda: self._switch_logs_view(0))

        self._transcripts_btn = QPushButton("📝 Transcripts")
        self._transcripts_btn.setCheckable(True)
        self._transcripts_btn.setStyleSheet(f"""
            QPushButton {{ border-radius: 0 6px 6px 0; }}
            QPushButton:checked {{ background-color: {COLORS['accent']}; }}
        """)
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
        self._transcripts_viewer.setPlaceholderText("Transcripts history will appear here...")
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
        self._logs_refresh_timer = QTimer()
        self._logs_refresh_timer.timeout.connect(self._refresh_logs)

        # Initial load
        self._refresh_logs()

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
            "Minimalistic voice-to-text for Windows.\n"
            "Inspired by Wispr Flow."
        )
        desc.setFont(QFont("Segoe UI", 10))
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        card_layout.addWidget(desc)

        card_layout.addSpacing(20)

        # Links
        links = QLabel(
            '<a href="https://github.com/pulsescribe/pulsescribe" style="color: #007AFF;">GitHub</a> · '
            '<a href="https://pulsescribe.com/docs" style="color: #007AFF;">Documentation</a>'
        )
        links.setFont(QFont("Segoe UI", 10))
        links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        links.setOpenExternalLinks(True)
        card_layout.addWidget(links)

        layout.addWidget(card)

        # Startup Settings Card
        startup_card, startup_layout = create_card(
            "⚙️ Startup",
            "Configure behavior when PulseScribe starts."
        )

        # Show at startup checkbox
        self._show_at_startup_checkbox = QCheckBox("Show Settings at startup")
        self._show_at_startup_checkbox.setChecked(get_show_welcome_on_startup())
        self._show_at_startup_checkbox.stateChanged.connect(self._on_show_at_startup_changed)
        startup_layout.addWidget(self._show_at_startup_checkbox)

        layout.addWidget(startup_card)
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
            self._refresh_logs()

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

    def _on_batch_size_changed(self, value: int):
        """Handler für Batch-Size Slider."""
        if self._lightning_batch_value:
            self._lightning_batch_value.setText(str(value))

    def _on_show_at_startup_changed(self, state: int):
        """Handler für Show at startup Checkbox."""
        set_show_welcome_on_startup(state == Qt.CheckState.Checked.value)

    def _apply_hotkey_preset(self, hotkey: str):
        """Wendet ein Hotkey-Preset an (nur Toggle)."""
        if self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText(hotkey)

    def _apply_hotkey_preset_pair(self, toggle: str, hold: str):
        """Wendet ein Hotkey-Preset-Paar an."""
        if self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText(toggle)
        if self._hold_hotkey_field:
            self._hold_hotkey_field.setText(hold)
        self._set_hotkey_status(f"Preset applied: Toggle={toggle or 'none'}, Hold={hold or 'none'}", "success")

    def _start_hotkey_recording(self, kind: str):
        """Startet Hotkey-Recording für toggle oder hold."""
        self._recording_hotkey_for = kind
        self._pressed_keys.clear()

        # Button-Text ändern
        if kind == "toggle" and hasattr(self, "_toggle_record_btn"):
            self._toggle_record_btn.setText("Press key...")
            self._toggle_record_btn.setStyleSheet(f"background-color: {COLORS['accent']};")
        elif kind == "hold" and hasattr(self, "_hold_record_btn"):
            self._hold_record_btn.setText("Press key...")
            self._hold_record_btn.setStyleSheet(f"background-color: {COLORS['accent']};")

        self._set_hotkey_status("Press your hotkey combination, then press Enter to confirm...", "warning")

        # Low-level pynput Hook für Win-Taste (Qt kann sie nicht abfangen)
        self._start_pynput_listener()
        self.setFocus()

    def _start_pynput_listener(self):
        """Startet pynput Listener für Low-Level Key-Capture."""
        self._using_qt_grab = False
        available, _ = _get_pynput_key_map()

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

            self._pynput_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
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
        _, key_map = _get_pynput_key_map()

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
            self._hotkey_field_update.emit(hotkey_str)

    def _set_hotkey_field_text(self, hotkey_str: str):
        """Setzt den Text im aktiven Hotkey-Feld (Thread-safe)."""
        if self._recording_hotkey_for == "toggle" and self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText(hotkey_str)
        elif self._recording_hotkey_for == "hold" and self._hold_hotkey_field:
            self._hold_hotkey_field.setText(hotkey_str)

    def _stop_hotkey_recording(self, hotkey_str: str | None = None):
        """Beendet Hotkey-Recording."""
        kind = self._recording_hotkey_for
        self._recording_hotkey_for = None

        # pynput Listener stoppen
        self._stop_pynput_listener()

        # Buttons zurücksetzen
        if hasattr(self, "_toggle_record_btn"):
            self._toggle_record_btn.setText("Record")
            self._toggle_record_btn.setStyleSheet("")
        if hasattr(self, "_hold_record_btn"):
            self._hold_record_btn.setText("Record")
            self._hold_record_btn.setStyleSheet("")

        if hotkey_str and kind:
            # Hotkey in Feld setzen
            if kind == "toggle" and self._toggle_hotkey_field:
                self._toggle_hotkey_field.setText(hotkey_str)
            elif kind == "hold" and self._hold_hotkey_field:
                self._hold_hotkey_field.setText(hotkey_str)
            self._set_hotkey_status(f"✓ Recorded: {hotkey_str}", "success")
        else:
            self._set_hotkey_status("Recording cancelled", "text_hint")

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
            return chr(ord('a') + key - Qt.Key.Key_A)

        # Zahlen
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(ord('0') + key - Qt.Key.Key_0)

        return ""

    def _set_hotkey_status(self, text: str, color: str):
        """Setzt Hotkey-Status-Text."""
        if hasattr(self, "_hotkey_status") and self._hotkey_status:
            self._hotkey_status.setText(text)
            color_value = COLORS.get(color, COLORS["text"])
            self._hotkey_status.setStyleSheet(f"color: {color_value};")

    # =========================================================================
    # Prompt Handlers
    # =========================================================================

    def _on_prompt_context_changed(self, context: str):
        """Lädt Prompt für gewählten Kontext."""
        # Aktuellen Prompt im Cache speichern
        if self._prompt_editor and self._current_prompt_context:
            self._prompts_cache[self._current_prompt_context] = self._prompt_editor.toPlainText()

        self._current_prompt_context = context
        self._load_prompt_for_context(context)

    def _load_prompt_for_context(self, context: str):
        """Lädt den Prompt-Text für einen Kontext."""
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
                self._set_prompt_status("", "text")

        except Exception as e:
            logger.error(f"Prompt laden fehlgeschlagen: {e}")
            self._set_prompt_status(f"Error: {e}", "error")

    def _save_current_prompt(self):
        """Speichert den aktuellen Prompt."""
        try:
            from utils.custom_prompts import (
                load_custom_prompts,
                save_custom_prompts,
                parse_app_mappings,
            )

            context = self._prompt_context_combo.currentText() if self._prompt_context_combo else "default"
            text = self._prompt_editor.toPlainText() if self._prompt_editor else ""

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

            save_custom_prompts(data)
            self._set_prompt_status("✓ Saved", "success")

        except Exception as e:
            logger.error(f"Prompt speichern fehlgeschlagen: {e}")
            self._set_prompt_status(f"Error: {e}", "error")

    def _reset_prompt_to_default(self):
        """Setzt aktuellen Prompt auf Default zurück."""
        try:
            from utils.custom_prompts import get_defaults, format_app_mappings

            context = self._prompt_context_combo.currentText() if self._prompt_context_combo else "default"
            defaults = get_defaults()

            if context == "voice_commands":
                text = defaults["voice_commands"]["instruction"]
            elif context == "app_mappings":
                text = format_app_mappings(defaults["app_contexts"])
            else:
                text = defaults["prompts"].get(context, {}).get("prompt", "")

            if self._prompt_editor:
                self._prompt_editor.setPlainText(text)
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
                self._prompts_cache[self._current_prompt_context] = self._prompt_editor.toPlainText()

            # Nichts zu speichern?
            if not self._prompts_cache:
                return

            from utils.custom_prompts import (
                load_custom_prompts,
                save_custom_prompts,
                parse_app_mappings,
                get_defaults,
            )

            # Aktuelle Daten laden
            data = load_custom_prompts()
            defaults = get_defaults()

            # Alle gecachten Prompts speichern
            for context, text in self._prompts_cache.items():
                # Nur speichern wenn geändert (nicht Default)
                if context == "voice_commands":
                    default_text = defaults["voice_commands"]["instruction"]
                    if text != default_text:
                        data["voice_commands"] = {"instruction": text}
                elif context == "app_mappings":
                    data["app_contexts"] = parse_app_mappings(text)
                else:
                    default_text = defaults["prompts"].get(context, {}).get("prompt", "")
                    if text != default_text:
                        if "prompts" not in data:
                            data["prompts"] = {}
                        data["prompts"][context] = {"prompt": text}

            save_custom_prompts(data)
            logger.info(f"Prompts gespeichert: {list(self._prompts_cache.keys())}")

        except Exception as e:
            logger.error(f"Prompts speichern fehlgeschlagen: {e}")

    def _toggle_logs_auto_refresh(self, state: int):
        """Schaltet Auto-Refresh für Logs ein/aus."""
        if hasattr(self, "_logs_refresh_timer"):
            if state:
                self._logs_refresh_timer.start(2000)  # Alle 2 Sekunden
            else:
                self._logs_refresh_timer.stop()

    def _switch_logs_view(self, index: int):
        """Wechselt zwischen Logs und Transcripts Ansicht."""
        if hasattr(self, "_logs_stack"):
            self._logs_stack.setCurrentIndex(index)
            if index == 1:  # Transcripts
                self._refresh_transcripts()

    def _refresh_transcripts(self):
        """Aktualisiert Transcripts-Anzeige."""
        try:
            from utils.history import get_recent_transcripts

            entries = get_recent_transcripts(50)  # Letzte 50 Einträge
            if not entries:
                if self._transcripts_viewer:
                    self._transcripts_viewer.setPlainText("No transcripts yet.")
                if hasattr(self, "_transcripts_status"):
                    self._transcripts_status.setText("0 entries")
                return

            # Format entries
            lines = []
            for entry in reversed(entries):  # Älteste zuerst
                ts = entry.get("timestamp", "")[:19].replace("T", " ")
                text = entry.get("text", "")
                mode = entry.get("mode", "")
                refined = "✨" if entry.get("refined") else ""
                lines.append(f"[{ts}] {refined}{text}")
                if mode:
                    lines[-1] = f"[{ts}] ({mode}) {refined}{text}"

            if self._transcripts_viewer:
                self._transcripts_viewer.setPlainText("\n\n".join(lines))
                # Scroll to bottom
                scrollbar = self._transcripts_viewer.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())

            if hasattr(self, "_transcripts_status"):
                self._transcripts_status.setText(f"{len(entries)} entries")

        except Exception as e:
            logger.error(f"Transcripts laden fehlgeschlagen: {e}")
            if self._transcripts_viewer:
                self._transcripts_viewer.setPlainText(f"Error: {e}")

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
        try:
            # Versuche aus CHANGELOG.md zu lesen
            from pathlib import Path
            changelog = Path(__file__).parent.parent / "CHANGELOG.md"
            if changelog.exists():
                for line in changelog.read_text(encoding="utf-8").split("\n"):
                    if line.startswith("## [") and "]" in line:
                        # Format: ## [1.2.3] - 2024-01-01
                        version = line.split("[")[1].split("]")[0]
                        return version
        except Exception:
            pass
        return "1.1.1"  # Fallback

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
                    self._vocab_status.setStyleSheet(f"color: {COLORS['text_secondary']};")
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
                    self._vocab_status.setText(f"✓ Saved ({len(keywords)} keywords) - ⚠ " + "; ".join(warnings))
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
            if LOG_FILE.exists() and self._logs_viewer:
                lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").split("\n")
                # Letzte 100 Zeilen
                self._logs_viewer.setPlainText("\n".join(lines[-100:]))
        except Exception as e:
            logger.error(f"Logs laden fehlgeschlagen: {e}")

    def _open_logs_folder(self):
        """Öffnet Logs-Ordner im Explorer."""
        try:
            import subprocess
            from config import LOG_FILE
            subprocess.run(["explorer", "/select,", str(LOG_FILE)], check=False)
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
        backend = get_env_setting("PULSESCRIBE_LOCAL_BACKEND") or "whisper"
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
            self._streaming_checkbox.setChecked(streaming != "false")

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
        without_ts = get_env_setting("PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS") or "default"
        if hasattr(self, "_without_timestamps_combo") and self._without_timestamps_combo:
            idx = self._without_timestamps_combo.findText(without_ts)
            if idx >= 0:
                self._without_timestamps_combo.setCurrentIndex(idx)

        # Faster-Whisper: VAD Filter
        vad = get_env_setting("PULSESCRIBE_LOCAL_VAD_FILTER") or "default"
        if hasattr(self, "_vad_filter_combo") and self._vad_filter_combo:
            idx = self._vad_filter_combo.findText(vad)
            if idx >= 0:
                self._vad_filter_combo.setCurrentIndex(idx)

        # Faster-Whisper: FP16
        fp16 = get_env_setting("PULSESCRIBE_LOCAL_FP16") or "default"
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
            self._refine_checkbox.setChecked(refine == "true")

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
            self._overlay_checkbox.setChecked(overlay != "false")

        # RTF Display
        rtf = get_env_setting("PULSESCRIBE_SHOW_RTF")
        if self._rtf_checkbox:
            self._rtf_checkbox.setChecked(rtf == "true")

        # Clipboard Restore
        clipboard_restore = get_env_setting("PULSESCRIBE_CLIPBOARD_RESTORE")
        if self._clipboard_restore_checkbox:
            self._clipboard_restore_checkbox.setChecked(clipboard_restore == "true")

        # Hotkeys
        toggle = get_env_setting("PULSESCRIBE_TOGGLE_HOTKEY") or ""
        if hasattr(self, "_toggle_hotkey_field") and self._toggle_hotkey_field:
            self._toggle_hotkey_field.setText(toggle)

        hold = get_env_setting("PULSESCRIBE_HOLD_HOTKEY") or ""
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

    def _save_settings(self):
        """Speichert alle Settings."""
        try:
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
                backend = self._local_backend_combo.currentText()
                if backend == "whisper":
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
            if hasattr(self, "_without_timestamps_combo") and self._without_timestamps_combo:
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
                    remove_env_setting("PULSESCRIBE_LOCAL_FP16")
                else:
                    save_env_setting("PULSESCRIBE_LOCAL_FP16", fp16)

            # Advanced: Lightning Batch Size
            if hasattr(self, "_lightning_batch_slider") and self._lightning_batch_slider:
                batch_size = self._lightning_batch_slider.value()
                if batch_size == 12:
                    remove_env_setting("PULSESCRIBE_LIGHTNING_BATCH_SIZE")  # Default
                else:
                    save_env_setting("PULSESCRIBE_LIGHTNING_BATCH_SIZE", str(batch_size))

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
                    "true" if self._refine_checkbox.isChecked() else "false"
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
                toggle = self._toggle_hotkey_field.text().strip()
                if toggle:
                    save_env_setting("PULSESCRIBE_TOGGLE_HOTKEY", toggle)
                else:
                    remove_env_setting("PULSESCRIBE_TOGGLE_HOTKEY")

            if hasattr(self, "_hold_hotkey_field") and self._hold_hotkey_field:
                hold = self._hold_hotkey_field.text().strip()
                if hold:
                    save_env_setting("PULSESCRIBE_HOLD_HOTKEY", hold)
                else:
                    remove_env_setting("PULSESCRIBE_HOLD_HOTKEY")

            # API Keys
            for env_key, field in self._api_fields.items():
                value = field.text().strip()
                if value:
                    save_api_key(env_key, value)
                    status = self._api_status.get(env_key)
                    if status:
                        status.setText("✓")
                        status.setStyleSheet(f"color: {COLORS['success']};")

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
                "mode": "local",
                "local_backend": "faster",
                "local_model": "turbo",
                "device": "cuda",
                "compute_type": "float16",
                "vad_filter": "true",
                "without_timestamps": "true",
            },
            "cpu_fast": {
                "mode": "local",
                "local_backend": "faster",
                "local_model": "turbo",
                "device": "cpu",
                "compute_type": "int8",
                "cpu_threads": "0",
                "num_workers": "1",
                "vad_filter": "true",
                "without_timestamps": "true",
            },
            "cpu_quality": {
                "mode": "local",
                "local_backend": "faster",
                "local_model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "beam_size": "5",
            },
        }

        values = presets.get(preset)
        if not values:
            return

        # UI-Felder aktualisieren
        if self._mode_combo:
            idx = self._mode_combo.findText(values.get("mode", "local"))
            if idx >= 0:
                self._mode_combo.setCurrentIndex(idx)
                self._on_mode_changed("local")  # Sichtbarkeit aktualisieren

        if self._local_backend_combo:
            idx = self._local_backend_combo.findText(values.get("local_backend", "faster"))
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
            idx = self._compute_type_combo.findText(values.get("compute_type", "default"))
            if idx >= 0:
                self._compute_type_combo.setCurrentIndex(idx)

        if hasattr(self, "_beam_size_field") and self._beam_size_field:
            self._beam_size_field.setText(values.get("beam_size", ""))

        if hasattr(self, "_cpu_threads_field") and self._cpu_threads_field:
            self._cpu_threads_field.setText(values.get("cpu_threads", ""))

        if hasattr(self, "_num_workers_field") and self._num_workers_field:
            self._num_workers_field.setText(values.get("num_workers", ""))

        if hasattr(self, "_vad_filter_combo") and self._vad_filter_combo:
            idx = self._vad_filter_combo.findText(values.get("vad_filter", "default"))
            if idx >= 0:
                self._vad_filter_combo.setCurrentIndex(idx)

        if hasattr(self, "_without_timestamps_combo") and self._without_timestamps_combo:
            idx = self._without_timestamps_combo.findText(values.get("without_timestamps", "default"))
            if idx >= 0:
                self._without_timestamps_combo.setCurrentIndex(idx)

        # Feedback
        if hasattr(self, "_preset_status") and self._preset_status:
            self._preset_status.setText(f"✓ '{preset}' preset applied — click 'Save & Apply' to persist.")
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
            logger.warning(f"Reload-Signal konnte nicht geschrieben werden: {e} - "
                          "Daemon wird Änderungen erst nach Neustart übernehmen")

    def _show_save_feedback(self):
        """Zeigt visuelles Feedback nach erfolgreichem Speichern."""
        if hasattr(self, "_save_btn") and self._save_btn:
            self._save_btn.setText("✓ Saved!")
            self._save_btn.setStyleSheet(f"""
                QPushButton#primary {{
                    background-color: {COLORS['success']};
                    border-color: {COLORS['success']};
                }}
            """)
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

    def closeEvent(self, event):
        """Handler für Fenster schließen."""
        # Als erstes: Signal-Emission verhindern
        self._is_closed = True

        # Auto-Refresh Timer stoppen
        if hasattr(self, "_logs_refresh_timer"):
            self._logs_refresh_timer.stop()

        # pynput Listener und Keyboard Grab stoppen falls Recording aktiv
        if hasattr(self, "_recording_hotkey_for") and self._recording_hotkey_for:
            self._stop_pynput_listener()
            self._recording_hotkey_for = None

        self.closed.emit()
        super().closeEvent(event)


# =============================================================================
# Standalone Test
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SettingsWindow()
    window.show()
    sys.exit(app.exec())
