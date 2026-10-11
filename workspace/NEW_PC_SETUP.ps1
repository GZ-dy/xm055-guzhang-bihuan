[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

if ($env:OS -ne "Windows_NT") { throw "This setup only supports Windows." }

function Read-RequiredValue {
    param([Parameter(Mandatory=$true)][string]$Prompt)
    $value = (Read-Host $Prompt).Trim()
    if ([string]::IsNullOrWhiteSpace($value)) { throw "$Prompt cannot be empty." }
    return $value
}

if ($SkipDependencyInstall) {
    Write-Host "[1/3] Project Python environment already prepared." -ForegroundColor Green
} else {
    Write-Host "[1/3] Creating the project Python environment..." -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "install_env.ps1")
    if ($LASTEXITCODE -ne 0) { throw "install_env.ps1 failed with exit code $LASTEXITCODE" }
}

$privateDir = Join-Path $Root "config\private"
$templatePath = Join-Path $privateDir "feishu.template.json"
$configPath = Join-Path $privateDir "feishu.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    Write-Host "[2/3] Creating the local Feishu configuration..." -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $templatePath)) { throw "Missing $templatePath" }
    $config = Get-Content -LiteralPath $templatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $config.app_id = Read-RequiredValue "Feishu App ID"
    $secureSecret = Read-Host "Feishu App Secret (stored only in local feishu.json)" -AsSecureString
    $secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
    try {
        if ([Runtime.InteropServices.Marshal]::ReadInt16($secretPtr) -eq 0) { throw "Feishu App Secret cannot be empty." }
        $config.app_secret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
        $config.app_token = Read-RequiredValue "Feishu App Token"
        $config.table_id = Read-RequiredValue "Feishu Table ID"
        $config.view_id = Read-RequiredValue "Feishu View ID"
        $config.table_url = Read-RequiredValue "Feishu table URL"
        $config | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $configPath -Encoding UTF8
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
        Remove-Variable config, secureSecret -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "[2/3] Existing local Feishu configuration kept." -ForegroundColor Green
}

Write-Host "[3/3] Saving WeCom/SMTP credentials and starting the live bot..." -ForegroundColor Cyan
& (Join-Path $Root "install_wecom_bot_autostart.ps1")

Start-Sleep -Seconds 6
$logPath = Join-Path $Root ("work\bot\wecom_bot_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
if (Test-Path -LiteralPath $logPath) {
    Get-Content -LiteralPath $logPath -Tail 20
}
Write-Host "[DONE] New-PC setup completed." -ForegroundColor Green
Write-Host "Expected log markers: connected, authenticated, heartbeat ack." -ForegroundColor Green
