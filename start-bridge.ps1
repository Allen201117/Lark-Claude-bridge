# 飞书↔Claude 桥接 · Windows 启动器（对应 Mac 上的「启动飞书桥接.command」）
#
# 用法（在 PowerShell 里）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\start-bridge.ps1              后台启动（先停掉旧实例），关窗口也不停
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\start-bridge.ps1 -Stop        停掉桥接
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\start-bridge.ps1 -Supervise   前台看守：桥接退出后 10 秒自动重拉
#                                                                                       （给任务计划程序用，对应 launchd 的 KeepAlive）
# 只检查环境、不启动：  bash start.sh --check     （Git Bash 里跑）
#
# 前置：Git for Windows（自带 bash.exe，start.sh 靠它跑）、Python 3、npm 装好的 claude 和 lark-cli。
# 日志：~\.feishu-claude-bridge\bridge.log（和 Mac 一样）。
param([switch]$Stop, [switch]$Supervise)

$ErrorActionPreference = 'Stop'
$dir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $HOME '.feishu-claude-bridge'
$log    = Join-Path $logDir 'bridge.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Find-GitBash {
    $cands = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
        if ($base) { $cands += (Join-Path $base 'Git\bin\bash.exe') }
    }
    $cands += (Join-Path $HOME 'scoop\apps\git\current\bin\bash.exe')
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) { $cands += (Join-Path (Split-Path (Split-Path $git.Source)) 'bin\bash.exe') }
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    throw '找不到 Git for Windows 的 bash.exe，请先装 Git for Windows。'
}

function Get-BridgeProcs {
    # 旧实例：看守者（另一个 start-bridge.ps1 -Supervise，不先杀它会把桥接重新拉起来）、
    # 包着桥接的 bash start.sh、python 跑着的 bridge.py。看守者排最前，先杀。
    $all = Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and (
            ($_.Name -match '^(powershell|pwsh)\.exe$' -and $_.CommandLine -match 'start-bridge\.ps1') -or
            ($_.Name -eq 'bash.exe' -and $_.CommandLine -match 'start\.sh') -or
            ($_.Name -match '^pythonw?\d*\.exe$' -and $_.CommandLine -match 'bridge\.py'))
    }
    $all | Sort-Object { if ($_.Name -match 'powershell|pwsh') { 0 } elseif ($_.Name -eq 'bash.exe') { 1 } else { 2 } }
}

function Stop-Bridge {
    foreach ($p in @(Get-BridgeProcs)) {
        # /T 连子进程（claude、lark-cli）一起带走，免得留下孤儿抢消息
        & taskkill /PID $p.ProcessId /T /F *> $null
    }
}

if ($Stop) {
    Stop-Bridge
    Write-Host '桥接已停止。'
    return
}

$bash = Find-GitBash
Stop-Bridge   # 先停旧的，避免两个实例抢同一批消息

if ($Supervise) {
    while ($true) {
        $p = Start-Process -FilePath $bash -ArgumentList 'start.sh' -WorkingDirectory $dir -WindowStyle Hidden -PassThru -Wait
        Add-Content -Path $log -Encoding UTF8 -Value "[supervise] $(Get-Date) start.sh 退出（exit=$($p.ExitCode)），10 秒后重启"
        Start-Sleep -Seconds 10
    }
}

Start-Process -FilePath $bash -ArgumentList 'start.sh' -WorkingDirectory $dir -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 3
if (@(Get-BridgeProcs | Where-Object { $_.CommandLine -match 'bridge\.py' }).Count -gt 0) {
    Write-Host '飞书↔Claude 桥接已启动，现在可以关掉这个窗口了。'
    Write-Host '手机飞书给机器人发消息即可遥控。'
} else {
    Write-Host "启动失败，看日志：$log"
    exit 1
}
