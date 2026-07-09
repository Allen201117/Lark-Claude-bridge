# 飞书 ↔ Claude Code 桥接

人不在电脑前，用手机飞书遥控你 Mac 上的 Claude Code 干活：飞书发指令 → 本地无头 `claude` 跑 → 干完把结果发回飞书 → 你回复 → 接着干。支持接续你已有的会话、桌面端任务完成通知、实时看进度、切换模型——全部说人话，不用记命令。

靠飞书机器人**长连接（websocket）**收发消息，**不需要公网 IP、不用买服务器**；驱动本地已装的 `claude`，用你自己的订阅，不额外产生 API 费用。

## 依赖

- [lark-cli](https://github.com/) 已安装并授权（`lark-cli auth status` 里 bot 为 ready）
- Claude Code CLI 已**单独登录**：`claude auth login --claudeai`（桌面端的登录态不会自动给到命令行，必须单独登一次；走订阅不额外花钱）
- Python 3、macOS

## 首次配置

```bash
cp config.example.json ~/.feishu-claude-bridge/config.json
```
编辑 `~/.feishu-claude-bridge/config.json`，填你自己的：
- `owner_open_id`：你的飞书 open_id（`lark-cli auth status` 里能查到，只有这个人发的消息才会被执行）
- `workdir`：默认工作目录（Claude 在这里干活，可在飞书用 `/cd` 切换）

配置文件在运行目录、不会进仓库。

## 启动 / 长期运行

**日常用（推荐）**：双击 `启动飞书桥接.command`（可拖到桌面）。它在你的登录会话里跑，能正常访问 `~/Documents` 里的项目，关窗口也不停，直到重启/关机。重启后再双击一次即可。

**开机零操作自启（可选，需一次性授权）**：项目若在受保护的 `~/Documents`，launchd 自启会被 macOS 隐私保护拦下（exit 126 / Operation not permitted）。用 `feishu-claude-bridge.plist.example` 做模板改好路径放进 `~/Library/LaunchAgents/`，再到「系统设置 → 隐私与安全性 → 完全磁盘访问权限」加入 `/bin/bash` 并打开。

日志在 `~/.feishu-claude-bridge/bridge.log`。

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

想让**桌面端**开的任务跑完也能通知飞书，把 `notify_hook.py` 注册成 Claude Code 的 Stop 钩子（`~/.claude/settings.json` 的 `hooks.Stop` 追加一条，`async:true`）。被「监控」的会话跑完一轮会自动发飞书。

## 注意

- **权限**：默认 `bypassPermissions`（Claude 能改文件、跑命令，才能真正远程干活）。已用 open_id 白名单锁死只有你本人能遥控；想更保守把 config 里改成 `acceptEdits`。
- **别接正在进行的超长会话**：接上会加载全量上下文，又慢又贵。已加单任务 15 分钟超时 + 心跳兜底。
- **别在电脑和手机同时敲同一个会话**，会互相写乱。

## 结构

- `bridge.py` — 守护程序（收飞书消息 → 跑 claude → 回飞书）
- `notify_hook.py` — 完成通知的 Stop 钩子
- `启动飞书桥接.command` — 双击启动器
- `config.example.json` / `feishu-claude-bridge.plist.example` — 配置与自启示例
