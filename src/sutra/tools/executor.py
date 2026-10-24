"""ToolExecutor: the policy enforcement point for every tool call.

Order (fixed): resolve -> allowlist -> kill switch -> bind args -> validate ->
policy/confirmation -> budget & loop guard -> idempotency -> breaker ->
credentials -> timeout & retry -> project -> audit -> telemetry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from sutra.contracts.errors import ErrorCode, SutraError
from sutra.contracts.policy import PolicyDecision, PolicyInput, ResourceDescriptor
from sutra.contracts.result import Citation
from sutra.contracts.tools import BindingSource, ToolDefinition, ToolResult, ToolSpecForModel
from sutra.runtime.events import FlagProvider, StaticFlags
from sutra.runtime.primitives import call_signature
from sutra.runtime.stores import IdempotencyStore, InMemoryIdempotencyStore
from sutra.telemetry.core import NoopTelemetry, Telemetry

from .audit import AuditRecord, AuditSink, LoggingAuditSink
from .breaker import BreakerRegistry
from .confirmation import ConfirmationService
from .credentials import CredentialBroker
from .decorator import ToolCallContext

if TYPE_CHECKING:
    from sutra.policy.engine import PolicyEngine
    from sutra.runtime.context import ExecutionContext

_log = logging.getLogger("sutra.tools")


class ToolExecutor:
    def __init__(
        self,
        tools: Iterable[ToolDefinition],
        *,
        policy: PolicyEngine,
        telemetry: Telemetry | None = None,
        flags: FlagProvider | None = None,
        credentials: CredentialBroker | None = None,
        confirmations: ConfirmationService | None = None,
        idempotency: IdempotencyStore | None = None,
        audit: AuditSink | None = None,
        breakers: BreakerRegistry | None = None,
        app_id: str = "",
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._by_model_name: dict[str, str] = {}
        for t in tools:
            if t.name in self._tools:
                raise ValueError(f"duplicate tool {t.name}")
            self._tools[t.name] = t
            self._by_model_name[t.model_name] = t.name
        self.policy = policy
        self.telemetry = telemetry or NoopTelemetry()
        self.flags = flags or StaticFlags()
        self.credentials = credentials or CredentialBroker()
        self.confirmations = confirmations
        self.idempotency = idempotency or InMemoryIdempotencyStore()
        self.audit = audit or LoggingAuditSink()
        self.breakers = breakers or BreakerRegistry()
        self.app_id = app_id
        self._validate_config()

    def _validate_config(self) -> None:
        for t in self._tools.values():
            if not self.credentials.supports(t.auth):
                raise ValueError(f"tool {t.name}: no credential source for auth mode {t.auth.mode!r}")
            if t.requires_confirmation and self.confirmations is None:
                raise ValueError(f"tool {t.name} requires confirmation but no ConfirmationService is set")
            unknown = set(t.bound_args) - set(t.args_model.model_fields)
            if unknown:
                raise ValueError(f"tool {t.name}: bound args not in args model: {sorted(unknown)}")

    # -- registry -----------------------------------------------------------------

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def get(self, name: str) -> ToolDefinition:
        return self._tools[name]

    def resolve(self, name: str) -> str | None:
        if name in self._tools:
            return name
        return self._by_model_name.get(name)

    def specs_for(self, allowlist: Iterable[str]) -> list[ToolSpecForModel]:
        return [self._tools[n].spec_for_model() for n in allowlist if n in self._tools]

    # -- invocation ---------------------------------------------------------------

    async def invoke(
        self, ctx: ExecutionContext, name: str, args: dict[str, Any], *, caller: str = "code"
    ) -> ToolResult:
        resolved = self.resolve(name)
        display = resolved or "unknown"
        attrs = {"sutra.tool.name": display, "sutra.tool.caller": caller,
                 "sutra.unit.id": ctx.unit.id, "sutra.run.id": ctx.run_id}
        start = time.perf_counter()
        with self.telemetry.span("sutra.tool", attrs) as span:
            result = await self._invoke(ctx, resolved, args, caller)
            result.latency_ms = int((time.perf_counter() - start) * 1000)
            outcome = "ok" if result.ok else str(result.error.code if result.error else "error")
            span.set("sutra.tool.outcome", outcome)
            span.set("sutra.tool.attempts", result.attempts)
            if not result.ok:
                span.set_error(outcome)
        self.telemetry.count("sutra.tool.calls", 1, {"sutra.tool.name": display, "sutra.tool.outcome": outcome})
        self.telemetry.record("sutra.tool.latency_ms", result.latency_ms, {"sutra.tool.name": display})
        return result

    @staticmethod
    def _error(tool: str, code: ErrorCode, attempts: int = 0, **kw: Any) -> ToolResult:
        return ToolResult(tool=tool, ok=False, error=SutraError(code).to_info(), attempts=attempts, **kw)

    async def _invoke(
        self, ctx: ExecutionContext, name: str | None, raw_args: dict[str, Any], caller: str
    ) -> ToolResult:
        ctx.check()
        if name is None or name not in ctx.unit.tools:
            return self._error(name or "unknown", ErrorCode.POLICY_DENIED)
        tool = self._tools[name]
        if not self.flags.enabled(f"tool.{name}"):
            return self._error(name, ErrorCode.KILL_SWITCH)

        # Bound args always come from the principal/request; model-supplied values are discarded.
        args = {k: v for k, v in raw_args.items() if k not in tool.bound_args}
        try:
            for arg, source in tool.bound_args.items():
                args[arg] = self._bind(ctx, source)
        except SutraError as exc:
            return self._error(name, exc.code)

        try:
            parsed = tool.args_model.model_validate(args)
        except ValidationError as exc:
            fields = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})
            return ToolResult(tool=name, ok=False,
                              error=SutraError(ErrorCode.INVALID_REQUEST).to_info(),
                              data={"invalid_fields": fields})
        canonical = parsed.model_dump(mode="json")
        sig = call_signature(name, canonical)

        confirmed = False
        if tool.requires_confirmation and self.confirmations and ctx.principal.subject_hmac:
            confirmed = self.confirmations.consume(ctx.request.confirmations, ctx.principal.subject_hmac,
                                                   name, canonical)
        decision = self.policy.decide(PolicyInput(
            action="tool.invoke",
            principal=ctx.claims,
            resource=ResourceDescriptor(
                type="tool", id=name, version=tool.version, side_effect=tool.side_effect,
                data_classification=tool.data_classification, min_auth_level=tool.auth.min_auth_level,
                attributes={"requires_confirmation": tool.requires_confirmation},
            ),
            context={"app_id": self.app_id, "unit": ctx.unit.id, "channel": ctx.request.channel,
                     "caller": caller, "confirmed": confirmed},
        ))
        if decision.effect != "allow":
            await self._audit(ctx, tool, decision, "not_executed", sig, None)
            if decision.effect == "require_confirmation":
                assert self.confirmations is not None and ctx.principal.subject_hmac is not None
                challenge = self.confirmations.issue(ctx.principal.subject_hmac, name, canonical)
                return self._error(name, ErrorCode.CONFIRMATION_REQUIRED, challenge=challenge)
            code = ErrorCode.AUTH_LEVEL_INSUFFICIENT if decision.effect == "require_auth" else ErrorCode.POLICY_DENIED
            return self._error(name, code)

        ctx.budget.tool_call(name, canonical)  # raises BudgetExceeded / LoopDetected (run-level stop)

        idem_key: str | None = None
        if tool.side_effect == "write":
            idem_key = hashlib.sha256(
                json.dumps([self.app_id, ctx.request.conversation_id, ctx.request.turn_id, sig]).encode()
            ).hexdigest()
            prior = await self.idempotency.get(idem_key)
            if prior is not None:
                return prior.model_copy(update={"replayed": True})

        breaker = self.breakers.get(tool.breaker_key or f"tool:{name}")
        try:
            credential = await self.credentials.get(ctx.principal, tool.auth)
        except SutraError as exc:
            return self._error(name, exc.code)

        result = await self._call_with_retry(ctx, tool, parsed, breaker, credential, idem_key)
        if result.ok:
            result.citation = Citation(source=f"{name}@{tool.version}", ref=(idem_key or sig)[:16])
        if tool.side_effect == "write":
            if result.ok and idem_key:
                await self.idempotency.put(idem_key, result)
            outcome = "ok" if result.ok else str(result.error.code if result.error else "error")
            await self._audit(ctx, tool, decision, outcome, sig, idem_key)
        return result

    async def _call_with_retry(self, ctx, tool, parsed, breaker, credential, idem_key) -> ToolResult:
        attempt = 0
        last_code = ErrorCode.DEPENDENCY_UNAVAILABLE
        while attempt < tool.retry.max_attempts:
            attempt += 1
            ctx.check()
            if not breaker.allow():
                return self._error(tool.name, ErrorCode.DEPENDENCY_UNAVAILABLE, attempt - 1)
            timeout_ms = min(tool.timeout_ms, ctx.deadline.remaining_ms())
            if timeout_ms <= 0:
                breaker.record(True)  # not the dependency's fault; release half-open probe
                return self._error(tool.name, ErrorCode.DEADLINE_EXCEEDED, attempt - 1)
            call = ToolCallContext(tool=tool.name, run_id=ctx.run_id, execution_id=ctx.execution_id,
                                   attempt=attempt, timeout_ms=timeout_ms, locale=ctx.request.locale,
                                   idempotency_key=idem_key, credential=credential)
            try:
                async with asyncio.timeout(timeout_ms / 1000):
                    raw = await tool.handler(parsed, call)
                breaker.record(True)
                return self._project(tool, raw, attempt)
            except TimeoutError:
                last_code = ErrorCode.DEPENDENCY_TIMEOUT
                breaker.record(False)
            except SutraError as exc:
                last_code = exc.code
                breaker.record(not exc.retryable)  # caller errors don't trip the breaker
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as exc:  # noqa: BLE001 - tool bugs must not crash the run
                _log.warning("tool %s raised %s", tool.name, type(exc).__name__)
                last_code = ErrorCode.INTERNAL
                breaker.record(False)
            if last_code not in tool.retry.retry_on or attempt >= tool.retry.max_attempts:
                break
            backoff_ms = random.uniform(0, min(tool.retry.max_delay_ms, tool.retry.base_delay_ms * 2 ** (attempt - 1)))
            if backoff_ms + 50 >= ctx.deadline.remaining_ms():
                break
            await asyncio.sleep(backoff_ms / 1000)
        return self._error(tool.name, last_code, attempt)

    @staticmethod
    def _project(tool: ToolDefinition, raw: Any, attempts: int) -> ToolResult:
        if isinstance(raw, BaseModel):
            data = raw.model_dump(mode="json")
        elif isinstance(raw, dict):
            data = tool.result_model.model_validate(raw).model_dump(mode="json") if tool.result_model else raw
        else:
            data = {"value": raw}
        if tool.result_projection is not None:
            data = {k: v for k, v in data.items() if k in tool.result_projection}
        encoded = json.dumps(data, default=str)
        if len(encoded) > tool.max_result_chars:
            data = {"truncated": True, "preview": encoded[: tool.max_result_chars]}
        return ToolResult(tool=tool.name, ok=True, data=data, attempts=attempts)

    @staticmethod
    def _bind(ctx: ExecutionContext, source: BindingSource) -> Any:
        p, r = ctx.principal, ctx.request
        if source == "principal.subject":
            if p.subject is None:
                raise SutraError(ErrorCode.AUTH_LEVEL_INSUFFICIENT, "tool needs an identified user")
            return p.subject.get_secret_value()
        value = {
            "principal.subject_hmac": p.subject_hmac,
            "principal.tenant": p.tenant,
            "request.locale": r.locale,
            "request.channel": r.channel,
            "request.conversation_id": r.conversation_id,
        }[source]
        if value is None:
            raise SutraError(ErrorCode.AUTH_LEVEL_INSUFFICIENT, f"binding {source} unavailable")
        return value

    async def _audit(self, ctx, tool: ToolDefinition, decision: PolicyDecision, outcome: str,
                     sig: str, idem_key: str | None) -> None:
        if tool.side_effect != "write" and decision.allowed:
            return
        try:
            await self.audit.write(AuditRecord(
                app_id=self.app_id, run_id=ctx.run_id, execution_id=ctx.execution_id, unit=ctx.unit.id,
                tool=tool.name, tool_version=tool.version, side_effect=tool.side_effect,
                subject_hmac=ctx.principal.subject_hmac, decision=decision.effect,
                reason_code=decision.reason_code, outcome=outcome, args_signature=sig,
                idempotency_key=idem_key, policy_version=decision.policy_version,
            ))
        except Exception:  # noqa: BLE001
            _log.error("audit write failed for tool %s", tool.name)
