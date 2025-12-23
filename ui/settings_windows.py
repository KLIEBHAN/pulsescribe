"""Settings Window für PulseScribe (Windows).

PySide6-basiertes Settings-Fenster mit Dark Theme.
Portiert von ui/welcome.py (macOS AppKit).
"""

import logging
import sys
from typing import Callable

from PySide6.QtCore import Qt, Signal, Slot
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
    remove_env_setting,
    save_api_key,
    save_env_setting,
)

logger = logging.getLogger("pulsescribe.settings")

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
        self._clipboard_restore_checkbox: QCheckBox | None = None
        self._api_fields: dict[str, QLineEdit] = {}
        self._api_status: dict[str, QLabel] = {}

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
        header.setFixedHeight(80)
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
        """Erstellt den Footer mit Save-Button."""
        footer = QWidget()
        footer.setFixedHeight(60)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(20, 10, 20, 20)

        layout.addStretch()

        save_btn = QPushButton("Save && Apply")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn)

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

        # Recording State
        self._recording_hotkey_for: str | None = None

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
        """Logs-Tab: Log-Viewer."""
        from PySide6.QtCore import QTimer

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(CARD_SPACING)

        # Logs Card
        card, card_layout = create_card(
            "📋 Logs",
            "View application logs for debugging."
        )

        self._logs_viewer = QPlainTextEdit()
        self._logs_viewer.setReadOnly(True)
        self._logs_viewer.setMinimumHeight(300)
        self._logs_viewer.setPlaceholderText("Logs will appear here...")
        card_layout.addWidget(self._logs_viewer)

        # Buttons
        btn_layout = QHBoxLayout()

        self._auto_refresh_checkbox = QCheckBox("Auto-refresh")
        self._auto_refresh_checkbox.stateChanged.connect(self._toggle_logs_auto_refresh)
        btn_layout.addWidget(self._auto_refresh_checkbox)

        btn_layout.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_logs)
        btn_layout.addWidget(refresh_btn)

        open_btn = QPushButton("Open in Explorer")
        open_btn.clicked.connect(self._open_logs_folder)
        btn_layout.addWidget(open_btn)

        card_layout.addLayout(btn_layout)

        layout.addWidget(card)
        layout.addStretch()

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

        # Button-Text ändern
        if kind == "toggle" and hasattr(self, "_toggle_record_btn"):
            self._toggle_record_btn.setText("Press key...")
            self._toggle_record_btn.setStyleSheet(f"background-color: {COLORS['accent']};")
        elif kind == "hold" and hasattr(self, "_hold_record_btn"):
            self._hold_record_btn.setText("Press key...")
            self._hold_record_btn.setStyleSheet(f"background-color: {COLORS['accent']};")

        self._set_hotkey_status("Press your hotkey combination, then press Enter to confirm...", "warning")

        # Key-Capture aktivieren
        self.setFocus()
        self.grabKeyboard()

    def _stop_hotkey_recording(self, hotkey_str: str | None = None):
        """Beendet Hotkey-Recording."""
        kind = self._recording_hotkey_for
        self._recording_hotkey_for = None

        # Keyboard freigeben
        self.releaseKeyboard()

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
            from PySide6.QtCore import Qt as QtCore

            # Escape = Abbrechen
            if event.key() == QtCore.Key.Key_Escape:
                self._stop_hotkey_recording(None)
                return

            # Enter = Bestätigen
            if event.key() in (QtCore.Key.Key_Return, QtCore.Key.Key_Enter):
                # Aktuelles Feld auslesen
                if self._recording_hotkey_for == "toggle" and self._toggle_hotkey_field:
                    hotkey = self._toggle_hotkey_field.text()
                elif self._recording_hotkey_for == "hold" and self._hold_hotkey_field:
                    hotkey = self._hold_hotkey_field.text()
                else:
                    hotkey = None
                self._stop_hotkey_recording(hotkey)
                return

            # Hotkey bauen
            parts = []
            modifiers = event.modifiers()
            if modifiers & QtCore.KeyboardModifier.ControlModifier:
                parts.append("ctrl")
            if modifiers & QtCore.KeyboardModifier.AltModifier:
                parts.append("alt")
            if modifiers & QtCore.KeyboardModifier.ShiftModifier:
                parts.append("shift")
            if modifiers & QtCore.KeyboardModifier.MetaModifier:
                parts.append("win")

            # Key-Name
            key = event.key()
            key_name = self._qt_key_to_string(key)
            if key_name and key_name not in ("ctrl", "alt", "shift", "win", "meta"):
                parts.append(key_name)

            hotkey_str = "+".join(parts) if parts else ""

            # In Feld anzeigen
            if self._recording_hotkey_for == "toggle" and self._toggle_hotkey_field:
                self._toggle_hotkey_field.setText(hotkey_str)
            elif self._recording_hotkey_for == "hold" and self._hold_hotkey_field:
                self._hold_hotkey_field.setText(hotkey_str)

            event.accept()
            return

        super().keyPressEvent(event)

    def _qt_key_to_string(self, key: int) -> str:
        """Konvertiert Qt Key zu String."""
        from PySide6.QtCore import Qt as QtCore

        # Spezielle Tasten
        special_keys = {
            QtCore.Key.Key_Space: "space",
            QtCore.Key.Key_Tab: "tab",
            QtCore.Key.Key_Backspace: "backspace",
            QtCore.Key.Key_Delete: "delete",
            QtCore.Key.Key_Home: "home",
            QtCore.Key.Key_End: "end",
            QtCore.Key.Key_PageUp: "pageup",
            QtCore.Key.Key_PageDown: "pagedown",
            QtCore.Key.Key_Up: "up",
            QtCore.Key.Key_Down: "down",
            QtCore.Key.Key_Left: "left",
            QtCore.Key.Key_Right: "right",
            QtCore.Key.Key_Control: "ctrl",
            QtCore.Key.Key_Alt: "alt",
            QtCore.Key.Key_Shift: "shift",
            QtCore.Key.Key_Meta: "win",
        }
        if key in special_keys:
            return special_keys[key]

        # F-Tasten
        if QtCore.Key.Key_F1 <= key <= QtCore.Key.Key_F24:
            return f"f{key - QtCore.Key.Key_F1 + 1}"

        # Buchstaben
        if QtCore.Key.Key_A <= key <= QtCore.Key.Key_Z:
            return chr(ord('a') + key - QtCore.Key.Key_A)

        # Zahlen
        if QtCore.Key.Key_0 <= key <= QtCore.Key.Key_9:
            return chr(ord('0') + key - QtCore.Key.Key_0)

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

        # Tray Icon (Clipboard Restore)
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

            logger.info("Settings gespeichert")
            self.settings_changed.emit()

            # Callback aufrufen (für Daemon-Reload)
            if self._on_settings_changed_callback:
                self._on_settings_changed_callback()

        except Exception as e:
            logger.error(f"Settings speichern fehlgeschlagen: {e}")

    # =========================================================================
    # Public API
    # =========================================================================

    def set_on_settings_changed(self, callback: Callable[[], None]):
        """Setzt Callback für Settings-Änderungen."""
        self._on_settings_changed_callback = callback

    def closeEvent(self, event):
        """Handler für Fenster schließen."""
        # Auto-Refresh Timer stoppen
        if hasattr(self, "_logs_refresh_timer"):
            self._logs_refresh_timer.stop()

        # Keyboard Grab freigeben falls noch aktiv
        if hasattr(self, "_recording_hotkey_for") and self._recording_hotkey_for:
            self.releaseKeyboard()
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
