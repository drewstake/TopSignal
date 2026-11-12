[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ToolRoot,
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [Parameter(Mandatory=$true)][System.Management.Automation.PSCredential]$Credential,
    [string]$ServiceUser="$env:COMPUTERNAME\TopSignalSvc",
    [string]$TaskName='TopSignal Discord TopBot',
    [switch]$StartNow
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$tool=(Resolve-Path -LiteralPath $ToolRoot).Path
$python=(Resolve-Path -LiteralPath $PythonPath).Path
$config=(Resolve-Path -LiteralPath $ConfigPath).Path
$runner=Join-Path $tool 'discord_topbot.py'
if(!(Test-Path -LiteralPath $runner -PathType Leaf)){throw 'Reporter missing'}
if($config -ne (Join-Path $tool 'config.json')){throw 'Configuration must belong to reporter bundle'}
$localName=$ServiceUser.Split('\')[-1]
$account=Get-LocalUser -Name $localName
if(!$account.Enabled -or $localName -in @('Administrator','SYSTEM','Guest')){throw 'Dedicated enabled service identity required'}
if(Get-LocalGroupMember -SID 'S-1-5-32-544'|Where-Object {$_.SID -eq $account.SID}){throw 'Reporter service must not be Administrator'}
if($Credential.UserName -ine $ServiceUser -and $Credential.UserName -ine $localName){throw 'Credential identity mismatch'}
if(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue){throw 'Task already exists; inspect before replacing'}
$action=New-ScheduledTaskAction -Execute $python -Argument "-B `"$runner`" --config `"$config`"" -WorkingDirectory $tool
$startup=New-ScheduledTaskTrigger -AtStartup
$watchdog=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal=New-ScheduledTaskPrincipal -UserId $ServiceUser -LogonType Password -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
$definition=New-ScheduledTask -Action $action -Trigger @($startup,$watchdog) -Principal $principal -Settings $settings -Description 'Reports verified TopBot fills to Discord and updates completed P&L. Read-only execution database access; no broker calls.'
Register-ScheduledTask -TaskName $TaskName -InputObject $definition -User $ServiceUser -Password $Credential.GetNetworkCredential().Password | Out-Null
if($StartNow){Start-ScheduledTask -TaskName $TaskName}
$task=Get-ScheduledTask -TaskName $TaskName
[pscustomobject]@{task=$TaskName;state=$task.State.ToString();user=$task.Principal.UserId;runlevel=$task.Principal.RunLevel.ToString();startup=$true;watchdog_minutes=5;restart_minutes=1}|ConvertTo-Json -Compress
