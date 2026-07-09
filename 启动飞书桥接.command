#!/bin/bash
# 双击我就能启动「飞书↔Claude 桥接」。
# 在你自己的登录会话里跑，能正常访问 Documents 里的项目；启动后可关掉窗口。
export PATH="$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
cd "$(dirname "$0")" || exit 1

# 先停掉可能在跑的旧实例，避免两个抢消息
pkill -f "bridge.py" 2>/dev/null
sleep 1

mkdir -p "$HOME/.feishu-claude-bridge"
# caffeinate -s：接电源时不让 Mac 睡（离身也能远程干活）；nohup+detach：关窗口也不停
nohup caffeinate -s /usr/bin/python3 bridge.py >> "$HOME/.feishu-claude-bridge/bridge.log" 2>&1 </dev/null &

sleep 2
if pgrep -f "bridge.py" >/dev/null; then
  echo "✅ 飞书↔Claude 桥接已启动，现在可以关掉这个窗口了。"
  echo "   手机飞书给机器人发消息即可遥控。"
else
  echo "❌ 启动失败，看日志：~/.feishu-claude-bridge/bridge.log"
fi
