param(
    [string]$PythonExecutable = (
        Join-Path ([Environment]::GetFolderPath('UserProfile')) '.conda\envs\bilingual-book\python.exe'
    ),
    [string]$Version = '0.8.0-rc.4',
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
$ProductVersion = '0.8.0.4'
$PortableCoreName = 'bookflow-desktop.exe'
$WebView2Bootstrapper = (Resolve-Path (Join-Path $BuildRoot 'webview2\MicrosoftEdgeWebview2Setup.exe')).Path

if ($Version -ne '0.8.0-rc.4') {
    throw 'Only the approved 0.8.0-rc.4 OCR recovery and progress hotfix may be finalized by this script.'
}

$WebView2Signature = Get-AuthenticodeSignature -LiteralPath $WebView2Bootstrapper
$WebView2Subject = if ($WebView2Signature.SignerCertificate) { $WebView2Signature.SignerCertificate.Subject } else { '' }
if ($WebView2Signature.Status -ne 'Valid' -or $WebView2Subject -notmatch 'Microsoft Corporation') {
    throw "WebView2 bootstrapper signature is not a valid Microsoft signature: $($WebView2Signature.Status) $WebView2Subject"
}

& $NsisCompiler `
    '/INPUTCHARSET' 'UTF8' `
    "/DAPP_EXE=$AppExecutable" `
    "/DSIDECAR_DIR=$SidecarDir" `
    "/DDEFAULT_CONFIG=$(Join-Path $RepoRoot 'config\providers.release.yaml')" `
    "/DINSTALLER_ICON=$(Join-Path $RepoRoot 'ui\src-tauri\icons\icon.ico')" `
    "/DOUTPUT_INSTALLER=$Installer" `
    "/DPUBLISHER=$Publisher" `
    "/DVERSION=$Version" `
    "/DPRODUCT_VERSION=$ProductVersion" `
    "/DWEBVIEW2_BOOTSTRAPPER=$WebView2Bootstrapper" `
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
    -Destination (Join-Path $Stage $PortableCoreName) -Force
Copy-Item -Path (Join-Path $SidecarDir '*') `
    -Destination (Join-Path $Stage 'bookflow-sidecar') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot 'config\providers.release.yaml') `
    -Destination (Join-Path $Stage 'defaults\providers.yaml') -Force
Copy-Item -LiteralPath $WebView2Bootstrapper `
    -Destination (Join-Path $Stage 'MicrosoftEdgeWebview2Setup.exe') -Force
& $NsisCompiler `
    '/INPUTCHARSET' 'UTF8' `
    "/DAPP_EXE_NAME=$PortableCoreName" `
    "/DOUTPUT_LAUNCHER=$(Join-Path $Stage 'Bookflow Scholar.exe')" `
    "/DLAUNCHER_ICON=$(Join-Path $RepoRoot 'ui\src-tauri\icons\icon.ico')" `
    "/DVERSION=$Version" `
    "/DPRODUCT_VERSION=$ProductVersion" `
    (Join-Path $RepoRoot 'packaging\bookflow-portable-launcher.nsi')
if ($LASTEXITCODE -ne 0) {
    throw 'Portable WebView2 launcher build failed.'
}
@'
Bookflow Scholar portable release

Always start "Bookflow Scholar.exe". It checks Microsoft Edge WebView2 Runtime and,
when needed, runs the signed Microsoft bootstrapper included in this folder.
An internet connection is required only when WebView2 must be installed.
Do not start bookflow-desktop.exe directly or move files out of this directory.
User projects and settings remain in %LOCALAPPDATA%\Bookflow Scholar\.
This release is unsigned. Verify the published SHA-256 before running it.
LibreOffice is optional. Core source, target-language, and bilingual PDFs use the
built-in native PDF renderer and do not require LibreOffice. LibreOffice is only
used for optional Office/DOCX compatibility rendering:
https://www.libreoffice.org/download/
'@ | Set-Content -LiteralPath (Join-Path $Stage 'README-PORTABLE.txt') -Encoding UTF8
Compress-Archive -Path (Join-Path $Stage '*') `
    -DestinationPath $Portable -CompressionLevel Optimal -Force
Remove-Item -LiteralPath $Stage -Recurse -Force

Copy-Item -LiteralPath (Join-Path $RepoRoot "docs\INSTALLATION_$Version.md") `
    -Destination (Join-Path $ReleaseDir 'INSTALLATION.md') -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "docs\RELEASE_NOTES_$Version.md") `
    -Destination (Join-Path $ReleaseDir 'RELEASE_NOTES.md') -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "docs\OPEN_ISSUES_$Version.md") `
    -Destination (Join-Path $ReleaseDir 'OPEN_ISSUES.md') -Force

& $PythonExecutable (Join-Path $RepoRoot 'scripts\generate_s11_metadata.py') `
    --release-dir $ReleaseDir `
    --sidecar-dir $SidecarDir `
    --installer $Installer `
    --portable $Portable `
    --webview2-bootstrapper $WebView2Bootstrapper `
    --version $Version `
    --repo-root $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Final release metadata generation failed.'
}

Get-Content -LiteralPath (Join-Path $ReleaseDir 'SHA256SUMS.txt')
