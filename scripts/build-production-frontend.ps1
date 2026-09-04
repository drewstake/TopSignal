[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [int]$ApiPort = 8000
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($ApiPort -lt 1 -or $ApiPort -gt 65535) {
    throw "API port must be between 1 and 65535."
}

$resolvedRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$frontendDir = Join-Path $resolvedRoot "frontend"
$packageJson = Join-Path $frontendDir "package.json"
$lockFile = Join-Path $frontendDir "package-lock.json"
foreach ($requiredFile in @($packageJson, $lockFile)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required frontend build file was not found: $requiredFile"
    }
}

$npm = (Get-Command "npm.cmd" -ErrorAction Stop).Source
$apiVariable = Get-Item -LiteralPath "Env:VITE_API_BASE_URL" -ErrorAction SilentlyContinue
$hadApiVariable = $null -ne $apiVariable
$previousApiUrl = if ($hadApiVariable) { $apiVariable.Value } else { $null }
$demoVariable = Get-Item -LiteralPath "Env:VITE_DEMO_MODE" -ErrorAction SilentlyContinue
$hadDemoVariable = $null -ne $demoVariable
$previousDemoMode = if ($hadDemoVariable) { $demoVariable.Value } else { $null }

try {
    $env:VITE_API_BASE_URL = "http://127.0.0.1:$ApiPort"
    $env:VITE_DEMO_MODE = "false"
    & $npm --prefix $frontendDir run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend production build failed with exit code $LASTEXITCODE."
    }
}
finally {
    if ($hadApiVariable) {
        $env:VITE_API_BASE_URL = $previousApiUrl
    }
    else {
        Remove-Item -LiteralPath "Env:VITE_API_BASE_URL" -ErrorAction SilentlyContinue
    }
    if ($hadDemoVariable) {
        $env:VITE_DEMO_MODE = $previousDemoMode
    }
    else {
        Remove-Item -LiteralPath "Env:VITE_DEMO_MODE" -ErrorAction SilentlyContinue
    }
}

$index = Join-Path $frontendDir "dist\index.html"
if (-not (Test-Path -LiteralPath $index -PathType Leaf)) {
    throw "Frontend build completed without producing frontend/dist/index.html."
}
Write-Output "Built the loopback control UI for http://127.0.0.1:$ApiPort."
