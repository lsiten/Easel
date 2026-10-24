"""Pi coding agent adapter over its custom JSONL RPC mode.

Pi's RPC mode (``pi --mode rpc``) is a language-independent JSONL protocol
(see Pi docs rpc.md / json.md / rpc-commands.md / rpc-extension-ui.md). It is
not JSON-RPC, so it cannot reuse :mod:`easel.harnesses.rpc`.

Session strategy (verified live against Pi v1.4.0):
  * turn 1 (no native_id): spawn ``pi --mode rpc`` (no --session), send the
    prompt plus a ``get_state`` command, and bind the returned ``sessionId``.
  * later turns: spawn ``pi --mode rpc --session <native_id>`` and send the
    prompt. A ``get_state`` confirms the id is stable. The process is respawned
    per turn and the session is resumed by id, so history is never migrated
    across runtimes (the store binding enforces a single runtime per session).

Pi's undici HTTP layer rejects any proxy URL whose scheme is not http/https
(e.g. a shell ``socks5h://`` proxy), which crashes the process at startup, so
the adapter sanitises the proxy environment for this subprocess only: it drops
non-http/https proxies and adds the dropped proxy host plus loopback to
NO_PROXY. The sanitisation is contained to Pi and does not affect Codex/ACP.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal

from .base import Descriptor, HarnessError, RunRequest
from .commands import base_command
from .workspace import harness_instructions

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
_DIALOG_METHODS = ("select", "confirm", "input", "editor")


def _sanitize_env(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of *env* whose proxy variables are safe for Pi.

    Only this subprocess is affected. A proxy whose scheme is not http/https is
    dropped (Pi cannot parse it); its host, plus loopback, is added to NO_PROXY
    so the LAN model endpoint is reached directly.
    """
    safe = dict(env)
    extra: set[str] = set()
    for key in _PROXY_KEYS:
        value = safe.get(key)
        if not value or value.lower().startswith(("http://", "https://")):
            continue
        try:
            host = value.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
        except Exception:
            host = ""
        safe.pop(key, None)
        if host:
            extra.add(host)
    existing = (safe.get("NO_PROXY") or safe.get("no_proxy") or "").split(",")
    hosts = {part.strip() for part in existing if part.strip()}
    hosts.update(extra)
    for host in ("127.0.0.1", "localhost", "::1"):
        hosts.add(host)
    safe["NO_PROXY"] = ",".join(sorted(hosts))
    return safe


_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL")


def _redact(message: str, env: dict[str, str]) -> str:
    """Scrub secret-looking env values out of an error before it is surfaced."""
    text = str(message)
    for name, value in env.items():
        if not isinstance(value, str) or len(value) < 8 or value not in text:
            continue
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_MARKERS):
            text = text.replace(value, "***redacted***")
    return text


class PiAdapter:
    descriptor = Descriptor(
        "pi", "Pi", "RPC",
        "pi 已安装；在终端运行 pi 完成登录；socks 代理会被忽略（仅 http/https 生效）")

    async def run(self, request: RunRequest, emit, ask, bind):
        if request.sandbox not in ("read-only", "workspace-write"):
            raise HarnessError("Pi 只允许只读或工作区写入权限")

        env = _sanitize_env(request.env)
        command = self._command(request)
        first_turn = not request.native_id

        try:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=str(request.cwd), env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=4 * 1024 * 1024,
                start_new_session=os.name != "nt")
        except OSError as error:
            raise HarnessError("无法启动 Pi CLI，请检查安装与 PATH") from error

        completion = asyncio.get_running_loop().create_future()
        failure: dict[str, Exception | None] = {"value": None}
        bound = {"done": False}
        dialog_tasks: set[asyncio.Task] = set()
        write_lock = asyncio.Lock()

        async def send(message: dict) -> None:
            async with write_lock:
                try:
                    process.stdin.write(
                        (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError) as error:
                    raise HarnessError("Pi 进程已退出，请检查 CLI 登录与模型配置") from error

        def record_failure(detail: object) -> None:
            if failure["value"] is None:
                failure["value"] = HarnessError(_redact(str(detail), request.env)[:1000])

        def finish() -> None:
            if completion.done():
                return
            if failure["value"] is not None:
                completion.set_exception(failure["value"])
            else:
                completion.set_result(None)

        def on_session_id(session_id: str) -> None:
            if not session_id:
                return
            if first_turn and not bound["done"]:
                bound["done"] = True
                bind(session_id)
            elif not first_turn and session_id != request.native_id:
                record_failure("Pi 返回了不同的原生会话 ID，不会替换已有历史")

        def handle(record: dict) -> None:
            rtype = record.get("type")
            if rtype == "response":
                self._handle_response(record, record_failure, finish, on_session_id)
            elif rtype == "message_update":
                self._handle_message_update(record, emit, record_failure)
            elif rtype == "tool_execution_start":
                emit("activity", f"工具：{record.get('toolName', '')}")
            elif rtype == "tool_execution_end":
                if record.get("isError"):
                    emit("activity", f"工具 {record.get('toolName', '')} 执行失败")
            elif rtype == "auto_retry_end":
                if not record.get("success") and record.get("finalError"):
                    record_failure(record.get("finalError"))
            elif rtype == "agent_settled":
                finish()
            elif rtype == "extension_ui_request":
                self._handle_extension_ui(record, emit, send, ask, dialog_tasks, record_failure)

        async def reader() -> None:
            try:
                while line := await process.stdout.readline():
                    text = line.rstrip(b"\r").decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    try:
                        record = json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(record, dict):
                        handle(record)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                if not completion.done():
                    record_failure("Pi 进程在本轮完成前退出，请检查 CLI 登录与模型配置")
                    finish()

        reader_task = asyncio.create_task(reader())
        prompt = request.prompt
        if first_turn:
            prompt = harness_instructions(request) + "\n\n" + prompt
        try:
            await send({"id": "req-prompt", "type": "prompt", "message": prompt})
            await send({"id": "req-state", "type": "get_state"})
            await completion
        except asyncio.CancelledError:
            try:
                await send({"id": "req-abort", "type": "abort"})
            except Exception:
                pass
            raise
        finally:
            for task in list(dialog_tasks):
                task.cancel()
            reader_task.cancel()
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass
            await self._reap(process)

    async def _reap(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            await process.wait()
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def _command(self, request: RunRequest) -> list[str]:
        command = list(request.command) if request.command else base_command("pi") + ["--mode", "rpc"]
        if request.native_id:
            command += ["--session", request.native_id]
        if request.model:
            command += ["--model", request.model]
        return command

    def _handle_response(self, record, record_failure, finish, on_session_id) -> None:
        command = record.get("command")
        if command == "get_state":
            if not record.get("success"):
                record_failure(record.get("error") or "无法读取 Pi 会话状态")
                return
            on_session_id((record.get("data") or {}).get("sessionId"))
            return
        if command == "prompt":
            if not record.get("success"):
                record_failure(record.get("error") or "Pi 拒绝了该消息")
                finish()
                return
            if (record.get("data") or {}).get("disposition") == "handled":
                finish()
            return
        if not record.get("success"):
            record_failure(record.get("error") or f"Pi 命令 {command} 失败")

    def _handle_message_update(self, record, emit, record_failure) -> None:
        event = record.get("assistantMessageEvent") or {}
        kind = event.get("type")
        if kind == "text_delta":
            emit("token", event.get("delta", ""))
        elif kind == "thinking_delta":
            emit("thinking", event.get("delta", ""))
        elif kind == "error":
            record_failure(event.get("error") or event.get("reason") or "模型返回错误")

    def _handle_extension_ui(self, record, emit, send, ask, dialog_tasks, record_failure) -> None:
        method = record.get("method")
        if method in _DIALOG_METHODS:
            async def dialog() -> None:
                try:
                    response = await self._dialog_response(ask, record)
                    await send({"type": "extension_ui_response", "id": record["id"], **response})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # 无法在非交互环境中确认权限：记录失败并取消该操作，避免本轮挂到超时。
                    record_failure("需要交互式授权，无法在当前环境完成，已取消该操作")
                    try:
                        await send({"type": "extension_ui_response",
                                   "id": record["id"], "cancelled": True})
                    except Exception:
                        pass
            task = asyncio.create_task(dialog())
            dialog_tasks.add(task)
            task.add_done_callback(dialog_tasks.discard)
        elif method == "notify":
            message = str(record.get("message") or "").strip()
            if message:
                emit("activity", message)
        # setStatus / setWidget / setTitle / set_editor_text are fire-and-forget.

    async def _dialog_response(self, ask, record: dict) -> dict:
        """Map a Pi extension UI dialog to Easel's ``ask`` and return the reply.

        ``ask`` returns ``{questionId: [answer]}``; the web UI allows a custom
        free-text answer, so ``input``/``editor`` are presented as a question
        without fixed options.
        """
        method = record.get("method")
        title = record.get("title") or ""
        if method == "confirm":
            message = record.get("message") or ""
            answers = await ask([{
                "questionId": "permission", "header": "确认",
                "question": (title or "确认操作") + (f"：{message}" if message else ""),
                "options": [{"label": "允许本次"}, {"label": "拒绝"}],
            }])
            choice = (answers.get("permission") or [""])[0]
            if choice == "允许本次":
                return {"confirmed": True}
            if choice == "拒绝":
                return {"confirmed": False}
            return {"cancelled": True}
        if method == "select":
            labels = [str(option) for option in record.get("options") or []]
            answers = await ask([{
                "questionId": "permission", "header": "选择",
                "question": title or "请选择",
                "options": [{"label": label} for label in labels],
            }])
            choice = (answers.get("permission") or [])
            if len(choice) == 1 and choice[0] in labels:
                return {"value": choice[0]}
            return {"cancelled": True}
        if method == "input":
            placeholder = record.get("placeholder") or ""
            question = (title or "请输入") + (f"（{placeholder}）" if placeholder else "")
            return self._text_response(await ask([{
                "questionId": "permission", "header": "输入", "question": question,
            }]))
        if method == "editor":
            prefill = record.get("prefill") or ""
            question = (title or "请编辑") + (f"\n{prefill}" if prefill else "")
            return self._text_response(await ask([{
                "questionId": "permission", "header": "编辑", "question": question,
            }]))
        return {"cancelled": True}

    @staticmethod
    def _text_response(answers: dict[str, list[str]]) -> dict:
        value = (answers.get("permission") or [""])[0]
        if value:
            return {"value": value}
        return {"cancelled": True}
