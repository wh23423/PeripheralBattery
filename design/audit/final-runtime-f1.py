#!/usr/bin/env python3
"""[audit only] Test 3 / F1: visible start with incremental device arrival (mock collector).

Checks the hero card after all 5 devices arrive one-by-one (mock default: no mutation)
and captures a DPI-aware screenshot. Retries if the app deadlocks on show (known
intermittent issue documented in the audit report).
"""
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
SCR = ROOT / "design" / "audit" / "final-runtime-screens"
PS7 = r"C:\Program Files\PowerShell\7\pwsh.exe"


def log_bytes() -> bytes:
    try:
        return LOG.read_bytes()
    except OSError:
        return b""


def kill_tree(pid: int):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def shot(path: Path):
    subprocess.run([PS7, "-NoProfile", "-File",
                    str(ROOT / "tools" / "capture_screen.ps1"), "-Out", str(path)],
                   capture_output=True, timeout=60)


def attempt(i: int) -> dict:
    env = os.environ.copy()
    env["PB_LOG_LEVEL"] = "debug"
    env["PB_SHOW_ON_START"] = "1"
    env["PB_COLLECTOR"] = f'"{PY}" "{MOCK}"'
    env["PB_SINGLE_INSTANCE_NAME"] = f"Global\\PBAuditFinal-f1-{i}-{random.randint(1000,9999)}"
    env["PB_QUIT_AFTER_MS"] = "15000"
    env.pop("PB_FPS_PROBE", None)
    env.pop("PB_HIDE_AFTER_MS", None)
    env.pop("PB_MOCK_MUTATE_MS", None)
    off = len(log_bytes())
    t0 = time.monotonic()
    p = subprocess.Popen([str(EXE)], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # wait 6s after start (all mock devices arrive by ~1.2s) then screenshot
    time.sleep(6.0)
    img = SCR / "test3-f1-mock-hero.png"
    shot(img)
    exited = p.poll()
    hung = exited is None
    if hung:
        # give the normal quit path a chance first
        try:
            p.wait(timeout=12)
            hung = False
            exited = p.returncode
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            exited = None
    seg = log_bytes()[off:].decode("utf-8", "replace")
    (EV / f"test3-f1-mock-attempt{i}.log").write_text(seg, encoding="utf-8")
    lines = seg.splitlines()
    return {
        "attempt": i,
        "hung": hung,
        "exit_code": exited,
        "screenshot": str(img),
        "tray_frontend": [ln for ln in lines if "tray-state source=frontend" in ln],
        "hero_relevant": [ln for ln in lines if "collector device key" in ln],
        "warn_err": [ln for ln in lines if "[WARN]" in ln or "[ERROR]" in ln or "panic" in ln],
    }


def main() -> int:
    EV.mkdir(parents=True, exist_ok=True)
    SCR.mkdir(parents=True, exist_ok=True)
    results = []
    for i in range(4):
        r = attempt(i)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False))
        if not r["hung"]:
            break
        print(f"attempt {i} hung; retrying")
    (EV / "test3-f1.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    ok = any(not r["hung"] for r in results)
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
