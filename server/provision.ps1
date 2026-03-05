#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Provision a fresh Windows Server VM for 9Bot cloud hosting.

.DESCRIPTION
    Installs all prerequisites (Python, Git, ADB, BlueStacks), clones 9Bot,
    sets up the VM Agent as a scheduled task, and configures firewall rules.

    Run this script via RDP or WinRM on a fresh Hetzner Windows Server 2022 VM.

.PARAMETER LicenseKey
    9Bot license key for all instances on this VM.

.PARAMETER AgentSecret
    Shared secret for VM Agent authentication.

.PARAMETER GitRepo
    Git repository URL for 9Bot. Default: https://github.com/nine/9Bot.git

.PARAMETER BotDir
    Installation directory for 9Bot. Default: C:\9Bot

.PARAMETER InstanceCount
    Number of BlueStacks instances to create. Default: 4

.EXAMPLE
    .\provision.ps1 -LicenseKey "abc123" -AgentSecret "secret123"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$LicenseKey,

    [Parameter(Mandatory=$true)]
    [string]$AgentSecret,

    [string]$GitRepo = "https://github.com/nine/9Bot.git",
    [string]$BotDir = "C:\9Bot",
    [int]$InstanceCount = 4
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([int]$Step, [int]$Total, [string]$Message)
    Write-Host "`n[$Step/$Total] $Message" -ForegroundColor Cyan
}

$totalSteps = 10
$step = 0

# ============================================================
# 1. Install Chocolatey
# ============================================================
$step++
Write-Step $step $totalSteps "Installing Chocolatey package manager"

if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    $env:PATH = "$env:PATH;$env:ALLUSERSPROFILE\chocolatey\bin"
} else {
    Write-Host "  Chocolatey already installed" -ForegroundColor Green
}

# ============================================================
# 2. Install Python, Git, OpenSSH
# ============================================================
$step++
Write-Step $step $totalSteps "Installing Python 3.11, Git, OpenSSH"

choco install python311 git openssh -y --no-progress
refreshenv

# Ensure Python is on PATH
$env:PATH = "$env:PATH;C:\Python311;C:\Python311\Scripts"

python --version
git --version

# ============================================================
# 3. Clone 9Bot repository
# ============================================================
$step++
Write-Step $step $totalSteps "Cloning 9Bot repository to $BotDir"

if (Test-Path $BotDir) {
    Write-Host "  Directory exists — pulling latest" -ForegroundColor Yellow
    Push-Location $BotDir
    git pull --ff-only
    Pop-Location
} else {
    git clone $GitRepo $BotDir
}

# ============================================================
# 4. Set up Python virtual environment + dependencies
# ============================================================
$step++
Write-Step $step $totalSteps "Setting up Python venv and dependencies"

Push-Location $BotDir

if (-not (Test-Path "venv")) {
    python -m venv venv
}

# Activate and install
& venv\Scripts\pip.exe install -r requirements.txt --quiet
& venv\Scripts\pip.exe install psutil --quiet

Pop-Location

# ============================================================
# 5. Pre-download EasyOCR models
# ============================================================
$step++
Write-Step $step $totalSteps "Pre-downloading EasyOCR models (this may take a few minutes)"

$warmupScript = @"
import sys
sys.path.insert(0, r'$BotDir')
try:
    import easyocr
    reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    print('EasyOCR models downloaded successfully')
except Exception as e:
    print(f'EasyOCR warmup failed: {e}')
"@

& "$BotDir\venv\Scripts\python.exe" -c $warmupScript

# ============================================================
# 6. Download and install BlueStacks 5
# ============================================================
$step++
Write-Step $step $totalSteps "Installing BlueStacks 5"

$bsInstaller = "$env:TEMP\BlueStacksInstaller.exe"
$bsUrl = "https://cdn3.bluestacks.com/downloads/windows/nxt/installer/FullInstaller/x64/BlueStacksMicroInstaller_5.21.580.1002_native_f722839c06cd95d5bc3c6a4ab1e490e2.exe"

if (-not (Test-Path "C:\Program Files\BlueStacks_nxt\HD-Player.exe")) {
    Write-Host "  Downloading BlueStacks..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $bsUrl -OutFile $bsInstaller -UseBasicParsing
    Start-Process -FilePath $bsInstaller -ArgumentList "--defaultImageName Nougat64 --imageToLaunch Nougat64" -Wait
    Write-Host "  BlueStacks installed" -ForegroundColor Green
} else {
    Write-Host "  BlueStacks already installed" -ForegroundColor Green
}

# ============================================================
# 7. Create BlueStacks instances
# ============================================================
$step++
Write-Step $step $totalSteps "Creating $InstanceCount BlueStacks instances"

$bsManager = "C:\Program Files\BlueStacks_nxt\HD-MultiInstanceManager.exe"

# Note: Instance creation is done via BlueStacks Multi-Instance Manager.
# The first instance (Nougat64) is created by the installer.
# Additional instances need to be created manually via the manager UI
# or by duplicating the instance config files.

Write-Host @"
  MANUAL STEP REQUIRED:
  1. Open BlueStacks Multi-Instance Manager
  2. Create $($InstanceCount - 1) additional instances (clone from Nougat64)
  3. Name them: Instance1, Instance2, Instance3, Instance4
  4. Set each to 1080x1920 portrait mode, 2 CPU cores, 3GB RAM
  5. Install the game on each instance

  After completing these steps, run this script again with -SkipInstall
  or continue with the remaining setup steps.
"@ -ForegroundColor Yellow

# ============================================================
# 8. Configure environment variables
# ============================================================
$step++
Write-Step $step $totalSteps "Setting system environment variables"

[System.Environment]::SetEnvironmentVariable("CLOUD_MODE", "1", "Machine")
[System.Environment]::SetEnvironmentVariable("NINEBOT_LICENSE_KEY", $LicenseKey, "Machine")
[System.Environment]::SetEnvironmentVariable("NINEBOT_AGENT_SECRET", $AgentSecret, "Machine")

# Also set for current session
$env:CLOUD_MODE = "1"
$env:NINEBOT_LICENSE_KEY = $LicenseKey
$env:NINEBOT_AGENT_SECRET = $AgentSecret

Write-Host "  Environment variables set" -ForegroundColor Green

# ============================================================
# 9. Configure firewall rules
# ============================================================
$step++
Write-Step $step $totalSteps "Configuring Windows Firewall"

# VM Agent port
New-NetFirewallRule -DisplayName "9Bot VM Agent" -Direction Inbound `
    -Protocol TCP -LocalPort 9090 -Action Allow -ErrorAction SilentlyContinue

# 9Bot dashboard ports (one per instance)
for ($i = 0; $i -lt $InstanceCount; $i++) {
    $port = 8081 + $i
    New-NetFirewallRule -DisplayName "9Bot Instance $($i+1)" -Direction Inbound `
        -Protocol TCP -LocalPort $port -Action Allow -ErrorAction SilentlyContinue
}

Write-Host "  Firewall rules configured (ports: 9090, 8081-$($8080 + $InstanceCount))" -ForegroundColor Green

# ============================================================
# 10. Set up VM Agent as scheduled task (auto-start on boot)
# ============================================================
$step++
Write-Step $step $totalSteps "Registering VM Agent as scheduled task"

$pythonExe = "$BotDir\venv\Scripts\python.exe"
$agentScript = "$BotDir\server\vm_agent.py"

# Remove existing task if present
Unregister-ScheduledTask -TaskName "9BotVMAgent" -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "-m server.vm_agent" -WorkingDirectory $BotDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "9BotVMAgent" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "9Bot VM Agent - manages BlueStacks instances"

# Start the agent now
Start-ScheduledTask -TaskName "9BotVMAgent"

Write-Host "  VM Agent registered and started" -ForegroundColor Green

# ============================================================
# Done
# ============================================================
Write-Host "`n" -NoNewline
Write-Host "============================================" -ForegroundColor Green
Write-Host "  9Bot VM provisioning complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host @"

  VM Agent: http://localhost:9090
  Bot Dir:  $BotDir

  REMAINING MANUAL STEPS:
  1. Create BlueStacks instances (see step 7 above)
  2. Install the game on each instance
  3. Log in to each game account
  4. Patch APK with Frida Gadget (optional):
     cd $BotDir
     venv\Scripts\python.exe -m protocol.patch_apk --device 127.0.0.1:5555 --install

  Test the agent:
     curl -H "Authorization: Bearer $AgentSecret" http://localhost:9090/health
     curl -H "Authorization: Bearer $AgentSecret" http://localhost:9090/instances

"@ -ForegroundColor White
