#!/usr/bin/env python3
"""A14 诊断：对比 区域裁剪 / acrylic 开关 组合下窗口安全边的像素表现。

用法：.venv/Scripts/python.exe tools/probe_region_variants.py
每个组合独立临时数据目录；输出 design/verify/region-variant-*.png 与像素摘要。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import verify_phase_a as V  # noqa: E402

EXE = ROOT / "src-tauri" / "target" / "debug" / "peripheral-battery.exe"
OUT = ROOT / "design" / "verify"
VARIANTS = {
    "default": {},
    "no-region": {"PB_DISABLE_PANEL_REGION": "1"},
    "no-acrylic": {"PB_DISABLE_ACRYLIC": "1"},
    "no-region-no-acrylic": {"PB_DISABLE_PANEL_REGION": "1", "PB_DISABLE_ACRYLIC": "1"},
}


def run_variant(name: str, extra: dict) -> dict:
    app = V.AppRun(f"probe-{name}")
    try:
        hidden = app.start(EXE, mock=True, show=False, extra=extra)
        V.wait_log(app.env, r"collector-state state=ready|hello protocol=", 15)
        time.sleep(1.2)
        before, ox, oy = V.screenshot()
        app.second_instance(EXE, extra)
        V.wait_log(app.env, r"window shown reason=", 15)
        time.sleep(1.8)
        after, ox2, oy2 = V.screenshot()
        hwnd = app.wait_window(hidden.pid, 5)
        if not hwnd:
            return {"variant": name, "error": "no window"}
        left, top, right, bottom = V.window_rect(hwnd)
        scale = V.dpi_scale(hwnd)
        inset = round(30.0 * scale)
        pts = {
            "band_tl_10": (left + 10, top + 10),
            "band_top_mid": ((left + right) // 2, top + 10),
            "band_left_mid": (left + 10, (top + bottom) // 2),
            "band_corner_2": (left + 2, top + 2),
            "panel_inside_bg": (left + inset + 20, top + inset + 20),
        }
        pix = {}
        for key, (x, y) in pts.items():
            try:
                pix[key] = {
                    "before": V.px(before, ox, oy, x, y),
                    "after": V.px(after, ox2, oy2, x, y),
                }
            except (IndexError, OSError):
                pass
        crop = after.crop((left - ox2 - 20, top - oy2 - 20,
                           right - ox2 + 20, bottom - oy2 + 20))
        crop.save(OUT / f"region-variant-{name}.png")
        log_text = V.read_text(V.app_log(app.env))
        return {
            "variant": name,
            "extra": extra,
            "rect": [left, top, right, bottom],
            "scale": scale,
            "region": V.window_region(hwnd),
            "backdrop": V.dwm_backdrop(hwnd),
            "pixels": pix,
            "log": [ln for ln in log_text.splitlines()
                    if any(k in ln for k in ("panel region", "acrylic", "panel position"))],
        }
    finally:
        app.cleanup()


def main() -> int:
    results = []
    for name, extra in VARIANTS.items():
        print(f"\n===== {name} =====")
        r = run_variant(name, extra)
        results.append(r)
        print(json.dumps({k: r.get(k) for k in ("rect", "region", "backdrop", "pixels")},
                         ensure_ascii=False, indent=2))
        for line in r.get("log", []):
            print("  ", line[:160])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "region-variants.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
