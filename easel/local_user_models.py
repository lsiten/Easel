"""Local persona user profile and model preference contracts."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue

AIKind = Literal['image', 'edit', 'video', 'agent']


class UserModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class AIPreference(UserModel):
    default_model: str = Field(min_length=1, max_length=200)
    options: dict[str, JsonValue] = Field(default_factory=dict)


class AIPreferences(UserModel):
    image: AIPreference | None = None
    edit: AIPreference | None = None
    video: AIPreference | None = None
    agent: AIPreference | None = None


class AIConfiguration(UserModel):
    image: AIPreference
    edit: AIPreference
    video: AIPreference
    agent: AIPreference


class UserLocation(UserModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    country: str = Field(default='', max_length=200)
    country_code: str = Field(default='', max_length=30)
    city: str = Field(default='', max_length=200)
    city_code: str = Field(default='', max_length=30)
    district: str = Field(default='', max_length=200)
    district_code: str = Field(default='', max_length=30)


class UserPatch(UserModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    avatar: str | None = Field(default=None, max_length=2048)
    locale: Literal['zh-CN', 'en-US'] | None = None
    location: UserLocation | None = None
    user_type: Literal['creator', 'business_owner'] | None = None


class LocalUser(UserModel):
    persona: str
    name: str = ''
    avatar: str = ''
    locale: Literal['zh-CN', 'en-US'] = 'zh-CN'
    location: UserLocation | None = None
    user_type: Literal['creator', 'business_owner'] = 'creator'
    ai: AIPreferences = Field(default_factory=AIPreferences)
