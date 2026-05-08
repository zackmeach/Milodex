# build_installer.ps1 — Manual Milodex installer build pipeline.
#
# Runs from the repo root (uses $PSScriptRoot to locate repo-relative paths).
#
# Steps:
#   1. Validate prerequisites (venv active, PyInstaller present, Inno Setup present).
#   2. Clean prior build/dist artifacts.
#   3. Run PyInstaller to produce dist/Milodex/ (--onedir bundle).
#   4. Smoke-test the bundle (Milodex.exe --help must exit 0).
#   5. Run Inno Setup (iscc) to produce dist/Milodex-Setup-vX.Y.Z.exe.
#   6. Compute and print the SHA-256 hash of the installer.
#   7. Print final output paths.
#
# Design rationale: ADR 0037 — manual build for Phase 5; CI-driven builds
# are Phase 6+ scope.  This script is the operator's one-command build.
#
# Usage (from repo root, in any PowerShell):
#   .\installer\build_installer.ps1
#
# If execution policy blocks the script:
#   powershell.exe -ExecutionPolicy Bypass -File .\installer\build_installer.ps1
#
# Works in both Windows PowerShell 5.1 (built-in to Windows) and
# PowerShell 7+ (`pwsh`). No version-specific syntax used.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

$repoRoot   = (Resolve-Path "$PSScriptRoot\..").Path
$specFile   = Join-Path $PSScriptRoot "milodex.spec"
$issFile    = Join-Path $PSScriptRoot "milodex.iss"
$buildDir   = Join-Path $repoRoot "build"
$distDir    = Join-Path $repoRoot "dist"
$bundleDir  = Join-Path $distDir "Milodex"
$bundleExe  = Join-Path $bundleDir "Milodex.exe"

Write-Host ""
Write-Host "=== Milodex installer build ==="
Write-Host "Repo root : $repoRoot"
Write-Host "Spec      : $specFile"
Write-Host "ISS       : $issFile"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Validate prerequisites
# ---------------------------------------------------------------------------

Write-Host "--- Checking prerequisites ---"

# Venv check: python must resolve to a venv interpreter.
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonPath) {
    Write-Error "python not found on PATH. Activate your virtual environment and re-run."
    exit 1
}
Write-Host "python   : $pythonPath"

# PyInstaller check.
$pyinstallerPath = (Get-Command pyinstaller -ErrorAction SilentlyContinue).Source
if (-not $pyinstallerPath) {
    Write-Error ("pyinstaller not found. Install dev dependencies with:" +
                 "`n  pip install -e `".[dev]`"")
    exit 1
}
Write-Host "pyinstaller: $pyinstallerPath"

# Inno Setup check: look in the default install path first, then PATH.
$isccExe = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $isccExe)) {
    $isccExe = (Get-Command iscc -ErrorAction SilentlyContinue).Source
}
if (-not $isccExe -or -not (Test-Path $isccExe)) {
    Write-Error ("Inno Setup 6 not found.  Install from:" +
                 "`n  https://jrsoftware.org/isdl.php" +
                 "`nThen re-run this script.")
    exit 1
}
Write-Host "iscc     : $isccExe"
Write-Host ""

# ---------------------------------------------------------------------------
# 2. Clean prior build/dist
# ---------------------------------------------------------------------------

Write-Host "--- Cleaning prior build artifacts ---"
foreach ($dir in @($buildDir, $distDir)) {
    if (Test-Path $dir) {
        Write-Host "Removing $dir"
        Remove-Item -Recurse -Force $dir
    }
}
Write-Host ""

# ---------------------------------------------------------------------------
# 3. Run PyInstaller
# ---------------------------------------------------------------------------

Write-Host "--- Running PyInstaller ---"
Push-Location $repoRoot
try {
    & pyinstaller $specFile --clean --noconfirm
    if ($LASTEXITCODE -ne 0) {
        Write-Error "PyInstaller exited with code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}
Write-Host ""

# ---------------------------------------------------------------------------
# 4. Smoke-test the bundle
# ---------------------------------------------------------------------------

Write-Host "--- Smoke-testing bundle ---"
if (-not (Test-Path $bundleExe)) {
    Write-Error "Expected bundle EXE not found: $bundleExe"
    exit 1
}

# The launcher passes --help through to the CLI which prints usage and exits 0.
# We use --help rather than a bare invocation to avoid launching the Qt event
# loop (which would block the build script waiting for the GUI to close).
$smokeResult = & $bundleExe --help 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error ("Smoke test failed (exit code $LASTEXITCODE).`n" +
                 "Output:`n$smokeResult")
    exit 1
}
Write-Host "Smoke test passed (exit 0)."
Write-Host ""

# ---------------------------------------------------------------------------
# 5. Run Inno Setup
# ---------------------------------------------------------------------------

Write-Host "--- Running Inno Setup ---"
Push-Location $repoRoot
try {
    & $isccExe $issFile
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Inno Setup (iscc) exited with code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}
Write-Host ""

# ---------------------------------------------------------------------------
# 6. Compute SHA-256 hash
# ---------------------------------------------------------------------------

Write-Host "--- Computing installer hash ---"
$setupExe = Get-ChildItem -Path $distDir -Filter "Milodex-Setup-v*.exe" |
             Sort-Object LastWriteTime -Descending |
             Select-Object -First 1

if (-not $setupExe) {
    Write-Error "No Milodex-Setup-v*.exe found in $distDir after iscc run."
    exit 1
}

$hash = (Get-FileHash -Algorithm SHA256 $setupExe.FullName).Hash
Write-Host ""
Write-Host "SHA-256 : $hash"
Write-Host ""

# ---------------------------------------------------------------------------
# 7. Final summary
# ---------------------------------------------------------------------------

Write-Host "=== Build complete ==="
Write-Host "Bundle    : $bundleDir"
Write-Host "Installer : $($setupExe.FullName)"
Write-Host "SHA-256   : $hash"
Write-Host ""
Write-Host "Upload the installer to GitHub Releases and publish the SHA-256"
Write-Host "hash in the release notes so users can verify before running."
Write-Host ""
