#!/usr/bin/env python3
"""可见/隐藏启动压测：验证托盘并发更新不再造成主线程死锁（P0）。

用法（项目根，使用 .venv）：
  .venv/Scripts/python.exe tools/stress_start.py --runs 40 --mode visible
  .venv/Scripts/python.exe tools/stress_start.py --runs 20 --mode hidden

做法：每次用唯一 PB_SINGLE_INSTANCE_NAME 启动 release exe，
PB_QUIT_AFTER_MS 到点应正常退出；超过时限仍存活视为 hang（强杀并记录）。
visible 模式用 scripts/mock-collector.py 快速推送 device，放大托盘更新并发；
隐藏模式不带 PB_SHOW_ON_START（托盘更新来自 Rust tray_mirror）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PeripheralBattery" / "logs" / "app.log"
APP = ROOT / "src-tauri" / "target" / "release" / "peripheral-battery.exe"
MOCK = ROOT / "scripts" / "mock-collector.py"
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def read_from(offset: int) -> str:
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            return f.read()
    except OSError:
        return ""


def run_once(i: int, mode: str, quit_ms: int, timeout_s: float) -> dict:
    env = os.environ.copy()
    env["PB_LOG_LEVEL"] = "info"
    env["PB_QUIT_AFTER_MS"] = str(quit_ms)
    env["PB_SINGLE_INSTANCE_NAME"] = f"Global\\PBStress-{os.getpid()}-{i}"
    env["PB_COLLECTOR"] = f'"{PY}" "{MOCK}"'
    if mode == "visible":
        env["PB_SHOW_ON_START"] = "1"
    else:
        env.pop("PB_SHOW_ON_START", None)
    offset = LOG.stat().st_size if LOG.exists() else 0
    t0 = time.monotonic()
    proc = subprocess.Popen([str(APP)], cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=quit_ms / 1000 + timeout_s)
        hang = False
    except subprocess.TimeoutExpired:
        hang = True
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    elapsed = round(time.monotonic() - t0, 2)
    text = read_from(offset)
    exit_logged = "app exit" in text
    return {"run": i, "hang": hang, "elapsed_s": elapsed,
            "exit_code": proc.returncode, "app_exit_logged": exit_logged,
            "log_lines": len(text.splitlines())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=40)
    ap.add_argument("--mode", choices=["visible", "hidden"], default="visible")
    ap.add_argument("--quit-ms", type=int, default=4500)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    if not APP.exists():
        print(f"FAIL: {APP} 不存在")
        return 2

    # 可见模式用 pinned 避免失焦自动隐藏影响启动路径；结束恢复。
    appcfg = Path(os.environ["LOCALAPPDATA"]) / "PeripheralBattery" / "app.json"
    backup = None
    if appcfg.exists():
        backup = appcfg.read_text(encoding="utf-8", errors="replace")
    if args.mode == "visible":
        appcfg.write_text('{"pinned":true}', encoding="utf-8")

    results = []
    try:
        for i in range(1, args.runs + 1):
            r = run_once(i, args.mode, args.quit_ms, args.timeout)
            results.append(r)
            mark = "HANG" if r["hang"] else ("OK" if r["app_exit_logged"] else "NO-EXIT-LOG")
            print(f"[{i:02d}/{args.runs}] {mark:10s} {r['elapsed_s']:6.2f}s exit={r['exit_code']} log={r['log_lines']}行")
            time.sleep(0.8)
    finally:
        if args.mode == "visible":
            appcfg.write_text(backup if backup is not None else '{"pinned":false}', encoding="utf-8")

    hangs = [r for r in results if r["hang"]]
    no_exit_log = [r for r in results if not r["hang"] and not r["app_exit_logged"]]
    summary = {
        "mode": args.mode,
        "runs": args.runs,
        "hangs": len(hangs),
        "no_exit_log": len(no_exit_log),
        "results": results,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved:", args.json)
    print(f"\n=== {args.mode}: {len(hangs)} hangs / {args.runs} runs; no-exit-log={len(no_exit_log)} ===")
    return 0 if not hangs and not no_exit_log else 1


if __name__ == "__main__":
    raise SystemExit(main())
