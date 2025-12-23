<#
.SYNOPSIS
    Build script for PulseScribe on Windows.

.DESCRIPTION
    Builds PulseScribe EXE and optionally creates an installer.

.PARAMETER Clean
    Remove build artifacts before building.

.PARAMETER Installer
    Create Inno Setup installer after building EXE.

.PARAMETER SkipExe
    Skip EXE build (only create installer from existing build).

.EXAMPLE
    .\build_windows.ps1
    # Standard build (EXE only)

.EXAMPLE
    .\build_windows.ps1 -Clean -Installer
    # Clean build with installer

.EXAMPLE
    .\build_windows.ps1 -Installer -SkipExe
    # Create installer from existing EXE build
#>

param(
    [switch]$Clean,
    [switch]$Installer,
    [switch]$SkipExe
)

$ErrorActionPreference = "Stop"

# Colors for output
function Write-Step { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host $msg -ForegroundColor Green }
function Write-Warning { param($msg) Write-Host $msg -ForegroundColor Yellow }
function Write-Error { param($msg) Write-Host $msg -ForegroundColor Red }

# Get version from pyproject.toml
function Get-AppVersion {
    $pyproject = Get-Content "pyproject.toml" -Raw
    if ($pyproject -match 'version\s*=\s*"([^"]+)"') {
        return $matches[1]
    }
    return "1.0.0"
}

$Version = Get-AppVersion
Write-Host "`nPulseScribe Build Script v$Version" -ForegroundColor Magenta
Write-Host "=================================" -ForegroundColor Magenta

# Check prerequisites
Write-Step "Checking prerequisites..."

# Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Please install Python 3.10+."
    exit 1
}
Write-Success "  Python: $(python --version)"

# PyInstaller
$pyinstallerCheck = python -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
if (-not $pyinstallerCheck) {
    Write-Warning "  PyInstaller not found. Installing..."
    pip install pyinstaller
}
Write-Success "  PyInstaller: OK"

# Inno Setup (only if -Installer)
if ($Installer) {
    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if (-not $iscc) {
        # Try common install locations
        $isccPaths = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
            "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        )
        foreach ($path in $isccPaths) {
            if (Test-Path $path) {
                $iscc = $path
                break
            }
        }
    }
    if (-not $iscc) {
        Write-Error "Inno Setup not found. Please install from https://jrsoftware.org/isinfo.php"
        Write-Host "  Or run without -Installer flag to build EXE only."
        exit 1
    }
    Write-Success "  Inno Setup: OK"
}

# Clean build artifacts
if ($Clean) {
    Write-Step "Cleaning build artifacts..."
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
    Write-Success "  Cleaned build/ and dist/"
}

# Build EXE
if (-not $SkipExe) {
    Write-Step "Building PulseScribe.exe..."

    # Set version environment variable
    $env:PULSESCRIBE_VERSION = $Version

    # Run PyInstaller
    python -m PyInstaller build_windows.spec --clean --noconfirm

    if ($LASTEXITCODE -ne 0) {
        Write-Error "PyInstaller build failed!"
        exit 1
    }

    # Verify output
    if (-not (Test-Path "dist\PulseScribe\PulseScribe.exe")) {
        Write-Error "Build output not found: dist\PulseScribe\PulseScribe.exe"
        exit 1
    }

    $exeSize = (Get-Item "dist\PulseScribe\PulseScribe.exe").Length / 1MB
    Write-Success "  Built: dist\PulseScribe\PulseScribe.exe ($([math]::Round($exeSize, 1)) MB)"
}

# Create installer
if ($Installer) {
    Write-Step "Creating installer..."

    # Verify EXE exists
    if (-not (Test-Path "dist\PulseScribe\PulseScribe.exe")) {
        Write-Error "EXE not found. Run without -SkipExe first."
        exit 1
    }

    # Check for icon
    if (Test-Path "assets\icon.ico") {
        Write-Success "  Icon: assets\icon.ico"
    } else {
        Write-Warning "  assets\icon.ico not found - using default icon"
    }

    # Run Inno Setup
    if ($iscc -is [string]) {
        & $iscc installer_windows.iss
    } else {
        iscc installer_windows.iss
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Inno Setup build failed!"
        exit 1
    }

    $installerPath = "dist\PulseScribe-Setup-$Version.exe"
    if (Test-Path $installerPath) {
        $installerSize = (Get-Item $installerPath).Length / 1MB
        Write-Success "  Built: $installerPath ($([math]::Round($installerSize, 1)) MB)"
    }
}

# Summary
Write-Host "`n" -NoNewline
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "===============" -ForegroundColor Green

Write-Host "`nOutput files:"
if (Test-Path "dist\PulseScribe\PulseScribe.exe") {
    Write-Host "  - dist\PulseScribe\PulseScribe.exe (portable)"
}
$installerPath = "dist\PulseScribe-Setup-$Version.exe"
if (Test-Path $installerPath) {
    Write-Host "  - $installerPath (installer)"
}

Write-Host "`nNext steps:"
Write-Host "  - Test: .\dist\PulseScribe\PulseScribe.exe"
if ($Installer) {
    Write-Host "  - Install: .\$installerPath"
}
Write-Host ""
