# 飞书 ↔ Claude Code 桥接

人不在电脑前，用手机飞书遥控你 Mac（或 Windows）上的 Claude Code 干活：飞书发指令 → 本地无头 `claude` 跑 → 干完把结果发回飞书 → 你回复 → 接着干。支持接续你已有的会话、桌面端任务完成通知、实时看进度、切换模型——全部说人话，不用记命令。

靠飞书机器人**长连接（websocket）**收发消息，**不需要公网 IP、不用买服务器**；驱动本地已装的 `claude`，用你自己的订阅，不额外产生 API 费用。

## 依赖

- [lark-cli](https://github.com/) 已安装并授权（`lark-cli auth status` 里 bot 为 ready）
- Claude Code CLI 已**单独登录**：`claude auth login --claudeai`（桌面端的登录态不会自动给到命令行，必须单独登一次；走订阅不额外花钱）
- Python 3、macOS 或 Windows 10/11（Windows 还需要 Git for Windows，`start.sh` 靠它自带的 bash 跑；npm 装的 `claude`、`lark-cli` 要在 PATH 里）

## 首次配置

```bash
cp config.example.json ~/.feishu-claude-bridge/config.json
```
Windows（Git Bash 里）同样的命令；PowerShell 里是 `Copy-Item config.example.json $HOME\.feishu-claude-bridge\config.json`。
编辑 `~/.feishu-claude-bridge/config.json`，填你自己的：
- `owner_open_id`：你的飞书 open_id（`lark-cli auth status` 里能查到，只有这个人发的消息才会被执行）
- `workdir`：默认工作目录（Claude 在这里干活，可在飞书用 `/cd` 切换）
- Windows 的路径写成 `C:/Users/you/diy`（正斜杠，JSON 里不用转义），`allowed_roots` 同理；注意默认白名单是 `~/Desktop` 和 `~/Documents`，项目放在别处（比如 `~/diy`）要自己加进 `allowed_roots`

配置文件在运行目录、不会进仓库。

## 启动 / 长期运行

**日常用（推荐）**：双击 `启动飞书桥接.command`（可拖到桌面）。它在你的登录会话里跑，能正常访问 `~/Documents` 里的项目，关窗口也不停，直到重启/关机。重启后再双击一次即可。

**开机零操作自启（可选，需一次性授权）**：项目若在受保护的 `~/Documents`，launchd 自启会被 macOS 隐私保护拦下（exit 126 / Operation not permitted）。用 `feishu-claude-bridge.plist.example` 做模板改好路径放进 `~/Library/LaunchAgents/`，再到「系统设置 → 隐私与安全性 → 完全磁盘访问权限」加入 `/bin/bash` 并打开。

日志在 `~/.feishu-claude-bridge/bridge.log`。

**Windows**（不用 .cmd / .bat，用 PowerShell 脚本 + 任务计划程序）：

```powershell
# 先自检：找得到 python / claude / lark-cli 吗、配置在不在（不连飞书）
bash start.sh --check            # 在 Git Bash 里；或：python bridge.py --check

# 日常用：后台启动（先停旧实例），关窗口也不停；对应 Mac 的「启动飞书桥接.command」
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-bridge.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-bridge.ps1 -Stop    # 停掉

# 登录后自启 + 挂了自动重拉（对应 launchd 的 KeepAlive）：改好路径后跑一次示例脚本
powershell -NoProfile -ExecutionPolicy Bypass -File .\feishu-claude-bridge.schtask.example.ps1
```

Windows 上和 Mac 的几处差别：没有 caffeinate，`bridge.py` 启动后自己告诉系统「别因空闲睡眠」（`SetThreadExecutionState`）；`claude` / `lark-cli` 是 npm 的 `.cmd` 包装脚本，`platform_util.py` 会解析出它们真正指向的 `.exe` / `.js` 再启动——不经 `cmd.exe`，否则飞书消息里的换行会把参数截断、`& | " %` 会被当命令执行；解析不出来就拒绝启动，不会退回 `cmd.exe`。

## 手机飞书里怎么用

给你的机器人发消息即可，都能说人话：

| 你想干嘛 | 直接说 |
|---|---|
| 派个活 | 任意文字指令；再回复 = 继续同一个会话 |
| 监控桌面端任务、跑完通知我 | 「监控当前会话」 |
| 看任务跑到哪了 | 「进度」「在干嘛」 |
| 接着以前的会话干 | 「接上我聊 XX 的会话」（带「上次聊到哪」预览） |
| 换模型 | 「换成 opus」「用 haiku」 |

命令速查：`/watch` 监控 · `/progress` 进度 · `/sessions` 列会话 · `/resume` 接会话 · `/context` 看上下文 · `/model` 换模型 · `/cd` 切目录 · `/new` 新会话 · `/help`。

## 完成通知（可选）

想让**桌面端**开的任务跑完也能通知飞书，把 `notify_hook.py` 注册成 Claude Code 的 Stop 钩子（`~/.claude/settings.json` 的 `hooks.Stop` 追加一条，`async:true`）。Windows 上 command 写成 `python3 "$HOME/diy/feishu-claude-bridge/notify_hook.py"`（Claude 在 Git Bash 里执行钩子，`$HOME` 可用）。被「监控」的会话跑完一轮会自动发飞书。

## 注意

- **权限（默认收敛，要跑命令得先提权）**：
  威胁模型说清楚——`open_id` 白名单只有「飞书账号」一个因子，账号被盗就等于把整台 Mac 的 shell 交出去。所以默认不再全放开：
  - 平时跑 `acceptEdits`：**能改文件，跑命令被挡**。
  - 要跑命令：飞书发 `/arm <口令>` 临时提权（默认 60 分钟，到点自动降回，进程重启也一律降权）；用完 `/disarm` 立即收回；`/security` 看当前状态。
  - 口令是**第二因子**——光偷到飞书账号跑不了命令。先在 config 里设 `arm_passphrase`，不设 `/arm` 不可用。
  - ⚠️ 老配置里写死的 `permission_mode: "bypassPermissions"` 会被**自动收敛**为 `acceptEdits` 并在启动日志告警。
- **目录白名单**：`allowed_roots` 限定 `/cd` 和启动目录的范围，默认只放开 `~/Desktop` 和 `~/Documents`，把 `~/.ssh` `~/.aws` `~/.claude` `~/Library` 挡在外面（软链穿越也会被解析后拦下）。
- **高危指令不走远程通道**：递归删除 / sudo / 下载即执行 / 读 SSH 与凭据 / `.env` / force push 这类指令直接拒绝，让你自己坐到电脑前做。判据在 `bridge.py` 的 `DANGER_PATTERNS`，误伤了自己改。
- **别接正在进行的超长会话**：接上会加载全量上下文，又慢又贵。已加单任务 15 分钟超时 + 心跳兜底。
- **别在电脑和手机同时敲同一个会话**，会互相写乱。

## 结构

- `bridge.py` — 守护程序（收飞书消息 → 跑 claude → 回飞书）
- `notify_hook.py` — 完成通知的 Stop 钩子
- `platform_util.py` — 跨平台小工具（Windows 下解析 `.cmd` 包装脚本、杀进程树、防睡眠；Mac 上原样透传）
- `启动飞书桥接.command` — macOS 双击启动器
- `start-bridge.ps1` — Windows 启动 / 停止 / 看守脚本
- `config.example.json` / `feishu-claude-bridge.plist.example`（macOS）/ `feishu-claude-bridge.schtask.example.ps1`（Windows）— 配置与自启示例
