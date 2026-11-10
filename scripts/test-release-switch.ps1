$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'release-host.ps1') -FunctionsOnly

function Assert-Equal($Actual, $Expected) {
    if ($Actual -ne $Expected) { throw "Expected '$Expected', got '$Actual'" }
}
function Run-Case([string]$Failure, [string]$Expected, [bool]$ShouldThrow) {
    $script:events = New-Object 'System.Collections.Generic.List[string]'
    function Step([string]$Name) {
        $script:events.Add($Name)
        if ($Name -eq $Failure) { throw "injected $Name failure" }
    }
    $threw = $false
    try {
        Invoke-ReleaseSwitch -Preflight { Step 'gate' } -Stop { Step 'stop' } -PostStop { Step 'stopped-gate' } `
            -StartCandidate { Step 'start' } -VerifyCandidate { Step 'verify' } -CanRollback { Step 'rollback-gate' } `
            -StopCandidate { Step 'stop-new' } -RestorePrevious { Step 'restore' } -VerifyPrevious { Step 'verify-old' }
    } catch { $threw = $true }
    Assert-Equal $threw $ShouldThrow
    Assert-Equal ($script:events -join ',') $Expected
}
Run-Case '' 'gate,stop,stopped-gate,start,verify' $false
Run-Case 'gate' 'gate' $true
Run-Case 'stop' 'gate,stop' $true
Run-Case 'stopped-gate' 'gate,stop,stopped-gate,restore,verify-old' $true
Run-Case 'start' 'gate,stop,stopped-gate,start,rollback-gate,stop-new,restore,verify-old' $true
Run-Case 'verify' 'gate,stop,stopped-gate,start,verify,rollback-gate,stop-new,restore,verify-old' $true

# A candidate with exposure must keep managing it; a failed rollback gate must
# never reach StopCandidate or restore a different worker over the position.
$script:events = New-Object 'System.Collections.Generic.List[string]'
try {
    Invoke-ReleaseSwitch -Preflight {} -Stop {} -PostStop {} -StartCandidate {} -VerifyCandidate { throw 'unhealthy' } `
        -CanRollback { throw 'broker exposure' } -StopCandidate { $script:events.Add('unsafe-stop') } `
        -RestorePrevious { $script:events.Add('unsafe-restore') } -VerifyPrevious {}
    throw 'Expected rollback refusal'
} catch { if ($_.Exception.Message -ne 'broker exposure') { throw } }
Assert-Equal $script:events.Count 0

$scratch = Join-Path ([IO.Path]::GetTempPath()) ('ts-release-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
try {
    [IO.File]::WriteAllText((Join-Path $scratch 'STOP'), 'operator')
    try { Remove-OwnedStop $scratch 'release'; throw 'Expected ownership refusal' }
    catch { if ($_.Exception.Message -notlike 'STOP ownership changed*') { throw } }
    Assert-Equal ([IO.File]::ReadAllText((Join-Path $scratch 'STOP'))) 'operator'
    Remove-OwnedStop $scratch 'operator'
    Assert-Equal (Test-Path -LiteralPath (Join-Path $scratch 'STOP')) $false
} finally {
    $resolvedScratch = [IO.Path]::GetFullPath($scratch)
    [void](Assert-ChildPath ([IO.Path]::GetTempPath()) $resolvedScratch)
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
}
try { [void](Assert-ChildPath 'C:\release' 'C:\release-other\candidate'); throw 'Expected path refusal' }
catch { if ($_.Exception.Message -ne 'Release path escaped the configured root') { throw } }
Write-Output 'Release switch safety regressions passed.'
