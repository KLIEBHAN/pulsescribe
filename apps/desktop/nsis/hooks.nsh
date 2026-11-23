; PulseScribe NSIS installer hooks (Tauri v2 bundle.windows.nsis.installerHooks).
;
; Migrates an existing legacy Python Inno Setup autostart entry
; (`HKCU\...\Run\PulseScribe`) to the newly installed Rust executable.
; Removal on uninstall is handled natively by Tauri v2's `installer.nsi`
; (`${If} $UpdateMode <> 1` -> `DeleteRegValue ... "${PRODUCTNAME}"`), so
; Rust-to-Rust upgrades preserve user autostart automatically.

!macro NSIS_HOOK_POSTINSTALL
  ; If a prior installation enabled Autostart under HKCU\...\Run\PulseScribe,
  ; update it to the newly installed Rust executable so users do not launch an
  ; obsolete legacy Python binary on next login.
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PulseScribe"
  StrCmp $0 "" _ps_no_existing_autostart
  StrCmp $0 '"$INSTDIR\pulsescribe-desktop.exe"' _ps_no_existing_autostart
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PulseScribe" '"$INSTDIR\pulsescribe-desktop.exe"'
  _ps_no_existing_autostart:
!macroend
