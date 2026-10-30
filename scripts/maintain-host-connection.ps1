[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RemoteTarget,
    [Parameter(Mandatory = $true)][string]$KeyPath,
    [string]$LogPath = (Join-Path $env:LOCALAPPDATA 'TopSignal\host-connection.log'),
    [ValidateRange(1,65535)][int]$FrontendPort = 4173,
    [ValidateRange(1,65535)][int]$ApiPort = 8000,
    [ValidateRange(1,65535)][int]$RemoteFrontendPort = 4173,
    [ValidateRange(1,65535)][int]$RemoteApiPort = 8000,
    [ValidateRange(1,60)][int]$ProbeIntervalSeconds = 10,
    [ValidateRange(1,60)][int]$RetryBaseSeconds = 5,
    [ValidateRange(1,300)][int]$RetryMaxSeconds = 60
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($FrontendPort -eq $ApiPort -or $RetryBaseSeconds -gt $RetryMaxSeconds) { throw 'Invalid port or retry configuration.' }
if ($RemoteTarget -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+$') { throw 'Expected user@host.' }
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$resolvedKey = (Resolve-Path -LiteralPath $KeyPath).Path
$LogPath = [IO.Path]::GetFullPath($LogPath)
New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
. (Join-Path $PSScriptRoot 'host-connection-job.ps1')
# Also coordinate with the older installed supervisor during a rolling repair.
$mutexName = if ($FrontendPort -eq 4173 -and $ApiPort -eq 8000) { 'Local\TopSignalHostConnection' } else { "Local\TopSignalHostConnection-$FrontendPort-$ApiPort" }
$mutex = [Threading.Mutex]::new($false, $mutexName)
$ownsMutex = $false
$fileLock = $null
$transport = $null
$job = [IntPtr]::Zero
$lastState = ''
$transportLog = "$LogPath.ssh"

function Rotate-ConnectionLog([string]$Path) {
    if ((Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path).Length -gt 1MB) {
        Move-Item -LiteralPath $Path -Destination "$Path.previous" -Force
    }
}

function Write-ConnectionLog([string]$Message) {
    Rotate-ConnectionLog $LogPath
    Add-Content -LiteralPath $LogPath -Value "$([DateTimeOffset]::Now.ToString('o')) supervisor=$PID $Message" -Encoding UTF8
}

function Write-ConnectionState([string]$State, [int]$FrontendStatus = 0, [int]$ApiStatus = 0) {
    if ($State -ne $script:lastState) {
        Write-ConnectionLog "State=$State frontend_http=$FrontendStatus api_http=$ApiStatus"
        $script:lastState = $State
    }
    $childId = if ($null -ne $transport -and -not $transport.HasExited) { $transport.Id } else { $null }
    $status = @{ observed_at = [DateTimeOffset]::Now.ToString('o'); supervisor_pid = $PID; ssh_pid = $childId; state = $State; frontend_http = $FrontendStatus; api_http = $ApiStatus }
    [IO.File]::WriteAllText("$LogPath.status.json.tmp", ($status | ConvertTo-Json -Compress))
    Move-Item -LiteralPath "$LogPath.status.json.tmp" -Destination "$LogPath.status.json" -Force
}

function Get-EndpointStatus([int]$LocalPort, [int]$HostPort, [string]$Path) {
    $response = $null
    try {
        $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$LocalPort$Path")
        $request.Host = "127.0.0.1:$HostPort"
        $request.Proxy = $null
        $request.AllowAutoRedirect = $false
        $request.Timeout = 3000
        $request.ReadWriteTimeout = 3000
        $request.KeepAlive = $false
        $response = $request.GetResponse()
        return [int]$response.StatusCode
    }
    catch [Net.WebException] {
        if ($null -ne $_.Exception.Response) {
            $response = $_.Exception.Response
            return [int]$response.StatusCode
        }
        return 0
    }
    finally { if ($null -ne $response) { $response.Dispose() } }
}

function Get-ReconnectDelay([int]$Failures) {
    return [int][Math]::Min($RetryMaxSeconds, $RetryBaseSeconds * [Math]::Pow(2, [Math]::Min($Failures, 8)))
}

function Quote-ConnectionArgument([string]$Value) {
    if ($Value.Contains('"') -or $Value.Contains("`r") -or $Value.Contains("`n")) { throw 'Invalid argument.' }
    return '"' + $Value + '"'
}

try {
    try { $ownsMutex = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { exit 0 }
    try { $fileLock = [IO.File]::Open("$LogPath.lock", 'OpenOrCreate', 'ReadWrite', 'None') }
    catch [IO.IOException] { exit 0 }
    Write-ConnectionLog "Started session=$([Diagnostics.Process]::GetCurrentProcess().SessionId)."
    $failures = 0
    while ($true) {
        $listeners = @([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() | Where-Object { $_.Port -in @($ApiPort, $FrontendPort) })
        if ($listeners.Count -gt 0) {
            Write-ConnectionState 'ports_occupied'
            # Never kill unrelated servers or adopt somebody else's listeners.
            Start-Sleep -Seconds 30
            continue
        }
        Rotate-ConnectionLog $transportLog
        Write-ConnectionState 'connecting'
        $arguments = @(
            '-N', '-T', '-i', $resolvedKey,
            '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=3',
            '-L', "127.0.0.1:${FrontendPort}:127.0.0.1:$RemoteFrontendPort",
            '-L', "127.0.0.1:${ApiPort}:127.0.0.1:$RemoteApiPort", $RemoteTarget
        )
        $argumentLine = ($arguments | ForEach-Object { Quote-ConnectionArgument $_ }) -join ' '
        $healthySince = $null
        $unreachableCount = 0
        try {
            $job = [TopSignalProcessJob]::CreateKillOnClose()
            # No inherited console, owned job: terminal control events cannot
            # flow into SSH; abrupt supervisor death cannot orphan its listeners.
            $transport = [TopSignalSshProcess]::Start($ssh, $argumentLine, $transportLog)
            [TopSignalProcessJob]::Assign($job, $transport.Handle)
            Write-ConnectionLog "SSH started pid=$($transport.Id)."
            while (-not $transport.WaitForExit($ProbeIntervalSeconds * 1000)) {
                $frontendStatus = Get-EndpointStatus $FrontendPort $RemoteFrontendPort '/index.html'
                $apiStatus = Get-EndpointStatus $ApiPort $RemoteApiPort '/health'
                if ($frontendStatus -eq 200 -and $apiStatus -eq 200) {
                    Write-ConnectionState 'reachable' $frontendStatus $apiStatus
                    $unreachableCount = 0
                    if ($null -eq $healthySince) { $healthySince = [DateTimeOffset]::UtcNow }
                    if (([DateTimeOffset]::UtcNow - $healthySince).TotalSeconds -ge 300) { $failures = 0 }
                }
                else {
                    $healthySince = $null
                    Write-ConnectionState 'degraded' $frontendStatus $apiStatus
                    # HTTP errors prove forwarding works. Do not reconnect for
                    # just one failed application or a readiness/database error.
                    if ($frontendStatus -eq 0 -and $apiStatus -eq 0) { $unreachableCount++ } else { $unreachableCount = 0 }
                    if ($unreachableCount -ge 3) {
                        Write-ConnectionLog 'Both HTTP destinations unreachable for three probes; recycling only this SSH transport. Host/network/application cause is unknown.'
                        break
                    }
                }
            }
            if ($transport.HasExited) { Write-ConnectionLog "SSH exited code=$($transport.ExitCode)." }
        }
        catch { Write-ConnectionLog "Transport attempt failed type=$($_.Exception.GetType().Name)." }
        finally {
            [TopSignalProcessJob]::Close($job)
            $job = [IntPtr]::Zero
            if ($null -ne $transport) {
                if (-not $transport.HasExited) { $transport.Kill() }
                $transport.WaitForExit(5000) | Out-Null
                $transport.Dispose()
                $transport = $null
            }
        }
        $delay = Get-ReconnectDelay $failures
        $failures++
        Write-ConnectionState 'retrying'
        Write-ConnectionLog "Retry in $delay seconds."
        Start-Sleep -Seconds $delay
    }
}
finally {
    [TopSignalProcessJob]::Close($job)
    if ($null -ne $transport) { $transport.Dispose() }
    if ($null -ne $fileLock) { $fileLock.Dispose() }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
