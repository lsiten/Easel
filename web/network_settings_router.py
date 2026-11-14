from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from easel.network_proxy import (
    ProxyConfig,
    ProxyMode,
    apply_process_proxy,
    effective_proxy_config,
    load_proxy_config,
    mask_proxy_value,
    save_proxy_config,
)

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
_VALUE_ALLOWED = re.compile(r"[A-Za-z0-9_.:/@+\-=~,%?#\[\]]*")

router = APIRouter()


class ProxyValueView(BaseModel):
    model_config = ConfigDict(frozen=True)

    configured: bool
    value: str


class ProxySettingsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: ProxyMode
    httpProxy: ProxyValueView
    httpsProxy: ProxyValueView
    allProxy: ProxyValueView
    noProxy: ProxyValueView


class ProxySettingsUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: ProxyMode
    updates: dict[str, str] = Field(default_factory=dict)


def _field_view(value: str) -> ProxyValueView:
    return ProxyValueView(configured=bool(value), value=mask_proxy_value(value))


def _settings_view(config: ProxyConfig) -> ProxySettingsView:
    return ProxySettingsView(
        mode=config.mode,
        httpProxy=_field_view(config.http_proxy),
        httpsProxy=_field_view(config.https_proxy),
        allProxy=_field_view(config.all_proxy),
        noProxy=_field_view(config.no_proxy),
    )


def _validate_value(name: str, value: str) -> None:
    if len(value) > 2048 or not _VALUE_ALLOWED.fullmatch(value):
        raise HTTPException(400, f"{name} 包含不支持的字符")


def _validate_proxy(name: str, value: str) -> None:
    _validate_value(name, value)
    if not value:
        return
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https", "socks5", "socks5h"} and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400, f"{name} 必须是 http(s):// 或 socks5(h):// 地址")


def _merged_custom_config(current: ProxyConfig,
                          updates: dict[str, str]) -> ProxyConfig:
    allowed = {
        "HTTP_PROXY": "http_proxy",
        "HTTPS_PROXY": "https_proxy",
        "ALL_PROXY": "all_proxy",
        "NO_PROXY": "no_proxy",
    }
    unknown = sorted(set(updates) - set(allowed))
    if unknown:
        raise HTTPException(400, f"不允许设置：{', '.join(unknown)}")
    values = {
        "http_proxy": current.http_proxy,
        "https_proxy": current.https_proxy,
        "all_proxy": current.all_proxy,
        "no_proxy": current.no_proxy,
    }
    for key, value in updates.items():
        field = allowed[key]
        values[field] = value.strip()
    for key, field in allowed.items():
        if field == "no_proxy":
            _validate_value(key, values[field])
        else:
            _validate_proxy(key, values[field])
    if not any(values[key] for key in ("http_proxy", "https_proxy", "all_proxy")):
        raise HTTPException(400, "自定义代理模式至少需要填写一个代理地址")
    return ProxyConfig(mode="custom", **values)


@router.get("/api/settings/proxy", response_model=ProxySettingsView)
async def api_proxy_settings() -> ProxySettingsView:
    """Return the effective proxy configuration without exposing credentials."""
    return _settings_view(effective_proxy_config(ENV_FILE))


@router.post("/api/settings/proxy", response_model=ProxySettingsView)
async def api_proxy_save(req: ProxySettingsUpdate) -> ProxySettingsView:
    """Persist and immediately apply proxy settings for this Easel process."""
    current = (effective_proxy_config(ENV_FILE)
               if req.mode == "custom" else load_proxy_config(ENV_FILE))
    config = (_merged_custom_config(current, req.updates)
              if req.mode == "custom"
              else ProxyConfig(mode=req.mode))
    save_proxy_config(ENV_FILE, config)
    apply_process_proxy(config)
    return _settings_view(effective_proxy_config(ENV_FILE))
