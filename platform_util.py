# -*- coding: utf-8 -*-
"""
桥接用到的跨平台小工具（bridge.py / notify_hook.py 共用）。

Mac / Linux 上这里的函数全部是「原样透传」，行为和以前一模一样；
只有 Windows 才会走额外逻辑：

  · resolve_cli()  把 PATH 里的命令解析成「能安全 spawn 的 argv」。
      原因：Windows 的 subprocess 不会按 PATHEXT 找 .cmd，Popen(["claude"]) 直接
      FileNotFoundError；而 npm 装出来的 claude / lark-cli 都是 .cmd 包装脚本，
      一旦经 cmd.exe 转发，消息里的换行会把参数截断、& | " % 会被 cmd 当命令解析
      （等于远程命令注入）。所以这里读 .cmd 包装脚本，拿到它真正指向的 .exe / .js，
      直接 spawn，不经 cmd.exe。
  · POPEN_KW       Windows 后台常驻时，子进程不要弹黑窗口。
  · kill_tree()    Windows 的 proc.kill() 只杀一个进程，claude 派生的子进程会变孤儿，
                   这里改用 taskkill /T 杀整棵树。
  · keep_awake()   Mac 上 `caffeinate -s` 的 Windows 等价物（防空闲睡眠）。
"""
import os
import re
import shutil
import subprocess
import sys

IS_WIN = sys.platform == "win32"

# subprocess.CREATE_NO_WINDOW（只在 Windows 的 subprocess 模块里才有，这里写死数值）
_CREATE_NO_WINDOW = 0x08000000
POPEN_KW = {"creationflags": _CREATE_NO_WINDOW} if IS_WIN else {}

# npm 的 cmd-shim 里真正的目标长这样：  "%dp0%\node_modules\pkg\bin\claude.exe"  %*
_SHIM_TARGET_RE = re.compile(r'"%~?dp0%?[\\/]+([^"]+)"', re.I)
_SHIM_PROG_RE = re.compile(r'SET\s+"_prog=([^"]+)"', re.I)


def _shim_argv(shim_path):
    """解析 npm 生成的 .cmd 包装脚本，返回真实的 argv 前缀；解析不出返回 None。"""
    try:
        with open(shim_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    base = os.path.dirname(shim_path)
    for m in _SHIM_TARGET_RE.finditer(text):
        target = os.path.normpath(os.path.join(base, m.group(1)))
        if not os.path.isfile(target):
            continue
        if target.lower().endswith(".exe"):
            return [target]
        # 脚本：包装脚本里最后一个 SET "_prog=xxx" 是回落到 PATH 的解释器（一般是 node）
        progs = _SHIM_PROG_RE.findall(text)
        prog = progs[-1] if progs else "node"
        prog_path = shutil.which(prog)
        if prog_path:
            return [prog_path, target]
    return None


def resolve_cli(name):
    """返回可直接传给 subprocess.Popen / run 的 argv 前缀（列表）。找不到抛 FileNotFoundError。"""
    if not IS_WIN:
        return [name]  # Mac / Linux：照旧交给 PATH 查找，行为不变
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"PATH 里找不到 {name}")
    if os.path.splitext(path)[1].lower() in (".cmd", ".bat"):
        argv = _shim_argv(path)
        if argv is None:
            raise FileNotFoundError(
                f"{path} 是 cmd 包装脚本且解析不出真实程序；"
                "为避免消息内容被 cmd.exe 当命令解析，拒绝经它启动")
        return argv
    return [path]


def kill_tree(proc):
    """杀掉 proc 及它派生的所有子进程。"""
    if IS_WIN:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10, **POPEN_KW)
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def keep_awake():
    """Windows：等价于 `caffeinate -s`，进程存活期间不因空闲进入睡眠。其它系统返回 False。"""
    if not IS_WIN:
        return False
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    fn = ctypes.windll.kernel32.SetThreadExecutionState
    fn.argtypes = [ctypes.c_uint]
    fn.restype = ctypes.c_uint
    return bool(fn(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
