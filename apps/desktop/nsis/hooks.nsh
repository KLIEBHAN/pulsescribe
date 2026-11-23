; PulseScribe NSIS installer hooks (Tauri v2 bundle.windows.nsis.installerHooks).
;
; Preserves and migrates an existing `HKCU\...\Run\PulseScribe` autostart entry
; across legacy Python Inno Setup upgrades and Rust-to-Rust reinstalls —
; including when `PageLeaveReinstall` launches the previous uninstaller without
; `/UPDATE`, which removes the Run entry before `Section Install` runs.
; Standalone uninstall removal is handled natively by Tauri v2's `installer.nsi`
; (`${If} $UpdateMode <> 1` -> `DeleteRegValue ... "${PRODUCTNAME}"`).

Var PsCapturedAutostart

!define MUI_CUSTOMFUNCTION_GUIINIT PsCaptureExistingAutostart
Function PsCaptureExistingAutostart
  ClearErrors
  ReadRegStr $PsCapturedAutostart HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PulseScribe"
FunctionEnd

!macro NSIS_HOOK_POSTINSTALL
  ; In GUI/passive installs, `$PsCapturedAutostart` was captured in `.onGUIInit`
  ; before `PageLeaveReinstall` could invoke the previous uninstaller. In silent
  ; (`/S`) installs, `.onGUIInit` and `PageReinstall` do not run, so fall back
  ; to reading the registry directly.
  StrCmp $PsCapturedAutostart "" 0 _ps_restore_autostart
  ClearErrors
  ReadRegStr $PsCapturedAutostart HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PulseScribe"
  StrCmp $PsCapturedAutostart "" _ps_no_existing_autostart
  _ps_restore_autostart:
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PulseScribe" '"$INSTDIR\pulsescribe-desktop.exe"'
  _ps_no_existing_autostart:
!macroend
