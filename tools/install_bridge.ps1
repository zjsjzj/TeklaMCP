<#
    One-click installer for the remote Tekla bridge.

    Downloads the latest source into %LOCALAPPDATA%\TeklaMCP-agent,
    writes start-bridge.ps1 (token/relay, log to bridge.log),
    registers a logon task "TeklaMCPBridge" and starts it.

    Usage:
      powershell -ExecutionPolicy Bypass -File install_bridge.ps1 -Token <token>

    Keep this file ASCII-only: Windows PowerShell 5.1 misparses UTF-8
    without BOM (Chinese text becomes GBK garbage and breaks quoting).
#>
param(
    [string]$Relay = "http://10.10.10.20:8765",
    [string]$Token = "",
    [string]$Python = "C:\Users\VS\AppData\Local\Programs\Python\Python315\python.exe",
    [string]$InstallDir = "$env:LOCALAPPDATA\TeklaMCP-agent",
    [string]$Branch = "feat/modeling-tools"
)

$ErrorActionPreference = "Stop"

if (-not $Token) {
    throw "Token is required: -Token <token>"
}
if (-not (Test-Path $Python)) {
    throw "Python not found: $Python (pass -Python <path>)"
}
New-Item -ItemType Directory -Force $InstallDir | Out-Null

$zip = Join-Path $InstallDir "source.zip"
$url = "https://github.com/zjsjzj/TeklaMCP/archive/refs/heads/$Branch.zip"
Write-Host "Downloading $url"
Invoke-WebRequest $url -OutFile $zip
Expand-Archive $zip $InstallDir -Force

$src = Get-ChildItem $InstallDir -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$server = Join-Path $src.FullName "tekla_server.py"
if (-not (Test-Path $server)) {
    throw "tekla_server.py not found under $($src.FullName)"
}
if (-not (Select-String -Path $server -Pattern "_component_class_order" -Quiet)) {
    throw "Downloaded source is stale (missing _component_class_order); check network/branch"
}

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*tekla_remote_bridge.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$startScript = Join-Path $InstallDir "start-bridge.ps1"
$content = @(
    '$ErrorActionPreference = "Continue"',
    ('& "{0}" "{1}" --token {2} --relay {3} *>> "{4}"' -f $Python, (Join-Path $src.FullName "tools\tekla_remote_bridge.py"), $Token, $Relay, (Join-Path $InstallDir "bridge.log"))
)
Set-Content -Path $startScript -Value $content -Encoding UTF8

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $startScript)
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

try {
    Register-ScheduledTask -TaskName "TeklaMCPBridge" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName "TeklaMCPBridge"
    Write-Host "Registered logon task TeklaMCPBridge and started it."
} catch {
    Write-Warning "Register-ScheduledTask failed (admin rights?): $($_.Exception.Message)"
    Write-Host "Starting a visible window instead; later run: $startScript"
    Start-Process powershell -ArgumentList @("-ExecutionPolicy", "Bypass", "-File", $startScript)
}

Write-Host ""
Write-Host "Installed: $($src.FullName)"
Write-Host "Log: $InstallDir\bridge.log"
Write-Host "Stop: Stop-ScheduledTask -TaskName TeklaMCPBridge"
Write-Host "Remove: Unregister-ScheduledTask -TaskName TeklaMCPBridge -Confirm:$false"
