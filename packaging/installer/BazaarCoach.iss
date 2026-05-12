#define AppName "Bazaar Coach"
#ifndef AppVersion
#define AppVersion "0.1.0-dev"
#endif
#ifndef SourceDir
#define SourceDir "..\..\dist\BazaarCoach"
#endif
#ifndef OutputDir
#define OutputDir "..\..\dist\installer"
#endif

[Setup]
AppId={{E5A3F7C2-1D94-4B8E-AF61-3C07D5E82A40}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Bazaar Coach
AppPublisherURL=https://github.com/
AppSupportURL=https://github.com/
DefaultDirName={autopf}\Bazaar Coach\{#AppVersion}
DefaultGroupName=Bazaar Coach
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=BazaarCoachSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
UninstallDisplayIcon={app}\BazaarCoach.exe
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Bazaar Coach"; Filename: "{app}\BazaarCoach.exe"; WorkingDir: "{app}"
Name: "{group}\Bazaar Coach - Doctor"; Filename: "{app}\BazaarCoachCLI.exe"; Parameters: "doctor"; WorkingDir: "{app}"
Name: "{group}\Bazaar Coach - Refresh Builds"; Filename: "{app}\BazaarCoachCLI.exe"; Parameters: "refresh-builds"; WorkingDir: "{app}"
Name: "{group}\Bazaar Coach - Export Diagnostics"; Filename: "{app}\BazaarCoachCLI.exe"; Parameters: "export-diagnostics"; WorkingDir: "{app}"
Name: "{group}\Uninstall Bazaar Coach"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Bazaar Coach"; Filename: "{app}\BazaarCoach.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\BazaarCoachCLI.exe"; Parameters: "doctor"; Description: "Run Bazaar Coach Doctor"; Flags: postinstall skipifsilent

[Code]
var
  RemoveUserData: Boolean;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  RemoveUserData :=
    MsgBox(
      'Remove all Bazaar Coach user data from %APPDATA% and %LOCALAPPDATA%?' + #13#10 + #13#10 +
      'Choose No to keep settings, logs, cache, and the run database.',
      mbConfirmation,
      MB_YESNO
    ) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDataPath: string;
  LocalDataPath: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if RemoveUserData then
    begin
      AppDataPath := ExpandConstant('{userappdata}\BazaarCoach');
      LocalDataPath := ExpandConstant('{localappdata}\BazaarCoach');
      if DirExists(AppDataPath) then
        DelTree(AppDataPath, True, True, True);
      if DirExists(LocalDataPath) then
        DelTree(LocalDataPath, True, True, True);
    end;
  end;
end;
