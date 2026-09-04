[CmdletBinding()]
param(
    [string]$RuntimeRoot = (Join-Path $env:ProgramData "TopSignal\runtime"),
    [string]$TaskName = "TopSignal Personal Device"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$resolvedRuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
# The durable latch is written before touching Task Scheduler. A task failure
# or reboot must never remove the stop request. This never routes broker orders.
[System.IO.File]::WriteAllText((Join-Path $resolvedRuntimeRoot "STOP"), [DateTimeOffset]::UtcNow.ToString("o"))
[System.IO.File]::WriteAllText((Join-Path $resolvedRuntimeRoot "shutdown.request"), "operator stop")
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
}
function Test-SupervisorRunning {
    $lockPath = Join-Path $resolvedRuntimeRoot "supervisor.lock"
    if (-not (Test-Path -LiteralPath $lockPath)) { return $false }
    try {
        $probe = [System.IO.File]::Open($lockPath, 'Open', 'ReadWrite', 'None')
        $probe.Dispose()
        return $false
    }
    catch { return $true }
}
# Task Scheduler can report Disabled while its process is still exiting. The
# supervisor releases this exclusive lock only after its child job is closed.
$deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
while ((Test-SupervisorRunning) -and [DateTimeOffset]::UtcNow -lt $deadline) {
    Start-Sleep -Seconds 1
}
if (Test-SupervisorRunning) {
    if ($null -ne $task) {
        Stop-ScheduledTask -TaskName $TaskName
        Write-Warning "Graceful stop deadline expired; the supervisor job was terminated. Inspect broker exposure."
    }
    else {
        throw "STOP is latched, but the manual supervisor still owns its lock after 60 seconds. Terminate that supervisor and verify provider exposure."
    }
}
Write-Output "Persistent STOP latch set and scheduled startup disabled. Broker orders and positions are unchanged; verify them directly with the provider."
