"""Context contracts: typed, provenance-carrying items assembled into a packet."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import DataClass
from .models import Message

Trust = Literal["trusted", "untrusted"]


class ContextKind(StrEnum):
    INSTRUCTIONS = "instructions"
    POLICY = "policy"
    APP_CONFIG = "app_config"
    CONVERSATION = "conversation"
    MEMORY = "memory"
    USER_DATA = "user_data"
    RETRIEVED = "retrieved"
    TOOL_RESULT = "tool_result"
    ARTIFACT = "artifact"
    SCRATCH = "scratch"


# Kinds that make up the stable, cache-friendly prefix (assembled first, in this order).
STABLE_KINDS: tuple[ContextKind, ...] = (
    ContextKind.INSTRUCTIONS,
    ContextKind.POLICY,
    ContextKind.APP_CONFIG,
)


class ContextItem(BaseModel):
    kind: ContextKind
    source: str  # provider name@version
    content: str
    ref: str | None = None
    role: Literal["user", "assistant"] | None = None  # conversation items only
    trust: Trust = "untrusted"
    data_classification: DataClass = DataClass.INTERNAL
    # subject_hmac of the owner for user-scoped items; None for shared items.
    principal_scope: str | None = None
    priority: int = 0  # higher survives budget trimming
    fetched_at: datetime | None = None


class ContextProviderRef(BaseModel):
    name: str
    max_tokens: int = 2000
    required: bool = False
    timeout_ms: int = 1000
    params: dict[str, Any] = Field(default_factory=dict)


class DroppedItem(BaseModel):
    source: str
    kind: ContextKind
    reason: Literal["budget", "unauthorized", "provider_error", "provider_timeout", "classification"]


class ContextPacket(BaseModel):
    system: str
    messages: list[Message]
    items: list[ContextItem] = Field(default_factory=list)
    dropped: list[DroppedItem] = Field(default_factory=list)
    token_report: dict[str, int] = Field(default_factory=dict)
    manifest_hash: str = ""
