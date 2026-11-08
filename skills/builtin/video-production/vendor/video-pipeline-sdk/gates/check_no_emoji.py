#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gates/check_no_emoji.py — 薄封装：优先复用 Easel 共享版 check_no_emoji.py，
   找不到（如 SDK 被单独抽出）才回退到本文件内嵌的最小实现。

   目的：门禁逻辑只在 shared/scripts/check_no_emoji.py 一处维护；本文件只负责
   「定位并复用共享版」，不再复制一份完整实现（去重）。

   触发：default-gates.json 的 no-emoji 门 → {python} gates/check_no_emoji.py --dir {run_dir}/workdir
   用法：同共享版（--html/--dir/--text/--lenient/--allow/--strict）
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _find_shared() -> Path | None:
    # 1) EASEL_ROOT 环境变量
    env = os.environ.get("EASEL_ROOT")
    if env:
        cand = Path(env) / "skills/shared/scripts/check_no_emoji.py"
        if cand.is_file():
            return cand
    # 2) 从本文件向上逐级找 shared/scripts/check_no_emoji.py
    for parent in Path(__file__).resolve().parents:
        cand = parent / "shared/scripts/check_no_emoji.py"
        if cand.is_file():
            return cand
    return None


def _run_shared() -> "int | None":
    shared = _find_shared()
    if shared is None:
        return None
    try:
        spec = importlib.util.spec_from_file_location("easel_check_no_emoji", shared)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.main()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[WARN] 共享版 check_no_emoji.py 加载失败，回退内嵌实现：{e}\n")
        return None


def _fallback_main() -> int:
    """内嵌兜底：仅在共享版不可用时启用（保持 SDK 独立可用）。
    日常由共享版承担；门禁逻辑增强请改 shared/scripts/check_no_emoji.py。"""
    import argparse

    HARD = [(0x1F000, 0x1FAFF), (0x2600, 0x26FF), (0x2700, 0x27BF),
            (0x2300, 0x23FF), (0x2B00, 0x2BFF), (0x1F1E6, 0x1F1FF)]
    SOFT = [(0x2190, 0x21FF), (0x2100, 0x214F)]
    COMBO = {0x200D, 0x20E3, 0xFE0F}

    ap = argparse.ArgumentParser(description="禁 emoji 门禁（内嵌兜底，优先用共享版）")
    ap.add_argument("--html")
    ap.add_argument("--dir")
    ap.add_argument("--text")
    ap.add_argument("--lenient", action="store_true")
    ap.add_argument("--allow", type=int, default=0)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    def is_emoji(ch: str) -> bool:
        cp = ord(ch)
        if cp in COMBO:
            return True
        if any(lo <= cp <= hi for lo, hi in HARD):
            return True
        return args.strict and any(lo <= cp <= hi for lo, hi in SOFT)

    def scan(text: str) -> list[tuple[int, str]]:
        return [(i, ch) for i, line in enumerate(text.splitlines(), 1)
                for ch in line if is_emoji(ch)]

    hits: dict[str, list[tuple[int, str]]] = {}
    if args.text is not None:
        hits["arg"] = scan(args.text)
    elif args.html:
        try:
            hits[args.html] = scan(Path(args.html).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: 读取失败 {args.html}: {e}", file=sys.stderr)
            return 1
    elif args.dir:
        skip = {"node_modules", "dist", ".venv"}
        base = Path(args.dir)
        if base.is_dir():
            for f in sorted(base.rglob("*")):
                if f.suffix not in {".html", ".jsx", ".tsx", ".css", ".js"}:
                    continue
                if any(seg in f.parts for seg in skip):
                    continue
                try:
                    hits[str(f)] = scan(f.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue

    total = sum(len(v) for v in hits.values())
    for fname, hs in hits.items():
        for line, ch in hs:
            print(f"EMOJI  {fname}:{line}  U+{ord(ch):04X}  {ch!r}")
    if total == 0:
        print(f"[PASS] 未发现 emoji（扫描 {len(hits)} 处）")
        return 0
    if args.lenient or total <= args.allow:
        print(f"[WARN/PASS] {total} 个 emoji（lenient/allow）", file=sys.stderr)
        return 0
    print(f"[FAIL] emoji {total} > 允许 {args.allow}", file=sys.stderr)
    return 2


def main() -> int:
    rc = _run_shared()
    if rc is not None:
        return rc
    sys.stderr.write("[WARN] 未定位 Easel 共享版 check_no_emoji.py，使用内嵌最小实现兜底\n")
    return _fallback_main()


if __name__ == "__main__":
    sys.exit(main())
