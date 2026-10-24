"""Tool contracts. Tools are typed, versioned and governed by the ToolExecutor."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import AuthLevel, DataClass
from .errors import RETRYABLE_CODES, ErrorCode, ErrorInfo
from .result import Citation, ConfirmationChallenge

# Binding sources usable in ``ToolDefinition.bound_args``. Values come from the verified
# principal or the request, never from the model.
BindingSource = Literal[
    "principal.subject",
    "principal.subject_hmac",
    "principal.tenant",
    "request.locale",
    "request.channel",
    "request.conversation_id",
]


class ToolAuth(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["none", "service", "delegated", "api_key"] = "none"
    audience: str | None = None
    scopes: tuple[str, ...] = ()
    min_auth_level: AuthLevel = AuthLevel.ANONYMOUS


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=2, ge=1, le=5)
    base_delay_ms: int = 100
    max_delay_ms: int = 1000
    retry_on: frozenset[ErrorCode] = RETRYABLE_CODES


class ToolSpecForModel(BaseModel):
    """The model-visible view of a tool: bound args are removed from the schema."""

    name: str
    description: str
    parameters: dict[str, Any]


ToolHandler = Callable[[Any, Any], Awaitable[Any]]  # (args_model_instance, ToolCallContext)


class ToolDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
    version: str = "1.0.0"
    description: str
    side_effect: Literal["read", "write"] = "read"
    data_classification: DataClass = DataClass.INTERNAL
    args_model: type[BaseModel]
    result_model: type[BaseModel] | None = None
    auth: ToolAuth = Field(default_factory=ToolAuth)
    bound_args: dict[str, BindingSource] = Field(default_factory=dict)
    requires_confirmation: bool = False
    timeout_ms: int = 3000
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    breaker_key: str | None = None  # defaults to tool name
    result_projection: frozenset[str] | None = None  # model-visible fields
    max_result_chars: int = 8000
    handler: ToolHandler

    @property
    def model_name(self) -> str:
        """Name exposed to models (dots are not allowed by some providers)."""
        return self.name.replace(".", "__")

    def spec_for_model(self) -> ToolSpecForModel:
        schema = self.args_model.model_json_schema()
        props = dict(schema.get("properties", {}))
        required = [r for r in schema.get("required", []) if r not in self.bound_args]
        for bound in self.bound_args:
            props.pop(bound, None)
        params: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            params["required"] = required
        if "$defs" in schema:
            params["$defs"] = schema["$defs"]
        return ToolSpecForModel(name=self.model_name, description=self.description, parameters=params)


class ToolResult(BaseModel):
    tool: str
    ok: bool
    data: dict[str, Any] | None = None  # projected, model-safe payload
    error: ErrorInfo | None = None
    citation: Citation | None = None
    challenge: ConfirmationChallenge | None = None
    attempts: int = 0
    latency_ms: int = 0
    replayed: bool = False  # served from the idempotency store

    def to_model_payload(self) -> dict[str, Any]:
        if self.ok:
            return {"ok": True, "data": self.data or {}}
        assert self.error is not None
        return {"ok": False, "error": {"code": self.error.code, "retryable": self.error.retryable}}
