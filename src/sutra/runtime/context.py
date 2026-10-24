"""ExecutionContext: everything a unit may use, scoped to one execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sutra.contracts.common import new_ulid
from sutra.contracts.context import ContextItem, ContextPacket
from sutra.contracts.errors import ErrorCode, PolicyDenied, SutraError
from sutra.contracts.identity import Principal
from sutra.contracts.models import ModelRequest, ModelResponse
from sutra.contracts.policy import PolicyInput, PrincipalClaims, ResourceDescriptor
from sutra.contracts.request import AgentRequest
from sutra.contracts.result import UnitOutput
from sutra.contracts.tools import ToolResult, ToolSpecForModel
from sutra.contracts.units import ContextSpec, UnitDefinition

from .events import EventSink, FlagProvider
from .primitives import BudgetMeter, CancellationToken, Deadline
from .stores import ConversationRecord
from .unit import Unit, normalize_output

if TYPE_CHECKING:
    from sutra.context.builder import ContextBuilder
    from sutra.models.client import ModelClient
    from sutra.policy.engine import PolicyEngine
    from sutra.telemetry.core import Telemetry
    from sutra.tools.executor import ToolExecutor


@dataclass
class Services:
    """Process-wide, shared harness services. Built once at startup."""

    app_id: str
    tools: ToolExecutor
    models: ModelClient
    context: ContextBuilder
    policy: PolicyEngine
    telemetry: Telemetry
    flags: FlagProvider
    units: dict[str, Unit]


@dataclass
class RunTrace:
    """Versions actually used during a run (for the result's VersionStamp)."""

    units: set[str] = field(default_factory=set)
    prompts: set[str] = field(default_factory=set)
    models: set[str] = field(default_factory=set)


class BoundTools:
    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx

    async def invoke(self, name: str, args: dict[str, Any] | None = None, *, caller: str = "code") -> ToolResult:
        return await self._ctx.services.tools.invoke(self._ctx, name, args or {}, caller=caller)

    def specs(self) -> list[ToolSpecForModel]:
        return self._ctx.services.tools.specs_for(self._ctx.unit.tools)


class BoundModels:
    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return await self._ctx.services.models.generate(self._ctx, request)


@dataclass(eq=False)
class ExecutionContext:
    request: AgentRequest
    principal: Principal
    unit: UnitDefinition
    run_id: str
    deadline: Deadline
    cancel: CancellationToken
    budget: BudgetMeter
    events: EventSink
    services: Services
    conversation: ConversationRecord | None = None
    trace: RunTrace = field(default_factory=RunTrace)
    execution_id: str = field(default_factory=new_ulid)
    parent_execution_id: str | None = None
    depth: int = 0
    state: dict[str, Any] = field(default_factory=dict)  # unit-private scratch

    @property
    def input_text(self) -> str:
        return self.request.input.text

    @property
    def tools(self) -> BoundTools:
        return BoundTools(self)

    @property
    def models(self) -> BoundModels:
        return BoundModels(self)

    @property
    def claims(self) -> PrincipalClaims:
        p = self.principal
        return PrincipalClaims(
            actor=p.actor,
            subject_hmac=p.subject_hmac,
            tenant=p.tenant,
            auth_level=p.auth_level,
            scopes=p.scopes,
            is_anonymous=p.is_anonymous,
        )

    def check(self) -> None:
        """Raise if cancelled or past deadline. Call between steps."""
        self.cancel.raise_if_cancelled()
        self.deadline.check()

    async def build_context(
        self, *, spec: ContextSpec | None = None, extra: list[ContextItem] | None = None
    ) -> ContextPacket:
        return await self.services.context.build(self, spec=spec, extra=extra or [])

    def for_child(self, child: UnitDefinition) -> ExecutionContext:
        return ExecutionContext(
            request=self.request,
            principal=self.principal,  # never escalated
            unit=child,
            run_id=self.run_id,
            deadline=self.deadline.child(child.limits.timeout_ms),
            cancel=self.cancel.child(),
            budget=BudgetMeter(child.limits, parent=self.budget),
            events=self.events,
            services=self.services,
            conversation=self.conversation,
            trace=self.trace,
            parent_execution_id=self.execution_id,
            depth=self.depth + 1,
        )

    async def delegate(self, unit_id: str) -> UnitOutput:
        """Run another unit as a child execution with a narrowed budget and deadline."""
        if unit_id not in self.unit.delegates_to:
            raise PolicyDenied("delegate_not_allowlisted")
        if self.depth + 1 > self.unit.limits.max_delegation_depth:
            raise PolicyDenied("delegation_depth_exceeded")
        if not self.services.flags.enabled(f"unit.{unit_id}"):
            raise SutraError(ErrorCode.KILL_SWITCH, f"unit {unit_id} disabled")
        target = self.services.units.get(unit_id)
        if target is None:
            raise SutraError(ErrorCode.INTERNAL, f"unknown unit {unit_id}")
        decision = self.services.policy.decide(
            PolicyInput(
                action="delegate",
                principal=self.claims,
                resource=ResourceDescriptor(type="unit", id=unit_id, version=target.definition.version,
                                            min_auth_level=target.definition.min_auth_level),
                context={"app_id": self.services.app_id, "caller_unit": self.unit.id},
            )
        )
        if not decision.allowed:
            raise PolicyDenied(decision.reason_code)
        self.budget.delegation()
        child = self.for_child(target.definition)
        self.trace.units.add(target.definition.ref)
        with self.services.telemetry.span(
            "sutra.delegate",
            {"sutra.unit.id": unit_id, "sutra.unit.version": target.definition.version,
             "sutra.execution.depth": child.depth},
        ):
            async with asyncio.timeout(child.deadline.timeout_s()):
                return normalize_output(await target.run(child))
