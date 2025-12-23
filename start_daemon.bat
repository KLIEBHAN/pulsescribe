@echo off
REM PulseScribe Daemon Starter (Windows)
REM Autostart: Win+R, shell:startup, Shortcut erstellen

REM Avoid "Terminate batch job (Y/N)?" after Ctrl+C by running with stdin=NUL.
if "%~1"=="-FIXED_CTRL_C" (
    shift
) else (
    call <nul "%~f0" -FIXED_CTRL_C %*
    exit /b
)

cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

python pulsescribe_windows.py
