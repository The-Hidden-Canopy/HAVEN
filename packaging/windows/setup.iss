#ifndef PayloadDir
  #error PayloadDir must point to the reviewed standalone Windows package.
#endif
[Setup]
AppId={{CEE22DD7-A850-4C71-A08D-F3AFC7969AA4}
AppName=HAVEN
AppVersion=0.1.0-preview.20261030
VersionInfoVersion=0.1.0.0
AppPublisher=The Hidden Canopy LLC
AppPublisherURL=https://github.com/The-Hidden-Canopy/HAVEN
DefaultDirName={localappdata}\Programs\HAVEN
DefaultGroupName=HAVEN
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
OutputDir=..\..\downloads\windows
OutputBaseFilename=HAVENSetup
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\Haven.exe
UninstallDisplayName=HAVEN

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\HAVEN"; Filename: "{app}\Haven.exe"; WorkingDir: "{app}"

; The household's selected data root is deliberately outside this installer.
; No service, startup task, machine registry entry or security enrollment is created.
