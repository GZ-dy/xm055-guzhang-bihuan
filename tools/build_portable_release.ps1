param(
    [string]$Stamp = (Get-Date -Format "yyyyMMdd_HHmmss"),
    [switch]$IncludePrivateConfig,
    [switch]$IncludeWheelhouse
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Dist = Join-Path $Root "dist"
$PackageName = "fault_closed_loop_v1_$Stamp"
$OutDir = Join-Path $Dist $PackageName
$ZipPath = Join-Path $Dist "$PackageName.zip"

if (Test-Path -LiteralPath $OutDir) {
    throw "Output already exists: $OutDir"
}
if (Test-Path -LiteralPath $ZipPath) {
    throw "Zip already exists: $ZipPath"
}

New-Item -ItemType Directory -Force -Path $Dist, $OutDir | Out-Null

function Copy-IfExists {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Warning "Skipping optional package item (not present): $Source"
        return
    }
    $parent = Split-Path -Parent $Destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Copy-Existing {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Required package item missing: $Source"
    }
    $parent = Split-Path -Parent $Destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

# AGENTS.md 第 2 节"包内必备清单" 11 项承诺。这里是清单的唯一权威来源：
# 改了这里，必须同步改 AGENTS.md 第 2 节。
$required = @("run_closed_loop.py", "AGENTS.md", "requirements.txt", "requirements-wecom.txt", "README.md",
              "src", "data", "docs", "scripts", "tests", "tools")
foreach ($item in $required) {
    Copy-Existing (Join-Path $Root $item) (Join-Path $OutDir $item)
}

# Hermes webhook is a workspace integration helper, not part of the standalone V1 package.
$hermesWebhook = Join-Path $OutDir "scripts\post_to_hermes_webhook.py"
if (Test-Path -LiteralPath $hermesWebhook) {
    Remove-Item -LiteralPath $hermesWebhook -Force
}

$portableIndexPath = Join-Path $OutDir "data\reference\manuals_index\manual_index.json"
$portableIndex = Get-Content -LiteralPath $portableIndexPath -Raw -Encoding UTF8 | ConvertFrom-Json
$portableIndex.project_root = "data\reference\manuals"
$portableIndex.manual_dir = "data\reference\manuals\处理手册"
$portableIndex | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $portableIndexPath -Encoding UTF8

# Keep source-side index backups for maintenance, but do not ship them in the V1 package.
$portableIndexDir = Split-Path -Parent $portableIndexPath
Get-ChildItem -LiteralPath $portableIndexDir -File | Where-Object {
    $_.Name -like "*.bak" -or
    $_.Name -like "*.bak-*" -or
    $_.Name -like "*.tmp" -or
    $_.Name -like "*.temp" -or
    $_.Name -like "*~"
} | Remove-Item -Force

# Contacts are runtime data; maintenance backups are not delivery inputs.
$portableContactsDir = Join-Path $OutDir "data\reference\contacts"
if (Test-Path -LiteralPath $portableContactsDir) {
    Get-ChildItem -LiteralPath $portableContactsDir -File | Where-Object {
        $_.Name -like "*.bak" -or $_.Name -like "*.bak-*" -or $_.Name -like "*.tmp" -or $_.Name -like "*~"
    } | Remove-Item -Force
}

# Historical ledger snapshots are maintenance evidence, not runtime inputs.
$portableLedgerSnapshots = Join-Path $OutDir "data\reference\ledger_snapshots"
if (Test-Path -LiteralPath $portableLedgerSnapshots) {
    Remove-Item -LiteralPath $portableLedgerSnapshots -Recurse -Force
}

# optional: wheelhouse/ 只在断网无 PyPI 时入包，需要显式 -IncludeWheelhouse
if ($IncludeWheelhouse) {
    Copy-IfExists (Join-Path $Root "wheelhouse") (Join-Path $OutDir "wheelhouse")
}

# optional: feishu.template.json 是脱敏模板（不含密钥），默认入包
Copy-IfExists (Join-Path $Root "config/private/feishu.template.json") (Join-Path $OutDir "config/private/feishu.template.json")
Copy-IfExists (Join-Path $Root "config/private/wecom_bot.env.template") (Join-Path $OutDir "config/private/wecom_bot.env.template")

# optional: 安装/验收/demo，以及企业微信当前值班入口。
$optionalScripts = @(
    "install_env.ps1", "install_env.bat",
    "validate_package.ps1", "validate_package.bat",
    "run_local_demo.ps1", "run_local_demo.bat",
    "start_wecom_ai_bot_dry_run.ps1", "start_wecom_ai_bot_dry_run.bat",
    "start_wecom_ai_bot_live.ps1", "install_wecom_bot_autostart.ps1",
    "setup_env.ps1", "verify_env.ps1", "diagnose_bot.ps1", "stop_bot.ps1",
    "INSTALL_DEPENDENCIES.ps1", "INSTALL_DEPENDENCIES.bat",
    "NEW_PC_SETUP.ps1", "NEW_PC_SETUP.bat",
    "START_LOCAL_DEMO.cmd", "ONE_CLICK_NEW_PC.bat", "RUN_KEEP_OPEN.cmd", "README_DEPENDENCIES.md"
)
foreach ($s in $optionalScripts) {
    Copy-IfExists (Join-Path $Root $s) (Join-Path $OutDir $s)
}

# optional: render_docx.py（视觉 QA / LibreOffice wrapper，只在项目根放了的版本里入包）
Copy-IfExists (Join-Path $Root "render_docx.py") (Join-Path $OutDir "render_docx.py")

New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "work"), (Join-Path $OutDir "outputs"), (Join-Path $OutDir "config") | Out-Null
Set-Content -LiteralPath (Join-Path $OutDir "work\.gitkeep") -Encoding ASCII -Value ""
Set-Content -LiteralPath (Join-Path $OutDir "outputs\.gitkeep") -Encoding ASCII -Value ""
Set-Content -LiteralPath (Join-Path $OutDir "config\.gitkeep") -Encoding ASCII -Value ""

if ($IncludePrivateConfig) {
    Copy-Existing (Join-Path $Root "config\private\feishu.json") (Join-Path $OutDir "config\private\feishu.json")
}

$readme = @'
# 故障闭环系统 V1 便携包

本包包含当前 V1 运行代码、模板、处置手册、Windows 安装脚本和本地验收脚本。完整文件清单见 `PACKAGE_MANIFEST.txt`，详细使用说明见 `README.md`。

## 普通授权用户：安全体验

1. 完整解压 ZIP，不要在 ZIP 预览窗口中运行。
2. 双击 `START_LOCAL_DEMO.cmd`。
3. 看到 `[DONE] Safe local demo passed.` 后，打开 `outputs` 目录查看 Word 报告、Excel 台账、手册匹配和邮件预览。

首次安装 Python 和依赖需要联网。上述入口不会配置生产凭据、不会启动 live 机器人、不会写入飞书、不会发送邮件。

## 包含与排除边界

- 包含：`run_closed_loop.py`、`src/`、`data/`、`docs/`、`scripts/`、`tests/`、`tools/`、脱敏配置模板和 Windows 交付脚本。
- 默认排除：`.venv`、真实飞书配置、企业微信/SMTP 凭据、DPAPI 文件、历史 `outputs/` 和 `work/` 数据。
- `wheelhouse/` 只在构建时显式使用 `-IncludeWheelhouse` 才入包；默认发布包不是断网安装包。

## 生产切换警告

`RUN_KEEP_OPEN.cmd`、`ONE_CLICK_NEW_PC.bat`、`NEW_PC_SETUP.*` 会配置本机真实凭据、创建登录自启动并启动 live 机器人。它们不是普通试用入口。只能在旧机器人已停止、切换已获批且按 `docs/HANDOVER_OPERATIONS.md` 核对完成后使用。
'@
Set-Content -LiteralPath (Join-Path $OutDir "README_PORTABLE.md") -Encoding UTF8 -Value $readme

Get-ChildItem -LiteralPath $OutDir -Force -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @("__pycache__", ".pytest_cache") } |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $OutDir -Force -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in @(".pyc", ".pyo") -or $_.Name.StartsWith("~$") } |
    Remove-Item -Force
Get-ChildItem -LiteralPath $OutDir -Force -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -like "*.bak" -or $_.Name -like "*.bak-*" -or
        $_.Name -like "*.backup" -or $_.Name -like "*.backup-*" -or
        $_.Name -like "*.tmp" -or $_.Name -like "*.temp" -or $_.Name -like "*~"
    } |
    Remove-Item -Force

$manifestPath = Join-Path $OutDir "PACKAGE_MANIFEST.txt"
Get-ChildItem -LiteralPath $OutDir -Force -Recurse -File |
    ForEach-Object {
        $relative = $_.FullName.Substring($OutDir.Length + 1)
        "{0}`t{1}" -f $_.Length, $relative
    } |
    Sort-Object |
    Set-Content -LiteralPath $manifestPath -Encoding UTF8

Compress-Archive -Path (Join-Path $OutDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal
$hash = Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256

[pscustomobject]@{
    PackageDirectory = $OutDir
    ZipPath = $ZipPath
    Sha256 = $hash.Hash
}
