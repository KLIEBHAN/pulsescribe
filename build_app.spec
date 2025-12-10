# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

# Pfade zu deinen Modulen
datas = [
    ('ui', 'ui'),
    ('utils', 'utils'),
    ('providers', 'providers'),
    ('refine', 'refine'),
    ('whisper_platform', 'whisper_platform'),
    ('audio', 'audio'),
    # Wir inkludieren KEINE .env Datei, da diese benutzerspezifisch ist
]

a = Analysis(
    ['whisper_daemon.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'pynput.keyboard._darwin',
        'pynput.mouse._darwin',
        'sounddevice',
        'rumps',
        'AppKit',
        'Quartz',
        'AVFoundation',
        'CoreFoundation',
        'objc',
        'Foundation',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    console=False, 
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch='arm64', # Optimiert für Apple Silicon, ändere zu 'universal2' für Intel Support
)

app = BUNDLE(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='WhisperGo.app',
    icon=None, 
    bundle_identifier='com.kliebhan.whisper-go',
    info_plist={
        'NSMicrophoneUsageDescription': 'Whisper Go benötigt Zugriff auf das Mikrofon für die Spracherkennung.',
        'NSAppleEventsUsageDescription': 'Whisper Go benötigt Zugriff, um Text in andere Apps einzufügen.',
        'LSUIElement': True, # Menubar App (kein Dock Icon)
        'CFBundleName': 'Whisper Go',
        'CFBundleDisplayName': 'Whisper Go',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': 'True'
    },
)
