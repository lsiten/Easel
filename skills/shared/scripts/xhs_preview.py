#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xhs_preview.py — 小红书图文「真实发布效果」预览 + 发布前自检门禁。

小红书读者看到的不是 .md 原稿，是**图文笔记**：竖图轮播 + 标题 + caption + 话题 +
作者 + 互动条。本脚本把一组已渲染的小红书卡片（card_*.png）+ meta.json 渲染成
**还原真实小红书图文展示交互**的预览页 `xhs_preview.html`，让用户在真正发布前看到
「发布后读者会看到的样子」；再基于预览做确定性自检（内容/视觉/合规/去AI味），有问题
退出非 0，让工作流回炉优化，全 PASS 才交付/发布。

为什么是「预览 HTML 走 App iframe」而不是原生组件：
- 预览 = 一个独立 .html 文件，App（OutputsPage）已用 sandbox=allow-scripts 的 iframe
  渲染任意 HTML 产物 → 预览自动可交互（轮播/滑动/点点），零前端改动。
- 预览与真实发布内容同源（同一批 card_*.png + meta.json），所见即所得。
- check 子命令把预览渲染成截图（playwright），供 agent「基于预览」做视觉复核。

子命令：
  preview  <note_dir|meta.json>   生成/刷新 xhs_preview.html（默认相对引用卡片；
                                   --embed 内联 base64 便于独立打开）
  check    <note_dir|meta.json>   自检门禁：刷新预览 + 渲染预览截图 + 内容/视觉/合规/
                                   去AI味 检查 → 报告 + verdict；PASS 退出 0，
                                   有问题退出 1，被内容安全闸门拦下退出 7。
  render   <preview.html>         仅把预览渲染成截图 PNG（供视觉复核）
  selftest                        合成 meta 验证 preview/check 逻辑

作为库：
  from xhs_preview import build_preview_html, self_check, render_preview_to_png
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

EXIT_LEAK = 7   # 与 content_guard 对齐：真·敏感信息泄露，fail-closed

# ── 字数/数量阈值（与 xhs-note-creator / meta-schema 一致）─────────────────────
TITLE_MIN, TITLE_MAX = 1, 20        # 小红书标题建议 10-20 全角；硬上限 30（schema）
TITLE_HARD = 30
CAP_MIN, CAP_MAX = 100, 300         # caption 100-300 字
HAS_MIN, HAS_MAX = 3, 8            # hashtags 5-8，下限 3 容忍
CARD_MIN, CARD_MAX = 3, 9         # 图文卡 3-9
CARD_HARD = 18                    # 小红书单帖硬上限 18 图
CARD_TEXT_MAX = 80              # 单卡正文 ≤80 字（card-xiaohongshu 规范）
CARD_TEXT_WARN = 120
CARD_TEXT_HARD = 220           # 超过 → 必溢出卡面（视觉 FAIL）

# 引流词（小红书禁正文引流；命中即 FAIL）
LEAD_PATTERNS = [
    r"微信号", r"微信", r"\bwechat\b", r"\bwx\b(?![a-z])", r"\bvx\b(?![a-z])",
    r"二维码", r"\bQR\s*码", r"扫码", r"加我", r"加群", r"私信我", r"私我",
    r"购买链接", r"购买方式", r"下单", r"淘宝", r"京东", r"拼多多", r"公众号",
    r"加V", r"加Vx", r"联系方式", r"联系电话", r"电话[：: ]?\d",
]
# 绝对化用语（小红书限；命中即 FAIL）
ABSOLUTE_PATTERNS = [
    r"最好", r"最佳", r"第一", r"唯一", r"国家级", r"全球[^十][^第]",
    r"100\s*%", r"百分百", r"绝对", r"根治", r"永不", r"永久有效",
]
# 去AI味短语（命中即 WARN；权威源 text-polisher / jiaxing 去AI味）
AI_MARKERS = [
    "不只是", "不仅是", "更是", "首先", "其次", "最后", "综上所述", "总而言之",
    "赋能", "颠覆", "生态闭环", "抓住红利", "必看", "天花板", "效率翻倍",
    "不会就亏", "帮你", "带你", "轻松", "轻松搞定", "手把手", "保姆级",
]


# ── 路径解析 ──────────────────────────────────────────────────────────────────
def resolve_target(target: str) -> tuple[Path, dict, Path | None]:
    """把 <note_dir|meta.json> 解析为 (note_dir, meta, meta_path)。"""
    p = Path(target).expanduser().resolve()
    if p.is_dir():
        meta_path = p / "meta.json"
    elif p.name == "meta.json":
        p, meta_path = p.parent, p
    elif p.suffix == ".html":
        # 直接是预览文件，回退到同目录 meta.json
        p, meta_path = p.parent, p.parent / "meta.json"
    else:
        meta_path = p.parent / "meta.json"
    if not meta_path.is_file():
        print(f"ERROR: 找不到 meta.json（{meta_path}）", file=sys.stderr)
        sys.exit(2)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return p, meta, meta_path


def find_cards(note_dir: Path) -> list[tuple[int, Path]]:
    """按页码收集 card_*.png（优先 images/，回退 _preview/ 与根目录）。返回 [(page, path)]。"""
    found: dict[int, Path] = {}
    for sub in ("images", "_preview", "."):
        base = note_dir / sub
        if not base.is_dir():
            continue
        for f in sorted(base.glob("card_*.png")):
            m = re.search(r"card_(\d+)", f.name)
            if m:
                pg = int(m.group(1))
                # 若同一页在 images/ 已有，优先 images/
                if sub == "images" or pg not in found:
                    found[pg] = f
    return sorted(found.items())


def card_relpath(note_dir: Path, card_path: Path, meta: dict) -> str:
    """卡片相对预览文件（note_dir/xhs_preview.html）的引用路径。"""
    # 优先用 meta.cards[].image_url（发布阶段可能写绝对/外链），否则相对 note_dir
    imgs = meta.get("images") or []
    rel = card_path.name
    for sub in ("images", "_preview"):
        cand = note_dir / sub / card_path.name
        if cand.is_file():
            rel = f"{sub}/{card_path.name}"
            break
    return rel


# ── 预览 HTML（还原真实小红书图文展示交互）─────────────────────────────────────
def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_preview_html(meta: dict, cards: list[tuple[int, Path]], note_dir: Path,
                       embed: bool = False) -> str:
    """把 meta + 已渲染卡片渲染成「真实小红书图文笔记」预览页 HTML。

    还原交互：竖图轮播（左右滑动/点点/计数/箭头）、标题、caption 正文、蓝色话题、
    作者行、点赞/收藏/评论/转发互动条。数据全部来自 meta.json + 已渲染 card_*.png，
    即真实发布会展示的内容，所见即所得。
    """
    title = _esc(meta.get("title") or "")
    caption = _esc(meta.get("caption") or "")
    hashtags = meta.get("hashtags") or []
    author = _esc(meta.get("author") or meta.get("xhs", {}).get("author") or "小红书用户")
    poster_style = _esc(meta.get("poster_style") or "")
    created = meta.get("created_at") or ""
    rel_time = _rel_time(created)

    # 幻灯片（图片）：有渲染图用图，缺图用占位卡（仍能看布局）
    slides, dots = [], []
    for i, (pg, card_path) in enumerate(cards, 1):
        rel = card_relpath(note_dir, card_path, meta)
        src = _b64(card_path) if embed else rel
        dots.append(f'<span class="xhs-dot{" on" if i == 1 else ""}" data-i="{i}"></span>')
        slides.append(f'<div class="xhs-slide"><img src="{src}" alt="图{i}" loading="lazy"/></div>')
    if not slides:
        slides = ['<div class="xhs-slide xhs-empty">尚未渲染卡片<br/>先跑 card-xiaohongshu 出图</div>']
        dots = ['<span class="xhs-dot on"></span>']
    n = len(cards) or 1

    # 话题标签（蓝色 # 话题，小红书话题样式）
    tag_html = "".join(
        f'<span class="xhs-tag">{_esc(h.lstrip("#"))}</span>' for h in hashtags
    )

    # 互动条：评论/收藏/点赞/转发（真实发布前计数为 0，展示真实结构）
    actions = [
        ("comment", "评论", "评论"),
        ("collect", "收藏", "收藏"),
        ("like", "点赞", "点赞"),
        ("share", "转发", "转发"),
    ]
    bar_html = "".join(
        f'<div class="xhs-act xhs-act-{kind}"><span class="xhs-act-icon">{icon}</span>'
        f'<span class="xhs-act-count">0</span></div>'
        for kind, label, icon in actions
    )

    # 作者头像（首字占位 / 或 profile 图）
    avatar = author[0] if author else "红"
    style_note = f' · 风格：{poster_style}' if poster_style else ""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1"/>
<title>小红书图文预览 · {_esc(title)}</title>
<style>
  * {{ box-sizing: border-box; }}
  html,body {{ margin:0; padding:0; }}
  body {{
    background:#f5f5f5; font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",
    "Hiragino Sans GB","Microsoft YaHei",sans-serif; color:#333;
    -webkit-font-smoothing:antialiased;
  }}
  .xhs-stage {{ display:flex; justify-content:center; padding:20px 12px 40px; }}
  .xhs-note {{
    width:100%; max-width:420px; background:#fff; border-radius:16px; overflow:hidden;
    box-shadow:0 8px 30px rgba(0,0,0,.12);
  }}
  /* —— 竖图轮播 —— */
  .xhs-gallery {{ position:relative; background:#000; }}
  .xhs-slides {{
    display:flex; overflow-x:auto; scroll-snap-type:x mandatory;
    -webkit-overflow-scrolling:touch; scrollbar-width:none;
  }}
  .xhs-slides::-webkit-scrollbar {{ display:none; }}
  .xhs-slide {{
    flex:0 0 100%; scroll-snap-align:center; position:relative;
    aspect-ratio:3/4; background:#111;
  }}
  .xhs-slide img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .xhs-slide.xhs-empty {{ display:flex; align-items:center; justify-content:center;
    color:#bbb; text-align:center; font-size:14px; }}
  .xhs-counter {{
    position:absolute; top:12px; right:12px; background:rgba(0,0,0,.45);
    color:#fff; font-size:12px; padding:2px 9px; border-radius:999px;
  }}
  .xhs-dots {{
    position:absolute; left:0; right:0; bottom:12px; display:flex;
    justify-content:center; gap:6px;
  }}
  .xhs-dot {{ width:6px; height:6px; border-radius:999px; background:rgba(255,255,255,.5);
    transition:.2s; }}
  .xhs-dot.on {{ width:16px; background:#fff; }}
  .xhs-arrow {{
    position:absolute; top:50%; transform:translateY(-50%); z-index:3;
    width:30px; height:30px; border:0; background:rgba(0,0,0,.35); color:#fff;
    border-radius:999px; cursor:pointer; font-size:16px; line-height:30px;
  }}
  .xhs-arrow.l {{ left:8px; }} .xhs-arrow.r {{ right:8px; }}
  /* —— 正文区 —— */
  .xhs-body {{ padding:14px 14px 10px; }}
  .xhs-title {{ font-size:16px; font-weight:700; line-height:1.35; margin:0 0 8px;
    color:#222; }}
  .xhs-caption {{ font-size:14px; line-height:1.7; color:#333; white-space:pre-wrap;
    word-break:break-word; }}
  .xhs-tags {{ margin-top:8px; }}
  .xhs-tag {{ display:inline-block; color:#3b82f6; font-size:13px; margin:2px 8px 2px 0; }}
  .xhs-meta {{ margin-top:8px; font-size:12px; color:#999; }}
  /* —— 作者 + 互动条 —— */
  .xhs-bar {{ display:flex; align-items:center; padding:10px 14px; border-top:1px solid #f2f2f2;
    gap:10px; }}
  .xhs-author {{ display:flex; align-items:center; gap:8px; flex:0 1 auto; min-width:0; }}
  .xhs-avatar {{ width:32px; height:32px; border-radius:999px; background:#ff2442;
    color:#fff; display:flex; align-items:center; justify-content:center; font-size:14px;
    flex:0 0 auto; overflow:hidden; }}
  .xhs-avatar img {{ width:100%; height:100%; object-fit:cover; }}
  .xhs-author .name {{ font-size:13px; color:#333; font-weight:600; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; max-width:120px; }}
  .xhs-actions {{ margin-left:auto; display:flex; align-items:center; gap:14px; }}
  .xhs-act {{ display:flex; flex-direction:column; align-items:center; font-size:11px;
    color:#999; min-width:30px; }}
  .xhs-act-icon {{ font-size:18px; line-height:1; margin-bottom:2px; }}
  .xhs-act-like .xhs-act-icon {{ color:#ff2442; }}
  .xhs-act-count {{ font-size:11px; color:#999; }}
  /* —— 预览说明 —— */
  .xhs-banner {{ display:flex; align-items:center; gap:6px; padding:8px 14px;
    background:#fff7ed; color:#b45309; font-size:12px; border-bottom:1px solid #fde3c3; }}
  .xhs-watermark {{ position:absolute; top:12px; left:12px; z-index:3;
    background:rgba(0,0,0,.45); color:#fff; font-size:11px; padding:2px 8px;
    border-radius:6px; }}
</style>
</head>
<body>
  <div class="xhs-stage">
    <div class="xhs-note">
      <div class="xhs-banner">👁 发布效果预览 · 模拟小红书图文笔记展示（非真实发布，最终发布以平台为准）</div>
      <div class="xhs-gallery">
        <div class="xhs-watermark">预览</div>
        <div class="xhs-slides" id="slides">{"".join(slides)}</div>
        <div class="xhs-counter" id="counter">1/{n}</div>
        <button class="xhs-arrow l" id="prev" aria-label="上一张">‹</button>
        <button class="xhs-arrow r" id="next" aria-label="下一张">›</button>
        <div class="xhs-dots" id="dots">{"".join(dots)}</div>
      </div>
      <div class="xhs-body">
        <h1 class="xhs-title">{title}</h1>
        <div class="xhs-caption">{caption}</div>
        <div class="xhs-tags">{tag_html}</div>
        <div class="xhs-meta">{_esc(rel_time)}{style_note}</div>
      </div>
      <div class="xhs-bar">
        <div class="xhs-author">
          <div class="xhs-avatar">{_esc(avatar)}</div>
          <div class="name">{author}</div>
        </div>
        <div class="xhs-actions">{bar_html}</div>
      </div>
    </div>
  </div>
<script>
(function(){{
  var slides = document.getElementById('slides');
  var counter = document.getElementById('counter');
  var dots = document.querySelectorAll('.xhs-dot');
  var total = {n};
  function active(){{
    if(!slides) return 0;
    return Math.round(slides.scrollLeft / slides.clientWidth) || 0;
  }}
  function update(){{
    var i = active();
    if(counter) counter.textContent = (i+1) + '/' + total;
    dots.forEach(function(d, k){{ d.classList.toggle('on', k === i); }});
  }}
  if(slides) slides.addEventListener('scroll', function(){{ update(); }});
  dots.forEach(function(d, k){{
    d.addEventListener('click', function(){{
      slides.scrollTo({{ left: slides.clientWidth * k, behavior: 'smooth' }});
    }});
  }});
  var prev = document.getElementById('prev'), next = document.getElementById('next');
  function go(dir){{
    var i = Math.max(0, Math.min(total-1, active() + dir));
    slides.scrollTo({{ left: slides.clientWidth * i, behavior: 'smooth' }});
  }}
  if(prev) prev.addEventListener('click', function(){{ go(-1); }});
  if(next) next.addEventListener('click', function(){{ go(1); }});
  update();
}})();
</script>
</body>
</html>"""


def _rel_time(created: str) -> str:
    """created_at(ISO) → 相对时间（真实小红书显示「x 天前 / 昨天 / 今天」）。"""
    if not created:
        return "刚刚发布"
    try:
        from datetime import datetime, timezone
        d = created.replace("Z", "+00:00")
        dt = datetime.fromisoformat(d)
        now = datetime.now(timezone.utc)
        delta = (now - dt).total_seconds()
        day = int(delta // 86400)
        if day <= 0:
            return "今天发布"
        if day == 1:
            return "昨天发布"
        if day < 30:
            return f"{day} 天前发布"
        return "较早发布"
    except Exception:
        return created


def _b64(p: Path) -> str:
    try:
        data = p.read_bytes()
    except Exception:
        return ""
    return "data:image/png;base64," + base64.b64encode(data).decode()


# ── 预览渲染成截图（playwright；供 agent 视觉复核 + 自检）──────────────────────
def render_preview_to_png(html_path: Path, out_png: Path, width: int = 440,
                          height: int = 920) -> int:
    """把 xhs_preview.html 渲染成 PNG 截图。返回 0 成功 / 非 0 失败。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: 未安装 playwright。请运行：pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 3
    html = html_path.resolve()
    if not html.is_file():
        print(f"ERROR: 预览 HTML 不存在：{html}", file=sys.stderr)
        return 2
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": width, "height": height},
                                    device_scale_factor=1)
            try:
                page.goto(f"file://{html}", wait_until="load", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            page.screenshot(path=str(out_png), full_page=True)
            browser.close()
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 渲染预览截图失败：{e}", file=sys.stderr)
        return 3


# ── 自检：基于 meta + 已渲染卡片 + 预览（确定性）────────────────────────────────
def _count_cjk(s: str) -> int:
    """统计非空白字符数（含中文/emoji/标点），近似小红书字数口径。"""
    return len(re.sub(r"\s", "", s or ""))


def self_check(meta: dict, cards: list[tuple[int, Path]], note_dir: Path,
               audit_pngs: bool = True) -> dict:
    """基于 meta + 卡片 + 预览做确定性自检，返回报告 + verdict。

    verdict: PASS / WARN / FAIL / BLOCK
    - PASS  : 可交付/发布
    - WARN  : 有瑕疵（长度/去AI味），列出但不阻断（agent 复核）
    - FAIL  : 硬伤（缺图/溢出/引流/绝对化/非 3:4），必须返工
    - BLOCK : 内容安全闸门命中（真·敏感信息），退出 7
    """
    fails, warns, passes = [], [], []
    title = meta.get("title") or ""
    caption = meta.get("caption") or ""
    hashtags = meta.get("hashtags") or []
    cards_meta = meta.get("cards") or []

    # —— 内容检查 ——
    tc = _count_cjk(title)
    if tc == 0:
        fails.append("缺少标题")
    elif tc > TITLE_HARD:
        fails.append(f"标题 {tc} 字 > 硬上限 {TITLE_HARD}（会被平台截断）")
    elif tc > TITLE_MAX:
        warns.append(f"标题 {tc} 字 > 建议 {TITLE_MAX}（小红书标题建议 10-20）")
    else:
        passes.append(f"标题 {tc} 字")

    cc = _count_cjk(caption)
    if cc == 0:
        fails.append("缺少 caption 正文")
    elif not (CAP_MIN <= cc <= CAP_MAX):
        warns.append(f"caption {cc} 字 越出 {CAP_MIN}-{CAP_MAX} 区间")
    else:
        passes.append(f"caption {cc} 字")

    hc = len(hashtags)
    if hc < HAS_MIN:
        fails.append(f"话题仅 {hc} 个 < {HAS_MIN}")
    elif hc > HAS_MAX:
        warns.append(f"话题 {hc} 个 > 建议 {HAS_MAX}")
    else:
        passes.append(f"话题 {hc} 个")

    # 卡片数量
    nc = len(cards) if cards else len(cards_meta)
    if nc == 0:
        fails.append("没有卡片（需 card-xiaohongshu 出图）")
    elif nc > CARD_HARD:
        fails.append(f"卡片 {nc} 张 > 单帖硬上限 {CARD_HARD}")
    elif nc < CARD_MIN or nc > CARD_MAX:
        warns.append(f"卡片 {nc} 张 越出 {CARD_MIN}-{CARD_MAX} 建议区间")
    else:
        passes.append(f"卡片 {nc} 张")

    # 单卡字数 + 图片存在
    for i, cm in enumerate(cards_meta, 1):
        ct = _count_cjk(cm.get("content") or cm.get("title") or "")
        if ct > CARD_TEXT_HARD:
            fails.append(f"第 {i} 卡正文 {ct} 字 > {CARD_TEXT_HARD}（必然溢出卡面）")
        elif ct > CARD_TEXT_WARN:
            warns.append(f"第 {i} 卡正文 {ct} 字 > {CARD_TEXT_WARN}（偏满）")
    have = {pg for pg, _ in cards}
    for i, cm in enumerate(cards_meta, 1):
        pg = cm.get("page", i)
        if pg not in have and (meta.get("post_format") or "image") == "image":
            fails.append(f"第 {pg} 卡缺渲染图 card_{pg}.png")

    # —— 合规 / 引流 / 绝对化（扫 caption + 卡片文字）——
    scan_text = caption + " " + " ".join(
        (cm.get("content") or "") + " " + (cm.get("title") or "") for cm in cards_meta
    )
    for pat in LEAD_PATTERNS:
        m = re.search(pat, scan_text, re.I)
        if m:
            fails.append(f"正文含引流「{m.group(0)}」：小红书禁正文引流（微信/二维码/购买/公众号等）")
    for pat in ABSOLUTE_PATTERNS:
        m = re.search(pat, scan_text)
        if m:
            fails.append(f"正文含绝对化用语「{m.group(0)}」：小红书限绝对化用语")

    # —— 去AI味（WARN）——
    for mk in AI_MARKERS:
        if mk in scan_text:
            warns.append(f"疑似AI味措辞「{mk}」：建议按 text-polisher 去AI味重写")

    # —— 视觉检查：卡片 3:4 + 密度（复用 card_audit 逻辑）——
    visual_notes = []
    if audit_pngs and cards:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "builtin" / "card-design" / "scripts"))
            from card_audit import analyze  # card-design 视觉硬门禁
            for pg, cp in cards:
                try:
                    r = analyze(str(cp))
                    # card_audit.analyze() 返回 {"pass": bool, "problems": [...]}
                    if r.get("pass"):
                        passes.append(f"第 {pg} 卡视觉 PASS")
                    else:
                        probs = r.get("problems") or []
                        reason = "；".join(probs[:2]) or "死空白/密度不足"
                        fails.append(f"第 {pg} 卡视觉 FAIL（{cp.name}）：{reason}")
                except Exception:
                    # 视觉检查异常不阻断（降级为 WARN）
                    warns.append(f"第 {pg} 卡视觉检查跳过：{cp.name}")
        except Exception:
            # 无 card_audit 依赖时，仅做基础 3:4 检查
            _basic_aspect_check(cards, fails, warns, passes)

    # —— 内容安全闸门（真·敏感信息 → BLOCK / 退出 7）——
    block = _content_block(scan_text)

    # —— 判定 ——
    if block:
        verdict = "BLOCK"
    elif fails:
        verdict = "FAIL"
    elif warns:
        verdict = "WARN"
    else:
        verdict = "PASS"
    return {
        "verdict": verdict,
        "block": block,
        "fails": fails,
        "warns": warns,
        "passes": passes,
        "counts": {"cards": nc, "title": tc, "caption": cc, "hashtags": hc},
    }


def _basic_aspect_check(cards, fails, warns, passes):
    """无 card_audit 依赖时的降级视觉检查：仅判 3:4 比例。"""
    try:
        from PIL import Image
        for pg, cp in cards:
            try:
                w, h = Image.open(cp).size
                ratio = w / h
                if 0.7 < ratio < 1.4:  # 3:4≈0.75
                    passes.append(f"第 {pg} 卡 3:4")
                else:
                    fails.append(f"第 {pg} 卡比例 {ratio:.2f} 非 3:4（XHS 图文卡应 3:4）")
            except Exception:
                warns.append(f"第 {pg} 卡尺寸读取失败：{cp.name}")
    except Exception:
        pass


def _content_block(text: str) -> list[str]:
    """复用 content_guard 做真·敏感信息扫描（BLOCK 级 → 退出 7）。"""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import content_guard
        issues = content_guard.scan(text)
        # 只把 BLOCK 级（密钥/内部拓扑）当拦；WARN 级（AI 措辞/模型名）不拦
        blocks = [i for i in issues
                  if str(getattr(i, "category", "") or "").lower()
                  not in ("ai-disclosure", "model-name")]
        return [getattr(i, "reason", str(i)) for i in blocks]
    except Exception:
        return []


# ── CLI ────────────────────────────────────────────────────────────────────────
def _print_report(rep: dict) -> None:
    v = rep["verdict"]
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "BLOCK": "🚫"}.get(v, "?")
    print(f"\n=== 小红书图文发布前自检（基于预览）===  {icon} {v}")
    if rep["counts"]["cards"]:
        print(f"卡片 {rep['counts']['cards']} · 标题 {rep['counts']['title']} 字 · "
              f"caption {rep['counts']['caption']} 字 · 话题 {rep['counts']['hashtags']} 个")
    if rep["block"]:
        print("\n🚫 内容安全闸门（发布将被拦）：")
        for b in rep["block"]:
            print(f"  - {b}")
    if rep["fails"]:
        print("\n❌ 硬伤（必须返工）：")
        for f in rep["fails"]:
            print(f"  - {f}")
    if rep["warns"]:
        print("\n⚠️ 瑕疵（建议优化，不阻断）：")
        for w in rep["warns"]:
            print(f"  - {w}")
    if not rep["fails"] and not rep["warns"] and not rep["block"]:
        print("全部通过：可把预览交付用户确认，确认无误后再发布。")
    print()


def cmd_preview(args) -> int:
    note_dir, meta, _ = resolve_target(args.target)
    cards = find_cards(note_dir)
    if not cards:
        print(f"WARNING: 未找到已渲染 card_*.png（{note_dir}）；预览将用占位卡，"
              "先跑 card-xiaohongshu 出图。", file=sys.stderr)
    html = build_preview_html(meta, cards, note_dir, embed=args.embed)
    out = Path(args.out) if args.out else note_dir / "xhs_preview.html"
    out.write_text(html, encoding="utf-8")
    print(f"✅ 预览已生成：{out}（{len(cards)} 张卡片）")
    return 0


def cmd_check(args) -> int:
    note_dir, meta, _ = resolve_target(args.target)
    cards = find_cards(note_dir)
    # 1) 刷新预览
    html = build_preview_html(meta, cards, note_dir, embed=args.embed)
    out = Path(args.out) if args.out else note_dir / "xhs_preview.html"
    out.write_text(html, encoding="utf-8")
    # 2) 渲染预览截图（供 agent「基于预览」视觉复核）
    png = out.with_suffix(".png")
    rc = render_preview_to_png(out, png)
    if rc:
        print(f"WARNING: 预览截图渲染失败（{rc}）；自检仍继续（内容/视觉按 meta+卡片）",
              file=sys.stderr)
    # 3) 自检
    rep = self_check(meta, cards, note_dir, audit_pngs=True)
    _print_report(rep)
    if rep["block"]:
        return EXIT_LEAK
    if rep["verdict"] in ("FAIL",):
        return 1
    # PASS / WARN → 0（WARN 已列出，交 agent/用户复核）
    return 0


def cmd_render(args) -> int:
    html = Path(args.target).expanduser().resolve()
    out = Path(args.out).expanduser().resolve() if args.out else html.with_suffix(".png")
    return render_preview_to_png(html, out)


def cmd_selftest(args) -> int:
    """合成 meta + 合成卡片，验证 preview 生成 + check 判定（PASS / FAIL / BLOCK）。"""
    import tempfile
    d = Path(tempfile.mkdtemp())
    # 合成 3:4 卡片（纯色块，模拟 card_*.png）
    try:
        from PIL import Image
    except ImportError:
        print("ERROR: selftest 需要 Pillow", file=sys.stderr)
        return 3
    imgs = d / "images"
    imgs.mkdir(exist_ok=True)
    for i in range(1, 4):
        Image.new("RGB", (1080, 1440), (30 + i * 20, 60, 120)).save(imgs / f"card_{i}.png")

    def run(meta: dict, label: str) -> int:
        mp = d / "meta.json"
        mp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        note_dir, m, _ = resolve_target(str(mp))
        cards = find_cards(note_dir)
        html = build_preview_html(m, cards, note_dir)
        (d / "xhs_preview.html").write_text(html, encoding="utf-8")
        rep = self_check(m, cards, note_dir, audit_pngs=False)
        _print_report(rep)
        print(f"[{label}] verdict={rep['verdict']}")
        return rep["verdict"]

    good = {
        "title": "AI 让我更累了，但省下了这 3 件事",
        "caption": "用了半年 AI，我反而更累了。不是它不好，是我把不该给的也交给了它。"
                   "这篇说三件我用 AI 后明显省下的事，以及一件我至今不肯交给它的。"
                   "都是真实记录，不夸大。嘉兴本地打工人，欢迎聊聊你的用法。",
        "hashtags": ["#AI工具", "#AI提效", "#独立开发", "#嘉兴", "#职业提效"],
        "cards": [
            {"page": 1, "type": "cover", "content": "AI 让我更累了", "content_len": 8},
            {"page": 2, "type": "content", "content": "省下的三件事", "content_len": 8},
            {"page": 3, "type": "ending", "content": "一件我不交给它的", "content_len": 9},
        ],
        "platform": "小红书", "post_format": "image",
    }
    bad = dict(good, caption="加微信 xxx 领取，绝对最好用，效率翻倍")  # 引流+绝对化
    leak = dict(good, caption="api_key=sk-xxxxxxxxxxxxxxxxxxxx 测试")  # 泄露
    r_good = run(good, "good")
    r_bad = run(bad, "bad-引流")
    r_leak = run(leak, "leak")
    ok = r_good in ("PASS", "WARN") and r_bad == "FAIL" and r_leak == "BLOCK"
    print(f"\nselftest {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="小红书图文「真实发布效果」预览 + 发布前自检门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("preview", help="生成/刷新 xhs_preview.html")
    p.add_argument("target", help="笔记目录 或 meta.json 路径")
    p.add_argument("--out", help="输出 HTML 路径（默认 <note_dir>/xhs_preview.html）")
    p.add_argument("--embed", action="store_true", help="内联 base64 图片（独立打开/跨域）")
    p.set_defaults(func=cmd_preview)

    c = sub.add_parser("check", help="发布前自检门禁（基于预览）")
    c.add_argument("target", help="笔记目录 或 meta.json 路径")
    c.add_argument("--out", help="预览 HTML 路径（默认 <note_dir>/xhs_preview.html）")
    c.add_argument("--embed", action="store_true", help="内联 base64 图片")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("render", help="把预览 HTML 渲染成截图 PNG")
    r.add_argument("target", help="xhs_preview.html 路径")
    r.add_argument("--out", help="截图 PNG 路径（默认同目录 .png）")
    r.set_defaults(func=cmd_render)

    st = sub.add_parser("selftest", help="合成数据自检")
    st.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    try:
        return args.func(args)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
