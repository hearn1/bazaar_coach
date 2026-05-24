<#
.SYNOPSIS
    Build a complete test artifact set (portable + installer) for local
    pre-release validation. No git or GitHub side effects.

.DESCRIPTION
    Wraps the existing portable + installer build flow into one command.
    Reads APP_VERSION from version.py without modifying it; both artifacts
    are produced with that version.

    Steps:
      1. pytest (skippable)
      2. packaging/pyinstaller/build_portable.ps1
      3. packaging/pyinstaller/smoke_test_portable.py (skippable)
      4. Compress-Archive -> dist/BazaarCoach-Portable-<version>.zip
      5. packaging/installer/build_installer.ps1
      6. Either silent-install (-Install) or print install command

.PARAMETER Install
    Run the freshly built installer silently (/VERYSILENT /SUPPRESSMSGBOXES
    /CURRENTUSER) after building. Default: print the install command and
    let the user invoke it manually.

.PARAMETER SkipTests
    Skip the pytest step.

.PARAMETER SkipSmoke
    Skip smoke_test_portable.py.

.EXAMPLE
    .\packaging\release\build_test.ps1

.EXAMPLE
    .\packaging\release\build_test.ps1 -SkipTests -SkipSmoke

.EXAMPLE
    .\packaging\release\build_test.ps1 -Install
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$SkipTests,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

function Read-AppVersion {
    param([string]$RepoRoot)
    $VersionFile = Join-Path $RepoRoot "version.py"
    $VersionText = Get-Content -Raw -Path $VersionFile
    if ($VersionText -match 'APP_VERSION\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }
    throw "Could not read APP_VERSION from $VersionFile"
}

function Resolve-VenvPython {
    param([string]$RepoRoot)
    $VenvPython = Join-Path $RepoRoot "venv312\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython) { return $VenvPython }
    $PathPython = Get-Command python -ErrorAction Stop
    return $PathPython.Source
}

Push-Location $RepoRoot
try {
    $AppVersion = Read-AppVersion -RepoRoot $RepoRoot
    $PythonExe = Resolve-VenvPython -RepoRoot $RepoRoot

    Write-Host "[BuildTest] Version: $AppVersion"
    Write-Host "[BuildTest] Python:  $PythonExe"

    if (-not $SkipTests) {
        Write-Host "[BuildTest] Running pytest..."
        & $PythonExe -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE" }
    } else {
        Write-Host "[BuildTest] Skipping pytest (-SkipTests)."
    }

    Write-Host "[BuildTest] Building portable package..."
    & (Join-Path $ScriptDir "..\pyinstaller\build_portable.ps1") -PythonExe $PythonExe
    if ($LASTEXITCODE -ne 0) { throw "Portable build failed with exit code $LASTEXITCODE" }

    $PortableDir = Join-Path $RepoRoot "dist\BazaarCoach"
    $PortableExe = Join-Path $PortableDir "BazaarCoach.exe"
    if (-not (Test-Path $PortableExe)) {
        throw "Expected portable executable not found: $PortableExe"
    }

    if (-not $SkipSmoke) {
        Write-Host "[BuildTest] Running portable smoke test..."
        & $PythonExe (Join-Path $ScriptDir "..\pyinstaller\smoke_test_portable.py") --exe $PortableExe
        if ($LASTEXITCODE -ne 0) { throw "Smoke test failed with exit code $LASTEXITCODE" }
    } else {
        Write-Host "[BuildTest] Skipping smoke test (-SkipSmoke)."
    }

    $ZipPath = Join-Path $RepoRoot "dist\BazaarCoach-Portable-$AppVersion.zip"
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    Write-Host "[BuildTest] Creating portable zip: $ZipPath"
    Compress-Archive -Path (Join-Path $PortableDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal

    Write-Host "[BuildTest] Building installer..."
    & (Join-Path $ScriptDir "..\installer\build_installer.ps1") -AppVersion $AppVersion
    if ($LASTEXITCODE -ne 0) { throw "Installer build failed with exit code $LASTEXITCODE" }

    $InstallerExe = Join-Path $RepoRoot "dist\installer\BazaarCoachSetup-$AppVersion.exe"
    if (-not (Test-Path $InstallerExe)) {
        throw "Expected installer not found: $InstallerExe"
    }

    Write-Host ""
    Write-Host "[BuildTest] Artifacts ready:"
    Write-Host "  Portable zip: $ZipPath"
    Write-Host "  Installer:    $InstallerExe"
    Write-Host ""

    $SilentArgs = "/VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART"
    if ($Install) {
        Write-Host "[BuildTest] Installing silently: $InstallerExe $SilentArgs"
        $proc = Start-Process -FilePath $InstallerExe -ArgumentList $SilentArgs -Wait -PassThru
        if ($proc.ExitCode -ne 0) { throw "Installer exited with code $($proc.ExitCode)" }
        Write-Host "[BuildTest] Installed. Launch via Start Menu shortcut 'Bazaar Coach ($AppVersion)'."
    } else {
        Write-Host "[BuildTest] To install locally for testing, run:"
        Write-Host "  Start-Process -FilePath '$InstallerExe' -ArgumentList '$SilentArgs' -Wait"
        Write-Host ""
        Write-Host "[BuildTest] Or double-click the installer for the GUI flow."
    }
}
finally {
    Pop-Location
}
