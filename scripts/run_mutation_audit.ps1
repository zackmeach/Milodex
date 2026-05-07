# Mutation-testing audit runner (PowerShell).
#
# Reproduces the test-efficacy audit documented in docs/TEST_EFFICACY_AUDIT.md.
# Usage:
#   pwsh scripts/run_mutation_audit.ps1                # all four targets
#   pwsh scripts/run_mutation_audit.ps1 -Target risk   # one target only
#
# Targets: risk | promotion-state-machine | promotion-manifest | exec-state | all
#
# Notes:
#   - mutmut 2.x is required because 3.x refuses to run on native Windows
#     (boxed/mutmut#397). pyproject.toml dev deps pin this.
#   - Python 3.13 is required because mutmut 2.5.x's pony ORM dependency
#     fails to deepcopy translator state under Python 3.14
#     (TypeError on itertools.count). 3.13 works as of this writing.
#   - PYTHONIOENCODING=utf-8 / NO_COLOR=1 / PY_COLORS=0 prevent cp1252
#     bytes leaking into mutmut's utf-8 streaming output decoder.

param(
    [ValidateSet("risk", "promotion-state-machine", "promotion-manifest", "exec-state", "all")]
    [string]$Target = "all",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$env:PYTHONIOENCODING = "utf-8"
$env:NO_COLOR = "1"
$env:PY_COLORS = "0"

$targets = @{
    "risk"                     = @{ Path = "src/milodex/risk/evaluator.py";        Tests = "tests/milodex/risk" }
    "promotion-state-machine"  = @{ Path = "src/milodex/promotion/state_machine.py"; Tests = "tests/milodex/promotion" }
    "promotion-manifest"       = @{ Path = "src/milodex/promotion/manifest.py";     Tests = "tests/milodex/promotion" }
    "exec-state"               = @{ Path = "src/milodex/execution/state.py";        Tests = "tests/milodex/execution" }
}

function Invoke-MutationRun {
    param([string]$Name, [string]$SrcPath, [string]$TestDir)

    Write-Host "=== Running mutation testing on $Name ===" -ForegroundColor Cyan
    Write-Host "  source: $SrcPath"
    Write-Host "  tests : $TestDir"

    if (Test-Path .mutmut-cache) { Remove-Item .mutmut-cache -Force }

    & $Python -m mutmut run `
        --paths-to-mutate $SrcPath `
        --tests-dir $TestDir `
        --runner "python -m pytest --no-cov -x -q --tb=line -p no:cacheprovider $TestDir" `
        --no-progress --simple-output --CI

    Write-Host ""
    Write-Host "Results for $Name :" -ForegroundColor Yellow
    & $Python -m mutmut results
}

if ($Target -eq "all") {
    foreach ($key in $targets.Keys) {
        Invoke-MutationRun -Name $key -SrcPath $targets[$key].Path -TestDir $targets[$key].Tests
    }
} else {
    $t = $targets[$Target]
    Invoke-MutationRun -Name $Target -SrcPath $t.Path -TestDir $t.Tests
}
