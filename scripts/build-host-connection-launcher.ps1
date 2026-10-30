[CmdletBinding()]
param(
    [string]$SourcePath = (Join-Path $PSScriptRoot 'host-connection-launcher.cs'),
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourcePath).Path
$output = (Resolve-Path -LiteralPath $OutputDirectory).Path
$hasher = [Security.Cryptography.SHA256]::Create()
try { $hash = ([BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($source)))).Replace('-', '').Substring(0,16).ToLowerInvariant() }
finally { $hasher.Dispose() }
# Versioned names allow repair while the previous launcher is still running.
$executable = Join-Path $output "host-connection-launcher-$hash.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    $compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not (Test-Path -LiteralPath $compiler)) {
        $compiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
    }
    $temporary = Join-Path $output ("host-connection-build-" + [guid]::NewGuid().ToString('N') + '.exe')
    $info = [Diagnostics.ProcessStartInfo]::new($compiler, ('/nologo /target:winexe /optimize+ /out:"{0}" "{1}"' -f $temporary, $source))
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $compilerProcess = [Diagnostics.Process]::Start($info)
    try {
        $stdout = $compilerProcess.StandardOutput.ReadToEndAsync()
        $stderr = $compilerProcess.StandardError.ReadToEndAsync()
        $compilerProcess.WaitForExit()
        if ($compilerProcess.ExitCode -ne 0) { throw "Launcher compilation failed: $($stdout.Result) $($stderr.Result)" }
        Move-Item -LiteralPath $temporary -Destination $executable -Force
    }
    finally { $compilerProcess.Dispose() }
}
Write-Output $executable
