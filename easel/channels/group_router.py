"""Account group CRUD and atomic ranking through the local channel API."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from easel.channels.groups import AccountGroups
from easel.channels.models import ChannelError
from easel.channels.store import CredentialStore


class GroupData(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True, allow_inf_nan=False)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    rank: float | None = None
    ip: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    country_code: str | None = Field(default=None, alias='countryCode', max_length=10)
    proxy_ip: str | None = Field(default=None, alias='proxyIp', max_length=200)
    browser_config: dict[str, JsonValue] | None = Field(default=None, alias='browserConfig')


class GroupWrite(BaseModel):
    persona: str
    data: GroupData


class GroupDelete(BaseModel):
    persona: str
    ids: list[str] = Field(min_length=1, max_length=100)


class GroupMember(BaseModel):
    persona: str
    platform: str
    account_uid: str


class RankItem(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    platform: str
    account_uid: str
    rank: float


class GroupRanks(BaseModel):
    persona: str
    items: list[RankItem] = Field(max_length=1000)


def add_group_routes(router: APIRouter, store_factory: Callable[[], CredentialStore], owner: Callable[[str], str],
                     require_write: Callable[[Request], None]) -> None:
    def execute(persona: str, operation: Callable[[AccountGroups, str], object]):
        try:
            return operation(AccountGroups(store_factory()), owner(persona))
        except ChannelError as error:
            raise HTTPException(error.status, error.code) from error

    @router.get('/account-groups')
    def groups(persona: str):
        return execute(persona, lambda service, name: service.list(name))

    @router.post('/account-groups')
    def create(payload: GroupWrite, request: Request):
        require_write(request)
        return execute(payload.persona, lambda service, name: service.save(name, payload.data.model_dump(mode='json', exclude_none=True, by_alias=True)))

    @router.patch('/account-groups/{group_id}')
    def update(group_id: str, payload: GroupWrite, request: Request):
        require_write(request)
        return execute(payload.persona, lambda service, name: service.update(name, group_id, payload.data.model_dump(mode='json', exclude_none=True, by_alias=True)))

    @router.delete('/account-groups')
    def delete(payload: GroupDelete, request: Request):
        require_write(request)
        execute(payload.persona, lambda service, name: service.delete(name, payload.ids))
        return {'deleted': True}

    @router.patch('/account-groups/{group_id}/accounts')
    def assign(group_id: str, payload: GroupMember, request: Request):
        require_write(request)
        execute(payload.persona, lambda service, name: service.assign(name, group_id, payload.platform, payload.account_uid))
        return {'assigned': True}

    @router.patch('/account-groups/{group_id}/accounts/rank')
    def rank(group_id: str, payload: GroupRanks, request: Request):
        require_write(request)
        execute(payload.persona, lambda service, name: service.rank(name, group_id, [item.model_dump(mode='json') for item in payload.items]))
        return {'ranked': True}
