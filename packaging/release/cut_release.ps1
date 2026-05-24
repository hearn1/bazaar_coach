<#
.SYNOPSIS
    Cut a full Bazaar Coach release: bump version, build artifacts, tag, push,
    and publish a draft GitHub Release with both assets attached.

.DESCRIPTION
    Orchestrates the release flow end-to-end:
      1. Validate working tree clean, branch is main.
      2. Determine new version (auto-increment alpha/beta/rc suffix if -Version omitted).
      3. Validate v<NewVersion> tag does not exist locally or on origin.
      4. Write APP_VERSION in version.py and commit the bump.
      5. Run pytest.
      6. Build portable + run smoke test + zip.
      7. Build installer.
      8. Generate release notes (or copy -NotesFile content).
      9. Create annotated tag v<NewVersion>.
     10. Push main + tag.
     11. gh release create as draft pre-release with portable zip + installer attached.

    -DryRun performs all validation but skips every mutation (no version.py
    write, no commit, no tag, no push, no publish). Builds still run so
    artifacts are real and inspectable, but they reflect the CURRENT
    checked-in version, not the proposed new version.

.PARAMETER Version
    The release version (without leading 'v'). If omitted, the script
    auto-increments the trailing -alpha.N / -beta.N / -rc.N suffix in
    version.py. A plain x.y.z (no suffix) requires explicit -Version.

.PARAMETER NotesFile
    Path to a markdown file used as the release body. If omitted, a stub
    is generated from `git log <last-tag>..HEAD --oneline` and written to
    dist/release/release-notes-<NewVersion>.md.

.PARAMETER DryRun
    Skip all mutations. Validation and builds still run.

.PARAMETER SkipTests
    Skip the pytest step.

.PARAMETER Publish
    Publish the GitHub Release immediately. Default: create as --draft so
    notes can be edited in the GitHub UI before going live.

.EXAMPLE
    # Auto-increment alpha suffix, dry run first.
    .\packaging\release\cut_release.ps1 -DryRun

.EXAMPLE
    # Explicit version, create draft release.
    .\packaging\release\cut_release.ps1 -Version 0.2.0-alpha.4

.EXAMPLE
    # Explicit version, publish immediately (skip draft).
    .\packaging\release\cut_release.ps1 -Version 0.2.0 -Publish
#>
[CmdletBinding()]
param(
    [string]$Version,
    [string]$NotesFile,
    [switch]$DryRun,
    [switch]$SkipTests,
    [switch]$Publish
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$VersionFile = Join-Path $RepoRoot "version.py"

function Read-AppVersion {
    $text = Get-Content -Raw -Path $VersionFile
    if ($text -match 'APP_VERSION\s*=\s*"([^"]+)"') { return $Matches[1] }
    throw "Could not read APP_VERSION from $VersionFile"
}

function Write-AppVersion {
    param([string]$NewVersion)
    $text = Get-Content -Raw -Path $VersionFile
    $updated = [regex]::Replace($text, 'APP_VERSION\s*=\s*"[^"]+"', "APP_VERSION = `"$NewVersion`"")
    Set-Content -Path $VersionFile -Value $updated -NoNewline -Encoding UTF8
}

function Get-NextVersion {
    param([string]$Current)
    if ($Current -match '^(.*?-(?:alpha|beta|rc)\.)(\d+)$') {
        $prefix = $Matches[1]
        $n = [int]$Matches[2]
        return "$prefix$($n + 1)"
    }
    throw "Cannot auto-increment '$Current' (no -alpha.N/-beta.N/-rc.N suffix). Pass -Version explicitly."
}

function Resolve-VenvPython {
    $VenvPython = Join-Path $RepoRoot "venv312\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython) { return $VenvPython }
    $PathPython = Get-Command python -ErrorAction Stop
    return $PathPython.Source
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments)] [string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) { throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE" }
}

function Test-TagExists {
    param([string]$Tag)
    $local = & git tag --list $Tag
    if ($local) { return $true }
    & git ls-remote --exit-code --tags origin $Tag 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

Push-Location $RepoRoot
try {
    $CurrentVersion = Read-AppVersion
    if (-not $Version) { $Version = Get-NextVersion -Current $CurrentVersion }
    $Tag = "v$Version"

    Write-Host "[Release] Current version: $CurrentVersion"
    Write-Host "[Release] New version:     $Version"
    Write-Host "[Release] Tag:             $Tag"
    if ($DryRun) { Write-Host "[Release] DRY RUN: no git mutations, no publish." }
    Write-Host ""

    # 1. Working tree clean.
    $dirty = & git status --porcelain
    if ($dirty) {
        throw "Working tree is not clean. Commit or stash changes first:`n$dirty"
    }

    # 2. Branch is main.
    $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
    if ($branch -ne "main") {
        $resp = Read-Host "[Release] Current branch is '$branch', not 'main'. Continue anyway? (y/N)"
        if ($resp -notmatch '^[Yy]') { throw "Aborted: not on main." }
    }

    # 3. Version must change.
    if ($Version -eq $CurrentVersion) {
        throw "New version equals current version ($CurrentVersion). Pass a different -Version."
    }

    # 4. Tag must not exist.
    & git fetch origin --tags --quiet
    if (Test-TagExists -Tag $Tag) {
        throw "Tag $Tag already exists locally or on origin."
    }

    # 5. Bump + commit version.
    if (-not $DryRun) {
        Write-Host "[Release] Writing $Version to version.py and committing..."
        Write-AppVersion -NewVersion $Version
        Invoke-Git add -- (Resolve-Path $VersionFile).Path
        Invoke-Git commit -m "Bump version to $Version"
    } else {
        Write-Host "[Release] DRY RUN: would update version.py from $CurrentVersion to $Version and commit."
    }

    # 6. Tests.
    $PythonExe = Resolve-VenvPython
    if (-not $SkipTests) {
        Write-Host "[Release] Running pytest..."
        & $PythonExe -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE" }
    }

    # 7. Build portable.
    Write-Host "[Release] Building portable package..."
    & (Join-Path $ScriptDir "..\pyinstaller\build_portable.ps1") -PythonExe $PythonExe
    if ($LASTEXITCODE -ne 0) { throw "Portable build failed with exit code $LASTEXITCODE" }

    $PortableDir = Join-Path $RepoRoot "dist\BazaarCoach"
    $PortableExe = Join-Path $PortableDir "BazaarCoach.exe"
    if (-not (Test-Path $PortableExe)) { throw "Expected portable executable not found: $PortableExe" }

    Write-Host "[Release] Running portable smoke test..."
    & $PythonExe (Join-Path $ScriptDir "..\pyinstaller\smoke_test_portable.py") --exe $PortableExe
    if ($LASTEXITCODE -ne 0) { throw "Smoke test failed with exit code $LASTEXITCODE" }

    # 8. Portable zip. Use whatever APP_VERSION the build was actually produced with.
    $BuiltVersion = Read-AppVersion
    $ZipPath = Join-Path $RepoRoot "dist\BazaarCoach-Portable-$BuiltVersion.zip"
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    Write-Host "[Release] Creating portable zip: $ZipPath"
    Compress-Archive -Path (Join-Path $PortableDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal

    # 9. Installer (pass -AppVersion explicitly to guarantee no iss-default leak).
    Write-Host "[Release] Building installer..."
    & (Join-Path $ScriptDir "..\installer\build_installer.ps1") -AppVersion $BuiltVersion
    if ($LASTEXITCODE -ne 0) { throw "Installer build failed with exit code $LASTEXITCODE" }

    $InstallerExe = Join-Path $RepoRoot "dist\installer\BazaarCoachSetup-$BuiltVersion.exe"
    if (-not (Test-Path $InstallerExe)) { throw "Expected installer not found: $InstallerExe" }

    # 10. Release notes.
    $NotesDir = Join-Path $RepoRoot "dist\release"
    New-Item -ItemType Directory -Force -Path $NotesDir | Out-Null
    $NotesPath = Join-Path $NotesDir "release-notes-$Version.md"

    if ($NotesFile) {
        if (-not (Test-Path $NotesFile)) { throw "NotesFile not found: $NotesFile" }
        Copy-Item -Path $NotesFile -Destination $NotesPath -Force
        Write-Host "[Release] Using notes from $NotesFile -> $NotesPath"
    } else {
        $lastTag = & git describe --tags --abbrev=0 2>$null
        $logRange = if ($lastTag) { "$lastTag..HEAD" } else { "HEAD" }
        $log = & git log $logRange --oneline --no-merges
        $header = "# Bazaar Coach $Version`n`n"
        if ($lastTag) {
            $header += "Changes since ``$lastTag``:`n`n"
        } else {
            $header += "Initial release notes:`n`n"
        }
        $body = ($log | ForEach-Object { "- $_" }) -join "`n"
        if (-not $body) { $body = "_(no commits since last tag)_" }
        Set-Content -Path $NotesPath -Value ($header + $body + "`n") -Encoding UTF8
        Write-Host "[Release] Generated notes stub: $NotesPath"
        Write-Host "[Release] Edit this file in the GitHub UI after the draft is created."
    }

    # 11. Tag + push + publish.
    if ($DryRun) {
        Write-Host ""
        Write-Host "[Release] DRY RUN: would run:"
        Write-Host "  git tag -a $Tag -m 'Bazaar Coach $Version'"
        Write-Host "  git push origin main"
        Write-Host "  git push origin $Tag"
        $publishFlag = if ($Publish) { "" } else { " --draft" }
        Write-Host "  gh release create $Tag --title 'Bazaar Coach $Version' --notes-file $NotesPath --prerelease$publishFlag $ZipPath $InstallerExe"
        Write-Host ""
        Write-Host "[Release] DRY RUN complete. Artifacts at:"
        Write-Host "  $ZipPath"
        Write-Host "  $InstallerExe"
        Write-Host "  $NotesPath"
        return
    }

    Write-Host "[Release] Creating tag $Tag..."
    Invoke-Git tag -a $Tag -m "Bazaar Coach $Version"

    Write-Host "[Release] Pushing main..."
    Invoke-Git push origin main

    Write-Host "[Release] Pushing tag $Tag..."
    Invoke-Git push origin $Tag

    Write-Host "[Release] Creating GitHub release..."
    $ghArgs = @(
        "release", "create", $Tag,
        "--title", "Bazaar Coach $Version",
        "--notes-file", $NotesPath,
        "--prerelease"
    )
    if (-not $Publish) { $ghArgs += "--draft" }
    $ghArgs += $ZipPath
    $ghArgs += $InstallerExe

    & gh @ghArgs
    if ($LASTEXITCODE -ne 0) { throw "gh release create failed with exit code $LASTEXITCODE" }

    $releaseUrl = & gh release view $Tag --json url --jq .url
    Write-Host ""
    Write-Host "[Release] Done. Release URL: $releaseUrl"
    if (-not $Publish) {
        Write-Host "[Release] Release is a DRAFT. Edit notes in the GitHub UI, then click Publish."
    }
}
finally {
    Pop-Location
}
