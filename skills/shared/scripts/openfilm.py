#!/usr/bin/env python3
"""openfilm.py — Easel 操作 OpenFilm（视频制作引擎 + Studio 可视化）的统一封装。

OpenFilm 把"编码 agent"变成"视频 agent"：电影 = 含 film.html 的文件夹，镜头由
frame(t) 页面绘制，Studio（本机浏览器）实时可视化与编辑，render 出 MP4。
本脚本是 Easel 驱动 OpenFilm 的程序化入口（供 shared 脚本 / skill / doctor / web 复用）：

- 解析 openfilm 运行器（优先级）：EASEL_OPENFILM > 子项目 subprojects/openfilm/.bin/openfilm
  > 全局 openfilm > `npx -y openfilm@latest`
- 透传 openfilm 命令：open / render / look / get / skills / skill / canvas / doctor / update
- check / install：能力探测与安装（Node 22.2+ / ffmpeg / openfilm / 浏览器 / Studio）
- 把电影项目落在 outputs/<主题>/ 下，render 出的 MP4 归入 outputs/<主题>/

OpenFilm 自身的"操作 skill"（edit / media / canvas / …）用 `openfilm skills` 与
`openfilm skill <name>` 读取——Easel 制作视频时据此操作，不自行发明 film.html 写法。

用法：
    python openfilm.py check            # 能力探测（--json 机读）
    python openfilm.py install          # 安装 openfilm 并下载渲染浏览器（约 100MB）
    python openfilm.py open <主题>       # 在 outputs/<主题>/ 新建/打开电影并启动 Studio（可视化）
    python openfilm.py render [-p 主题] [a-b]     # 渲染 MP4 到 outputs/<主题>/<主题>.mp4
    python openfilm.py <cmd> [args...]  # 其余 openfilm 命令透传
    python openfilm.py frame [主题] [t|a-b]  # 取视频帧（绝对路径，供 view_image 自检）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── 输出路径规约（与 Easel 其余 shared 脚本一致）────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from output_paths import validate_output_path, OutputPathError  # type: ignore
except Exception:  # noqa: BLE001  缺规约库时退化为裸路径
    class OutputPathError(Exception):  # type: ignore
        pass

    def validate_output_path(value, **_kwargs):  # type: ignore
        return Path(value).expanduser()


# ── 颜色（与 install_tool / doctor 一致）──────────────────────────────────
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[0;33m"
NC = "\033[0m"


def _eprint(*parts: str) -> None:
    print(*parts, file=sys.stderr)


def _as_bool(text: str) -> bool:
    return str(text).strip().lower() in ("1", "true", "yes", "on")


# ── 运行器解析：EASEL_OPENFILM > 子项目 > 全局 > npx ───────────────────────
def project_root() -> Path:
    env = os.environ.get("EASEL_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    for parent in (Path(__file__).resolve().parents[2], *Path(__file__).resolve().parents):
        if (parent / "skills" / "shared" / "scripts").is_dir():
            return parent
    return Path(__file__).resolve().parents[2]


def _candidate_runners() -> list[tuple[str, tuple[str, ...]]]:
    """按优先级返回 (label, argv-prefix)。"""
    out: list[tuple[str, tuple[str, ...]]] = []
    env_bin = os.environ.get("EASEL_OPENFILM", "").strip()
    if env_bin:
        out.append(("EASEL_OPENFILM", (env_bin,)))
    # 子项目（git submodule）：仅在已构建出可执行 bin 时直接用，否则回退 npx
    subproj = project_root() / "subprojects" / "openfilm"
    built = (subproj / "node_modules").is_dir() or any(
        (subproj / rel).is_file() for rel in
        ("packages/openfilm/bin/openfilm.mjs", ".bin/openfilm", "bin/openfilm.mjs"))
    if built:
        for rel in ("packages/openfilm/bin/openfilm.mjs", ".bin/openfilm", "bin/openfilm.mjs"):
            cand = subproj / rel
            if cand.is_file():
                runner = ("node",) if cand.suffix == ".mjs" else (cand,)
                out.append(("subproject", runner + (str(cand),) if runner[0] == "node" else (str(cand),)))
                break
    g = shutil.which("openfilm")
    if g:
        out.append(("global", (g,)))
    # npx 兜底（首次下载后缓存）
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    if npx:
        out.append(("npx", (npx, "-y", "openfilm@latest")))
    elif npm:
        out.append(("npm", (npm, "exec", "--yes", "openfilm@latest")))
    return out


def resolve_runner() -> tuple[str, tuple[str, ...]] | None:
    for label, prefix in _candidate_runners():
        return label, prefix
    return None


def _run(prefix: tuple[str, ...], args: list[str], cwd: Path | None = None,
         timeout: int | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
    cmd = [str(x) for x in prefix] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                              capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "超时"
    except FileNotFoundError:
        return 127, f"找不到运行器：{prefix[0]}"


def openfilm(*args: str, cwd: Path | None = None, timeout: int | None = None,
            no_browser: bool = False,
            extra_env: dict[str, str] | None = None) -> tuple[int, str, tuple[str, ...] | None]:
    """跑 openfilm；返回 (rc, output, runner)。extra_env 覆盖/补充运行环境——1 台共享 Studio
    （默认 home，所有会话/AI 操作共用，不各起一台）；Easel 仅按会话注入 EASEL_SESSION_KEY 供 openfilm
    把本项目的 film 预览/editor 地址按会话落盘，实现按项目 film 预览隔离。"""
    runner = resolve_runner()
    if runner is None:
        return 1, "未找到 openfilm（请 `python openfilm.py install` 或设 EASEL_OPENFILM）。", None
    label, prefix = runner
    env = dict(os.environ)
    # 让 OpenFilm 运行时发现「子项目自带的 openfilm 操作 skill」：否则 `openfilm skills` 报
    # 「No skills installed」，Agent 会误判「未装任何 skill」。仅当未显式设置且子项目确有该 skill
    # 时注入（指向 subprojects/openfilm/skills，里面含 openfilm 的 SKILL.md）。
    if not env.get("OPENFILM_SKILLS"):
        sub_skills = project_root() / "subprojects" / "openfilm" / "skills"
        if (sub_skills / "openfilm" / "SKILL.md").is_file():
            env["OPENFILM_SKILLS"] = str(sub_skills)
    if no_browser:
        env["OPENFILM_NO_BROWSER"] = "1"
    if extra_env:
        env.update(extra_env)
    rc, out = _run(prefix, [str(a) for a in args], cwd=cwd, timeout=timeout, env=env)
    return rc, out, (label,)


# ── 能力探测 ─────────────────────────────────────────────────────────────
def _node_ok() -> tuple[bool, str]:
    node = shutil.which("node")
    if not node:
        return False, "Node.js 缺失"
    try:
        out = subprocess.run([node, "-v"], capture_output=True, text=True, timeout=30)
        ver = (out.stdout or "").strip()
        parts = [int(x) for x in ver.lstrip("v").split(".") if x.isdigit()]
        ok = bool(parts) and (parts[0], parts[1] if len(parts) > 1 else 0) >= (22, 2)
        return ok, ver
    except Exception:  # noqa: BLE001
        return False, "Node.js 版本探测失败"


def _ffmpeg_ok() -> tuple[bool, str]:
    ff = shutil.which("ffmpeg")
    if not ff:
        return False, "ffmpeg 缺失"
    try:
        out = subprocess.run([ff, "-version"], capture_output=True, text=True, timeout=30)
        line = (out.stdout or "").splitlines()[0] if out.stdout else "ffmpeg"
        return True, line.strip()
    except Exception:  # noqa: BLE001
        return True, "ffmpeg"


def _openfilm_rc(text: str) -> tuple[bool, str]:
    """从 `openfilm --version`/`doctor` 输出判断可用性与版本。"""
    import re
    ok = True
    ver = ""
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        # 直接抓任意行里的 semver 形如 0.3.0 / 1.0.0
        if not ver:
            m = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", s)
            if m:
                ver = m.group(1)
        if s.startswith("✗"):
            ok = False
    return ok, ver


def _browser_ready() -> tuple[bool, str]:
    """渲染浏览器是否已下载（openfilm 首次 open 时下载，约 100MB）。"""
    rc, out, _ = openfilm("doctor", timeout=120)
    if rc not in (0, 1):
        return False, f"openfilm doctor 不可用（rc={rc}）"
    for line in out.splitlines():
        s = line.strip()
        if "Browser" not in s:
            continue
        # 形如 "✓ Browser  ..." / "✗ Browser  not downloaded yet"
        ok = s.startswith("✓") and not s.startswith("✗")
        if not ok and ("not downloaded" in s.lower() or "not " in s.lower()):
            ok = False
        detail = s.split("Browser", 1)[-1].strip() if "Browser" in s else s
        return ok, detail
    return True, "（未探测到 Browser 行，假定已就绪）"


def capability() -> dict:
    runner = resolve_runner()
    node_ok, node_detail = _node_ok()
    ff_ok, ff_detail = _ffmpeg_ok()
    if runner is None:
        of_ok, of_detail, of_ver = False, "未找到 openfilm 运行器", ""
    else:
        rc, out, _ = openfilm("--version", timeout=90)
        of_ok = rc == 0
        _, of_ver = _openfilm_rc(out)
        of_detail = out.strip().splitlines()[0] if out.strip() else ("openfilm 可用" if of_ok else f"rc={rc}")
    browser_ok, browser_detail = (True, "未检查") if not of_ok else _browser_ready()
    all_ok = of_ok and node_ok and ff_ok
    return {
        "available": of_ok,
        "node": {"ok": node_ok, "detail": node_detail},
        "ffmpeg": {"ok": ff_ok, "detail": ff_detail},
        "openfilm": {"ok": of_ok, "version": of_ver, "detail": of_detail},
        "browser": {"ok": browser_ok, "detail": browser_detail},
        "runner": [runner[0], " ".join(runner[1])] if runner else None,
    }


def _check_json(cap: dict) -> dict:
    return {
        "ok": cap["available"] and cap["node"]["ok"] and cap["ffmpeg"]["ok"],
        "available": cap["available"],
        "node": cap["node"]["detail"] if cap["node"]["ok"] else None,
        "ffmpeg": cap["ffmpeg"]["detail"] if cap["ffmpeg"]["ok"] else None,
        "openfilm": f"{cap['openfilm'].get('version') or 'unknown'}" if cap["available"] else None,
        "runner": cap["runner"],
        "browser": cap["browser"]["ok"],
    }


# ── 子命令 ───────────────────────────────────────────────────────────────
def cmd_check(args: argparse.Namespace) -> int:
    cap = capability()
    if args.json:
        print(json.dumps(_check_json(cap), ensure_ascii=False))
        return 0 if _check_json(cap)["ok"] else 1
    print("OpenFilm — 能力探测\n")
    for label, item in (
        ("Node.js (>= 22.2)", cap["node"]),
        ("ffmpeg", cap["ffmpeg"]),
        ("openfilm", cap["openfilm"]),
        ("渲染浏览器（Chromium，约 100MB）", cap["browser"]),
    ):
        ok = item["ok"]
        mark = f"{GREEN}OK{NC}" if ok else f"{RED}FAIL{NC}"
        detail = item.get("detail") or item.get("version") or ""
        print(f"  {label:<40s} {mark}")
        if not ok and detail:
            print(f"    └─ {detail}")
    if cap["runner"]:
        print(f"  {str(cap['runner'][0]):<40s} {'(运行器)'}")
    print()
    ok = _check_json(cap)["ok"]
    print(f"{GREEN}✓ 就绪{NC}" if ok else f"{YELLOW}⚠ 未就绪{NC} — 运行 python openfilm.py install")
    return 0 if ok else 1


def cmd_install(args: argparse.Namespace) -> int:
    npm = shutil.which("npm")
    if not npm:
        _eprint("未找到 npm；请先安装 Node.js 22.2+（https://nodejs.org/）。")
        return 1
    print("安装 openfilm（npm i -g openfilm）…")
    rc = subprocess.run([npm, "install", "-g", "openfilm"], capture_output=False).returncode
    if rc != 0:
        _eprint("npm 安装失败；可改用 npx 兜底（无需全局安装）。")
        return rc
    # 下载渲染浏览器（首次 open 也会下载；这里显式拉一次以便离线可 render）
    if not args.skip_browser:
        print("下载渲染浏览器（约 100MB，一次）…")
        rc, out = openfilm("doctor", timeout=600)
        if "Browser" in out and ("✗" in out or "not downloaded" in out.lower()):
            # 触发一次 open 以下载浏览器（不实际开浏览器）
            rc, _, _ = openfilm("open", ".", timeout=1200, no_browser=True)
    print("openfilm 安装完成；运行 python openfilm.py check 复检。")
    return 0


def _project_dir(topic: str) -> Path:
    """把电影项目落在 outputs/<主题>/ 下；返回项目目录。

    topic 可以是裸主题名（如「用 AI 做一支视频」），也可以是已带 outputs/ 前缀的
    相对/绝对路径；统一规整到 project_root()/outputs/<主题>/，并做安全校验。
    注意：validate_output_path 要求「主题+文件」两段路径，不适用于项目目录本身，
    因此这里直接拼接 outputs 根并做 relative_to 安全校验。
    """
    outputs_dir = (project_root() / "outputs").resolve()
    p = Path(topic).expanduser()
    if not p.is_absolute():
        rel = "/".join(p.parts)
        if rel.startswith("outputs/"):
            rel = rel[len("outputs/"):]
        elif rel.startswith("./"):
            rel = rel[2:]
        p = Path(rel)
        if not p.is_absolute():
            p = outputs_dir / p
    resolved = p.resolve()
    try:
        rel_path = resolved.relative_to(outputs_dir)
    except ValueError:
        _eprint(f"输出路径不合法：{topic} 不在 {outputs_dir} 内")
        raise SystemExit(2)
    if not rel_path.parts:
        _eprint("不能把 outputs/ 根目录作为电影项目目录")
        raise SystemExit(2)
    # openfilm 会在该目录下建 film.html；这里把主题目录本身当项目目录
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def cmd_open(args: argparse.Namespace) -> int:
    topic = args.topic or "film"
    proj = _project_dir(topic)
    print(f"OpenFilm 项目：{proj}")
    # 共享一台 Studio（默认 home）：OpenFilm 的 editor 是全局单视图（一个 open 广播会翻所有页），
    # 无法在共享 Studio 里按会话隔离；隔离改由面板按项目指向 film 预览页（每项目独立地址，不被翻转）。
    # 这里只把项目打开到共享 Studio 供 Agent 制作/编辑，并按会话记录 film 预览地址供面板隔离展示。
    rc, out, _ = openfilm("open", str(proj), cwd=proj.parent, timeout=1800,
                       no_browser=args.no_browser)
    print(out)
    _emit_studio_url(out, rc, topic)
    # open 的退出码：0 done / 1 失败 / 2 非项目或命令错误
    return 0 if rc == 0 else rc


def _emit_studio_url(out: str, rc: int, topic: str) -> None:
    if rc != 0:
        _eprint(f"openfilm open 返回 {rc}（见上）。")
        return
    m = re.search(r"https?://\S+", out)
    editor_url = m.group(0).rstrip() if m else ""
    if editor_url:
        print(f"\n[Studio 可视化] {editor_url}")
        try:
            _write_studio_link(editor_url)
            _write_studio_topic(topic)
        except Exception:  # noqa: BLE001
            pass
    # 按会话记录 editor（编辑视角）+ film 预览地址。
    # 共享一台 Studio 时 editor 会被别的会话的 open 广播翻转；现改用 pin 模式（?pin=1，见 OpenFilm
    # editor 的 App/ProjectView）让 editor 忽略广播、锁定本项目，实现「1 台共享 Studio + 按会话隔离」，
    # 面板展示编辑视角（而非 film 播放预览）。算不出 pin 地址（Studio 未就绪/无 key）时退回 film 预览。
    try:
        sk = _session_sk()
        if sk:
            _write_session_editor_link(editor_url)
            pinned = _pin_editor(editor_url)
            film_url = _film_preview_url(_project_id_from_editor_url(editor_url))
            # 面板展示：优先 pin 后的 editor（编辑视角、按会话隔离）；取不到则退回 film 预览。
            _write_session_studio_link(pinned or film_url or editor_url)
            # film 预览地址另存一份，供 editor 不可用时面板退回（播放视图兜底）。
            _write_session_film_link(film_url)
    except Exception:  # noqa: BLE001
        pass
    # 按会话记录主题绑定：供 Easel 前端按会话取自己的视频项目（不串到别会话）
    try:
        _record_session_topic(topic)
    except Exception:  # noqa: BLE001
        pass


def _write_studio_link(url: str) -> None:
    root = project_root()
    link_dir = root / "outputs" / "_run"
    link_dir.mkdir(parents=True, exist_ok=True)
    (link_dir / "openfilm-studio.txt").write_text(url + "\n", encoding="utf-8")


def _write_studio_topic(topic: str) -> None:
    """记录「一机一台」的共享 Studio 此刻打开的主题（全局，非按会话）。Easel 据此判断共享 Studio
    是否正显示某会话绑定的项目，实现按会话隔离（不同会话不再加载同一个 OpenFilm 项目）。"""
    link_dir = project_root() / "outputs" / "_run"
    link_dir.mkdir(parents=True, exist_ok=True)
    (link_dir / "openfilm-studio-topic.txt").write_text(topic + "\n", encoding="utf-8")


def _session_sk() -> str:
    """当前会话 key（Easel 注入 EASEL_SESSION_KEY，值即 _eff_sk 结果）；无则空串。"""
    return os.environ.get("EASEL_SESSION_KEY", "").strip()


def _safe_session_file(sk: str) -> str:
    """会话 key 做文件名安全化，与 Easel web/app.py 的 _turn_file 同规约（保证两边路径一致）。"""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", sk)[:120]


def _session_topic_file(sk: str) -> Path:
    return project_root() / "outputs" / "_sessions" / f"{_safe_session_file(sk)}.openfilm-topic.txt"


def _record_session_topic(topic: str) -> None:
    """Record the session binding and fill the project's missing account ownership."""
    from project_scope import record_profile
    sk = _session_sk()
    if not sk:
        return
    record_profile(_project_dir(topic), os.environ.get("EASEL_PERSONA", "").strip())
    f = _session_topic_file(sk)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(topic + "\n", encoding="utf-8")


def _session_studio_file(sk: str) -> Path:
    """某会话的 OpenFilm 视频预览地址落盘路径（open 时按 EASEL_SESSION_KEY 写入）。
    内容是每项目独立的 film 预览地址（filmOrigin/p/<token>/film.html），Easel 面板按会话取自己的
    地址——不同会话各看各的项目，互不顶掉（隔离来自每项目独立地址，而非各起一台 Studio）。与
    Easel web/app.py 的 _session_openfilm_studio_file 同规约（路径必须一致）。"""
    return project_root() / "outputs" / "_sessions" / f"{_safe_session_file(sk)}.openfilm-studio.txt"


def _session_editor_file(sk: str) -> Path:
    """某会话的 OpenFilm editor 地址落盘路径（open 时按 EASEL_SESSION_KEY 写入）。供面板「在 Studio
    中编辑/独立窗口」按钮打开共享 Studio（到本会话项目）；与 Easel web/app.py 同规约。"""
    return project_root() / "outputs" / "_sessions" / f"{_safe_session_file(sk)}.openfilm-editor.txt"


def _write_session_studio_link(url: str) -> None:
    """按会话记录 OpenFilm 视频预览地址（open 时写）。供 Easel 前端按会话取自己的视频预览——
    不同会话各看各的，互不顶掉；仅当 EASEL_SESSION_KEY 注入时记录（非 Easel 场景跳过）。"""
    sk = _session_sk()
    if not sk:
        return
    f = _session_studio_file(sk)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(url + "\n", encoding="utf-8")


def _write_session_editor_link(url: str) -> None:
    """按会话记录 OpenFilm editor 地址（open 时写）。供面板「在 Studio 中编辑/独立窗口」按钮；
    仅当 EASEL_SESSION_KEY 注入时记录（非 Easel 场景跳过）。"""
    sk = _session_sk()
    if not sk:
        return
    f = _session_editor_file(sk)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(url + "\n", encoding="utf-8")


def _session_film_file(sk: str) -> Path:
    """某会话的 OpenFilm film 预览地址落盘路径（open 时按 EASEL_SESSION_KEY 写入）。供面板在 editor
    （编辑视角）不可用时退回 film 预览（播放视图）。与 _session_studio_file 同规约。"""
    return project_root() / "outputs" / "_sessions" / f"{_safe_session_file(sk)}.openfilm-film.txt"


def _write_session_film_link(url: str) -> None:
    """按会话记录 OpenFilm film 预览地址（open 时写）。供面板在 editor 不可用时退回（播放视图兜底）；
    仅当 EASEL_SESSION_KEY 注入时记录。"""
    sk = _session_sk()
    if not sk:
        return
    f = _session_film_file(sk)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text((url or "") + "\n", encoding="utf-8")


# ── 共享一台 Studio + 按项目隔离：面板指向每项目独立的 film 预览地址（不被 editor 广播翻转）──
# OpenFilm 的 editor（/open?to=/projects/<id>）是全局单视图：一个 `openfilm open` 的 /api/open 广播
# 会让所有连着的 editor 页都翻到该项目，无法在共享 Studio 里按会话隔离。而 film 预览页
# （filmOrigin/p/<token>/…，token=HMAC(key,'film origin\0'+项目id)）按项目独立、不被翻转，
# 故共享一台 Studio 时面板改指向 film 预览，即可「1 台 + 按会话隔离」。token 跨重启稳定（key/id 稳定）。
def _read_studio_run() -> dict | None:
    """读共享 Studio 的 run.json（默认 home ~/.openfilm）：取 key + filmOrigin 以算 film 预览地址。
    非 Easel 共享 home 时用 OPENFILM_HOME；读不到返回 None。"""
    import json as _json
    home = os.environ.get("OPENFILM_HOME", "").strip()
    cand = Path(home).expanduser() / "run.json" if home else Path.home() / ".openfilm" / "run.json"
    try:
        if cand.is_file():
            return _json.loads(cand.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def _project_id_from_editor_url(url: str) -> str:
    """从 openfilm open 打印的 editor 地址（…/open?…&to=%2Fprojects%2F<id>）取项目 id；取不到返回空。"""
    if not url:
        return ""
    try:
        from urllib.parse import parse_qs, unquote, urlparse
        to = parse_qs(urlparse(url).query).get("to", [""])[0]
        m = re.search(r"/projects/([\w-]+)", unquote(to))
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def _film_preview_url(project_id: str) -> str:
    """按项目 id 算 film 预览地址：filmOrigin/p/<token>/film.html?film-preview=1。token 与 OpenFilm pages.mjs 同算法
    （HMAC-SHA256(key,'film origin\0'+id) 取前 18 字节 base64url 去 padding）。无 run.json/key 或
    filmOrigin 时返回空（调用方退回 editor 地址）。
    尾参 ?film-preview=1 让 OpenFilm 服务端（pages.mjs/host.mjs）注入 timeline 与其定位 CSS（filmStart），但
    不注入 filmHost 宿主标记/桥接：film.html 的 timeline 自动播放循环运行（非托管、无人驱动），镜头被定位/
    逐帧显示——面板/agent 直接看到非黑、非网格的画面。注意 timeline 会另以 ?film-host=0 重新拉取「原始」film.html
    解析（该分支保持不注入、可被 readFilmFile 解析），故面板地址用 ?film-preview=1 而非 ?film-host=0。"""
    run = _read_studio_run()
    if not run or not project_id:
        return ""
    key = str(run.get("key") or "").strip()
    film_origin = str(run.get("filmOrigin") or "").strip()
    if not key or not film_origin:
        return ""
    import base64
    import hmac
    token = base64.urlsafe_b64encode(
        hmac.new(key.encode("utf-8"), f"film origin\0{project_id}".encode("utf-8"), "sha256").digest()[:18]
    ).decode().rstrip("=")
    return f"{film_origin}/p/{token}/film.html?film-preview=1"


def _pin_editor(url: str) -> str:
    """给 editor 地址补 pin（?pin=1 / &pin=1）：让 OpenFilm editor（App.tsx/ProjectView.tsx 的 pin 分支）
    忽略 open 广播、锁定本项目，实现「1 台共享 Studio + 按会话隔离」——面板展示编辑视角而不串台。
    已带 pin 则原样返回；url 为空返回空。"""
    if not url:
        return ""
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        parts = urlsplit(url)
        if "pin" in {k for k, _ in parse_qsl(parts.query, keep_blank_values=True)}:
            return url
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        q["pin"] = "1"
        return urlunsplit(parts._replace(query=urlencode(q)))
    except Exception:  # noqa: BLE001
        return url


def _editor_url_fresh(project_id: str, pinned: bool = True) -> str:
    """按项目 id 算 editor（编辑视角）地址：origin/open?to=/projects/<id>&key=…（+pin）。
    origin 与 key 取自共享 Studio 的 run.json，与 openfilm open 打印的 editor 地址同构。
    取不到 origin/key 时返回空（调用方退回 film 预览）。pinned=True 补 ?pin=1 按会话隔离。"""
    run = _read_studio_run()
    if not run or not project_id:
        return ""
    origin = str(run.get("origin") or "").strip()
    key = str(run.get("key") or "").strip()
    if not origin or not key:
        return ""
    base = f"{origin}/open?to=%2Fprojects%2F{project_id}&key={key}"
    return _pin_editor(base) if pinned else base


def _find_project_dir() -> Path | None:
    from openfilm_project import find_project
    return find_project(project_root())


def _resolve_project(topic: str | None) -> Path | None:
    from openfilm_project import resolve_project
    return resolve_project(project_root(), topic)


def cmd_frame(args: argparse.Namespace) -> int:
    return _pass(args)


def _pass(args: argparse.Namespace) -> int:
    from openfilm_project import run_command
    return run_command(project_root(), args, openfilm)


def main() -> int:
    ap = argparse.ArgumentParser(description="Easel 操作 OpenFilm（视频制作 + Studio 可视化）")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("check", help="能力探测（--json 机读）")
    p1.add_argument("--json", action="store_true", help="输出机读 JSON")
    p1.set_defaults(func=cmd_check)

    p2 = sub.add_parser("install", help="安装 openfilm 并下载渲染浏览器")
    p2.add_argument("--skip-browser", action="store_true", help="只装 CLI，不下载浏览器")
    p2.set_defaults(func=cmd_install)

    p3 = sub.add_parser("open", help="在 outputs/<主题>/ 新建/打开电影并启动 Studio（可视化）")
    p3.add_argument("topic", nargs="?", help="主题名（outputs 下；默认 film）")
    p3.add_argument("--no-browser", action="store_true", help="不自动打开内置浏览器")
    p3.set_defaults(func=cmd_open)

    pf = sub.add_parser("frame", help="取视频帧供 agent 自检：跑 look 出帧并打印可 view_image 的绝对路径")
    pf.add_argument("args", nargs=argparse.REMAINDER, help="openfilm look 参数（如 2 / 0-6 / --full / --count N / <媒体文件>）")
    pf.add_argument("-p", "--project", default=None, help="项目/主题名或媒体文件；省略=当前会话绑定项目；无会话时取最近项目")
    pf.set_defaults(func=cmd_frame)

    for name in ("render", "look", "get", "skills", "skill", "canvas", "doctor", "update"):
        p = sub.add_parser(name, help=f"透传 openfilm {name}")
        p.add_argument("args", nargs=argparse.REMAINDER, help="openfilm 参数")
        if name in ("render", "look", "canvas", "get"):
            p.add_argument("-p", "--project", default=None, help="项目/主题名；省略使用当前会话绑定")
        p.set_defaults(func=_pass)

    args, forwarded = ap.parse_known_args()
    if forwarded:
        if not hasattr(args, "args"):
            ap.error("unrecognized arguments: " + " ".join(forwarded))
        args.args = forwarded + (args.args or [])
    if not args.cmd:
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
