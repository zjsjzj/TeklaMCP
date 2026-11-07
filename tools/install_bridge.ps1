<#
    一键安装「远程 Tekla 桥」到当前 Windows 用户：
      - 下载最新源码到 %LOCALAPPDATA%\TeklaMCP-agent
      - 生成 start-bridge.ps1（带 token/relay、日志写 bridge.log）
      - 注册登录自启任务 TeklaMCPBridge 并立即启动

    用法（在任意 PowerShell 里）：
      powershell -ExecutionPolicy Bypass -File install_bridge.ps1
#>
param(
    [string]$Relay = "http://10.10.10.20:8765",
    [string]$Token = "xsPb5E2d-GVixSap2-9T4vHx",
    [string]$Python = "C:\Users\VS\AppData\Local\Programs\Python\Python315\python.exe",
    [string]$InstallDir = "$env:LOCALAPPDATA\TeklaMCP-agent",
    [string]$Branch = "feat/modeling-tools"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Python)) {
    throw "找不到 Python：$Python；用 -Python <路径> 指定装好 pythonnet/mcp 的解释器"
}
New-Item -ItemType Directory -Force $InstallDir | Out-Null

$zip = Join-Path $InstallDir "source.zip"
$url = "https://github.com/zjsjzj/TeklaMCP/archive/refs/heads/$Branch.zip"
Write-Host "下载 $url"
Invoke-WebRequest $url -OutFile $zip
Expand-Archive $zip $InstallDir -Force

$src = Get-ChildItem $InstallDir -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not (Test-Path (Join-Path $src.FullName "tekla_server.py"))) {
    throw "解压目录里没有 tekla_server.py：$($src.FullName)"
}
if (-not (Select-String -Path (Join-Path $src.FullName "tekla_server.py") -Pattern "_component_class_order" -Quiet)) {
    throw "源码不是最新版（缺少 _component_class_order），请检查网络/分支"
}

# 只保留一个桥：先结束旧进程
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*tekla_remote_bridge.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$startScript = Join-Path $InstallDir "start-bridge.ps1"
$content = @"
`$ErrorActionPreference = "Continue"
& "$Python" "$($src.FullName)\tools\tekla_remote_bridge.py" --token $Token --relay $Relay *>> "$InstallDir\bridge.log"
"@
Set-Content -Path $startScript -Value $content -Encoding UTF8

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ('-WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $startScript)
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
try {
    Register-ScheduledTask -TaskName "TeklaMCPBridge" -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName "TeklaMCPBridge"
    Write-Host "已注册登录自启任务 TeklaMCPBridge，并已启动。"
} catch {
    Write-Warning "注册计划任务失败（可能需要管理员）：$($_.Exception.Message)"
    Write-Host "改为直接启动一个可见窗口；以后可手动运行：$startScript"
    Start-Process powershell -ArgumentList @(
        "-ExecutionPolicy", "Bypass", "-File", $startScript)
}

Write-Host ""
Write-Host "安装完成：$($src.FullName)"
Write-Host "日志：$InstallDir\bridge.log"
Write-Host "停止：Stop-ScheduledTask -TaskName TeklaMCPBridge"
Write-Host "卸载：Unregister-ScheduledTask -TaskName TeklaMCPBridge -Confirm:`$false"
