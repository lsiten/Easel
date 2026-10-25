"""KB service —— 集成项目 Otter Wiki (llm-wiki) 的查询/沉淀能力。

内容库此前只把 ai-社区 作为「外部只读源」做目录浏览；本模块把项目自带的
知识库能力接进来，使「知识库使用」具备：

查询（只读，随画像聚焦）：
  - query  ：BM25 + CJK bigram 证据包（--json），按画像 kb_scopes.json 过滤结果，
             使同一知识库在不同画像下查询结果不同（与内容库浏览的聚焦一致）。
  - ask    ：cite-ready 证据摘要（文本），供快速问答入口。
  - search ：词法检索（--json），按 relative_path 随画像聚焦。
  - status ：索引规模（文档/块/链接数），用于前端显示「知识库已就绪」。

沉淀（create-only 事务，dry-run 默认、显式才写库）：
  - ingest ：把 URL/本地文件捕获为收件箱笔记。
  - save   ：把显式内容（对话结论/草稿）保存为笔记。
  二者均先 dry-run 预览（路径/plan_sha256/预览），--apply 才真正写库；
  同名冲突 fail-closed（绝不覆盖），全程 journaled 可恢复。

实现：subprocess 调用 llm-wiki CLI（--json），不依赖其 venv 的第三方依赖；
可执行文件定位优先级 EASEL_LLM_WIKI → <vault>/tools/knowledge-base/.venv/bin/llm-wiki → PATH。
知识库 vault = 配置的内容源根（EASEL_CONTENT_SOURCES 里含 .knowledge-base 的源，回退首个）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from easel.direct_api import read_settings
from easel.persona import _kb_in_scope, _persona_kb_scope

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

# 知识库 CLI 单次调用超时（索引 1.5k 文档，正常 <2s；给足余量防冷启动）。
_CLI_TIMEOUT = 60


# ── 解析：内容源 / vault / llm-wiki 可执行文件 ─────────────────────────────
_CONTENT_SRC_RE = re.compile(r"^[^\x00-\x1f/\\]+$")


def _content_sources() -> dict[str, Path]:
    """{source_name: 绝对根目录}，与 web/app.py 的解析保持一致（读同一 .env）。"""
    raw = read_settings(ENV_FILE).get("EASEL_CONTENT_SOURCES", "").strip()
    if not raw:
        return {}
    entries: list[tuple[str, str]] = []
    if raw.lstrip().startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("name") and item.get("path"):
                        entries.append((str(item["name"]).strip(), str(item["path"]).strip()))
        except Exception:
            pass
    else:
        for part in re.split(r"[;,\n]", raw):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                name, _, path = part.partition("=")
                entries.append((name.strip(), path.strip()))
            else:
                entries.append((Path(part).name, part))
    sources: dict[str, Path] = {}
    for name, path in entries:
        if not name or not path or not _CONTENT_SRC_RE.match(name):
            continue
        root = Path(path).expanduser()
        if not root.is_absolute():
            root = (PROJECT_ROOT / path).resolve()
        if root.is_dir():
            sources[name] = root
    return sources


def _kb_vault() -> Path | None:
    """知识库 vault = 内容源根；优先含 .knowledge-base / tools/knowledge-base 的源，
    回退首个内容源。未配置返回 None。"""
    sources = _content_sources()
    if not sources:
        return None
    for root in sources.values():
        if (root / ".knowledge-base").is_dir() or (root / "tools" / "knowledge-base").is_dir():
            return root
    return next(iter(sources.values()), None)


def _kb_source() -> str:
    """知识库对应内容源的名字（供前端拼 @<source>/<rel> 打开原文）。"""
    sources = _content_sources()
    vault = _kb_vault()
    if vault is None:
        return ""
    for name, root in sources.items():
        if root == vault:
            return name
    return next(iter(sources), "")


def _llm_wiki_exec() -> Path | None:
    """定位 llm-wiki 可执行文件：EASEL_LLM_WIKI → <vault>/tools/knowledge-base/.venv/bin/llm-wiki → PATH。"""
    env = read_settings(ENV_FILE).get("EASEL_LLM_WIKI", "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p
    vault = _kb_vault()
    if vault is not None:
        candidate = vault / "tools" / "knowledge-base" / ".venv" / "bin" / "llm-wiki"
        if candidate.is_file():
            return candidate
    found = shutil.which("llm-wiki")
    return Path(found) if found else None


def _base() -> dict:
    """KB 可用性基线（exec 是否就绪、vault 路径）。前端据此提示「知识库已就绪/未配置」。"""
    exec_ = _llm_wiki_exec()
    vault = _kb_vault()
    return {
        "available": exec_ is not None and vault is not None,
        "source": _kb_source(),
        "exec": str(exec_) if exec_ else "",
        "vault": str(vault) if vault else "",
        "error": "" if (exec_ and vault) else "未找到 llm-wiki CLI 或知识库 vault（检查 EASEL_CONTENT_SOURCES / tools/knowledge-base）",
    }


def _run(args: list[str]) -> tuple[int, str, str]:
    """运行 llm-wiki CLI；返回 (returncode, stdout, stderr)。exec 缺失 → (2, '', error)。"""
    exec_ = _llm_wiki_exec()
    if exec_ is None:
        return 2, "", "未找到 llm-wiki CLI"
    try:
        proc = subprocess.run(
            [str(exec_), *args], capture_output=True, text=True,
            timeout=_CLI_TIMEOUT,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"llm-wiki 超时（>{_CLI_TIMEOUT}s）"
    except Exception as exc:  # noqa: BLE001 - 统一兜底为错误信息
        return 1, "", f"{type(exc).__name__}: {exc}"


def _filter_docs(bundle: dict, patterns: list[str]) -> dict:
    """按画像聚焦过滤 query 结果：只保留 relative_path 命中的文档；total_hits 同步重算。"""
    if not patterns or "documents" not in bundle:
        return bundle
    docs = [d for d in bundle.get("documents", [])
            if _kb_in_scope(str(d.get("relative_path", "")), patterns)]
    total = sum(len(d.get("hits", [])) for d in docs)
    return {**bundle, "documents": docs, "total_hits": total}


# ── 查询（只读，随画像聚焦）────────────────────────────────────────────────
def status() -> dict:
    """索引规模 + 可用性；前端用于显示「知识库已就绪 · N 篇」。"""
    base = _base()
    if not base["available"]:
        return {**base, "documents": 0}
    rc, out, err = _run(["status", _kb_vault().as_posix(), "--json"])
    if rc != 0:
        return {**base, "documents": 0, "error": err.strip() or out.strip()}
    try:
        data = json.loads(out)
    except Exception:
        return {**base, "documents": 0, "error": "解析 status 失败"}
    return {**base, **data}


def query(question: str, limit: int = 10, persona: str | None = None) -> dict:
    """BM25 证据包（结构化），按画像 kb_scopes.json 聚焦；通用/无画像 = 全量。"""
    base = _base()
    if not base["available"]:
        return {"question": question, "total_hits": 0, "documents": [], **base}
    args = ["query", question, _kb_vault().as_posix(),
           "--limit", str(max(1, min(100, int(limit or 10)))), "--json"]
    rc, out, err = _run(args)
    if rc != 0:
        return {"question": question, "total_hits": 0, "documents": [], **base,
                "error": err.strip() or out.strip()}
    try:
        bundle = json.loads(out)
    except Exception:
        return {"question": question, "total_hits": 0, "documents": [], **base,
                "error": "解析 query 结果失败"}
    bundle = _filter_docs(bundle, _persona_kb_scope(persona))
    return {**bundle, **base, "scoped": bool(_persona_kb_scope(persona))}


def ask(question: str, limit: int = 10, persona: str | None = None) -> dict:
    """cite-ready 证据摘要（文本）。文本不可按相对路径聚焦，故仅作摘要；结构化检索用 query。"""
    base = _base()
    if not base["available"]:
        return {"question": question, "summary": "", **base}
    args = ["ask", question, _kb_vault().as_posix(),
           "--limit", str(max(1, min(100, int(limit or 10))))]
    rc, out, err = _run(args)
    if rc != 0:
        return {"question": question, "summary": "", **base, "error": err.strip() or out.strip()}
    return {"question": question, "summary": out, **base}


def search(query: str, limit: int = 10, persona: str | None = None) -> dict:
    """词法检索（--json），按 relative_path 随画像聚焦。"""
    base = _base()
    if not base["available"]:
        return {"query": query, "hits": [], **base}
    args = ["search", query, _kb_vault().as_posix(),
           "--limit", str(max(1, min(100, int(limit or 10)))), "--json"]
    rc, out, err = _run(args)
    if rc != 0:
        return {"query": query, "hits": [], **base, "error": err.strip() or out.strip()}
    try:
        hits = json.loads(out)
    except Exception:
        return {"query": query, "hits": [], **base, "error": "解析 search 结果失败"}
    if not isinstance(hits, list):
        hits = []
    patterns = _persona_kb_scope(persona)
    if patterns:
        hits = [h for h in hits if _kb_in_scope(str(h.get("relative_path", "")), patterns)]
    return {"query": query, "hits": hits, **base, "scoped": bool(patterns)}


# ── 沉淀（create-only 事务；dry-run 默认、显式才写库）────────────────────────
def _dryrun(text: str) -> dict:
    """解析 dry-run 的人类可读输出 → {target, plan_sha256, preview}。"""
    target = ""
    plan = ""
    preview = ""
    for line in text.splitlines():
        if line.startswith("capture target:") or line.startswith("save target:"):
            target = line.split(":", 1)[1].strip()
        elif line.startswith("plan_sha256="):
            plan = line.split("=", 1)[1].strip()
    # 预览 = 去掉首两行（target/plan）与末行（apply with 提示）后的中段
    lines = text.splitlines()
    # 定位 "note preview:" 行，取其后的内容直到 "apply with:"
    preview_lines: list[str] = []
    capturing = False
    for line in lines:
        if line.strip().startswith("note preview:"):
            capturing = True
            continue
        if line.strip().startswith("apply with:"):
            break
        if capturing:
            preview_lines.append(line)
    return {"dry_run": True, "target": target, "plan_sha256": plan,
            "preview": "\n".join(preview_lines).strip() or text.strip()}


def ingest(source: str, target: str | None = None, title: str | None = None,
          apply: bool = False, persona: str | None = None) -> dict:
    """把 URL/本地文件捕获为收件箱笔记。dry-run 默认；--apply 才写库（冲突 fail-closed）。"""
    base = _base()
    if not base["available"]:
        return {"applied": False, "target": "", "error": base.get("error", "未就绪"), **base}
    if not source or not source.strip():
        return {"applied": False, "error": "source 不能为空", **base}
    args = ["ingest", source.strip(), _kb_vault().as_posix()]
    if target and target.strip():
        args += ["--target", target.strip()]
    if title and title.strip():
        args += ["--title", title.strip()]
    if apply:
        args += ["--apply", "--json"]
    rc, out, err = _run(args)
    if not apply:
        return {**_dryrun(out), "applied": False, **base,
                "error": None if rc == 0 else err.strip()}
    if rc == 75:
        try:
            return {**json.loads(out), "applied": False, "conflict": True, **base}
        except Exception:
            return {"applied": False, "conflict": True, "error": out.strip() or err.strip(), **base}
    if rc != 0:
        return {"applied": False, "error": err.strip() or out.strip(), **base}
    try:
        result = json.loads(out)
    except Exception:
        return {"applied": True, "error": "解析 apply 结果失败", **base}
    return {"applied": True, **result, **base}


def save(title: str, content: str | None = None, target: str | None = None,
        apply: bool = False, persona: str | None = None) -> dict:
    """把显式内容（对话结论/草稿）保存为笔记。dry-run 默认；--apply 才写库。"""
    base = _base()
    if not base["available"]:
        return {"applied": False, "target": "", "error": base.get("error", "未就绪"), **base}
    if not title or not title.strip():
        return {"applied": False, "error": "title 不能为空", **base}
    if not (content or "").strip():
        return {"applied": False, "error": "content 不能为空", **base}
    args = ["save", title.strip(), _kb_vault().as_posix(), "--content", content or ""]
    if target and target.strip():
        args += ["--target", target.strip()]
    if apply:
        args.append("--apply")
    rc, out, err = _run(args)
    if not apply:
        return {**_dryrun(out), "applied": False, **base,
                "error": None if rc == 0 else err.strip()}
    if rc == 75:
        try:
            return {**json.loads(out), "applied": False, "conflict": True, **base}
        except Exception:
            return {"applied": False, "conflict": True, "error": out.strip() or err.strip(), **base}
    if rc != 0:
        return {"applied": False, "error": err.strip() or out.strip(), **base}
    try:
        result = json.loads(out)
    except Exception:
        return {"applied": True, "error": "解析 apply 结果失败", **base}
    return {"applied": True, **result, **base}
