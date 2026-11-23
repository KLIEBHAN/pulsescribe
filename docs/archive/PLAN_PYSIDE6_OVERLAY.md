# Migrationsplan: Windows Overlay von Tkinter zu PySide6

> **Status:** ✅ Implementiert (2025-12-22)
> **Ergebnis:** GPU-beschleunigtes PySide6-Overlay mit Tkinter-Fallback

## Übersicht

Migration des Windows-Overlays (`ui/overlay_windows.py`) von Tkinter zu PySide6 für:
- GPU-beschleunigte Animationen
- Echten Blur-Hintergrund (Windows 10+)
- Präzisere Timer
- Glattere Wellenanimation

---

## 1. Architektur-Vergleich

### Aktuell (Tkinter)

```
WindowsOverlayController
├── tk.Tk (Root Window)
├── tk.Canvas (Bars + Background)
├── tk.Label (Text)
├── queue.Queue (Thread-Safety)
└── after() Timer (Animation Loop)
```

**Probleme:**
- `canvas.delete("bars")` + Neuzeichnen pro Frame (90 Operationen/Frame)
- Software-Rendering (CPU)
- Ungenaue Timer
- Kein nativer Blur

### Neu (PySide6)

```
PySide6OverlayController
├── QWidget (Frameless, Translucent)
│   ├── setAttribute(WA_TranslucentBackground)
│   └── setWindowFlags(FramelessWindowHint | WindowStaysOnTopHint)
├── Custom paintEvent() (QPainter)
│   ├── Blur-Background (optional: Windows Composition API)
│   └── Pill-Bars (QPainterPath mit Antialiasing)
├── QLabel (Text)
├── QTimer (Animation Loop, präzise)
└── Signals/Slots (Thread-Safety)
```

**Vorteile:**
- Persistente Geometrie, nur Repaint
- Hardware-beschleunigt (je nach Backend)
- Präzise 16ms Timer
- Native Windows-Integration möglich

---

## 2. Abhängigkeiten

### Neue Dependency

```
# requirements.txt (Windows-spezifisch)
PySide6>=6.6.0
```

**Größe:** ~80MB (kann mit `--only-binary` optimiert werden)

### Optionale Blur-Unterstützung

```python
# Für Windows 10+ Acrylic/Mica Blur
import ctypes
from ctypes import wintypes
```

---

## 3. Klassen-Design

### 3.1 Hauptklasse: `PySide6OverlayWidget`

```python
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QRectF, Property
from PySide6.QtGui import QPainter, QPainterPath, QColor, QFont, QBrush

class PySide6OverlayWidget(QWidget):
    """GPU-beschleunigtes Overlay für Windows."""

    # Signals für Thread-Safety
    state_changed = Signal(str, str)  # state, text
    level_changed = Signal(float)     # audio_level

    def __init__(self):
        super().__init__()
        self._setup_window()
        self._setup_ui()
        self._setup_animation()

        # Verbinde Signals zu Slots (thread-safe)
        self.state_changed.connect(self._on_state_changed)
        self.level_changed.connect(self._on_level_changed)
```

### 3.2 Window-Setup

```python
def _setup_window(self):
    # Frameless + Transparent + Always on Top
    self.setWindowFlags(
        Qt.FramelessWindowHint |
        Qt.WindowStaysOnTopHint |
        Qt.Tool  # Nicht in Taskbar
    )
    self.setAttribute(Qt.WA_TranslucentBackground)
    self.setAttribute(Qt.WA_ShowWithoutActivating)

    # Größe und Position
    self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
    self._center_on_screen()

    # Optional: Windows Blur aktivieren
    self._enable_blur()
```

### 3.3 Custom Painting

```python
def paintEvent(self, event):
    painter = QPainter(self)
    painter.setRenderHint(QPainter.Antialiasing)

    # Hintergrund (abgerundetes Rechteck)
    self._draw_background(painter)

    # Bars
    self._draw_bars(painter)

    painter.end()

def _draw_background(self, painter: QPainter):
    path = QPainterPath()
    rect = QRectF(0, 0, self.width(), self.height())
    path.addRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)

    painter.fillPath(path, QColor(26, 26, 26, 242))  # #1A1A1A mit Alpha

def _draw_bars(self, painter: QPainter):
    color = self._get_state_color()
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.NoPen)

    for i in range(BAR_COUNT):
        height = self._bar_heights[i]
        x = self._bar_x_positions[i]

        # Pill-Form als QPainterPath
        path = self._create_pill_path(x, height)
        painter.drawPath(path)
```

### 3.4 Animation mit QTimer

```python
def _setup_animation(self):
    self._animation_timer = QTimer(self)
    self._animation_timer.timeout.connect(self._animate_frame)
    self._animation_timer.setTimerType(Qt.PreciseTimer)  # Präzise!

def _start_animation(self):
    if not self._animation_timer.isActive():
        self._animation_start = time.perf_counter()
        self._animation_timer.start(16)  # ~60 FPS

def _stop_animation(self):
    self._animation_timer.stop()

@Slot()
def _animate_frame(self):
    t = time.perf_counter() - self._animation_start

    # Smoothing + AGC (wie bisher)
    self._update_smoothed_level()

    # Bar-Höhen berechnen
    for i in range(BAR_COUNT):
        self._bar_heights[i] = self._calculate_bar_height(i, t)

    # Nur ein Repaint-Request (effizient!)
    self.update()
```

### 3.5 Thread-Safety via Signals

```python
# Public API (von Worker-Threads aufrufbar)
def update_state(self, state: str, text: str | None = None):
    """Thread-safe State-Update."""
    self.state_changed.emit(state, text or "")

def update_audio_level(self, level: float):
    """Thread-safe Level-Update."""
    self.level_changed.emit(level)

# Slots (laufen im Main-Thread)
@Slot(str, str)
def _on_state_changed(self, state: str, text: str):
    self._state = state
    self._text = text
    self._animation_start = time.perf_counter()

    if state == "IDLE":
        self.hide()
        self._stop_animation()
    else:
        self.show()
        self._start_animation()

@Slot(float)
def _on_level_changed(self, level: float):
    self._audio_level = level
```

---

## 4. Windows Blur (Optional, Windows 10+)

### 4.1 Acrylic Blur via DWM

```python
def _enable_blur(self):
    """Aktiviert Windows Acrylic Blur (Windows 10 1803+)."""
    if sys.platform != "win32":
        return

    try:
        import ctypes
        from ctypes import wintypes

        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t),
            ]

        # ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
        accent = ACCENT_POLICY()
        accent.AccentState = 4
        accent.GradientColor = 0x99000000  # ABGR: Semi-transparent black

        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)

        hwnd = int(self.winId())
        ctypes.windll.user32.SetWindowCompositionAttribute(
            hwnd, ctypes.byref(data)
        )
        logger.debug("Windows Acrylic Blur aktiviert")
    except Exception as e:
        logger.debug(f"Blur nicht verfügbar: {e}")
```

---

## 5. Controller-Wrapper

Für Kompatibilität mit bestehendem Code:

```python
class PySide6OverlayController:
    """Drop-in Replacement für WindowsOverlayController."""

    def __init__(self, interim_file: Path | None = None):
        self._app: QApplication | None = None
        self._widget: PySide6OverlayWidget | None = None
        self._interim_file = interim_file
        self._running = False

    def run(self):
        """Start Qt Event Loop (call from dedicated thread)."""
        self._app = QApplication.instance() or QApplication([])
        self._widget = PySide6OverlayWidget()

        if self._interim_file:
            self._setup_interim_polling()

        self._running = True
        self._app.exec()

    def stop(self):
        self._running = False
        if self._app:
            self._app.quit()

    def update_state(self, state: str, text: str | None = None):
        if self._widget:
            self._widget.update_state(state, text)

    def update_audio_level(self, level: float):
        if self._widget:
            self._widget.update_audio_level(level)

    def update_interim_text(self, text: str):
        if self._widget:
            self._widget.update_state("RECORDING", text)
```

---

## 6. Dateistruktur

```
ui/
├── overlay.py              # macOS (unverändert)
├── overlay_windows.py      # Tkinter (deprecated, Fallback)
├── overlay_pyside6.py      # NEU: PySide6-Implementation
└── __init__.py             # Plattform-Auswahl
```

### `ui/__init__.py` Update

```python
import sys

if sys.platform == "darwin":
    from .overlay import OverlayController, SoundWaveView
elif sys.platform == "win32":
    try:
        from .overlay_pyside6 import PySide6OverlayController as WindowsOverlayController
    except ImportError:
        # Fallback auf Tkinter wenn PySide6 nicht installiert
        from .overlay_windows import WindowsOverlayController
```

---

## 7. Animations-Logik (Portierung)

Die bestehende Animations-Logik wird 1:1 übernommen:

| Komponente | Tkinter | PySide6 |
|------------|---------|---------|
| Traveling Wave | `_calc_recording_height()` | Identisch |
| Gaussian Envelope | `_gaussian()` | Identisch |
| AGC | `_update_agc()` | Identisch |
| Smoothing | `SMOOTHING_ALPHA_*` | Identisch |
| Height Factors | `_HEIGHT_FACTORS` | Identisch |

### Neue Smoothing-Ebene (wie macOS)

```python
# Zusätzliches Level-Smoothing (macOS hat das)
LEVEL_SMOOTHING_RISE = 0.30
LEVEL_SMOOTHING_FALL = 0.10

def _update_smoothed_level(self):
    target = self._audio_level
    alpha = LEVEL_SMOOTHING_RISE if target > self._smoothed_level else LEVEL_SMOOTHING_FALL
    self._smoothed_level += alpha * (target - self._smoothed_level)
```

---

## 8. Implementierungs-Schritte

### Phase 1: Grundgerüst (Schritt 1-3)

1. **Datei erstellen**: `ui/overlay_pyside6.py`
2. **Konstanten portieren**: Farben, Größen, Animation-Parameter
3. **`PySide6OverlayWidget` Basis**: Window-Setup, paintEvent-Stub

### Phase 2: Rendering (Schritt 4-6)

4. **Background zeichnen**: Abgerundetes Rechteck mit QPainterPath
5. **Bars zeichnen**: Pill-Form, Farben pro State
6. **Text-Label**: QLabel mit Styling

### Phase 3: Animation (Schritt 7-9)

7. **QTimer-Loop**: 60 FPS mit PreciseTimer
8. **Animations-Logik**: Traveling Wave, Envelope, AGC portieren
9. **State-Machine**: IDLE/LISTENING/RECORDING/etc.

### Phase 4: Integration (Schritt 10-12)

10. **Controller-Wrapper**: API-Kompatibilität
11. **Thread-Safety**: Signals/Slots für cross-thread Updates
12. **`__init__.py` Update**: Plattform-Auswahl

### Phase 5: Polish (Schritt 13-15)

13. **Windows Blur**: Optional Acrylic-Effekt
14. **Zweite Smoothing-Ebene**: Level-Smoothing wie macOS
15. **Testing & Feintuning**: Parameter-Anpassung

---

## 9. Testplan

### Unit Tests

```python
def test_overlay_state_transitions():
    """Teste alle State-Übergänge."""

def test_overlay_thread_safety():
    """Teste Updates von Worker-Threads."""

def test_animation_timing():
    """Verifiziere 60 FPS Timing."""
```

### Manuelle Tests

- [x] Overlay erscheint bei Hotkey-Press
- [x] Waveform reagiert auf Audio-Level
- [x] Interim-Text wird angezeigt
- [x] Smooth Animation ohne Ruckeln
- [x] Korrekte Farben pro State
- [x] Blur funktioniert (Windows 10+)
- [x] Graceful Shutdown

---

## 10. Risiken & Mitigationen

| Risiko | Mitigation |
|--------|------------|
| PySide6 nicht installiert | Fallback auf Tkinter |
| Blur nicht verfügbar (Win7/8) | Graceful Degradation zu solidem Hintergrund |
| Qt Event Loop Konflikte | Dedizierter Thread wie bisher |
| Größere Dependency | Optional in requirements-windows.txt |

---

## 11. Erfolgskriterien

- [x] Animation fühlt sich so smooth an wie macOS
- [x] Keine sichtbaren Frame-Drops
- [x] Blur-Hintergrund (wenn verfügbar)
- [x] API-kompatibel (kein Code-Change in `pulsescribe_windows.py` nötig)
- [x] Fallback auf Tkinter funktioniert
