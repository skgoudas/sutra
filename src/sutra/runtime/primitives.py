"""Deadline, cancellation and budget primitives. All are propagated parent -> child."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter
from typing import Any

from sutra.contracts.errors import BudgetExceeded, Cancelled, DeadlineExceeded, LoopDetected
from sutra.contracts.models import TokenUsage
from sutra.contracts.result import Usage
from sutra.contracts.units import ExecutionLimits


class Deadline:
    """Monotonic-clock deadline. Children can only shrink it."""

    __slots__ = ("_expires",)

    def __init__(self, expires_monotonic: float) -> None:
        self._expires = expires_monotonic

    @classmethod
    def after_ms(cls, ms: int) -> Deadline:
        return cls(time.monotonic() + ms / 1000)

    def remaining_ms(self) -> int:
        return max(0, int((self._expires - time.monotonic()) * 1000))

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self._expires

    def child(self, max_ms: int | None = None) -> Deadline:
        if max_ms is None:
            return Deadline(self._expires)
        return Deadline(min(self._expires, time.monotonic() + max_ms / 1000))

    def timeout_s(self, cap_ms: int | None = None) -> float:
        ms = self.remaining_ms()
        if cap_ms is not None:
            ms = min(ms, cap_ms)
        return ms / 1000

    def check(self) -> None:
        if self.expired:
            raise DeadlineExceeded("deadline reached")


class CancellationToken:
    """Cooperative cancellation; cancelling a parent cancels all children."""

    def __init__(self, parent: CancellationToken | None = None) -> None:
        self._event = asyncio.Event()
        self._children: list[CancellationToken] = []
        self._parent = parent
        if parent is not None:
            parent._children.append(self)
            if parent.cancelled:
                self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        if not self._event.is_set():
            self._event.set()
            for child in self._children:
                child.cancel()

    async def wait(self) -> None:
        await self._event.wait()

    def child(self) -> CancellationToken:
        return CancellationToken(self)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise Cancelled("run cancelled")


def call_signature(tool: str, args: dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{tool}\x00{canonical}".encode()).hexdigest()


class BudgetMeter:
    """Hard per-execution budgets. Charges propagate to the parent meter."""

    def __init__(self, limits: ExecutionLimits, parent: BudgetMeter | None = None) -> None:
        self.limits = limits
        self.parent = parent
        self.usage = Usage()
        self._signatures: Counter[str] = Counter()

    def _increment(self, field: str, limit: int, amount: int = 1) -> None:
        current = getattr(self.usage, field)
        if current + amount > limit:
            raise BudgetExceeded(f"{field} limit {limit} reached", details={"budget": field})
        if self.parent is not None:
            self.parent._increment(field, getattr(self.parent.limits, f"max_{field}", 1 << 62), amount)
        setattr(self.usage, field, current + amount)

    def step(self) -> None:
        self._increment("steps", self.limits.max_steps)

    def delegation(self) -> None:
        self._increment("delegations", 1 << 62)

    def tool_call(self, tool: str, args: dict[str, Any]) -> None:
        sig = call_signature(tool, args)
        if self._signatures[sig] >= self.limits.max_identical_tool_calls:
            raise LoopDetected(f"identical call to {tool} repeated", details={"tool": tool})
        self._increment("tool_calls", self.limits.max_tool_calls)
        self._signatures[sig] += 1

    def _check_token_budgets(self, estimated_input_tokens: int) -> None:
        if self.usage.input_tokens + estimated_input_tokens > self.limits.max_input_tokens:
            raise BudgetExceeded("input token budget reached", details={"budget": "input_tokens"})
        if self.usage.output_tokens >= self.limits.max_output_tokens:
            raise BudgetExceeded("output token budget reached", details={"budget": "output_tokens"})
        if self.usage.cost_usd_micros >= self.limits.max_cost_usd_micros:
            raise BudgetExceeded("cost budget reached", details={"budget": "cost"})
        if self.parent is not None:
            self.parent._check_token_budgets(estimated_input_tokens)

    def precheck_model_call(self, estimated_input_tokens: int = 0) -> None:
        self._check_token_budgets(estimated_input_tokens)
        self._increment("model_calls", self.limits.max_model_calls)

    def record_model_usage(self, usage: TokenUsage, cost_micros: int) -> None:
        """Record consumed tokens/cost (post-call; never raises — next precheck enforces)."""
        self.usage.input_tokens += usage.input_tokens
        self.usage.cached_input_tokens += usage.cached_input_tokens
        self.usage.output_tokens += usage.output_tokens
        self.usage.cost_usd_micros += cost_micros
        if self.parent is not None:
            self.parent.record_model_usage(usage, cost_micros)

    def remaining_output_tokens(self) -> int:
        own = max(0, self.limits.max_output_tokens - self.usage.output_tokens)
        if self.parent is not None:
            return min(own, self.parent.remaining_output_tokens())
        return own
