# ADR 0037 — Distribution model: PyInstaller --onedir + Inno Setup, unsigned with documented SmartScreen workaround

**Status:** Accepted · 2026-05-08
**Related:** [PHASE5_PLANNING.md §4.7](../PHASE5_PLANNING.md), [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md) (PySide6 + Qt Quick runtime; PyInstaller as default candidate), [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md) (Phase 5 (b)+(c) scope, installer ships after observability surface), [FOUNDER_INTENT.md](../FOUNDER_INTENT.md) (priorities #3 accessibility and #4 shareability), [VISION.md](../VISION.md), [CLAUDE.md](../../CLAUDE.md) (Phase 1 capital under $1,000)

## Context

PHASE5_PLANNING.md §4.7 left distribution open as the last unresolved Phase 5 scope question. The Phase 5 GUI ships, but a working GUI on the developer's own machine is not the deliverable — the deliverable is a desktop application a non-developer can install and run on a clean Windows machine. C-2, the second of three Phase 5 exit criteria ([ADR 0034](0034-phase-5-scope-orders-observability-before-features.md)), is explicit:

> *Distributable installer produces a working Milodex install on a clean machine. The §4.7 distribution-model ADR is landed and references this exit. Friend-tested at least once on a non-developer machine — installation, first launch, and observability-surface rendering all work without operator intervention. Code-signing posture is explicit and recorded (signed, or unsigned with documented SmartScreen workaround).*

§4.7 articulated four candidate paths:

- **(α) GitHub clone + `pip install -e .`** — developer-only. Ruled out for failing FOUNDER_INTENT priorities #3 and #4 for a non-developer audience.
- **(β) PyInstaller binary (single-file or single-folder)** — pre-bundled Python + Qt + Milodex. Default candidate per [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md). Mid-friction.
- **(γ) Native installer (MSI / NSIS / WiX / Inno Setup)** — most polished install UX. Builds on top of PyInstaller in most workflows.
- **(δ) Containerized distribution (Docker)** — ruled out for not fitting "polished desktop product" intent.

This ADR resolves §4.7 by choosing one of the remaining candidates and documenting the code-signing posture. It is the last scope-bearing ADR before the installer PR opens; the Phase-5-closes ADR follows after the installer ships.

The decision is shaped by four constraints that interact:

1. **Audience.** FOUNDER_INTENT priorities #3 (accessibility) and #4 (shareability) — a hiring manager, peer, or curious friend should be able to download Milodex, install it, and have it work on a clean Windows machine without invoking a terminal. The friend-test in C-2 is the acceptance test.

2. **Budget.** [CLAUDE.md](../../CLAUDE.md) caps Phase 1 capital at $1,000. Production-grade Authenticode code-signing certificates from CAs (DigiCert, Sectigo, GlobalSign) cost ~$300–500/year — a recurring cost difficult to justify when the actual distribution audience is "a few peers and friends." OV (Organization Validation) certs require business registration the founder doesn't have; IV (Individual Validation) certs are slowly being phased out by major CAs in favor of OV. EV (Extended Validation) certs are $400-700/year and require hardware tokens. None of these are appropriate Phase 5 commitments.

3. **PyInstaller's distribution reality.** PyInstaller binaries — particularly `--onefile` — have a well-documented history of false-positive antivirus detection. The unpack-at-runtime mechanic that PyInstaller uses to bootstrap a frozen Python interpreter resembles the unpack-at-runtime mechanic some malware uses. Major AV products (Windows Defender, Avast, McAfee, Malwarebytes) flag unsigned PyInstaller `--onefile` binaries on a meaningful percentage of clean machines. `--onedir` mode is meaningfully better — the Python interpreter and DLLs sit on disk as ordinary files, no runtime unpack — but is not immune. Code signing helps with SmartScreen but does not reliably eliminate Defender's machine-learning-based false positives.

4. **Windows-first.** Per the founder's primary platform ([CLAUDE.md](../../CLAUDE.md): "Windows-first, PowerShell-first"). macOS and Linux distribution are out of scope for Phase 5; recording that explicitly is part of this ADR.

## Decision

1. **Bundle: PyInstaller `--onedir` mode, not `--onefile`.** The Milodex GUI bundles into a directory containing the Python runtime, PySide6 / Qt6, the bundled fonts (Newsreader, Public Sans, JetBrains Mono), the QML source tree, and Milodex Python source. The `data/milodex.db` event store and `logs/` directory are *not* bundled — they belong to the user's runtime data, not the application bundle.

2. **Installer wrapper: Inno Setup.** The PyInstaller `--onedir` output is wrapped in an Inno Setup script (`installer/milodex.iss`) that produces a single signed-or-unsigned `Milodex-Setup-vX.Y.Z.exe`. The installer:
   - installs the application files to `%LOCALAPPDATA%\Programs\Milodex\` (per-user, no admin elevation required),
   - creates a Start Menu shortcut and an optional desktop shortcut,
   - registers an uninstaller,
   - establishes `%LOCALAPPDATA%\Milodex\` as the writable data root for `data/`, `logs/`, and `~/.milodex/state/` equivalents.

3. **Code-signing posture: unsigned, with SmartScreen workaround documented.** The Phase 5 build does not pay for an Authenticode certificate. The README and a new `docs/INSTALL.md` document the SmartScreen "More info → Run anyway" path with screenshots, framed honestly: "Milodex is unsigned because Phase 1 doesn't justify the recurring certificate cost; here's how to verify the binary's hash against the GitHub release, and here's how to bypass SmartScreen if you've satisfied yourself the binary is legitimate." This satisfies C-2's "code-signing posture is explicit and recorded" requirement.

4. **Auto-update: deferred to Phase 6+.** Phase 5 ships with a manual update path: download new installer, run it, the installer overwrites the previous `--onedir` files. No Sparkle-equivalent infrastructure, no in-app "check for updates," no version-skew detection at startup. The README documents the manual update path.

5. **Build pipeline: manual for Phase 5; CI deferred to Phase 6+.** The first installer build is produced manually by running `pyinstaller installer/milodex.spec && iscc installer/milodex.iss` on the founder's Windows machine. The result is uploaded as a GitHub Release asset. CI-driven builds — GitHub Actions Windows runner producing the installer per tag — are scope for Phase 6+ once the build process is empirically stable.

6. **Platform scope: Windows only for Phase 5.** `Milodex-Setup-*.exe` is a Windows installer producing a Windows binary. macOS (`.dmg`/`.app`/notarization) and Linux (AppImage / Flatpak / `.deb`) are explicitly out of Phase 5 scope. Phase 6+ may revisit if a real audience demand emerges; the architecture (Python + PySide6 + Qt) supports cross-platform packaging when the time comes, but doing it now would dilute the C-2 exit on the platform that actually matters.

7. **Antivirus false-positive monitoring.** The release process includes a one-time submission of the unsigned installer to Microsoft's [submit-a-sample](https://www.microsoft.com/en-us/wdsi/filesubmission) endpoint with a "potentially unwanted (PUP)" / "false positive" classification before each release tag, plus VirusTotal verification post-build. False-positive findings get documented in `docs/INSTALL.md` so a friend running the installer can compare the hash against what's already been verified.

## Rationale

**`--onedir` over `--onefile` is the right PyInstaller mode for this distribution.** The startup-time difference alone justifies it (`--onefile` extracts a multi-megabyte payload to a temp directory on each launch — measurable seconds even on a fast SSD), but the AV behavior gap is the load-bearing reason. `--onedir` puts the Python runtime and PySide6 DLLs on disk as ordinary signed Microsoft-and-Qt-Group binaries that AV engines have already classified as benign through millions of installs. The Milodex-specific .exe shim is the only file that's "novel," and it's a small launcher that imports the bundled Python — much less suspicious than a self-extracting opaque blob. `--onedir` also dramatically simplifies the future code-signing path if and when it lands: the founder signs only the Milodex launcher, not the entire bundled Python+Qt payload.

**Inno Setup over alternatives (NSIS, WiX, MSI direct).** All four produce a Windows installer; the differences are in author ergonomics and output character.
- *NSIS* is similarly mature but its scripting language is a generation older and more frustrating to debug.
- *WiX* (which produces native MSI files) is more powerful but materially more complex; MSI is the right choice when group-policy-deployment, MSI transforms, or enterprise deployment scenarios matter — none of which apply here.
- *Inno Setup* sits in the sweet spot for solo-developer Windows installers: Pascal-like scripting, clean documentation, ubiquitous in the Windows ecosystem (used by Python's own installers, JetBrains tools, Audacity, OBS, dozens of projects familiar to the audience). The output exe is small, the script files version-control well, the build invocation (`iscc installer.iss`) is one command.

The Inno Setup output's first-run UX is what FOUNDER_INTENT priority #4 (shareability) cares about: a friend double-clicks the installer, sees a familiar Windows install wizard, clicks Next a few times, and Milodex appears in their Start Menu. That experience reads as "real software" in a way that "extract this zip and run the .exe inside" does not.

**Unsigned with documented SmartScreen workaround over paying for a cert at Phase 5.** The math doesn't favor the cert at this stage:

- **Cost.** ~$300–500/year, recurring. That's 30–50% of the entire Phase 1 capital budget. The cert buys a clean SmartScreen experience but does not reliably eliminate Defender false-positives on PyInstaller binaries — multiple developers have documented signed-and-still-flagged scenarios in PyInstaller's GitHub issues over the years.
- **Audience.** Phase 5's distribution audience is "a few peers and friends" plus the operator. SmartScreen's "More info → Run anyway" path is one extra click. A peer or friend, by definition, is willing to click through a one-time warning if they trust the source — and the README's hash-verification instructions give them a way to verify the binary independently.
- **Reversibility.** This is not a one-way decision. The next release after a cert lands becomes a signed installer with no other change. No data migration, no code change, no architectural lock-in.
- **Honesty.** A Phase-5-priced project ($1k cap) with a $400/year cert reads as overcommitted-to-the-distribution-pipeline at the expense of capital that could go to better data sources or actual paper-trading runway. The honest posture is "I'm not yet at the audience scale that justifies the cert" — and the documented SmartScreen workaround is the explicit acknowledgment of that.

The combination — unsigned, with SmartScreen workaround documented and SHA-256 hashes published per release — satisfies C-2's "code-signing posture is explicit and recorded" without spending money the project doesn't have.

**Auto-update deferral is honest scope-bounding.** Auto-update is genuinely valuable and there are good Python options (`tufup` from the TUF project, Sparkle bindings, custom HTTPS-fetch logic). But the infrastructure is multi-PR scope: signed update bundles, version comparison, rollback mechanism, "do not auto-update during a live trading session" gating, and the per-Defender-false-positive-per-release surface. None of these advance Phase 5's exit criteria. The manual "download new installer, run it" path is sufficient for an audience of size measured in the single digits.

**Per-user `%LOCALAPPDATA%` install over Program Files.** Program Files installs require admin elevation, which is a UX speed-bump and a friction point that violates priority #3 (accessibility). They're appropriate for software that has to be system-wide (drivers, services, multi-user installs); Milodex is single-user, single-session, and does not need to be visible to other Windows accounts. `%LOCALAPPDATA%\Programs\Milodex\` is the convention modern Windows desktop apps follow (VS Code, Discord, GitHub Desktop, Slack) — installs without elevation, updates without elevation, uninstalls cleanly, and never asks the user "Do you want to allow this app to make changes to your device?"

**Writable data root at `%LOCALAPPDATA%\Milodex\`.** The bundle is read-only; runtime state (the SQLite event store, logs, the `~/.milodex/state/gui_theme.json` equivalent) lives at a separate writable location. This separation matters for two reasons. (1) An update overwrites the bundle without touching user data — the founder's actual paper trades survive the install. (2) Antivirus engines treat write-back-to-installation-folder as a malware-adjacent behavior; keeping runtime writes outside the bundle reduces false-positive risk further.

**Windows-only platform scope.** Per [CLAUDE.md](../../CLAUDE.md), Windows-first. The audience is the founder's peers (likely Windows or macOS) and a hypothetical hiring manager. Cross-platform packaging is real work — macOS notarization in particular is a separate capital outlay (Apple Developer Program, ~$99/year) plus engineering — and isn't gated on this ADR. Phase 5 ships Windows; Phase 6+ revisits if a non-Windows distribution case emerges.

## Consequences

- **Installer PR ships a directory `installer/`** containing at minimum: `milodex.spec` (PyInstaller spec file with explicit data-file inclusions for fonts, QML, configs, icons), `milodex.iss` (Inno Setup script), and a build script (`build_installer.ps1` for Windows PowerShell). The PyInstaller spec specifies `--onedir`, target name `Milodex.exe`, icon path, and explicit data inclusions for fonts and QML.
- **A new `docs/INSTALL.md`** documents the user-facing install flow: download from GitHub Releases, SmartScreen workaround with screenshot, hash verification instructions, expected install location, where data lives, how to uninstall.
- **The `pyproject.toml` `[project.gui-scripts]` entry remains.** `milodex gui` from a `pip install -e .` install still works for developers; the installer is a parallel distribution path, not a replacement.
- **`%LOCALAPPDATA%\Milodex\` becomes the canonical user data root** for installed builds. The codebase already resolves `data/` relative to repo root for editable installs; the installed build needs a runtime path resolution that prefers `%LOCALAPPDATA%\Milodex\` when it exists. This is a small `core/` change in the installer PR.
- **Phase 5 exit criterion C-2 is testable** the moment the installer PR ships: the founder builds the installer, downloads it on a clean Windows VM (or a friend's machine), runs through SmartScreen, completes the install, launches the app, observes that the Anchor surface and Strategy Bank surface render correctly. The friend-test is one round-trip.
- **The unsigned posture is reversed cleanly later.** When Phase 6+ work justifies a cert (e.g., a public release, a portfolio submission), the only changes are: Inno Setup script gains `SignTool=signtool`, the GitHub release asset is signed, the README's SmartScreen section is replaced with "Milodex is signed by [Founder Name]". No architectural rework.
- **Auto-update is a Phase 6+ ADR.** When that lands, candidate mechanisms include `tufup` (cryptographically-verified update bundles), Sparkle-via-PyUpdater, or a custom HTTPS+SHA256 fetcher. Each has security and complexity trade-offs.
- **macOS / Linux distribution are explicitly Phase 6+ candidates.** A future ADR can revisit. The architecture supports it (PySide6 + Qt run on both platforms; PyInstaller has macOS and Linux backends), but the engineering and signing work is meaningful: `.dmg` + Apple notarization + Apple Developer Program for macOS; AppImage or Flatpak for Linux. None of those are gated on this ADR's resolution.
- **PHASE5_PLANNING.md §4.7 closes.** The strikethrough+ADR-reference pattern from §4.1 / §4.2 / §4.6 applies. §4.7 marks **Decided 2026-05-08: (γ) PyInstaller `--onedir` + Inno Setup, unsigned with documented SmartScreen workaround. ADR 0037.**
- **Phase 5 close-out unblocks.** Two deliverables remain: (1) the installer PR implementing this ADR, and (2) the Phase-5-closes ADR — analogue of [ADR 0031](0031-phase-4-is-closed-and-phase-5-may-open.md) — that records what shipped, what carried forward to Phase 6 (Kanban as anchor program per [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md)), and what remains deferred. The installer PR ships first; the Phase-5-closes ADR follows once the installer is friend-tested green.
- **Antivirus false-positive monitoring becomes an ongoing operator responsibility.** Per-release: build, hash, submit to Microsoft's false-positive endpoint, verify on VirusTotal, document any findings in `docs/INSTALL.md`. The first time this gets unwieldy, that's the signal to revisit code signing.
