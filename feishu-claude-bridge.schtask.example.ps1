# 开机（登录）自启示例（可选）——Windows 版的 feishu-claude-bridge.plist.example。
# 用前把下面的路径改成你自己的项目路径；这个文件只是示例，不会被桥接本身调用。
#
# 注册：  powershell -NoProfile -ExecutionPolicy Bypass -File .\feishu-claude-bridge.schtask.example.ps1
# 立刻跑： schtasks /Run /TN FeishuClaudeBridge
# 注销：  schtasks /Delete /TN FeishuClaudeBridge /F
# 说明：  任务在你登录后拉起 start-bridge.ps1 -Supervise（前台看守，桥接挂了 10 秒后自动重拉）。
#         Windows 没有 macOS 的「受保护目录 / 完全磁盘访问权限」问题，项目放哪都行。

$projectDir = "$HOME\diy\feishu-claude-bridge"   # <- 改成你的项目路径
$taskName   = 'FeishuClaudeBridge'

$action   = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$projectDir\start-bridge.ps1`" -Supervise" `
    -WorkingDirectory $projectDir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description '飞书↔Claude 桥接（登录后自启，挂了自动重拉）' -Force
