#!/usr/bin/env python3
"""Lead 验收脚本：真实 app + sidecar 的进程树 CPU / 内存 / 窗口可见性。

用法（在项目根，使用 .venv 的 python）：
  .venv/Scripts/python.exe tools/verify_runtime.py hidden  --duration 120
  .venv/Scripts/python.exe tools/verify_runtime.py visible --duration 20 --show
  .venv/Scripts/python.exe tools/verify_runtime.py hidden  --duration 60 --collector-args "--stdio --fake --scenario static"

它做四件事：
 1) 启动 src-tauri/target/debug/peripheral-battery.exe（或 --exe 指定），等待
    app.log 出现 setup complete / collector-state=ready，并把 spawn 路径、hello 记下来；
 2) 周期采样进程树（app + msedgewebview2* + collector*）的 CPU 时间与 RSS，
    输出单核百分比、内存合计、隐藏时主窗口是否可见；
 3) 用 PB_QUIT_AFTER_MS 让 app 走正常退出路径，检查是否有孤儿进程；
 4) --json 写出机器可读结果。

注意：本脚本只用于验收，不属于产品代码。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "src-tauri" / "target" / "debug" / "peripheral-battery.exe"
LOGDIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PeripheralBattery" / "logs"
APP_LOG = LOGDIR / "app.log"
COLLECTOR_LOG = LOGDIR / "collector.log"

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def visible_titled_windows(pid: int) -> list[str]:
    """该 pid 的可见、有标题的顶层窗口标题。"""
    out: list[str] = []

    def _cb(hwnd, _lparam):
        wpid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        out.append(buf.value)
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def descendants(pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(pid)
        return [root] + root.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def process_kind(proc: psutil.Process) -> str:
    try:
        name = (proc.name() or "").lower()
        cmd = " ".join(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return "gone"
    if name == "peripheral-battery.exe":
        return "app"
    if name == "msedgewebview2.exe":
        return "webview2"
    if "collector" in cmd or name == "collector.exe":
        return "collector"
    return "other:" + name


def snapshot_tree(pid: int) -> dict[int, dict]:
    snap: dict[int, dict] = {}
    for p in descendants(pid):
        try:
            with p.oneshot():
                snap[p.pid] = {
                    "kind": process_kind(p),
                    "cpu": sum(p.cpu_times()[:2]),  # user + system
                    "rss": p.memory_info().rss,
                    "name": p.name(),
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return snap


def read_new(path: Path, offset: int) -> tuple[str, int]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            data = f.read()
            return data, f.tell()
    except OSError:
        return "", offset


def parse_launch(log_text: str) -> dict:
    info: dict = {"spawn": None, "hello": None, "ready": False, "errors": []}
    for line in log_text.splitlines():
        if "sidecar spawn path=" in line:
            info["spawn"] = line.split("[INFO]", 1)[-1].strip()
        elif "hello protocol=" in line:
            info["hello"] = line.split("[INFO]", 1)[-1].strip()
        elif "collector-state state=ready" in line:
            info["ready"] = True
        elif "[ERROR]" in line or "[WARN]" in line:
            info["errors"].append(line.strip())
    return info


def parse_fps_probe(log_text: str) -> dict | None:
    """从 app.log 里取最后一条 `FPS_PROBE {...}`。"""
    found = None
    marker = "FPS_PROBE "
    for line in log_text.splitlines():
        idx = line.find(marker)
        if idx < 0:
            continue
        try:
            found = json.loads(line[idx + len(marker):])
        except json.JSONDecodeError:
            continue
    return found


def measure(exe: Path, duration: float, show: bool, collector_args: str | None,
            warmup: float, json_out: Path | None, fps: bool = False,
            shot: Path | None = None, fps_ms: int = 10000) -> int:
    if not exe.exists():
        print(f"FAIL: app exe 不存在: {exe}")
        return 2
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log_offset = APP_LOG.stat().st_size if APP_LOG.exists() else 0

    env = os.environ.copy()
    env["PB_LOG_LEVEL"] = "debug"
    # 让 app 自己在采样结束后走正常退出（留 12s 余量给 shutdown 握手）
    env["PB_QUIT_AFTER_MS"] = str(int((warmup + duration + 12) * 1000))
    if show:
        env["PB_SHOW_ON_START"] = "1"
    else:
        env.pop("PB_SHOW_ON_START", None)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        env.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        + (" --remote-debugging-port=9222" if (show and fps) else "")
    ).strip()
    if collector_args is not None:
        py = ROOT / ".venv" / "Scripts" / "python.exe"
        env["PB_COLLECTOR"] = f'"{py}" -m collector {collector_args}'
    else:
        env.pop("PB_COLLECTOR", None)
    env.pop("PB_PYTHON", None)
    if show and fps:
        # wry 会覆盖 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS（CDP 端口打不开），
        # 改用 Rust 内置的 PB_FPS_PROBE：面板显示后 500ms 在页面里测 rAF，
        # 通过 report_fps 命令写到 app.log 的 `FPS_PROBE {...}` 行。
        env["PB_FPS_PROBE"] = str(fps_ms)

    t0 = time.monotonic()
    proc = subprocess.Popen([str(exe)], cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"started pid={proc.pid} show={show} duration={duration}s")

    # 1) 等 collector ready（最多 8s）
    launch = {"ready": False, "spawn": None, "hello": None, "errors": []}
    launch_at = None
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        text, log_offset = read_new(APP_LOG, log_offset)
        parsed = parse_launch(text)
        for k in ("spawn", "hello"):
            if parsed[k]:
                launch[k] = parsed[k]
        launch["errors"].extend(parsed["errors"])
        if parsed["ready"]:
            launch["ready"] = True
            launch_at = time.monotonic()
            break
        time.sleep(0.1)

    # 2) 预热：等 WebView2 子进程稳定（把读到的日志累积下来，FPS 结果在预热期间写入）
    log_buf = ""
    target = t0 + warmup
    while time.monotonic() < target and proc.poll() is None:
        text, log_offset = read_new(APP_LOG, log_offset)
        log_buf += text
        time.sleep(0.5)

    hidden_probe = []
    if not show:
        for _ in range(3):
            hidden_probe.append(visible_titled_windows(proc.pid))
            time.sleep(0.4)

    # 2b) 可见模式：解析 Rust PB_FPS_PROBE 写进 app.log 的 `FPS_PROBE {...}`
    fps_result = None
    if show and fps:
        fps_result = parse_fps_probe(log_buf)
        if fps_result is None:
            text, log_offset = read_new(APP_LOG, log_offset)
            log_buf += text
            fps_result = parse_fps_probe(log_buf)
        if fps_result is None:
            fps_result = {"ok": False, "error": "app.log 没有 FPS_PROBE 行（检查 PB_FPS_PROBE / 面板是否显示）"}
        print("fps:", json.dumps(fps_result, ensure_ascii=False))

    # 2c) 桌面截图（包含 acrylic 与壁纸，需要用整屏抓取而不是只截 WebView）
    if shot:
        shot.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
            "$g=[System.Drawing.Graphics]::FromImage($bmp); "
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size); "
            f"$bmp.Save('{shot}',[System.Drawing.Imaging.ImageFormat]::Png); "
            "$g.Dispose(); $bmp.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=30)
        print("screenshot:", shot)

    # 3) 采样
    base = snapshot_tree(proc.pid)
    last_cpu = {pid: s["cpu"] for pid, s in base.items()}
    totals = {"app": 0.0, "webview2": 0.0, "collector": 0.0, "other": 0.0}
    samples = []
    start_sample = time.monotonic()
    next_sample = start_sample
    while time.monotonic() - start_sample < duration and proc.poll() is None:
        time.sleep(0.25)
        if time.monotonic() < next_sample:
            continue
        next_sample += 5.0
        snap = snapshot_tree(proc.pid)
        interval = {k: 0.0 for k in ("app", "webview2", "collector", "other")}
        for pid, s in snap.items():
            prev = last_cpu.get(pid, 0.0)
            delta = max(0.0, s["cpu"] - prev)
            kind = s["kind"]
            key = kind if kind in interval else "other"
            interval[key] += delta
            totals[key] += delta
        last_cpu = {pid: s["cpu"] for pid, s in snap.items()}
        mem = sum(s["rss"] for s in snap.values())
        now = time.monotonic()
        samples.append({
            "t": round(now - start_sample, 2),
            "cpu": {k: round(v / 5.0 * 100, 3) for k, v in interval.items()},
            "rss_mb": round(mem / 1e6, 1),
            "procs": {k: sum(1 for s in snap.values() if s["kind"] == k)
                      for k in ("app", "webview2", "collector")},
        })
        print(f"  t={now - start_sample:6.1f}s cpu={samples[-1]['cpu']} rss={samples[-1]['rss_mb']}MB procs={samples[-1]['procs']}")

    elapsed = max(1e-6, time.monotonic() - start_sample)
    cpu_pct = {k: round(v / elapsed * 100, 3) for k, v in totals.items()}
    end_snap = snapshot_tree(proc.pid)
    rss_mb = round(sum(s["rss"] for s in end_snap.values()) / 1e6, 1)
    breakdown = sorted(
        ({"pid": pid, "name": s["name"], "kind": s["kind"],
          "rss_mb": round(s["rss"] / 1e6, 1)} for pid, s in end_snap.items()),
        key=lambda x: -x["rss_mb"])
    peak_rss = round(max([s["rss_mb"] for s in samples] + [rss_mb]), 1)

    # 4) 正常退出 + 孤儿检查
    print("waiting for app self-quit ...")
    try:
        proc.wait(timeout=duration + 40)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        exit_code = None
    time.sleep(1.5)
    leftovers = []
    my_pid = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline", "exe"]):
        if p.info["pid"] == my_pid:
            continue
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            name = (p.info.get("name") or "").lower()
            exe_path = (p.info.get("exe") or "").lower()
            is_collector = (
                name == "collector.exe"
                or (name.startswith("python") and "-m collector" in cmd
                    and "verify_runtime" not in cmd))
            if is_collector and (str(ROOT).lower().replace("\\", "/") in (exe_path + cmd).lower().replace("\\", "/")):
                leftovers.append({"pid": p.info["pid"], "cmd": cmd[:200]})
            if name == "peripheral-battery.exe" and str(ROOT).lower().replace("\\", "/") in exe_path.replace("\\", "/"):
                leftovers.append({"pid": p.info["pid"], "cmd": cmd[:200]})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    result = {
        "exe": str(exe),
        "duration_s": round(elapsed, 1),
        "show": show,
        "launch": launch,
        "hidden_window_probe": hidden_probe,
        "fps": fps_result,
        "screenshot": str(shot) if shot else None,
        "cpu_single_core_pct": cpu_pct,
        "rss_mb": rss_mb,
        "peak_rss_mb": peak_rss,
        "rss_breakdown": breakdown,
        "samples": samples,
        "app_exit_code": exit_code,
        "leftovers": leftovers,
    }
    print("\n=== RESULT ===")
    print(json.dumps({k: result[k] for k in
                      ("exe", "show", "launch", "hidden_window_probe", "fps",
                       "cpu_single_core_pct", "rss_mb", "peak_rss_mb",
                       "rss_breakdown",
                       "app_exit_code", "leftovers")},
                     ensure_ascii=False, indent=2))
    if json_out:
        json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved: {json_out}")
    return 0 if exit_code == 0 and not leftovers else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["hidden", "visible"])
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--warmup", type=float, default=12.0)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--exe", default=str(DEFAULT_EXE))
    ap.add_argument("--collector-args", default=None,
                    help='例如 "--stdio --fake --scenario static --speed 1"')
    ap.add_argument("--fps", action="store_true", help="可见模式下用 PB_FPS_PROBE 测 rAF 帧时间")
    ap.add_argument("--fps-ms", type=int, default=10000)
    ap.add_argument("--shot", default=None, help="可见模式下抓整屏 PNG 路径")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    show = args.show or args.mode == "visible"
    return measure(Path(args.exe), args.duration, show, args.collector_args,
                   args.warmup, Path(args.json) if args.json else None, args.fps,
                   Path(args.shot) if args.shot else None, args.fps_ms)


if __name__ == "__main__":
    raise SystemExit(main())
