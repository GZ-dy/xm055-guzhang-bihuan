[CmdletBinding()]
param(
    [string]$BundlePath,
    [string]$TargetDir,
    [Parameter(Mandatory=$true)]
    [string]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
if ($ConfirmRestore -ne "RESTORE-PRIVATE") { throw "Pass -ConfirmRestore RESTORE-PRIVATE to restore credentials." }
if ($env:OS -ne "Windows_NT") { throw "Windows DPAPI is required." }

Add-Type -AssemblyName System.Security
function Get-Sha256Hex([byte[]]$Bytes) {
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha256.ComputeHash($Bytes))).Replace("-", "") }
    finally { $sha256.Dispose() }
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($BundlePath)) { $BundlePath = Join-Path $ProjectRoot "backup\private_credentials.dpapi.json" }
if ([string]::IsNullOrWhiteSpace($TargetDir)) { $TargetDir = Join-Path $ProjectRoot "config\private" }
$BundlePath = [IO.Path]::GetFullPath($BundlePath)
$TargetDir = [IO.Path]::GetFullPath($TargetDir)
$bundle = Get-Content -LiteralPath $BundlePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($bundle.schema_version -ne 1 -or $bundle.protection -ne "Windows DPAPI CurrentUser") {
    throw "Unsupported credential bundle."
}

$entropy = [Text.Encoding]::UTF8.GetBytes("fault-closed-loop-private-backup-v1")
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
foreach ($entry in $bundle.files) {
    $name = [string]$entry.relative_path
    if ([IO.Path]::GetFileName($name) -ne $name) { throw "Unsafe bundle path: $name" }
    $cipher = [Convert]::FromBase64String([string]$entry.ciphertext_base64)
    $plain = [Security.Cryptography.ProtectedData]::Unprotect(
        $cipher,
        $entropy,
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $actualHash = Get-Sha256Hex $plain
    if ($actualHash -ne [string]$entry.sha256) { throw "Hash mismatch for $name" }

    $target = Join-Path $TargetDir $name
    $temporary = "$target.tmp-$PID"
    [IO.File]::WriteAllBytes($temporary, $plain)
    Move-Item -LiteralPath $temporary -Destination $target -Force
    Write-Host "[OK] restored $name"
}
