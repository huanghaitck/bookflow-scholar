param(
    [string]$Version = '0.8.0-rc.2'
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
$App = Join-Path $Stage 'Bookflow Scholar.exe'
$Client = Start-Process -FilePath $App -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 8
if ($Client.HasExited) {
    throw "Portable app exited early: $($Client.ExitCode)"
}
$Client.CloseMainWindow() | Out-Null
Start-Sleep -Seconds 3
if (-not $Client.HasExited) {
    Stop-Process -Id $Client.Id -Force
}

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
