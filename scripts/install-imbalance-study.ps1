[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$ToolRoot,
  [Parameter(Mandatory)][string]$PythonPath,
  [Parameter(Mandatory)][PSCredential]$Credential,
  [ValidateSet('Collector','Dashboard')][string]$Component='Collector'
)
$ErrorActionPreference='Stop'
$tool=(Resolve-Path -LiteralPath $ToolRoot).Path
$python=(Resolve-Path -LiteralPath $PythonPath).Path
$identity=(Get-ScheduledTask -TaskName 'TopSignal Personal Device').Principal.UserId
$acct=Get-LocalUser -Name $identity.Split('\')[-1]
if(!$acct.Enabled -or $acct.Name -ine 'TopSignalSvc' -or
   $Credential.UserName.Split('\')[-1] -ine $acct.Name){throw 'Expected limited TopSignal service identity'}
if(Get-LocalGroupMember -SID 'S-1-5-32-544' | Where-Object {$_.SID -eq $acct.SID}){throw 'Administrator refused'}
$cfg=Join-Path $tool 'config.json'
$config=Get-Content -Raw -LiteralPath $cfg | ConvertFrom-Json
if($Component -eq 'Collector'){
    $name='TopSignal Imbalance Study'
    $runner=Join-Path $tool 'track_imbalances.py'
} else {
    $name='TopSignal Study Dashboard'
    $runner=Join-Path $tool 'serve_imbalance_dashboard.py'
}
if(!(Test-Path -LiteralPath $runner -PathType Leaf)){throw 'Missing tested component'}
if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){throw 'Component already installed'}
if($config.account -le 0 -or $config.config -le 0){throw 'Explicit scope required'}
$action=New-ScheduledTaskAction -Execute $python -Argument ('-B "{0}" --config "{1}"' -f $runner,$cfg) -WorkingDirectory $tool
$boot=New-ScheduledTaskTrigger -AtStartup
$boot.Delay='PT30S'
$watch=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings=New-ScheduledTaskSettingsSet -Priority 7 -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
$principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType Password -RunLevel Limited
$task=New-ScheduledTask -Action $action -Trigger @($boot,$watch) -Settings $settings -Principal $principal -Description "TopBot read-only imbalance research $Component"
Register-ScheduledTask -TaskName $name -InputObject $task -User $identity -Password $Credential.GetNetworkCredential().Password | Out-Null
$installed=Get-ScheduledTask -TaskName $name
if($installed.Settings.Priority -ne 7 -or $installed.Principal.RunLevel.ToString() -ne 'Limited'){throw 'Component identity or priority mismatch'}
Start-ScheduledTask -TaskName $name
Get-ScheduledTask -TaskName $name | Select-Object TaskName,State
