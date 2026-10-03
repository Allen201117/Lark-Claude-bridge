#!/bin/bash
# 启动飞书↔Claude 桥接守护程序（macOS / Windows(Git Bash) 通用）
# Mac：caffeinate -s 运行期间不让 Mac 睡觉（不然你不在时没人干活）
# Windows：没有 caffeinate，由 bridge.py 自己调 SetThreadExecutionState 防睡眠
# 日志写到 ~/.feishu-claude-bridge/bridge.log
#
# 用法：start.sh            启动（前台一直跑，直到进程退出）
#       start.sh --check    只检查环境（找 python / claude / lark-cli、配置在不在），不连飞书

case "$(uname -s)" in
  Darwin)
    # launchd 给的 PATH 很干净，这里显式写全
    export PATH="$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin" ;;
  *)
    # Windows(Git Bash) / Linux：保留继承来的 PATH，覆盖掉会找不到 node / claude / python
    export PATH="$HOME/.npm-global/bin:$PATH"
    export PYTHONUTF8=1 ;;   # 日志、飞书消息都是 UTF-8，别按 Windows 的 GBK 编码
esac
cd "$(dirname "$0")"

LOGDIR="$HOME/.feishu-claude-bridge"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/bridge.log"

# python：Mac 用系统自带的 /usr/bin/python3（和以前一致）；没有就在 PATH 里找一个真能跑的
PY=/usr/bin/python3
if [ ! -x "$PY" ]; then
  PY=""
  for c in python3 python; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    # 试跑一下：Windows 的 Microsoft Store 占位 python 会在这一步露馅
    if "$p" -c "import sys" >/dev/null 2>&1; then PY="$p"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "[start] $(date) 找不到可用的 python3，没法启动" | tee -a "$LOG" >&2
  exit 1
fi

if [ "$1" = "--check" ]; then
  echo "python3  : $PY"
  exec "$PY" bridge.py --check
fi

echo "[start] $(date) 启动桥接…" >> "$LOG"
# Mac 上用 caffeinate 包一层；没有这个命令的系统（Windows）就直接跑
KEEP=""
command -v caffeinate >/dev/null 2>&1 && KEEP="caffeinate -s"
# 不用 exec：让有 FDA 的 bash 留作父进程，子进程(python/claude)才稳继承磁盘权限
# 绝对路径：Windows 的 start-bridge.ps1 靠命令行里的完整路径认出「本目录的」桥接，不误杀别的项目
$KEEP "$PY" "$(pwd)/bridge.py" >> "$LOG" 2>&1
