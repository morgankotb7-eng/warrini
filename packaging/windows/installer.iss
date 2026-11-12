; Warrini Windows installer (Inno Setup 6).
; Build: iscc installer.iss /DAppVersion=0.1.0 /DSourceDir=<staged app folder> /DOutputDir=<dist>
; The staged folder holds: python\ (embedded Python with Warrini and the voice installed),
; ffmpeg\ (ffmpeg.exe, ffprobe.exe with libass) and warrini.ico.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\Warrini"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{6B0E3F52-6A4C-4C8E-9C1E-5A1E2A7D9F31}
AppName=Warrini
AppVerName=Warrini {#AppVersion}
AppVersion={#AppVersion}
AppPublisher=Mohamed Kotb
AppPublisherURL=https://github.com/morgankotb7-eng/warrini
AppSupportURL=https://github.com/morgankotb7-eng/warrini/issues
DefaultDirName={localappdata}\Programs\Warrini
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=Warrini-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\warrini.ico
UninstallDisplayIcon={app}\warrini.ico
UninstallDisplayName=Warrini (ورّيني)
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
CloseApplications=force

[Languages]
#ifexist "compiler:Languages\Arabic.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
english.Launch=Open Warrini
english.DesktopIcon=Create a desktop shortcut
#ifexist "compiler:Languages\Arabic.isl"
arabic.Launch=افتح ورّيني
arabic.DesktopIcon=اعمل اختصار على الـ Desktop
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ورّيني"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m warrini"; WorkingDir: "{app}"; IconFilename: "{app}\warrini.ico"; Comment: "Turn work procedures into narrated how-to videos"
Name: "{autodesktop}\ورّيني"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m warrini"; WorkingDir: "{app}"; IconFilename: "{app}\warrini.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m warrini"; WorkingDir: "{app}"; Description: "{cm:Launch}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; files Python writes at run time (bytecode caches) so the folder is removed completely.
; The user's guides live in %USERPROFILE%\Warrini and are never touched.
Type: filesandordirs; Name: "{app}"
