#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书↔Claude 桥接 · Stop 钩子
被监控的会话每跑完一轮 → 把「最后说了啥」发飞书通知你（一次性，通知后自动取消监控）。

注册在 ~/.claude/settings.json 的 hooks.Stop 里，用 async:true + 短 timeout，
全程 try/except 兜底 + 永远 exit 0，坏了也绝不影响你正常用 Claude。
"""
import sys
import os
import json
import subprocess

BASE = os.path.expanduser("~/.feishu-claude-bridge")
WATCH = os.path.join(BASE, "watch.json")

def _owner():
    try:
        return json.load(open(os.path.join(BASE, "config.json"))).get("owner_open_id", "") \
            or os.environ.get("FCB_OWNER_OPEN_ID", "")
    except Exception:
        return os.environ.get("FCB_OWNER_OPEN_ID", "")

def _lark():
    import shutil
    p = os.path.expanduser("~/.npm-global/bin/lark-cli")
    return p if os.path.exists(p) else (shutil.which("lark-cli") or p)

OWNER = _owner()
LARK = _lark()


def last_assistant_text(tpath):
    """读 transcript，返回最后一条助手的文字消息。"""
    txt = ""
    try:
        with open(tpath, "r") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "assistant":
                    c = o.get("message", {}).get("content", [])
                    if isinstance(c, list):
                        for b in c:
                            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                                txt = b["text"].strip()
    except Exception:
        pass
    return txt


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    sid = data.get("session_id")
    tpath = data.get("transcript_path")
    if not sid or not os.path.exists(WATCH):
        return
    try:
        watch = json.load(open(WATCH))
    except Exception:
        return
    if sid not in watch:
        return

    # 一次性：通知后从监控列表移除，避免每轮都刷屏
    try:
        json.dump([w for w in watch if w != sid], open(WATCH, "w"))
    except Exception:
        pass

    msg = last_assistant_text(tpath) if tpath else ""
    preview = (msg[:1500] + " …（省略）") if len(msg) > 1500 else (msg or "(本轮没有文字输出)")
    text = (f"🔔 你监控的会话跑完一轮了！\n会话 {sid}\n\n最后说：\n{preview}\n\n"
            f"（想接着这个会话聊，在这直接发：接上会话 {sid[:8]}）")
    try:
        subprocess.run([LARK, "im", "+messages-send", "--as", "bot",
                        "--user-id", OWNER, "--text", text],
                       capture_output=True, timeout=25)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
