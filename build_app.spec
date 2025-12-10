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

# Pfade zu Modulen und Ressourcen
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
    'scipy',
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
    binaries=[],
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
    icon=None,  # TODO: App-Icon hinzufügen (icon='assets/icon.icns')
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
