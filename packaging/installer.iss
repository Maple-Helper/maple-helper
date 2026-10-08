; Maple Helper installer (Inno Setup 6). Per-user install: no admin rights, silent self-updates.
; Build: ISCC packaging\installer.iss   (after PyInstaller has produced dist\Maple Helper)

#define AppName "Maple Helper"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
; releases ship lzma2/ultra; CI passes /DCompression=lzma2/fast (build.ps1 -FastInstaller) to save build time
#ifndef Compression
  #define Compression "lzma2/ultra"
#endif
; the bundled knowledge base's version (its meta.json), passed by build.ps1; empty = always install it
#ifndef KbVersion
  #define KbVersion ""
#endif

[Setup]
AppId={{4B526220-22E4-45D2-88B7-C62A0B7385E4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Maple Helper
AppPublisherURL=https://github.com/Maple-Helper/maple-helper
AppSupportURL=https://github.com/Maple-Helper/maple-helper/issues
DefaultDirName={localappdata}\Programs\Maple Helper
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; the player may pick the folder (live feedback); updates keep using it (UsePreviousAppDir)
DisableDirPage=auto
UsePreviousAppDir=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MapleHelper-Setup
SetupIconFile=..\assets\brand\app.ico
UninstallDisplayIcon={app}\Maple Helper.exe
UninstallDisplayName={#AppName}
; look: Windows 11 style that follows the system light/dark setting, like the app itself:
; the app's surfaces with a soft orange light, its logo and icon (installer-art, made from assets/brand)
WizardStyle=modern dynamic windows11
ShowLanguageDialog=no
DisableWelcomePage=no
LanguageDetectionMethod=locale
WizardBackColor=#F5F5F7
WizardBackColorDynamicDark=#1C1C1E
WizardBackImageFile=installer-art\back-light.png
WizardBackImageFileDynamicDark=installer-art\back-dark.png
WizardImageFile=installer-art\side.png
WizardImageFileDynamicDark=installer-art\side-dark.png
WizardSmallImageFile=installer-art\small.png
WizardSmallImageFileDynamicDark=installer-art\small.png
WizardImageAlphaFormat=defined
Compression={#Compression}
SolidCompression=yes
; compress on the runner's 4 cores instead of one
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
CloseApplications=force
; the app's own .pyd/.pyc files count too, not only exe/dll
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
; the app waits while this exists, so a relaunch can't land in the middle of an update
SetupMutex=MapleHelperSetup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; English first: a Windows whose language matches neither gets the first entry (a Spanish PC got the Hebrew wizard)
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; a bundled KB that is replaced goes first (dropped pages, old generated tables: drops.tsv and the rest of
; maplehelper/tables.py, which the app rebuilds anyway when their mark doesn't fit the KB); one that is kept stays
; (see KbNeedsInstall). Only the KB: removing all of _internal would leave nothing that can even start if a silent
; update stopped halfway
Type: filesandordirs; Name: "{app}\_internal\data\kb"; Check: KbNeedsInstall
; package metadata of the previous build: an upgraded package left its old version's folder beside the new one.
; Small, and [Files] writes the new ones right after
Type: filesandordirs; Name: "{app}\_internal\*.dist-info"

[Files]
Source: "..\dist\Maple Helper\*"; Excludes: "\_internal\data\kb"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; the KB is ~8,000 small files, most of an update's time; app releases usually carry the KB already installed
Source: "..\dist\Maple Helper\_internal\data\kb\*"; Excludes: "\meta.json"; DestDir: "{app}\_internal\data\kb"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Check: KbNeedsInstall
; meta.json (the version KbNeedsInstall reads) goes in last: a KB cut short, by a crash or a killed setup, has none,
; so the next update installs it again instead of skipping a KB with missing pages
Source: "..\dist\Maple Helper\_internal\data\kb\meta.json"; DestDir: "{app}\_internal\data\kb"; Flags: ignoreversion skipifsourcedoesntexist; Check: KbNeedsInstall

[Registry]
; "Start with Windows" (written by the app): removed on uninstall, never created here
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Maple Helper"; Flags: uninsdeletevalue dontcreatekey

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Maple Helper.exe"
; only on an install the player clicks through: a silent self-update brought back a shortcut they had deleted
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Maple Helper.exe"; Tasks: desktopicon; Check: not WizardSilent

[Run]
Filename: "{app}\Maple Helper.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; after a silent self-update, start the app again: in the tray when it updated on quit (the player closed it),
; with the chat open after "Update now" (the app passes /LAUNCHARGS=--updated)
Filename: "{app}\Maple Helper.exe"; Parameters: "{param:LAUNCHARGS|--background}"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
; only what the app itself made: the player may have picked a folder that holds other things (D:\Games), and
; deleting all of {app} wiped them (found in testing)
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Messages]
hebrew.WelcomeLabel1=ברוכים הבאים ל-Maple Helper
hebrew.WelcomeLabel2=העוזר האישי שלכם ב-MapleStory Classic, ישר מעל המשחק.%n%nההתקנה לוקחת פחות מדקה ולא דורשת הרשאות מנהל.
hebrew.FinishedHeadingLabel=Maple Helper מוכן!
hebrew.FinishedLabel=בכניסה הראשונה נחבר את ה-AI שלכם (Claude, ChatGPT, Gemini או Grok) וניצור את הדמות שלכם.%n%nכדי לפתוח את הצ'אט מתוך המשחק, לוחצים על הסמל של Maple Helper ליד השעון.
; the player is addressed in plural, like everywhere in the app (the stock Hebrew texts use the singular)
hebrew.ClickNext=לחצו 'הבא' כדי להמשיך, או 'ביטול' כדי לצאת.
hebrew.ClickFinish=לחצו 'סיום' כדי לסגור.
; the stock button says 'סיים': the text above names it 'סיום', like the other noun buttons ('הבא', 'הקודם', 'עיון')
hebrew.ButtonFinish=&סיום
hebrew.ExitSetupMessage=ההתקנה עוד לא הסתיימה. אם תצאו עכשיו, Maple Helper לא יותקן.%n%nאפשר להריץ את ההתקנה שוב בפעם אחרת.%n%nלצאת בכל זאת?
hebrew.ApplicationsFound=התוכנות הבאות משתמשות בקבצים שההתקנה צריכה לעדכן. מומלץ לאפשר להתקנה לסגור אותן אוטומטית.
hebrew.ApplicationsFound2=התוכנות הבאות משתמשות בקבצים שההתקנה צריכה לעדכן. מומלץ לאפשר להתקנה לסגור אותן אוטומטית. בסוף ההתקנה היא תנסה לפתוח אותן מחדש.
hebrew.PrepareToInstallNeedsRestart=כדי לסיים את ההתקנה צריך להפעיל מחדש את המחשב. אחרי ההפעלה מחדש, הריצו שוב את ההתקנה כדי לסיים את ההתקנה של [name].%n%nלהפעיל מחדש עכשיו?
hebrew.ConfirmUninstall=להסיר את %1 ואת כל הרכיבים שלו?
hebrew.WizardSelectDir=איפה להתקין?
hebrew.SelectDirDesc=בחרו את התיקייה של [name]
hebrew.SelectDirLabel3=[name] יותקן בתיקייה הזו. אפשר להשאיר אותה כמו שהיא.
hebrew.SelectDirBrowseLabel=כדי לבחור תיקייה אחרת לחצו 'עיון'. כדי להמשיך לחצו 'הבא'.
hebrew.WizardSelectTasks=עוד כמה אפשרויות
hebrew.SelectTasksDesc=מה עוד להוסיף?
hebrew.SelectTasksLabel2=בחרו מה להוסיף בזמן ההתקנה, ואז לחצו 'הבא'.
hebrew.WizardReady=מוכנים להתקנה
hebrew.ReadyLabel1=הכל מוכן להתקנת [name].
hebrew.ReadyLabel2a=לחצו 'התקן' כדי להתחיל, או 'הקודם' כדי לשנות משהו.
hebrew.ReadyLabel2b=לחצו 'התקן' כדי להתחיל.
hebrew.ReadyMemoDir=תיקיית ההתקנה:
hebrew.ReadyMemoTasks=אפשרויות נוספות:
hebrew.WizardInstalling=מתקינים...
hebrew.InstallingLabel=רק רגע, [name] מותקן על המחשב.
english.WelcomeLabel2=Your personal MapleStory Classic assistant, right over the game.%n%nSetup takes under a minute and needs no admin rights.
english.FinishedLabel=On first launch we'll connect your AI (Claude, ChatGPT, Gemini or Grok) and set up your character.%n%nTo open the chat while playing, click the Maple Helper icon by the clock.
english.WizardSelectDir=Where to install?
english.SelectDirLabel3=[name] will be installed in this folder. You can leave it as it is.
english.WelcomeLabel1=Welcome to Maple Helper
english.FinishedHeadingLabel=Maple Helper is ready!

[CustomMessages]
; these are Inno's custom messages (the [Messages] section ignores them)
hebrew.AdditionalIcons=קיצורי דרך:
hebrew.CreateDesktopIcon=קיצור דרך על &שולחן העבודה
hebrew.LaunchProgram=לפתוח את %1 עכשיו
english.LaunchProgram=Open %1 now
; asked once the app is removed (never when silent), as a Windows task dialog with two labelled choices (a plain
; Yes/No box looked nothing like the app). Keeping is the first choice, so Enter keeps everything. Deleting removes
; the saved API keys too: the uninstaller does it itself (Settings, which the text used to point at, is gone by then)
hebrew.DeleteUserDataTitle=למחוק גם את הנתונים שלכם ב-Maple Helper?
hebrew.DeleteUserData=המחיקה כוללת את הדמויות, היסטוריית הצ'אט, ההגדרות וההתחברויות ל-Gemini ול-Grok שנשמרו בתיקייה:%n%1%n%nוגם את מפתחות ה-API שנשמרו במנהל האישורים של Windows.%n%nאי אפשר לבטל את המחיקה.
hebrew.DeleteUserDataKeep=להשאיר את הנתונים%nהכל יחכה לכם בהתקנה הבאה.
hebrew.DeleteUserDataDelete=למחוק את הנתונים%nהדמויות, ההיסטוריה, ההגדרות ומפתחות ה-API.
english.DeleteUserDataTitle=Also delete your Maple Helper data?
english.DeleteUserData=This deletes your characters, chat history, settings and the Gemini and Grok sign-ins kept in:%n%1%n%nand the API keys saved in Windows Credential Manager.%n%nThis can't be undone.
english.DeleteUserDataKeep=Keep my data%nEverything will be there when you reinstall.
english.DeleteUserDataDelete=Delete my data%nCharacters, history, settings and API keys.

[Code]
var
  KbDecided, KbInstall: Boolean;

// Windows Credential Manager: where the app keeps the players' API keys (Python keyring, service "MapleHelper")
function CredDelete(TargetName: String; CredType: Cardinal; Flags: Cardinal): Integer;
  external 'CredDeleteW@advapi32.dll stdcall';

// "2026.10.02.0638" from <Dir>\meta.json, or '' when Dir holds no usable KB (same test as store.kb_dir)
function KbVersionIn(Dir: String): String;
var
  S: AnsiString;
  P: Integer;
begin
  Result := '';
  if not FileExists(Dir + '\index.json') or not LoadStringFromFile(Dir + '\meta.json', S) then
    Exit;
  P := Pos('"version"', S);
  if P = 0 then
    Exit;
  S := Copy(S, P + Length('"version"'), Length(S));
  P := Pos('"', S);
  if P = 0 then
    Exit;
  S := Copy(S, P + 1, Length(S));
  P := Pos('"', S);
  if P > 0 then
    Result := Copy(S, 1, P - 1);
end;

// Skip the KB when this one is already installed, or when the app's downloaded KB (which wins over
// the bundled one) is as new. Decided once: the answer must not change while the KB's files go in.
// Versions are zero-padded UTC timestamps, so a plain string comparison orders them.
function KbNeedsInstall(): Boolean;
var
  Installed, Downloaded: String;
begin
  if not KbDecided then
  begin
    KbDecided := True;
    Installed := KbVersionIn(ExpandConstant('{app}\_internal\data\kb'));
    Downloaded := KbVersionIn(ExpandConstant('{userappdata}\MapleHelper\kb'));
    KbInstall := ('{#KbVersion}' = '') or (Installed = '') or
                 ((Installed <> '{#KbVersion}') and (CompareStr(Downloaded, '{#KbVersion}') < 0));
    Log(Format('KB: bundled %s, installed %s, downloaded %s -> install: %d', ['{#KbVersion}',
               Installed, Downloaded, Ord(KbInstall)]));
  end;
  Result := KbInstall;
end;

// An update runs right after the app quits: wait (up to 30 s) until it has really exited,
// so no file is still in use while it is replaced (a half-updated install otherwise).
function InitializeSetup(): Boolean;
var
  i: Integer;
begin
  i := 0;
  // only a silent self-update waits here: a player who runs the installer by hand would stare at nothing for
  // 30 s (seen in testing); the wizard's own "close applications" step handles a running app then
  if not WizardSilent then
  begin
    Result := True;
    exit;
  end;
  while CheckForMutexes('MapleHelperRunning') and (i < 60) do
  begin
    Sleep(500);
    i := i + 1;
  end;
  // still running (hung on quit): stop it, a file in use can't be replaced and a silent setup would abort halfway
  if CheckForMutexes('MapleHelperRunning') then
  begin
    // no /T: this setup runs as the app's child, and killing the tree killed the update itself (found in testing)
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "Maple Helper.exe"', '', SW_HIDE, ewWaitUntilTerminated, i);
    Sleep(1500);
  end;
  Result := True;
end;

// The saved API keys: keyring stores one under the target "MapleHelper" and, when a second is saved, moves the
// first to "<user>@MapleHelper". Each is a generic credential (type 1) of this Windows user; a missing one is fine
procedure DeleteApiKeys();
var
  Targets: TArrayOfString;
  I: Integer;
begin
  SetArrayLength(Targets, 5);
  Targets[0] := 'MapleHelper';
  Targets[1] := 'anthropic_api_key@MapleHelper';
  Targets[2] := 'openai_api_key@MapleHelper';
  Targets[3] := 'gemini_api_key@MapleHelper';
  Targets[4] := 'xai_api_key@MapleHelper';
  for I := 0 to GetArrayLength(Targets) - 1 do
    if CredDelete(Targets[I], 1, 0) <> 0 then
      Log('deleted the saved key ' + Targets[I]);
end;

// After the app is removed: ask whether the player's own data goes too (SEC-12: chats, characters and the Grok and
// Gemini sign-ins stayed on disk), with the saved API keys. Only when the player says so; a silent uninstall keeps it
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  Choices: TArrayOfString;
begin
  if (CurUninstallStep <> usPostUninstall) or UninstallSilent then
    Exit;
  DataDir := ExpandConstant('{userappdata}\MapleHelper');
  if not DirExists(DataDir) then
    Exit;
  // the first choice (IDYES) keeps: it has the focus. TaskDialogMsgBox takes no default-button flag; a dialog
  // that fails returns 0, which keeps too
  // (the choices are built first: a line of [Code] that starts with "[" is read as a new section)
  SetArrayLength(Choices, 2);
  Choices[0] := CustomMessage('DeleteUserDataKeep');
  Choices[1] := CustomMessage('DeleteUserDataDelete');
  if TaskDialogMsgBox(CustomMessage('DeleteUserDataTitle'), FmtMessage(CustomMessage('DeleteUserData'), [DataDir]),
                      mbConfirmation, MB_YESNO, Choices, 0) = IDNO then
  begin
    DeleteApiKeys();
    if not DelTree(DataDir, True, True, True) then
      Log('could not delete all of ' + DataDir);
  end;
end;

// Uninstalling while the app runs left its files behind (in use): close it first
function InitializeUninstall(): Boolean;
var
  i: Integer;
begin
  if CheckForMutexes('MapleHelperRunning') then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "Maple Helper.exe"', '', SW_HIDE, ewWaitUntilTerminated, i);
    Sleep(1500);
  end;
  Result := True;
end;
