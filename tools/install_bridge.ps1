<#
    One-click installer for the remote Tekla bridge (no window).

    - Downloads a pinned source commit into %LOCALAPPDATA%\TeklaMCP-agent
    - Creates start-bridge.vbs (pythonw + relay polling, no console)
    - Drops a launcher into the user's Startup folder and starts it now
    - Kills any old bridge process first

    Usage:
      powershell -ExecutionPolicy Bypass -File install_bridge.ps1 -Token <token>

    This file and the generated VBS are ASCII-only: Windows PowerShell 5.1
    misreads UTF-8 without BOM as GBK and breaks quoting.
#>
param(
    [string]$Relay = "http://10.10.10.20:8765",
    [string]$Token = "",
    [string]$Python = "C:\Users\VS\AppData\Local\Programs\Python\Python315\python.exe",
    [string]$InstallDir = "$env:LOCALAPPDATA\TeklaMCP-agent",
    [string]$Ref = "ef4a5a6"
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
$url = "https://github.com/zjsjzj/TeklaMCP/archive/$Ref.zip"
Write-Host "Downloading $url"
Invoke-WebRequest $url -OutFile $zip
Expand-Archive $zip $InstallDir -Force

$src = Get-ChildItem $InstallDir -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName "tekla_server.py") } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $src) {
    throw "No extracted source with tekla_server.py under $InstallDir"
}
$server = Join-Path $src.FullName "tekla_server.py"
foreach ($marker in @("SetPrimaryObject", "_component_class_order")) {
    if (-not (Select-String -Path $server -Pattern $marker -Quiet)) {
        throw "Source is stale: missing $marker"
    }
}

$pythonw = Join-Path (Split-Path $Python) "pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $pythonw = $Python
}
$bridge = Join-Path $src.FullName "tools\tekla_remote_bridge.py"
if (-not (Test-Path $bridge)) {
    throw "Bridge script not found: $bridge"
}

Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*tekla_remote_bridge.py*" -or $_.CommandLine -like "*bridge-watchdog.vbs*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

# Watchdog VBS: relaunch the hidden bridge whenever it exits (self-healing).
# bWaitOnReturn=True blocks until pythonw exits; on crash it sleeps 3s and restarts.
$vbs = Join-Path $InstallDir "bridge-watchdog.vbs"
$vbsContent = @(
    'Set sh = CreateObject("WScript.Shell")',
    'Do',
    ('  sh.Run """{0}"" ""{1}"" --token {2} --relay {3}", 0, True' -f $pythonw, $bridge, $Token, $Relay),
    '  WScript.Sleep 3000',
    'Loop'
)
Set-Content -Path $vbs -Value $vbsContent -Encoding ASCII

# Permanent autostart: watchdog runs at every logon and keeps the bridge alive.
$startup = [Environment]::GetFolderPath("Startup")
Copy-Item -Path $vbs -Destination (Join-Path $startup "TeklaMCPBridge.vbs") -Force

Start-Process -FilePath "wscript.exe" -ArgumentList ('"{0}"' -f $vbs)
Start-Sleep -Seconds 4
$alive = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*tekla_remote_bridge.py*" })

Write-Host ""
Write-Host "Installed: $($src.FullName)"
Write-Host "Watchdog (self-healing): $vbs"
Write-Host "Autostart (every logon): $(Join-Path $startup 'TeklaMCPBridge.vbs')"
Write-Host "Bridge processes alive: $($alive.Count)"
Write-Host "Stop now: Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*tekla_remote_bridge.py*' -or $_.CommandLine -like '*bridge-watchdog.vbs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
Write-Host "Remove autostart: Remove-Item \"$(Join-Path $startup 'TeklaMCPBridge.vbs')\""
