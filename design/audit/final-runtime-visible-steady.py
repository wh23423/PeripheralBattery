#!/usr/bin/env python3
"""[audit only] Visible steady-state probe: does the tray-update deadlock also happen
during ongoing device updates (not only at first show)?

Visible start (pinned=true expected), mock collector mutating every 1.5 s. Every 2 s
we send WM_NULL to the app's top-level window with a 1 s timeout; timeout => the UI
thread is not pumping messages (frozen). Runs up to 2 attempts of ~22 s.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import random
import subprocess
import sys
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


def app_windows(pid: int):
    out = []

    def cb(hwnd, _lp):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            out.append((hwnd, cls.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def responsive(hwnd) -> bool:
    r = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(hwnd, 0, 0, 0, 0x0002, 1000, ctypes.byref(r)))


def attempt(i: int) -> dict:
    env = os.environ.copy()
    env["PB_LOG_LEVEL"] = "debug"
    env["PB_SHOW_ON_START"] = "1"
    env["PB_COLLECTOR"] = f'"{PY}" "{MOCK}"'
    env["PB_MOCK_MUTATE_MS"] = "1500"
    env["PB_SINGLE_INSTANCE_NAME"] = f"Global\\PBAuditFinal-steady-{i}-{random.randint(1000,9999)}"
    env["PB_QUIT_AFTER_MS"] = "22000"
    env.pop("PB_FPS_PROBE", None)
    env.pop("PB_HIDE_AFTER_MS", None)
    off = len(log_bytes())
    p = subprocess.Popen([str(EXE)], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 6
    hwnd = None
    while time.time() < deadline:
        wins = [h for h, cls in app_windows(p.pid) if cls == "Tauri Window"]
        if wins:
            hwnd = wins[0]
            break
        time.sleep(0.2)
    samples = []
    frozen_at = None
    t0 = time.time()
    while time.time() < deadline + 24:
        if p.poll() is not None:
            break
        time.sleep(1.8)
        if hwnd is None:
            wins = [h for h, cls in app_windows(p.pid) if cls == "Tauri Window"]
            hwnd = wins[0] if wins else None
        ok = responsive(hwnd) if hwnd else False
        samples.append(round(time.time() - t0, 1))
        if not ok and frozen_at is None:
            frozen_at = round(time.time() - t0, 1)
            break
    hung = p.poll() is None
    if hung:
        kill_tree(p.pid)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    seg = log_bytes()[off:].decode("utf-8", "replace")
    (EV / f"test2s-steady-attempt{i}.log").write_text(seg, encoding="utf-8")
    lines = seg.splitlines()
    return {
        "attempt": i,
        "frozen_at_s": frozen_at,
        "exit_code": None if hung else p.returncode,
        "hung": hung,
        "tray_updates": len([ln for ln in lines if "tray-state source=" in ln]),
        "last_lines": lines[-6:],
        "warn_err": [ln for ln in lines if "[WARN]" in ln or "[ERROR]" in ln or "panic" in ln],
    }


def main() -> int:
    EV.mkdir(parents=True, exist_ok=True)
    results = []
    for i in range(2):
        r = attempt(i)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False))
    (EV / "test2s-steady.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    frozen = sum(1 for r in results if r["frozen_at_s"] is not None or r["hung"])
    print(f"RESULT frozen={frozen}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
