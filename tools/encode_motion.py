#!/usr/bin/env python
"""把 motion_preview.js 抓的 PNG 序列编码为 GIF / 动画 WebP。

用法：
    .venv\\Scripts\\python.exe tools\\encode_motion.py \
        --src design/_motion_frames --gif design/motion-preview.gif \
        --webp design/motion-preview.webp --width 780 --fps 12

输入目录里若有 timings.json（screencast 时间戳），会先按真实时间轴把
变帧率的帧序列重采样成固定 fps；没有则按 --fps 直接编码。

为什么用全局调色板：逐帧各自量化会让背景渐变在帧间抖动；这里抽取
若干帧拼成一张参考图统一量化，每帧只做映射，GIF 观感更稳。
"""
import argparse
import json
from pathlib import Path

from PIL import Image


def load_frames(src: Path, width: int):
    """返回 [(RGB 图, 时间戳 ms)]，按时间排序；无 timings 时按 80ms 估算。"""
    files = sorted(src.glob("f*.png"))
    if not files:
        raise SystemExit(f"no frames found in {src}")

    timing_path = src / "timings.json"
    times = {}
    if timing_path.exists():
        data = json.loads(timing_path.read_text(encoding="utf-8"))
        times = {item["file"]: item["t"] for item in data.get("frames", [])}

    frames = []
    for f in files:
        t = times.get(f.name)
        if t is None:
            t = len(frames) * 80
        with Image.open(f) as im:
            im = im.convert("RGB")
            h = round(im.height * width / im.width)
            frames.append((im.resize((width, h), Image.Resampling.LANCZOS), t))
    frames.sort(key=lambda item: item[1])
    return frames


def resample(frames, fps):
    """按固定帧率重采样；返回 (帧列表, 每帧时长 ms)。"""
    step = 1000.0 / fps
    last_t = frames[-1][1]
    # 补一个典型帧间隔，避免最后一帧被截掉
    deltas = [b[1] - a[1] for a, b in zip(frames, frames[1:]) if b[1] > a[1]]
    tail = sum(deltas) / len(deltas) if deltas else step
    duration = last_t + tail
    count = max(1, round(duration / step))

    picked = []
    idx = 0
    for i in range(count):
        tick = i * step
        while idx + 1 < len(frames) and frames[idx + 1][1] <= tick:
            idx += 1
        picked.append(frames[idx][0])
    return picked, int(round(step))


def build_palette(frames, colors):
    """从等间隔帧拼一条竖直长图，量化出一张全局调色板。"""
    step = max(1, len(frames) // 12)
    samples = frames[::step][:12]
    width = samples[0].width
    strip = Image.new("RGB", (width, samples[0].height * len(samples)))
    for idx, im in enumerate(samples):
        strip.paste(im, (0, idx * samples[0].height))
    return strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--gif", required=True)
    ap.add_argument("--webp", default=None)
    ap.add_argument("--width", type=int, default=780)
    ap.add_argument("--fps", type=float, default=12)
    ap.add_argument("--colors", type=int, default=192)
    ap.add_argument("--quality", type=int, default=78)
    args = ap.parse_args()

    frames = load_frames(Path(args.src), args.width)
    frames, frame_ms = resample(frames, args.fps)

    palette = build_palette(frames, args.colors)
    pal_frames = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in frames]

    gif_path = Path(args.gif)
    gif_path.parent.mkdir(parents=True, exist_ok=True)
    pal_frames[0].save(
        gif_path,
        save_all=True,
        append_images=pal_frames[1:],
        duration=frame_ms,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(
        f"gif  {gif_path}  {gif_path.stat().st_size / 1024 / 1024:.2f} MB  "
        f"{len(pal_frames)} frames @ {frame_ms}ms"
    )

    if args.webp:
        webp_path = Path(args.webp)
        webp_path.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            webp_path,
            save_all=True,
            append_images=frames[1:],
            duration=frame_ms,
            loop=0,
            quality=args.quality,
            method=6,
        )
        print(f"webp {webp_path}  {webp_path.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
