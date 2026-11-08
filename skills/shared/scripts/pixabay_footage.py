#!/usr/bin/env python3
"""pixabay_footage.py — 从 Pixabay 免费视频库抓取现成的实拍 / 空镜 / B-roll（Ego 浏览器 + 直链下载）。

视频制作需要的**真实动态画面**（motion_clip，不是把静图用 Ken Burns 冒充动态）从 pixabay.com 视频库获取：
- 用 Ego 浏览器（真实指纹，绕过 pixabay 对裸请求的 403）打开视频搜索页；
- 点击 download 触发下载事件，拿到 CDN 直链（cdn.pixabay.com/download/video/…mp4）；
- 再用 urllib 直连下载 MP4 —— CDN 直链可直接拉取，浏览器只负责「拿到直链」。
产物落到 outputs/<主题>/video/，或经 --to-asset 登记进画像素材库（assets.py），供 OpenFilm / 一键短视频取用。

免登录、免 API key、免版税（no-copyright / 可商用、无需署名）—— 无需任何账号或密钥配置。

与 pixabay-music（同结构，取音乐）区别：本脚本取**视频画面**，供分镜 `motion_clip` 段用真实动态画面；
与 ai-video-gen（AI 生成视频）区别：本脚本是从 Pixabay 免费库**抓取现成实拍**，不需要 provider / API key；
与 ai-image-gen + Ken Burns（静图缓动）区别：本脚本给**真动态画面**，用于「这段该有真实运动」的分镜。

为什么用 Ego 而不是直连：pixabay.com 主站对裸 HTTP 请求返回 403（反爬），需真实浏览器；
但下载的是 cdn.pixabay.com 直链，可直接 urllib 拉取。故浏览器只负责「拿到直链」。

子命令：
    check                              能力探测（Ego 浏览器 + Pixabay 可达；--json 机读）
    list <关键词> [--limit N]          列出搜索结果（编号 + 标题 + 作者 + 时长），供挑选
    get  <关键词> [--pick N] [--out P] [--to-asset <画像> [--category 视频/空镜]]
                                       下载第 N 段（默认第 1 段）到 P（默认 outputs/<主题>/video/），
                                       可选登记进画像素材库（assets.py place）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

# ── 输出路径规约（与 Easel 其余 shared 脚本一致）────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from output_paths import validate_output_path, OutputPathError  # type: ignore
except Exception:  # noqa: BLE001
    class OutputPathError(Exception):  # type: ignore
        pass

    def validate_output_path(value, **_kwargs):  # type: ignore
        return Path(value).expanduser()

# ── 颜色（与 install_tool / pixabay_music 一致）──────────────────────────────
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
NC = "\033[0m"


def _eprint(*parts: str) -> None:
    print(*parts, file=sys.stderr)


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PIXBAY_HOME = "https://pixabay.com/"

# 节点脚本：Ego 浏览器打开视频搜索页 → 等 download 按钮 → 点第 N 个 → 抓下载事件 → 打印直链。
# 与 pixabay_music 一致，用 Ego 的独立 CDP 会话；download 事件在 CDP 上下文可稳定拿到。
_NODE_SCRIPT = r"""
const __CFG = __CFG__;
const task = await taskSpace("easel-pixabay-footage");
const page = task.page("p1");
const MODE = __CFG.mode;
const KW = __CFG.keyword;
const PICK = __CFG.pick;
const LIMIT = __CFG.limit;
await page.goto("https://pixabay.com/videos/search/" + encodeURIComponent(KW), { waitUntil: "domcontentloaded" }).catch(()=>{});
await page.waitForLoadState("networkidle").catch(()=>{});
await page.waitForTimeout(1500);
let seen = 0;
for (let i = 0; i < 60; i++) {
  const n = await page.evaluate(() => document.querySelectorAll('[aria-label="Download"]').length);
  if (n > 0) { seen = n; break; }
  // 视频结果常懒加载：滚动触发加载
  if (i % 3 === 0) await page.evaluate(() => window.scrollBy(0, 1200)).catch(()=>{});
  await page.waitForTimeout(1000);
}
if (seen === 0) { console.log("PX_URL=\n"); console.log("PX_NAME=\n"); await task.finish({ keep: [] }).catch(()=>{}); process.exit(0); }
// 注意：不要 scrollTo(0,0)——视频卡片懒加载，滚回顶部会卸载已加载的卡片；get 的点击自带 scrollIntoView。
await page.waitForTimeout(800);
// list：从 download 按钮向上定位卡片，抽取标题 + 作者 + 时长（视频卡片标题常在 href slug 而非文字）
if (MODE === "list") {
  const limit = LIMIT;
  const meta = await page.evaluate((limit) => {
    const out = [];
    const btns = [...document.querySelectorAll('[aria-label="Download"]')].slice(0, limit);
    for (const btn of btns) {
      let card = btn;
      for (let i = 0; i < 16 && card; i++) {
        const a = card.querySelector && card.querySelector('a[href*="/videos/"]');
        if (a) break;
        card = card.parentElement;
      }
      const root = card || btn;
      const link = root.querySelector('a[href*="/videos/"]') || root.querySelector('a[href]');
      let title = (link && (link.textContent || "").trim()) || "";
      let author = "";
      let duration = "";
      if (!title) {
        const href = (link && link.getAttribute("href")) || "";
        const m = href.match(/\/videos\/([^\s\/]+)\/?/);
        if (m) { const slug = m[1].replace(/-\d+$/, ""); title = slug.replace(/-/g, " "); }
      }
      const links = [...root.querySelectorAll('a[href]')];
      for (const a of links) { const x = (a.textContent || "").trim(); if (x && x !== title) { author = x; break; } }
      const durEl = root.querySelector('[class*="duration"], [class*="time"], time');
      if (durEl) duration = (durEl.textContent || "").trim();
      out.push({ title: title.slice(0, 60), author: author.slice(0, 40), duration: duration.slice(0, 12) });
    }
    return out;
  }, limit);
  const rows = (meta || []).map((m, i) => (i + 1) + ". " + (m.title || "(无标题)") + "  - " + (m.author || "") + (m.duration ? "  [" + m.duration + "]" : ""));
  console.log("PX_TRACKS=" + JSON.stringify(rows));
  await task.finish({ keep: [] }).catch(()=>{});
  process.exit(0);
}
// get：点真正的 button（父级 + 图标都试），抓下载事件拿直链
const dlP = page.waitForEvent("download", { timeout: 22000 }).catch(() => null);
async function clickTarget() {
  return await page.evaluate((idx) => {
    const all = document.querySelectorAll('[aria-label="Download"]');
    const b = all[Math.max(0, Math.min(idx - 1, all.length - 1))];
    if (!b) return false;
    b.scrollIntoView({ block: "center" });
    const realBtn = b.closest("button");
    if (realBtn) realBtn.click();
    b.click();
    return true;
  }, PICK);
}
let captured = "", name = "";
for (let attempt = 1; attempt <= 3; attempt++) {
  const clicked = await clickTarget();
  let dl = null;
  try { dl = await Promise.race([dlP, new Promise((r) => setTimeout(() => r(null), 20000))]); } catch (e) {}
  if (dl) {
    captured = (typeof dl.url === "function" ? dl.url() : dl.url) || "";
    name = (typeof dl.suggestedFilename === "function" ? dl.suggestedFilename() : dl.suggestedFilename) || "";
    break;
  }
  await page.waitForTimeout(1500);
}
console.log("PX_URL=" + captured);
console.log("PX_NAME=" + name);
await task.finish({ keep: [] }).catch(()=>{});
"""


def _ego_browser_path() -> str | None:
    from shutil import which
    return which("ego-browser")


def _run_capture(keyword: str, *, mode: str = "get", pick: int = 1,
                 limit: int = 20, timeout: int = 200) -> dict:
    """跑一次 Ego 浏览器抓取，返回 {url, name, tracks}。"""
    ego = _ego_browser_path()
    if not ego:
        raise RuntimeError("未找到 ego-browser CLI（请安装 Ego / ego-browser）。")
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "pixabay_footage.mjs"
        script_path.write_text(_NODE_SCRIPT, encoding="utf-8")
        env = dict(os.environ)
        cfg = {
            "mode": mode, "keyword": keyword,
            "pick": pick, "limit": limit,
        }
        script_text = script_path.read_text(encoding="utf-8").replace(
            "const __CFG = __CFG__;", "const __CFG = " + json.dumps(cfg, ensure_ascii=False) + ";")
        proc = subprocess.run(
            [ego, "nodejs"],
            input=script_text,
            capture_output=True, text=True, env=env, timeout=timeout,
        )
    out = (proc.stdout or "") + (proc.stderr or "")
    url = re.search(r"^PX_URL=(.*)$", out, re.MULTILINE)
    name = re.search(r"^PX_NAME=(.*)$", out, re.MULTILINE)
    tracks = re.search(r"^PX_TRACKS=(.*)$", out, re.MULTILINE)
    return {
        "url": url.group(1).strip() if url else "",
        "name": name.group(1).strip() if name else "",
        "tracks": json.loads(tracks.group(1)) if tracks else [],
        "raw": out[-800:],
        "rc": proc.returncode,
    }


def _download(url: str, dest: Path) -> int:
    """urllib 直连下载 CDN 直链到 dest，返回字节数。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": PIXBAY_HOME, "Accept": "video/*,*/*"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    dest.write_bytes(data)
    return len(data)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w\.\-]+", "_", (name or "clip").strip())
    return name[:80] or "clip"


# ── 子命令 ────────────────────────────────────────────────────────────
def cmd_check(args) -> int:
    ok = True
    lines = []
    try:
        from easel.browser import select_browser
        choice = select_browser()
        lines.append(f"{GREEN}OK{NC} Ego/浏览器：{choice.name}（{choice.executable}）")
    except Exception as e:  # noqa: BLE001
        ok = False
        lines.append(f"{RED}✗{NC} Ego/浏览器：{e}")
    ego_ok = bool(_ego_browser_path())
    lines.append(
        (f"{GREEN}OK{NC} ego-browser CLI：{_ego_browser_path()}" if ego_ok
         else f"{RED}✗{NC} 未找到 ego-browser CLI"))
    ok = ok and ego_ok
    try:
        from playwright.sync_api import sync_playwright
        _ = sync_playwright
        lines.append(f"{GREEN}OK{NC} playwright（Ego 会话依赖）")
    except Exception:  # noqa: BLE001
        ok = False
        lines.append(f"{YELLOW}!{NC} 未安装 playwright（`pip install playwright`）")
    print("Pixabay 视频/空镜 — 能力探测\n")
    for x in lines:
        print(x)
    print()
    print(f"{GREEN}✓ 就绪{NC}" if ok else f"{YELLOW}⚠ 未就绪{NC} — 安装 Ego / ego-browser 后重试")
    if args.json:
        print(json.dumps({"ok": ok, "ego": ego_ok}, ensure_ascii=False))
    return 0 if ok else 1


def cmd_list(args) -> int:
    res = _run_capture(args.keyword, mode="list", limit=args.limit)
    if not res["url"] and not res["tracks"]:
        _eprint(f"{RED}✗{NC} 未在 Pixabay 抓取到结果；见日志：\n{res['raw']}")
        return 1
    if args.json:
        print(json.dumps({"keyword": args.keyword, "tracks": res["tracks"]},
                         ensure_ascii=False, indent=2))
        return 0
    rows = res["tracks"] or ["（未取到标题列表，可直接 get 取第 1 段）"]
    print(f"「{args.keyword}」Pixabay 视频/空镜搜索结果：")
    for r in rows:
        print(f"  {r}")
    if len(rows) < 20:
        print(f"\n（仅取到 {len(rows)} 段；可 `get {args.keyword} --pick N` 指定）")
    return 0


def _resolve_out(out: str | None, keyword: str) -> Path:
    if out:
        try:
            return validate_output_path(out, create_parent=False)
        except OutputPathError:
            p = Path(out).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
    slug = re.sub(r"[^\w]+", "_", keyword).strip("_").lower() or "footage"
    return Path("outputs") / slug / "video"


def cmd_get(args) -> int:
    res = _run_capture(args.keyword, mode="get", pick=args.pick)
    if not res["url"]:
        _eprint(f"{RED}✗{NC} 未拿到下载直链；见日志：\n{res['raw']}")
        return 1
    url = res["url"]
    name = res["name"] or _safe_name(args.keyword)
    if not name.lower().endswith((".mp4", ".webm", ".mov", ".mkv")):
        name += ".mp4"
    dest = _resolve_out(args.out, args.keyword) / _safe_name(name)
    _eprint(f"下载 {url}")
    n = _download(url, dest)
    _eprint(f"已保存：{dest}（{n} 字节）")
    if getattr(args, "to_asset", None):
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import assets
            category = args.category or "视频 / 空镜"
            tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()] or [args.keyword]
            r = assets.place(args.to_asset, str(dest), category, tags=tags,
                            summary=f"Pixabay 免版税实拍/空镜：{name}", kind="video")
            if r.get("ok"):
                _eprint(f"{GREEN}✓{NC} 已登记进 {args.to_asset} 素材库：{category} / {dest.name}")
            else:
                _eprint(f"{YELLOW}!{NC} 素材库登记失败：{r.get('error')}")
        except Exception as e:  # noqa: BLE001
            _eprint(f"{YELLOW}!{NC} 素材库登记出错：{e}")
    if args.json:
        print(json.dumps({"url": url, "name": name, "path": str(dest),
                          "bytes": n, "keyword": args.keyword}, ensure_ascii=False))
    else:
        print(f"{GREEN}✓{NC} 已下载 Pixabay 视频/空镜：{dest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="从 Pixabay 免费视频库抓取实拍 / 空镜 / B-roll（Ego 浏览器 + 直链下载）。")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("check", help="能力探测（Ego 浏览器 + 依赖）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("list", help="列出搜索结果（标题 / 作者 / 时长），供挑选")
    p.add_argument("keyword", help="搜索关键词 / 场景 / 动作（如 city skyline / 海浪 / timelapse）")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("get", help="下载第 N 段到 --out（默认 outputs/<主题>/video/）")
    p.add_argument("keyword", help="搜索关键词 / 场景 / 动作")
    p.add_argument("--pick", type=int, default=1, help="取第几段（默认 1）")
    p.add_argument("-o", "--out", default=None, help="输出目录（默认 outputs/<主题>/video/）")
    p.add_argument("--to-asset", default=None, metavar="画像",
                   help="下载后登记进该画像素材库（assets.py place）")
    p.add_argument("--category", default="视频 / 空镜", help="素材库分类（--to-asset 时用）")
    p.add_argument("--tags", default=None, help="素材库标签，逗号分隔")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_get)

    return ap


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, "cmd", None):
        ap.print_help()
        return 0
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
