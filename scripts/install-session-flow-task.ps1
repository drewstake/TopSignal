[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ToolRoot,
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][System.Management.Automation.PSCredential]$Credential,
    [string]$ServiceUser="$env:COMPUTERNAME\TopSignalSvc",
    [string]$TaskName='TopSignal Session Order Flow',
    [switch]$StartNow
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
function Assert-NoReparse([string]$Path) {
    $item=Get-Item -LiteralPath $Path -Force
    while($null -ne $item) {
        if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Reparse path refused'}
        $item=if($item -is [IO.DirectoryInfo]){$item.Parent}else{$item.Directory}
    }
}
function Set-HelperAcl([string]$Path,[string]$Sid,[bool]$Writable) {
    $item=Get-Item -LiteralPath $Path -Force
    $acl=if($item.PSIsContainer){New-Object Security.AccessControl.DirectorySecurity}else{New-Object Security.AccessControl.FileSecurity}
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]'S-1-5-32-544')
    $inherit=if($item.PSIsContainer){[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}else{[Security.AccessControl.InheritanceFlags]::None}
    foreach($identity in @('S-1-5-18','S-1-5-32-544',$Sid)){
        $rights=if($identity -eq $Sid){if($Writable){'Modify'}else{'ReadAndExecute'}}else{'FullControl'}
        $rule=New-Object Security.AccessControl.FileSystemAccessRule(([Security.Principal.SecurityIdentifier]$identity),$rights,$inherit,'None','Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

$tool=(Resolve-Path -LiteralPath $ToolRoot).Path.TrimEnd('\')
$python=(Resolve-Path -LiteralPath $PythonPath).Path
$runtime=[IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
$runner=Join-Path $tool 'maintain_session_flow.py'
$config=Join-Path $tool 'config.json'
if(!(Test-Path -LiteralPath $runner -PathType Leaf)){throw 'Session-flow helper missing'}
if(Test-Path -LiteralPath $runtime){throw 'Runtime directory already exists; inspect before replacing'}
if((Split-Path -Leaf $runtime) -ne 'session-flow-maintainer'){throw 'Dedicated session-flow-maintainer runtime directory required'}
if($tool.StartsWith($runtime+'\',[StringComparison]::OrdinalIgnoreCase) -or $runtime.StartsWith($tool+'\',[StringComparison]::OrdinalIgnoreCase) -or $tool -ieq $runtime){throw 'Executable and writable runtime roots must be separate'}
Assert-NoReparse $tool
Assert-NoReparse $python
Assert-NoReparse (Split-Path -Parent $runtime)
foreach($item in @(Get-ChildItem -LiteralPath $tool -Recurse -Force)){Assert-NoReparse $item.FullName}
$cfg=Get-Content -LiteralPath $config -Raw|ConvertFrom-Json
if($cfg.source_sha256 -ne (Get-FileHash -LiteralPath $runner -Algorithm SHA256).Hash.ToLowerInvariant()){throw 'Helper source hash mismatch'}
if([IO.Path]::GetFullPath($cfg.status_path) -ine (Join-Path $runtime 'status.json')){throw 'Status must belong to dedicated runtime directory'}
if($cfg.api_port -ne 8000){throw 'Production helper must use the existing loopback API port'}
if($cfg.account_id -le 0 -or $cfg.bot_config_id -le 0){throw 'Explicit account and config scope required'}
Assert-NoReparse $cfg.recorder_status
$localName=$ServiceUser.Split('\')[-1]
$account=Get-LocalUser -Name $localName
$canonicalUser="$env:COMPUTERNAME\$localName"
if($ServiceUser -ine $localName -and $ServiceUser -ine $canonicalUser){throw 'Dedicated local identity required'}
if(!$account.Enabled -or $localName -in @('Administrator','SYSTEM','Guest')){throw 'Enabled service identity required'}
if(Get-LocalGroupMember -SID 'S-1-5-32-544'|Where-Object {$_.SID -eq $account.SID}){throw 'Helper service must not be Administrator'}
if($Credential.UserName -ine $canonicalUser -and $Credential.UserName -ine $localName){throw 'Credential identity mismatch'}
if(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue){throw 'Task already exists; inspect before replacing'}

# Restrict only this new helper's files. Existing runtime/releases are untouched.
foreach($item in @(Get-ChildItem -LiteralPath $tool -Recurse -Force|Sort-Object {$_.FullName.Length} -Descending)){
    Set-HelperAcl $item.FullName $account.SID.Value $false
}
Set-HelperAcl $tool $account.SID.Value $false
New-Item -ItemType Directory -Path $runtime|Out-Null
Set-HelperAcl $runtime $account.SID.Value $true

$action=New-ScheduledTaskAction -Execute $python -Argument "-B `"$runner`" --config `"$config`"" -WorkingDirectory $tool
$startup=New-ScheduledTaskTrigger -AtStartup
$startup.Delay='PT1M'
$watchdog=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal=New-ScheduledTaskPrincipal -UserId $canonicalUser -LogonType Password -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -Priority 7 -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
$definition=New-ScheduledTask -Action $action -Trigger @($startup,$watchdog) -Principal $principal -Settings $settings -Description 'Optional loopback session order-flow warming with recorder-pressure guards; no trading mutations, browser dependency or raw archive writes.'
# No -Force: never replace a pre-existing task or its identity/settings.
Register-ScheduledTask -TaskName $TaskName -InputObject $definition -User $canonicalUser -Password $Credential.GetNetworkCredential().Password|Out-Null
$installed=Get-ScheduledTask -TaskName $TaskName
if($installed.Settings.Priority -ne 7 -or $installed.Principal.RunLevel.ToString() -ne 'Limited' -or $installed.Settings.MultipleInstances.ToString() -ne 'IgnoreNew'){
    Disable-ScheduledTask -TaskName $TaskName|Out-Null
    throw 'Unexpected helper configuration; new task disabled'
}
if($StartNow){Start-ScheduledTask -TaskName $TaskName}
[ordered]@{task=$TaskName;user=$canonicalUser;priority=7;startup_delay_seconds=60;watchdog_minutes=5;state=[string](Get-ScheduledTask -TaskName $TaskName).State}|ConvertTo-Json -Compress
