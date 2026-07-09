#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书 ↔ Claude Code 桥接守护程序（路 B 轻量版）

做的事：
  1. 用 `lark-cli event consume im.message.receive_v1` 长连接监听你发给机器人的飞书消息（无需公网 IP）。
  2. 收到消息 → 当作给 Claude 的指令，跑无头 `claude -p`（可 --resume 续你已有的 session）。
  3. Claude 干完 → 把结果发回飞书（这就是「任务完成通知」）。
  4. 你在飞书回复 → 继续跑同一个 session（这就是「回复继续」）。

安全：只处理 OWNER_OPEN_ID 本人发来的消息，别人发的一律忽略。
"""

import difflib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ─────────────────────────── 配置 ───────────────────────────
# 个人配置放 ~/.feishu-claude-bridge/config.json（不进仓库）。参见 config.example.json。
CONFIG_FILE = Path.home() / ".feishu-claude-bridge" / "config.json"

def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}

_cfg = load_config()

# 只有这个人发的消息才会被执行（= 你自己）。防止别人遥控你的电脑。
OWNER_OPEN_ID = os.environ.get("FCB_OWNER_OPEN_ID") or _cfg.get("owner_open_id") or ""

# Claude 权限模式。远程无人值守要真正干活（改文件/跑命令）就得放开权限。
#   bypassPermissions = 全放开（能改文件、能跑命令）。方便但有风险：
#   任何能用你飞书账号给机器人发消息的人，都能在你 Mac 上跑命令。已用 OWNER 白名单兜底。
#   也可改成 "acceptEdits"（只自动批准改文件，跑命令仍会被挡）。
PERMISSION_MODE = _cfg.get("permission_mode", "bypassPermissions")

# 状态文件：记住「当前工作目录」和「当前 session」，重启后不丢。
STATE_FILE = Path.home() / ".feishu-claude-bridge" / "state.json"

# 默认工作目录（可用 /cd 切换）
DEFAULT_WORKDIR = _cfg.get("workdir") or str(Path.home())

# Claude session 存档目录（用于 /sessions 列出你桌面端的历史会话）
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# 飞书单条文字消息分块大小（太长会发失败，分段发）
CHUNK = 3000

# 监听的事件
EVENT_KEY = "im.message.receive_v1"

LOG_DIR = Path.home() / ".feishu-claude-bridge"
LOG_DIR.mkdir(parents=True, exist_ok=True)
CONSUME_ERR_LOG = LOG_DIR / "consume.stderr.log"

# ─────────────────────────── 状态 ───────────────────────────
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"workdir": DEFAULT_WORKDIR, "session_id": None, "last_chat_id": None}

def save_state(st):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2))

state = load_state()

# ─────────────────────────── 飞书发消息 ───────────────────────────
def send_feishu(text, chat_id=None):
    """把文字发回飞书。优先发到来消息的 chat_id；没有就发到 OWNER 的私聊。"""
    if not text:
        return
    # 分块，避免超长发送失败
    parts = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [text]
    for idx, part in enumerate(parts):
        prefix = f"[{idx+1}/{len(parts)}] " if len(parts) > 1 else ""
        cmd = ["lark-cli", "im", "+messages-send", "--as", "bot", "--text", prefix + part]
        if chat_id:
            cmd += ["--chat-id", chat_id]
        else:
            cmd += ["--user-id", OWNER_OPEN_ID]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as e:
            print(f"[bridge] 发消息失败: {e}", file=sys.stderr)

# ─────────────────────────── 跑 Claude ───────────────────────────
# 单个任务的安全阀：最长跑多久（秒）超时就中止；每隔多久报一次「还在跑」
CLAUDE_TIMEOUT = 900   # 15 分钟
HEARTBEAT = 90         # 90 秒一次心跳

def run_claude(prompt, session_id, workdir, chat_id, fork=False, model=None):
    """
    无头跑 Claude。返回 (result_text, new_session_id, is_error)。
    有 session_id 就 --resume 续聊；没有就新开一个。
    fork=True：--fork-session，在原会话的副本上继续（用于接历史会话，避免和桌面端打架）。
    model：opus/sonnet/haiku/fable 或完整模型名；None=用默认。
    带超时 + 心跳：防跑飞；长任务也让你知道它还活着，不假死。
    """
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--permission-mode", PERMISSION_MODE]
    if model:
        cmd += ["--model", model]
    if session_id:
        cmd += ["--resume", session_id]
        if fork:
            cmd += ["--fork-session"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=workdir)
    except Exception as e:
        return (f"❌ 启动 Claude 失败：{e}", session_id, True)

    start = last_beat = time.time()
    out, err = "", ""
    while True:
        try:
            out, err = proc.communicate(timeout=10)
            break
        except subprocess.TimeoutExpired:
            now = time.time()
            if now - start > CLAUDE_TIMEOUT:
                proc.kill()
                try:
                    proc.communicate(timeout=10)
                except Exception:
                    pass
                return (f"⏱️ 跑了超过 {CLAUDE_TIMEOUT // 60} 分钟还没完成，已自动中止。\n"
                        f"（多半是接的历史会话上下文太大。换个更聚焦的会话，或发 /new 开新的。）",
                        session_id, True)
            if now - last_beat >= HEARTBEAT:
                send_feishu(f"⏳ 还在跑…（已 {int(now - start)} 秒，最多 {CLAUDE_TIMEOUT // 60} 分钟）", chat_id)
                last_beat = now

    out = (out or "").strip()
    if not out:
        return (f"❌ Claude 没有输出。stderr:\n{(err or '')[:1000]}", session_id, True)
    try:
        d = json.loads(out)
    except Exception:
        # 不是 JSON，直接把原始输出返回
        return (out[:CHUNK * 3], session_id, False)

    result = str(d.get("result", "")).strip()
    new_sid = d.get("session_id") or session_id
    is_err = bool(d.get("is_error"))
    cost = d.get("total_cost_usd")
    dur = d.get("duration_ms")
    footer = ""
    if cost is not None or dur is not None:
        bits = []
        if dur is not None:
            bits.append(f"{dur/1000:.0f}s")
        if cost is not None:
            bits.append(f"${cost:.4f}")
        footer = "\n\n— " + " · ".join(bits)
    return (result + footer, new_sid, is_err)

# ─────────────────────────── 列出历史 session ───────────────────────────
def encode_project_path(workdir):
    """~/.claude/projects 下的目录名规则：把路径里的 / 换成 -。"""
    return str(workdir).replace("/", "-")

def list_sessions(workdir, limit=10):
    """列出当前工作目录对应的历史 session（就是你桌面端存过的会话）。"""
    pdir = CLAUDE_PROJECTS / encode_project_path(workdir)
    if not pdir.exists():
        return []
    files = sorted(pdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        sid = f.stem
        mtime = time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime))
        snippet = first_user_message(f)
        out.append((sid, mtime, snippet))
    return out

def first_user_message(jsonl_path):
    """读 session 文件里第一条用户消息，做个摘要，帮你认出是哪个会话。"""
    try:
        with open(jsonl_path, "r") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") == "user":
                    msg = obj.get("message", {})
                    content = msg.get("content")
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                text = c.get("text", "")
                                break
                    text = re.sub(r"\s+", " ", text).strip()
                    if text and not text.startswith("<"):
                        return text[:50]
    except Exception:
        pass
    return "(无摘要)"

def find_transcript(sid):
    """在所有项目目录里找 <sid>.jsonl（会话可能在别的项目下）。"""
    hits = list(CLAUDE_PROJECTS.glob(f"*/{sid}.jsonl"))
    return hits[0] if hits else None

def last_assistant_text(tpath):
    """transcript 里最后一条助手的文字消息。"""
    txt = ""
    try:
        with open(tpath) as fh:
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

def last_exchange(tpath):
    """返回 (最后一条用户消息, 最后一条助手消息)，用于「上次聊到哪」预览。"""
    lu = la = ""
    try:
        with open(tpath) as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                t = o.get("type")
                if t == "user":
                    c = o.get("message", {}).get("content")
                    s = ""
                    if isinstance(c, str):
                        s = c
                    elif isinstance(c, list):
                        for b in c:
                            if isinstance(b, dict) and b.get("type") == "text":
                                s = b.get("text", ""); break
                    s = re.sub(r"\s+", " ", s).strip()
                    if s and not s.startswith("<"):
                        lu = s
                elif t == "assistant":
                    c = o.get("message", {}).get("content", [])
                    if isinstance(c, list):
                        for b in c:
                            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                                la = b["text"].strip()
    except Exception:
        pass
    return lu, la

def _norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()

def search_sessions(workdir, query, limit=60):
    """按 query 在历史会话摘要里模糊搜索，返回 [(sid, mtime, snip, score)] 按相似度降序。"""
    pdir = CLAUDE_PROJECTS / encode_project_path(workdir)
    if not pdir.exists():
        return []
    files = sorted(pdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    q = _norm(query)
    scored = []
    for f in files[:limit]:
        snip = first_user_message(f)
        s = _norm(snip)
        if not s:
            continue
        if q and (q in s or s in q):
            score = 1.0
        elif q and ((s[:10] and s[:10] in q) or (q[:10] and q[:10] in s)):
            score = 0.9
        else:
            score = difflib.SequenceMatcher(None, q, s).ratio()
        mtime = time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime))
        scored.append((f.stem, mtime, snip, score))
    scored.sort(key=lambda x: x[3], reverse=True)
    return scored

# ─────────────────────────── 监控（配合 notify_hook.py 的 Stop 钩子）───────────────────────────
WATCH_FILE = LOG_DIR / "watch.json"

def load_watch():
    try:
        return json.loads(WATCH_FILE.read_text())
    except Exception:
        return []

def add_watch(sid):
    w = load_watch()
    if sid not in w:
        w.append(sid)
    try:
        WATCH_FILE.write_text(json.dumps(w, ensure_ascii=False))
    except Exception:
        pass

def most_recent_sessions(exclude, limit=5):
    """所有项目里最近活动的会话（排除桥接自己建的 + 当前会话）。"""
    files = [f for f in CLAUDE_PROJECTS.glob("*/*.jsonl") if "/subagents/" not in str(f)]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files:
        if f.stem in exclude:
            continue
        mt = time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime))
        out.append((f.stem, mt, first_user_message(f)))
        if len(out) >= limit:
            break
    return out

WATCH_VERBS = ["监控", "盯住", "盯着", "盯", "看住", "关注", "watch"]
WATCH_NOUNS = ["会话", "任务", "对话", "session", "当前", "这个", "那个", "进度", "它"]

def detect_watch_intent(text):
    """监控意图 → 返回关键词（可能空串=盯最近的）；否则 None。要求以监控动词开头，避免误判。"""
    if any(text.startswith(v) for v in WATCH_VERBS) and any(n in text.lower() for n in WATCH_NOUNS):
        topic = text
        for w in sorted(WATCH_VERBS + WATCH_NOUNS + ["我", "的", "一下", "帮我", "请", " "], key=len, reverse=True):
            topic = topic.replace(w, " ")
        return re.sub(r"\s+", " ", topic).strip()
    return None

def do_watch(sid, snip, chat_id, note=""):
    add_watch(sid)
    send_feishu(f"🔭 已盯住会话：{(snip or '')[:36]}\n   id {sid[:8]}…\n它一跑完我立刻通知你（跑完那条会带最后结果）。{note}", chat_id)

def handle_watch(query, chat_id):
    exclude = set(state.get("_bridge_sessions") or [])
    if state.get("session_id"):
        exclude.add(state["session_id"])
    if query:
        matches = [m for m in search_sessions(state["workdir"], query) if m[0] not in exclude and m[3] >= 0.35]
        if not matches:
            send_feishu(f"没找到和「{query}」相关的会话来监控。发「监控当前会话」盯最近活动的那个。", chat_id)
            return
        do_watch(matches[0][0], matches[0][2], chat_id)
    else:
        recent = most_recent_sessions(exclude, limit=1)
        if not recent:
            send_feishu("没找到可监控的会话（最近没有别的会话在动）。", chat_id)
            return
        do_watch(recent[0][0], recent[0][2], chat_id, note="\n（默认盯最近活动的那个；不对就说「监控 关键词」换一个）")

# ─────────────────────────── 实时进度（读会话记录，不打断任务）───────────────────────────
def recent_activity(tpath, n=6):
    """最近几条动作：助手说的话 💬 + 用了什么工具 🔧。用于「看进度」。"""
    events = []
    try:
        with open(tpath) as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "assistant":
                    for b in o.get("message", {}).get("content", []) or []:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "text" and b.get("text", "").strip():
                            events.append(("💬", re.sub(r"\s+", " ", b["text"]).strip()[:90]))
                        elif b.get("type") == "tool_use":
                            inp = b.get("input", {})
                            hint = ""
                            if isinstance(inp, dict):
                                hint = (inp.get("command") or inp.get("file_path") or inp.get("pattern")
                                        or inp.get("description") or inp.get("prompt") or "")
                            events.append(("🔧", f"{b.get('name', '?')} {str(hint)[:60]}".strip()))
    except Exception:
        pass
    return events[-n:]

def progress_report(sid, chat_id):
    f = find_transcript(sid)
    if not f:
        send_feishu("找不到这个会话的记录，可能还没开始跑。", chat_id)
        return
    age = time.time() - f.stat().st_mtime
    status = "🏃 正在跑" if age < 30 else (f"💤 已停 {int(age)} 秒" if age < 3600 else "💤 空闲")
    lines = [f"📊 会话 {sid[:8]}… · {status}", "最近动作："]
    acts = recent_activity(f)
    lines += [f"  {icon} {txt}" for icon, txt in acts] or ["  （暂无）"]
    send_feishu("\n".join(lines), chat_id)

def handle_progress(chat_id):
    watch = load_watch()
    sid = (watch[0] if watch else None) or state.get("session_id")
    if not sid:
        send_feishu("现在没有在监控或进行中的会话。先说「监控当前会话」盯一个。", chat_id)
        return
    progress_report(sid, chat_id)

_PROGRESS_KEYS = ["进度", "在干嘛", "在忙啥", "干到哪", "跑到哪", "什么进展", "有进展", "现在在做", "咋样了"]
# 含这些「干活动词」= 是任务不是查进度（避免「帮我做个进度条」被误判）
_TASK_VERBS = ["做", "写", "加", "改", "建", "开发", "实现", "生成", "画", "搞", "帮我", "弄", "创建", "设计"]

def detect_progress_intent(text):
    t = text.strip()
    if len(t) > 12 or any(v in t for v in _TASK_VERBS):
        return False
    return any(k in t for k in _PROGRESS_KEYS)

# ─────────────────────────── 自然语言「接上X会话」 ───────────────────────────
# 触发条件：同时出现「动作词」+「会话词」，才认为是想接历史会话（避免误判正常任务）。
RESUME_VERBS = ["接上", "接着", "接著", "继续", "回到", "切回", "切到", "回去",
                "找回", "恢复", "打开", "回去接", "接回"]
CONV_NOUNS = ["会话", "对话", "session", "聊天", "chat"]  # 必须出现其一
# 抽取话题时要剥掉的“元词”（剥完剩下的当搜索关键词）
_TOPIC_STRIP = RESUME_VERBS + CONV_NOUNS + [
    "你能", "能不能", "可以", "帮我", "我", "想", "把", "的", "那个", "这个", "之前", "以前",
    "上次", "刚才", "刚刚", "最近", "跟", "和", "关于", "聊", "说", "那次", "一下", "吗",
    "呢", "啊", "，", ",", "。", "、", "?", "？", "!", "！", " ",
]

def detect_resume_intent(text):
    """是接历史会话的意图就返回话题字符串（可能是空串）；否则返回 None。"""
    t = text.lower()
    if any(v in text for v in RESUME_VERBS) and any(n in t for n in CONV_NOUNS):
        topic = text
        for w in sorted(_TOPIC_STRIP, key=len, reverse=True):
            topic = topic.replace(w, " ")
        return re.sub(r"\s+", " ", topic).strip()
    return None

def set_resume(sid, chat_id):
    """接上真实会话（不 fork）：桌面端回去也能看到手机上聊的；并把「上次聊到哪」发给你看。"""
    state["session_id"] = sid
    state["fork_next"] = False
    state["_awaiting_pick"] = False
    save_state(state)
    f = find_transcript(sid)
    preview = ""
    if f:
        u, a = last_exchange(f)
        if u or a:
            preview = (f"\n\n———上次聊到这儿———\n🧑 你：{u[:200]}\n🤖 它：{a[:400]}\n——————————————")
        try:
            mb = f.stat().st_size / 1024 / 1024
            if mb >= 1.5:
                preview += f"\n⚠️ 这会话较大（{mb:.1f}MB），每轮偏慢偏贵。"
        except Exception:
            pass
    send_feishu(f"✅ 接上会话 {sid}（同一个会话，不是副本）{preview}\n\n直接发指令接着聊。\n（提醒：别在电脑和手机同时敲同一个会话）", chat_id)

def handle_resume_query(query, chat_id):
    """按关键词搜历史会话：明显唯一就直接接；拿不准就列出来让你回数字挑。"""
    matches = [m for m in search_sessions(state["workdir"], query or "") if m[3] >= 0.35]
    if not matches:
        send_feishu(f"没找到和「{query}」相关的历史会话。\n发 /sessions 看全部，或直接发指令我当新任务处理。", chat_id)
        return
    if len(matches) == 1 or (matches[0][3] >= 0.85 and matches[0][3] - matches[1][3] >= 0.2):
        set_resume(matches[0][0], chat_id)  # 明显唯一 → 直接接
        return
    top = matches[:5]
    lines = ["🤔 你想接哪个会话？回复序号就行（1/2/3…）：\n"]
    for i, (s_, mt, sn, sc) in enumerate(top, 1):
        lines.append(f"{i}. [{mt}] {sn}")
    state["_last_list"] = [m[0] for m in top]
    state["_awaiting_pick"] = True  # 下一条纯数字当成选择
    save_state(state)
    send_feishu("\n".join(lines), chat_id)

# ─────────────────────────── 切换模型 ───────────────────────────
# CLI 接受别名：opus / sonnet / haiku / fable（用你的 Max 订阅）
MODEL_ALIASES = {
    "opus": "opus", "sonnet": "sonnet", "haiku": "haiku", "fable": "fable",
    "最强": "opus", "最聪明": "opus", "最快": "haiku", "省钱": "haiku", "便宜": "haiku",
}
_MODEL_STRIP = ["切换", "切到", "切成", "换成", "换到", "改用", "改成", "模型", "model",
                "用", "换", "改", "切", "一下", "吧", "啊", "呢", "把", "成", "到",
                "的", "了", "嘛", "要", "我", "想", "这个", "那个", "个",
                "，", ",", "。", "、", " "] + list(MODEL_ALIASES.keys())

def detect_model_switch(text):
    """纯切模型指令就返回模型别名；否则 None（避免把『用opus写代码』这种任务误判）。"""
    t = text.lower()
    model = None
    for k, v in MODEL_ALIASES.items():
        if k in t:
            model = v
            break
    if not model:
        return None
    rest = t
    for w in sorted(_MODEL_STRIP, key=len, reverse=True):
        rest = rest.replace(w, "")
    return model if rest.strip() == "" else None  # 剥完只剩模型名才算“纯切换”

def set_model(model, chat_id):
    if model in (None, "default"):
        state["model"] = None
        save_state(state)
        send_feishu("✅ 已恢复默认模型", chat_id)
    else:
        state["model"] = model
        save_state(state)
        send_feishu(f"✅ 之后用 {model} 跑（会话继续沿用；发任务即可）", chat_id)

# ─────────────────────────── 命令处理 ───────────────────────────
HELP = """🤖 飞书↔Claude 桥接 · 用法

直接发文字 = 给 Claude 的指令，干完把结果发回来。再回复 = 继续同一个会话。

都能说人话（不用记命令）：
  🔭 监控桌面端任务：「监控当前会话」→ 它一跑完就通知你，再回复即可接着聊
  📊 看任务跑到哪：「进度」「在干嘛」
  🔗 接以前的会话：「接上我聊飞书的那个会话」（拿不准会列几条你回数字挑）
  🔄 换模型：「换成 opus」「用 haiku」
  👀 看上次聊到哪：/context

命令速查：
  /watch 监控最近会话   /progress 看进度      /sessions 列历史会话
  /resume 飞书 接历史   /context 看上下文     /model opus 换模型
  /cd 切目录   /new 开新会话   /pwd 看状态   /help 帮助"""

def handle_message(text, chat_id):
    global state
    state["last_chat_id"] = chat_id
    text = text.strip()

    # ---- 若正在等你从列表里挑会话：纯数字当选择 ----
    if state.get("_awaiting_pick"):
        if text.isdigit():
            lst = state.get("_last_list") or []
            i = int(text) - 1
            if 0 <= i < len(lst):
                set_resume(lst[i], chat_id)
            else:
                send_feishu("序号超出范围，重发一个数字，或发 /sessions 重新看。", chat_id)
            return
        state["_awaiting_pick"] = False  # 不是挑选 → 取消等待，继续按正常处理

    # ---- 命令 ----
    if text in ("/help", "帮助", "help"):
        send_feishu(HELP, chat_id); save_state(state); return

    if text in ("/pwd", "/status"):
        sid = state.get("session_id")
        send_feishu(f"📂 工作目录：{state['workdir']}\n🧵 当前会话：{sid or '（新会话，未开始）'}", chat_id)
        return

    if text in ("/context", "看上下文", "上次聊到哪"):
        sid = state.get("session_id")
        f = find_transcript(sid) if sid else None
        if not f:
            send_feishu("当前没接任何会话。先说「接上我聊X的会话」或 /sessions。", chat_id)
            return
        u, a = last_exchange(f)
        send_feishu(f"🧵 当前会话 {sid[:8]}…\n\n🧑 你上次：{u[:400] or '（无）'}\n\n🤖 它上次：{a[:1200] or '（无）'}", chat_id)
        return

    if text == "/progress":
        handle_progress(chat_id)
        return

    if text.startswith("/cd "):
        path = text[4:].strip()
        p = Path(path).expanduser()
        if p.is_dir():
            state["workdir"] = str(p)
            state["session_id"] = None  # 换目录 → 重置会话
            save_state(state)
            send_feishu(f"✅ 切到：{p}\n（会话已重置，发消息开新的，或 /sessions 挑历史会话续）", chat_id)
        else:
            send_feishu(f"❌ 目录不存在：{path}", chat_id)
        return

    if text in ("/sessions", "/ls"):
        sessions = list_sessions(state["workdir"])
        if not sessions:
            send_feishu(f"该目录下没有历史会话：{state['workdir']}", chat_id)
            return
        lines = [f"📚 {state['workdir']} 的历史会话（回复 /resume 序号 接着干）：\n"]
        for i, (sid, mtime, snip) in enumerate(sessions, 1):
            lines.append(f"{i}. [{mtime}] {snip}\n   id: {sid}")
        send_feishu("\n".join(lines), chat_id)
        # 暂存这次列表，供 /resume 序号 用
        state["_last_list"] = [s[0] for s in sessions]
        save_state(state)
        return

    if text.startswith("/resume"):
        arg = text[len("/resume"):].strip()
        if not arg:
            send_feishu("用法：\n• /resume 1          按 /sessions 序号\n• /resume 飞书        按关键词搜\n• 或直接大白话说「接上我聊飞书的会话」", chat_id)
            return
        if arg.isdigit():  # 序号
            lst = state.get("_last_list") or [s[0] for s in list_sessions(state["workdir"])]
            i = int(arg) - 1
            if 0 <= i < len(lst):
                set_resume(lst[i], chat_id)
            else:
                send_feishu(f"序号 {arg} 超出范围，先发 /sessions 看列表。", chat_id)
            return
        if re.fullmatch(r"[0-9a-fA-F-]{32,40}", arg):  # 完整 UUID
            set_resume(arg, chat_id)
            return
        handle_resume_query(arg, chat_id)  # 关键词 → 模糊搜索
        return

    if text == "/new":
        state["session_id"] = None
        save_state(state)
        send_feishu("🆕 已开新会话。发第一条指令吧。", chat_id)
        return

    if text.startswith("/watch"):
        handle_watch(text[len("/watch"):].strip(), chat_id)
        return

    if text.startswith("/model"):
        arg = text[len("/model"):].strip().lower()
        if not arg:
            cur = state.get("model") or "默认（一般是 sonnet）"
            send_feishu(f"当前模型：{cur}\n切换：/model opus | sonnet | haiku | fable | default\n（也可直接说「换成 opus」）", chat_id)
            return
        if arg in ("default", "默认", "重置"):
            set_model(None, chat_id)
            return
        if arg in MODEL_ALIASES:
            set_model(MODEL_ALIASES[arg], chat_id)
            return
        send_feishu(f"没有「{arg}」这个模型。可选：opus / sonnet / haiku / fable / default", chat_id)
        return

    if text == "/stop":
        send_feishu("（/stop 占位：当前版本任务跑完才会响应，暂不能中断）", chat_id)
        return

    if text.startswith("/"):
        send_feishu(f"未知命令：{text}\n发 /help 看用法。", chat_id)
        return

    # ---- 自然语言「进度 / 在干嘛」→ 看监控中会话的实时进度 ----
    if detect_progress_intent(text):
        handle_progress(chat_id)
        return

    # ---- 自然语言「监控当前会话」→ 盯住并在跑完时通知 ----
    wq = detect_watch_intent(text)
    if wq is not None:
        handle_watch(wq, chat_id)
        return

    # ---- 自然语言「换成 opus」→ 切模型 ----
    m = detect_model_switch(text)
    if m is not None:
        set_model(m, chat_id)
        return

    # ---- 自然语言「接上X会话」→ 自动识别并接会话 ----
    topic = detect_resume_intent(text)
    if topic is not None:
        handle_resume_query(topic, chat_id)
        return

    # ---- 普通指令 → 交给 Claude ----
    sid = state.get("session_id")
    fork = bool(state.get("fork_next"))
    model = state.get("model")
    tag = "▶️ 继续会话" if sid else "🆕 新会话"
    mtag = f"，模型 {model}" if model else ""
    send_feishu(f"🏃 收到，开始干活…（{tag}{mtag}，目录 {state['workdir']}）", chat_id)

    result, new_sid, is_err = run_claude(text, sid, state["workdir"], chat_id, fork=fork, model=model)
    state["session_id"] = new_sid
    state["fork_next"] = False  # fork 只在接历史会话那一次，之后就在副本上正常续
    if new_sid:  # 记下桥接自己用的会话，监控时排除，避免盯到自己
        bs = state.get("_bridge_sessions") or []
        if new_sid not in bs:
            bs.append(new_sid)
        state["_bridge_sessions"] = bs[-50:]
    save_state(state)
    print(f"[bridge] 完成 is_error={is_err} sid={new_sid} 回复长度={len(result or '')}", file=sys.stderr)

    head = "⚠️ 出错了：\n" if is_err else "✅ 完成：\n"
    send_feishu(head + (result or "(无内容)"), chat_id)

# ─────────────────────────── 解析来消息 ───────────────────────────
def extract_text(evt):
    """从事件里取纯文本。event consume 已把 text 消息的 content 解码成纯文本。"""
    content = evt.get("content", "")
    if isinstance(content, str):
        s = content.strip()
        # 兜底：万一是 {"text":"..."} 这种没解码的
        if s.startswith("{"):
            try:
                j = json.loads(s)
                if isinstance(j, dict) and "text" in j:
                    return j["text"]
            except Exception:
                pass
        return s
    return ""

# ─────────────────────────── 主循环 ───────────────────────────
def main():
    if not OWNER_OPEN_ID:
        print("[bridge] ❌ 未配置 owner_open_id。请在 ~/.feishu-claude-bridge/config.json 里设置"
              "（复制 config.example.json，open_id 用 `lark-cli auth status` 可查）。", file=sys.stderr)
        sys.exit(1)
    print(f"[bridge] 启动。OWNER={OWNER_OPEN_ID} workdir={state['workdir']}", file=sys.stderr)
    seen_events = set()

    while True:  # 断线自动重连
        err_fh = open(CONSUME_ERR_LOG, "a")
        proc = subprocess.Popen(
            ["lark-cli", "event", "consume", EVENT_KEY, "--as", "bot"],
            stdout=subprocess.PIPE,
            stderr=err_fh,
            stdin=subprocess.PIPE,   # 关键：保持 stdin 打开，否则 EOF 会让它立刻优雅退出
            text=True,
        )
        print(f"[bridge] event consume 已连接（pid={proc.pid}），等待飞书消息…", file=sys.stderr)
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    continue

                # 去重
                eid = evt.get("event_id")
                if eid:
                    if eid in seen_events:
                        continue
                    seen_events.add(eid)
                    if len(seen_events) > 500:
                        seen_events = set(list(seen_events)[-200:])

                # 安全过滤：只认 OWNER 本人 + 文字消息
                if evt.get("sender_id") != OWNER_OPEN_ID:
                    continue
                if evt.get("message_type") != "text":
                    send_feishu("目前只支持文字消息～", evt.get("chat_id"))
                    continue

                text = extract_text(evt)
                chat_id = evt.get("chat_id")
                if not text:
                    continue
                print(f"[bridge] 收到指令: {text[:60]}", file=sys.stderr)
                try:
                    handle_message(text, chat_id)
                except Exception as e:
                    print(f"[bridge] 处理出错: {e}", file=sys.stderr)
                    send_feishu(f"❌ 桥接内部出错：{e}", chat_id)
        except KeyboardInterrupt:
            print("[bridge] 收到中断，退出。", file=sys.stderr)
            try:
                proc.terminate()
            except Exception:
                pass
            break
        finally:
            err_fh.close()

        # 走到这说明 consume 断了 → 等几秒重连
        code = proc.poll()
        print(f"[bridge] event consume 断开（exit={code}），3 秒后重连…", file=sys.stderr)
        time.sleep(3)

if __name__ == "__main__":
    main()
