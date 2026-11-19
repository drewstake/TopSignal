[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RepoRoot,
    [Parameter(Mandatory)][PSCredential]$Credential,
    [string]$SecUserAgent = '',
    [switch]$StartNow
)
$ErrorActionPreference = 'Stop'
$taskName = 'TopSignal Scanner'
$release = (Resolve-Path -LiteralPath $RepoRoot).Path
$backend = Join-Path $release 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$private = Join-Path $env:ProgramData 'TopSignal\scanner-private'
$config = Join-Path $private 'config.json'
$service = Get-LocalUser -Name 'TopSignalSvc'
$principal = "$env:COMPUTERNAME\TopSignalSvc"
if (!$service.Enabled -or $Credential.UserName -notin @($principal,'TopSignalSvc')) { throw 'Limited TopSignalSvc credential required' }
if (Get-LocalGroupMember -SID S-1-5-32-544 | Where-Object SID -eq $service.SID) { throw 'Administrator service identity refused' }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Scanner task already exists' }
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Immutable release Python required' }
$production = Get-ScheduledTask -TaskName 'TopSignal Personal Device'
$active = [regex]::Match($production.Actions.Arguments,'(?i)-RepoRoot\s+"([^"]+)"').Groups[1].Value
if (!$active -or (Resolve-Path -LiteralPath $active).Path -ne $release) { throw 'Install from the verified active release' }
if (Test-Path -LiteralPath $private) { throw 'Private scanner directory exists; inspect before a new installation' }
New-Item -ItemType Directory -Path $private | Out-Null
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
$acl.SetOwner([Security.Principal.SecurityIdentifier]'S-1-5-32-544')
foreach ($sid in @('S-1-5-18','S-1-5-32-544',$service.SID.Value)) {
    $rights = if ($sid -eq $service.SID.Value) {'ReadAndExecute'} else {'FullControl'}
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(([Security.Principal.SecurityIdentifier]$sid),$rights,'ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $private -AclObject $acl
& $python -B (Join-Path $backend 'tools\provision_scanner.py') --runtime-env (Join-Path $backend '.env') --config $config --sec-user-agent $SecUserAgent
if ($LASTEXITCODE -ne 0) { throw 'Local scanner provisioning failed' }
$arguments = '-B -m app.scanner.worker --config "' + $config + '"'
$action = New-ScheduledTaskAction -Execute $python -WorkingDirectory $backend -Argument $arguments
$boot = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = 'PT1M'
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -Priority 7 -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
$identity = New-ScheduledTaskPrincipal -UserId $principal -LogonType Password -RunLevel Limited
$definition = New-ScheduledTask -Action $action -Trigger @($boot,$watchdog) -Principal $identity -Settings $settings -Description 'Independent public headline and completed equity bar scanner'
Register-ScheduledTask -TaskName $taskName -InputObject $definition -User $principal -Password $Credential.GetNetworkCredential().Password | Out-Null
if ($StartNow) { Start-ScheduledTask -TaskName $taskName }
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
