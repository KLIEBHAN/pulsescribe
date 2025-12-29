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

import os
import pathlib
import re
import glob


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

# Build variant: API-only (default) or Local (with CUDA Whisper)
BUILD_LOCAL = os.getenv("PULSESCRIBE_BUILD_LOCAL", "0") == "1"
print(f"Build variant: {'Local (CUDA)' if BUILD_LOCAL else 'API-only'}")

# Collect only required PySide6 modules (not the full ~500MB package)
# We only need QtCore, QtGui, QtWidgets for the overlay
from PyInstaller.utils.hooks import collect_data_files, get_package_paths

pyside6_binaries = []
pyside6_datas = []

try:
    _, pyside6_dir = get_package_paths('PySide6')
    _, shiboken6_dir = get_package_paths('shiboken6')

    # Core Qt DLLs needed for QtWidgets app
    qt_dlls = [
        'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
        'Qt6Svg.dll',  # Often needed for icons
    ]
    for dll in qt_dlls:
        dll_path = os.path.join(pyside6_dir, dll)
        if os.path.exists(dll_path):
            pyside6_binaries.append((dll_path, 'PySide6'))

    # PySide6 Python bindings (.pyd files)
    for pyd in ['QtCore', 'QtGui', 'QtWidgets']:
        pattern = os.path.join(pyside6_dir, f'{pyd}*.pyd')
        for f in glob.glob(pattern):
            pyside6_binaries.append((f, 'PySide6'))

    # shiboken6 bindings
    for f in glob.glob(os.path.join(shiboken6_dir, '*.pyd')):
        pyside6_binaries.append((f, 'shiboken6'))
    for f in glob.glob(os.path.join(shiboken6_dir, '*.dll')):
        pyside6_binaries.append((f, 'shiboken6'))

    # Qt plugins (platforms is required for Windows)
    plugins_dir = os.path.join(pyside6_dir, 'plugins')
    if os.path.isdir(plugins_dir):
        for subdir in ['platforms', 'styles', 'imageformats']:
            src = os.path.join(plugins_dir, subdir)
            if os.path.isdir(src):
                pyside6_datas.append((src, f'PySide6/plugins/{subdir}'))

    print(f"PySide6 collected: {len(pyside6_binaries)} binaries, {len(pyside6_datas)} plugin dirs (minimal)")
except Exception as e:
    print(f"WARNING: PySide6 not found or collect failed: {e}")
    print("  Falling back to Tkinter overlay")
    pyside6_binaries, pyside6_datas = [], []

# Binaries and data files
binaries = pyside6_binaries
datas = pyside6_datas + [
    ('config.py', '.'),
    ('utils', 'utils'),
    ('providers', 'providers'),
    ('refine', 'refine'),
    ('whisper_platform', 'whisper_platform'),
    ('ui', 'ui'),      # Overlay modules (PySide6, Tkinter, animation)
    ('audio', 'audio'), # Recording module
]
if BUILD_LOCAL:
    faster_whisper_datas = collect_data_files(
        "faster_whisper", includes=["assets/silero_vad_v6.onnx"]
    )
    if not faster_whisper_datas:
        raise RuntimeError(
            "Missing faster_whisper assets (silero_vad_v6.onnx). "
            "Install faster-whisper before Local builds."
        )
    print(
        f"faster_whisper assets collected: {len(faster_whisper_datas)} files"
    )
    datas += faster_whisper_datas

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
    'audio',
    'audio.recording',

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
    'ui.settings_windows',
    'ui.onboarding_wizard_windows',
    'ui.styles_windows',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'shiboken6',
    'tkinter',
]

# Local Whisper imports (only for -Local builds)
if BUILD_LOCAL:
    hiddenimports += [
        'faster_whisper',
        'ctranslate2',
        'tokenizers',
        'huggingface_hub',
        'torch',
        'torchaudio',
    ]

hiddenimports = _dedupe(hiddenimports)

# Exclude unnecessary modules (reduces size and build time)
excludes = [
    # GUI Frameworks (not needed - except PySide6/tkinter for overlay)
    'PyQt5', 'PyQt6', 'PySide2', 'wx',

    # Data Science (not needed)
    'matplotlib', 'pandas', 'sklearn', 'scipy',

    # Testing Frameworks
    'pytest', 'unittest',

    # macOS-specific (not available on Windows)
    'objc', 'Foundation', 'AppKit', 'Quartz',
    'AVFoundation', 'CoreMedia', 'CoreAudio', 'CoreFoundation',
    'rumps', 'quickmachotkey',

    # CLI (not needed for Windows daemon)
    'typer', 'click',

    # Dev Tools
    'IPython', 'jupyter',

    # Misc
    'curses',
]

# For API-only builds: exclude ML/Whisper packages (saves ~4GB)
if not BUILD_LOCAL:
    excludes += [
        'torch', 'torchaudio', 'torchvision',
        'ctranslate2', 'faster_whisper', 'openai_whisper', 'whisper',
        'transformers', 'tokenizers', 'huggingface_hub', 'safetensors',
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
    upx=False,  # Disabled for faster builds
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
    upx=False,  # Disabled for faster builds
    upx_exclude=[],
    name='PulseScribe',
)
