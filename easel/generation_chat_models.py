"""Chat wire contracts; provider-specific JSON remains lossless."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra='allow')
    role: str
    content: JsonValue


class ChatRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='allow')
    persona: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    messages: tuple[ChatMessage, ...] = Field(min_length=1)


class CompletionRequest(ChatRequest):
    temperature: float | None = Field(default=None, ge=0, le=2)
    maxTokens: int | None = Field(default=None, ge=1)
    maxCompletionTokens: float | None = None
    topP: float | None = None
    modalities: tuple[Literal['text', 'audio', 'image', 'video'], ...] | None = None
    modelKwargs: dict[str, JsonValue] = Field(default_factory=dict)


class ClaudeRequest(ChatRequest):
    max_tokens: int = Field(default=32000, ge=1)


class ChatRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    persona: str
    state: Literal['running', 'succeeded', 'failed', 'unknown'] = 'running'
    channel: str
    request_model: str
    operation: Literal['completion', 'stream', 'claude', 'claude_completion']
    created_at: float
    updated_at: float
    request: dict[str, JsonValue] = Field(default_factory=dict)
    response: dict[str, JsonValue] | None = None
    error: str = ''


class ChatModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    name: str
    description: str = ''
    summary: str | None = None
    logo: str | None = None
    tags: tuple[JsonValue, ...] = ()
    mainTag: str | None = None
    channel: str = 'openai'
    scenes: tuple[str, ...] = ()
    inputModalities: tuple[str, ...] = ()
    outputModalities: tuple[str, ...] = ()
    runtime_model: str = Field(default='', exclude=True)
    configured: bool = True
