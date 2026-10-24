"""Inbound request contract. Contains no identity: identity lives in ``Principal``."""

from __future__ import annotations

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .common import LocaleTag, SafeId

MAX_INPUT_CHARS = 8000
MAX_CLIENT_HINTS = 20

HintValue = str | int | float | bool


class AttachmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: SafeId
    media_type: str = Field(max_length=100)


class UserInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(max_length=MAX_INPUT_CHARS)
    attachments: tuple[AttachmentRef, ...] = ()


class AgentRequest(BaseModel):
    """One user turn, as accepted by ingress."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1"
    request_id: SafeId
    turn_id: SafeId  # client idempotency key
    conversation_id: SafeId
    app_id: SafeId
    channel: SafeId = "api"
    locale: LocaleTag = "en-US"
    input: UserInput
    # UNTRUSTED, app-defined hints (e.g. a UI button intent). Never used for authorization.
    client_hints: dict[str, HintValue] = Field(default_factory=dict)
    # Confirmation tokens previously issued by the runtime for side-effecting tools.
    confirmations: tuple[str, ...] = ()
    deadline: AwareDatetime

    @field_validator("client_hints")
    @classmethod
    def _limit_hints(cls, v: dict[str, HintValue]) -> dict[str, HintValue]:
        if len(v) > MAX_CLIENT_HINTS:
            raise ValueError(f"at most {MAX_CLIENT_HINTS} client hints")
        for key, value in v.items():
            if len(key) > 64 or (isinstance(value, str) and len(value) > 256):
                raise ValueError("client hint key or value too long")
        return v

    @field_validator("confirmations")
    @classmethod
    def _limit_confirmations(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if len(v) > 5 or any(len(t) > 512 for t in v):
            raise ValueError("too many or too long confirmation tokens")
        return v

    def remaining_ms(self, now: datetime) -> int:
        return max(0, int((self.deadline - now).total_seconds() * 1000))
