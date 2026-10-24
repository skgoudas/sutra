"""Test kit: scripted model provider, request/principal factories, offline runtime builder."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import SecretStr

from sutra.contracts.common import AuthLevel, new_ulid
from sutra.contracts.errors import ErrorCode, SutraError
from sutra.contracts.identity import Principal
from sutra.contracts.models import (
    Message,
    ModelAlias,
    ModelRequest,
    ModelResponse,
    ModelTarget,
    TextPart,
    TokenUsage,
    ToolCallPart,
)
from sutra.contracts.request import AgentRequest, UserInput
from sutra.harness import build_runtime
from sutra.runtime.runtime import Runtime
from sutra.runtime.unit import Unit
from sutra.telemetry.core import InMemoryTelemetry

Script = ModelResponse | SutraError | Callable[[ModelRequest], ModelResponse]


def text_response(text: str, *, input_tokens: int = 10, output_tokens: int = 5) -> ModelResponse:
    return ModelResponse(message=Message(role="assistant", parts=[TextPart(text=text)]),
                         usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens))


def tool_call_response(name: str, args: dict[str, Any] | None = None, *, call_id: str | None = None) -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", parts=[ToolCallPart(id=call_id or new_ulid(), name=name, args=args or {})]),
        finish_reason="tool_calls",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


class ScriptedModelProvider:
    """Replays a script of responses in order; records every request it receives."""

    def __init__(self, script: Iterable[Script] = ()) -> None:
        self.script: list[Script] = list(script)
        self.requests: list[tuple[ModelTarget, ModelRequest]] = []

    def push(self, *items: Script) -> None:
        self.script.extend(items)

    async def generate(self, target: ModelTarget, request: ModelRequest) -> ModelResponse:
        self.requests.append((target, request))
        if not self.script:
            raise SutraError(ErrorCode.MODEL_OUTPUT_INVALID, "scripted provider exhausted")
        item = self.script.pop(0)
        if isinstance(item, SutraError):
            raise item
        if callable(item) and not isinstance(item, ModelResponse):
            return item(request)
        return item.model_copy(deep=True)


def fake_alias(alias: str = "default", provider: str = "fake", fallbacks: int = 0) -> ModelAlias:
    targets = [ModelTarget(provider=provider, model=f"fake-model-{i}") for i in range(fallbacks + 1)]
    return ModelAlias(alias=alias, targets=targets)


def principal(level: AuthLevel = AuthLevel.AUTHENTICATED, subject: str = "user-1", **kw: Any) -> Principal:
    digest = hashlib.sha256(subject.encode()).hexdigest()[:32]
    if level == AuthLevel.ANONYMOUS:
        return Principal(subject_hmac=digest, auth_level=level, **kw)
    return Principal(subject=SecretStr(subject), subject_hmac=digest, auth_level=level,
                     assertion=SecretStr("test-assertion"), **kw)


def make_request(
    text: str,
    *,
    app_id: str = "test-app",
    conversation_id: str | None = None,
    turn_id: str | None = None,
    deadline_ms: int = 10000,
    **kw: Any,
) -> AgentRequest:
    return AgentRequest(
        request_id=new_ulid(),
        turn_id=turn_id or new_ulid(),
        conversation_id=conversation_id or new_ulid(),
        app_id=app_id,
        input=UserInput(text=text),
        deadline=datetime.now(UTC) + timedelta(milliseconds=deadline_ms),
        **kw,
    )


def build_test_runtime(
    units: Sequence[Unit],
    *,
    provider: ScriptedModelProvider | None = None,
    app_id: str = "test-app",
    aliases: Sequence[ModelAlias] | None = None,
    **kw: Any,
) -> tuple[Runtime, ScriptedModelProvider, InMemoryTelemetry]:
    """Offline runtime with a scripted 'fake' provider and strict in-memory telemetry."""
    provider = provider or ScriptedModelProvider()
    telemetry = kw.pop("telemetry", None) or InMemoryTelemetry(strict=True)
    runtime = build_runtime(
        app_id=app_id,
        units=units,
        models=aliases if aliases is not None else [fake_alias()],
        providers={"fake": provider, **kw.pop("providers", {})},
        telemetry=telemetry,
        **kw,
    )
    return runtime, provider, telemetry


__all__ = [
    "ScriptedModelProvider",
    "build_test_runtime",
    "fake_alias",
    "make_request",
    "principal",
    "text_response",
    "tool_call_response",
]
