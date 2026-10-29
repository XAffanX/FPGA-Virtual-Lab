; Inno Setup script for FPGA Virtual Lab (compiled by scripts\build_windows.ps1 when Inno Setup 6 is installed:
;   winget install JRSoftware.InnoSetup )
#define AppName "FPGA Virtual Lab"
#define AppVersion "1.1.0"
#define Author "XAffanX"
#define AuthorURL "https://github.com/XAffanX"
#define RepoURL "https://github.com/XAffanX/FPGA-Virtual-Lab"
#define SourceDir "..\dist\FPGAVirtualLab"

[Setup]
AppId={{6A1D9C7E-4F2B-4C61-9E0B-0F1110000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} by {#Author}
; Publisher, links and copyright appear in the installer and in Windows "Apps & features".
AppPublisher={#Author}
AppPublisherURL={#AuthorURL}
AppSupportURL={#RepoURL}/issues
AppUpdatesURL={#RepoURL}/releases
AppCopyright=Copyright (C) 2026 {#Author} - {#AuthorURL}
VersionInfoCompany={#Author}
VersionInfoCopyright=Copyright (C) 2026 {#Author}
VersionInfoDescription={#AppName} {#AppVersion} setup - by {#Author} ({#AuthorURL})
VersionInfoVersion={#AppVersion}.0
DefaultDirName={autopf}\FPGA Virtual Lab
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=FPGAVirtualLab-{#AppVersion}-setup
SetupIconFile=..\assets\icons\app.ico
UninstallDisplayIcon={app}\FPGAVirtualLab.exe
UninstallDisplayName={#AppName} {#AppVersion} (by {#Author})
; Shown as its own page before installing: author, links, requirements.
InfoBeforeFile=about_author.txt
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nCreated by {#Author}%n{#AuthorURL}%n%nIt is recommended that you close all other applications before continuing.
FinishedLabel=Setup has finished installing [name] on your computer.%n%nFPGA Virtual Lab is created by {#Author} - {#AuthorURL}%nNews, source code and updates: {#RepoURL}

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "about_author.txt"; DestDir: "{app}"; DestName: "ABOUT.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\FPGAVirtualLab.exe"
Name: "{group}\{#AppName} on GitHub ({#Author})"; Filename: "{#RepoURL}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\FPGAVirtualLab.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\FPGAVirtualLab.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
