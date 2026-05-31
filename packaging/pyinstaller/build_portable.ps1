param(
    [string]$PythonExe,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$SpecPath = Join-Path $PSScriptRoot "BazaarCoach.spec"

Push-Location $RepoRoot
try {
    if ($PythonExe) {
        $ResolvedPythonExe = $PythonExe
    }
    else {
        $VenvPython = Join-Path $RepoRoot "venv312\Scripts\python.exe"
        if (Test-Path -LiteralPath $VenvPython) {
            $ResolvedPythonExe = $VenvPython
        }
        else {
            $PathPython = Get-Command python -ErrorAction Stop
            $ResolvedPythonExe = $PathPython.Source
        }
    }

    Write-Host "Using Python: $ResolvedPythonExe"

    # Generate Windows file-version metadata before PyInstaller reads the spec
    $GenVersionScript = Join-Path $PSScriptRoot "gen_version_info.py"
    & $ResolvedPythonExe $GenVersionScript
    if ($LASTEXITCODE -ne 0) {
        throw "gen_version_info.py failed with exit code $LASTEXITCODE"
    }

    $args = @($SpecPath, "--noconfirm")
    if (-not $NoClean) {
        $args += "--clean"
    }
    & $ResolvedPythonExe -m PyInstaller @args
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }
    Write-Host "Portable package: $(Join-Path $RepoRoot 'dist\BazaarCoach')"
}
finally {
    Pop-Location
}
