; Inno Setup script for MilkDrop Visualizer.
; Driven by installer\build_windows.ps1, which passes the four /D values below.
; Not run directly: StageDir has to point at a tree that build_windows.ps1 has
; already assembled.

#ifndef AppVersion
  #error Run this through installer\build_windows.ps1
#endif

#define AppName    "MilkDrop Visualizer"
#define AppExeName "MilkDropVisualizer.exe"
#define Publisher  "BomsAI Software"
#define SiteUrl    "https://bomsaisoftware.com"

[Setup]
; Never change AppId -- it is what lets an upgrade replace an install rather
; than sitting beside it.
AppId={{7C4B2E19-3F5A-4D8C-9B61-2A0E7D3F8C14}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL={#SiteUrl}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\app\{#AppExeName}
UninstallDisplayName={#AppName}
OutputDir={#OutDir}
OutputBaseFilename={#OutName}
SetupIconFile={#SourcePath}\milkdrop-visualizer.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The app and its Qt libraries are x64 only.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Program Files needs elevation; per-user would dodge it but Steam and the
; Start Menu both expect a normal machine-wide install.
PrivilegesRequired=admin
LicenseFile={#StageDir}\licenses\README-third-party.txt
InfoAfterFile={#StageDir}\README.txt
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#StageDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "{#StageDir}\README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\VERSION"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\app\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\app\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\app\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Settings and ratings live in %APPDATA%\MilkDropVisualizer (config.py moves
; BASE_DIR there when frozen, because this directory is replaced on upgrade).
; They are deliberately left behind on uninstall, exactly as the Linux
; uninstall.sh leaves ~/.config alone.
