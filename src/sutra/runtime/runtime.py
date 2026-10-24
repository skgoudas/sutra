"""Runtime: owns the run lifecycle. One instance per app process."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sutra.contracts.common import new_ulid
from sutra.contracts.errors import ErrorCode, SutraError
from sutra.contracts.events import RunEvent
from sutra.contracts.identity import Principal
from sutra.contracts.policy import PolicyInput, ResourceDescriptor
from sutra.contracts.request import AgentRequest
from sutra.contracts.result import (
    AgentResult,
    DispatchDecision,
    InputRequest,
    ResponseMessage,
    RunStatus,
    UnitOutput,
    VersionStamp,
)
from sutra.contracts.units import ExecutionLimits
from sutra.dispatch import Dispatcher, DispatchInput
from sutra.eval.core import EvalRecord, EvalSink, Redactor, ValidationOutcome, Validator

from .context import ExecutionContext, RunTrace, Services
from .events import EventSink
from .primitives import BudgetMeter, CancellationToken, Deadline
from .stores import ConversationLock, ConversationRecord, ResultStore, SessionStore, Turn
from .unit import normalize_output

_log = logging.getLogger("sutra.runtime")

# Errors after which we answer with the app fallback (degraded) rather than fail.
DEGRADE_CODES = frozenset({
    ErrorCode.BUDGET_EXCEEDED, ErrorCode.LOOP_DETECTED, ErrorCode.DEADLINE_EXCEEDED,
    ErrorCode.MODEL_UNAVAILABLE, ErrorCode.MODEL_OUTPUT_INVALID, ErrorCode.DEPENDENCY_UNAVAILABLE,
    ErrorCode.DEPENDENCY_TIMEOUT, ErrorCode.RATE_LIMITED, ErrorCode.KILL_SWITCH,
})


@dataclass
class RuntimeSettings:
    app_id: str
    bundle_hash: str = "dev"
    max_deadline_ms: int = 30000
    fallback_text: str = "Sorry, I can't help with that right now. Please try again."
    eval_sample_rate: float = 1.0
    debug: bool = False


class Runtime:
    def __init__(
        self,
        *,
        settings: RuntimeSettings,
        services: Services,
        dispatcher: Dispatcher,
        sessions: SessionStore,
        results: ResultStore,
        lock: ConversationLock,
        validators: Sequence[Validator] = (),
        eval_sink: EvalSink | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.settings = settings
        self.services = services
        self.dispatcher = dispatcher
        self.sessions = sessions
        self.results = results
        self.lock = lock
        self.validators = list(validators)
        self.eval_sink = eval_sink
        self.redactor = redactor or Redactor()
        self._background: set[asyncio.Task[None]] = set()

    # -- public API -----------------------------------------------------------------

    async def submit(self, request: AgentRequest, principal: Principal) -> AsyncIterator[RunEvent]:
        """Run one turn, streaming events. Closing the iterator cancels the run."""
        run_id = new_ulid()
        sink = EventSink(run_id)
        cancel = CancellationToken()
        task = asyncio.create_task(self._execute(request, principal, run_id, sink, cancel))
        try:
            while True:
                event = await sink.next()
                if event is None:
                    break
                yield event
            await task
        finally:
            if not task.done():
                cancel.cancel()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def run(self, request: AgentRequest, principal: Principal) -> AgentResult:
        """Run one turn and return the final result (events are drained)."""
        result: AgentResult | None = None
        async for event in self.submit(request, principal):
            if event.terminal:
                result = AgentResult.model_validate(event.payload["result"])
        assert result is not None
        return result

    async def drain(self) -> None:
        """Wait for background work (eval emission). Call on shutdown and in tests."""
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    # -- lifecycle --------------------------------------------------------------------

    async def _execute(self, request: AgentRequest, principal: Principal, run_id: str,
                       sink: EventSink, cancel: CancellationToken) -> None:
        start = time.perf_counter()
        attrs = {"sutra.app.id": self.settings.app_id, "sutra.run.id": run_id,
                 "sutra.channel": request.channel, "sutra.auth_level": str(principal.auth_level)}
        trace = RunTrace()
        try:
            with self.services.telemetry.span("sutra.run", attrs) as span:
                sink.emit("run.started", {"run_id": run_id})
                result, _, decision, validations = await self._lifecycle(
                    request, principal, run_id, sink, cancel, trace)
                span.set("sutra.run.status", result.status)
                if decision:
                    span.set("sutra.unit.id", decision.unit)
                    span.set("sutra.dispatch.strategy", decision.strategy)
                if result.error:
                    span.set_error(str(result.error.code))
            latency_ms = int((time.perf_counter() - start) * 1000)
            labels = {"sutra.app.id": self.settings.app_id, "sutra.run.status": result.status}
            self.services.telemetry.count("sutra.runs", 1, labels)
            self.services.telemetry.record("sutra.run.latency_ms", latency_ms, labels)
            if result.pending_input:
                sink.emit("input.required", result.pending_input.model_dump(mode="json"))
            terminal = "run.failed" if result.status in ("failed", "cancelled") else "run.completed"
            sink.emit(terminal, {"result": result.model_dump(mode="json")})
            self._schedule_eval(request, principal, result, decision, validations, latency_ms)
        finally:
            sink.close()

    async def _lifecycle(self, request: AgentRequest, principal: Principal, run_id: str,
                         sink: EventSink, cancel: CancellationToken, trace: RunTrace):
        versions = lambda: self._versions(trace)  # noqa: E731

        def fail(code: ErrorCode, *, status: RunStatus = "failed",
                 decision: DispatchDecision | None = None, ctx: ExecutionContext | None = None) -> AgentResult:
            messages = [ResponseMessage(text=self.settings.fallback_text, generated_by="template")] \
                if status == "degraded" else []
            return AgentResult(run_id=run_id, status=status, messages=messages,
                               error=SutraError(code).to_info(reference=run_id), entrypoint=decision,
                               usage=ctx.budget.usage if ctx else BudgetMeter(ExecutionLimits()).usage,
                               versions=versions())

        if principal.subject_hmac is None:
            return fail(ErrorCode.UNAUTHENTICATED), None, None, []
        if request.app_id != self.settings.app_id:
            return fail(ErrorCode.INVALID_REQUEST), None, None, []
        if not self.services.flags.enabled("app"):
            return fail(ErrorCode.KILL_SWITCH, status="degraded"), None, None, []

        remaining = min(request.remaining_ms(datetime.now(UTC)), self.settings.max_deadline_ms)
        if remaining <= 0:
            return fail(ErrorCode.DEADLINE_EXCEEDED), None, None, []
        deadline = Deadline.after_ms(remaining)

        # Turn-level idempotency: a retried turn returns the stored result.
        result_key = f"{self.settings.app_id}:{principal.subject_hmac}:{request.conversation_id}:{request.turn_id}"
        prior = await self.results.get(result_key)
        if prior is not None:
            return prior, None, prior.entrypoint, []

        conversation = await self.sessions.get(request.conversation_id)
        if conversation is not None and conversation.owner_hmac != principal.subject_hmac:
            return fail(ErrorCode.NOT_FOUND_OR_FORBIDDEN), None, None, []
        if not await self.lock.try_acquire(request.conversation_id):
            return fail(ErrorCode.CONFLICT), None, None, []
        try:
            if conversation is None:
                conversation = await self.sessions.create(request.conversation_id, principal.subject_hmac)
            result, ctx, decision, validations = await self._run_unit(
                request, principal, run_id, sink, cancel, trace, deadline, conversation, fail)
            if result.status not in ("failed", "cancelled"):
                await self.results.put(result_key, result)
                turns = [Turn("user", request.input.text, run_id)]
                turns += [Turn("assistant", m.text, run_id, decision.unit if decision else None)
                          for m in result.messages]
                sticky = decision.unit if (decision and result.status == "needs_input") else None
                await self.sessions.append(request.conversation_id, turns, sticky)
            return result, ctx, decision, validations
        finally:
            await self.lock.release(request.conversation_id)

    async def _run_unit(self, request, principal, run_id, sink, cancel, trace, deadline,
                        conversation: ConversationRecord, fail):
        units = {uid: u.definition for uid, u in self.services.units.items()}
        decision = await self.dispatcher.dispatch(DispatchInput(request, principal, conversation, units))
        unit = self.services.units[decision.unit]
        definition = unit.definition
        trace.units.add(definition.ref)
        if not self.services.flags.enabled(f"unit.{definition.id}"):
            return fail(ErrorCode.KILL_SWITCH, status="degraded", decision=decision), None, decision, []

        ctx = ExecutionContext(
            request=request, principal=principal, unit=definition, run_id=run_id,
            deadline=deadline.child(definition.limits.timeout_ms), cancel=cancel,
            budget=BudgetMeter(definition.limits), events=sink, services=self.services,
            conversation=conversation, trace=trace,
        )
        policy = self.services.policy.decide(PolicyInput(
            action="run.start", principal=ctx.claims,
            resource=ResourceDescriptor(type="unit", id=definition.id, version=definition.version,
                                        min_auth_level=definition.min_auth_level),
            context={"app_id": self.settings.app_id, "channel": request.channel},
        ))
        if policy.effect == "require_auth":
            out = UnitOutput.ask(InputRequest(kind="auth", prompt_key="auth.required",
                                              required_auth_level=str(definition.min_auth_level)))
            return self._wrap(run_id, out, decision, ctx), ctx, decision, []
        if not policy.allowed:
            return fail(ErrorCode.POLICY_DENIED, decision=decision, ctx=ctx), ctx, decision, []

        sink.progress("unit.started", unit=definition.id)
        try:
            async with asyncio.timeout(ctx.deadline.timeout_s()):
                with self.services.telemetry.span("sutra.unit", {"sutra.unit.id": definition.id,
                                                                 "sutra.unit.version": definition.version}):
                    output = normalize_output(await unit.run(ctx))
        except TimeoutError:
            return fail(ErrorCode.DEADLINE_EXCEEDED, status="degraded", decision=decision, ctx=ctx), \
                ctx, decision, []
        except SutraError as exc:
            if exc.code == ErrorCode.CANCELLED:
                return fail(exc.code, status="cancelled", decision=decision, ctx=ctx), ctx, decision, []
            status: RunStatus = "degraded" if exc.code in DEGRADE_CODES else "failed"
            _log.info("unit %s stopped: %s", definition.id, exc.code)
            return fail(exc.code, status=status, decision=decision, ctx=ctx), ctx, decision, []
        except Exception as exc:  # noqa: BLE001
            _log.error("unit %s crashed: %s", definition.id, type(exc).__name__,
                       exc_info=self.settings.debug)
            return fail(ErrorCode.INTERNAL, decision=decision, ctx=ctx), ctx, decision, []

        validations = [await v.validate(ctx, output) for v in self.validators]
        if any(not v.passed and v.action == "block" for v in validations):
            blocked = [v.validator for v in validations if not v.passed and v.action == "block"]
            _log.info("validators blocked output: %s", ",".join(blocked))
            out = UnitOutput(status="degraded",
                             messages=[ResponseMessage(text=self.settings.fallback_text, generated_by="template")])
            return self._wrap(run_id, out, decision, ctx), ctx, decision, validations
        return self._wrap(run_id, output, decision, ctx), ctx, decision, validations

    # -- helpers ----------------------------------------------------------------------

    def _versions(self, trace: RunTrace) -> VersionStamp:
        return VersionStamp(bundle_hash=self.settings.bundle_hash, units=sorted(trace.units),
                            prompts=sorted(trace.prompts), models=sorted(trace.models),
                            policy=self.services.policy.version)

    def _wrap(self, run_id: str, out: UnitOutput, decision: DispatchDecision,
              ctx: ExecutionContext) -> AgentResult:
        return AgentResult(run_id=run_id, status=out.status, messages=out.messages, ui_blocks=out.ui_blocks,
                           pending_input=out.pending_input, data=out.data, entrypoint=decision,
                           usage=ctx.budget.usage, versions=self._versions(ctx.trace))

    def _schedule_eval(self, request: AgentRequest, principal: Principal, result: AgentResult,
                       decision: DispatchDecision | None, validations: list[ValidationOutcome],
                       latency_ms: int) -> None:
        if self.eval_sink is None or random.random() >= self.settings.eval_sample_rate:
            return
        record = EvalRecord(
            app_id=self.settings.app_id, run_id=result.run_id, unit=decision.unit if decision else None,
            channel=request.channel, locale=request.locale, status=result.status,
            error_code=str(result.error.code) if result.error else None, subject_hmac=principal.subject_hmac,
            request_redacted=self.redactor(request.input.text),
            response_redacted=self.redactor("\n".join(m.text for m in result.messages)),
            validators=validations, usage=result.usage, versions=result.versions, latency_ms=latency_ms,
        )
        sink = self.eval_sink

        async def emit() -> None:
            try:
                await sink.emit(record)
            except Exception:  # noqa: BLE001 - eval must never affect serving
                _log.warning("eval emit failed")

        task = asyncio.create_task(emit())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

