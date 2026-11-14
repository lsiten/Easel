"""Private atomic structured configuration with compare-and-swap revisions."""
from collections.abc import Mapping
from skills.shared.scripts.asset_storage import _storage_lock
import hashlib
import json
import os
from pathlib import Path
import tempfile
from easel.generation_config_models import AppConfig, ConfigUpdate
from easel.generation_models import GenerationError

PROVIDER_ENV = {
    'openai': ('EASEL_DIRECT_API_BASE_URL', 'EASEL_DIRECT_API_KEY'),
    'image': ('IMG_BASE_URL', 'IMG_API_KEY'), 'video': ('VIDEO_BASE_URL', 'VIDEO_API_KEY'),
    'gemini': ('GEMINI_BASE_URL', 'GEMINI_API_KEY'), 'anthropic': ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY'),
    'grok': ('GROK_BASE_URL', 'GROK_API_KEY'), 'relay': ('RELAY_BASE_URL', 'RELAY_API_KEY'),
    'dashscope': ('DASHSCOPE_BASE_URL', 'DASHSCOPE_API_KEY'), 'volcengine': ('ARK_BASE_URL', 'ARK_API_KEY'),
}

SECRET_ALIASES = {'image': ('OPENAI_API_KEY', 'API_KEY'), 'video': ('OPENAI_API_KEY', 'API_KEY'),
    'volcengine': ('VOLCENGINE_API_KEY', 'VOLC_ARK_API_KEY'), 'dashscope': ('DASHSCOPE_KEY', 'ALIYUN_API_KEY')}

def config_path(outputs: Path) -> Path:
    path = outputs / '_generation' / 'app-config.json'
    if path.parent.is_symlink() or path.is_symlink():
        raise GenerationError('config_path_unsafe', 409)
    return path


def effective_config(outputs: Path) -> AppConfig:
    path = config_path(outputs)
    return AppConfig.model_validate_json(path.read_bytes()) if path.exists() else AppConfig()


def revision(outputs: Path) -> str:
    path = config_path(outputs)
    return hashlib.sha256(path.read_bytes() if path.exists() else b'').hexdigest()


def effective_settings(outputs: Path, settings: Mapping[str, str]) -> dict[str, str]:
    result = dict(settings)
    config = effective_config(outputs)
    for name, provider in config.providers.items():
        base, key = PROVIDER_ENV[name]
        if provider.baseUrl:
            result[base] = provider.baseUrl
        result[key] = provider.apiKey.get_secret_value()
        if not result[key]:
            for alias in SECRET_ALIASES.get(name, ()):
                result[alias] = ''
    if config.application.plannerModel:
        result['EASEL_DRAFT_PLANNER_MODEL'] = config.application.plannerModel
    return {**result, **os.environ}


def public_config(outputs: Path, settings: Mapping[str, str] = {}):
    path = config_path(outputs)
    snapshot = path.read_bytes() if path.exists() else b''
    config = AppConfig.model_validate_json(snapshot) if snapshot else AppConfig()
    data = config.model_dump(mode='json', by_alias=True)
    configured = []
    effective = effective_settings(outputs, settings)
    for name, (base, key) in PROVIDER_ENV.items():
        if name not in data['providers'] and not (effective.get(base) or effective.get(key) or any(effective.get(alias) for alias in SECRET_ALIASES.get(name, ()))):
            continue
        data['providers'][name] = {'baseUrl': effective.get(base, ''), 'apiKey': ''}
        if effective.get(key) or any(effective.get(alias) for alias in SECRET_ALIASES.get(name, ())):
            configured.append('providers.' + name + '.apiKey')
    return {'revision': hashlib.sha256(snapshot).hexdigest(), 'config': data, 'configuredSecrets': configured,
            'locked': sorted({key for pair in (*PROVIDER_ENV.values(), *SECRET_ALIASES.values()) for key in pair if key in os.environ}),
            'applyMode': 'per-request'}


def save_config(outputs: Path, update: ConfigUpdate, settings: Mapping[str, str] = {}) -> None:
    path = config_path(outputs)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = path.with_suffix('.lock')
    if lock.is_symlink():
        raise GenerationError('config_path_unsafe', 409)
    with _storage_lock(lock):
        lock.chmod(0o600)
        if revision(outputs) != update.revision:
            raise GenerationError('config_revision_conflict', 409)
        old = effective_config(outputs)
        data = update.config.model_dump(mode='json', by_alias=True)
        allowed = {'providers.' + name + '.apiKey' for name in update.config.providers}
        if set(update.clearSecrets) - allowed:
            raise GenerationError('config_secret_path_invalid')
        for name, provider in update.config.providers.items():
            prior = old.providers.get(name)
            base_env, key_env = PROVIDER_ENV[name]
            if base_env in os.environ and provider.baseUrl not in (os.environ[base_env], prior.baseUrl if prior else ''):
                raise GenerationError('config_environment_locked', 409)
            secret = provider.apiKey.get_secret_value()
            clearing = 'providers.' + name + '.apiKey' in update.clearSecrets
            if any(key in os.environ for key in (key_env, *SECRET_ALIASES.get(name, ()))) and (secret or clearing):
                raise GenerationError('config_environment_locked', 409)
            data['providers'][name]['apiKey'] = '' if clearing else secret or (prior.apiKey.get_secret_value() if prior else next((settings.get(key, '') for key in (key_env, *SECRET_ALIASES.get(name, ())) if settings.get(key)), ''))
        for name in old.providers.keys() - update.config.providers.keys():
            data['providers'][name] = {'baseUrl': '', 'apiKey': ''}
        encoded = json.dumps(data, ensure_ascii=False).encode()
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.config-')
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
