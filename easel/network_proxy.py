from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never
from urllib.parse import urlsplit, urlunsplit

from easel.env_store import update_environment

ProxyMode = Literal["system", "custom", "direct"]
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
PROXY_ENV_KEYS = (
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy",
    "NO_PROXY", "no_proxy",
)
DEFAULT_NO_PROXY = (
    "localhost,127.0.0.1,::1,10.*,"
    "*.xiaohongshu.com,*.devops.xiaohongshu.com,*.douyin.com,"
    "*.kuaishou.com,*.zhihu.com,*.bilibili.com,*.weixin.qq.com,*.qq.com"
)
_BOOTSTRAP_PROXY = {key: os.environ[key] for key in PROXY_ENV_KEYS if key in os.environ}


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    mode: ProxyMode
    http_proxy: str = ""
    https_proxy: str = ""
    all_proxy: str = ""
    no_proxy: str = ""


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_proxy_config(path: Path) -> ProxyConfig:
    """Load saved proxy settings without letting process variables overwrite them."""
    values = _read_env_file(path)
    raw_mode = values.get("EASEL_PROXY_MODE", "").strip().lower()
    configured = any(values.get(key, "").strip() for key in (*PROXY_KEYS, "NO_PROXY"))
    mode: ProxyMode = raw_mode if raw_mode in {"system", "custom", "direct"} else (
        "custom" if configured else "system")
    return ProxyConfig(
        mode=mode,
        http_proxy=values.get("HTTP_PROXY", "").strip(),
        https_proxy=values.get("HTTPS_PROXY", "").strip(),
        all_proxy=values.get("ALL_PROXY", "").strip(),
        no_proxy=values.get("NO_PROXY", "").strip(),
    )


def effective_proxy_config(path: Path,
                           environment: Mapping[str, str] | None = None) -> ProxyConfig:
    """Return the proxy values currently effective for this process."""
    saved = load_proxy_config(path)
    if saved.mode != "system":
        return saved
    env = environment or os.environ
    return ProxyConfig(
        mode="system",
        http_proxy=env.get("HTTP_PROXY") or env.get("http_proxy", ""),
        https_proxy=env.get("HTTPS_PROXY") or env.get("https_proxy", ""),
        all_proxy=env.get("ALL_PROXY") or env.get("all_proxy", ""),
        no_proxy=env.get("NO_PROXY") or env.get("no_proxy", "") or DEFAULT_NO_PROXY,
    )


def proxy_environment(base: Mapping[str, str], config: ProxyConfig) -> dict[str, str]:
    """Apply one proxy configuration to a subprocess environment."""
    env = dict(base)
    if config.mode != "system":
        for key in PROXY_ENV_KEYS:
            env.pop(key, None)
    match config.mode:
        case "custom":
            for upper, value in (
                ("HTTP_PROXY", config.http_proxy),
                ("HTTPS_PROXY", config.https_proxy),
                ("ALL_PROXY", config.all_proxy),
            ):
                if value:
                    env[upper] = value
                    env[upper.lower()] = value
            no_proxy = config.no_proxy or DEFAULT_NO_PROXY
            env["NO_PROXY"] = no_proxy
            env["no_proxy"] = no_proxy
        case "system":
            env.setdefault("NO_PROXY", DEFAULT_NO_PROXY)
            env.setdefault("no_proxy", env["NO_PROXY"])
        case "direct":
            pass
        case unreachable:
            assert_never(unreachable)
    return env


def apply_process_proxy(config: ProxyConfig) -> None:
    """Apply the selected proxy to this long-running Easel process."""
    resolved = proxy_environment(_BOOTSTRAP_PROXY, config)
    for key in PROXY_ENV_KEYS:
        value = resolved.get(key, "")
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def save_proxy_config(path: Path, config: ProxyConfig) -> None:
    """Persist proxy settings while removing stale values when disabled."""
    custom = config.mode == "custom"
    updates = {
        "EASEL_PROXY_MODE": config.mode,
        "HTTP_PROXY": config.http_proxy if custom else "",
        "HTTPS_PROXY": config.https_proxy if custom else "",
        "ALL_PROXY": config.all_proxy if custom else "",
        "NO_PROXY": config.no_proxy if custom else "",
    }
    update_environment(path, updates)


def mask_proxy_value(value: str) -> str:
    """Hide proxy credentials while keeping host and port visible."""
    if not value or "@" not in value:
        return value
    parsed = urlsplit(value)
    if parsed.username is None:
        return value
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{parsed.username}:***@{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
