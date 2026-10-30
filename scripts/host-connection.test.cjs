const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { promisify } = require('node:util');
const test = require('node:test');
const run = promisify(execFile);
const root = path.resolve(__dirname, '..');
const extract = `
$syntax = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $env:TOPSIGNAL_TEST_ROOT 'scripts/maintain-host-connection.ps1'), [ref]$null, [ref]$null)
foreach ($fn in $syntax.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $false)) { Invoke-Expression $fn.Extent.Text }
`;

async function powershell(t, source) {
  if (process.platform !== 'win32') { t.skip('Windows fixture'); return; }
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'topsignal-connection-test-'));
  try {
    const script = path.join(directory, 'fixture.ps1');
    fs.writeFileSync(script, `$ErrorActionPreference='Stop'\n${source}`);
    const result = await run('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script], {
      cwd: root, windowsHide: true, timeout: 20000,
      env: { ...process.env, TOPSIGNAL_TEST_ROOT: root, TOPSIGNAL_TEST_DIR: directory },
    });
    return result.stdout;
  } finally {
    assert.ok(directory.startsWith(path.join(os.tmpdir(), 'topsignal-connection-test-')));
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('connection scripts parse and retry delays stay bounded', async (t) => {
  await powershell(t, `${extract}
foreach ($file in @('maintain-host-connection.ps1','install-host-connection-task.ps1','host-connection-job.ps1','build-host-connection-launcher.ps1')) {
  $errors=$null
  [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $env:TOPSIGNAL_TEST_ROOT "scripts/$file"), [ref]$null, [ref]$errors) | Out-Null
  if ($errors.Count) { throw ($errors | Out-String) }
}
$RetryBaseSeconds=5; $RetryMaxSeconds=60
$delays=@(0..10 | ForEach-Object { Get-ReconnectDelay $_ })
if (($delays -join ',') -ne '5,10,20,40,60,60,60,60,60,60,60') { throw 'Unbounded backoff' }
$rejected=$false
try { Quote-ConnectionArgument 'bad"argument' } catch { $rejected=$true }
if (-not $rejected) { throw 'Ambiguous quoting accepted' }
`);
});

test('scheduled-task launcher starts PowerShell without a console and returns its exit code', async (t) => {
  await powershell(t, `
$launcher = & (Join-Path $env:TOPSIGNAL_TEST_ROOT 'scripts/build-host-connection-launcher.ps1') -OutputDirectory $env:TOPSIGNAL_TEST_DIR
$bytes = [IO.File]::ReadAllBytes($launcher)
$peOffset = [BitConverter]::ToInt32($bytes, 60)
if ([BitConverter]::ToUInt16($bytes, $peOffset + 24 + 68) -ne 2) { throw 'Launcher is not a Windows GUI executable' }
$fixture = Join-Path $env:TOPSIGNAL_TEST_DIR 'console fixture.ps1'
$result = Join-Path $env:TOPSIGNAL_TEST_DIR 'console-result.txt'
Set-Content -LiteralPath $fixture -Value @'
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class ConsoleProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
[IO.File]::WriteAllText((Join-Path $env:TOPSIGNAL_TEST_DIR 'console-result.txt'), [ConsoleProbe]::GetConsoleWindow().ToInt64().ToString())
exit 37
'@
$info = [Diagnostics.ProcessStartInfo]::new($launcher, ('-NoProfile -NonInteractive -File "' + $fixture + '"'))
$info.UseShellExecute = $false
# Do not suppress the outer process's console: the executable itself must do so.
$info.CreateNoWindow = $false
$child = [Diagnostics.Process]::Start($info)
try {
  if (-not $child.WaitForExit(10000)) { $child.Kill(); throw 'Launcher failed to exit with its child' }
  if ($child.ExitCode -ne 37) { throw 'Child exit code was lost' }
  if ((Get-Content -LiteralPath $result -Raw) -ne '0') { throw 'PowerShell acquired a console' }
} finally { $child.Dispose() }
`);
});

test('HTTP probes distinguish a responding failed service from no listener', async (t) => {
  const server = http.createServer((req, res) => { res.writeHead(req.url === '/health' ? 200 : 503); res.end('fixture'); });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  try {
    await powershell(t, `${extract}
if ((Get-EndpointStatus ${port} ${port} '/health') -ne 200) { throw 'Health probe failed' }
if ((Get-EndpointStatus ${port} ${port} '/failed') -ne 503) { throw 'HTTP failure was mistaken for transport failure' }
`);
  } finally { await new Promise((resolve) => server.close(resolve)); }
  await powershell(t, `${extract}
if ((Get-EndpointStatus ${port} ${port} '/health') -ne 0) { throw 'Closed port reported reachable' }
`);
});

test('SSH child helper drains stderr with rotation and kill-on-close releases child', async (t) => {
  await powershell(t, `
. (Join-Path $env:TOPSIGNAL_TEST_ROOT 'scripts/host-connection-job.ps1')
$log=Join-Path $env:TOPSIGNAL_TEST_DIR 'native.log'
$fixture=Join-Path $env:TOPSIGNAL_TEST_DIR 'native.ps1'
Set-Content $fixture 'for($i=0;$i -lt 650;$i++){[Console]::Error.WriteLine(("x"*2048))}; Start-Sleep -Seconds 30'
$child=[TopSignalSshProcess]::Start((Get-Command powershell.exe).Source, ('-NoProfile -NonInteractive -File "'+$fixture+'"'), $log)
$job=[TopSignalProcessJob]::CreateKillOnClose()
try {
  [TopSignalProcessJob]::Assign($job,$child.Handle)
  $deadline=(Get-Date).AddSeconds(8)
  while (-not (Test-Path "$log.previous") -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 20 }
  if (-not (Test-Path "$log.previous")) { throw 'Native stderr was not drained/rotated' }
  if ((Get-Item $log).Length -gt 1051000) { throw 'Native log was not bounded' }
} finally {
  [TopSignalProcessJob]::Close($job)
  if (-not $child.WaitForExit(2000)) { $child.Kill(); throw 'Job orphaned its child' }
  $child.Dispose()
}
`);
});
