"""生成应用 / 托盘图标源图（1024×1024 PNG），随后用 `npx tauri icon` 生成全套尺寸。

依赖 Pillow（DSH 内置 Python 已带）。此脚本只被 scripts/build.ps1 或人工调用，
不参与运行时。
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 4  # 超采样倍数
W = 1024 * S


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def vertical_gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (w, y)], fill=c)
    return img


def main(out: Path) -> None:
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # 电池外壳（深色玻璃质感）
    body = [150 * S, 250 * S, 874 * S, 774 * S]
    radius = 96 * S
    shell = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(shell)
    d.rounded_rectangle(body, radius=radius, fill=(15, 23, 42, 255))
    # 内描边
    d.rounded_rectangle(body, radius=radius, outline=(148, 163, 184, 140), width=6 * S)
    img.alpha_composite(shell)

    # 电极帽
    cap = [874 * S, 400 * S, 934 * S, 624 * S]
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(cap, radius=22 * S, fill=(148, 163, 184, 230))

    # 电量填充（绿→青渐变），约占 72%
    fill_box = [206 * S, 306 * S, 818 * S, 718 * S]
    fw = fill_box[2] - fill_box[0]
    fh = fill_box[3] - fill_box[1]
    grad = vertical_gradient(fw, fh, (52, 211, 153), (34, 197, 236))
    mask = rounded_mask(fw, 42 * S)
    fill_layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    # 只填 72% 宽度
    visible_w = int(fw * 0.72)
    fill_layer.paste(grad.crop((0, 0, visible_w, fh)).convert("RGBA"), (fill_box[0], fill_box[1]),
                     mask.crop((0, 0, visible_w, fh)))
    img.alpha_composite(fill_layer)

    # 高光条
    hl = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    dh = ImageDraw.Draw(hl)
    dh.rounded_rectangle([240 * S, 330 * S, 330 * S, 690 * S], radius=30 * S,
                         fill=(255, 255, 255, 46))
    hl = hl.filter(ImageFilter.GaussianBlur(10 * S))
    img.alpha_composite(hl)

    img = img.resize((1024, 1024), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"icon source -> {out}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("design/verify/tauri-icon-source.png")
    main(target)
