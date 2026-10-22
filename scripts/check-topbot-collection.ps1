param(
    [string]$Root = 'C:\ProgramData\TopSignal',
    [Parameter(Mandatory=$true)][int]$BotConfigId,
    [Parameter(Mandatory=$true)][DateTimeOffset]$DeployedAt,
    [string]$TaskName = 'TopSignal Personal Device',
    [ValidateSet('preflight','market-check')][string]$Phase = 'market-check'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Resolve the deployed code from the existing hosting task; never start/stop it.
$task = Get-ScheduledTask -TaskName $TaskName
if (@($task.Actions).Count -ne 1) { throw 'Expected exactly one hosting action' }
$match = [regex]::Match($task.Actions[0].Arguments, '(?i)(?:^|\s)-RepoRoot\s+"([^"]+)"(?:\s|$)')
if (-not $match.Success) { throw 'Cannot determine active release from hosting task' }
$releases = (Resolve-Path -LiteralPath (Join-Path $Root 'releases')).Path.TrimEnd('\') + '\'
$release = (Resolve-Path -LiteralPath $match.Groups[1].Value).Path.TrimEnd('\')
if (-not $release.StartsWith($releases, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Hosting release is outside the expected releases directory'
}
$runtime = Join-Path $Root 'runtime'
$reports = Join-Path $runtime 'checks'
$null = New-Item -ItemType Directory -Path $reports -Force
$eastern = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
$day = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::Now, $eastern).ToString('yyyy-MM-dd')
$prefix = Join-Path $reports "$day-$Phase"
try {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction Stop)
    if ($listeners.Count -ne 1) { throw 'Expected one backend listener' }
    $listener = $listeners[0]
    $backendProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    $snapshot = [ordered]@{
        observed_at = [DateTimeOffset]::Now.ToString('o')
        state = [string]$task.State
        user = $task.Principal.UserId
        release = $release
        release_matches = [bool]($task.Actions.Arguments -like "*$release*")
        env_modified_at = (Get-Item -LiteralPath "$release\backend\.env").LastWriteTimeUtc.ToString('o')
        backend_process = @{
            pid = $listener.OwningProcess
            started_at = $backendProcess.CreationDate.ToUniversalTime().ToString('o')
            expected_command = [bool]($backendProcess.CommandLine -like "*$release*")
        }
    }
    $snapshot | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$prefix-runtime.json" -Encoding UTF8
} catch {
    @{snapshot_error_type=$_.Exception.GetType().Name;state='Unknown'} |
        ConvertTo-Json | Set-Content -LiteralPath "$prefix-runtime.json" -Encoding UTF8
}
$env:PYTHONDONTWRITEBYTECODE = '1'
& "$release\backend\.venv\Scripts\python.exe" -B "$PSScriptRoot\check-topbot.py" `
    --release $release --runtime-root $runtime --runtime-snapshot "$prefix-runtime.json" `
    --config-id $BotConfigId --deployed-at $DeployedAt.ToUniversalTime().ToString('o') --phase $Phase `
    1> "$prefix-runner.log" 2> "$prefix-errors.log"
$result = $LASTEXITCODE
Get-Content -LiteralPath "$prefix-runner.log"
if ($result -notin @(0,2)) {
    @{status='ERROR';completed_at=[DateTimeOffset]::Now.ToString('o');reason='Checker did not complete; inspect private error log';exit_code=$result} |
        ConvertTo-Json | Set-Content -LiteralPath "$prefix.json" -Encoding UTF8
}
exit $result
