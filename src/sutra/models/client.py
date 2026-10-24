"""ModelClient: the single path to any LLM. Aliases, fallback, budgets, cost, telemetry."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol

from sutra.contracts.errors import DeadlineExceeded, ErrorCode, PolicyDenied, SutraError
from sutra.contracts.models import ModelAlias, ModelRequest, ModelResponse, ModelTarget
from sutra.runtime.events import FlagProvider, StaticFlags
from sutra.telemetry.core import NoopTelemetry, Telemetry
from sutra.tools.breaker import BreakerRegistry

if TYPE_CHECKING:
    from sutra.runtime.context import ExecutionContext


class ModelProvider(Protocol):
    """Vendor adapter. Must map vendor errors to SutraError codes
    (429 -> RATE_LIMITED, 5xx/unavailable -> MODEL_UNAVAILABLE, bad output -> MODEL_OUTPUT_INVALID)."""

    async def generate(self, target: ModelTarget, request: ModelRequest) -> ModelResponse: ...


def estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1


def estimate_request_tokens(request: ModelRequest) -> int:
    total = estimate_tokens(request.system or "")
    for m in request.messages:
        total += estimate_tokens(m.model_dump_json())
    for t in request.tools:
        total += estimate_tokens(t.model_dump_json())
    return total


class ModelClient:
    def __init__(
        self,
        aliases: Iterable[ModelAlias],
        providers: dict[str, ModelProvider],
        *,
        telemetry: Telemetry | None = None,
        flags: FlagProvider | None = None,
        breakers: BreakerRegistry | None = None,
    ) -> None:
        self.aliases = {a.alias: a for a in aliases}
        self.providers = providers
        self.telemetry = telemetry or NoopTelemetry()
        self.flags = flags or StaticFlags()
        self.breakers = breakers or BreakerRegistry()
        for a in self.aliases.values():
            if not a.targets:
                raise ValueError(f"model alias {a.alias} has no targets")
            for t in a.targets:
                if t.provider not in providers:
                    raise ValueError(f"model alias {a.alias}: provider {t.provider!r} not registered")

    async def generate(self, ctx: ExecutionContext, request: ModelRequest) -> ModelResponse:
        if request.alias not in ctx.unit.models:
            raise PolicyDenied("model_not_allowlisted")
        alias = self.aliases.get(request.alias)
        if alias is None:
            raise SutraError(ErrorCode.INTERNAL, f"unknown model alias {request.alias}")
        if not self.flags.enabled(f"model.{request.alias}"):
            raise SutraError(ErrorCode.KILL_SWITCH, f"model alias {request.alias} disabled")
        ctx.check()
        estimated = estimate_request_tokens(request)
        ctx.budget.precheck_model_call(estimated)
        max_out = min(request.max_output_tokens or alias.max_output_tokens, ctx.budget.remaining_output_tokens())
        if max_out <= 0:
            raise SutraError(ErrorCode.BUDGET_EXCEEDED, "no output tokens left")
        request = request.model_copy(update={"max_output_tokens": max_out})

        last: SutraError | None = None
        for index, target in enumerate(alias.targets):
            if estimated > target.max_input_tokens or (request.tools and not target.supports_tools):
                continue
            if request.response_schema and not target.supports_json_schema:
                continue
            breaker = self.breakers.get(f"model:{target.id}")
            if not breaker.allow():
                last = SutraError(ErrorCode.MODEL_UNAVAILABLE, "breaker open")
                continue
            attrs = {
                "gen_ai.operation.name": "chat",
                "gen_ai.system": target.provider,
                "gen_ai.request.model": target.model,
                "sutra.model.alias": request.alias,
                "sutra.model.fallback_index": index,
                "sutra.unit.id": ctx.unit.id,
            }
            with self.telemetry.span("sutra.model", attrs) as span:
                timeout_s = ctx.deadline.timeout_s(target.timeout_ms)
                if timeout_s <= 0:
                    breaker.record(True)
                    raise DeadlineExceeded("no time left for model call")
                start = time.perf_counter()
                try:
                    async with asyncio.timeout(timeout_s):
                        response = await self.providers[target.provider].generate(target, request)
                except TimeoutError:
                    breaker.record(False)
                    if ctx.deadline.expired:
                        raise DeadlineExceeded("model call hit run deadline") from None
                    last = SutraError(ErrorCode.MODEL_UNAVAILABLE, "model timeout")
                    span.set_error(str(ErrorCode.DEPENDENCY_TIMEOUT))
                    continue
                except SutraError as exc:
                    breaker.record(not exc.retryable)
                    span.set_error(str(exc.code))
                    if not exc.retryable:
                        raise
                    last = exc
                    continue
                breaker.record(True)
                response.latency_ms = int((time.perf_counter() - start) * 1000)
                response.target = target.id
                cost = target.price.cost_micros(response.usage)
                ctx.budget.record_model_usage(response.usage, cost)
                ctx.trace.models.add(target.id)
                ctx.trace.prompts.update(request.prompt_versions)
                span.set("gen_ai.usage.input_tokens", response.usage.input_tokens)
                span.set("gen_ai.usage.output_tokens", response.usage.output_tokens)
                span.set("sutra.model.cost_usd_micros", cost)
                span.set("gen_ai.response.finish_reasons", response.finish_reason)
            labels = {"sutra.model.alias": request.alias, "gen_ai.request.model": target.model}
            self.telemetry.count("sutra.model.calls", 1, labels)
            self.telemetry.count("sutra.model.cost_usd_micros", cost, labels)
            self.telemetry.record("sutra.model.latency_ms", response.latency_ms, labels)
            return response
        raise last or SutraError(ErrorCode.MODEL_UNAVAILABLE, "no eligible model target")
