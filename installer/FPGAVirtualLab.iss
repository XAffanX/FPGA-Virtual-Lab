; Inno Setup script for FPGA Virtual Lab (compiled by scripts\build_windows.ps1 when Inno Setup 6 is installed:
;   winget install JRSoftware.InnoSetup )
#define AppName "FPGA Virtual Lab"
#define AppVersion "1.1.0"
#define SourceDir "..\dist\FPGAVirtualLab"

[Setup]
AppId={{6A1D9C7E-4F2B-4C61-9E0B-0F1110000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=FPGA Virtual Lab
DefaultDirName={autopf}\FPGA Virtual Lab
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=FPGAVirtualLab-{#AppVersion}-setup
SetupIconFile=..\assets\icons\app.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\FPGAVirtualLab.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\FPGAVirtualLab.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\FPGAVirtualLab.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
