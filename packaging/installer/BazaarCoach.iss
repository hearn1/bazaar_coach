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
AppPublisherURL=https://github.com/hearn1/bazaar_coach
AppSupportURL=https://github.com/hearn1/bazaar_coach
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
SetupIconFile=..\..\assets\icon.ico
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Versioned shortcuts for side-by-side installs. A stable "Bazaar Coach" Start Menu
; shortcut targeting the newest build is refreshed by the app after updates.
Name: "{group}\Bazaar Coach ({#AppVersion})"; Filename: "{app}\BazaarCoach.exe"; WorkingDir: "{app}"
Name: "{group}\Bazaar Coach Doctor ({#AppVersion})"; Filename: "{cmd}"; Parameters: "/K ""{app}\BazaarCoach.exe"" doctor"; WorkingDir: "{app}"
Name: "{group}\Uninstall Bazaar Coach ({#AppVersion})"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Bazaar Coach ({#AppVersion})"; Filename: "{app}\BazaarCoach.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\BazaarCoach.exe"; Description: "Launch Bazaar Coach"; Flags: postinstall nowait skipifsilent
Filename: "{app}\BazaarCoachCLI.exe"; Parameters: "doctor"; Description: "Run Bazaar Coach Doctor"; Flags: postinstall skipifsilent unchecked

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

procedure ForceRemoveUserDataDir(const DirPath: string);
var
  ResultCode: Integer;
begin
  if DirExists(DirPath) then
  begin
    if not DelTree(DirPath, True, True, True) then
      Exec('cmd.exe', '/c rd /s /q "' + DirPath + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
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
      ForceRemoveUserDataDir(AppDataPath);
      ForceRemoveUserDataDir(LocalDataPath);
    end;
  end;
end;
