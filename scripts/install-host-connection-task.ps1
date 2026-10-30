[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RemoteTarget,
    [Parameter(Mandatory = $true)][string]$KeyPath,
    [string]$InstallDirectory = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssh'),
    [string]$TaskName = 'TopSignal Host Connection',
    [ValidateRange(1,65535)][int]$FrontendPort = 4173,
    [ValidateRange(1,65535)][int]$ApiPort = 8000,
    [ValidateRange(1,65535)][int]$RemoteFrontendPort = 4173,
    [ValidateRange(1,65535)][int]$RemoteApiPort = 8000,
    [switch]$DoNotStart
)

$ErrorActionPreference = 'Stop'
$resolvedKey = (Resolve-Path -LiteralPath $KeyPath).Path
New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
$installPath = (Resolve-Path -LiteralPath $InstallDirectory).Path
$scriptPath = Join-Path $installPath 'maintain-host-connection.ps1'
$logPath = Join-Path $installPath 'peteamdpc-host-connection.log'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'maintain-host-connection.ps1') -Destination $scriptPath -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'host-connection-job.ps1') -Destination $installPath -Force
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -RemoteTarget "{1}" -KeyPath "{2}" -LogPath "{3}"' -f $scriptPath, $RemoteTarget, $resolvedKey, $logPath
$arguments += " -FrontendPort $FrontendPort -ApiPort $ApiPort -RemoteFrontendPort $RemoteFrontendPort -RemoteApiPort $RemoteApiPort"
$action = New-ScheduledTaskAction -Execute (Get-Command powershell.exe).Source -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
# Independent fallback for a stopped task, including successful/control exits.
# IgnoreNew makes this a watchdog for a stopped task, never a periodic restart.
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($trigger, $watchdog) -Principal $principal -Settings $settings -Description 'Maintain the private connection to the hosted TopSignal backend and dashboard. Disable this task to keep it stopped.' -Force | Out-Null
if (-not $DoNotStart) { Start-ScheduledTask -TaskName $TaskName }
Write-Output "Private dashboard connection installed for this Windows user. Dashboard: http://127.0.0.1:$FrontendPort"
