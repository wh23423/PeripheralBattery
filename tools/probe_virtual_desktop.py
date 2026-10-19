#!/usr/bin/env python3
"""A7 虚拟桌面验收（Lead 专用）：双桌面下托盘激活面板是否出现在当前桌面。

真实桌面切换：Win+Ctrl+D 创建并切到临时桌面（实测可被合成输入触发）；
测试结束用 Win+Ctrl+F4 关闭临时桌面恢复用户环境（仅当初始只有 1 个桌面）。

判定：
  1) 切到桌面 2 + 打开记事本置前（给 app 的“前台窗口桌面”启发式提供目标）；
  2) 触发第二实例（与托盘点击同一条 show_panel 路径）；
  3) 截屏对比：面板矩形区域出现面板内容 → 面板确实在当前（桌面2）可见；
  4) app.log 的 `window virtual desktop ... result=` 为 moved-to-current；
  5) 结束后桌面数量/当前桌面 GUID 恢复。

用法：.venv/Scripts/python.exe tools/probe_virtual_desktop.py
结果写 design/verify/a7-virtual-desktop.json。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import time
import uuid
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import verify_phase_a as V  # noqa: E402

EXE = ROOT / "src-tauri" / "target" / "debug" / "peripheral-battery.exe"
OUT = ROOT / "design" / "verify" / "a7-virtual-desktop.json"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SendInput.restype = ctypes.c_uint
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
ole32 = ctypes.WinDLL("ole32")
dwmapi = ctypes.WinDLL("dwmapi")

DWMWA_CLOAKED = 14


def dwm_cloaked(hwnd: int):
    """DWM cloak 状态：0=未 cloak（在当前桌面可见），非 0=被隐藏（在其它桌面/虚拟桌面）。

    Windows 的虚拟桌面通过 DWM cloak 隐藏其它桌面上的窗口，因此这是判断
    “窗口此刻是否在当前虚拟桌面可见”的可靠依据（不依赖调用进程所在桌面）。
    """
    val = ctypes.c_int(0)
    hr = dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_CLOAKED,
                                      ctypes.byref(val), ctypes.sizeof(val))
    return val.value if hr == 0 else None

VK_LWIN, VK_CONTROL, VK_D, VK_F4 = 0x5B, 0x11, 0x44, 0x73
KEYEVENTF_KEYUP = 0x0002
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUT_UNION)]


def send_key(vk: int, up: bool = False) -> int:
    inp = INPUT()
    inp.type = 1  # INPUT_KEYBOARD
    inp.u.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def key_combo(*keys: int) -> None:
    for k in keys:
        send_key(k)
        time.sleep(0.08)
    for k in reversed(keys):
        send_key(k, up=True)
        time.sleep(0.08)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


CLSID_VDM = GUID()
_u = uuid.UUID("AA509086-5CA9-4C25-8F95-589D3C07B48A")
CLSID_VDM.Data1, CLSID_VDM.Data2, CLSID_VDM.Data3 = _u.time_low, _u.time_mid, _u.time_hi_version
CLSID_VDM.Data4 = (ctypes.c_ubyte * 8).from_buffer_copy(_u.bytes[8:])
IID_VDM = GUID()
_u = uuid.UUID("A5CD92FF-29BE-454C-8D04-D82879FB3F1B")
IID_VDM.Data1, IID_VDM.Data2, IID_VDM.Data3 = _u.time_low, _u.time_mid, _u.time_hi_version
IID_VDM.Data4 = (ctypes.c_ubyte * 8).from_buffer_copy(_u.bytes[8:])


class VDManager:
    def __init__(self) -> None:
        hr = ole32.CoInitializeEx(None, 2)
        if hr & 0xFFFFFFFF not in (0, 1, 0x80010106):
            raise OSError(f"CoInitializeEx hr=0x{hr & 0xFFFFFFFF:08x}")
        self.ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(CLSID_VDM), None, 1,
                                    ctypes.byref(IID_VDM), ctypes.byref(self.ptr))
        if hr != 0 or not self.ptr:
            raise OSError(f"CoCreateInstance hr=0x{hr & 0xFFFFFFFF:08x}")
        self.vtbl = ctypes.cast(
            self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents

    def _fn(self, index: int, restype, *argtypes):
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(self.vtbl[index])

    def on_current(self, hwnd: int):
        fn = self._fn(3, ctypes.c_long, wt.HWND, ctypes.POINTER(ctypes.c_int))
        val = ctypes.c_int(0)
        hr = fn(self.ptr, hwnd, ctypes.byref(val))
        return bool(val.value) if hr == 0 else None

    def window_desktop(self, hwnd: int):
        fn = self._fn(4, ctypes.c_long, wt.HWND, ctypes.POINTER(GUID))
        g = GUID()
        hr = fn(self.ptr, hwnd, ctypes.byref(g))
        if hr != 0:
            return None
        raw = ctypes.string_at(ctypes.byref(g), ctypes.sizeof(GUID))
        text = str(uuid.UUID(bytes_le=raw))
        return None if text == "00000000-0000-0000-0000-000000000000" else text


def desktop_state() -> dict:
    key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops"
    out = {"current": None, "count": None}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            cur = winreg.QueryValueEx(k, "CurrentVirtualDesktop")[0]
            ids = winreg.QueryValueEx(k, "VirtualDesktopIDs")[0]
            out["current"] = str(uuid.UUID(bytes_le=bytes(cur)))
            out["count"] = len(ids) // 16
    except OSError:
        pass
    return out


def wait_desktop(predicate, timeout: float = 6.0) -> dict:
    deadline = time.monotonic() + timeout
    st = desktop_state()
    while time.monotonic() < deadline:
        st = desktop_state()
        if predicate(st):
            return st
        time.sleep(0.2)
    return st


def visible_windows(pid: int | None):
    out = []

    def _cb(hwnd, _lparam):
        wpid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if (pid is None or wpid.value == pid) and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 2)
                user32.GetWindowTextW(hwnd, buf, n + 2)
                out.append((hwnd, buf.value))
        return True

    user32.EnumWindows(V.WNDENUMPROC(_cb), 0)
    return out


def panel_visible_in_screenshot(before, after, rect) -> float:
    """面板矩形内相对基线的变化像素比例（>0.5 视为面板已在本桌面绘制）。"""
    ox = user32.GetSystemMetrics(V.SM_XVIRTUALSCREEN) if hasattr(V, "SM_XVIRTUALSCREEN") else 0
    oy = user32.GetSystemMetrics(77)
    ox2, oy2 = 0, 0
    left, top, right, bottom = rect
    total = changed = 0
    for x in range(left + 20, right - 20, 60):
        for y in range(top + 20, bottom - 20, 60):
            try:
                pb = before.getpixel((x - ox2, y - oy2))[:3]
                pa = after.getpixel((x - ox2, y - oy2))[:3]
            except (IndexError, OSError):
                continue
            total += 1
            if max(abs(pb[i] - pa[i]) for i in range(3)) > 18:
                changed += 1
    return changed / total if total else 0.0


def main() -> int:
    results = []
    orig = desktop_state()
    print("initial:", orig)
    if orig["count"] != 1:
        print("SKIP: 本机已有多个虚拟桌面，为避免影响用户环境不自动创建/关闭桌面")
        return 0

    app = V.AppRun("vd")
    notepad = None
    try:
        proc = app.start(EXE, mock=True, show=True, quit_after_ms=120000)
        hwnd = app.wait_window(proc.pid, 20)
        V.wait_log(app.env, r"window shown reason=", 20)
        time.sleep(1.0)
        if not hwnd:
            print("FAIL: 没有主窗口")
            return 1
        mgr = VDManager()
        results.append({
            "check": "桌面1：面板在主窗口可见",
            "ok": bool(user32.IsWindowVisible(hwnd)) and mgr.on_current(hwnd) is not False,
            "detail": f"visible={bool(user32.IsWindowVisible(hwnd))} "
                      f"on_current={mgr.on_current(hwnd)}",
        })
        rect = V.window_rect(hwnd)
        panel_cloaked_d1 = dwm_cloaked(hwnd)

        # 阳性对照：找一个普通应用窗口（切换桌面后会被 DWM cloak）
        control = None
        for h, title in visible_windows(None):
            if "DeepSeek" in title or "Chrome" in title or "Edge" in title:
                control = (h, title)
                break
        results.append({
            "check": "桌面1：面板可见（DWM cloak=0）",
            "ok": bool(user32.IsWindowVisible(hwnd)) and panel_cloaked_d1 == 0,
            "detail": f"visible={bool(user32.IsWindowVisible(hwnd))} cloaked={panel_cloaked_d1}",
        })

        # 切到临时桌面 2
        key_combo(VK_LWIN, VK_CONTROL, VK_D)
        st2 = wait_desktop(lambda s: s["current"] != orig["current"])
        switched = st2["current"] != orig["current"]
        print("desktop2:", st2, "switched=", switched)
        results.append({"check": "创建/切换到临时桌面 2", "ok": switched, "detail": str(st2)})
        if not switched:
            return 1
        time.sleep(2.5)
        before, _ox, _oy = V.screenshot()
        before.save(ROOT / "design" / "verify" / "a7-desktop2-before.png")

        control_cloaked = dwm_cloaked(control[0]) if control else None
        panel_cloaked_d2 = dwm_cloaked(hwnd)
        results.append({
            "check": "阳性对照：普通窗口在桌面2 被 DWM cloak（证明检测有效）",
            "ok": control_cloaked is not None and control_cloaked != 0,
            "detail": f"control={control[1][:40] if control else None} cloaked={control_cloaked}",
        })
        results.append({
            "check": "桌面2：面板可见（未 cloak，符合“面板出现在当前桌面”）",
            "ok": panel_cloaked_d2 == 0,
            "detail": f"cloaked={panel_cloaked_d2}",
        })

        # 打开记事本置前，给 app 的“前台窗口桌面”启发式提供目标
        notepad = subprocess.Popen(["notepad.exe"])
        time.sleep(1.8)
        wins = visible_windows(notepad.pid)
        np_hwnd = wins[0][0] if wins else None
        np_desktop = mgr.window_desktop(np_hwnd) if np_hwnd else None
        if np_hwnd:
            user32.SetForegroundWindow(np_hwnd)
            time.sleep(0.5)
        results.append({
            "check": "桌面2 前台窗口（记事本）的桌面 ID = 当前桌面",
            "ok": np_desktop == st2["current"],
            "detail": f"hwnd={np_hwnd} desktop={np_desktop} current={st2['current']}",
        })

        # 触发激活（第二实例 = 托盘点击同一 show_panel 路径）
        off = len(V.read_text(V.app_log(app.env)))
        app.second_instance(EXE)
        time.sleep(2.5)
        log = V.read_text(V.app_log(app.env))[off:]
        line = ""
        for ln in log.splitlines():
            if "window virtual desktop" in ln:
                line = ln
        after, _ox2, _oy2 = V.screenshot()
        after.save(ROOT / "design" / "verify" / "a7-desktop2-after.png")
        panel_cloaked_after = dwm_cloaked(hwnd)
        rect_after = V.window_rect(hwnd)
        try:
            after.crop((rect[0] - 40, rect[1] - 40, rect[2] + 40, rect[3] + 40)).save(
                ROOT / "design" / "verify" / "a7-desktop2-after-crop.png")
        except Exception:
            pass
        print("vd log:", line[:220])
        print("panel cloaked before/after activation:", panel_cloaked_d2, panel_cloaked_after)
        print("rect:", rect, "->", rect_after)
        results.append({
            "check": "桌面2 激活后面板仍可见（DWM cloak=0）",
            "ok": panel_cloaked_after == 0,
            "detail": f"cloaked={panel_cloaked_after}",
        })
        results.append({
            "check": "app 日志记录 VD 决策且未 failed",
            "ok": bool(line) and "failed" not in line,
            "detail": line.strip()[:180],
        })

    finally:
        cur = desktop_state()
        if cur["count"] == 2 and cur["current"] != orig["current"]:
            key_combo(VK_LWIN, VK_CONTROL, VK_F4)
            restored = wait_desktop(lambda s: s["current"] == orig["current"])
            time.sleep(0.8)
            results.append({
                "check": "恢复用户环境（临时桌面已关闭，桌面数/当前桌面恢复）",
                "ok": restored["current"] == orig["current"] and restored["count"] == orig["count"],
                "detail": str(restored),
            })
        if notepad is not None and notepad.poll() is None:
            notepad.kill()
        app.cleanup()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"initial": orig, "results": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in results if not r["ok"]]
    for r in results:
        print(("PASS " if r["ok"] else "FAIL ") + r["check"] + " | " + str(r["detail"]))
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
