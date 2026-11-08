#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_no_emoji.py — 确定性「禁 emoji」门禁（专治卡片/帧里 emoji 图标）。

card-design 设计系统把「禁 emoji 当图标」列为硬规则，但此前只有 prose，无法执行。
本脚本对卡片/帧的**源码**（HTML / JSX / tsx / css / js）做确定性 emoji 字形检测：
命中任一 emoji code point 即判 FAIL，渲染前跑一遍，fail 就换线性图标(SVG)/纯排版重渲。

为什么查源码而不是查 PNG：agent 写 HTML/JSX 时就把 emoji 打进去，源码级检测
100% 可复现、不误报；PNG 级只能靠字形像素近似，易漏易误。渲染出的 PNG 另由
card_audit.py 做死空白/密度门禁，两者互补。

用法：
    check_no_emoji.py --html outputs/x/card.html
    check_no_emoji.py --dir outputs/x            # 递归扫 .html/.jsx/.tsx/.css/.js
    check_no_emoji.py --text "🔥 蹭热点"          # 直接查一段文本
    check_no_emoji.py --html a.html --lenient    # 只告警不 FAIL（exit 0）
    check_no_emoji.py --html a.html --allow 2    # 允许最多 2 个（anti-ai-slop 例外口）

退出码：0 通过 / 1 用法错 / 2 发现 emoji 且超 --allow。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 一律 2-tuple 区间。HARD=永远判 emoji；SOFT=排版符号(箭头/®©™)，默认放过，--strict 才判。
_HARD_RANGES = [
    (0x1F000, 0x1FAFF),   # Mahjong/Playing/Poke/Dingbat/Emoticons/Symbols/Misc/Transport/Supplement
    (0x2600, 0x26FF),     # Misc symbols（⚠ ✅ ⌥ 等，含「当图标用」的符号）
    (0x2700, 0x27BF),     # Dingbats（✂ ✏️ ✒ ✓ ✗ 等常见「图标」）
    (0x2300, 0x23FF),     # Misc technical（⌚ ⌛ ⏰ 等）
    (0x2B00, 0x2BFF),     # Misc symbols and arrows（⭐ ⬜ ⬛ 等）
    (0x1F1E6, 0x1F1FF),   # Regional indicators（国旗）
]
_SOFT_RANGES = [
    (0x2190, 0x21FF),     # Arrows（→ ↔ ↑ ↓，多为排版符号，默认放过）
    (0x2100, 0x214F),     # Letterlike（® ™ © ®，默认放过）
]
# 组合符/变体选择符：单独判（它们本身不显示字形，但常和 emoji 连写）。
_COMBO = {0x200D, 0x20E3, 0xFE0F}  # ZWJ / 键帽 / 变体选择符


def _in_ranges(cp: int, ranges: list[tuple[int, int]]) -> bool:
    return any(lo <= cp <= hi for lo, hi in ranges)


def _is_emoji(ch: str, strict: bool = False) -> bool:
    cp = ord(ch)
    if cp in _COMBO:
        return True
    if _in_ranges(cp, _HARD_RANGES):
        return True
    if strict and _in_ranges(cp, _SOFT_RANGES):
        return True
    return False


def scan_text(text: str, strict: bool = False) -> list[tuple[int, str]]:
    """返回 [(line_no, emoji_char), ...]（line 从 1 起）。"""
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for ch in line:
            if _is_emoji(ch, strict=strict):
                hits.append((i, ch))
    return hits


def _iter_targets(path: str, recursive: bool = True) -> list[Path]:
    p = Path(path)
    if p.is_dir():
        patterns = ("*.html", "*.jsx", "*.tsx", "*.css", "*.js")
        out: list[Path] = []
        for pat in patterns:
            out += list(p.rglob(pat)) if recursive else list(p.glob(pat))
        # 跳过第三方/构建产物，避免把依赖里的 emoji（⚠ ♥ 等）误判进门禁。
        skip = {"node_modules", "dist", ".venv"}
        out = [f for f in out if not any(seg in f.parts for seg in skip)]
        return sorted(set(out))
    return [p] if p.is_file() else []


def main() -> int:
    ap = argparse.ArgumentParser(description="确定性「禁 emoji」门禁（扫卡片/帧源码）")
    ap.add_argument("--html", help="输入 HTML/JSX/tsx 文件（单个）")
    ap.add_argument("--dir", help="递归扫目录下的 .html/.jsx/.tsx/.css/.js")
    ap.add_argument("--text", help="直接检测一段文本")
    ap.add_argument("--lenient", action="store_true", help="只告警不 FAIL（exit 0）")
    ap.add_argument("--allow", type=int, default=0,
                    help="允许的最多 emoji 数（超出才 FAIL；0=一个都不允许）")
    ap.add_argument("--strict", action="store_true",
                    help="连箭头/®/©/™ 等排版符号也判为 emoji（默认放过）")
    if not (args := ap.parse_args()) and False:
        pass
    args = ap.parse_args()
    if not (args.html or args.dir or args.text):
        ap.error("需要 --html / --dir / --text 之一")

    hits_by_file: dict[str, list[tuple[int, str]]] = {}
    if args.text is not None:
        hits_by_file["arg"] = scan_text(args.text, strict=args.strict)
    elif args.html:
        try:
            txt = Path(args.html).read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: 读取失败 {args.html}: {e}", file=sys.stderr)
            return 1
        hits_by_file[args.html] = scan_text(txt, strict=args.strict)
    elif args.dir:
        for f in _iter_targets(args.dir):
            try:
                txt = f.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            hits_by_file[str(f)] = scan_text(txt, strict=args.strict)

    total = sum(len(v) for v in hits_by_file.values())
    for fname, hits in hits_by_file.items():
        for line, ch in hits:
            print(f"EMOJI  {fname}:{line}  U+{ord(ch):04X}  {ch!r}")

    if total == 0:
        print(f"[PASS] 未发现 emoji（扫描 {len(hits_by_file)} 处）")
        return 0
    print(f"\n发现 {total} 个 emoji 字形。", file=sys.stderr)

    if args.lenient:
        print(f"[WARN] {total} 个 emoji（--lenient：不 FAIL）", file=sys.stderr)
        return 0
    if total <= args.allow:
        print(f"[PASS] emoji {total} ≤ 允许 {args.allow}", file=sys.stderr)
        return 0
    print(f"[FAIL] emoji {total} > 允许 {args.allow}：换线性图标(SVG)/纯排版后重渲。",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
