param(
    [string]$PythonExecutable = (
        Join-Path ([Environment]::GetFolderPath('UserProfile')) '.conda\envs\bilingual-book\python.exe'
    ),
    [string]$Version = '0.8.0-rc.2',
    [string]$Publisher = 'huanghaitck'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BuildRoot = (Resolve-Path (Join-Path $RepoRoot 'output\s11-build')).Path
$ReleaseDir = (Resolve-Path (Join-Path $RepoRoot "output\releases\bookflow-scholar-$Version")).Path
$AppExecutable = (Resolve-Path (Join-Path $RepoRoot 'ui\src-tauri\target\release\bookflow-desktop.exe')).Path
$SidecarDir = (Resolve-Path (Join-Path $BuildRoot 'pyinstaller-dist\bookflow-sidecar')).Path
$NsisCompiler = (Resolve-Path (Join-Path $BuildRoot 'nsis-3.12\makensis.exe')).Path
$Installer = Join-Path $ReleaseDir "Bookflow-Scholar-$Version-setup.exe"
$Portable = Join-Path $ReleaseDir "Bookflow-Scholar-$Version-portable-win-x64.zip"
$Stage = Join-Path $BuildRoot "portable-final-$Version"

if ($Version -ne '0.8.0-rc.2') {
    throw 'Only the approved 0.8.0-rc.2 candidate may be finalized by this script.'
}

& $NsisCompiler `
    '/INPUTCHARSET' 'UTF8' `
    "/DAPP_EXE=$AppExecutable" `
    "/DSIDECAR_DIR=$SidecarDir" `
    "/DDEFAULT_CONFIG=$(Join-Path $RepoRoot 'config\providers.release.yaml')" `
    "/DINSTALLER_ICON=$(Join-Path $RepoRoot 'ui\src-tauri\icons\icon.ico')" `
    "/DOUTPUT_INSTALLER=$Installer" `
    "/DPUBLISHER=$Publisher" `
    (Join-Path $RepoRoot 'packaging\bookflow-scholar.nsi')
if ($LASTEXITCODE -ne 0) {
    throw 'Final NSIS packaging failed.'
}

if (Test-Path -LiteralPath $Stage) {
    $ResolvedStage = (Resolve-Path -LiteralPath $Stage).Path
    $ControlledPrefix = $BuildRoot.TrimEnd('\') + '\'
    if (-not (($ResolvedStage.TrimEnd('\') + '\').StartsWith(
        $ControlledPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    ))) {
        throw "Portable staging path escaped the controlled build root: $ResolvedStage"
    }
    Remove-Item -LiteralPath $ResolvedStage -Recurse -Force
}

New-Item -ItemType Directory -Force -Path `
    $Stage, `
    (Join-Path $Stage 'bookflow-sidecar'), `
    (Join-Path $Stage 'defaults') | Out-Null
Copy-Item -LiteralPath $AppExecutable `
    -Destination (Join-Path $Stage 'Bookflow Scholar.exe') -Force
Copy-Item -Path (Join-Path $SidecarDir '*') `
    -Destination (Join-Path $Stage 'bookflow-sidecar') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot 'config\providers.release.yaml') `
    -Destination (Join-Path $Stage 'defaults\providers.yaml') -Force
@'
Bookflow Scholar portable release

Run "Bookflow Scholar.exe" from this extracted directory.
Do not move the executable away from the bookflow-sidecar and defaults folders.
User projects and settings remain in %LOCALAPPDATA%\Bookflow Scholar\.
This release is unsigned. Verify the published SHA-256 before running it.
LibreOffice is optional but recommended for validated Office-document rendering:
https://www.libreoffice.org/download/
'@ | Set-Content -LiteralPath (Join-Path $Stage 'README-PORTABLE.txt') -Encoding UTF8
Compress-Archive -Path (Join-Path $Stage '*') `
    -DestinationPath $Portable -CompressionLevel Optimal -Force
Remove-Item -LiteralPath $Stage -Recurse -Force

& $PythonExecutable (Join-Path $RepoRoot 'scripts\generate_s11_metadata.py') `
    --release-dir $ReleaseDir `
    --sidecar-dir $SidecarDir `
    --installer $Installer `
    --portable $Portable `
    --version $Version `
    --repo-root $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Final release metadata generation failed.'
}

Get-Content -LiteralPath (Join-Path $ReleaseDir 'SHA256SUMS.txt')
