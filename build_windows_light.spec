# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for PulseScribe Windows (Light Version).

This is a smaller build WITHOUT local Whisper support.
Use this if you only need API-based transcription (Deepgram, Groq, OpenAI).

Build: pyinstaller build_windows_light.spec --clean
Output: dist/PulseScribe-Light/PulseScribe.exe

For full version with local Whisper support, use build_windows.spec instead.
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

# Collect PySide6 completely (binaries, datas, hiddenimports)
# This is critical - without it, PySide6 won't work in the bundled EXE
try:
    pyside6_datas, pyside6_binaries, pyside6_hiddenimports = collect_all('PySide6')
    print(f"PySide6 collected: {len(pyside6_binaries)} binaries, {len(pyside6_datas)} datas")
except Exception as e:
    print(f"WARNING: PySide6 collect failed: {e}")
    pyside6_datas, pyside6_binaries, pyside6_hiddenimports = [], [], []

# Collect shiboken6 (required C++ bindings for PySide6)
try:
    shiboken6_datas, shiboken6_binaries, shiboken6_hiddenimports = collect_all('shiboken6')
    print(f"shiboken6 collected: {len(shiboken6_binaries)} binaries, {len(shiboken6_datas)} datas")
except Exception as e:
    print(f"WARNING: shiboken6 collect failed: {e}")
    shiboken6_datas, shiboken6_binaries, shiboken6_hiddenimports = [], [], []

# Paths to modules and resources
binaries = pyside6_binaries + shiboken6_binaries
datas = pyside6_datas + shiboken6_datas + [
    ('config.py', '.'),
    ('pyproject.toml', '.'),
    ('utils', 'utils'),
    ('providers', 'providers'),
    ('refine', 'refine'),
    ('whisper_platform', 'whisper_platform'),
    ('ui', 'ui'),
    ('audio', 'audio'),
]

# Hidden imports - LIGHT version (no local Whisper)
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

    # === Utils ===
    'pyperclip',
    'dotenv',

    # === UI/Overlay ===
    'ui',
    'ui.overlay_pyside6',
    'ui.overlay_windows',
    'ui.animation',
    'ui.settings_windows',
    'ui.onboarding_wizard_windows',
    'ui.styles_windows',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'shiboken6',
    'tkinter',

    # === Audio ===
    'audio',
    'audio.recording',

    # NOTE: Local Whisper imports REMOVED for light build
    # 'faster_whisper', 'ctranslate2', 'tokenizers', 'huggingface_hub'
]

hiddenimports = _dedupe(hiddenimports + pyside6_hiddenimports + shiboken6_hiddenimports)

# Excludes - more aggressive for light build
excludes = [
    # === Local ML (NOT needed for API-only) ===
    'torch',
    'torchvision',
    'torchaudio',
    'numba',
    'llvmlite',
    'faster_whisper',
    'ctranslate2',
    'whisper',
    'openai_whisper',
    'transformers',
    'huggingface_hub',
    'tokenizers',
    'safetensors',
    'accelerate',
    'onnxruntime',
    'sympy',
    'networkx',

    # GUI Frameworks (not needed - except PySide6/tkinter for overlay)
    'PyQt5',
    'PyQt6',
    'PySide2',
    'wx',

    # Data Science (not needed)
    'matplotlib',
    'pandas',
    'sklearn',
    'scipy',

    # Testing Frameworks
    'pytest',
    'unittest',
    'coverage',

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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=icon_file,
)

# COLLECT for onedir mode - output to PulseScribe-Light folder
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PulseScribe-Light',
)
