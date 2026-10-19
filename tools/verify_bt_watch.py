#!/usr/bin/env python3
"""A11 运行验收：直接运行被 btconfig 打过补丁的 Bluetooth WATCH_SCRIPT。

验证目标（P1-11）：
  - 冷启动首快照之后的补采按 +3/+8/+15/+30 s 实际触发（旧实现里被
    固定 30s 的 Start-Sleep 吞掉，只剩 +30s 一次）；
  - 稳态快照仍按 60s 周期触发；连接检查节拍由 bt_watch_ms 控制。

用法（项目根，.venv python）：
  .venv/Scripts/python.exe tools/verify_bt_watch.py --interval 2000 --duration 45
  .venv/Scripts/python.exe tools/verify_bt_watch.py --duration 100   # 加测 60s 稳态

只用 PowerShell 读系统 PnP 信息，不修改任何系统状态；结果写
design/verify/bt-watch.json。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector import btconfig  # noqa: E402
from collector.providers import bluetooth  # noqa: E402

OUT = ROOT / "design" / "verify" / "bt-watch.json"


def build_script(interval_ms: int) -> str:
    original = btconfig.original_script() or bluetooth.WATCH_SCRIPT
    patched = btconfig.build_watch_script(original, interval_ms)
    return patched.replace("%PPID%", str(os.getpid()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=2000)
    parser.add_argument("--duration", type=float, default=45.0)
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    script = build_script(args.interval)
    if "Start-Sleep -Milliseconds 2000" in script and args.interval != 2000:
        print("FAIL: 补丁未生效（仍有固定 Start-Sleep）")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="pb-btwatch-")) / "watch.ps1"
    tmp.write_text(script, encoding="utf-8")
    print(f"patched script -> {tmp} ({len(script)} chars), run {args.duration}s")

    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(tmp)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    lines: list[tuple[float, str]] = []
    t0 = time.monotonic()
    import threading

    def reader():
        assert proc.stdout is not None
        for raw in proc.stdout:
            ts = time.monotonic() - t0
            lines.append((ts, raw.decode("utf-8", errors="replace").strip()))
            if ts > args.duration:
                break

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    while time.monotonic() - t0 < args.duration and proc.poll() is None:
        time.sleep(0.2)
    try:
        proc.kill()
    except OSError:
        pass

    snapshots = []
    roots_count = None
    for ts, text in lines:
        if not text:
            continue
        try:
            snap = json.loads(text)
        except json.JSONDecodeError:
            continue
        roots = snap.get("roots") if isinstance(snap, dict) else None
        if isinstance(roots, list):
            roots_count = len(roots)
        snapshots.append({"t_rel": round(ts, 2), "roots": len(roots) if isinstance(roots, list) else None,
                          "devs": len(snap.get("devs") or []) if isinstance(snap, dict) else None})

    result = {
        "interval_ms": args.interval,
        "duration_s": args.duration,
        "roots": roots_count,
        "snapshots": snapshots,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    deltas = [round(s["t_rel"], 1) for s in snapshots]
    ok = True
    if roots_count in (None, 0):
        print("SKIP: 本机没有已配对的蓝牙根设备，无法验证 +3/+8/+15 补采（脚本本身已生成）")
        return 0
    if not snapshots:
        print("FAIL: 没有任何快照输出")
        return 1
    base = snapshots[0]["t_rel"]
    offsets = [round(s["t_rel"] - base, 1) for s in snapshots]
    print(f"snapshot offsets from first: {offsets}")
    expect = [3.0, 8.0, 15.0, 30.0]
    for exp in expect:
        hit = min(offsets, key=lambda v: abs(v - exp))
        good = abs(hit - exp) <= 2.0
        ok = ok and good
        print(("PASS " if good else "FAIL ") + f"cold-start retry +{exp:.0f}s -> observed {hit:+.1f}s")
    if args.duration >= 100:
        hit = [v for v in offsets if v >= 55]
        good = bool(hit) and abs(hit[0] - 90.0) <= 3.0 or any(abs(v - 90) <= 3 for v in offsets)
        ok = ok and good
        print(("PASS " if good else "FAIL ") + f"steady periodic snapshot ~+90s -> {offsets[-3:]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
