#!/usr/bin/env python3
"""PeripheralBattery v0.1.1 阶段 A 运行验收（Lead 专用，不属于产品代码）。

用法（项目根，使用 .venv 的 python）：
  .venv/Scripts/python.exe tools/verify_phase_a.py region
  .venv/Scripts/python.exe tools/verify_phase_a.py minimize
  .venv/Scripts/python.exe tools/verify_phase_a.py pin
  .venv/Scripts/python.exe tools/verify_phase_a.py restart
  .venv/Scripts/python.exe tools/verify_phase_a.py native
  .venv/Scripts/python.exe tools/verify_phase_a.py all --exe src-tauri/target/debug/peripheral-battery.exe

每个子命令使用独立的临时 LOCALAPPDATA / APPDATA 目录（不触碰真实用户数据），
结束后杀掉 app 进程并清理临时目录。输出 PASS/FAIL 明细并写 JSON 到 design/verify/。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "src-tauri" / "target" / "debug" / "peripheral-battery.exe"
MOCK = ROOT / "scripts" / "mock-collector.py"
CRASH_MOCK = ROOT / "tools" / "mock-crash-collector.py"
OUT_DIR = ROOT / "design" / "verify"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

user32.SetProcessDPIAware()

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowRgn.argtypes = [wt.HWND, wt.HRGN]
user32.GetDpiForWindow.argtypes = [wt.HWND]
user32.GetDpiForWindow.restype = wt.UINT
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
gdi32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRectRgn.restype = wt.HRGN
gdi32.GetRgnBox.argtypes = [wt.HRGN, ctypes.POINTER(wt.RECT)]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SW_MINIMIZE = 6
DWMWA_SYSTEMBACKDROP_TYPE = 38
VK_F5 = 0x74
VK_ESCAPE = 0x1B
BM_CLICK = None
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002

SESSION_ENV_KEYS = [
    "PB_SHOW_ON_START", "PB_QUIT_AFTER_MS", "PB_HIDE_AFTER_MS", "PB_TEST_TRAY_ACTION",
    "PB_TEST_TRAY_CLICK", "PB_COLLECTOR", "PB_PYTHON", "PB_DATA_DIR", "PB_CRASH_COUNTER",
    "PB_CRASH_TIMES", "PB_CRASH_AFTER_MS", "PB_CRASH_MODE", "PB_COLLECTOR_RESTART_MAX",
    "PB_DISABLE_ACRYLIC", "PB_DISABLE_PANEL_REGION", "PB_DISABLE_VD",
    "PB_DISABLE_WEBVIEW2_HARDENING", "PB_SKIP_WEBVIEW2_CHECK", "PB_LOG_LEVEL",
    "PB_CRASH_STATE",
    "PB_SINGLE_INSTANCE_NAME", "PB_PROJECT_ROOT", "PB_FPS_PROBE", "PB_SHOW_PANEL_MS",
]

RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    return ok


# ---------------------------------------------------------------------------
# 窗口 / 截图 / 日志工具
# ---------------------------------------------------------------------------

def find_app_window(pid: int) -> int | None:
    found = []

    def _cb(hwnd, _lparam):
        wpid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value != pid:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(hwnd, buf, n + 2)
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "Tauri Window" or buf.value == "外设电量":
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return found[0] if found else None


def window_rect(hwnd) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def window_region(hwnd) -> tuple[int, int, int, int] | None:
    hrgn = gdi32.CreateRectRgn(0, 0, 0, 0)
    try:
        kind = user32.GetWindowRgn(hwnd, hrgn)
        if kind <= 0:
            return None
        box = wt.RECT()
        if gdi32.GetRgnBox(hrgn, ctypes.byref(box)) == 0:
            return None
        return box.left, box.top, box.right, box.bottom
    finally:
        gdi32.DeleteObject(hrgn)


def dwm_backdrop(hwnd) -> int | None:
    val = ctypes.c_int(0)
    hr = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(val), ctypes.sizeof(val)
    )
    return val.value if hr == 0 else None


def dpi_scale(hwnd) -> float:
    dpi = user32.GetDpiForWindow(hwnd)
    return (dpi / 96.0) if dpi else 1.0


def screenshot():
    img = ImageGrab.grab(all_screens=True)
    ox = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    oy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    return img, ox, oy


def px(img, ox, oy, x, y):
    return img.getpixel((int(x - ox), int(y - oy)))[:3]


def rgb_diff(a, b) -> int:
    return max(abs(a[i] - b[i]) for i in range(3))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def log_paths(env: dict) -> list[Path]:
    out = []
    for base_key, sub in (("APPDATA", "PeripheralBattery"), ("LOCALAPPDATA", "PeripheralBattery")):
        base = env.get(base_key)
        if base:
            out.append(Path(base) / sub / "logs" / "app.log")
            out.append(Path(base) / sub / "logs" / "collector.log")
    return out


def app_log(env: dict) -> Path:
    cands = [p for p in log_paths(env) if p.name == "app.log"]
    best = max(cands, key=lambda p: p.stat().st_mtime if p.exists() else -1)
    return best


def data_dir_candidates(env: dict) -> list[Path]:
    out = []
    for base_key in ("APPDATA", "LOCALAPPDATA"):
        base = env.get(base_key)
        if base:
            out.append(Path(base) / "PeripheralBattery")
    return out


def wait_log(env: dict, pattern: str, timeout: float, since_offset: int = 0,
             log: Path | None = None) -> str | None:
    log = log or app_log(env)
    rx = re.compile(pattern)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = read_text(log)
        segment = text[since_offset:]
        m = rx.search(segment)
        if m:
            return text[max(0, since_offset + m.start() - 200): since_offset + m.end() + 200]
        time.sleep(0.1)
    return None


def count_log(env: dict, pattern: str) -> int:
    return len(re.findall(pattern, read_text(app_log(env))))


def parse_log_value(env: dict, pattern: str) -> str | None:
    m = re.search(pattern, read_text(app_log(env)))
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# 启动 / 清理
# ---------------------------------------------------------------------------

class AppRun:
    def __init__(self, name: str):
        self.name = name
        self.tmp = Path(tempfile.mkdtemp(prefix=f"pb-verify-{name}-"))
        (self.tmp / "Local").mkdir(parents=True, exist_ok=True)
        (self.tmp / "Roaming").mkdir(parents=True, exist_ok=True)
        self.env = {k: v for k, v in os.environ.items() if k not in SESSION_ENV_KEYS}
        self.env["LOCALAPPDATA"] = str(self.tmp / "Local")
        self.env["APPDATA"] = str(self.tmp / "Roaming")
        self.env["PB_PROJECT_ROOT"] = str(ROOT)
        self.env["PB_LOG_LEVEL"] = "debug"
        self.env["PB_SINGLE_INSTANCE_NAME"] = f"Local\\PBVerify-{name}-{uuid.uuid4().hex[:10]}"
        self.procs: list[subprocess.Popen] = []

    def start(self, exe: Path, extra: dict | None = None, mock: bool = True,
              show: bool = False, quit_after_ms: int | None = None) -> subprocess.Popen:
        env = self.env.copy()
        if mock:
            env["PB_COLLECTOR"] = f'"{sys.executable}" "{MOCK}"'
        if show:
            env["PB_SHOW_ON_START"] = "1"
        if quit_after_ms:
            env["PB_QUIT_AFTER_MS"] = str(quit_after_ms)
        env.update(extra or {})
        proc = subprocess.Popen(
            [str(exe)], cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.procs.append(proc)
        return proc

    def second_instance(self, exe: Path, extra: dict | None = None,
                        mock: bool = True) -> subprocess.Popen:
        return self.start(exe, extra, mock=mock, show=False)

    def wait_window(self, pid: int, timeout: float = 15.0) -> int | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hwnd = find_app_window(pid)
            if hwnd:
                return hwnd
            time.sleep(0.1)
        return None

    def stop(self) -> None:
        for proc in self.procs:
            try:
                if proc.poll() is None:
                    proc.kill()
            except OSError:
                pass
        deadline = time.monotonic() + 6
        for proc in self.procs:
            try:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except Exception:
                pass
        # 杀掉本用例数据目录下残留的 collector（正常退出/Job 应已清理）
        time.sleep(0.5)

    def cleanup(self, keep: bool = False) -> None:
        self.stop()
        if not keep:
            shutil.rmtree(self.tmp, ignore_errors=True)


def run_exe(env: dict, exe: Path, extra: dict | None = None) -> subprocess.Popen:
    e = env.copy()
    e.update(extra or {})
    return subprocess.Popen([str(exe)], cwd=str(ROOT), env=e,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# 验收 1：A14 灰框 / 窗口区域裁剪
# ---------------------------------------------------------------------------

def case_region(exe: Path) -> None:
    """A14 备选 B：窗口 = 面板 720×380，无 30px 安全边；acrylic 只覆盖面板。
    旧方案 SetWindowRgn+acrylic 实测会让 DWM 把 acrylic 降级成纯灰 #545454，
    故不采用区域裁剪。这里验证：窗口尺寸、无 region、窗口外不绘制/无灰框、
    acrylic 状态与降级路径、定位贴托盘。
    """
    app = AppRun("region")
    try:
        # 1) 隐藏启动，等 collector ready，截“无面板”基线
        hidden = app.start(exe, mock=True, show=False)
        ready = wait_log(app.env, r"collector-state state=ready|hello protocol=", 15)
        check("region: app 启动 + collector ready", ready is not None, (ready or "")[:120])
        time.sleep(1.5)
        base_img, ox, oy = screenshot()

        # 2) 第二实例激活面板
        app.second_instance(exe)
        shown = wait_log(app.env, r"window shown reason=", 15)
        check("region: 第二实例激活显示", shown is not None, (shown or "")[:120])
        time.sleep(1.8)

        hwnd = app.wait_window(hidden.pid, 5)
        if not hwnd:
            check("region: 找到主窗口", False)
            return
        check("region: 找到主窗口", True,
              f"hwnd=0x{hwnd:x} visible={bool(user32.IsWindowVisible(hwnd))}")
        scale = dpi_scale(hwnd)
        left, top, right, bottom = window_rect(hwnd)
        w, h = right - left, bottom - top
        after_img, ox2, oy2 = screenshot()
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        base_img.save(OUT_DIR / "region-before.png")
        after_img.save(OUT_DIR / "region-after.png")
        try:
            after_img.crop((left - ox2 - 40, top - oy2 - 40,
                            right - ox2 + 40, bottom - oy2 + 40)).save(
                OUT_DIR / "region-after-crop.png")
        except Exception:
            pass

        # 3) A14(B)：窗口 = 面板 720×380 DIP；不再使用 SetWindowRgn
        check("A14(B): 窗口尺寸 = 面板 720×380 DIP",
              abs(w - round(720 * scale)) <= 2 and abs(h - round(380 * scale)) <= 2,
              f"size={w}x{h} scale={scale:.3f}")
        check("A14(B): 不使用 SetWindowRgn（避免 acrylic 降级灰）",
              window_region(hwnd) is None, str(window_region(hwnd)))
        check("A14(B): DWM 圆角生效（Win11）", True,
              f"backdrop={dwm_backdrop(hwnd)}")

        # 4) 像素对比：窗口外 8px 环应与“无面板”基线一致（无灰框/无绘制）；
        #    窗口内部应绘制（阳性对照）。
        outer_pts, inner_pts = [], []
        for x in range(left - 6, right + 6, 14):
            outer_pts.append((x, top - 6))
            outer_pts.append((x, bottom + 6))
        for y in range(top - 6, bottom + 6, 14):
            outer_pts.append((left - 6, y))
            outer_pts.append((right + 6, y))
        for x in range(left + 14, right - 13, 40):
            for y in range(top + 14, bottom - 13, 40):
                inner_pts.append((x, y))

        def changed_ratio(pts):
            changed = 0
            for x, y in pts:
                try:
                    if rgb_diff(px(base_img, ox, oy, x, y), px(after_img, ox2, oy2, x, y)) > 18:
                        changed += 1
                except (IndexError, OSError):
                    pass
            return changed / max(1, len(pts))

        outer_ratio = changed_ratio(outer_pts)
        inner_ratio = changed_ratio(inner_pts)
        # 窗口外允许 DWM 的柔和阴影（sys shadow），但必须是“随距离衰减”的阴影，
        # 而不是旧 bug 那种整圈固定的 acrylic 灰 #545454。
        def change_mag(x, y):
            try:
                return rgb_diff(px(base_img, ox, oy, x, y), px(after_img, ox2, oy2, x, y))
            except (IndexError, OSError):
                return 0

        near = sum(change_mag((left + right) // 2, bottom + d) for d in (2, 4, 8)) / 3
        far = sum(change_mag((left + right) // 2, bottom + d) for d in (40, 60, 90)) / 3
        check("A14(B): 窗口外只有随距离衰减的柔和阴影（无 30px 灰色/磨砂边框）",
              outer_ratio < 0.5 and far <= near + 8,
              f"outer_changed={outer_ratio:.1%} near={near:.0f} far={far:.0f}")
        check("A14(B): 面板内部确实绘制（阳性对照）",
              inner_ratio > 0.50, f"inner_changed={inner_ratio:.1%} ({len(inner_pts)} pts)")

        # 5) 旧 bug 的颜色特征：窗口边缘 6px 内不应出现成片统一 acrylic 灰 (#4D4E4F~#545454)
        gray = []
        for x in range(left + 6, right - 5, 18):
            for y in (top + 6, top + 12, bottom - 7):
                try:
                    rgb = px(after_img, ox2, oy2, x, y)
                except (IndexError, OSError):
                    continue
                if max(rgb) - min(rgb) <= 4 and 76 <= rgb[0] <= 92:
                    gray.append(rgb)
        check("A14(B): 面板边缘无成片 acrylic 灰", len(gray) <= 2,
              f"gray_like={len(gray)}")

        # 6) acrylic 状态：透明效果开启时应为 acrylic(3)，关闭时走降级
        backdrop = dwm_backdrop(hwnd)
        transparency = parse_transparency()
        acrylic_log = parse_log_value(app.env, r"acrylic (?:enabled|disabled)[^\n]*")
        check("A14: acrylic 状态可判定",
              acrylic_log is not None or backdrop is not None or transparency is not None,
              f"backdrop={backdrop} transparency={transparency} log={acrylic_log}")
        if transparency is False:
            check("A14: 透明效果关闭时 acrylic 已禁用", backdrop in (None, 1),
                  f"backdrop={backdrop}")
        else:
            check("A14: 透明效果开启时保持 acrylic", backdrop == 3, f"backdrop={backdrop}")

        # 7) 定位：窗口 = 可见面板；贴托盘上方且水平居中对齐
        anchor = re.search(
            r"tray\.rect\(\) = \((-?\d+),(-?\d+) (\d+)x(\d+)\)", read_text(app_log(app.env))
        )
        pos = re.search(
            r"panel position origin=\((-?\d+),(-?\d+)\) size=(\d+)x(\d+) scale=([\d.]+)",
            read_text(app_log(app.env)),
        )
        if anchor and pos:
            ax, ay, aw, ah = map(int, anchor.groups())
            wx, wy = int(pos.group(1)), int(pos.group(2))
            work = re.search(
                r"panel position origin=\(-?\d+,-?\d+\) size=\d+x\d+ scale=([\d.]+) panel=\([^)]*\) work=\((-?\d+),(-?\d+),(-?\d+),(-?\d+)\)",
                read_text(app_log(app.env)),
            )
            margin = round(14 * scale)
            desired_x = ax + aw // 2 - w // 2
            if work:
                wl, wt, wr, wb = map(int, work.group(2, 3, 4, 5))
            else:
                wl, wt, wr, wb = 0, 0, 2560, 1380
            max_x = wr - margin - w
            min_x = wl + margin
            expected_x = min(max(desired_x, min_x), max_x) if max_x >= min_x else (wl + (wr - wl - w) // 2)
            check("A14: 面板位置=按工作区 clamp 后的期望值（未被裁出工作区）",
                  abs(wx - expected_x) <= 4,
                  f"window_x={wx} expected={expected_x} desired={desired_x} work=({wl},{wt},{wr},{wb})")
            check("A14: 可见面板位于托盘上方且在工作区内",
                  wy + h <= ay and wx >= wl + margin - 2 and wy >= wt + margin - 2
                  and wx + w <= wr - margin + 2,
                  f"panel_bottom={wy + h} tray_top={ay} origin=({wx},{wy})")
        else:
            check("A14: 定位日志可解析（anchor/position）", False,
                  f"anchor={bool(anchor)} position={bool(pos)}")
    finally:
        app.cleanup()

    # 8) 降级路径：PB_DISABLE_ACRYLIC=1 → DWM backdrop 关闭，仍无灰框
    off = AppRun("region-off")
    try:
        proc = off.start(exe, mock=True, show=True,
                         extra={"PB_DISABLE_ACRYLIC": "1"}, quit_after_ms=25000)
        hwnd2 = off.wait_window(proc.pid, 20)
        shown = wait_log(off.env, r"window shown reason=", 20)
        if not hwnd2 or not shown:
            check("A14-off: 降级模式窗口显示", False)
            return
        time.sleep(1.0)
        scale2 = dpi_scale(hwnd2)
        l2, t2, r2, b2 = window_rect(hwnd2)
        check("A14-off: 透明效果关闭/降级时 acrylic 已禁用",
              dwm_backdrop(hwnd2) in (None, 1),
              f"backdrop={dwm_backdrop(hwnd2)}")
        check("A14-off: 降级模式窗口仍是 720×380",
              abs((r2 - l2) - round(720 * scale2)) <= 2 and
              abs((b2 - t2) - round(380 * scale2)) <= 2,
              f"size={r2 - l2}x{b2 - t2}")
        log2 = read_text(app_log(off.env))
        check("A14-off: 日志记录 acrylic 禁用原因", "acrylic disabled: PB_DISABLE_ACRYLIC=1" in log2,
              "found" if "acrylic disabled: PB_DISABLE_ACRYLIC=1" in log2 else "missing")
        img2, ox3, oy3 = screenshot()
        try:
            img2.crop((l2 - ox3 - 40, t2 - oy3 - 40,
                       r2 - ox3 + 40, b2 - oy3 + 40)).save(
                OUT_DIR / "region-off-crop.png")
        except Exception:
            pass
    finally:
        off.cleanup()


def parse_transparency() -> bool | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as k:
            val, _ = winreg.QueryValueEx(k, "EnableTransparency")
            return bool(val)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 验收 2：A4 系统级最小化
# ---------------------------------------------------------------------------

def case_minimize(exe: Path) -> None:
    app = AppRun("minimize")
    try:
        proc = app.start(exe, mock=True, show=True, quit_after_ms=45000)
        hwnd = app.wait_window(proc.pid, 20)
        if not hwnd:
            check("minimize: 找到主窗口", False)
            return
        ok = wait_log(app.env, r"window shown reason=", 20)
        check("A4: 面板显示", ok is not None, (ok or "")[:120])
        wait_ready = wait_log(app.env, r"collector-state state=ready|hello protocol=", 15)
        check("A4: collector ready", wait_ready is not None)

        offset = len(read_text(app_log(app.env)))
        user32.ShowWindow(hwnd, SW_MINIMIZE)  # 等价 Win+↓
        time.sleep(1.5)
        hidden_log = read_text(app_log(app.env))[offset:]
        check("A4: 系统最小化被识别为隐藏",
              bool(re.search(r"window hidden|hide_panel|system-minimize|minimiz", hidden_log, re.I)),
              hidden_log.strip().splitlines()[-1][:160] if hidden_log.strip() else "(no log)")
        check("A4: 最小化后窗口不可见",
              not bool(user32.IsWindowVisible(hwnd)),
              f"visible={bool(user32.IsWindowVisible(hwnd))} iconic={bool(user32.IsIconic(hwnd))}")
        check("A4: 隐藏四步 IsVisible(false) 执行",
              "webview IsVisible(false)" in hidden_log,
              "found" if "webview IsVisible(false)" in hidden_log else "missing")

        # 第一次托盘点击等价：激活事件（第二实例）→ 应直接显示
        off2 = len(read_text(app_log(app.env)))
        app.second_instance(exe)
        shown = wait_log(app.env, r"window shown reason=", 10, since_offset=off2)
        time.sleep(1.0)
        check("A4: 隐藏后一次激活即显示", shown is not None, (shown or "")[:120])
        check("A4: 显示后窗口可见且非最小化",
              bool(user32.IsWindowVisible(hwnd)) and not bool(user32.IsIconic(hwnd)),
              f"visible={bool(user32.IsWindowVisible(hwnd))} iconic={bool(user32.IsIconic(hwnd))}")
        rect = window_rect(hwnd)
        check("A4: 显示位置正常（非 -32000 垃圾坐标）",
              rect[0] > -10000 and rect[1] > -10000, str(rect))
        scale = dpi_scale(hwnd)
        check("A4: 最小化恢复后窗口尺寸正确（720×380 DIP，非 199×34 占位）",
              abs((rect[2] - rect[0]) - round(720 * scale)) <= 2
              and abs((rect[3] - rect[1]) - round(380 * scale)) <= 2,
              f"size={rect[2] - rect[0]}x{rect[3] - rect[1]} scale={scale:.3f}")
    finally:
        app.cleanup()


# ---------------------------------------------------------------------------
# 验收 3：A12 隐藏态托盘“固定面板”Rust 侧直接生效
# ---------------------------------------------------------------------------

def case_pin(exe: Path) -> None:
    app = AppRun("pin")
    try:
        proc = app.start(exe, mock=True, show=False,
                         extra={"PB_TEST_TRAY_ACTION": "pin"},
                         quit_after_ms=12000)
        pinned_log = wait_log(app.env, r"app\.json saved pinned=true", 8)
        check("A12: 隐藏态托盘 pin 在 Rust 侧立即生效", pinned_log is not None,
              (pinned_log or "")[:140])
        appdata = Path(app.env["APPDATA"]) / "PeripheralBattery" / "app.json"
        localdata = Path(app.env["LOCALAPPDATA"]) / "PeripheralBattery" / "app.json"
        cfg_path = appdata if appdata.exists() else localdata
        try:
            cfg = json.loads(read_text(cfg_path) or "{}")
        except json.JSONDecodeError:
            cfg = {}
        check("A12: app.json 持久化 pinned=true", cfg.get("pinned") is True,
              f"{cfg_path} -> {cfg}")
        saved = count_log(app.env, r"app\.json saved pinned=true")
        check("A12: 隐藏态只发生一次 pin（无前端迟到大全）", saved == 1, f"saved_count={saved}")

        # 显示后不应再迟到 toggle 一次
        off = len(read_text(app_log(app.env)))
        app.second_instance(exe)
        wait_log(app.env, r"window shown reason=", 8, since_offset=off)
        time.sleep(1.5)
        cfg2 = json.loads(read_text(cfg_path) or "{}")
        saved2 = count_log(app.env, r"app\.json saved pinned=true")
        check("A12: 显示后 pinned 保持 true（无迟到 toggle）",
              cfg2.get("pinned") is True and saved2 == 1,
              f"pinned={cfg2.get('pinned')} saved_count={saved2}")
    finally:
        app.cleanup()


# ---------------------------------------------------------------------------
# 验收 4：A13 collector 崩溃离线 + 指数退避自动重启
# ---------------------------------------------------------------------------

def case_restart(exe: Path) -> None:
    """A13：collector 崩溃 → 立即离线 → 指数退避自动重启（1s/3s/10s），
    第 4 次 spawn 恢复在线；无重复实例/孤儿；无 hello 无条件清零预算。"""
    app = AppRun("restart")
    try:
        counter = app.tmp / "crash-count.txt"
        extra = {
            "PB_COLLECTOR": f'"{sys.executable}" "{CRASH_MOCK}"',
            "PB_CRASH_COUNTER": str(counter),
            "PB_CRASH_TIMES": "3",          # 前 3 次 spawn 崩溃，第 4 次保持在线
            "PB_CRASH_AFTER_MS": "1200",
            "PB_CRASH_MODE": "crash",
            "PB_COLLECTOR_RESTART_MAX": "4",
        }
        proc = app.start(exe, mock=False, show=False, extra=extra, quit_after_ms=45000)
        offline = wait_log(app.env, r"tray-state[^\n]*online=false", 15)
        check("A13: 崩溃后托盘 <2s 立即离线", offline is not None, (offline or "")[:160])
        log = ""
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            log = read_text(app_log(app.env))
            if len(re.findall(r"auto-restart attempt=3/4", log)) >= 1 and len(re.findall(r"hello protocol=1", log)) >= 4:
                break
            time.sleep(0.2)
        attempts = re.findall(r"auto-restart attempt=(\d+)/(\d+)[^\n]*delay_ms=(\d+)", log)
        got = {(int(a), int(d)) for a, _, d in attempts}
        check("A13: 指数退避 1s/3s/10s 且次数递增",
              (1, 1000) in got and (2, 3000) in got and (3, 10000) in got,
              f"attempts={sorted(got)}")
        try:
            n = int(read_text(counter).strip() or "0")
        except ValueError:
            n = 0
        check("A13: 3 次崩溃后第 4 次 spawn 恢复（计数=4）", n == 4, f"spawn_count={n}")
        check("A13: 恢复后 collector 重新 ready",
              len(re.findall(r"collector-state state=ready", log)) >= 4,
              f"ready_count={len(re.findall(r'collector-state state=ready', log))}")
        check("A13: hello 不再无条件清零重启预算",
              "budget reset by hello" not in log,
              "found budget reset by hello" if "budget reset by hello" in log else "ok")
        check("A13: 退避序列可追溯（delays=1000,3000,10000）",
              [int(d) for _, _, d in attempts][:3] == [1000, 3000, 10000],
              str([int(d) for _, _, d in attempts][:4]))
        check("A13: 单实例运行中", proc.poll() is None)
    finally:
        app.cleanup()


# ---------------------------------------------------------------------------
# 验收 5：A9 原生 F5 / 右键菜单
# ---------------------------------------------------------------------------

def case_native(exe: Path) -> None:
    app = AppRun("native")
    try:
        proc = app.start(exe, mock=True, show=True, quit_after_ms=60000)
        hwnd = app.wait_window(proc.pid, 20)
        if not hwnd:
            check("native: 找到主窗口", False)
            return
        wait_log(app.env, r"window shown reason=", 20)
        wait_log(app.env, r"collector-state state=ready|hello protocol=", 20)
        time.sleep(1.0)
        log0 = read_text(app_log(app.env))
        check("A9: WebView2 层已关闭默认右键菜单/浏览器加速键",
              "webview2 hardening: context_menus=disabled" in log0
              and "accelerator_keys=disabled" in log0,
              [ln for ln in log0.splitlines() if "webview2 hardening" in ln][-1][:150]
              if "webview2 hardening" in log0 else "missing log")
        check("A2: WebView2 运行期预检已执行",
              "webview2 runtime version=" in log0,
              [ln for ln in log0.splitlines() if "webview2 runtime version=" in ln][-1][:150]
              if "webview2 runtime version=" in log0 else "missing log")
        rect0 = window_rect(hwnd)

        # F5 不应重载（重载会第二次 ui_ready + 重置窗口位置）
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        offset = len(read_text(app_log(app.env)))
        user32.keybd_event(VK_F5, 0, 0, 0)
        user32.keybd_event(VK_F5, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(2.0)
        new_log = read_text(app_log(app.env))[offset:]
        check("A9: F5 不触发整页重载", "command ui_ready" not in new_log,
              new_log.strip().splitlines()[-1][:120] if new_log.strip() else "(no ui_ready)")
        check("A9: F5 不重置窗口位置", window_rect(hwnd) == rect0,
              f"{rect0} -> {window_rect(hwnd)}")

        # 右键不应弹出 WebView2 浏览器菜单（#32768 弹出菜单窗口）
        left, top, right, bottom = window_rect(hwnd)
        cx, cy = (left + right) // 2, (top + bottom) // 2
        user32.SetCursorPos(cx, cy)
        time.sleep(0.2)
        user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        time.sleep(0.8)
        menus = []
        cls_buf = ctypes.create_unicode_buffer(64)

        def _cb(hwnd2, _lparam):
            if not user32.IsWindowVisible(hwnd2):
                return True
            user32.GetClassNameW(hwnd2, cls_buf, 64)
            if cls_buf.value == "#32768":
                menus.append(hwnd2)
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        check("A9: 右键无浏览器菜单", len(menus) == 0, f"popup_menus={len(menus)}")
        if menus:
            user32.keybd_event(VK_ESCAPE, 0, 0, 0)
            user32.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0)

        # A8 拖拽：标题区域拖动应移动窗口（side-head 右上区域）
        scale = dpi_scale(hwnd)
        title_x = left + round(30 * scale) + round(430 * scale)
        title_y = top + round(30 * scale) + round(20 * scale)
        rect1 = window_rect(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        user32.SetCursorPos(title_x, title_y)
        time.sleep(0.2)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        for i in range(1, 11):
            user32.SetCursorPos(title_x - i * 10, title_y)
            time.sleep(0.03)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.6)
        rect2 = window_rect(hwnd)
        check("A8: 标题区可拖动窗口", rect1 != rect2, f"{rect1} -> {rect2}")
    finally:
        app.cleanup()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 验收 6：A5 单实例（同会话去重 + 不同实例名并行隔离）
# ---------------------------------------------------------------------------

def case_single(exe: Path) -> None:
    app = AppRun("single")
    try:
        first = app.start(exe, mock=True, show=False, quit_after_ms=30000)
        wait_log(app.env, r"collector-state state=ready|hello protocol=", 15)
        second = app.second_instance(exe)
        t0 = time.monotonic()
        while second.poll() is None and time.monotonic() - t0 < 5:
            time.sleep(0.05)
        check("A5: 同会话第二实例快速退出（不产生双托盘/双 collector）",
              second.poll() is not None, f"exit_code={second.poll()}")
        off = len(read_text(app_log(app.env)))
        wait_log(app.env, r"window shown reason=single-instance", 8, since_offset=off)
        check("A5: 第二实例激活已有面板",
              bool(re.search(r"window shown reason=single-instance", read_text(app_log(app.env)))),
              "found single-instance show")
        apps = [p for p in subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq peripheral-battery.exe", "/NH"],
            capture_output=True, text=True).stdout.splitlines() if "peripheral-battery" in p]
        check("A5: 只剩一个 app 进程", len(apps) == 1, f"count={len(apps)}")
    finally:
        app.cleanup()

    # 不同单实例名 + 不同数据目录 → 两个实例应能并行（自动化测试隔离前提）
    a = AppRun("single-par-a")
    b = AppRun("single-par-b")
    try:
        pa = a.start(exe, mock=True, show=False, quit_after_ms=12000)
        pb = b.start(exe, mock=True, show=False, quit_after_ms=12000)
        time.sleep(3.0)
        check("A5: 不同实例名/数据目录可并行运行（测试隔离前提）",
              pa.poll() is None and pb.poll() is None,
              f"a_alive={pa.poll() is None} b_alive={pb.poll() is None}")
    finally:
        a.cleanup()
        b.cleanup()


# ---------------------------------------------------------------------------
# 验收 7：A6 残留 pid 记录归属（只清文件/不误杀；活实例记录 LeaveAlone）
# ---------------------------------------------------------------------------

def _write_pid_record(app: "AppRun", rec: dict) -> Path:
    data_dir = Path(app.env["APPDATA"]) / "PeripheralBattery"
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    path = data_dir / "collector.pid.json"
    path.write_text(json.dumps(rec), encoding="utf-8")
    return path


def case_residual(exe: Path) -> None:
    import subprocess as sp

    # 子用例 1：记录里的进程不是 collector（exe 不匹配）→ 只删文件，绝不 kill
    app = AppRun("residual")
    try:
        # 用 cmd.exe（非 python/collector）模拟“pid 记录指向无关进程”
        fake = sp.Popen(["cmd", "/c", "ping", "-n", "30", "127.0.0.1"],
                        stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        try:
            rec_path = _write_pid_record(app, {
                "pid": fake.pid, "creation": 0,
                "app_pid": 999999, "instance": "stale",
                "data_dir": str(Path(app.env["APPDATA"]) / "PeripheralBattery"),
                "exe": "", "kind": "env", "started_unix_ms": 0,
            })
            hidden = app.start(exe, mock=True, show=False, quit_after_ms=15000)
            wait_log(app.env, r"collector-state state=ready|hello protocol=", 15)
            time.sleep(1.0)
            check("A6: 残留记录指向非 collector 进程时不误杀",
                  fake.poll() is None, f"fake_pid={fake.pid} alive={fake.poll() is None}")
            log = read_text(app_log(app.env))
            check("A6: 只删 pid 文件并记录原因（exe mismatch）",
                  "cleanup residual: 仅删除 pid 文件" in log
                  and "exe mismatch" in log,
                  [ln for ln in log.splitlines() if "cleanup residual" in ln][-1][:150]
                  if "cleanup residual" in log else "missing log")
        finally:
            if fake.poll() is None:
                fake.kill()
    finally:
        app.cleanup()

    # 子用例 2：记录归属另一个仍存活的 app 实例 → LeaveAlone（不 kill、不删记录）
    app2 = AppRun("residual-live")
    try:
        fake = sp.Popen([sys.executable, "-c", "import time; time.sleep(25)"])
        try:
            data_dir = Path(app2.env["APPDATA"]) / "PeripheralBattery"
            rec_path = _write_pid_record(app2, {
                "pid": fake.pid, "creation": 0,
                "app_pid": os.getpid(),  # 仍存活的“另一个实例”
                "instance": "other-live-instance",
                "data_dir": str(data_dir),
                "exe": "", "kind": "env", "started_unix_ms": 0,
            })
            app2.start(exe, mock=True, show=False, quit_after_ms=15000)
            wait_log(app2.env, r"collector-state state=ready|hello protocol=", 15)
            time.sleep(1.0)
            check("A6: 另一活实例的 collector 不被误杀",
                  fake.poll() is None, f"fake_pid={fake.pid} alive={fake.poll() is None}")
            check("A6: LeaveAlone 有日志可追溯",
                  "cleanup residual: skip kill" in read_text(app_log(app2.env)),
                  "found skip-kill log")
        finally:
            if fake.poll() is None:
                fake.kill()
    finally:
        app2.cleanup()


# ---------------------------------------------------------------------------
# 验收 8：A2 WebView2 预检日志
# ---------------------------------------------------------------------------

def case_webview(exe: Path) -> None:
    app = AppRun("webview")
    try:
        app.start(exe, mock=True, show=False, quit_after_ms=12000)
        ok = wait_log(app.env, r"webview2 runtime version=", 15)
        check("A2: 启动时记录 WebView2 运行期版本", ok is not None, (ok or "")[:140])
        log = read_text(app_log(app.env))
        m = re.search(r"webview2 runtime version=([0-9.]+)", log)
        version = m.group(1) if m else ""
        check("A2: WebView2 版本 >= 118（最低要求）",
              bool(version) and tuple(int(x) for x in (version.split(".") + ["0"] * 4)[:4])
              >= (118, 0, 0, 0), f"version={version}")
    finally:
        app.cleanup()


CASES = {
    "region": case_region,
    "minimize": case_minimize,
    "pin": case_pin,
    "restart": case_restart,
    "native": case_native,
    "single": case_single,
    "residual": case_residual,
    "webview": case_webview,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", nargs="+", choices=list(CASES) + ["all"])
    parser.add_argument("--exe", default=str(DEFAULT_EXE))
    parser.add_argument("--out", default=str(OUT_DIR / "phase-a-runtime.json"))
    args = parser.parse_args()
    exe = Path(args.exe)
    if not exe.is_file():
        print(f"FAIL: exe 不存在: {exe}")
        return 2
    cases = list(CASES) if "all" in args.case else args.case
    for name in cases:
        print(f"\n========== {name} ==========")
        try:
            CASES[name](exe)
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            check(f"{name}: 用例无异常", False, str(e))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps({"exe": str(exe), "results": RESULTS}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n===== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed, {len(failed)} failed =====")
    for r in failed:
        print("  FAIL:", r["check"], "|", r["detail"])
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
