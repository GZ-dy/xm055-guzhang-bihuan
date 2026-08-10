[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = (Get-Location).Path }
Set-Location $Root

$logPath = Join-Path $Root "dependency_install.log"
$transcriptStarted = $false
try {
    Start-Transcript -LiteralPath $logPath -Force | Out-Null
    $transcriptStarted = $true
} catch {}
trap {
    Write-Host "[FAILED] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Log: $logPath" -ForegroundColor Yellow
    if ($transcriptStarted) { try { Stop-Transcript | Out-Null } catch {} }
    exit 1
}

if ($env:OS -ne "Windows_NT") {
    throw "This installer only supports Windows."
}

$entryPoint = Join-Path $Root "run_closed_loop.py"
$requirements = Join-Path $Root "requirements-wecom.txt"
if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    throw "Please extract this dependency package into the fault-system workspace root, then run it again. Missing: run_closed_loop.py"
}
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "Missing dependency list: $requirements"
}

function Get-UsablePython {
    $candidates = @()
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $candidates += [pscustomobject]@{ Command = $pythonCommand.Source; Arguments = @() }
    }

    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyCommand) {
        $candidates += [pscustomobject]@{ Command = $pyCommand.Source; Arguments = @("-3.13") }
        $candidates += [pscustomobject]@{ Command = $pyCommand.Source; Arguments = @("-3") }
    }

    $localPrograms = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path -LiteralPath $localPrograms -PathType Container) {
        $localPythons = Get-ChildItem -LiteralPath $localPrograms -Filter python.exe -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending
        foreach ($item in $localPythons) {
            $candidates += [pscustomobject]@{ Command = $item.FullName; Arguments = @() }
        }
    }

    foreach ($candidate in $candidates) {
        try {
            $prefix = @($candidate.Arguments)
            $versionText = (& $candidate.Command @prefix -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))" 2>$null | Select-Object -Last 1)
            if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($versionText)) { continue }
            $version = [version]$versionText.Trim()
            if ($version -ge [version]"3.10") {
                return $candidate
            }
        } catch {
            continue
        }
    }
    return $null
}

Write-Host "[1/4] Checking Python 3.10+..." -ForegroundColor Cyan
$bootstrap = Get-UsablePython
if (-not $bootstrap) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    $pythonInstalled = $false
    if ($winget) {
        Write-Host "Python is missing. Installing Python 3.13 x64 with winget..." -ForegroundColor Yellow
        & $winget.Source install --id Python.Python.3.13 --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
        $pythonInstalled = ($LASTEXITCODE -eq 0)
        if (-not $pythonInstalled) {
            Write-Warning "winget failed; switching to the official Python installer."
        }
    }

    if (-not $pythonInstalled) {
        $pythonUrl = "https://www.python.org/ftp/python/3.13.14/python-3.13.14-amd64.exe"
        $pythonSha256 = "C54D9B9BBB8A36E6489363DDD01139707FD781D72F1F9E90C7EC65D0061368E0"
        $pythonInstaller = Join-Path $env:TEMP "python-3.13.14-amd64.exe"
        Write-Host "Downloading the official Python 3.13 x64 installer..." -ForegroundColor Yellow
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        try {
            try {
                Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonInstaller -UseBasicParsing
            } catch {
                $bits = Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue
                if (-not $bits) { throw }
                Start-BitsTransfer -Source $pythonUrl -Destination $pythonInstaller
            }
            $actualHash = (Get-FileHash -LiteralPath $pythonInstaller -Algorithm SHA256).Hash
            if ($actualHash -ne $pythonSha256) { throw "Python installer SHA256 verification failed." }
            $process = Start-Process -FilePath $pythonInstaller -ArgumentList @(
                "/quiet",
                "InstallAllUsers=0",
                "PrependPath=1",
                "Include_launcher=1",
                "Include_test=0",
                "SimpleInstall=1"
            ) -Wait -PassThru
            if ($process.ExitCode -notin @(0, 3010)) { throw "Official Python installer failed (exit code $($process.ExitCode))." }
        } finally {
            if (Test-Path -LiteralPath $pythonInstaller -PathType Leaf) {
                Remove-Item -LiteralPath $pythonInstaller -Force
            }
        }
    }

    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
    $bootstrap = Get-UsablePython
    if (-not $bootstrap) {
        throw "Python was installed but is not visible yet. Restart Windows, then run ONE_CLICK_NEW_PC.bat again."
    }
}

$bootstrapPrefix = @($bootstrap.Arguments)
$bootstrapVersion = (& $bootstrap.Command @bootstrapPrefix -c "import sys; print(sys.version.split()[0])" | Select-Object -Last 1)
Write-Host "Python ${bootstrapVersion}: $($bootstrap.Command)" -ForegroundColor Green

Write-Host "[2/4] Creating the project .venv..." -ForegroundColor Cyan
$venvRoot = Join-Path $Root ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$recreateVenv = $false
if (Test-Path -LiteralPath $venvRoot -PathType Container) {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $recreateVenv = $true
    } else {
        & $venvPython -c "import sys" 2>$null
        if ($LASTEXITCODE -ne 0) { $recreateVenv = $true }
    }
}
if ($recreateVenv) {
    Write-Host "Removing an invalid copied .venv; it will be rebuilt locally." -ForegroundColor Yellow
    Remove-Item -LiteralPath $venvRoot -Recurse -Force
}
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $bootstrap.Command @bootstrapPrefix -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "Failed to create $venvRoot" }
}

Write-Host "[3/4] Installing business and WeCom dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Host "[4/4] Verifying imports..." -ForegroundColor Cyan
$verifyCode = "import importlib; [importlib.import_module(name) for name in ('openpyxl','docx','PIL','pytesseract','cryptography','aibot')]; print('all dependency imports passed')"
& $venvPython -B -c $verifyCode
if ($LASTEXITCODE -ne 0) { throw "Dependency verification failed." }

Write-Host ""
Write-Host "[DONE] Dependencies are ready in .venv." -ForegroundColor Green
if (Test-Path -LiteralPath (Join-Path $Root "NEW_PC_SETUP.bat") -PathType Leaf) {
    Write-Host "Next: double-click NEW_PC_SETUP.bat to configure local credentials and bot autostart." -ForegroundColor Yellow
}
if ($transcriptStarted) { Stop-Transcript | Out-Null }
