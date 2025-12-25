# Building PulseScribe on Windows

PulseScribe ships as a standalone `.exe` (onedir mode) or as an installer. For distribution, both can optionally be code-signed.

## Prerequisites

- **Python 3.10+** with pip
- **PyInstaller** (`pip install pyinstaller`)
- **PySide6** (optional, recommended for GPU-accelerated overlay)
- **Inno Setup 6** (optional, for installer - [download](https://jrsoftware.org/isinfo.php))

## Quick Start

### Using the Build Script (Recommended)

```powershell
# Standard build (EXE only)
.\build_windows.ps1

# Clean build with installer
.\build_windows.ps1 -Clean -Installer

# Create installer from existing EXE
.\build_windows.ps1 -Installer -SkipExe
```

### Manual Build

```bash
# Install dependencies
pip install -r requirements.txt

# Optional: GPU-accelerated overlay (recommended)
pip install PySide6

# Build the EXE
pyinstaller build_windows.spec --clean

# Output: dist/PulseScribe/PulseScribe.exe

# Optional: Create installer (requires Inno Setup)
iscc installer_windows.iss

# Output: dist/PulseScribe-Setup-{version}.exe
```

## Build Output

The build produces a **onedir** bundle and optionally an installer:

```
dist/
├── PulseScribe/
│   ├── PulseScribe.exe      # Main executable (portable)
│   ├── *.dll                # Python + dependency DLLs
│   └── ...                  # Supporting files
└── PulseScribe-Setup-1.1.1.exe  # Installer (if built)
```

### Distribution Options

| Format | File | Use Case |
|--------|------|----------|
| **Portable** | `dist/PulseScribe/` folder | USB stick, no installation needed |
| **Installer** | `PulseScribe-Setup-{ver}.exe` | Standard installation with Start Menu, Autostart option |

## Build Options

### Environment Variables

| Variable | Description |
|----------|-------------|
| `PULSESCRIBE_VERSION` or `VERSION` | Override version number (default: reads from `pyproject.toml`) |

### Build Variants

| Variant | Command | Overlay |
|---------|---------|---------|
| **Full** (recommended) | `pip install PySide6 && pyinstaller build_windows.spec` | GPU-accelerated (PySide6) |
| **Minimal** | `pyinstaller build_windows.spec` | CPU-only (Tkinter) |

> **Note:** Without PySide6, the overlay falls back to Tkinter automatically. PySide6 provides smoother animations.

## Included Dependencies

The spec file bundles:

### Core
- `sounddevice`, `soundfile`, `numpy` (audio)
- `deepgram-sdk`, `groq`, `openai` (transcription)
- `python-dotenv` (configuration)

### Windows-specific
- `pystray`, `Pillow` (system tray icon)
- `pynput` (global hotkeys + Ctrl+V simulation)
- `pywin32`, `psutil` (app detection)

> **Note:** The Windows daemon uses native ctypes for clipboard operations (no pyperclip).

### UI/Overlay
- `PySide6` (GPU-accelerated overlay, optional)
- `tkinter` (fallback, built-in)

## Excluded Modules

The spec excludes unnecessary modules to reduce size:

- macOS frameworks (`objc`, `AppKit`, `Quartz`, etc.)
- Unused GUI frameworks (`PyQt5`, `PyQt6`, `wx`)
- Data science (`matplotlib`, `pandas`, `sklearn`)
- Testing (`pytest`, `unittest`)

## Custom Icon

Place an icon at `assets/icon.ico` and it will be automatically included:

```
pulsescribe/
└── assets/
    └── icon.ico    # Windows icon (256x256 recommended)
```

## Installer Features

The Inno Setup installer (`installer_windows.iss`) provides:

| Feature | Description |
|---------|-------------|
| **Installation wizard** | Language selection (English/German), license, destination |
| **Start Menu entries** | PulseScribe + Uninstall shortcuts |
| **Desktop shortcut** | Optional, user can choose during install |
| **Autostart option** | Optional, adds to Windows startup |
| **Clean uninstall** | Via Windows "Apps & Features", optionally removes settings |
| **Per-user install** | No admin rights required (installs to `%LocalAppData%\Programs`) |

### Customizing the Installer

Edit `installer_windows.iss` to customize:

```ini
; Change app metadata
#define MyAppVersion "1.1.1"
#define MyAppPublisher "Your Name"
#define MyAppURL "https://your-website.com"

; Change install location (default: per-user)
DefaultDirName={autopf}\{#MyAppName}  ; Program Files (requires admin)
DefaultDirName={localappdata}\{#MyAppName}  ; Per-user (no admin)
```

## Autostart Setup

### Via Installer

The installer offers an "Autostart" checkbox during installation. This adds a registry entry to start PulseScribe with Windows.

### Manual Setup (Portable)

To run PulseScribe on Windows startup without the installer:

1. Press `Win+R`, type `shell:startup`
2. Create a shortcut to `dist/PulseScribe/PulseScribe.exe` in the opened folder

Or use the included batch file:

```powershell
# Create shortcut in startup folder
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\PulseScribe.lnk")
$Shortcut.TargetPath = "$PWD\dist\PulseScribe\PulseScribe.exe"
$Shortcut.Save()
```

## Code Signing (Optional)

For distribution without SmartScreen warnings:

```powershell
# Sign with a code signing certificate
signtool sign /f certificate.pfx /p password /t http://timestamp.digicert.com dist\PulseScribe\PulseScribe.exe
```

> **Note:** Without signing, Windows SmartScreen may warn users on first launch. Users can click "More info" → "Run anyway".

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError: No module named 'pystray'` | `pip install pystray pillow` |
| `ModuleNotFoundError: No module named 'win32gui'` | `pip install pywin32` |
| Overlay not showing | Check `PULSESCRIBE_OVERLAY=true` in `.env` |
| Hotkeys not working | Run as Administrator (some apps require elevated privileges) |
| Antivirus blocks EXE | Add exception or code-sign the executable |

## Configuration

The EXE uses the same configuration as the Python version:

- `~/.pulsescribe/.env` (recommended)
- Or project root `.env` (for development)

See `README.md` for all configuration options.

## Development Build

For faster iteration during development:

```bash
# Run directly without building
python pulsescribe_windows.py --debug

# Quick test build (no UPX compression)
pyinstaller build_windows.spec --clean --noconfirm
```
