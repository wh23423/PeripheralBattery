#!/usr/bin/env python3
"""[audit only] Final independent runtime audit - visible-start stability loop.

Starts the release app N times (visible, fake static collector), each with a unique
single-instance name and PB_QUIT_AFTER_MS; records whether FPS_PROBE appears and
whether the app exits cleanly. Does not modify sources/config (app.json pinned=true
is set by the caller and expected during the loop).
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
LOG = Path(os.environ["LOCALAPPDATA"]) / "PeripheralBattery" / "logs" / "app.log"
OUT = ROOT / "design" / "audit" / "final-runtime-evidence"


def log_bytes() -> bytes:
    try:
        return LOG.read_bytes()
    except OSError:
        return b""


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True)


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    quit_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    fps_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(n):
        env = os.environ.copy()
        env["PB_LOG_LEVEL"] = "debug"
        env["PB_SHOW_ON_START"] = "1"
        if fps_ms > 0:
            env["PB_FPS_PROBE"] = str(fps_ms)
        else:
            env.pop("PB_FPS_PROBE", None)
        env["PB_QUIT_AFTER_MS"] = str(quit_ms)
        env["PB_COLLECTOR"] = f'"{PY}" -m collector --stdio --fake --scenario static --speed 1'
        name = f"Global\\PBAuditFinal-loop{i}-{random.randint(1000, 9999)}"
        env["PB_SINGLE_INSTANCE_NAME"] = name
        env.pop("PB_HIDE_AFTER_MS", None)
        env.pop("PB_MOCK_MUTATE_MS", None)
        off = len(log_bytes())
        t0 = time.monotonic()
        p = subprocess.Popen([str(EXE)], cwd=str(ROOT), env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fps = None
        deadline = t0 + quit_ms / 1000 + 15
        exited = None
        while time.monotonic() < deadline:
            time.sleep(0.2)
            raw = log_bytes()[off:].decode("utf-8", "replace")
            if fps is None and "FPS_PROBE {" in raw:
                for line in raw.splitlines():
                    idx = line.find("FPS_PROBE {")
                    if idx >= 0:
                        try:
                            fps = json.loads(line[idx + len("FPS_PROBE "):])
                        except json.JSONDecodeError:
                            pass
            if p.poll() is not None:
                exited = p.returncode
                break
        hung = exited is None
        if hung:
            kill_tree(p.pid)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        time.sleep(0.8)
        raw = log_bytes()[off:].decode("utf-8", "replace")
        seg = [ln for ln in raw.splitlines() if ln.strip()]
        warn = [ln for ln in seg if "[WARN]" in ln or "[ERROR]" in ln or "panic" in ln]
        last = seg[-3:] if seg else []
        row = {
            "run": i,
            "instance": name,
            "exit_code": exited,
            "hung": hung,
            "fps": None if fps is None else {k: fps.get(k) for k in
                    ("fps", "p99_ms", "max_ms", "rows", "backdrop_filter", "tauri")},
            "warn_err": warn,
            "last_log": last,
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    (OUT / "test2-visible-loop.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    hangs = sum(1 for r in rows if r["hung"])
    print(f"SUMMARY runs={n} hangs={hangs} fps_missing={sum(1 for r in rows if r['fps'] is None)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
