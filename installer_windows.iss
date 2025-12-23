; PulseScribe Windows Installer
; Inno Setup Script
;
; Build:
;   1. Build EXE first: pyinstaller build_windows.spec --clean
;   2. Run Inno Setup: iscc installer_windows.iss
;   Output: dist/PulseScribe-Setup-{version}.exe
;
; Requirements:
;   - Inno Setup 6.x (https://jrsoftware.org/isinfo.php)
;   - PyInstaller build output in dist/PulseScribe/

#define MyAppName "PulseScribe"
#define MyAppVersion "1.1.1"
#define MyAppPublisher "KLIEBHAN"
#define MyAppURL "https://pulsescribe.me"
#define MyAppExeName "PulseScribe.exe"
#define MyAppDescription "Voice input for Windows - Transcription with Deepgram, OpenAI, Groq or locally"

[Setup]
; App identity (GUID must be unique and properly formatted with double braces)
AppId={{9A961D4A-17CB-4B01-A331-17C773E14149}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Installation settings
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
DisableProgramGroupPage=yes

; Output settings
OutputDir=dist
OutputBaseFilename=PulseScribe-Setup-{#MyAppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

; Compression
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; UI settings
WizardStyle=modern
WizardSizePercent=100

; Privileges (no admin required for per-user install)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Version info
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppDescription}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

; Misc
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Start PulseScribe automatically when Windows starts"; GroupDescription: "Startup:"

[Files]
; Main application (entire onedir bundle)
Source: "dist\PulseScribe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppDescription}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; Desktop (optional)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "{#MyAppDescription}"

[Registry]
; Autostart (optional)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; Launch after install (optional)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up log files on uninstall
; Note: PulseScribe stores config in %USERPROFILE%\.pulsescribe (not AppData)
Type: filesandordirs; Name: "{%USERPROFILE}\.pulsescribe\logs"

[Code]
// Custom code for version checking and cleanup

function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Post-install actions (if needed)
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ConfigDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // PulseScribe stores config in %USERPROFILE%\.pulsescribe
    ConfigDir := ExpandConstant('{%USERPROFILE}') + '\.pulsescribe';

    // Ask user if they want to remove settings
    if MsgBox('Do you want to remove PulseScribe settings and logs?' + #13#10 +
              '(Located in: ' + ConfigDir + ')',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      DelTree(ConfigDir, True, True, True);
    end;
  end;
end;
