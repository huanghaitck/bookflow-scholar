param(
    [string]$PythonExecutable = (
        Join-Path ([Environment]::GetFolderPath('UserProfile')) '.conda\envs\bilingual-book\python.exe'
    ),
    [string]$Version = '0.8.0-rc.5',
    [string]$NsisCompiler = '',
    [string]$Publisher = 'huanghaitck'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BuildRoot = Join-Path $RepoRoot 'output\s11-build'
$ReleaseDir = Join-Path $RepoRoot "output\releases\bookflow-scholar-$Version"
$SidecarDistRoot = Join-Path $BuildRoot 'pyinstaller-dist'
$SidecarDir = Join-Path $SidecarDistRoot 'bookflow-sidecar'
$SidecarResourceDir = Join-Path $RepoRoot 'ui\src-tauri\resources\bookflow-sidecar'
$DefaultsResourceDir = Join-Path $RepoRoot 'ui\src-tauri\resources\defaults'
$BuildLog = Join-Path $ReleaseDir 'S11_BUILD.log'
$ExpectedInstaller = "Bookflow-Scholar-$Version-setup.exe"
$ExpectedPortable = "Bookflow-Scholar-$Version-portable-win-x64.zip"
$ProductVersion = '0.8.0.5'
$PortableCoreName = 'bookflow-desktop.exe'
$WebView2Bootstrapper = Join-Path $BuildRoot 'webview2\MicrosoftEdgeWebview2Setup.exe'
$WebView2BootstrapperUrl = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
$PythonRoot = Split-Path -Parent $PythonExecutable
$OpenSslDll = Join-Path $PythonRoot 'Library\bin\libssl-3-x64.dll'
$CryptoDll = Join-Path $PythonRoot 'Library\bin\libcrypto-3-x64.dll'
$AppExecutable = Join-Path $RepoRoot 'ui\src-tauri\target\release\bookflow-desktop.exe'

if ($Version -ne '0.8.0-rc.5') {
    throw 'The approved difficult-page round-trip hotfix version is 0.8.0-rc.5.'
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Approved Python interpreter not found: $PythonExecutable"
}
foreach ($RuntimeDll in @($OpenSslDll, $CryptoDll)) {
    if (-not (Test-Path -LiteralPath $RuntimeDll -PathType Leaf)) {
        throw "Required packaged runtime library not found: $RuntimeDll"
    }
}
if (-not $NsisCompiler) {
    $NsisCompiler = Join-Path $BuildRoot 'nsis-3.12\makensis.exe'
}
if (-not (Test-Path -LiteralPath $NsisCompiler -PathType Leaf)) {
    throw "NSIS compiler not found: $NsisCompiler"
}

function Assert-MicrosoftWebView2Bootstrapper {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "WebView2 bootstrapper not found: $Path"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    $subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { '' }
    if ($signature.Status -ne 'Valid' -or $subject -notmatch 'Microsoft Corporation') {
        throw "WebView2 bootstrapper signature is not a valid Microsoft signature: $($signature.Status) $subject"
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $WebView2Bootstrapper) | Out-Null
if (-not (Test-Path -LiteralPath $WebView2Bootstrapper -PathType Leaf)) {
    $download = "$WebView2Bootstrapper.download"
    Invoke-WebRequest -Uri $WebView2BootstrapperUrl -OutFile $download
    Assert-MicrosoftWebView2Bootstrapper -Path $download
    Move-Item -LiteralPath $download -Destination $WebView2Bootstrapper
}
Assert-MicrosoftWebView2Bootstrapper -Path $WebView2Bootstrapper

New-Item -ItemType Directory -Force -Path $BuildRoot, $ReleaseDir, $SidecarResourceDir, $DefaultsResourceDir | Out-Null
Start-Transcript -LiteralPath $BuildLog -Force
try {
    & $PythonExecutable (Join-Path $RepoRoot 'scripts\assert_bilingual_book_environment.py')
    if ($LASTEXITCODE -ne 0) { throw 'Python environment gate failed.' }

    & $PythonExecutable -m PyInstaller `
        --noconfirm `
        --onedir `
        --name bookflow-sidecar `
        --paths (Join-Path $RepoRoot 'src') `
        --add-binary "$OpenSslDll;." `
        --add-binary "$CryptoDll;." `
        --distpath $SidecarDistRoot `
        --workpath (Join-Path $BuildRoot 'pyinstaller-work') `
        --specpath (Join-Path $BuildRoot 'pyinstaller-spec') `
        (Join-Path $RepoRoot 'scripts\bookflow_sidecar_entry.py')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller sidecar build failed.' }

    Copy-Item -Path (Join-Path $SidecarDir '*') -Destination $SidecarResourceDir -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot 'config\providers.release.yaml') `
        -Destination (Join-Path $DefaultsResourceDir 'providers.yaml') -Force

    Push-Location (Join-Path $RepoRoot 'ui')
    $PreviousRustFlags = $env:RUSTFLAGS
    $CargoHomePath = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $env:USERPROFILE '.cargo' }
    $RustupHomePath = if ($env:RUSTUP_HOME) { $env:RUSTUP_HOME } else { Join-Path $env:USERPROFILE '.rustup' }
    $RemapFlags = @(
        "--remap-path-prefix=$RepoRoot=bookflow-source",
        "--remap-path-prefix=$CargoHomePath=rust-cargo",
        "--remap-path-prefix=$RustupHomePath=rust-toolchain"
    ) -join ' '
    $env:RUSTFLAGS = (($PreviousRustFlags, $RemapFlags) -join ' ').Trim()
    try {
        & corepack pnpm tauri build --no-bundle
        if ($LASTEXITCODE -ne 0) { throw 'Tauri release build failed.' }
    }
    finally {
        $env:RUSTFLAGS = $PreviousRustFlags
        Pop-Location
    }

    $Installer = Join-Path $ReleaseDir $ExpectedInstaller
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
    if ($LASTEXITCODE -ne 0) { throw 'NSIS installer build failed.' }

    $PortableRoot = Join-Path $BuildRoot ("portable-package-" + $PID)
    $PortableZip = Join-Path $ReleaseDir $ExpectedPortable
    if (Test-Path -LiteralPath $PortableRoot) {
        $ResolvedPortableRoot = (Resolve-Path -LiteralPath $PortableRoot).Path
        if (-not $ResolvedPortableRoot.StartsWith($BuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Portable staging path escaped the controlled build directory: $ResolvedPortableRoot"
        }
        Remove-Item -LiteralPath $ResolvedPortableRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path `
        $PortableRoot, `
        (Join-Path $PortableRoot 'bookflow-sidecar'), `
        (Join-Path $PortableRoot 'defaults') | Out-Null
    Copy-Item -LiteralPath $AppExecutable `
        -Destination (Join-Path $PortableRoot $PortableCoreName) -Force
    Copy-Item -Path (Join-Path $SidecarDir '*') `
        -Destination (Join-Path $PortableRoot 'bookflow-sidecar') -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot 'config\providers.release.yaml') `
        -Destination (Join-Path $PortableRoot 'defaults\providers.yaml') -Force
    Copy-Item -LiteralPath $WebView2Bootstrapper `
        -Destination (Join-Path $PortableRoot 'MicrosoftEdgeWebview2Setup.exe') -Force
    & $NsisCompiler `
        '/INPUTCHARSET' 'UTF8' `
        "/DAPP_EXE_NAME=$PortableCoreName" `
        "/DOUTPUT_LAUNCHER=$(Join-Path $PortableRoot 'Bookflow Scholar.exe')" `
        "/DLAUNCHER_ICON=$(Join-Path $RepoRoot 'ui\src-tauri\icons\icon.ico')" `
        "/DVERSION=$Version" `
        "/DPRODUCT_VERSION=$ProductVersion" `
        (Join-Path $RepoRoot 'packaging\bookflow-portable-launcher.nsi')
    if ($LASTEXITCODE -ne 0) { throw 'Portable WebView2 launcher build failed.' }
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
'@ | Set-Content -LiteralPath (Join-Path $PortableRoot 'README-PORTABLE.txt') -Encoding UTF8
    Compress-Archive -Path (Join-Path $PortableRoot '*') `
        -DestinationPath $PortableZip -CompressionLevel Optimal -Force
    Remove-Item -LiteralPath $PortableRoot -Recurse -Force
}
finally {
    Stop-Transcript
}

$WebView2Signer = (Get-AuthenticodeSignature -LiteralPath $WebView2Bootstrapper).SignerCertificate.Subject
@"
Bookflow Scholar S11 build summary
Version: $Version
Build time (UTC): $([DateTime]::UtcNow.ToString('o'))
Python environment gate: PASS (Python 3.12 project environment)
PyInstaller persistent sidecar (onedir): PASS
Tauri desktop release build: PASS
NSIS current-user installer: PASS
Portable WebView2 bootstrap launcher: PASS
WebView2 bootstrapper Authenticode: PASS
WebView2 signer: $WebView2Signer
Source and user-profile paths: REDACTED
Credentials included: NO
"@ | Set-Content -LiteralPath $BuildLog -Encoding UTF8

Copy-Item -LiteralPath (Join-Path $RepoRoot "docs\INSTALLATION_$Version.md") `
    -Destination (Join-Path $ReleaseDir 'INSTALLATION.md') -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "docs\RELEASE_NOTES_$Version.md") `
    -Destination (Join-Path $ReleaseDir 'RELEASE_NOTES.md') -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "docs\OPEN_ISSUES_$Version.md") `
    -Destination (Join-Path $ReleaseDir 'OPEN_ISSUES.md') -Force

& $PythonExecutable (Join-Path $RepoRoot 'scripts\generate_s11_metadata.py') `
    --release-dir $ReleaseDir `
    --sidecar-dir $SidecarDir `
    --installer (Join-Path $ReleaseDir $ExpectedInstaller) `
    --portable (Join-Path $ReleaseDir $ExpectedPortable) `
    --webview2-bootstrapper $WebView2Bootstrapper `
    --version $Version `
    --repo-root $RepoRoot
if ($LASTEXITCODE -ne 0) { throw 'Release metadata generation failed.' }

Write-Output (Join-Path $ReleaseDir $ExpectedInstaller)
Write-Output (Join-Path $ReleaseDir $ExpectedPortable)
