[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'Activate', 'Verify')][string]$Phase = 'Prepare',
    [string]$Stage,
    [string]$CIReceiptHash,
    [string]$BundleHash,
    [switch]$FunctionsOnly
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

function Assert-ChildPath([string]$Root, [string]$Child) {
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $resolvedChild = [IO.Path]::GetFullPath($Child)
    if (-not $resolvedChild.StartsWith($resolvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Release path escaped the configured root'
    }
    return $resolvedChild
}

function Assert-OwnedStop([string]$Runtime, [string]$Marker) {
    $path = Join-Path $Runtime 'STOP'
    if (-not (Test-Path -LiteralPath $path) -or (Get-Content -LiteralPath $path -Raw) -ne $Marker) {
        throw 'STOP ownership changed; refusing to remove an operator marker'
    }
}

function Remove-OwnedStop([string]$Runtime, [string]$Marker) {
    Assert-OwnedStop $Runtime $Marker
    Remove-Item -LiteralPath (Join-Path $Runtime 'STOP')
}

function Request-GracefulStop([string]$Runtime, [string]$Marker, [string]$TaskName) {
    $handle = [IO.File]::Open((Join-Path $Runtime 'STOP'), 'CreateNew', 'Write', 'None')
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Marker)
        $handle.Write($bytes, 0, $bytes.Length)
    } finally { $handle.Dispose() }
    [IO.File]::WriteAllText((Join-Path $Runtime 'shutdown.request'), $Marker)
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
    do {
        try {
            $lock = [IO.File]::Open((Join-Path $Runtime 'supervisor.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
            $lock.Dispose()
            if (Get-NetTCPConnection -State Listen -LocalPort 8000,4173 -ErrorAction SilentlyContinue) {
                throw 'Unexpected listeners remain'
            }
            return
        } catch { Start-Sleep -Milliseconds 500 }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'Graceful stop deadline exceeded; STOP retained, no process was force-killed'
}

function Set-ReleaseAcl([string]$Release, [string]$ServiceSid) {
    # Set a complete, explicit ACL on release-owned files only. There are no
    # junctions, so this cannot traverse into the currently running environment.
    $items = @(Get-Item -LiteralPath $Release; Get-ChildItem -LiteralPath $Release -Force -Recurse)
    foreach ($item in $items) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point in candidate' }
    }
    foreach ($item in $items) {
        $acl = Get-Acl -LiteralPath $item.FullName
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleSpecific($rule) }
        $acl.SetOwner([Security.Principal.SecurityIdentifier]'S-1-5-32-544')
        $inheritance = if ($item.PSIsContainer) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
        foreach ($sid in @('S-1-5-18', 'S-1-5-32-544', $ServiceSid)) {
            $rights = if ($sid -eq $ServiceSid) {
                if (Test-PrivateEnvironmentFile $item.Name) { [Security.AccessControl.FileSystemRights]::Read }
                else { [Security.AccessControl.FileSystemRights]::ReadAndExecute }
            } else { [Security.AccessControl.FileSystemRights]::FullControl }
            $rule = New-Object Security.AccessControl.FileSystemAccessRule(
                ([Security.Principal.SecurityIdentifier]$sid), $rights, $inheritance,
                [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $item.FullName -AclObject $acl
    }
}

function Invoke-ReleaseTool([string]$Python, [string]$Tool, [string]$Command, [string]$StagePath, [string[]]$Extra = @()) {
    & $Python -B $Tool $Command --stage $StagePath @Extra
    if ($LASTEXITCODE -ne 0) { throw "Release $Command check failed ($LASTEXITCODE)" }
}

function Wait-ReleaseHealth([scriptblock]$Probe, [int]$Seconds = 180) {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($Seconds)
    do {
        try { & $Probe; return } catch { Start-Sleep -Seconds 2 }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'Frontend/API/worker readiness deadline exceeded'
}

function Invoke-ReleaseSwitch(
    [scriptblock]$Preflight, [scriptblock]$Stop, [scriptblock]$PostStop,
    [scriptblock]$StartCandidate, [scriptblock]$VerifyCandidate,
    [scriptblock]$CanRollback, [scriptblock]$StopCandidate,
    [scriptblock]$RestorePrevious, [scriptblock]$VerifyPrevious
) {
    & $Preflight
    & $Stop
    $candidateStarted = $false
    try {
        & $PostStop
        # Mark before starting: a start command can partially succeed then throw.
        $candidateStarted = $true
        & $StartCandidate
        & $VerifyCandidate
    } catch {
        $activationError = $_
        if ($candidateStarted) {
            # Never interrupt a newly opened position just to roll code back.
            & $CanRollback
            & $StopCandidate
        }
        & $RestorePrevious
        & $VerifyPrevious
        throw "Candidate activation failed; previous release restored: $activationError"
    }
}

if ($FunctionsOnly) { return }
if (-not $Stage) { throw 'Stage is required' }
$Stage = (Resolve-Path -LiteralPath $Stage).Path
$config = Get-Content -LiteralPath (Join-Path $Stage 'host.json') -Raw | ConvertFrom-Json
$previous = (Resolve-Path -LiteralPath $config.previous_release).Path
$release = Assert-ChildPath $config.releases_root (Join-Path $config.releases_root $config.release_id)
[void](Assert-ChildPath $config.releases_root $previous)
if ($release -ieq $previous) { throw 'Candidate cannot be the running release' }
$runtime = (Resolve-Path -LiteralPath $config.runtime_root).Path
$tool = Join-Path $Stage 'release-host.py'
$previousPython = Join-Path $previous 'backend\.venv\Scripts\python.exe'
$python = Join-Path $release 'backend\.venv\Scripts\python.exe'
$taskName = 'TopSignal Personal Device'
$task = Get-ScheduledTask -TaskName $taskName
 $expectedTaskRelease = if ($Phase -eq 'Verify') { $release } else { $previous }
if ($task.Actions.Count -ne 1 -or -not $task.Actions[0].Arguments.Contains((Join-Path $expectedTaskRelease 'scripts\run-production.ps1'))) {
    throw 'Scheduled task no longer points to the expected previous release'
}
$currentManifestHash = (Get-FileHash -LiteralPath (Join-Path $Stage 'manifest.json')).Hash.ToLowerInvariant()
if ($currentManifestHash -ne $config.manifest_sha256) { throw 'Manifest checksum mismatch' }
$manifest = Get-Content -LiteralPath (Join-Path $Stage 'manifest.json') -Raw | ConvertFrom-Json
foreach ($name in @('release-host.ps1', 'release-host.py', 'release_lib.py')) {
    if ((Get-FileHash -LiteralPath (Join-Path $Stage $name)).Hash.ToLowerInvariant() -ne $manifest.source_sha256.("scripts/$name")) {
        throw 'Bootstrap source does not match the validated release'
    }
}
function Log([string]$Message) {
    ([DateTimeOffset]::UtcNow.ToString('o') + ' ' + $Message) | Tee-Object -FilePath (Join-Path $Stage 'activation.log') -Append
}
if ($Phase -eq 'Prepare') {
    Log 'Preparing candidate while current release remains running.'
    Invoke-ReleaseTool $previousPython $tool 'prepare' $Stage
    . (Join-Path $release 'scripts\install-windows-startup-task.ps1') -FunctionsOnly
    $identity = Resolve-LocalServiceUser $task.Principal.UserId
    Set-ReleaseAcl $release $identity.Sid
    Assert-HardenedDeployment $release $runtime (Join-Path $release 'backend\.env') $python $identity.Sid
    Invoke-ReleaseTool $python $tool 'verify' $Stage
    # --check never applies a database migration. Existing checked startup is
    # preserved; pending migrations require the migration-specific runbook.
    Invoke-ReleaseTool $python $tool 'migration-check' $Stage
    @{manifest_sha256=$config.manifest_sha256;config_sha256=(Get-FileHash -LiteralPath (Join-Path $Stage 'host.json')).Hash.ToLowerInvariant()} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Stage 'ready.json')
    Log 'Candidate prepared, ACL-checked and checksum-verified.'
    return
}
if ($Phase -eq 'Verify') {
    Invoke-ReleaseTool $python $tool 'verify' $Stage
    Invoke-ReleaseTool $python $tool 'health' $Stage
    return
}
if ($task.State -ne 'Running' -or (Test-Path -LiteralPath (Join-Path $runtime 'STOP'))) {
    throw 'Runtime not running or operator STOP exists; activation blocked'
}
if ($BundleHash -ne $config.manifest_sha256) { throw 'Requested bundle does not match the staged candidate' }
$ready = Get-Content -LiteralPath (Join-Path $Stage 'ready.json') -Raw | ConvertFrom-Json
if ($ready.manifest_sha256 -ne $BundleHash -or $ready.config_sha256 -ne (Get-FileHash -LiteralPath (Join-Path $Stage 'host.json')).Hash.ToLowerInvariant()) {
    throw 'Candidate has not completed preparation for this configuration'
}
if (-not $CIReceiptHash -or (Get-FileHash -LiteralPath (Join-Path $Stage 'ci.json')).Hash.ToLowerInvariant() -ne $CIReceiptHash) {
    throw 'Exact-commit CI receipt required for activation'
}
$ci = Get-Content -LiteralPath (Join-Path $Stage 'ci.json') -Raw | ConvertFrom-Json
$age = ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$ci.verified_at)
if ($ci.commit -ne $config.git_commit -or $age -lt -30 -or $age -gt 900) { throw 'CI receipt is stale or belongs to another commit' }
Invoke-ReleaseTool $python $tool 'verify' $Stage
. (Join-Path $release 'scripts\install-windows-startup-task.ps1') -FunctionsOnly
$identity = Resolve-LocalServiceUser $task.Principal.UserId
Assert-HardenedDeployment $release $runtime (Join-Path $release 'backend\.env') $python $identity.Sid
$sidecars = @{}
foreach ($sidecar in @(Get-ScheduledTask | Where-Object { $_.TaskName -like 'TopSignal *' -and $_.TaskName -ne $taskName })) {
    $sidecars[$sidecar.TaskName] = Export-ScheduledTask -TaskName $sidecar.TaskName
}
$recorders = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'record_market_depth.py' } | Select-Object ProcessId,CreationDate)
if (-not $recorders.Count) { throw 'Independent recorder is not running' }
$credential = & $config.credential_script
if ($credential -isnot [Management.Automation.PSCredential] -or $credential.UserName -ine $identity.CanonicalName) {
    throw 'Credential helper must return only the expected limited service credential'
}
$oldAction = $task.Actions[0]
$newAction = New-ScheduledTaskAction -Execute $oldAction.Execute -Argument $oldAction.Arguments.Replace($previous, $release) -WorkingDirectory $release
Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $Stage 'task-before.xml')
$marker = 'TopSignal release ' + $config.release_id + ' ' + [guid]::NewGuid().ToString()
[IO.File]::WriteAllText((Join-Path $Stage 'owned-stop.txt'), $marker)
function Start-Action($Action, [string]$Name) {
    Assert-OwnedStop $runtime $marker
    Set-ScheduledTask -TaskName $taskName -Action $Action -User $credential.UserName -Password $credential.GetNetworkCredential().Password | Out-Null
    Remove-OwnedStop $runtime $marker
    Enable-ScheduledTask -TaskName $taskName | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Log "$Name started."
}
function Assert-Sidecars {
    foreach ($name in $sidecars.Keys) {
        if ((Export-ScheduledTask -TaskName $name) -ne $sidecars[$name]) { throw "Sidecar changed: $name" }
    }
    $after = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'record_market_depth.py' } | Select-Object ProcessId,CreationDate)
    if (Compare-Object $recorders $after -Property ProcessId,CreationDate) { throw 'Independent recorder changed' }
}
try {
    Invoke-ReleaseSwitch -Preflight {
        Invoke-ReleaseTool $previousPython $tool 'gate' $Stage @('--previous', '--record')
        Log 'Broker flat, runner configuration recorded; requesting graceful switch.'
    } -Stop {
        Request-GracefulStop $runtime $marker $taskName
    } -PostStop {
        Invoke-ReleaseTool $python $tool 'gate' $Stage
        Invoke-ReleaseTool $python $tool 'verify' $Stage
    } -StartCandidate {
        Start-Action $newAction 'Candidate'
    } -VerifyCandidate {
        Wait-ReleaseHealth { Invoke-ReleaseTool $python $tool 'health' $Stage }
        Invoke-ReleaseTool $python $tool 'gate' $Stage @('--allow-exposure', '--allow-restart-disarm')
        Invoke-ReleaseTool $python $tool 'verify' $Stage
        Assert-Sidecars
        Invoke-ReleaseTool $python $tool 'recording' $Stage
        Log 'Candidate frontend, API, worker, broker configuration and sidecars verified.'
    } -CanRollback {
        Invoke-ReleaseTool $python $tool 'verify' $Stage
        Invoke-ReleaseTool $python $tool 'gate' $Stage @('--allow-restart-disarm')
    } -StopCandidate {
        if (-not (Test-Path -LiteralPath (Join-Path $runtime 'STOP'))) { Request-GracefulStop $runtime $marker $taskName }
        else { Assert-OwnedStop $runtime $marker }
    } -RestorePrevious {
        Start-Action $oldAction 'Previous release'
    } -VerifyPrevious {
        Wait-ReleaseHealth { Invoke-ReleaseTool $previousPython $tool 'health' $Stage @('--previous') }
        Invoke-ReleaseTool $previousPython $tool 'gate' $Stage @('--previous', '--allow-exposure', '--allow-restart-disarm')
        Assert-Sidecars
    }
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $Stage 'task-after.xml')
    Log 'Release complete.'
} finally { $credential = $null }
