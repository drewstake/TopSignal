[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ToolRoot,
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$CacheRoot,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$OwnerHash,
    [string]$ServiceUser="$env:COMPUTERNAME\TopSignalSvc",
    [System.Management.Automation.PSCredential]$Credential,
    [switch]$StartNow
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$tool=(Resolve-Path -LiteralPath $ToolRoot).Path
$python=(Resolve-Path -LiteralPath $PythonPath).Path
$cache=(Resolve-Path -LiteralPath $CacheRoot).Path
$runner=Join-Path $tool 'backfill_depth_features.py'
if(!(Test-Path -LiteralPath $runner -PathType Leaf)){throw 'Feature builder missing'}
$taskName='TopSignal Microstructure Features'
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Task already exists; review before changing its schedule'}
$account=Get-LocalUser -Name ($ServiceUser.Split('\')[-1])
if(!$account.Enabled -or $account.Name -in @('Administrator','SYSTEM','Guest')){throw 'Dedicated enabled service identity required'}
if(Get-LocalGroupMember -SID 'S-1-5-32-544'|Where-Object {$_.SID -eq $account.SID}){throw 'Feature worker must not be Administrator'}
$canonicalUser="$env:COMPUTERNAME\$($account.Name)"
if($ServiceUser -ine $account.Name -and $ServiceUser -ine $canonicalUser){throw 'Dedicated local service identity required'}
$currentIdentity=[Security.Principal.WindowsIdentity]::GetCurrent()
if($null -eq $Credential -and $currentIdentity.User.Value -ne $account.SID.Value){
    throw 'Cross-account S4U registration requires -Credential for the service account, even from an administrator. Use the existing protected deployment credential; do not reset the account.'
}
if($null -ne $Credential -and $Credential.UserName -ine $account.Name -and $Credential.UserName -ine $canonicalUser){throw 'Credential identity mismatch'}
# Only the helper bundle needs a new read/execute grant. Preserve existing ACLs,
# raw recording permissions and derived-cache write permissions; never grant write
# access to executable helpers or broader access to the trading deployment.
$toolAcl=Get-Acl -LiteralPath $tool
$rule=New-Object Security.AccessControl.FileSystemAccessRule($account.SID,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')
$toolAcl.AddAccessRule($rule)
Set-Acl -LiteralPath $tool -AclObject $toolAcl

# Explicit COM registration preserves S4U while supplying the password required
# to authorize registration for another user. No password is saved by this task.
# https://learn.microsoft.com/windows/win32/taskschd/security-contexts-for-running-tasks
$scheduler=New-Object -ComObject Schedule.Service
$scheduler.Connect()
$definition=$scheduler.NewTask(0)
$definition.RegistrationInfo.Description='Bounded observed microstructure features from sealed local recordings; no network, broker, or trading dependency. Stops on existing storage limits.'
$definition.Principal.UserId=$canonicalUser
$definition.Principal.LogonType=2 # TASK_LOGON_S4U
$definition.Principal.RunLevel=0 # TASK_RUNLEVEL_LUA (Limited)
$settings=$definition.Settings
$settings.Enabled=$true
$settings.StartWhenAvailable=$true
$settings.ExecutionTimeLimit='PT3M'
$settings.MultipleInstances=2 # TASK_INSTANCES_IGNORE_NEW
$settings.DisallowStartIfOnBatteries=$false
$settings.StopIfGoingOnBatteries=$false
$settings.Hidden=$true
$settings.Priority=7 # Below normal; leave trading and recording responsive.
$trigger=$definition.Triggers.Create(1) # Time trigger; repeats indefinitely.
$trigger.StartBoundary=(Get-Date).AddMinutes(1).ToString('s')
$trigger.Repetition.Interval='PT5M'
$trigger.Enabled=$true
$startup=$definition.Triggers.Create(8) # Reboot recovery; no interactive login.
$startup.Delay='PT1M'
$startup.Enabled=$true
$action=$definition.Actions.Create(0)
$action.Path=$python
$action.Arguments="-B `"$runner`" --cache-root `"$cache`" --owner-hash $OwnerHash --latest"
$action.WorkingDirectory=$tool
$plainPassword=$null
try{
    if($null -ne $Credential){$plainPassword=$Credential.GetNetworkCredential().Password}
    # TASK_CREATE refuses replacement, including a concurrent registration.
    $scheduler.GetFolder('\').RegisterTaskDefinition($taskName,$definition,2,$canonicalUser,$plainPassword,2,$null)|Out-Null
}finally{$plainPassword=$null}
$installed=Get-ScheduledTask -TaskName $taskName
if($installed.Principal.LogonType.ToString() -ne 'S4U' -or $installed.Principal.RunLevel.ToString() -ne 'Limited'){
    Disable-ScheduledTask -TaskName $taskName|Out-Null
    throw 'Unexpected installed security context; feature task disabled'
}
if($StartNow){Start-ScheduledTask -TaskName $taskName}
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State,@{N='User';E={$_.Principal.UserId}},@{N='Logon';E={$_.Principal.LogonType}}
