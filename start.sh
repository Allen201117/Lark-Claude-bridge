#!/bin/bash
# 启动飞书↔Claude 桥接守护程序
# caffeinate -s：运行期间不让 Mac 睡觉（不然你不在时没人干活）
# 日志写到 ~/.feishu-claude-bridge/bridge.log

export PATH="$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
cd "$(dirname "$0")"

LOGDIR="$HOME/.feishu-claude-bridge"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/bridge.log"

echo "[start] $(date) 启动桥接…" >> "$LOG"
# 不用 exec：让有 FDA 的 bash 留作父进程，子进程(python/claude)才稳继承磁盘权限
caffeinate -s /usr/bin/python3 bridge.py >> "$LOG" 2>&1
