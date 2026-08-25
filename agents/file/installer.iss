; Inno Setup script for the Mascope File Agent.
;
; Compiled by build.ps1 -Installer, which passes the version defines:
;   ISCC /DAppVersion=v1.4.0 /DFileVersion=1.4.0.0 installer.iss
;
; build.ps1 -Sign additionally passes /DSignToolName=<name> together with a
; matching /S<name>=<command>, which turns on the signing blocks below. An
; unsigned build never defines SignToolName, so it compiles unchanged on any
; Inno Setup 6 and needs no signing tooling at all.
;
; Per-user install (no admin rights required, no UAC prompt): the agent
; lands in %LocalAppData%\Programs\Mascope File Agent. Settings, logs and
; runtime state live in %AppData%\Mascope\FileAgent, written by the agent
; itself, and are intentionally left in place on uninstall so upgrading
; or reinstalling never loses a customer's configuration.

#ifndef AppVersion
  #define AppVersion "dev"
#endif
#ifndef FileVersion
  #define FileVersion "0.0.0.0"
#endif

[Setup]
; AppId identifies the app to the uninstaller; never change it, or
; upgrades will install alongside the old version instead of replacing it
AppId={{7C1FBA1E-2C39-4A05-A2F8-6E4B77D5A11C}
AppName=Mascope File Agent
AppVersion={#AppVersion}
AppPublisher=Ultra Trace Systems Oy
AppPublisherURL=https://ultratrace.eu
AppSupportURL=https://github.com/ultra-trace-systems/mascope
DefaultDirName={autopf}\Mascope File Agent
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=Mascope-File-Agent-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\Mascope-File-Agent.exe
VersionInfoVersion={#FileVersion}
Compression=lzma2
SolidCompression=yes
#ifdef SignToolName
; Sign the setup executable, using the sign tool build.ps1 defined on the
; ISCC command line. SignedUninstaller signs the uninstaller stub that gets
; embedded in it - already the default once SignTool is set, but spelled out
; because it is a third billable signature and because it makes Setup write
; the language messages to a separate unins000.msg beside unins000.exe
; rather than appending them to the exe, which would break the signature.
SignTool={#SignToolName}
SignedUninstaller=yes
#endif

[Messages]
; Tell the user what happens next: the watched folder is not chosen at
; install time but in the guided setup on first start.
FinishedLabel=Setup has finished installing [name] on your computer.%n%nWhen the agent first starts, a guided setup runs in its console window and asks for your Mascope server address and the folder to watch for new data files.

[Tasks]
Name: "startup"; Description: "Start the File Agent automatically when you sign in to Windows"

[Files]
#ifdef SignToolName
; signcheck signs nothing: build.ps1 has already signed the exe by the time
; ISCC runs, and this aborts the compile if it has not. That ordering cannot
; be recovered later - Inno stores the source bytes verbatim and Authenticode
; covers the whole PE, so an unsigned payload can only be fixed by rebuilding
; - and it fails silently otherwise, shipping an installer that looks signed
; but drops an unsigned exe onto the customer's machine. Needs Inno Setup 6.3+.
Source: "dist\Mascope-File-Agent.exe"; DestDir: "{app}"; Flags: ignoreversion signcheck
#else
Source: "dist\Mascope-File-Agent.exe"; DestDir: "{app}"; Flags: ignoreversion
#endif

[Icons]
Name: "{userprograms}\Mascope File Agent"; Filename: "{app}\Mascope-File-Agent.exe"
Name: "{userstartup}\Mascope File Agent"; Filename: "{app}\Mascope-File-Agent.exe"; Tasks: startup

[Run]
Filename: "{app}\Mascope-File-Agent.exe"; Description: "Start the File Agent now (guided setup runs on first start)"; Flags: nowait postinstall skipifsilent
