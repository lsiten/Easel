"""VOD HMAC request signing and bounded native material upload."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
import zlib
import anyio
import httpx
from pydantic import JsonValue, TypeAdapter
from easel.generation_models import GenerationError


def mapping(value: JsonValue) -> dict[str, JsonValue]:
    match value:
        case dict():
            return value
        case _:
            raise GenerationError('aideo_provider_response_invalid', 502)


def sequence(value: JsonValue) -> list[JsonValue]:
    match value:
        case list():
            return value
        case _:
            raise GenerationError('aideo_provider_response_invalid', 502)


def string(value: JsonValue) -> str:
    match value:
        case str():
            return value
        case _:
            raise GenerationError('aideo_provider_response_invalid', 502)


@dataclass(frozen=True, slots=True)
class VodConfig:
    access_key: str
    secret_key: str
    space: str
    endpoint: str = 'https://vod.volcengineapi.com'
    region: str = 'cn-north-1'
    playback_base: str = ''
    playback_key: str = ''

    @classmethod
    def from_settings(cls, settings: Mapping[str, str]) -> 'VodConfig':
        config = cls(settings.get('VOLCENGINE_ACCESS_KEY_ID', ''), settings.get('VOLCENGINE_SECRET_ACCESS_KEY', ''),
                     settings.get('VOLCENGINE_SPACE_NAME', ''), settings.get('VOLCENGINE_VOD_BASE_URL', 'https://vod.volcengineapi.com'),
                     settings.get('VOLCENGINE_REGION', 'cn-north-1'), settings.get('VOLCENGINE_PLAYBACK_BASE_URL', ''),
                     settings.get('VOLCENGINE_URL_AUTH_PRIMARY_KEY', ''))
        if not config.access_key or not config.secret_key or not config.space:
            raise GenerationError('aideo_vod_configuration_required', 409)
        parsed = urlsplit(config.endpoint)
        if parsed.scheme not in ('https', 'http') or not parsed.netloc or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
            raise GenerationError('aideo_vod_configuration_invalid', 409)
        return config


@dataclass(frozen=True, slots=True)
class VodCall:
    action: str
    parameters: dict[str, JsonValue]
    method: str = 'GET'
    version: str = '2020-08-01'


def signed_request(config: VodConfig, call: VodCall, stamp: str) -> httpx.Request:
    query = {'Action': call.action, 'Version': call.version}
    body = b''
    if call.method == 'POST':
        body = json.dumps(call.parameters, ensure_ascii=False, separators=(',', ':')).encode()
    else:
        query.update({key: string(value) for key, value in call.parameters.items()})
    encoded = urlencode(sorted(query.items()), quote_via=quote)
    headers = {'host': urlsplit(config.endpoint).netloc, 'x-date': stamp}
    digest = hashlib.sha256(body).hexdigest()
    if body:
        headers['x-content-sha256'] = digest
    names = ';'.join(sorted(headers))
    canonical = '\n'.join((call.method, '/', encoded,
                           ''.join(f'{key}:{headers[key]}\n' for key in sorted(headers)), names, digest))
    scope = f'{stamp[:8]}/{config.region}/vod/request'
    signing = '\n'.join(('HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    key = config.secret_key.encode()
    for part in (stamp[:8], config.region, 'vod', 'request'):
        key = hmac.digest(key, part.encode(), 'sha256')
    signature = hmac.new(key, signing.encode(), 'sha256').hexdigest()
    headers['authorization'] = f'HMAC-SHA256 Credential={config.access_key}/{scope}, SignedHeaders={names}, Signature={signature}'
    headers['content-type'] = 'application/json'
    return httpx.Request(call.method, config.endpoint.rstrip('/') + '/?' + encoded, headers=headers, content=body)


class VodProvider:
    def __init__(self, config: VodConfig) -> None:
        self.config = config

    async def call(self, call: VodCall) -> dict[str, JsonValue]:
        request = signed_request(self.config, call, datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=120) as client:
            response = await client.send(request)
        response.raise_for_status()
        value = TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
        if mapping(value.get('ResponseMetadata', {})).get('Error'):
            raise GenerationError('aideo_provider_rejected', 502)
        if call.action == 'QueryDramaRecapTask' and value.get('Result') is None:
            return {'TaskId': call.parameters['TaskId'], 'Status': 'Processing'}
        return mapping(value.get('Result'))

    async def upload(self, path: Path, space: str) -> str:
        result = await self.call(VodCall('ApplyUploadInfo', {'SpaceName': space, 'FileType': 'media',
            'FileName': path.stem, 'FileExtension': path.suffix}, version='2022-01-01'))
        address = mapping(mapping(result.get('Data')).get('UploadAddress'))
        stores = sequence(address.get('StoreInfos'))
        hosts = sequence(address.get('UploadHosts'))
        if not stores or not hosts:
            raise GenerationError('aideo_upload_address_invalid', 502)
        target = mapping(stores[0])
        host = string(hosts[0])
        if '/' in host or '@' in host:
            raise GenerationError('aideo_upload_address_invalid', 502)
        await upload_bytes(path, 'https://' + host + '/' + quote(string(target.get('StoreUri')), safe='/'), string(target.get('Auth')))
        functions = [{'Name': 'GetMeta'}, {'Name': 'AddOptionInfo', 'Input': {'RecordType': 1, 'Category': 'video', 'Format': path.suffix.lstrip('.').upper()}}]
        result = await self.call(VodCall('CommitUploadInfo', {'SpaceName': space, 'SessionKey': string(address.get('SessionKey')),
            'Functions': json.dumps(functions)}, version='2022-01-01'))
        vid = string(mapping(result.get('Data')).get('Vid'))
        if not vid:
            raise GenerationError('aideo_upload_vid_missing', 502)
        return vid


async def upload_bytes(path: Path, url: str, authorization: str) -> None:
    """Upload bounded chunks using the VOD SDK's gateway multipart protocol."""
    chunk_size = 20 * 1024**2
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=120) as client:
        if path.stat().st_size <= chunk_size:
            content = await anyio.to_thread.run_sync(path.read_bytes)
            response = await client.put(url, headers={'Authorization': authorization,
                'Content-CRC32': f'{zlib.crc32(content):08x}'}, content=content)
            response.raise_for_status()
            return
        headers = {'Authorization': authorization, 'X-Storage-Mode': 'gateway'}
        response = await client.put(url + '?uploads', headers=headers)
        response.raise_for_status()
        value = TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
        identifier = string(mapping(value.get('payload')).get('uploadID'))
        if not identifier:
            raise GenerationError('aideo_upload_id_missing', 502)
        checksums: list[str] = []
        async with await anyio.open_file(path, 'rb') as stream:
            while content := await stream.read(chunk_size):
                checksum = f'{zlib.crc32(content):08x}'
                index = len(checksums) + 1
                response = await client.put(url, params={'partNumber': str(index), 'uploadID': identifier},
                    headers={**headers, 'Content-CRC32': checksum}, content=content)
                response.raise_for_status()
                checksums.append(f'{index}:{checksum}')
        response = await client.put(url, params={'uploadID': identifier}, headers=headers, content=','.join(checksums))
        response.raise_for_status()
