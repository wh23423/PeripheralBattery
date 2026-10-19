#!/usr/bin/env python3
"""[audit only] Test 4 / F3: hidden cold start with the real packaged collector x2.

For each run: start the release app hidden with a unique single-instance name and
PB_QUIT_AFTER_MS=30000 (real collector, no PB_COLLECTOR), wait for exit, then compute
app start -> first device/tray-state seconds from the app.log segment.
"""
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

TAG = sys.argv[1] if len(sys.argv) > 1 else "a"

ROOT = Path(r"D:\desktop\电量显示插件")
EXE = ROOT / "src-tauri" / "target" / "release" / "peripheral-battery.exe"
LOG = Path(os.environ["LOCALAPPDATA"]) / "PeripheralBattery" / "logs" / "app.log"
EV = ROOT / "design" / "audit" / "final-runtime-evidence"

TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d\d\d) \[(\w+)\] (.*)$")
BOOT_CUTOFF_MS = 20_000  # a cold start must reach the first device well inside the 30s run


def log_bytes() -> bytes:
    try:
        return LOG.read_bytes()
    except OSError:
        return b""


def kill_tree(pid: int):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def ts_seconds(ts: str) -> float:
    h, m, rest = ts.split(" ")[1].split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def run(i: int) -> dict:
    env = os.environ.copy()
    env["PB_LOG_LEVEL"] = "debug"
    env.pop("PB_SHOW_ON_START", None)
    env.pop("PB_COLLECTOR", None)
    env.pop("PB_PYTHON", None)
    env.pop("PB_FPS_PROBE", None)
    env.pop("PB_HIDE_AFTER_MS", None)
    env["PB_QUIT_AFTER_MS"] = "30000"
    env["PB_SINGLE_INSTANCE_NAME"] = f"Global\\PBAuditFinal-f3-{i}-{random.randint(1000,9999)}"
    off = len(log_bytes())
    t0 = time.monotonic()
    p = subprocess.Popen([str(EXE)], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hung = p.poll() is None
    if hung:
        try:
            p.wait(timeout=45)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    seg = log_bytes()[off:].decode("utf-8", "replace")
    (EV / f"test4-f3-{TAG}-attempt{i}.log").write_text(seg, encoding="utf-8")
    rows = []
    for ln in seg.splitlines():
        m = TS.match(ln)
        if m:
            rows.append((ts_seconds(m.group(1)), m.group(3)))
    start = next((t for t, msg in rows if msg.startswith("app start ")), None)
    hello = next((t for t, msg in rows if "hello protocol=" in msg), None)
    ready = next((t for t, msg in rows if "collector-state state=ready" in msg), None)
    first_dev = next((t for t, msg in rows
                      if "collector device key=" in msg or "tray-state source=rust" in msg), None)
    first_tray = next((t for t, msg in rows if "tray-state source=rust" in msg), None)
    seg_text = "\n".join(msg for _, msg in rows)
    spawn = next((msg for msg in seg_text.splitlines() if "sidecar spawn path=" in msg), "")
    return {
        "run": i,
        "exit_code": p.returncode if p.poll() is not None else None,
        "hung": p.poll() is None,
        "spawn_kind_packaged": "kind=packaged" in spawn,
        "app_start_to_hello_s": None if (start is None or hello is None) else round(hello - start, 2),
        "app_start_to_ready_s": None if (start is None or ready is None) else round(ready - start, 2),
        "app_start_to_first_device_s": None if (start is None or first_dev is None) else round(first_dev - start, 2),
        "app_start_to_first_tray_s": None if (start is None or first_tray is None) else round(first_tray - start, 2),
        "first_device_line": next((msg for _, msg in rows if "collector device key=" in msg or "tray-state source=rust" in msg), ""),
        "warn_err": [msg for _, msg in rows if "[WARN]" in msg or "[ERROR]" in msg or "panic" in msg],
        "wall_elapsed_s": round(time.monotonic() - t0, 1),
    }


def main() -> int:
    EV.mkdir(parents=True, exist_ok=True)
    results = [run(i) for i in range(2)]
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    (EV / f"test4-f3-{TAG}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    ok = all(not r["hung"] and r["exit_code"] == 0 and r["spawn_kind_packaged"]
             and r["app_start_to_first_device_s"] is not None
             and r["app_start_to_first_device_s"] <= 30 for r in results)
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
