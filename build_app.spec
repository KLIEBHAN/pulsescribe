# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec für PulseScribe.app

Build: pyinstaller build_app.spec
Output: dist/PulseScribe.app

WICHTIG: Accessibility-Berechtigungen und Code-Signing
=======================================================
macOS identifiziert Apps anhand ihrer Signatur. Bei unsignierten Builds:
- Nach JEDEM Neubuild muss die App in Bedienungshilfen NEU hinzugefügt werden
- macOS merkt sich den Hash der Binary, der sich bei jedem Build ändert

Für stabilen Betrieb die App signieren:
    codesign --force --deep --sign - dist/PulseScribe.app

Oder mit Developer ID für Distribution:
    codesign --force --deep --sign "Developer ID Application: Name" dist/PulseScribe.app
"""

block_cipher = None

# PyInstaller Hook helpers for native libs/data
from PyInstaller.utils.hooks import collect_all  # type: ignore
import os
import pathlib
import re

# Build-Variante: SLIM = nur Cloud-Provider, FULL = mit lokalen Whisper-Backends
# Steuerung via ENV: PULSESCRIBE_SLIM_BUILD=true oder --slim Flag in build_app.sh
SLIM_BUILD = os.getenv("PULSESCRIBE_SLIM_BUILD", "false").lower() == "true"


def _dedupe(items):
    return list(dict.fromkeys(items))

def _read_app_version() -> str:
    env_version = (os.getenv("PULSESCRIBE_VERSION") or os.getenv("VERSION") or "").strip()
    if env_version:
        return env_version

    # SPEC_DIR wird von PyInstaller gesetzt, __file__ ist nicht verfügbar in .spec Dateien
    spec_dir = pathlib.Path(SPECPATH) if 'SPECPATH' in dir() else pathlib.Path.cwd()
    pyproject = spec_dir / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return "1.0.0"

    match = re.search(r'^\s*version\s*=\s*"([^"]+)"\s*$', text, flags=re.MULTILINE)
    return match.group(1) if match else "1.0.0"


APP_VERSION = _read_app_version()


# Pfade zu Modulen und Ressourcen
binaries = []
datas = [
    ('config.py', '.'),  # Top-Level Konfiguration
    ('cli', 'cli'),  # CLI Enums (TranscriptionMode, Context, etc.)
    ('ui', 'ui'),
    ('utils', 'utils'),
    ('providers', 'providers'),
    ('refine', 'refine'),
    ('whisper_platform', 'whisper_platform'),
    ('audio', 'audio'),
]

# Hidden imports die PyInstaller nicht automatisch erkennt
hiddenimports = [
    # === Hotkey ===
    'quickmachotkey',
    
    # === PyObjC Frameworks ===
    'objc',
    'Foundation',
    'AppKit',
    'Quartz',
    'AVFoundation',
    'CoreMedia',      # Dependency von AVFoundation
    'CoreAudio',      # Dependency von AVFoundation
    'CoreFoundation',
    
    # === Audio ===
    'sounddevice',
    'soundfile',
    'numpy',
    
    # === UI ===
    'rumps',
    'pynput',
    'pynput.keyboard._darwin',
    'pynput.mouse._darwin',
    
    # === API SDKs ===
    'openai',
    'deepgram',
    'groq',
    'httpx',
    'websockets',
    
    # === CLI ===
    'typer',
    'click',  # Typer dependency

    # === Utils ===
    'pyperclip',
    'dotenv',
    # Some runtime deps (e.g. SciPy via numpy.testing) rely on stdlib unittest.
    'unittest',
]

# === Local backends (nur bei Full-Build) ===
# Slim-Build: ~300 MB (nur Cloud-Provider: Deepgram, OpenAI, Groq)
# Full-Build: ~1 GB (mit lokalen Whisper-Backends: faster-whisper, mlx, lightning)
if not SLIM_BUILD:
    # faster-whisper / CTranslate2
    fw_datas, fw_binaries, fw_hidden = collect_all("faster_whisper")
    ct_datas, ct_binaries, ct_hidden = collect_all("ctranslate2")
    tok_datas, tok_binaries, tok_hidden = collect_all("tokenizers")

    datas += fw_datas + ct_datas + tok_datas
    binaries += fw_binaries + ct_binaries + tok_binaries
    hiddenimports += fw_hidden + ct_hidden + tok_hidden

    # mlx-whisper / MLX (Apple Silicon only)
    try:
        mlxw_datas, mlxw_binaries, mlxw_hidden = collect_all("mlx_whisper")
        mlx_datas, mlx_binaries, mlx_hidden = collect_all("mlx")
        # mlx-whisper depends on SciPy (e.g. for word-timestamp helpers)
        scipy_datas, scipy_binaries, scipy_hidden = collect_all("scipy")
    except Exception:
        mlxw_datas, mlxw_binaries, mlxw_hidden = [], [], []
        mlx_datas, mlx_binaries, mlx_hidden = [], [], []
        scipy_datas, scipy_binaries, scipy_hidden = [], [], []

    datas += mlxw_datas + mlx_datas + scipy_datas
    binaries += mlxw_binaries + mlx_binaries + scipy_binaries
    hiddenimports += mlxw_hidden + mlx_hidden + scipy_hidden

    # lightning-whisper-mlx (~4x faster via batched decoding, Apple Silicon only)
    try:
        lw_datas, lw_binaries, lw_hidden = collect_all("lightning_whisper_mlx")
    except Exception:
        lw_datas, lw_binaries, lw_hidden = [], [], []

    datas += lw_datas
    binaries += lw_binaries
    hiddenimports += lw_hidden
else:
    print("📦 SLIM BUILD: Lokale Whisper-Backends werden übersprungen")

hiddenimports = _dedupe(hiddenimports)

# Nicht benötigte Module ausschließen (reduziert App-Größe)
excludes = [
    # GUI Frameworks (wir nutzen PyObjC direkt)
    'tkinter',
    'PyQt5',
    'PyQt6',
    'PySide2',
    'PySide6',
    'wx',
    
    # Data Science (nicht benötigt)
    'matplotlib',
    'pandas',
    'sklearn',
    
    # Testing
    'pytest',
    # NOTE: Do not exclude Python's stdlib 'unittest' – some runtime deps (e.g. SciPy/numpy.testing)
    # import it and the bundled app would crash with "No module named 'unittest'".
    
    # Dev Tools
    'IPython',
    'jupyter',
    
    # Sonstige
    'curses',
]

a = Analysis(
    ['pulsescribe_daemon.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='pulsescribe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Keine Terminal-Fenster
    disable_windowed_traceback=False,
    argv_emulation=False,  # Nicht nötig für Menubar-App
    target_arch='arm64',  # Apple Silicon (für Universal: 'universal2')
)

# COLLECT für Onedir-Modus (wichtig für .app mit vielen Libraries)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='pulsescribe',
)

app = BUNDLE(
    coll,
    name='PulseScribe.app',
    icon='assets/icon.icns',  # Custom app icon
    bundle_identifier='com.kliebhan.pulsescribe',
    info_plist={
        # Berechtigungen
        'NSMicrophoneUsageDescription': 'PulseScribe benötigt Zugriff auf das Mikrofon für die Spracherkennung.',
        'NSAppleEventsUsageDescription': 'PulseScribe benötigt Zugriff, um Text in andere Apps einzufügen.',

        # App-Verhalten
        'LSUIElement': False,  # App im Dock anzeigen (für CMD+Q Support)
        'LSBackgroundOnly': False,

        # App-Info
        'CFBundleName': 'PulseScribe',
        'CFBundleDisplayName': 'PulseScribe',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,

        # macOS Features
        'NSHighResolutionCapable': True,
        'NSSupportsAutomaticGraphicsSwitching': True,
    },
)
