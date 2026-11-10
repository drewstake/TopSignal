$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'release-host.ps1') -FunctionsOnly
. (Join-Path $PSScriptRoot 'install-windows-startup-task.ps1') -FunctionsOnly

function Assert-Equal($Actual, $Expected) {
    if ($Actual -ne $Expected) { throw "Expected '$Expected', got '$Actual'" }
}
Assert-Equal (Test-PrivateEnvironmentFile '.env.example') $false
Assert-Equal (Test-PrivateEnvironmentFile '.env') $true
Assert-Equal (Test-PrivateEnvironmentFile '.env.production.local') $true
Assert-Equal (Test-PrivateEnvironmentFile 'main.py') $false

# Exercise real SID descriptors, including an identity Windows cannot resolve.
& {
    $service = 'S-1-5-19'
    $unknown = 'S-1-5-21-111111-222222-333333-4444'
    $script:testAcl = New-Object Security.AccessControl.FileSecurity
    $script:testAcl.SetSecurityDescriptorSddlForm("O:BAG:BAD:P(A;;FR;;;$service)(A;;FR;;;$unknown)")
    function Get-Acl { param($LiteralPath) return $script:testAcl }
    $access = Get-AllowRightsBySid 'fixture'
    Assert-Equal $access.Rights.ContainsKey($unknown) $true
    try { Assert-PrivateDataAcl 'fixture' $service -Access $access; throw 'Expected private ACL refusal' }
    catch { if ($_.Exception.Message -notlike 'Private data ACL*') { throw } }
    try { Assert-RequiredRights 'fixture' $service ReadAndExecute -ForbidWrite -Access $access; throw 'Expected RX refusal' }
    catch { if ($_.Exception.Message -notlike "ACL for*does not grant*") { throw } }
    $script:testAcl.SetSecurityDescriptorSddlForm("O:BAG:BAD:P(A;;FR;;;$service)(A;;FW;;;BU)")
    try { Assert-NoBroadWrite 'fixture'; throw 'Expected broad write refusal' }
    catch { if ($_.Exception.Message -notlike 'ACL for*grants write access*') { throw } }
    $script:testAcl.SetSecurityDescriptorSddlForm("O:$service`G:BAD:P(A;;FRFX;;;$service)")
    try { Assert-RequiredRights 'fixture' $service ReadAndExecute -ForbidWrite; throw 'Expected owner refusal' }
    catch { if ($_.Exception.Message -notlike 'The service identity owns*') { throw } }
}

# Verify descriptors are independent for consecutive files and directories.
& {
    function Get-Item { param($LiteralPath) return [pscustomobject]@{Name='root';FullName='fixture-root';PSIsContainer=$true;Attributes=0} }
    function Get-ChildItem { param($LiteralPath,[switch]$Force,[switch]$Recurse)
        foreach ($name in @('first.py','second.py','.env','.env.production.local','.env.example')) {
            [pscustomobject]@{Name=$name;FullName=('fixture-root\'+$name);PSIsContainer=$false;Attributes=0}
        }
    }
    $script:writtenAcls = @{}
    $script:writeOrder = @()
    function Set-Acl { param($LiteralPath,$AclObject)
        $script:writtenAcls[(Split-Path -Leaf $LiteralPath)] = $AclObject
        $script:writeOrder += $LiteralPath
    }
    Set-ReleaseAcl 'fixture-root' 'S-1-5-19'
    Assert-Equal $script:writtenAcls.Count 6
    Assert-Equal $script:writeOrder[-1] 'fixture-root'
    Assert-Equal ([object]::ReferenceEquals($script:writtenAcls['first.py'], $script:writtenAcls['second.py'])) $false
    foreach ($name in $script:writtenAcls.Keys) {
        $acl = $script:writtenAcls[$name]
        Assert-Equal $acl.AreAccessRulesProtected $true
        Assert-Equal $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value 'S-1-5-32-544'
        $rules = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
        Assert-Equal $rules.Count 3
        $serviceRule = $rules | Where-Object { $_.IdentityReference.Value -eq 'S-1-5-19' }
        $execute = [long][Security.AccessControl.FileSystemRights]::ExecuteFile
        Assert-Equal (([long]$serviceRule.FileSystemRights -band $execute) -ne 0) (-not (Test-PrivateEnvironmentFile $name))
    }
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
