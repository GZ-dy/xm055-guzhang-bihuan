[CmdletBinding()]
param(
    [string]$SourceDir,
    [string]$BundlePath
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Windows DPAPI is required." }

Add-Type -AssemblyName System.Security
function Get-Sha256Hex([byte[]]$Bytes) {
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha256.ComputeHash($Bytes))).Replace("-", "") }
    finally { $sha256.Dispose() }
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($SourceDir)) { $SourceDir = Join-Path $ProjectRoot "config\private" }
if ([string]::IsNullOrWhiteSpace($BundlePath)) { $BundlePath = Join-Path $ProjectRoot "backup\private_credentials.dpapi.json" }
$SourceDir = [IO.Path]::GetFullPath($SourceDir)
$BundlePath = [IO.Path]::GetFullPath($BundlePath)
$publicFiles = @("README.md", "feishu.template.json", "wecom_bot.env.template")
$sourceFiles = @(Get-ChildItem -LiteralPath $SourceDir -File | Where-Object { $_.Name -notin $publicFiles })
if ($sourceFiles.Count -eq 0) { throw "No private files found in $SourceDir" }

$entropy = [Text.Encoding]::UTF8.GetBytes("fault-closed-loop-private-backup-v1")
$entries = foreach ($file in $sourceFiles) {
    $plain = [IO.File]::ReadAllBytes($file.FullName)
    $cipher = [Security.Cryptography.ProtectedData]::Protect(
        $plain,
        $entropy,
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $roundTrip = [Security.Cryptography.ProtectedData]::Unprotect(
        $cipher,
        $entropy,
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $plainHash = Get-Sha256Hex $plain
    $roundTripHash = Get-Sha256Hex $roundTrip
    if ($plainHash -ne $roundTripHash) { throw "DPAPI verification failed for $($file.Name)" }

    [ordered]@{
        relative_path = $file.Name
        size = $plain.Length
        sha256 = $plainHash
        ciphertext_base64 = [Convert]::ToBase64String($cipher)
    }
}

$bundle = [ordered]@{
    schema_version = 1
    protection = "Windows DPAPI CurrentUser"
    portability = "same Windows user profile only"
    created_at = (Get-Date).ToString("o")
    source_computer = $env:COMPUTERNAME
    source_user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    files = $entries
}

$bundleDir = Split-Path -Parent $BundlePath
New-Item -ItemType Directory -Path $bundleDir -Force | Out-Null
$json = $bundle | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($BundlePath, $json, [Text.UTF8Encoding]::new($false))
Write-Host "[OK] encrypted $($entries.Count) private files -> $BundlePath"
Write-Host "[OK] DPAPI round-trip verification passed"
