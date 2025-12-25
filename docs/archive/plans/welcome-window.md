# Plan: PulseScribe Onboarding/Welcome Window

> **Status:** ✅ Implementiert
> **Erstellt:** 2025-12-11

## Übersicht

Erstelle eine native macOS Übersichtsseite (Welcome Window), die:

- Beim **ersten Start** automatisch erscheint
- Bei **jedem Start** kurz sichtbar ist (kann übersprungen werden)
- **Über Menubar** jederzeit aufrufbar ist ("About / Setup")
- Zeigt: Hotkey-Anleitung, Konfig-Status, API-Key-Setup, Features

## Architektur-Entscheidung

**Gewählt: Eigenes NSWindow** (nicht NSAlert)

- NSAlert ist zu limitiert für API-Key-Eingabe und Feature-Liste
- Eigenes Window erlaubt volles UI-Design mit Visual Effects
- Folgt dem bestehenden `OverlayController`-Pattern

## Dateien

| Datei                   | Aktion  | Beschreibung                         |
| ----------------------- | ------- | ------------------------------------ |
| `ui/welcome.py`         | **NEU** | WelcomeController mit NSWindow       |
| `ui/__init__.py`        | Ändern  | Export WelcomeController             |
| `ui/menubar.py`         | Ändern  | "Setup..." Menu-Item hinzufügen      |
| `utils/preferences.py`  | **NEU** | Persistenz für `has_seen_onboarding` |
| `utils/__init__.py`     | Ändern  | Export Preferences                   |
| `pulsescribe_daemon.py` | Ändern  | Welcome-Window beim Start zeigen     |

## UI-Design

```
┌─────────────────────────────────────────────────────────┐
│                    PulseScribe Setup                  ✕ │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  🎤 Welcome to PulseScribe                             │
│                                                         │
│  ─────────────────────────────────────────────────────  │
│                                                         │
│  ⌨️  Hotkey                                             │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Fn/Globe (Hold to record, default)            │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  🔑 API Configuration                                   │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Deepgram API Key (required):                   │   │
│  │  [____________________________________] ✓/✗     │   │
│  │                                        [Save]   │   │
│  │                                                 │   │
│  │  Groq API Key (optional, for LLM refine):      │   │
│  │  [____________________________________] ✓/✗     │   │
│  │                                        [Save]   │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ⚙️  Current Settings                                   │
│  • Refine: ✓ Enabled (groq/gpt-oss-120b)              │
│  • Language: Auto-detect                               │
│  • Provider: Deepgram Streaming                        │
│                                                         │
│  ─────────────────────────────────────────────────────  │
│                                                         │
│  ✨ Features                                            │
│  • Real-time streaming (~300ms latency)                │
│  • LLM post-processing for grammar & punctuation       │
│  • Context-aware: adapts to email/chat/code            │
│  • Voice commands: "new paragraph", "comma", etc.      │
│                                                         │
│  ┌─────────────┐  ┌─────────────────────────────────┐  │
│  │ [ ] Show    │  │       [Start PulseScribe]       │  │
│  │ at startup  │  └─────────────────────────────────┘  │
│  └─────────────┘                                       │
└─────────────────────────────────────────────────────────┘
```

**Größe:** ~500x600 Pixel
**Stil:** NSVisualEffectView (HUD-Material wie Overlay)
**Sprache:** Englisch (konsistent mit Release Notes)

## Implementation Details

### 1. `utils/preferences.py` (NEU)

```python
"""Persistente Einstellungen für PulseScribe."""
import json
from pathlib import Path
from config import USER_CONFIG_DIR

PREFS_FILE = USER_CONFIG_DIR / "preferences.json"

def load_preferences() -> dict:
    """Lädt Preferences aus JSON."""
    if PREFS_FILE.exists():
        return json.loads(PREFS_FILE.read_text())
    return {}

def save_preferences(prefs: dict) -> None:
    """Speichert Preferences als JSON."""
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(prefs, indent=2))

def has_seen_onboarding() -> bool:
    return load_preferences().get("has_seen_onboarding", False)

def set_onboarding_seen(seen: bool = True) -> None:
    prefs = load_preferences()
    prefs["has_seen_onboarding"] = seen
    save_preferences(prefs)

def get_show_welcome_on_startup() -> bool:
    return load_preferences().get("show_welcome_on_startup", True)

def set_show_welcome_on_startup(show: bool) -> None:
    prefs = load_preferences()
    prefs["show_welcome_on_startup"] = show
    save_preferences(prefs)

def save_api_key(key_name: str, value: str) -> None:
    """Speichert/aktualisiert einen API-Key in der .env Datei."""
    env_path = USER_CONFIG_DIR / ".env"

    lines = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key_name}="):
            lines[i] = f"{key_name}={value}"
            found = True
            break

    if not found:
        lines.append(f"{key_name}={value}")

    env_path.write_text("\n".join(lines) + "\n")
```

### 2. `ui/welcome.py` (NEU)

**Kernstruktur:**

```python
class WelcomeController:
    """Welcome/Setup Window für PulseScribe."""

    def __init__(self, hotkey: str, config: dict):
        self.hotkey = hotkey
        self.config = config  # ENV-basierte Konfig
        self._window = None
        self._build_window()

    def _build_window(self):
        # NSWindow mit Titel + Close-Button
        # NSVisualEffectView als Content
        # Subviews für alle Sections
        pass

    def _build_hotkey_section(self, parent, y) -> int:
        # Label + formatierter Hotkey-Badge
        return new_y

    def _build_api_section(self, parent, y) -> int:
        # API-Key Textfelder mit Save-Buttons
        # Status-Indicator (✓/✗)
        return new_y

    def _build_settings_section(self, parent, y) -> int:
        # Aktuelle Einstellungen anzeigen
        return new_y

    def _build_features_section(self, parent, y) -> int:
        # Feature-Liste
        return new_y

    def _save_api_key(self, key_name: str, text_field) -> None:
        # Callback für Save-Button
        from utils.preferences import save_api_key
        value = text_field.stringValue()
        if value:
            save_api_key(key_name, value)
            # Update status indicator

    def show(self) -> None:
        """Zeigt Window (nicht-modal)."""
        self._window.makeKeyAndOrderFront_(None)
        self._window.center()

    def close(self) -> None:
        """Schließt Window und markiert Onboarding als gesehen."""
        from utils.preferences import set_onboarding_seen
        set_onboarding_seen(True)
        self._window.close()
```

### 3. Menubar-Integration (`ui/menubar.py`)

Neuer Menu-Item zwischen "Open Logs" und "Quit":

```python
# In __init__:
setup_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
    "Setup...", "showSetup:", ""
)
setup_item.setTarget_(self._action_handler)
menu.addItem_(setup_item)

# In _MenuActionHandler:
@objc.signature(b"v@:@")
def showSetup_(self, _sender) -> None:
    if self.welcome_callback:
        self.welcome_callback()
```

### 4. Daemon-Integration (`pulsescribe_daemon.py`)

```python
def run(self):
    # ... existing setup ...

    # Welcome Window (einmalig oder wenn aktiviert)
    from utils.preferences import has_seen_onboarding, get_show_welcome_on_startup

    show_welcome = not has_seen_onboarding() or get_show_welcome_on_startup()

    if show_welcome:
        from ui import WelcomeController
        self._welcome = WelcomeController(
            hotkey=self.hotkey,
            config={
                "deepgram_key": bool(os.getenv("DEEPGRAM_API_KEY")),
                "groq_key": bool(os.getenv("GROQ_API_KEY")),
                "refine": self.refine,
                "refine_model": self.refine_model,
                "language": self.language,
                "mode": self.mode,
            }
        )
        self._welcome.show()

    # Pass callback to menubar for "Setup..." item
    self._menubar.set_welcome_callback(lambda: self._welcome.show())

    # ... rest of run() ...
```

## Abhängigkeiten

- Keine neuen Dependencies
- Nutzt existierende PyObjC/AppKit Patterns aus `overlay.py`
- Folgt bestehendem Code-Stil (Type Hints, Docstrings)

## Test-Plan

1. **Erster Start**: Welcome erscheint automatisch
2. **Folgende Starts**: Welcome erscheint wenn Checkbox aktiviert
3. **Menubar**: "Setup..." öffnet Welcome jederzeit
4. **API-Key-Eingabe**: Textfeld → Save → .env wird aktualisiert
5. **API-Status**: Zeigt ✓ wenn Key vorhanden, ✗ wenn nicht
6. **Hotkey-Display**: Zeigt konfigurierten Hotkey korrekt formatiert

## Entscheidungen

1. **API-Key-Eingabe**: ✅ Direkt im Window mit Textfeldern (speichert in `~/.pulsescribe/.env`)
2. **Sprache**: ✅ Englisch (konsistent mit Release Notes)

## Geschätzter Aufwand

- `utils/preferences.py`: ~50 Zeilen
- `ui/welcome.py`: ~300-350 Zeilen
- Änderungen an bestehenden Dateien: ~40 Zeilen
- **Gesamt: ~400 Zeilen**
