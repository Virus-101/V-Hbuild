; Inno Setup script: wraps dist\V-Hbuild\ into V-HbuildSetup.exe and registers .vbuild files.
;   iscc /DAppVersion=0.2.0 packaging\vhbuild.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6A4E2F0B-8C1D-4E7A-9B3F-5D2C1A0E9F47}
AppName=V-Hbuild
AppVersion={#AppVersion}
AppPublisher=V-Hbuild contributors
DefaultDirName={autopf}\V-Hbuild
DefaultGroupName=V-Hbuild
OutputDir=..\dist
OutputBaseFilename=V-HbuildSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ChangesAssociations=yes
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Files]
Source: "..\dist\V-Hbuild\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\V-Hbuild"; Filename: "{app}\V-Hbuild.exe"
Name: "{autodesktop}\V-Hbuild"; Filename: "{app}\V-Hbuild.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Registry]
; Double-clicking a .vbuild opens it in V-Hbuild's viewer.
Root: HKA; Subkey: "Software\Classes\.vbuild"; ValueType: string; ValueName: ""; ValueData: "VHbuild.vbuild"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.vbuild"; ValueType: string; ValueName: "Content Type"; ValueData: "application/vnd.vhbuild.vbuild+zip"
Root: HKA; Subkey: "Software\Classes\VHbuild.vbuild"; ValueType: string; ValueName: ""; ValueData: "V-Hbuild build"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\VHbuild.vbuild\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\V-Hbuild.exe,0"
Root: HKA; Subkey: "Software\Classes\VHbuild.vbuild\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\V-Hbuild.exe"" ""%1"""

[Run]
Filename: "{app}\V-Hbuild.exe"; Description: "Start V-Hbuild"; Flags: nowait postinstall skipifsilent
