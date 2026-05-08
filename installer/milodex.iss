; Inno Setup script for the Milodex desktop application.
;
; Produces: dist\Milodex-Setup-v{version}.exe
; Installs to: %LOCALAPPDATA%\Programs\Milodex\  (per-user, no UAC elevation)
;
; Design rationale — see ADR 0037 (docs/adr/0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md):
;   - Per-user %LOCALAPPDATA% install mirrors modern Windows desktop conventions
;     (VS Code, Discord, GitHub Desktop, Slack).  No admin prompt, no UAC.
;   - Writable runtime data lives at %LOCALAPPDATA%\Milodex\ (separate from the
;     bundle at %LOCALAPPDATA%\Programs\Milodex\).  Uninstalling the bundle does
;     NOT touch the user's data directory — paper-trading history survives a
;     reinstall cycle.
;   - Unsigned build per ADR 0037.  SmartScreen workaround is documented in
;     docs/INSTALL.md.  Code-signing posture is reversible without architectural
;     rework: add SignTool= to [Setup] and sign the output EXE.
;
; Build:
;   iscc installer\milodex.iss
; from the repo root.  Requires Inno Setup 6 (https://jrsoftware.org/isdl.php).
;
; TODO: wire AppVersion from pyproject.toml via a pre-build script rather than
; editing this constant manually per release.

#define MyAppVersion "0.5.0"
#define MyAppName "Milodex"
#define MyAppPublisher "Zack Meacham"
#define MyAppExeName "Milodex.exe"

; Stable application GUID.  Same GUID = in-place upgrade; different GUID = parallel
; install.  Do NOT change this value between releases — it is the upgrade key.
; Generated once (2026-05-08) and baked in.
#define MyAppId "{{B7F3C2A1-4E8D-4F1A-9C5B-2D6E8F0A3B7C}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppSupportURL=https://github.com/zdm80/milodex
AppUpdatesURL=https://github.com/zdm80/milodex/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; OutputDir is resolved relative to the .iss file's location. Using ..\dist
; lands the installer at <repo_root>\dist\Milodex-Setup-vX.Y.Z.exe alongside
; PyInstaller's <repo_root>\dist\Milodex\ bundle — both build artifacts in
; the conventional dist/ directory. The build script's hash step looks here.
OutputDir=..\dist
OutputBaseFilename={#MyAppName}-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Per-user install: no UAC elevation required.
PrivilegesRequired=lowest
; Target 64-bit Windows.  Milodex ships x64 binaries (Python 3.11+ on Windows
; is x64-only on the builds we produce).
ArchitecturesInstallIn64BitMode=x64compatible
; Register an uninstaller with an icon so it shows correctly in Settings > Apps.
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Desktop shortcut is opt-in (unchecked by default) — the Start Menu shortcut
; is always created and is the primary launch point.
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Copy the entire PyInstaller --onedir output.  ignoreversion means the
; installer overwrites existing files without version-checking — appropriate
; for PyInstaller output which does not embed Windows version resources.
Source: "..\dist\Milodex\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch Milodex immediately after installation completes.
; nowait: installer does not wait for the app to exit.
; skipifsilent: skipped in silent (/SILENT or /VERYSILENT) installs.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
