param(
    [string]$PythonExecutable = (
        Join-Path ([Environment]::GetFolderPath('UserProfile')) '.conda\envs\bilingual-book\python.exe'
    ),
    [string]$Version = '0.8.0-rc.2',
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
$PythonRoot = Split-Path -Parent $PythonExecutable
$OpenSslDll = Join-Path $PythonRoot 'Library\bin\libssl-3-x64.dll'
$CryptoDll = Join-Path $PythonRoot 'Library\bin\libcrypto-3-x64.dll'
$AppExecutable = Join-Path $RepoRoot 'ui\src-tauri\target\release\bookflow-desktop.exe'

if ($Version -ne '0.8.0-rc.2') {
    throw 'The approved S11 build version is 0.8.0-rc.2.'
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
        -Destination (Join-Path $PortableRoot 'Bookflow Scholar.exe') -Force
    Copy-Item -Path (Join-Path $SidecarDir '*') `
        -Destination (Join-Path $PortableRoot 'bookflow-sidecar') -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot 'config\providers.release.yaml') `
        -Destination (Join-Path $PortableRoot 'defaults\providers.yaml') -Force
    @'
Bookflow Scholar portable release

Run "Bookflow Scholar.exe" from this extracted directory.
Do not move the executable away from the bookflow-sidecar and defaults folders.
User projects and settings remain in %LOCALAPPDATA%\Bookflow Scholar\.
This release is unsigned. Verify the published SHA-256 before running it.
LibreOffice is optional but recommended for validated Office-document rendering:
https://www.libreoffice.org/download/
'@ | Set-Content -LiteralPath (Join-Path $PortableRoot 'README-PORTABLE.txt') -Encoding UTF8
    Compress-Archive -Path (Join-Path $PortableRoot '*') `
        -DestinationPath $PortableZip -CompressionLevel Optimal -Force
    Remove-Item -LiteralPath $PortableRoot -Recurse -Force
}
finally {
    Stop-Transcript
}

& $PythonExecutable (Join-Path $RepoRoot 'scripts\generate_s11_metadata.py') `
    --release-dir $ReleaseDir `
    --sidecar-dir $SidecarDir `
    --installer (Join-Path $ReleaseDir $ExpectedInstaller) `
    --portable (Join-Path $ReleaseDir $ExpectedPortable) `
    --version $Version `
    --repo-root $RepoRoot
if ($LASTEXITCODE -ne 0) { throw 'Release metadata generation failed.' }

Write-Output (Join-Path $ReleaseDir $ExpectedInstaller)
Write-Output (Join-Path $ReleaseDir $ExpectedPortable)
