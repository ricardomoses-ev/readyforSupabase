param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $projectRoot "release\readyforSupabase-win64"
$distExe = Join-Path $projectRoot "dist\readyforSupabase.exe"
$specPath = Join-Path $projectRoot "packaging\readyforSupabase.spec"

Set-Location $projectRoot

if (-not $SkipInstall) {
    python -m pip install -r "requirements-build.txt"
}

python -m PyInstaller --noconfirm --clean "$specPath"

if (-not (Test-Path $distExe)) {
    throw "Build failed: EXE not found at $distExe"
}

New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
Copy-Item $distExe (Join-Path $releaseDir "readyforSupabase.exe") -Force
Copy-Item (Join-Path $projectRoot "README.md") (Join-Path $releaseDir "README.md") -Force

Write-Host "Build complete."
Write-Host "Release folder: $releaseDir"
