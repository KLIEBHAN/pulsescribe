# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for PulseScribe Windows.

Build: pyinstaller build_windows.spec --clean
Output: dist/PulseScribe/PulseScribe.exe

Requirements:
    pip install -r requirements.txt
    pip install pyinstaller PySide6

Core: pystray pillow pynput sounddevice soundfile numpy deepgram-sdk groq openai pyperclip python-dotenv
Windows: pywin32 psutil
Overlay: PySide6 (GPU-accelerated, recommended) or tkinter (fallback, built-in)
"""

block_cipher = None

from PyInstaller.utils.hooks import collect_all
import os
import pathlib
import re


def _dedupe(items):
    return list(dict.fromkeys(items))


def _read_app_version() -> str:
    env_version = (os.getenv("PULSESCRIBE_VERSION") or os.getenv("VERSION") or "").strip()
    if env_version:
        return env_version

    spec_dir = pathlib.Path(SPECPATH) if 'SPECPATH' in dir() else pathlib.Path.cwd()
    pyproject = spec_dir / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return "1.0.0"

    match = re.search(r'^\s*version\s*=\s*"([^"]+)"\s*$', text, flags=re.MULTILINE)
    return match.group(1) if match else "1.0.0"


APP_VERSION = _read_app_version()

# Paths to modules and resources
binaries = []
datas = [
    ('config.py', '.'),
    ('utils', 'utils'),
    ('providers', 'providers'),
    ('refine', 'refine'),
    ('whisper_platform', 'whisper_platform'),
    ('ui', 'ui'),      # Overlay modules (PySide6, Tkinter, animation)
    ('audio', 'audio'), # Recording module
]

# Hidden imports that PyInstaller doesn't detect automatically
hiddenimports = [
    # === Tray & Hotkey ===
    'pystray',
    'pystray._win32',
    'PIL',
    'PIL.Image',
    'pynput',
    'pynput.keyboard',
    'pynput.keyboard._win32',
    'pynput.mouse._win32',

    # === Audio ===
    'sounddevice',
    'soundfile',
    'numpy',

    # === API SDKs ===
    'deepgram',
    'httpx',
    'websockets',
    'openai',
    'groq',

    # === App Detection ===
    'win32gui',
    'win32process',
    'psutil',

    # === FileWatcher ===
    'watchdog',
    'watchdog.observers',
    'watchdog.events',

    # === Utils ===
    'pyperclip',
    'dotenv',

    # === UI/Overlay ===
    'ui',
    'ui.overlay_pyside6',
    'ui.overlay_windows',
    'ui.animation',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'tkinter',

    # === Audio ===
    'audio',
    'audio.recording',

    # === Local Whisper (faster-whisper) ===
    'faster_whisper',
    'ctranslate2',
    'tokenizers',
    'huggingface_hub',
]

hiddenimports = _dedupe(hiddenimports)

# Exclude unnecessary modules (reduces size and build time)
excludes = [
    # GUI Frameworks (not needed - except PySide6/tkinter for overlay)
    'PyQt5',
    'PyQt6',
    'PySide2',
    # NOTE: PySide6 and tkinter are NOT excluded - used for overlay!
    'wx',

    # Data Science (not needed)
    'matplotlib',
    'pandas',
    'sklearn',

    # Testing Frameworks
    'pytest',
    'unittest',

    # macOS-specific (not available on Windows)
    'objc',
    'Foundation',
    'AppKit',
    'Quartz',
    'AVFoundation',
    'CoreMedia',
    'CoreAudio',
    'CoreFoundation',
    'rumps',
    'quickmachotkey',

    # CLI (not needed for Windows daemon)
    'typer',
    'click',

    # Dev Tools
    'IPython',
    'jupyter',

    # Misc
    'curses',
]

a = Analysis(
    ['pulsescribe_windows.py'],
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

# Check for Windows icon (optional)
icon_path = pathlib.Path(SPECPATH) / 'assets' / 'icon.ico' if 'SPECPATH' in dir() else pathlib.Path('assets/icon.ico')
icon_file = str(icon_path) if icon_path.exists() else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PulseScribe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No console window (tray app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=icon_file,  # Windows icon (if available)
)

# COLLECT for onedir mode
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PulseScribe',
)
