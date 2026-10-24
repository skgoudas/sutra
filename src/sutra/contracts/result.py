"""Result contracts: what a unit returns and what the runtime emits."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .errors import ErrorInfo

RunStatus = Literal["completed", "needs_input", "degraded", "escalated", "failed", "cancelled"]
UnitStatus = Literal["completed", "needs_input", "degraded", "escalated"]


class Citation(BaseModel):
    source: str  # tool or provider name + version
    ref: str  # opaque identifier
    fetched_at: datetime | None = None


class ResponseMessage(BaseModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)
    generated_by: Literal["template", "model", "code"] = "model"


class Option(BaseModel):
    id: str
    label: str


class ConfirmationChallenge(BaseModel):
    token: str
    tool: str
    summary_key: str  # i18n key describing the action; never raw arguments
    expires_at: datetime


class InputRequest(BaseModel):
    kind: Literal["clarify", "choose", "confirm", "auth", "form"]
    prompt_key: str
    options: list[Option] = Field(default_factory=list)
    confirmation: ConfirmationChallenge | None = None
    required_auth_level: str | None = None


class UiBlock(BaseModel):
    """App-registered UI payload. ``type`` selects an app-defined, versioned schema."""

    type: str
    version: str = "1"
    data: dict[str, Any]


class Usage(BaseModel):
    steps: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    delegations: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd_micros: int = 0


class VersionStamp(BaseModel):
    bundle_hash: str
    units: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    policy: str | None = None


class DispatchDecision(BaseModel):
    unit: str
    unit_version: str
    strategy: str
    confidence: float | None = None
    candidates: list[tuple[str, float]] = Field(default_factory=list)


class UnitOutput(BaseModel):
    """What a unit returns. The runtime wraps it into an ``AgentResult``."""

    status: UnitStatus = "completed"
    messages: list[ResponseMessage] = Field(default_factory=list)
    ui_blocks: list[UiBlock] = Field(default_factory=list)
    pending_input: InputRequest | None = None
    data: dict[str, Any] | None = None  # typed payload for programmatic callers

    @classmethod
    def text(cls, text: str, *, generated_by: Literal["template", "model", "code"] = "model",
             citations: list[Citation] | None = None) -> UnitOutput:
        return cls(messages=[ResponseMessage(text=text, generated_by=generated_by,
                                             citations=citations or [])])

    @classmethod
    def ask(cls, request: InputRequest, text: str | None = None) -> UnitOutput:
        msgs = [ResponseMessage(text=text, generated_by="code")] if text else []
        return cls(status="needs_input", pending_input=request, messages=msgs)


class AgentResult(BaseModel):
    run_id: str
    status: RunStatus
    messages: list[ResponseMessage] = Field(default_factory=list)
    ui_blocks: list[UiBlock] = Field(default_factory=list)
    pending_input: InputRequest | None = None
    data: dict[str, Any] | None = None
    error: ErrorInfo | None = None
    entrypoint: DispatchDecision | None = None
    usage: Usage = Field(default_factory=Usage)
    versions: VersionStamp
