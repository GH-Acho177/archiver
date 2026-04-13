; Inno Setup script for Media Downloader
; Download Inno Setup: https://jrsoftware.org/isinfo.php
; Compile:  Right-click this file → Compile  (or open in Inno Setup IDE)

#define AppName      "Media Downloader"
#define AppVersion   "1.0.0"
#define AppPublisher "GH-Acho177"
#define AppExeName   "MediaDownloader.exe"
; Install into user's AppData so no UAC prompt is needed
#define DefaultInstDir "{localappdata}\MediaDownloader"

[Setup]
AppId={{BA0057F9-834C-49A4-B889-3B01EFE23692}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisherURL="https://github.com/GH-Acho177"
AppSupportURL="https://github.com/GH-Acho177"
AppUpdatesURL="https://github.com/GH-Acho177"
DefaultDirName={#DefaultInstDir}
DefaultGroupName={#AppName}
AllowNoIcons=yes
; Output
OutputDir=..\dist
OutputBaseFilename=MediaDownloader_Setup_{#AppVersion}
; Compression
Compression=lzma2/ultra64
SolidCompression=yes
; Windows 10+ only (Sun Valley theme requires it)
MinVersion=10.0
; No UAC required — installs to user AppData
PrivilegesRequired=lowest
; Appearance
WizardStyle=modern
; Icon (comment out if you have no icon.ico yet)
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
; Misc
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Main application (PyInstaller --onedir output)
Source: "..\dist\MediaDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; Create writable data directories alongside the install (user-space)
Name: "{app}\config"
Name: "{app}\downloads"
Name: "{app}\logs"

[Icons]
; Start Menu
Name: "{group}\{#AppName}";      Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
; Desktop (optional task)
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Offer to launch after install
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove generated runtime files on uninstall
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"
