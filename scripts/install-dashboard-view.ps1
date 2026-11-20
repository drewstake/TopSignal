# One-time handoff for the existing local Study View. Run only for that topology.
[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:LOCALAPPDATA 'TopSignal\imbalance-view-20261117'
$target = Join-Path $dir 'serve_imbalance_dashboard.py'
$source = Join-Path $PSScriptRoot '..\backend\tools\serve_imbalance_dashboard.py'
$task = Get-ScheduledTask -TaskName 'TopSignal Study View'
if ($task.Actions.Count -ne 1 -or !$task.Actions[0].Arguments.Contains($target)) { throw 'Unexpected local viewing task' }
$request = [Net.HttpWebRequest]::Create('http://127.0.0.1:4176/bot')
$request.Host = '127.0.0.1:4173'
$request.Proxy = $null
$request.GetResponse().Close()
if (!$PSCmdlet.ShouldProcess($target, 'Forward the viewing page to peteamdpc and restart Study View')) { return }
if ((Get-FileHash $source).Hash -eq (Get-FileHash $target).Hash) { return }
$backup = "$target.before-dashboard-$([guid]::NewGuid().ToString('N'))"
$temp = "$backup.new"
Copy-Item -LiteralPath $source -Destination $temp
[IO.File]::Replace($temp, $target, $backup)
try {
    Stop-ScheduledTask -InputObject $task
    Start-ScheduledTask -InputObject $task
    for ($i=0; $i -lt 30; $i++) {
        try {
            $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:4176/bot')
            $request.Host = '127.0.0.1:4173'
            $request.Proxy = $null
            $upstream = $request.GetResponse()
            try { $a = [IO.StreamReader]::new($upstream.GetResponseStream()).ReadToEnd() } finally { $upstream.Close() }
            $b = (Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:4173/bot' -TimeoutSec 4).Content
            if ($a -ceq $b) { Write-Output "Viewing page verified. Prior viewer saved at $backup"; return }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    throw 'Local viewing page did not converge'
} catch {
    Copy-Item -LiteralPath $backup -Destination $target -Force
    Stop-ScheduledTask -InputObject $task
    Start-ScheduledTask -InputObject $task
    throw
}
