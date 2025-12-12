# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec für WhisperGo.app

Build: pyinstaller build_app.spec
Output: dist/WhisperGo.app

WICHTIG: Accessibility-Berechtigungen und Code-Signing
=======================================================
macOS identifiziert Apps anhand ihrer Signatur. Bei unsignierten Builds:
- Nach JEDEM Neubuild muss die App in Bedienungshilfen NEU hinzugefügt werden
- macOS merkt sich den Hash der Binary, der sich bei jedem Build ändert

Für stabilen Betrieb die App signieren:
    codesign --force --deep --sign - dist/WhisperGo.app

Oder mit Developer ID für Distribution:
    codesign --force --deep --sign "Developer ID Application: Name" dist/WhisperGo.app
"""

block_cipher = None

# PyInstaller Hook helpers for native libs/data
from PyInstaller.utils.hooks import collect_all  # type: ignore

# Pfade zu Modulen und Ressourcen
binaries = []
datas = [
    ('config.py', '.'),  # Top-Level Konfiguration
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
    
    # === Utils ===
    'pyperclip',
    'dotenv',
]

# === Local backends (faster-whisper / CTranslate2) ===
fw_datas, fw_binaries, fw_hidden = collect_all("faster_whisper")
ct_datas, ct_binaries, ct_hidden = collect_all("ctranslate2")
tok_datas, tok_binaries, tok_hidden = collect_all("tokenizers")

datas += fw_datas + ct_datas + tok_datas
binaries += fw_binaries + ct_binaries + tok_binaries
hiddenimports += fw_hidden + ct_hidden + tok_hidden
hiddenimports = list(dict.fromkeys(hiddenimports))

# === Local backend (mlx-whisper / MLX) ===
# Optional: only available/needed on Apple Silicon builds.
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
hiddenimports = list(dict.fromkeys(hiddenimports))

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
    'unittest',
    
    # Dev Tools
    'IPython',
    'jupyter',
    
    # Sonstige
    'curses',
]

a = Analysis(
    ['whisper_daemon.py'],
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
    name='whisper_go',
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
    name='whisper_go',
)

app = BUNDLE(
    coll,
    name='WhisperGo.app',
    icon='assets/icon.icns',  # Custom app icon
    bundle_identifier='com.kliebhan.whisper-go',
    info_plist={
        # Berechtigungen
        'NSMicrophoneUsageDescription': 'Whisper Go benötigt Zugriff auf das Mikrofon für die Spracherkennung.',
        'NSAppleEventsUsageDescription': 'Whisper Go benötigt Zugriff, um Text in andere Apps einzufügen.',
        
        # App-Verhalten
        'LSUIElement': False,  # App im Dock anzeigen (für CMD+Q Support)
        'LSBackgroundOnly': False,
        
        # App-Info
        'CFBundleName': 'Whisper Go',
        'CFBundleDisplayName': 'Whisper Go',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        
        # macOS Features
        'NSHighResolutionCapable': True,
        'NSSupportsAutomaticGraphicsSwitching': True,
    },
)
