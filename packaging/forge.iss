; Inno Setup script: wraps dist\Forge\ into ForgeSetup.exe and registers .vbuild files.
;   iscc /DAppVersion=0.2.0 packaging\forge.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6A4E2F0B-8C1D-4E7A-9B3F-5D2C1A0E9F47}
AppName=Forge
AppVersion={#AppVersion}
AppPublisher=Forge contributors
DefaultDirName={autopf}\Forge
DefaultGroupName=Forge
OutputDir=..\dist
OutputBaseFilename=ForgeSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ChangesAssociations=yes
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Files]
Source: "..\dist\Forge\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Forge"; Filename: "{app}\Forge.exe"
Name: "{autodesktop}\Forge"; Filename: "{app}\Forge.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Registry]
; Double-clicking a .vbuild opens it in Forge's viewer.
Root: HKA; Subkey: "Software\Classes\.vbuild"; ValueType: string; ValueName: ""; ValueData: "Forge.vbuild"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.vbuild"; ValueType: string; ValueName: "Content Type"; ValueData: "application/vnd.forge.vbuild+zip"
Root: HKA; Subkey: "Software\Classes\Forge.vbuild"; ValueType: string; ValueName: ""; ValueData: "Forge build"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Forge.vbuild\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\Forge.exe,0"
Root: HKA; Subkey: "Software\Classes\Forge.vbuild\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Forge.exe"" ""%1"""

[Run]
Filename: "{app}\Forge.exe"; Description: "Start Forge"; Flags: nowait postinstall skipifsilent
