"""Bounded provider HTTP calls with explicit endpoint authority."""
from __future__ import annotations

from collections.abc import Mapping, Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit
from typing import BinaryIO

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from easel.channels.models import ChannelError, JsonDocument

_DOCUMENT = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True, slots=True)
class ApiResponse:
    data: JsonDocument
    headers: Mapping[str, str]
    status: int


class ProviderHttp:
    """Own a client per request so a platform token cannot leak across base URLs."""

    def __init__(self, platform: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.platform = platform
        self.transport = transport

    def send(self, method: str, url: str, *, token: str = '',
             params: Mapping[str, str] | None = None, data: Mapping[str, str] | None = None,
             body: JsonDocument | None = None, content: bytes | Iterator[bytes] | None = None,
             files: Mapping[str, tuple[str, bytes | BinaryIO, str]] | None = None,
             headers: Mapping[str, str] | None = None, basic: tuple[str, str] | None = None,
             storage_upload: bool = False) -> ApiResponse:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as error:
            raise ChannelError(self.platform, 'endpoint_not_allowed') from error
        allowed = {
            'youtube': {'www.googleapis.com', 'oauth2.googleapis.com', 'youtubeanalytics.googleapis.com', 'www.youtube.com', 'upload.video.google.com'},
            'google-business': {'www.googleapis.com', 'oauth2.googleapis.com', 'mybusinessaccountmanagement.googleapis.com', 'mybusinessbusinessinformation.googleapis.com'},
            'tiktok': {'open.tiktokapis.com', 'open-upload.tiktokapis.com'},
            'instagram': {'graph.instagram.com', 'api.instagram.com'},
            'facebook': {'graph.facebook.com', 'rupload.facebook.com'},
            'threads': {'graph.threads.net'}, 'twitter': {'api.x.com'},
            'linkedin': {'api.linkedin.com', 'www.linkedin.com', 'media.licdn.com', 'www.linkedin-ei.com'},
            'pinterest': {'api.pinterest.com'},
            'bilibili-open': {'api.bilibili.com', 'member.bilibili.com', 'openupos.bilivideo.com'},
            'kwai-open': {'open.kuaishou.com'},
            'douyin-open': {'open.douyin.com', 'open-sandbox.douyin.com', 'developer.toutiao.com'},
            'wechat-official-open': {'api.weixin.qq.com'}, 'wechat-channels-open': {'api.weixin.qq.com', 'channels.weixin.qq.com'},
        }.get(self.platform, set())
        storage_host = storage_upload and not token and (parsed.hostname or '').endswith('.amazonaws.com')
        if parsed.scheme != 'https' or port not in (None, 443) or (parsed.hostname not in allowed and not storage_host) or parsed.username or parsed.password:
            raise ChannelError(self.platform, 'endpoint_not_allowed')
        request_headers = dict(headers or {})
        if token:
            request_headers['Authorization'] = f'Bearer {token}'
        try:
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(30, connect=10),
                              follow_redirects=False, trust_env=False) as client:
                response = client.request(method, url, headers=request_headers, params=params, data=data,
                                          json=body, content=content, files=files, auth=basic)
        except httpx.HTTPError as error:
            raise ChannelError(self.platform, 'transport_error', status=502) from error
        if response.status_code == 429:
            retry_after = response.headers.get('Retry-After', '')
            raise ChannelError(self.platform, 'rate_limited', retry_after if retry_after.isdecimal() else '', 429)
        if response.status_code == 412: raise ChannelError(self.platform, 'work_revision_changed', status=409)
        if not 200 <= response.status_code < 300:
            raise ChannelError(self.platform, 'provider_http_error', str(response.status_code), 502)
        if not response.content or storage_upload:
            return ApiResponse({}, dict(response.headers), response.status_code)
        try:
            payload = _DOCUMENT.validate_json(response.content)
        except ValidationError as error:
            raise ChannelError(self.platform, 'provider_response_invalid', status=502) from error
        provider_error = payload.get('error')
        if isinstance(provider_error, dict) and provider_error.get('code') not in (None, '', 'ok', 0):
            raise ChannelError(self.platform, 'provider_api_error', status=502)
        if payload.get('error_code') not in (None, 0, '0'):
            raise ChannelError(self.platform, 'provider_api_error', status=502)
        return ApiResponse(payload, dict(response.headers), response.status_code)
