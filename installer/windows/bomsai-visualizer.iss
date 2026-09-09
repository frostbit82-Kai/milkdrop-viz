; Inno Setup script for BomsAI Visualizer.
; Driven by installer\build_windows.ps1, which passes the four /D values below.
; Not run directly: StageDir has to point at a tree that build_windows.ps1 has
; already assembled.

#ifndef AppVersion
  #error Run this through installer\build_windows.ps1
#endif

#define AppName    "BomsAI Visualizer"
#define AppExeName "BomsAIVisualizer.exe"
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
SetupIconFile={#SourcePath}\bomsai-visualizer.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The app and its Qt libraries are x64 only.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 1607 (build 14393), and the floor is the dependencies, not us --
; nothing this app calls itself needs more than Vista, and the one modern call
; (DWM's immersive dark mode) already checks its own return. Qt6Core imports
; SetThreadDescription, which arrived in 1607, and python314.dll imports
; PathCchCombineEx from api-ms-win-core-path, which is Windows 8 and later.
; Both are load-time imports: without this line Inno's own default of Windows 7
; SP1 lets the install finish on a machine where the app then dies with an
; entry-point error naming a DLL, and no way for the user to know why.
MinVersion=10.0.14393
; Program Files needs elevation; per-user would dodge it but Steam and the
; Start Menu both expect a normal machine-wide install.
PrivilegesRequired=admin
LicenseFile={#StageDir}\licenses\EULA.txt
InfoAfterFile={#StageDir}\README.txt
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "screensaver"; Description: "Use {#AppName} as the Windows screen saver"; GroupDescription: "Optional:"; Flags: unchecked

[Files]
Source: "{#StageDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "{#StageDir}\README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\VERSION"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\app\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\app\{#AppExeName}"; Tasks: desktopicon

[Registry]
; The Screen Saver Settings dropdown only lists .scr files under %SystemRoot%,
; and this one cannot go there -- it is a PyInstaller onedir build and has to
; stay beside its _internal directory. Naming it here is what right-click ->
; Install on a .scr does, and it works from any path.
Root: HKCU; Subkey: "Control Panel\Desktop"; ValueType: string; ValueName: "SCRNSAVE.EXE"; ValueData: "{app}\app\BomsAIVisualizer.scr"; Tasks: screensaver
Root: HKCU; Subkey: "Control Panel\Desktop"; ValueType: string; ValueName: "ScreenSaveActive"; ValueData: "1"; Tasks: screensaver

[Run]
Filename: "{app}\app\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Settings and ratings live in %APPDATA%\BomsAIVisualizer (config.py moves
; BASE_DIR there when frozen, because this directory is replaced on upgrade).
; They are deliberately left behind on uninstall, exactly as the Linux
; uninstall.sh leaves ~/.config alone.
