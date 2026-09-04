[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonPath = "",
    [string]$RuntimeRoot = (Join-Path $env:ProgramData "TopSignal\runtime"),
    [int]$Port = 8000,
    [int]$FrontendPort = 4173,
    [int]$HealthIntervalSeconds = 10,
    [int]$HealthFailureThreshold = 3,
    [int]$WorkerHealthStartupGraceSeconds = 120,
    [int]$WorkerHealthRestartAfterSeconds = 300
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not ("TopSignalProcessJob" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class TopSignalProcessJob
{
    private const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    private const Int32 JobObjectExtendedLimitInformation = 9;

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public UIntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr securityAttributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr job,
        Int32 informationClass,
        ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION information,
        UInt32 informationLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static IntPtr CreateKillOnClose()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject failed");

        JOBOBJECT_EXTENDED_LIMIT_INFORMATION information =
            new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        UInt32 length = (UInt32)Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, ref information, length))
        {
            Int32 error = Marshal.GetLastWin32Error();
            CloseHandle(job);
            throw new Win32Exception(error, "SetInformationJobObject failed");
        }
        return job;
    }

    public static void Assign(IntPtr job, IntPtr process)
    {
        if (!AssignProcessToJobObject(job, process))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "AssignProcessToJobObject failed");
    }

    public static void Close(IntPtr job)
    {
        if (job != IntPtr.Zero)
            CloseHandle(job);
    }
}
'@
}

if ($Port -lt 1 -or $Port -gt 65535 -or $FrontendPort -lt 1 -or $FrontendPort -gt 65535) {
    throw "Backend and frontend ports must be between 1 and 65535."
}
if ($Port -eq $FrontendPort) {
    throw "Backend and frontend ports must be different."
}
if ($HealthIntervalSeconds -lt 5 -or $HealthFailureThreshold -lt 1) {
    throw "Health interval must be at least 5 seconds and failure threshold must be positive."
}
if ($WorkerHealthStartupGraceSeconds -lt 30) {
    throw "Worker-health startup grace must be at least 30 seconds."
}
if ($WorkerHealthRestartAfterSeconds -lt 120) {
    throw "Worker-health restart delay must be at least 120 seconds to avoid startup restart loops."
}

$resolvedRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
if (Test-Path -LiteralPath (Join-Path $resolvedRoot ".git")) {
    throw "Refusing to run production from a mutable Git working tree. Use an ACL-hardened release copy."
}
$currentWindowsIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
if ($currentWindowsIdentity.IsSystem) {
    throw "Refusing to run the trading supervisor as SYSTEM. Use the dedicated local service identity."
}
$backendDir = Join-Path $resolvedRoot "backend"
$frontendDist = Join-Path $resolvedRoot "frontend\dist"
$frontendIndex = Join-Path $frontendDist "index.html"
$frontendServer = Join-Path $resolvedRoot "scripts\serve-production-frontend.py"
$envPath = Join-Path $backendDir ".env"
$migrationTool = Join-Path $backendDir "tools\migrate_db.py"
$loggingConfig = Join-Path $backendDir "logging.production.json"

foreach ($requiredFile in @($envPath, $migrationTool, $loggingConfig, $frontendServer, $frontendIndex)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required production file was not found: $requiredFile"
    }
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $backendDir ".venv\Scripts\python.exe"
}
$resolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path

if (-not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
}
$resolvedRuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$logDir = Join-Path $resolvedRuntimeRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$supervisorLog = Join-Path $logDir "supervisor.log"
$runtimeLoggingConfig = Join-Path $resolvedRuntimeRoot "logging.runtime.json"
$supervisorLockPath = Join-Path $resolvedRuntimeRoot "supervisor.lock"

function Rotate-SupervisorLog {
    if (-not (Test-Path -LiteralPath $supervisorLog -PathType Leaf)) {
        return
    }
    $file = Get-Item -LiteralPath $supervisorLog
    if ($file.Length -lt 10485760) {
        return
    }
    for ($index = 4; $index -ge 1; $index--) {
        $source = "$supervisorLog.$index"
        $destination = "$supervisorLog.$($index + 1)"
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Move-Item -LiteralPath $source -Destination $destination -Force
        }
    }
    Move-Item -LiteralPath $supervisorLog -Destination "$supervisorLog.1" -Force
}

function Write-SupervisorLog([string]$Message) {
    Rotate-SupervisorLog
    $timestamp = [DateTimeOffset]::UtcNow.ToString("o")
    Add-Content -LiteralPath $supervisorLog -Value "$timestamp $Message" -Encoding UTF8
}

function Test-Endpoint([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

function Test-LoopbackPortInUse([int]$ProbePort) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.ConnectAsync("127.0.0.1", $ProbePort)
        if (-not $connect.Wait(500)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Quote-ProcessArgument([string]$Value) {
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Stop-ProcessTree(
    [System.Diagnostics.Process]$Process,
    [string]$Description
) {
    if ($null -eq $Process) {
        return
    }
    try {
        $Process.Refresh()
        if ($Process.HasExited) {
            return
        }
    }
    catch {
        return
    }

    $taskKill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    try {
        $killer = Start-Process `
            -FilePath $taskKill `
            -ArgumentList @("/PID", $Process.Id.ToString(), "/T", "/F") `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        if ($killer.ExitCode -ne 0) {
            Write-SupervisorLog "taskkill returned $($killer.ExitCode) while stopping $Description PID $($Process.Id)."
        }
    }
    catch {
        Write-SupervisorLog "taskkill failed while stopping $Description PID $($Process.Id): $($_.Exception.Message)"
    }

    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
        $Process.WaitForExit(10000) | Out-Null
    }
    catch {
        Write-SupervisorLog "Could not confirm that $Description PID $($Process.Id) exited: $($_.Exception.Message)"
    }
}

function Write-RuntimeLoggingConfig {
    $config = Get-Content -LiteralPath $loggingConfig -Raw | ConvertFrom-Json
    $config.handlers.rotating_file.filename = Join-Path $logDir "topsignal.log"
    $config.handlers.rotating_access_file.filename = Join-Path $logDir "access.log"
    $temporaryConfig = "$runtimeLoggingConfig.tmp"
    $json = $config | ConvertTo-Json -Depth 20
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporaryConfig, $json, $utf8WithoutBom)
    Move-Item -LiteralPath $temporaryConfig -Destination $runtimeLoggingConfig -Force
}

function Add-ProcessToSupervisorJob(
    [IntPtr]$JobHandle,
    [System.Diagnostics.Process]$Process,
    [string]$Description
) {
    try {
        [TopSignalProcessJob]::Assign($JobHandle, $Process.Handle)
    }
    catch {
        Stop-ProcessTree -Process $Process -Description $Description
        throw "Could not contain $Description PID $($Process.Id) in the kill-on-close job: $($_.Exception.Message)"
    }
}

$supervisorLock = $null
$killOnCloseJob = [IntPtr]::Zero
$locationPushed = $false
try {
    try {
        $supervisorLock = [System.IO.File]::Open(
            $supervisorLockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch {
        throw "Another TopSignal supervisor owns '$supervisorLockPath'. Stop it before starting a second instance."
    }
    $lockBytes = [System.Text.Encoding]::UTF8.GetBytes("$PID`r`n")
    $supervisorLock.SetLength(0)
    $supervisorLock.Write($lockBytes, 0, $lockBytes.Length)
    $supervisorLock.Flush()
    $killOnCloseJob = [TopSignalProcessJob]::CreateKillOnClose()

    # The scheduled identity has read/execute only on the release, including
    # the virtual environment. Prevent imports from attempting __pycache__
    # writes into that immutable tree.
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:TOPSIGNAL_ENV = "production"
    Write-RuntimeLoggingConfig
    Push-Location $resolvedRoot
    $locationPushed = $true

    # Validate the effective dotenv/environment configuration before making
    # even a schema change.  The application repeats this check in its lifespan,
    # but doing it here turns an unsafe AUTH_REQUIRED=false deployment into one
    # clear supervisor failure instead of a child-process restart loop.
    Write-SupervisorLog "Validating production security configuration."
    $securityCheckArguments = @(
        "-c",
        "from app.main import _validate_runtime_security_configuration; _validate_runtime_security_configuration()"
    )
    $securityCheckArgumentLine = ($securityCheckArguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " "
    $securityCheckProcess = Start-Process `
        -FilePath $resolvedPython `
        -ArgumentList $securityCheckArgumentLine `
        -WorkingDirectory $backendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "security-check.stdout.log") `
        -RedirectStandardError (Join-Path $logDir "security-check.stderr.log") `
        -PassThru
    Add-ProcessToSupervisorJob -JobHandle $killOnCloseJob -Process $securityCheckProcess -Description "production security check"
    $securityCheckProcess.WaitForExit()
    if ($securityCheckProcess.ExitCode -ne 0) {
        throw "Production security configuration validation failed. See the security-check logs."
    }

    Write-SupervisorLog "Applying checked migrations before backend startup."
    $migrationProcess = Start-Process `
        -FilePath $resolvedPython `
        -ArgumentList (Quote-ProcessArgument $migrationTool) `
        -WorkingDirectory $resolvedRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "migration.stdout.log") `
        -RedirectStandardError (Join-Path $logDir "migration.stderr.log") `
        -PassThru
    Add-ProcessToSupervisorJob -JobHandle $killOnCloseJob -Process $migrationProcess -Description "migration runner"
    $migrationProcess.WaitForExit()
    if ($migrationProcess.ExitCode -ne 0) {
        throw "Database migration failed with exit code $($migrationProcess.ExitCode)."
    }
    [TopSignalProcessJob]::Close($killOnCloseJob)
    $killOnCloseJob = [IntPtr]::Zero

    $env:TOPSIGNAL_DB_SCHEMA_INIT = "skip"
    $healthUrl = "http://127.0.0.1:$Port/health"
    $readyUrl = "http://127.0.0.1:$Port/ready"
    $workerHealthUrl = "http://127.0.0.1:$Port/health/worker?require_enabled=true"
    $frontendUrl = "http://127.0.0.1:$FrontendPort/"
    $restartAttempt = 0
    $workerHealthRestartUsed = $false

    while ($true) {
        $occupiedPorts = @(@($Port, $FrontendPort) | Where-Object { Test-LoopbackPortInUse $_ })
        if ($occupiedPorts.Count -gt 0) {
            Write-SupervisorLog "Loopback port(s) $($occupiedPorts -join ', ') are already accepting connections; waiting 60 seconds instead of entering an occupied-port restart loop."
            Start-Sleep -Seconds 60
            continue
        }
        # A fresh job per cycle guarantees that closing it removes descendants
        # even when a direct child exited before taskkill could traverse it.
        $killOnCloseJob = [TopSignalProcessJob]::CreateKillOnClose()
        $backendArguments = @(
            "-m", "uvicorn", "app.main:app",
            "--app-dir", $backendDir,
            "--host", "127.0.0.1",
            "--port", $Port.ToString(),
            "--workers", "1",
            "--log-config", $runtimeLoggingConfig
        )
        $backendArgumentLine = ($backendArguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " "
        $frontendArguments = @(
            $frontendServer,
            "--directory", $frontendDist,
            "--host", "127.0.0.1",
            "--port", $FrontendPort.ToString(),
            "--api-port", $Port.ToString(),
            "--log-file", (Join-Path $logDir "frontend-access.log")
        )
        $frontendArgumentLine = ($frontendArguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " "

        Write-SupervisorLog "Starting TopSignal backend on 127.0.0.1:$Port and control UI on 127.0.0.1:$FrontendPort."
        $startedAt = [DateTimeOffset]::UtcNow
        $backendProcess = $null
        $frontendProcess = $null
        $restartReason = "child process exit"

        try {
            $backendProcess = Start-Process `
                -FilePath $resolvedPython `
                -ArgumentList $backendArgumentLine `
                -WorkingDirectory $resolvedRoot `
                -WindowStyle Hidden `
                -PassThru
            Add-ProcessToSupervisorJob -JobHandle $killOnCloseJob -Process $backendProcess -Description "backend"
            $frontendProcess = Start-Process `
                -FilePath $resolvedPython `
                -ArgumentList $frontendArgumentLine `
                -WorkingDirectory $resolvedRoot `
                -WindowStyle Hidden `
                -PassThru
            Add-ProcessToSupervisorJob -JobHandle $killOnCloseJob -Process $frontendProcess -Description "frontend server"

            $healthFailures = 0
            $frontendFailures = 0
            $workerHealthFailureStartedAt = $null
            $lastWorkerHealthIndicator = [DateTimeOffset]::MinValue
            $lastReadinessIndicator = [DateTimeOffset]::MinValue
            $workerHealthGraceEndsAt = $startedAt.AddSeconds($WorkerHealthStartupGraceSeconds)

            while ($true) {
                if ($backendProcess.WaitForExit($HealthIntervalSeconds * 1000)) {
                    $restartReason = "backend exited with code $($backendProcess.ExitCode)"
                    break
                }
                $frontendProcess.Refresh()
                if ($frontendProcess.HasExited) {
                    $restartReason = "frontend server exited with code $($frontendProcess.ExitCode)"
                    break
                }

                $backendIsLive = Test-Endpoint $healthUrl
                if ($backendIsLive) {
                    $healthFailures = 0
                }
                else {
                    $healthFailures++
                    Write-SupervisorLog "Liveness probe failed ($healthFailures/$HealthFailureThreshold)."
                    if ($healthFailures -ge $HealthFailureThreshold) {
                        $restartReason = "persistent backend liveness failure"
                        break
                    }
                }

                if (Test-Endpoint $frontendUrl) {
                    $frontendFailures = 0
                }
                else {
                    $frontendFailures++
                    Write-SupervisorLog "Frontend probe failed ($frontendFailures/$HealthFailureThreshold)."
                    if ($frontendFailures -ge $HealthFailureThreshold) {
                        $restartReason = "persistent frontend failure"
                        break
                    }
                }

                # A failed liveness request is a process/network symptom, not a
                # worker-state result. Do not charge it to the worker-health
                # timer; the liveness threshold owns that recovery path.
                if (-not $backendIsLive) {
                    continue
                }

                $now = [DateTimeOffset]::UtcNow
                if ($now -lt $workerHealthGraceEndsAt) {
                    $workerHealthFailureStartedAt = $null
                }
                elseif (Test-Endpoint $workerHealthUrl) {
                    $workerHealthFailureStartedAt = $null
                    $workerHealthRestartUsed = $false
                }
                else {
                    if ($null -eq $workerHealthFailureStartedAt) {
                        $workerHealthFailureStartedAt = $now
                    }
                    $workerUnhealthySeconds = ($now - $workerHealthFailureStartedAt).TotalSeconds
                    if (($now - $lastWorkerHealthIndicator).TotalSeconds -ge 60) {
                        Write-SupervisorLog "Worker health has been unhealthy for $([int]$workerUnhealthySeconds) seconds. This local log is not an external alert."
                        $lastWorkerHealthIndicator = $now
                    }
                    if ($workerUnhealthySeconds -ge $WorkerHealthRestartAfterSeconds -and -not $workerHealthRestartUsed) {
                        $workerHealthRestartUsed = $true
                        $restartReason = "persistent worker-health failure after $WorkerHealthRestartAfterSeconds seconds (single bounded restart)"
                        break
                    }
                }

                if (-not (Test-Endpoint $readyUrl) -and ($now - $lastReadinessIndicator).TotalSeconds -ge 60) {
                    Write-SupervisorLog "Readiness is degraded; inspect authenticated runtime status and application logs. Readiness is an operator signal and does not itself trigger restart."
                    $lastReadinessIndicator = $now
                }
            }
        }
        finally {
            Stop-ProcessTree -Process $frontendProcess -Description "frontend server"
            Stop-ProcessTree -Process $backendProcess -Description "backend"
            [TopSignalProcessJob]::Close($killOnCloseJob)
            $killOnCloseJob = [IntPtr]::Zero
        }

        $uptime = ([DateTimeOffset]::UtcNow - $startedAt).TotalSeconds
        if ($uptime -ge 300) {
            $restartAttempt = 0
        }
        else {
            $restartAttempt++
        }
        $delay = [Math]::Min(60, [Math]::Pow(2, [Math]::Min($restartAttempt, 6)))
        Write-SupervisorLog "$restartReason; restarting both child processes after $delay seconds."
        Start-Sleep -Seconds $delay
    }
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    if ($killOnCloseJob -ne [IntPtr]::Zero) {
        [TopSignalProcessJob]::Close($killOnCloseJob)
    }
    if ($null -ne $supervisorLock) {
        $supervisorLock.Dispose()
    }
}
