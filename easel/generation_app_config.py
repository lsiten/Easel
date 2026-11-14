"""Private atomic structured configuration with compare-and-swap revisions.

Provider credentials have one editable home: the six model channels in ``.env``.
Older releases also wrote provider credentials to ``app-config.json``. Those
records are imported once and removed so a stale copy cannot shadow the channel
configuration shared by the Web app, API workers, and Agent subprocesses.
"""
from collections.abc import Mapping
from skills.shared.scripts.asset_storage import _storage_lock
import hashlib
import json
import os
from pathlib import Path
import tempfile
from easel.env_store import update_environment
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
    """Return the runtime environment, with channel `.env` values authoritative.

    Legacy app-config providers only fill keys that are absent from `.env`.
    Process environment variables remain the final deployment override.
    """
    result = dict(settings)
    config = effective_config(outputs)
    for name, provider in config.providers.items():
        base, key = PROVIDER_ENV[name]
        if provider.baseUrl and not result.get(base):
            result[base] = provider.baseUrl
        legacy_key = provider.apiKey.get_secret_value()
        if legacy_key and not result.get(key) and not any(
                result.get(alias) for alias in SECRET_ALIASES.get(name, ())):
            result[key] = legacy_key
        elif not legacy_key and not result.get(key):
            for alias in SECRET_ALIASES.get(name, ()):
                if not result.get(alias):
                    result[alias] = ''
    return {**result, **os.environ}


def migrate_provider_config(outputs: Path, env_file: Path) -> bool:
    """Move legacy structured providers into `.env` and clear the duplicate.

    The old runtime let app-config override `.env`; migration preserves that
    effective value once. Process environment variables still take precedence
    when the application resolves settings.
    """
    config = effective_config(outputs)
    if not config.providers:
        return False
    updates: dict[str, str] = {}
    for name, provider in config.providers.items():
        base, key = PROVIDER_ENV[name]
        if provider.baseUrl:
            updates[base] = provider.baseUrl
        legacy_key = provider.apiKey.get_secret_value()
        if legacy_key:
            updates[key] = legacy_key
            for alias in SECRET_ALIASES.get(name, ()):
                updates[alias] = ''
    if updates:
        update_environment(env_file, updates)
    cleared = config.model_copy(update={'providers': {}})
    path = config_path(outputs)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = path.with_name('app-config.pre-channel-migration.json')
    if not backup.exists():
        backup.write_bytes(path.read_bytes() if path.exists() else b'{}')
        backup.chmod(0o600)
    lock = path.with_suffix('.lock')
    encoded = json.dumps(cleared.model_dump(mode='json', by_alias=True), ensure_ascii=False).encode()
    with _storage_lock(lock):
        lock.chmod(0o600)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.config-')
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return True


def public_config(outputs: Path, settings: Mapping[str, str] = {}):
    path = config_path(outputs)
    snapshot = path.read_bytes() if path.exists() else b''
    config = AppConfig.model_validate_json(snapshot) if snapshot else AppConfig()
    data = config.model_dump(mode='json', by_alias=True)
    # Planning defaults belong to the chat channel and are resolved per task;
    # do not expose the retired global planner field as editable configuration.
    data.get('application', {}).pop('plannerModel', None)
    return {'revision': hashlib.sha256(snapshot).hexdigest(), 'config': data, 'configuredSecrets': [],
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
