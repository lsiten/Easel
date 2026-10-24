"""Fake Pi ``--mode rpc`` endpoint speaking the JSONL (not JSON-RPC) protocol.

It mirrors the real Pi RPC mode well enough to exercise :class:`PiAdapter`:
each line on stdin is one JSON command; every line on stdout is one JSON
response or event. It honours ``FAKE_HARNESS_MODE`` / ``FAKE_HARNESS_AUDIT``
the same way :mod:`harness_rpc` does so the Pi tests can share the same
request helpers and audit reader.
"""
import json
import os
import sys

mode = os.environ.get("FAKE_HARNESS_MODE", "normal")
audit = os.environ.get("FAKE_HARNESS_AUDIT")
command = os.environ.get("FAKE_HARNESS_COMMAND", "read public file")
session_id = "native-session"
pending_settled = False
settled = False


def write(message):
    print(json.dumps(message, ensure_ascii=False), flush=True)


def audit_write(message):
    if audit:
        with open(audit, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(message, ensure_ascii=False) + "\n")


def receive():
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def response(request_id, command_name, success=True, data=None, error=None):
    message = {"type": "response", "command": command_name,
               "success": success, "id": request_id}
    if data is not None:
        message["data"] = data
    if error is not None:
        message["error"] = error
    write(message)


def message_update(kind, delta=None, **extra):
    event = {"type": kind}
    if delta is not None:
        event["delta"] = delta
    event.update(extra)
    write({"type": "message_update", "assistantMessageEvent": event})


def settle():
    global settled
    if not settled:
        settled = True
        write({"type": "agent_settled"})


def emit_turn_events(with_tool=False):
    """Emit the thinking/text deltas the tests expect (no settle)."""
    message_update("thinking_delta", delta="思考")
    message_update("text_delta", delta="你好")
    if with_tool:
        write({"type": "tool_execution_start", "toolName": "read_file"})
        write({"type": "tool_execution_end", "toolName": "read_file", "isError": False})


def models():
    return {"models": [
        {"id": "qwen3.8-27b-office", "name": "Qwen 3.8 Office",
         "provider": "hh", "isDefault": True},
        {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4",
         "provider": "anthropic"},
    ]}


while True:
    message = receive()
    if message is None:
        break
    audit_write(message)
    kind = message.get("type")
    request_id = message.get("id", "")

    if kind == "get_state":
        if mode == "wrong-resume":
            response(request_id, "get_state", data={"sessionId": "replacement"})
        else:
            response(request_id, "get_state", data={"sessionId": session_id})
        if pending_settled:
            settle()
        continue

    if kind == "get_available_models":
        if mode == "models-error":
            response(request_id, "get_available_models", success=False,
                     error="invalid secret-test-key")
        else:
            response(request_id, "get_available_models", data=models())
        continue

    if kind == "prompt":
        if mode == "exit":
            sys.exit(0)
        if mode == "error":
            response(request_id, "prompt", success=False,
                     error="invalid model secret-test-key")
            continue
        response(request_id, "prompt", data={"disposition": "started"})
        if mode == "permission":
            write({"type": "extension_ui_request", "id": "dialog-1",
                   "method": "confirm", "title": "Confirm",
                   "message": "run " + command})
            # Do not settle here: the turn must wait for the dialog task's
            # extension_ui_response (handled below) so that response is sent
            # before the turn finishes. Binding already happened via the
            # get_state response read in between.
            continue
        if mode == "slow-cancel":
            emit_turn_events()
            pending_settled = True
            import time
            time.sleep(0.3)
            continue
        emit_turn_events(with_tool=(mode == "tool"))
        pending_settled = True
        # Do not settle yet: get_state (read next) must bind the session first.
        continue

    if kind == "extension_ui_response":
        confirmed = message.get("confirmed")
        if confirmed is False or message.get("cancelled") is True:
            message_update("error", error="permission denied")
            response(request_id, "prompt", data={"disposition": "handled"})
        # In permission mode the turn settles regardless of the answer so the
        # noninteractive case (cancelled) still terminates cleanly.
        settle()
        continue

    if kind == "abort":
        response(request_id, "abort")
        settle()
        continue
