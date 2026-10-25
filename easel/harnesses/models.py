"""Read native model catalogs without starting a conversation."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time

from easel import __version__

from .base import HarnessError, RunRequest
from .commands import base_command
from .rpc import RPC


_MODEL_CACHE: dict = {}
_MODEL_CACHE_TTL = 120.0


def _configured_model(runtime: str) -> str:
    """The model a runtime is *explicitly* configured with in its own config
    file, preferred over the harness's built-in default catalog.

    A placeholder secret in the environment (e.g. ``ANTHROPIC_API_KEY=
    sk-ant-REPLACE_ME``) makes a harness treat its built-in provider catalog
    as configured and pick an unreachable default (``api.anthropic.com``).
    The harness's config file holds the model the user actually set up, so we
    prefer it. Returns "" when nothing usable is configured.
    """
    if runtime != "pi":
        return ""
    from pathlib import Path
    candidates = []
    cfg_dir = os.environ.get("PI_CONFIG_DIR")
    if cfg_dir:
        candidates.append(Path(cfg_dir) / "models.json")
    candidates.append(Path.home() / ".pi" / "agent" / "models.json")
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        providers = data.get("providers")
        if not isinstance(providers, dict):
            break  # file exists but is not a usable catalog
        for provider, spec in providers.items():
            if not isinstance(spec, dict):
                continue
            for model in spec.get("models") or []:
                if isinstance(model, dict) and _valid_id(model.get("id")):
                    model_id = model["id"]
                    return (f"{provider}/{model_id}"
                            if isinstance(provider, str) and provider
                            else model_id)
        break
    return ""


async def resolve_effective_model(runtime: str, explicit: str,
                                  request: "RunRequest") -> str:
    """Model id to hand a native harness so an *empty* selection is never
    silently routed to the harness's built-in default.

    A configured selection (*explicit*) always wins. When it is empty we
    prefer the model the runtime is explicitly configured with in its own
    config file, then fall back to its native catalog, so the turn uses a
    configured/reachable model instead of the harness's built-in default,
    which may point at an unreachable endpoint (e.g. ``api.anthropic.com``
    on a LAN with no key). The result is cached with a short TTL so we do
    not spawn an extra CLI process on every turn; an empty result (nothing
    resolved) preserves the previous behaviour.
    """
    if explicit:
        return explicit
    if runtime not in ("pi", "codex", "opencode"):
        return ""
    now = time.monotonic()
    cached = _MODEL_CACHE.get(runtime)
    if cached is not None and now - cached[0] < _MODEL_CACHE_TTL:
        return cached[1]
    model = _configured_model(runtime)
    if not model:
        try:
            catalog = await available_models(runtime, request)
        except Exception:
            catalog = []
        if catalog:
            default = next((m for m in catalog if m.get("default")), None)
            model = (default or catalog[0]).get("id", "")
    _MODEL_CACHE[runtime] = (now, model)
    return model


def _valid_id(value):
    return isinstance(value, str) and 0 < len(value) <= 240 and not any(
        ord(character) < 32 for character in value)


async def available_models(runtime: str, request: RunRequest) -> list[dict]:
    if runtime == "codex":
        async def reject(method, params):
            raise HarnessError("读取模型列表时不允许执行工具")

        models = []
        cursor = None
        async with RPC(request, base_command("codex") + ["app-server"],
                       lambda method, params: None, reject) as rpc:
            await rpc.call("initialize", {
                "clientInfo": {"name": "easel", "version": __version__},
                "capabilities": {"experimentalApi": True}}, timeout=15)
            await rpc.send({"method": "initialized"})
            for page in range(20):
                params = {"limit": 100, "includeHidden": False}
                if cursor:
                    params["cursor"] = cursor
                response = await rpc.call("model/list", params, timeout=15)
                if not isinstance(response, dict) or not isinstance(response.get("data"), list):
                    raise HarnessError("Codex 返回了无效的模型列表")
                for item in response.get("data") or []:
                    if not isinstance(item, dict):
                        continue
                    identifier = item.get("model") or item.get("id")
                    if not item.get("hidden") and _valid_id(identifier):
                        label = item.get("displayName")
                        models.append({"id": identifier,
                                       "label": label if _valid_id(label) else identifier,
                                       "default": bool(item.get("isDefault"))})
                following = response.get("nextCursor")
                if not following or following == cursor:
                    break
                cursor = following
    elif runtime == "opencode":
        env = {**request.env, "EASEL_ROOT": str(request.cwd.resolve()),
               "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        process = await asyncio.create_subprocess_exec(
            *base_command("opencode"), "models",
            cwd=str(request.cwd), env=env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=os.name != "nt")
        try:
            async def read_output():
                chunks = []
                size = 0
                while chunk := await process.stdout.read(65536):
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise HarnessError("OpenCode 模型列表过大")
                    chunks.append(chunk)
                return b"".join(chunks)

            output = await asyncio.wait_for(read_output(), 15)
            await asyncio.wait_for(process.wait(), 5)
            if process.returncode or len(output) > 1024 * 1024:
                raise HarnessError("OpenCode 模型列表读取失败，请检查原生 CLI 配置")
        finally:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.returncode is None:
                process.kill()
            await process.wait()
        models = [{"id": identifier, "label": identifier, "default": False}
                  for identifier in output.decode("utf-8").splitlines()
                  if _valid_id(identifier) and "/" in identifier and not any(
                      character.isspace() for character in identifier)]
    elif runtime == "pi":
        # Pi 的 undici 会拒绝非 http/https 代理（如 socks），仅对本子进程清洗代理；
        # 模型列表错误同样要脱敏后再对外抛出。
        from .pi import _redact, _sanitize_env
        env = _sanitize_env(request.env)
        process = await asyncio.create_subprocess_exec(
            *base_command("pi"), "--mode", "rpc", "--no-session",
            cwd=str(request.cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=4 * 1024 * 1024,
            start_new_session=os.name != "nt")
        catalog: list[dict] = []
        try:
            process.stdin.write(
                (json.dumps({"id": "req-models", "type": "get_available_models"},
                            ensure_ascii=False) + "\n").encode("utf-8"))
            await process.stdin.drain()

            async def wait_for_response() -> dict:
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        raise HarnessError("Pi 未返回模型列表")
                    text = line.rstrip(b"\r").decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    try:
                        record = json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    if record.get("type") == "response" and record.get(
                            "command") == "get_available_models":
                        return record

            record = await asyncio.wait_for(wait_for_response(), 20)
            if not record.get("success"):
                raise HarnessError(_redact(
                    record.get("error") or "读取 Pi 模型列表失败", env))
            catalog = (record.get("data") or {}).get("models") or []
        finally:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            elif process.returncode is None:
                process.kill()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                pass
        models = []
        for item in catalog:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not _valid_id(model_id):
                continue
            provider = item.get("provider")
            identifier = (f"{provider}/{model_id}"
                         if isinstance(provider, str) and provider else model_id)
            if not _valid_id(identifier):
                continue
            label = item.get("name")
            models.append({"id": identifier,
                           "label": label if _valid_id(label) else identifier,
                           "default": bool(item.get("isDefault"))})
    else:
        raise HarnessError("此底座不支持原生模型列表")
    unique = {}
    for item in models:
        unique.setdefault(item["id"], item)
    return list(unique.values())
