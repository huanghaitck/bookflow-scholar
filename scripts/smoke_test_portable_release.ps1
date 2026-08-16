param(
    [string]$Version = '0.8.0-rc.3'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$OutputRoot = (Resolve-Path (Join-Path $RepoRoot 'output')).Path
$ReleaseDir = (Resolve-Path (Join-Path $OutputRoot "releases\bookflow-scholar-$Version")).Path
$Archive = Join-Path $ReleaseDir "Bookflow-Scholar-$Version-portable-win-x64.zip"
$Stage = Join-Path $OutputRoot "portable-smoke-$Version"

if (Test-Path -LiteralPath $Stage) {
    $ResolvedStage = (Resolve-Path -LiteralPath $Stage).Path
    $ControlledPrefix = $OutputRoot.TrimEnd('\') + '\'
    if (-not (($ResolvedStage.TrimEnd('\') + '\').StartsWith(
        $ControlledPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    ))) {
        throw "Portable smoke path escaped the output directory: $ResolvedStage"
    }
    Remove-Item -LiteralPath $ResolvedStage -Recurse -Force
}

Expand-Archive -LiteralPath $Archive -DestinationPath $Stage
$Launcher = Join-Path $Stage 'Bookflow Scholar.exe'
$App = Join-Path $Stage 'bookflow-desktop.exe'
$Bootstrapper = Join-Path $Stage 'MicrosoftEdgeWebview2Setup.exe'
foreach ($Required in @($Launcher, $App, $Bootstrapper)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Portable package is missing required file: $Required"
    }
}
$Signature = Get-AuthenticodeSignature -LiteralPath $Bootstrapper
$Subject = if ($Signature.SignerCertificate) { $Signature.SignerCertificate.Subject } else { '' }
if ($Signature.Status -ne 'Valid' -or $Subject -notmatch 'Microsoft Corporation') {
    throw "Portable WebView2 bootstrapper signature is invalid: $($Signature.Status) $Subject"
}

$LauncherProcess = Start-Process -FilePath $Launcher -PassThru -WindowStyle Hidden
$LauncherProcess.WaitForExit(30000) | Out-Null
Start-Sleep -Seconds 8
$Client = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.Equals($App, [System.StringComparison]::OrdinalIgnoreCase)
} | Select-Object -First 1
if (-not $Client) {
    throw 'Portable launcher did not start the internal Bookflow desktop executable.'
}
Stop-Process -Id $Client.ProcessId -Force -ErrorAction SilentlyContinue

$Remaining = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith(
        $Stage,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}
foreach ($Process in $Remaining) {
    Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $Stage -Recurse -Force
Write-Output 'PORTABLE_EXTRACT_AND_LAUNCH_OK'
