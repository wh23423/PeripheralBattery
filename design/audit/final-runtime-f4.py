#!/usr/bin/env python3
"""[audit only] Test 5 / F4: focus-loss hide must not trigger the ui_ready fallback.

Visible start (PB_SHOW_ON_START=1, app.json pinned=false), steal focus with Notepad
~1s after start, wait, then assert the app.log segment has:
  - auto-hide + window hidden reason=focus-lost
  - NO "ui_ready not received within 2000ms" WARN
  - NO "window show" after the hide (no fallback re-show)
Retries if the show path deadlocks (documented intermittent product issue).
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import random
import subprocess
import time
from pathlib import Path

ROOT = Path(r"D:\desktop\电量显示插件")
EXE = ROOT / "src-tauri" / "target" / "release" / "peripheral-battery.exe"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
MOCK = ROOT / "scripts" / "mock-collector.py"
LOG = Path(os.environ["LOCALAPPDATA"]) / "PeripheralBattery" / "logs" / "app.log"
EV = ROOT / "design" / "audit" / "final-runtime-evidence"

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def log_bytes() -> bytes:
    try:
        return LOG.read_bytes()
    except OSError:
        return b""


def kill_tree(pid: int):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def windows_of(pid: int):
    out = []

    def cb(hwnd, _lp):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 2)
            user32.GetWindowTextW(hwnd, buf, n + 2)
            out.append((hwnd, buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def focus_notepad() -> subprocess.Popen | None:
    np = subprocess.Popen(["notepad.exe"])
    deadline = time.time() + 5
    hwnd = None
    while time.time() < deadline and hwnd is None:
        time.sleep(0.15)
        wins = windows_of(np.pid)
        if wins:
            hwnd = wins[0][0]
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return np


def attempt(i: int) -> dict:
    env = os.environ.copy()
    env["PB_LOG_LEVEL"] = "debug"
    env["PB_SHOW_ON_START"] = "1"
    env["PB_COLLECTOR"] = f'"{PY}" "{MOCK}"'
    env["PB_SINGLE_INSTANCE_NAME"] = f"Global\\PBAuditFinal-f4-{i}-{random.randint(1000,9999)}"
    env["PB_QUIT_AFTER_MS"] = "12000"
    env.pop("PB_FPS_PROBE", None)
    env.pop("PB_HIDE_AFTER_MS", None)
    off = len(log_bytes())
    p = subprocess.Popen([str(EXE)], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # wait until window is shown in the log (<=5s), then steal focus ~1s after app start
    deadline = time.time() + 6
    shown = False
    while time.time() < deadline:
        seg = log_bytes()[off:].decode("utf-8", "replace")
        if "window shown reason=ui-ready" in seg:
            shown = True
            break
        time.sleep(0.1)
    time.sleep(0.6)
    np = focus_notepad()
    time.sleep(3.0)  # >2s fallback window + auto-hide delay
    if np:
        subprocess.run(["taskkill", "/PID", str(np.pid), "/F"], capture_output=True)
    hung = p.poll() is None
    if hung:
        try:
            p.wait(timeout=15)
            hung = False
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    seg = log_bytes()[off:].decode("utf-8", "replace")
    (EV / f"test5-f4-attempt{i}.log").write_text(seg, encoding="utf-8")
    lines = seg.splitlines()
    idx_hide = [n for n, ln in enumerate(lines) if "window hidden reason=focus-lost" in ln]
    fallback_warn = [ln for ln in lines if "ui_ready not received within 2000ms" in ln]
    show_after_hide = []
    if idx_hide:
        k = idx_hide[0]
        show_after_hide = [ln for ln in lines[k + 1:] if "window shown reason=" in ln or "window show reason=" in ln]
    return {
        "attempt": i,
        "window_shown_at_start": shown,
        "hung": hung,
        "exit_code": p.returncode if p.poll() is not None else None,
        "auto_hide": [ln for ln in lines if "auto-hide triggered" in ln or "window hidden reason=focus-lost" in ln],
        "fallback_warn": fallback_warn,
        "show_after_hide": show_after_hide,
        "fallback_debug": [ln for ln in lines if "ui_ready received; fallback thread exits" in ln],
        "warn_err": [ln for ln in lines if "[WARN]" in ln or "[ERROR]" in ln or "panic" in ln],
    }


def main() -> int:
    EV.mkdir(parents=True, exist_ok=True)
    results = []
    for i in range(4):
        r = attempt(i)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False))
        if not r["hung"]:
            break
        print(f"attempt {i} hung; retrying")
    (EV / "test5-f4.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    r = results[-1]
    ok = (not r["hung"]) and r["exit_code"] == 0 and bool(r["auto_hide"]) \
        and not r["fallback_warn"] and not r["show_after_hide"]
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
