#!/usr/bin/env python3
"""[audit only] Reproduce the visible-start UI-thread deadlock and capture diagnostics.

Starts the release app visible with the fake static collector until a freeze is seen
(max 5 attempts). A freeze is declared when WM_NULL SendMessageTimeout times out on
the app's "Tauri Window" while the process is still alive. Before killing it we record
window classes, per-window SendMessageTimeout, IsHungAppWindow, child processes and
the tail of the app.log segment.
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


def top_windows(pid: int):
    out = []

    def cb(hwnd, _lp):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 2)
            user32.GetWindowTextW(hwnd, buf, n + 2)
            out.append({"hwnd": int(hwnd), "class": cls.value, "title": buf.value,
                        "visible": bool(user32.IsWindowVisible(hwnd))})
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def msg_probe(hwnd) -> bool:
    r = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(hwnd, 0, 0, 0, 0x0002, 1200, ctypes.byref(r)))


def run_once(i: int) -> dict:
    env = os.environ.copy()
    env["PB_LOG_LEVEL"] = "debug"
    env["PB_SHOW_ON_START"] = "1"
    env["PB_COLLECTOR"] = f'"{ROOT / ".venv" / "Scripts" / "python.exe"}" -m collector --stdio --fake --scenario static --speed 1'
    env["PB_SINGLE_INSTANCE_NAME"] = f"Global\\PBAuditFinal-hang-{i}-{random.randint(1000,9999)}"
    env["PB_QUIT_AFTER_MS"] = "9000"
    env.pop("PB_FPS_PROBE", None)
    env.pop("PB_HIDE_AFTER_MS", None)
    off = len(log_bytes())
    p = subprocess.Popen([str(EXE)], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5.0)  # > startup burst, < PB_QUIT_AFTER_MS + shutdown
    wins = top_windows(p.pid)
    tauri = [w for w in wins if w["class"] == "Tauri Window"]
    probes = {w["class"] + "#" + str(w["hwnd"]): msg_probe(w["hwnd"]) for w in wins}
    frozen = bool(tauri) and not probes.get("Tauri Window#" + str(tauri[0]["hwnd"]), True)
    diag = {"tauri_windows": tauri, "probes": probes, "frozen": frozen}
    if frozen:
        # dump thread/window diagnostics before killing
        diag["child_count"] = len(p.children(recursive=True)) if hasattr(p, "children") else None
        try:
            import psutil
            proc = psutil.Process(p.pid)
            diag["children"] = [{"pid": c.pid, "name": c.name()} for c in proc.children(recursive=True)]
            diag["threads"] = proc.num_threads()
            diag["cpu_times"] = list(proc.cpu_times()[:2])
        except Exception as e:  # pragma: no cover
            diag["diag_error"] = str(e)
    kill_tree(p.pid)
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    seg = log_bytes()[off:].decode("utf-8", "replace")
    (EV / f"test9-hang-attempt{i}.log").write_text(seg, encoding="utf-8")
    diag["attempt"] = i
    diag["tail"] = seg.splitlines()[-14:]
    return diag


def main() -> int:
    EV.mkdir(parents=True, exist_ok=True)
    results = []
    for i in range(5):
        d = run_once(i)
        results.append(d)
        print(json.dumps({k: d.get(k) for k in ("attempt", "frozen", "probes", "threads", "cpu_times")},
                         ensure_ascii=False))
        if d["frozen"]:
            break
    (EV / "test9-hang.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print("RESULT frozen" if any(d["frozen"] for d in results) else "RESULT no-freeze")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
