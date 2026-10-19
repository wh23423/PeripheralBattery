#!/usr/bin/env python3
"""PeripheralBattery 面板拖拽原生验收（Lead/前端专用，不属于产品代码）。

用户 bug：「点开后有时候会无法拖动」——只有 header 是 data-tauri-drag-region，
hero 大卡 / hero 名称 / 列表空白 / footer 等大面积区域拖不动。修复后整个 #panel
都是 deep 拖拽区（交互元素由 Tauri drag.js 自动排除）。

本脚本用 Win32 真实鼠标输入（非合成 DOM 事件）验证：
  MUST MOVE    header 空白 (460,30)、hero 卡 (120,120)、hero 名称 (120,330)、
               列表空白 (500,330)
  MUST NOT MOVE 设备行 (500,140)、控制按钮 (650,32)
并覆盖「第二实例唤出面板后立刻拖动」。

用法（项目根）：
  .venv/Scripts/python.exe tools/verify_drag.py \
      --exe src-tauri/target/release/peripheral-battery.exe
  # 修复前基线（期望 hero/blank FAIL）：
  .venv/Scripts/python.exe tools/verify_drag.py --out design/verify/drag-native-before.json

每个用例使用独立临时 APPDATA/LOCALAPPDATA，启动 mock collector 并 PB_SHOW_ON_START=1。
窗口坐标 = 窗口左上角 + CSS 点 × DPI 缩放；用例之间用 SetWindowPos 还原窗口位置。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:  # Windows 控制台默认 GBK，中文 PASS/FAIL 明细需 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_phase_a as base  # noqa: E402  复用 Win32 绑定 / AppRun / 日志等待

ROOT = base.ROOT
OUT_DIR = base.OUT_DIR
DEFAULT_EXE = ROOT / "src-tauri" / "target" / "release" / "peripheral-battery.exe"

user32 = base.user32

user32.SetWindowPos.argtypes = [
    wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT
]
user32.SetWindowPos.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    return ok


# ---------------------------------------------------------------------------
# 鼠标 / 窗口
# ---------------------------------------------------------------------------

def set_pos(hwnd, x: int, y: int) -> None:
    user32.SetWindowPos(hwnd, None, int(x), int(y), 0, 0,
                        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(0.3)


def drag(hwnd, css_x: float, css_y: float, css_dx: float, css_dy: float,
         steps: int = 14) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """从窗口内 CSS 点 (css_x,css_y) 按住左键移动 (css_dx,css_dy)，返回拖动前后窗口矩形。"""
    scale = base.dpi_scale(hwnd)
    left, top, _, _ = base.window_rect(hwnd)
    x0 = left + round(css_x * scale)
    y0 = top + round(css_y * scale)
    before = base.window_rect(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    user32.SetCursorPos(x0, y0)
    time.sleep(0.12)
    user32.mouse_event(base.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    for i in range(1, steps + 1):
        user32.SetCursorPos(x0 + round(css_dx * scale * i / steps),
                            y0 + round(css_dy * scale * i / steps))
        time.sleep(0.025)
    time.sleep(0.1)
    user32.mouse_event(base.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.5)
    return before, base.window_rect(hwnd)


def is_expected_move(before, after, css_dx: float, css_dy: float, scale: float) -> bool:
    dx = after[0] - before[0]
    dy = after[1] - before[1]
    return abs(dx - round(css_dx * scale)) <= 14 and abs(dy - round(css_dy * scale)) <= 8


def wait_ready(app, timeout: float = 20.0) -> bool:
    ok = base.wait_log(app.env, r"collector-state state=ready|hello protocol=", timeout)
    return ok is not None


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

# (名称, CSS x, CSS y)
MUST_MOVE = [
    ("header 空白", 460, 30),
    ("hero 大卡", 120, 120),
    ("hero 名称", 120, 330),
    ("列表空白", 500, 330),
]
MUST_NOT_MOVE = [
    ("设备行", 500, 140),
    ("控制按钮", 650, 32),
]

DRAG_DX = -90.0
DRAG_DY = 0.0


def case_panel_drag(exe: Path) -> None:
    app = base.AppRun("drag")
    try:
        proc = app.start(exe, mock=True, show=True, quit_after_ms=120000)
        hwnd = app.wait_window(proc.pid, 20)
        if not hwnd:
            check("drag: 找到主窗口", False)
            return
        shown = base.wait_log(app.env, r"window shown reason=", 20)
        check("drag: 面板已显示", shown is not None, (shown or "")[:120])
        check("drag: mock collector ready", wait_ready(app), "")
        time.sleep(0.8)

        user32.SetForegroundWindow(hwnd)
        scale = base.dpi_scale(hwnd)
        origin = base.window_rect(hwnd)
        check("drag: 窗口 = 面板 720×380 DIP",
              abs((origin[2] - origin[0]) - round(720 * scale)) <= 2
              and abs((origin[3] - origin[1]) - round(380 * scale)) <= 2,
              f"rect={origin} scale={scale:.3f}")

        for name, x, y in MUST_MOVE:
            set_pos(hwnd, origin[0], origin[1])
            before, after = drag(hwnd, x, y, DRAG_DX, DRAG_DY)
            check(f"drag: {name} ({x},{y}) 可拖动窗口",
                  is_expected_move(before, after, DRAG_DX, DRAG_DY, scale),
                  f"{before} -> {after} scale={scale:.3f}")

        for name, x, y in MUST_NOT_MOVE:
            set_pos(hwnd, origin[0], origin[1])
            before, after = drag(hwnd, x, y, DRAG_DX, DRAG_DY)
            check(f"drag: {name} ({x},{y}) 不拖动窗口（可交互元素）",
                  before == after,
                  f"{before} -> {after}")

        # 位置还原能力自检：后面的用例依赖它
        set_pos(hwnd, origin[0], origin[1])
        restored = base.window_rect(hwnd)
        check("drag: 用例间窗口位置已还原",
              restored[0] == origin[0] and restored[1] == origin[1],
              f"origin={origin} restored={restored}")
    finally:
        app.cleanup()


def case_after_show(exe: Path) -> None:
    """用户 bug 场景：面板被第二实例唤出后，立刻拖动（header 与 hero 大卡都要能动）。"""
    app = base.AppRun("drag-after-show")
    try:
        hidden = app.start(exe, mock=True, show=False, quit_after_ms=90000)
        if not wait_ready(app):
            check("drag-show: mock collector ready", False)
            return
        app.second_instance(exe)
        shown = base.wait_log(app.env, r"window shown reason=single-instance", 15)
        hwnd = app.wait_window(hidden.pid, 10)
        check("drag-show: 第二实例唤出面板", shown is not None and hwnd is not None,
              (shown or "")[:120])
        if not hwnd:
            return
        # 不等待：面板刚显示就拖（还原「点开后有时候无法拖动」的时序）
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not user32.IsWindowVisible(hwnd):
            time.sleep(0.02)
        scale = base.dpi_scale(hwnd)
        origin = base.window_rect(hwnd)
        # 第一拖：header 空白（最贴近「唤出后立刻拖」的时序，不额外等待）
        before, after = drag(hwnd, 460, 30, DRAG_DX, DRAG_DY)
        check("drag-show: 刚唤出后 header 空白立即可拖",
              is_expected_move(before, after, DRAG_DX, DRAG_DY, scale),
              f"{before} -> {after} scale={scale:.3f}")
        set_pos(hwnd, origin[0], origin[1])
        # 第二拖：hero 大卡（用户报 bug 的抓取位置）
        before, after = drag(hwnd, 120, 120, DRAG_DX, DRAG_DY)
        check("drag-show: 刚唤出后 hero 大卡立即可拖",
              is_expected_move(before, after, DRAG_DX, DRAG_DY, scale),
              f"{before} -> {after} scale={scale:.3f}")
    finally:
        app.cleanup()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=str(DEFAULT_EXE))
    parser.add_argument("--out", default=str(OUT_DIR / "drag-native.json"))
    parser.add_argument("--case", nargs="*", default=["panel", "show"],
                        choices=["panel", "show", "all"],
                        help="默认跑全部（panel + show）")
    args = parser.parse_args()
    exe = Path(args.exe)
    if not exe.is_file():
        print(f"FAIL: exe 不存在: {exe}")
        return 2
    cases = ["panel", "show"] if "all" in args.case else args.case
    t0 = time.time()
    if "panel" in cases:
        print("\n========== panel drag ==========")
        try:
            case_panel_drag(exe)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check("drag: 用例无异常", False, str(e))
    if "show" in cases:
        print("\n========== drag right after show ==========")
        try:
            case_after_show(exe)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check("drag-show: 用例无异常", False, str(e))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "exe": str(exe),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 1),
        "drag_css": {"dx": DRAG_DX, "dy": DRAG_DY},
        "must_move": [{"name": n, "x": x, "y": y} for n, x, y in MUST_MOVE],
        "must_not_move": [{"name": n, "x": x, "y": y} for n, x, y in MUST_NOT_MOVE],
        "results": RESULTS,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n===== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed, "
          f"{len(failed)} failed · {args.out} =====")
    for r in failed:
        print("  FAIL:", r["check"], "|", r["detail"])
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
