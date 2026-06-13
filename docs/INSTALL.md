# Installing Milodex

## What is this?

Milodex is a personal autonomous trading system distributed as a Windows desktop application. The latest installer is available on the [GitHub Releases page](https://github.com/zackmeach/Milodex/releases). Releases are currently published manually — check the Releases page for the most recent `Milodex-Setup-vX.Y.Z.exe`.

---

## Installing on Windows

1. Download the latest `Milodex-Setup-vX.Y.Z.exe` from the GitHub Releases page.

2. Double-click the installer.

3. **Windows SmartScreen will likely show a "Windows protected your PC" blue dialog** because the installer is unsigned. This is expected. Click **More info**, then **Run anyway**. See [Why is the installer unsigned?](#why-is-the-installer-unsigned) below for the honest explanation.

4. The Inno Setup wizard opens. Click through **Next → Install**. The default install location is `%LOCALAPPDATA%\Programs\Milodex\` and does not require administrator permissions.

5. Once installation completes, Milodex appears in the Start Menu under **Milodex**. Launch it from there.

6. On first launch, the GUI opens to the Operations tab. The Strategy Bank tab shows "Loading..." briefly while the application reads the local data store. If you have never run a backtest, the strategy bank shows empty-state messages — this is normal.

---

## Verifying the installer is legitimate

The SHA-256 hash of each release installer is published in the release notes on the GitHub Releases page. To verify the hash before running the installer, open PowerShell and run:

```powershell
Get-FileHash -Algorithm SHA256 .\Milodex-Setup-vX.Y.Z.exe
```

Compare the output hash to the one published in the release notes. If they do not match, do not run the installer — contact the maintainer.

---

## Why is the installer unsigned?

Milodex is unsigned because the cost of Authenticode code-signing certificates ($300–500/year, recurring) is not appropriate for Phase 1, which caps trading capital at $1,000. A certificate would produce a cleaner SmartScreen experience but would not reliably eliminate Windows Defender false-positives on PyInstaller binaries — multiple developers have documented signed-and-still-flagged scenarios in PyInstaller's issue tracker. The unsigned posture is reversible without architectural rework: when a certificate becomes justified, the only changes are adding a `SignTool=` directive to the Inno Setup script and signing the output EXE. Full rationale is recorded in [ADR 0037](adr/0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md).

---

## Where data lives

After installation, runtime data is stored at:

| Directory | Purpose |
|-----------|---------|
| `%LOCALAPPDATA%\Milodex\data\` | SQLite event store (`milodex.db`), trade history, strategy runs |
| `%LOCALAPPDATA%\Milodex\logs\` | Runtime logs, kill-switch state |
| `%LOCALAPPDATA%\Milodex\market_cache\` | Cached market data (Parquet files) |

The application bundle itself (Python runtime, Qt libraries, QML) is at `%LOCALAPPDATA%\Programs\Milodex\` and is read-only at runtime.

**Uninstalling Milodex removes the bundle but does not delete `%LOCALAPPDATA%\Milodex\`.** Your historical paper-trading state, logs, and cached market data survive an uninstall and reinstall cycle. Remove `%LOCALAPPDATA%\Milodex\` manually if you want a full wipe.

---

## Updating Milodex

There is currently no auto-update mechanism (auto-update/CI are deferred per ADR 0037). To update:

1. Download the newer `Milodex-Setup-vX.Y.Z.exe` from the Releases page.
2. Run the installer.
3. The installer overwrites the previous bundle in `%LOCALAPPDATA%\Programs\Milodex\`.
4. Your data at `%LOCALAPPDATA%\Milodex\` is preserved.

---

## Uninstalling

Use **Windows Settings → Apps → Installed apps → Milodex → Uninstall**, or run the uninstaller directly at:

```
%LOCALAPPDATA%\Programs\Milodex\unins000.exe
```

The uninstaller removes the application bundle. It does not touch `%LOCALAPPDATA%\Milodex\`. To fully remove all Milodex data, delete that directory manually after uninstalling.

---

## Build prerequisites (for developers)

If you are building the installer from source:

1. **Install Python dependencies** (includes PyInstaller):
   ```powershell
   pip install -e ".[dev]"
   ```

2. **Install Inno Setup 6** from [https://jrsoftware.org/isdl.php](https://jrsoftware.org/isdl.php). The free version is sufficient; no registration required.

3. **Run the build script** from the repo root, in any PowerShell:
   ```powershell
   .\installer\build_installer.ps1
   ```
   Works in both Windows PowerShell 5.1 (built-in to Windows) and PowerShell 7+ (`pwsh`). If execution policy blocks the script, run instead:
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File .\installer\build_installer.ps1
   ```

The script validates prerequisites, runs PyInstaller, smoke-tests the bundle, runs Inno Setup, computes the SHA-256 hash of the resulting installer, and prints all output paths.

---

## Antivirus false-positive monitoring

PyInstaller binaries occasionally trigger antivirus false-positives, particularly on machines running aggressive AV configurations. Milodex uses `--onedir` mode (not `--onefile`) specifically because it places the Python runtime and Qt DLLs on disk as ordinary signed Microsoft-and-Qt-Group files, which AV engines have already classified as benign through millions of installs.

Per-release process:

1. The installer is submitted to Microsoft's [submit-a-sample](https://www.microsoft.com/en-us/wdsi/filesubmission) portal with a "false positive" classification before the release tag is published.
2. The installer is verified on [VirusTotal](https://www.virustotal.com) post-build.
3. Any false-positive findings, their AV vendor, and their resolution status are documented in updates to this section.

If your antivirus flags the Milodex installer after you have verified the SHA-256 hash against the release notes, submit a false-positive report to your AV vendor and optionally open an issue on the GitHub repository.
