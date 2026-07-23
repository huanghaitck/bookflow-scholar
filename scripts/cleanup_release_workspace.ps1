param(
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$OutputRoot = (Resolve-Path (Join-Path $RepoRoot 'output')).Path
$ReportPath = Join-Path $RepoRoot 'reports\RELEASE_CLEANUP_MANIFEST.md'

function Assert-ControlledPath([string]$Path, [string]$AllowedRoot) {
    $Resolved = (Resolve-Path -LiteralPath $Path).Path
    $Prefix = $AllowedRoot.TrimEnd('\') + '\'
    if (-not (($Resolved.TrimEnd('\') + '\').StartsWith(
        $Prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    ))) {
        throw "Cleanup target escaped its controlled root: $Resolved"
    }
    return $Resolved
}

$DirectoryTargets = @(
    'archive',
    'BACKEND_CONTRACT_BUNDLE',
    'BACKEND_CONTRACT_BUNDLE_V1_0_ARCHIVE',
    'build',
    'demo test',
    'dist',
    'handoff',
    'references',
    'release_candidate_bundle',
    'review_screenshots',
    'review_videos',
    'tmp',
    '__pycache__',
    'ui\node_modules',
    'ui\dist',
    'ui\storybook-static',
    'ui\storybook-static-s10r3',
    'ui\test-results',
    'ui\src-tauri\target',
    'ui\src-tauri\resources\bookflow-sidecar'
) | ForEach-Object { Join-Path $RepoRoot $_ } | Where-Object {
    Test-Path -LiteralPath $_ -PathType Container
}

$OutputNames = @(
    'archive',
    'audit',
    'candidate',
    'desktop_backend',
    'diagnostic',
    'final',
    'gate2_rebuild_diagnostic_20260723',
    'h4_launcher_smoke',
    'h4_new_chat_desktop_audit_20260722',
    'h4_review_runtime',
    'manual_user_retest_latest_20260723',
    'pdf',
    'rendered',
    's10_r3_runs',
    's11-build',
    's9_r3_s10_r4_reacceptance',
    'test_runs'
)
$OutputTargets = Get-ChildItem -LiteralPath $OutputRoot -Directory | Where-Object {
    $_.Name -in $OutputNames -or
    $_.Name.StartsWith('pytest', [System.StringComparison]::OrdinalIgnoreCase) -or
    $_.Name.StartsWith('s9r2_', [System.StringComparison]::OrdinalIgnoreCase)
} | Select-Object -ExpandProperty FullName
$DirectoryTargets += $OutputTargets

$DirectoryTargets += Get-ChildItem -LiteralPath $RepoRoot -Directory -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq '__pycache__' -or $_.Name.EndsWith('.egg-info') } |
    Select-Object -ExpandProperty FullName
$DirectoryTargets = $DirectoryTargets | Sort-Object -Unique
$AllDirectoryTargets = @($DirectoryTargets)
$DirectoryTargets = $AllDirectoryTargets | Where-Object {
    $Candidate = $_.TrimEnd('\') + '\'
    -not ($AllDirectoryTargets | Where-Object {
        $_ -ne $Candidate.TrimEnd('\') -and
        $Candidate.StartsWith(
            $_.TrimEnd('\') + '\',
            [System.StringComparison]::OrdinalIgnoreCase
        )
    })
}

$RootFilePatterns = @(
    'BH_APPROVAL.md',
    'H4_*',
    'S9_R2_*',
    'S10_R3_*',
    'SERIAL_PIPELINE_STATUS.json',
    '_fix_dup.py',
    'phase2_6_independent_audit.zip',
    'phase2_6_repair_audit.zip',
    'phase6_final_errata_audit.zip'
)
$FileTargets = foreach ($Pattern in $RootFilePatterns) {
    Get-ChildItem -LiteralPath $RepoRoot -File -Filter $Pattern -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
}
$FileTargets += @(
    (Join-Path $RepoRoot '.env'),
    (Join-Path $RepoRoot 'scripts\gh_auth_with_system_proxy.ps1')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
$FileTargets = $FileTargets | Sort-Object -Unique

$Rows = @()
foreach ($Path in $DirectoryTargets) {
    $Resolved = Assert-ControlledPath -Path $Path -AllowedRoot $RepoRoot
    $Files = Get-ChildItem -LiteralPath $Resolved -File -Recurse -Force -ErrorAction SilentlyContinue
    $Rows += [pscustomobject]@{
        Type = 'directory'
        Path = $Resolved.Substring($RepoRoot.Length + 1)
        Files = $Files.Count
        Bytes = [long](($Files | Measure-Object Length -Sum).Sum)
    }
}
foreach ($Path in $FileTargets) {
    $Resolved = Assert-ControlledPath -Path $Path -AllowedRoot $RepoRoot
    $Item = Get-Item -LiteralPath $Resolved
    $Rows += [pscustomobject]@{
        Type = 'file'
        Path = $Resolved.Substring($RepoRoot.Length + 1)
        Files = 1
        Bytes = [long]$Item.Length
    }
}
$Rows = $Rows | Sort-Object Path
$TotalBytes = [long](($Rows | Measure-Object Bytes -Sum).Sum)
$TotalFiles = [long](($Rows | Measure-Object Files -Sum).Sum)

$Lines = @(
    '# Release workspace cleanup manifest',
    '',
    "- Mode: $(if ($Execute) { 'executed' } else { 'plan only' })",
    "- Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')",
    "- Targets: $($Rows.Count)",
    "- Files: $TotalFiles",
    "- Bytes: $TotalBytes",
    '',
    '## Preserved',
    '',
    '- Git repository, production source, build scripts, configs, prompts, schemas, contracts, and language profiles',
    '- tests, fixtures, and self-created six-language/page-count audit fixtures',
    '- `data/`, `input/`, `output/fullbook/`, `output/releases/`, and `output/s12-acceptance/`',
    '- reports and current project state',
    '- `%LOCALAPPDATA%\Bookflow Scholar\` user projects and outputs',
    '',
    '## Non-blocking residual',
    '',
    '- `.pytest_cache/`: 0-byte inaccessible directory; the current user and `takeown` are denied by its existing ACL.',
    '',
    '## Cleanup targets',
    '',
    '| Type | Relative path | Files | Bytes |',
    '|---|---|---:|---:|'
)
foreach ($Row in $Rows) {
    $Lines += "| $($Row.Type) | ``$($Row.Path.Replace('|', '/'))`` | $($Row.Files) | $($Row.Bytes) |"
}
$Lines -join "`n" | Set-Content -LiteralPath $ReportPath -Encoding UTF8

if (-not $Execute) {
    Write-Output "PLAN_TARGETS=$($Rows.Count)"
    Write-Output "PLAN_FILES=$TotalFiles"
    Write-Output "PLAN_BYTES=$TotalBytes"
    Write-Output $ReportPath
    exit 0
}

$Failures = @()
foreach ($Path in $FileTargets) {
    try {
        Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
    }
    catch {
        $Failures += [pscustomobject]@{
            Path = $Path.Substring($RepoRoot.Length + 1)
            Error = $_.Exception.Message
        }
    }
}
foreach ($Path in ($DirectoryTargets | Sort-Object { $_.Length } -Descending)) {
    if (Test-Path -LiteralPath $Path) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        }
        catch {
            $Failures += [pscustomobject]@{
                Path = $Path.Substring($RepoRoot.Length + 1)
                Error = $_.Exception.Message
            }
        }
    }
}
if ($Failures) {
    @(
        '',
        '## Deletion residuals',
        '',
        'The following targets were attempted but could not be completely removed because of existing filesystem permissions or locks:',
        ''
    ) | Add-Content -LiteralPath $ReportPath -Encoding UTF8
    foreach ($Failure in $Failures) {
        "- ``$($Failure.Path)``: $($Failure.Error.Replace("`r", ' ').Replace("`n", ' '))" |
            Add-Content -LiteralPath $ReportPath -Encoding UTF8
    }
}
Write-Output "DELETED_TARGETS=$($Rows.Count)"
Write-Output "DELETED_FILES=$TotalFiles"
Write-Output "DELETED_BYTES=$TotalBytes"
Write-Output "RESIDUAL_TARGETS=$($Failures.Count)"
