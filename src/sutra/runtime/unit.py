"""The Unit protocol: the one interface every agent implementation satisfies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sutra.contracts.result import UnitOutput
from sutra.contracts.units import UnitDefinition

if TYPE_CHECKING:
    from .context import ExecutionContext


@runtime_checkable
class Unit(Protocol):
    """An executable agent unit.

    ``run`` returns the unit's output; streaming/progress is emitted through
    ``ctx.events``. The runtime owns the terminal event, budgets, deadline and
    persistence — units never emit ``run.completed``/``run.failed`` themselves.
    """

    definition: UnitDefinition

    async def run(self, ctx: ExecutionContext) -> UnitOutput | str: ...


def normalize_output(value: UnitOutput | str) -> UnitOutput:
    if isinstance(value, UnitOutput):
        return value
    if isinstance(value, str):
        return UnitOutput.text(value, generated_by="code")
    raise TypeError(f"unit returned unsupported type {type(value).__name__}")
