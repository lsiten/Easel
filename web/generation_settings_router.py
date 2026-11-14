"""Existing admin guard and atomic env writer for AI service configuration."""
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import TypeAdapter, ValidationError

from easel.channel_application_router import SecretSafeRoute
from easel.generation_settings import GenerationSettingsUpdate, SETTINGS, decoded_rates, encoded_rates
from web import app as server


def create_router() -> APIRouter:
    router = APIRouter(prefix='/api/generation-settings', route_class=SecretSafeRoute, tags=['settings'])

    @router.get('')
    def status(request: Request):
        server._require_side_effect_access(request)
        values = {**server.read_settings(server.ENV_FILE), **os.environ}
        try:
            return {'fields': [dict(item.model_dump(), configured=bool(values.get(item.name, '')),
                value='' if item.secret else decoded_rates(values.get(item.name, '')) if item.name == 'EASEL_DRAFT_MODEL_RATES' else values.get(item.name, ''),
                locked=item.name in os.environ) for item in SETTINGS]}
        except ValueError as error:
            raise HTTPException(409, 'generation_rates_encoding_invalid') from error

    @router.post('')
    def save(payload: GenerationSettingsUpdate, request: Request):
        server._require_side_effect_access(request)
        allowed = {item.name for item in SETTINGS}
        updates = {key: value.get_secret_value().strip() for key, value in payload.values.items()}
        if set(updates) - allowed:
            raise HTTPException(400, 'generation_setting_unknown')
        if any(key in os.environ for key in updates):
            raise HTTPException(409, 'generation_setting_locked_by_environment')
        for name, value in updates.items():
            if value and name.endswith('_URL'):
                try:
                    parsed = urlsplit(value)
                    parsed.port
                except ValueError as error:
                    raise HTTPException(400, 'generation_setting_url_invalid') from error
                if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise HTTPException(400, 'generation_setting_url_invalid')
            if value and name == 'EASEL_DRAFT_MODEL_RATES':
                try:
                    from easel.generation_draft_models import ModelRate
                    TypeAdapter(dict[str, tuple[ModelRate, ...]]).validate_json(value)
                except ValidationError as error:
                    raise HTTPException(400, 'generation_rates_invalid') from error
        stored = {key: encoded_rates(value) if key == 'EASEL_DRAFT_MODEL_RATES' else value for key, value in updates.items()}
        server._write_env(stored)
        return status(request)

    return router
