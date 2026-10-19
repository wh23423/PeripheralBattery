# -*- coding: utf-8 -*-
"""从 design/motion/tokens.css 生成 design/motion/force-motion.css。

用途：ui/styles.css 里的 prefers-reduced-motion 规则（以及 html[data-reduced-motion]）
会把 animation/transition 压成 0.001ms。当用户系统开了「减少动态效果」，或浏览器把
该媒体查询置位时，motion-lab 会整体静止。force-motion.css 把潮息自己的
animation / transition 声明以同等选择器 + !important 重新写一遍，
只在 <html data-force-motion> 时生效，用来在演示页里强制播放动效。

生成规则：
  1. 只取 tokens.css 中形如 html[data-motion="tidal"] ... 的规则（含伪元素/伪类）；
  2. 选择器前缀替换为 html[data-force-motion]；
  3. 只保留 animation* / transition* 声明，逐条加 !important；
  4. 末尾补两条 styles.css 里未被 tokens.css 接管的基础动画。

tokens.css 改动后重跑本脚本即可。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "design" / "motion" / "tokens.css"
DST = ROOT / "design" / "motion" / "force-motion.css"

PREFIX = 'html[data-motion="tidal"]'
NEW_PREFIX = "html[data-force-motion]"

# 需要以 !important 恢复的属性（animation 简写会一并设置 duration/name/...）
ANIM_PROPS = re.compile(r"^(animation|transition)(-[a-z-]+)?$")


def split_decls(body: str):
    """按顶层分号切声明，忽略括号内的分号。"""
    out, buf, depth = [], [], 0
    for ch in body:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == ";" and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf))
    return [d.strip() for d in out if d.strip()]


def transform(src: str) -> str:
    # 去掉注释，避免误伤
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    rules = []
    i = 0
    n = len(src)
    while i < n:
        brace = src.find("{", i)
        if brace < 0:
            break
        selector = src[i:brace].strip()
        # 跳过 @media / @keyframes 等 at-rule（tokens.css 的 keyframes 无需复制）
        if selector.startswith("@"):
            end = src.find("}", brace)
            # at-rule 至少跳过它的一层花括号
            depth, j = 1, brace + 1
            while j < n and depth:
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                j += 1
            i = j
            continue
        end = src.find("}", brace)
        body = src[brace + 1:end]
        i = end + 1
        if PREFIX not in selector:
            continue
        decls = [d for d in split_decls(body) if ANIM_PROPS.match(d.split(":", 1)[0].strip())]
        if not decls:
            continue
        selectors = ", ".join(
            s.strip().replace(PREFIX, NEW_PREFIX) for s in selector.split(",") if s.strip()
        )
        rules.append((selectors, [d + " !important" for d in decls]))

    head = (
        "/* 由 tools/build_force_motion.py 从 tokens.css 生成，请勿手改。\n"
        "   仅在 <html data-force-motion> 时生效：把潮息的 animation / transition\n"
        "   以 !important 重新声明，压过 ui/styles.css 的 prefers-reduced-motion 降级。 */\n\n"
    )
    out = [head]
    for sel, decls in rules:
        out.append(sel + " {\n  " + ";\n  ".join(decls) + ";\n}\n\n")
    out.append(
        "/* styles.css 中未被 tokens.css 接管的基底动画（在线点脉冲） */\n"
        "html[data-force-motion] .live i::after {\n"
        "  animation: pulse-ring 2.4s ease-out infinite !important;\n"
        "}\n"
    )
    return "".join(out)


def main():
    css = transform(SRC.read_text(encoding="utf-8"))
    DST.write_text(css, encoding="utf-8")
    print(f"wrote {DST.relative_to(ROOT)} ({len(css.splitlines())} lines, {css.count('!important')} important decls)")


if __name__ == "__main__":
    main()
