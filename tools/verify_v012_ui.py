#!/usr/bin/env python3
"""v0.1.2 真机 UI 验收：真实采集器 + 真实设备，截图证明三处显示。

启动 release exe（不 mock），等待真实 LunaFury / FlyDigi 出现后截窗口：
  * LunaFury Luna33-W  transport=2.4g  -> 副标题不再是「未知连接」
  * left_s_src=spec                  -> 剩余时间显示「≈ X 小时 Y 分」
  * FlyDigi BS2PRO left_s_src=mains  -> 显示「外接供电」
输出 design/verify/v012-ui-real.png 与 v012-ui-app.log / v012-ui-collector.log。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_phase_a as base  # noqa: E402

ROOT = base.ROOT
OUT = ROOT / "design" / "verify"
DEFAULT_EXE = ROOT / "src-tauri" / "target" / "release" / "peripheral-battery.exe"


def main() -> int:
    from PIL import ImageGrab

    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=str(DEFAULT_EXE))
    parser.add_argument("--out", default="v012-ui-real.png")
    parser.add_argument("--log-prefix", default="v012-ui")
    args = parser.parse_args()
    exe = Path(args.exe)
    if not exe.is_file():
        print(f"FAIL exe missing: {exe}")
        return 1
    print(f"exe={exe}")
    app = base.AppRun("v012-ui")
    try:
        app.start(exe, mock=False, show=True, quit_after_ms=40000)
        pid = app.procs[0].pid
        hwnd = app.wait_window(pid, timeout=20)
        print(f"window={'found' if hwnd else 'MISSING'} pid={pid}")
        if not hwnd:
            return 1
        ready = base.wait_log(app.env, r"collector-state state=ready|hello protocol=", 25)
        print(f"collector_ready={bool(ready)}")
        # 真实设备：FlyDigi 蓝牙首快照即可，LunaFury 约 6s 出数
        time.sleep(9)
        rect = base.window_rect(hwnd)
        print(f"rect={rect}")
        ImageGrab.grab(bbox=rect).save(OUT / args.out)
        print(f"screenshot={OUT / args.out}")
        logs = Path(app.env["APPDATA"]) / "PeripheralBattery" / "logs"
        for name in ("app.log", "collector.log"):
            src = logs / name
            if src.is_file():
                dst = OUT / f"{args.log_prefix}-{name}"
                shutil.copyfile(src, dst)
                print(f"log={dst}")
        return 0 if ready else 1
    finally:
        app.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
