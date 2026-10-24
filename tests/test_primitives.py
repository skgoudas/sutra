from __future__ import annotations

import asyncio

import pytest

from sutra.contracts.errors import BudgetExceeded, Cancelled, DeadlineExceeded, LoopDetected
from sutra.contracts.models import TokenUsage
from sutra.contracts.units import ExecutionLimits
from sutra.runtime.primitives import BudgetMeter, CancellationToken, Deadline
from sutra.tools.breaker import CircuitBreaker


def test_deadline_child_never_extends():
    parent = Deadline.after_ms(100)
    assert parent.child(10_000).remaining_ms() <= 100
    assert parent.child(10).remaining_ms() <= 10


async def test_deadline_check_raises_when_expired():
    d = Deadline.after_ms(1)
    await asyncio.sleep(0.01)
    with pytest.raises(DeadlineExceeded):
        d.check()


def test_cancellation_propagates_to_children():
    root = CancellationToken()
    child = root.child()
    grandchild = child.child()
    root.cancel()
    assert child.cancelled and grandchild.cancelled
    with pytest.raises(Cancelled):
        grandchild.raise_if_cancelled()


def test_budget_charges_propagate_to_parent():
    parent = BudgetMeter(ExecutionLimits(max_tool_calls=2))
    child = BudgetMeter(ExecutionLimits(max_tool_calls=10), parent=parent)
    child.tool_call("a", {"x": 1})
    child.tool_call("a", {"x": 2})
    assert parent.usage.tool_calls == 2
    with pytest.raises(BudgetExceeded):
        child.tool_call("a", {"x": 3})
    assert child.usage.tool_calls == 2


def test_loop_guard_blocks_identical_calls():
    meter = BudgetMeter(ExecutionLimits(max_identical_tool_calls=2, max_tool_calls=10))
    meter.tool_call("t", {"q": "same"})
    meter.tool_call("t", {"q": "same"})
    with pytest.raises(LoopDetected):
        meter.tool_call("t", {"q": "same"})


def test_model_budget_counts_once_per_call_with_parent():
    parent = BudgetMeter(ExecutionLimits(max_model_calls=3))
    child = BudgetMeter(ExecutionLimits(), parent=parent)
    child.precheck_model_call()
    assert parent.usage.model_calls == 1 and child.usage.model_calls == 1


def test_cost_budget_enforced_on_next_call():
    meter = BudgetMeter(ExecutionLimits(max_cost_usd_micros=100))
    meter.precheck_model_call()
    meter.record_model_usage(TokenUsage(input_tokens=1, output_tokens=1), 150)
    with pytest.raises(BudgetExceeded):
        meter.precheck_model_call()


def test_breaker_opens_and_recovers():
    now = [0.0]
    b = CircuitBreaker(min_calls=4, failure_ratio=0.5, open_s=10, half_open_probes=2, clock=lambda: now[0])
    for ok in (True, False, False, False):
        assert b.allow()
        b.record(ok)
    assert b.state == "open" and not b.allow()
    now[0] = 11
    assert b.state == "half_open"
    assert b.allow() and b.allow() and not b.allow()
    b.record(True)
    b.record(True)
    assert b.state == "closed"


def test_breaker_half_open_failure_reopens():
    now = [0.0]
    b = CircuitBreaker(min_calls=2, failure_ratio=0.5, open_s=5, clock=lambda: now[0])
    b.record(False)
    b.record(False)
    now[0] = 6
    assert b.allow()
    b.record(False)
    assert b.state == "open"
