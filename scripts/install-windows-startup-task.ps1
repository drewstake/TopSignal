[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$RuntimeRoot = (Join-Path $env:ProgramData "TopSignal\runtime"),
    [string]$TaskName = "TopSignal Personal Device",
    [string]$ServiceUser = "$env:COMPUTERNAME\TopSignalSvc",
    [System.Management.Automation.PSCredential]$Credential,
    [int]$Port = 8000,
    [int]$FrontendPort = 4173,
    [int]$WorkerHealthStartupGraceSeconds = 120,
    [int]$WorkerHealthRestartAfterSeconds = 300,
    [switch]$StartNow,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-LocalServiceUser([string]$RequestedUser) {
    $parts = $RequestedUser.Split('\', 2)
    if ($parts.Count -eq 1) {
        $localName = $parts[0]
    }
    elseif ($parts[0] -eq "." -or $parts[0] -ieq $env:COMPUTERNAME) {
        $localName = $parts[1]
    }
    else {
        throw "ServiceUser must be a local account on this laptop, not a domain or cloud identity."
    }
    if ([string]::IsNullOrWhiteSpace($localName)) {
        throw "ServiceUser must name a dedicated local account."
    }

    $forbiddenNames = @("SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE", "Administrator", "Guest")
    if ($forbiddenNames -icontains $localName) {
        throw "ServiceUser must be a dedicated, non-built-in local account."
    }
    $localUser = Get-LocalUser -Name $localName -ErrorAction Stop
    if (-not $localUser.Enabled) {
        throw "Local service user '$localName' is disabled."
    }
    $administratorMembers = Get-LocalGroupMember -SID ([System.Security.Principal.SecurityIdentifier]"S-1-5-32-544")
    if ($administratorMembers | Where-Object { $_.SID -and $_.SID.Value -eq $localUser.SID.Value }) {
        throw "Local service user '$localName' is an Administrator. Remove that membership before installation."
    }
    return [PSCustomObject]@{
        Name = $localName
        CanonicalName = "$env:COMPUTERNAME\$localName"
        Sid = $localUser.SID.Value
    }
}

function Resolve-PowerShellExecutable {
    try {
        $currentExecutable = (Get-Process -Id $PID -ErrorAction Stop).Path
        if ((Split-Path -Leaf $currentExecutable) -in @("powershell.exe", "pwsh.exe")) {
            return $currentExecutable
        }
    }
    catch {
        # Fall through to edition-aware resolution.
    }

    $hostName = if ($PSVersionTable.PSEdition -eq "Core") { "pwsh.exe" } else { "powershell.exe" }
    $inPsHome = Join-Path $PSHOME $hostName
    if (Test-Path -LiteralPath $inPsHome -PathType Leaf) {
        return $inPsHome
    }
    return (Get-Command $hostName -ErrorAction Stop).Source
}

function Get-AllowRightsBySid([string]$Path) {
    $rights = @{}
    $acl = Get-Acl -LiteralPath $Path
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
            continue
        }
        try {
            $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
        }
        catch {
            continue
        }
        $existing = if ($rights.ContainsKey($sid)) { [long]$rights[$sid] } else { 0L }
        $rights[$sid] = $existing -bor [long]$rule.FileSystemRights
    }
    return [PSCustomObject]@{
        Protected = $acl.AreAccessRulesProtected
        OwnerSid = (New-Object -TypeName System.Security.Principal.NTAccount -ArgumentList $acl.Owner).Translate([System.Security.Principal.SecurityIdentifier]).Value
        Rights = $rights
    }
}

function Get-WriteRightsMask {
    # Composite rights such as Modify and FullControl also contain read bits,
    # so they cannot safely be used as a mask for detecting write capability.
    return [long](
        [System.Security.AccessControl.FileSystemRights]::WriteData -bor
        [System.Security.AccessControl.FileSystemRights]::AppendData -bor
        [System.Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
        [System.Security.AccessControl.FileSystemRights]::WriteAttributes -bor
        [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [System.Security.AccessControl.FileSystemRights]::Delete -bor
        [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [System.Security.AccessControl.FileSystemRights]::TakeOwnership
    )
}

function Assert-RequiredRights(
    [string]$Path,
    [string]$Sid,
    [System.Security.AccessControl.FileSystemRights]$RequiredRights,
    [switch]$ForbidWrite
) {
    $result = Get-AllowRightsBySid $Path
    if (-not $result.Rights.ContainsKey($Sid)) {
        throw "ACL for '$Path' does not grant the service identity required access."
    }
    $actual = [long]$result.Rights[$Sid]
    if (($actual -band [long]$RequiredRights) -ne [long]$RequiredRights) {
        throw "ACL for '$Path' does not grant the service identity $RequiredRights."
    }
    if ($ForbidWrite) {
        $writeMask = Get-WriteRightsMask
        if (($actual -band $writeMask) -ne 0) {
            throw "ACL for '$Path' lets the service identity modify the release tree."
        }
        if ($result.OwnerSid -eq $Sid) {
            throw "The service identity owns '$Path' and could replace its read-only ACL."
        }
    }
}

function Assert-NoBroadWrite([string]$Path) {
    $result = Get-AllowRightsBySid $Path
    $writeMask = Get-WriteRightsMask
    $broadSids = @("S-1-1-0", "S-1-5-11", "S-1-5-32-545")
    foreach ($sid in $broadSids) {
        if ($result.Rights.ContainsKey($sid) -and (([long]$result.Rights[$sid] -band $writeMask) -ne 0)) {
            throw "ACL for '$Path' grants write access to a broad identity ($sid)."
        }
    }
}

function Assert-PrivateDataAcl([string]$Path, [string]$ServiceSid) {
    $result = Get-AllowRightsBySid $Path
    $allowed = @($ServiceSid, "S-1-5-18", "S-1-5-32-544")
    foreach ($sid in $result.Rights.Keys) {
        if ($sid -notin $allowed -and [long]$result.Rights[$sid] -ne 0) {
            throw "Private data ACL for '$Path' grants access outside the service, SYSTEM, and Administrators ($sid)."
        }
    }
}

function Assert-HardenedDeployment(
    [string]$ReleasePath,
    [string]$WritableRuntimePath,
    [string]$EnvironmentFile,
    [string]$PythonExecutable,
    [string]$ServiceSid
) {
    if (Test-Path -LiteralPath (Join-Path $ReleasePath ".git")) {
        throw "Refusing to schedule a mutable Git working tree. Install from an ACL-hardened release copy."
    }

    foreach ($protectedPath in @($ReleasePath, $EnvironmentFile, $WritableRuntimePath)) {
        if (-not (Get-Acl -LiteralPath $protectedPath).AreAccessRulesProtected) {
            throw "ACL inheritance is still enabled on '$protectedPath'. Follow the hardening procedure before installation."
        }
    }

    $releaseItems = @(
        Get-Item -LiteralPath $ReleasePath
        Get-ChildItem -LiteralPath $ReleasePath -Force -Recurse
    )
    foreach ($item in $releaseItems) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release item '$($item.FullName)' is a reparse point. Replace it with release-owned content."
        }
        $requiredRights = if ($item.FullName -ieq $EnvironmentFile) {
            [System.Security.AccessControl.FileSystemRights]::Read
        }
        else {
            [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
        }
        Assert-RequiredRights -Path $item.FullName -Sid $ServiceSid -RequiredRights $requiredRights -ForbidWrite
        Assert-NoBroadWrite -Path $item.FullName
    }
    Assert-RequiredRights -Path $EnvironmentFile -Sid $ServiceSid -RequiredRights Read -ForbidWrite
    Assert-PrivateDataAcl -Path $EnvironmentFile -ServiceSid $ServiceSid
    Assert-RequiredRights -Path $PythonExecutable -Sid $ServiceSid -RequiredRights ReadAndExecute -ForbidWrite
    Assert-RequiredRights -Path $WritableRuntimePath -Sid $ServiceSid -RequiredRights Modify
    foreach ($item in @(Get-Item -LiteralPath $WritableRuntimePath; Get-ChildItem -LiteralPath $WritableRuntimePath -Force -Recurse)) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Runtime item '$($item.FullName)' is a reparse point; use local private storage."
        }
        Assert-PrivateDataAcl -Path $item.FullName -ServiceSid $ServiceSid
    }
}

if ($Port -lt 1 -or $Port -gt 65535 -or $FrontendPort -lt 1 -or $FrontendPort -gt 65535) {
    throw "Backend and frontend ports must be between 1 and 65535."
}
if ($Port -eq $FrontendPort) {
    throw "Backend and frontend ports must be different."
}
if ($WorkerHealthStartupGraceSeconds -lt 30 -or $WorkerHealthRestartAfterSeconds -lt 120) {
    throw "Worker-health startup grace must be at least 30 seconds and restart delay at least 120 seconds."
}

$resolvedRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$resolvedRuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$launcher = Join-Path $resolvedRoot "scripts\run-production.ps1"
$frontendServer = Join-Path $resolvedRoot "scripts\serve-production-frontend.py"
$backendServer = Join-Path $resolvedRoot "scripts\serve-production-backend.py"
$frontendIndex = Join-Path $resolvedRoot "frontend\dist\index.html"
$python = Join-Path $resolvedRoot "backend\.venv\Scripts\python.exe"
$envFile = Join-Path $resolvedRoot "backend\.env"

foreach ($requiredPath in @($launcher, $backendServer, $frontendServer, $frontendIndex, $python, $envFile)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required production file not found: $requiredPath"
    }
}

$serviceIdentity = Resolve-LocalServiceUser $ServiceUser
Assert-HardenedDeployment `
    -ReleasePath $resolvedRoot `
    -WritableRuntimePath $resolvedRuntimeRoot `
    -EnvironmentFile $envFile `
    -PythonExecutable $python `
    -ServiceSid $serviceIdentity.Sid

if ($null -eq $Credential) {
    $Credential = Get-Credential `
        -UserName $serviceIdentity.CanonicalName `
        -Message "Enter the password for the dedicated TopSignal scheduled-task identity."
}
$credentialLeaf = $Credential.UserName.Split('\')[-1]
if ($credentialLeaf -ine $serviceIdentity.Name) {
    throw "Credential user '$($Credential.UserName)' does not match '$($serviceIdentity.CanonicalName)'."
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing -and -not $Force) {
    throw "Scheduled task '$TaskName' already exists. Re-run with -Force to replace exactly that task."
}

$powerShellExecutable = Resolve-PowerShellExecutable
$actionArguments = "-NoProfile -NonInteractive -File `"$launcher`" -RepoRoot `"$resolvedRoot`" -RuntimeRoot `"$resolvedRuntimeRoot`" -PythonPath `"$python`" -Port $Port -FrontendPort $FrontendPort -WorkerHealthStartupGraceSeconds $WorkerHealthStartupGraceSeconds -WorkerHealthRestartAfterSeconds $WorkerHealthRestartAfterSeconds"
$action = New-ScheduledTaskAction `
    -Execute $powerShellExecutable `
    -Argument $actionArguments `
    -WorkingDirectory $resolvedRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal `
    -UserId $serviceIdentity.CanonicalName `
    -LogonType Password `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "TopSignal loopback API, closed-candle worker, and local control UI"

$plainPassword = $null
try {
    $plainPassword = $Credential.GetNetworkCredential().Password
    Register-ScheduledTask `
        -TaskName $TaskName `
        -InputObject $task `
        -User $serviceIdentity.CanonicalName `
        -Password $plainPassword `
        -Force | Out-Null
}
finally {
    $plainPassword = $null
}

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
}

Write-Output "Installed '$TaskName' as $($serviceIdentity.CanonicalName) at Limited run level."
Write-Output "Verify http://127.0.0.1:$FrontendPort/ and http://127.0.0.1:$Port/ready locally."
