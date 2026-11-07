<#
.SYNOPSIS
    Tekla MCP Server 一键安装脚本（Windows / PowerShell 5.1+）

.DESCRIPTION
    1. 检查 Python 是否 64 位
    2. 在脚本所在目录创建 .venv 并安装依赖
    3. 自动探测 Tekla 安装路径
    4. 生成/合并 MCP 客户端配置（绝对路径，可直接用）
    5. 可选：跑一次连接诊断

.PARAMETER Client
    要写配置的客户端：workbuddy / claude / cline / none
    默认 workbuddy

.PARAMETER PythonExe
    指定 Python 解释器路径。不填则自动查找。

.PARAMETER SkipDiagnose
    跳过安装后的连接诊断。

.PARAMETER VenvPath
    复用已有的虚拟环境（其根目录下要有 Scripts\python.exe）。
    多份拷贝共用一套依赖时有用，省一份 80MB 的 venv。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -Client claude
#>

param(
    [ValidateSet('workbuddy', 'claude', 'cline', 'none')]
    [string]$Client = 'workbuddy',

    [string]$PythonExe = '',

    [switch]$SkipDiagnose,

    [string]$VenvPath = ''
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ServerPy = Join-Path $Root 'tekla_server.py'
if ($VenvPath -eq '') {
    $VenvDir = Join-Path $Root '.venv'
} else {
    $VenvDir = (Resolve-Path $VenvPath).Path
}
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'

function Write-Step($n, $text) {
    Write-Host ""
    Write-Host "[$n] $text" -ForegroundColor Cyan
}

function Write-Ok($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "    !!  $text" -ForegroundColor Yellow }
function Write-Err2($text) { Write-Host "    XX  $text" -ForegroundColor Red }

# ---------------------------------------------------------------- 1. Python
Write-Step 1 '检查 Python'

# 逐个实测候选解释器：能真正跑起来、且是 64 位的才收。
# 注意要排除 WindowsApps 下的微软商店假 python（执行只弹商店，返回码为 0）。
function Test-PyExe($exe) {
    if (-not $exe) { return $null }
    if ($exe -like '*\WindowsApps\*') { return $null }
    if (-not (Test-Path $exe)) { return $null }
    try {
        $o = & $exe -c "import sys,struct;print(sys.version_info[0], sys.version_info[1], struct.calcsize('P')*8)" 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0 -or -not $o) { return $null }
    $p = ($o -join ' ') -split '\s+'
    if ($p.Count -lt 3) { return $null }
    if (-not ($p[0] -match '^\d+$' -and $p[1] -match '^\d+$' -and $p[2] -match '^\d+$')) { return $null }
    return [pscustomobject]@{
        Exe   = $exe
        Major = [int]$p[0]
        Minor = [int]$p[1]
        Bits  = [int]$p[2]
    }
}

$cands = @()
if ($PythonExe -ne '') {
    $cands += $PythonExe
} else {
    foreach ($name in @('python', 'python3', 'py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) { $cands += $cmd.Source }
    }
    $roots = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python*",
        "C:\Python*",
        "$env:USERPROFILE\.workbuddy-ai\binaries\python\versions"
    )
    foreach ($r in $roots) {
        foreach ($d in (Get-ChildItem $r -Directory -ErrorAction SilentlyContinue)) {
            foreach ($sub in @('python.exe', 'bin\python.exe', 'Scripts\python.exe')) {
                $p = Join-Path $d.FullName $sub
                if (Test-Path $p) { $cands += $p }
            }
        }
    }
}

$valid = @()
foreach ($c in ($cands | Select-Object -Unique)) {
    $info = Test-PyExe $c
    if ($info) { $valid += $info }
}

if ($valid.Count -eq 0) {
    Write-Err2 '没找到可用的 Python。请先安装 64 位 Python 3.10+，或用 -PythonExe 指定路径。'
    Write-Err2 '下载：https://www.python.org/downloads/windows/ （选 Windows installer 64-bit）'
    exit 1
}

$py64 = $valid | Where-Object { $_.Bits -eq 64 } | Sort-Object Major, Minor -Descending
if ($py64.Count -eq 0) {
    $bad = $valid[0]
    Write-Err2 "找到的 Python 是 32 位（$($bad.Exe)），Tekla 是 64 位，位数必须一致。"
    exit 1
}
if ($PythonExe -eq '') { $PythonExe = $py64[0].Exe }
$pyVer = "$($py64[0].Major).$($py64[0].Minor)"

if ($py64[0].Major -lt 3 -or ($py64[0].Major -eq 3 -and $py64[0].Minor -lt 10)) {
    Write-Warn2 "Python $pyVer 偏旧，建议 3.10 以上"
}
Write-Ok "Python $pyVer (64 bit) -> $PythonExe"
Write-Ok "共找到 $($valid.Count) 个可用解释器"

# ---------------------------------------------------------------- 2. Tekla
Write-Step 2 '探测 Tekla 安装'

$teklaBin = ''
$found = @()
# 注意：循环变量不能叫 $root —— PowerShell 变量名不区分大小写，会覆盖 $Root
# Tekla 可能装在任意盘（本机就遇到过 C 盘搬到 D 盘），扫所有盘符的常见位置
foreach ($drv in [char[]](65..90)) {
    foreach ($sub in @('Program Files', 'Program Files (x86)')) {
        $tkRoot = "$($drv):\$sub\Tekla Structures"
        if (-not (Test-Path $tkRoot)) { continue }
        foreach ($d in (Get-ChildItem $tkRoot -Directory -ErrorAction SilentlyContinue)) {
            $bin = Join-Path $d.FullName 'bin'
            $net48 = Join-Path $bin 'Net48Runtime'
            if (Test-Path $net48) {
                $found += [pscustomobject]@{ Ver = $d.Name; Bin = $bin }
            }
        }
    }
}
if ($found.Count -gt 0) {
    $teklaBin = ($found | Sort-Object Ver -Descending | Select-Object -First 1).Bin
    Write-Ok "Tekla $($found.Count) 个版本，选用: $teklaBin"
} else {
    Write-Warn2 '没找到带 Net48Runtime 的 Tekla 安装。'
    Write-Warn2 '可以稍后设置环境变量 TEKLA_BIN 指向 <安装目录>\bin'
}

# ---------------------------------------------------------------- 3. venv
Write-Step 3 '创建虚拟环境并安装依赖'

$venvDir = $VenvDir
if (Test-Path $VenvPy) {
    Write-Ok "$venvDir 已存在，跳过创建"
} else {
    & $PythonExe -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Write-Err2 'venv 创建失败'; exit 1 }
    Write-Ok "已创建 $venvDir"
}

& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r (Join-Path $Root 'requirements.txt') --quiet
if ($LASTEXITCODE -ne 0) { Write-Err2 '依赖安装失败'; exit 1 }

$deps = & $VenvPy -c "from importlib.metadata import version as v;print('pythonnet',v('pythonnet'),'| mcp',v('mcp'),'| openpyxl',v('openpyxl'))"
Write-Ok $deps

# ---------------------------------------------------------------- 4. 配置
Write-Step 4 "生成 MCP 配置 (client=$Client)"

function New-TeklaEntry {
    $envMap = @{ PYTHONUNBUFFERED = '1'; PYTHONNET_RUNTIME = 'netfx'; TEKLA_MCP_LOG_LEVEL = 'INFO' }
    if ($teklaBin -ne '') { $envMap['TEKLA_BIN'] = $teklaBin }
    return [ordered]@{
        type    = 'stdio'
        command = $VenvPy
        args    = @($ServerPy)
        env     = $envMap
    }
}

function Format-JsonFile($path) {
    # PowerShell 5.1 的 ConvertTo-Json 排版很丑（多层缩进错位），
    # 交给同目录的 format_json.py 重新格式化成标准 2 空格缩进。
    $helper = Join-Path $Root 'format_json.py'
    if (-not (Test-Path $VenvPy) -or -not (Test-Path $helper)) { return }
    & $VenvPy $helper $path 2>$null
}

function Merge-Config($path, $extraFields) {
    $dir = Split-Path -Parent $path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    $cfg = $null
    if (Test-Path $path) {
        try {
            $raw = Get-Content $path -Raw -Encoding UTF8
            if ($raw.Trim() -ne '') { $cfg = $raw | ConvertFrom-Json }
        } catch {
            Write-Warn2 "原配置无法解析，已备份为 $path.bak"
            Copy-Item $path "$path.bak" -Force
            $cfg = $null
        }
    }

    if ($null -eq $cfg) {
        $cfg = New-Object psobject
    }
    if (-not ($cfg.PSObject.Properties.Name -contains 'mcpServers') -or $null -eq $cfg.mcpServers) {
        $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue (New-Object psobject) -Force
    }

    $entry = New-TeklaEntry
    foreach ($k in $extraFields.Keys) { $entry[$k] = $extraFields[$k] }

    $servers = $cfg.mcpServers
    $servers | Add-Member -NotePropertyName tekla -NotePropertyValue $entry -Force
    $cfg.mcpServers = $servers

    $json = $cfg | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
    Format-JsonFile $path
    Write-Ok "已写入 $path"
}

switch ($Client) {
    'workbuddy' {
        Merge-Config (Join-Path $env:USERPROFILE '.workbuddy-ai\mcp.json') @{}
        Write-Host '    提示：WorkBuddy 不会自动加载，需到「连接器管理 → 自定义连接器」点「信任」。' -ForegroundColor DarkGray
    }
    'claude' {
        Merge-Config (Join-Path $env:APPDATA 'Claude\claude_desktop_config.json') @{}
        Write-Host '    提示：改完要完全退出 Claude Desktop 再重开（托盘也要退）。' -ForegroundColor DarkGray
    }
    'cline' {
        $clinePath = Join-Path $env:APPDATA 'Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json'
        Merge-Config $clinePath @{ disabled = $false; autoApprove = @(); timeout = 60 }
        Write-Host "    提示：如果用的是 Cursor/其他 VS Code 分支，路径不同，请手动复制 config\cline.json。" -ForegroundColor DarkGray
    }
    'none' {
        $out = Join-Path $Root 'config\generated.json'
        $tmp = [ordered]@{ mcpServers = [ordered]@{ tekla = (New-TeklaEntry) } }
        $json = $tmp | ConvertTo-Json -Depth 12
        [System.IO.File]::WriteAllText($out, $json, (New-Object System.Text.UTF8Encoding($false)))
        Format-JsonFile $out
        Write-Ok "已生成 $out，自行复制到客户端配置。"
    }
}

# ---------------------------------------------------------------- 5. 诊断
if (-not $SkipDiagnose) {
    Write-Step 5 '连接诊断'
    Write-Host '    需要 Tekla 正在运行且已打开模型，否则这一步会失败（属正常）。' -ForegroundColor DarkGray
    & $VenvPy (Join-Path $Root 'diagnose.py')
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 '诊断未全部通过，看上面的输出定位问题。'
    }
}

Write-Host ""
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ' 安装完成' -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host " 解释器 : $VenvPy"
Write-Host " 服务端 : $ServerPy"
if ($teklaBin -ne '') { Write-Host " Tekla  : $teklaBin" }
Write-Host ''
Write-Host ' 以后排查环境：'
Write-Host "   `"$VenvPy`" `"$(Join-Path $Root 'diagnose.py')`""
Write-Host ''
