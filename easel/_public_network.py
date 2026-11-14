"""Public-address socket connections used by remote media transports."""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
import socket
from typing import Final
from urllib.parse import urlsplit


class RemoteMediaError(ValueError):
    """A remote URL, address, or response violates the download policy."""


@dataclass(frozen=True, slots=True)
class PublicAddress:
    family: int
    socket_type: int
    protocol: int
    address: tuple[str, int] | tuple[str, int, int, int]


PUBLIC_SCHEMES: Final = frozenset({'http', 'https'})


def resolve_public(host: str, port: int) -> tuple[PublicAddress, ...]:
    """Resolve once and reject the complete response if any address is non-public."""
    try:
        addresses = socket.getaddrinfo(host.rstrip('.'), port, type=socket.SOCK_STREAM)
        for _family, _kind, _protocol, _name, address in addresses:
            parsed = ip_address(address[0])
            if not parsed.is_global or parsed.is_multicast:
                raise RemoteMediaError('媒体 URL 必须解析到公网地址')
    except RemoteMediaError:
        raise
    except (OSError, ValueError) as error:
        raise RemoteMediaError('媒体 URL 主机无法解析') from error
    if not addresses:
        raise RemoteMediaError('媒体 URL 主机无法解析')
    return tuple(PublicAddress(family, kind, protocol, address)
                 for family, kind, protocol, _name, address in addresses)


def validate_public_url(value: str) -> str:
    url = value.strip()
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in PUBLIC_SCHEMES or not parsed.hostname:
            raise RemoteMediaError('媒体 URL 只允许 http/https')
        if parsed.username or parsed.password or any(ord(char) < 32 for char in url):
            raise RemoteMediaError('媒体 URL 不允许携带凭证或控制字符')
        port = parsed.port or (443 if parsed.scheme.lower() == 'https' else 80)
    except ValueError as error:
        raise RemoteMediaError('媒体 URL 格式无效') from error
    resolve_public(parsed.hostname, port)
    return url


def connect_public(host: str, port: int, timeout: float) -> socket.socket:
    """Connect a validated numeric sockaddr without a second DNS lookup."""
    addresses = resolve_public(host, port)
    last_error: OSError | None = None
    for address in addresses:
        stream = socket.socket(address.family, address.socket_type, address.protocol)
        stream.settimeout(timeout)
        try:
            stream.connect(address.address)
        except OSError as error:
            stream.close()
            last_error = error
            continue
        return stream
    raise RemoteMediaError('无法连接媒体主机') from last_error
