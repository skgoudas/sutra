"""Authoring helpers for units: ``FunctionUnit``, the ``@unit`` decorator and ``tool_loop``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sutra.contracts.models import Message, ModelRequest, ToolResultPart
from sutra.contracts.result import Citation, InputRequest, UnitOutput
from sutra.contracts.units import UnitDefinition
from sutra.runtime.context import ExecutionContext
from sutra.runtime.unit import Unit

UnitFn = Callable[[ExecutionContext], Awaitable[UnitOutput | str]]


class FunctionUnit:
    """A unit implemented as a plain async function (engine="function")."""

    def __init__(self, definition: UnitDefinition, fn: UnitFn) -> None:
        self.definition = definition
        self._fn = fn

    async def run(self, ctx: ExecutionContext) -> UnitOutput | str:
        return await self._fn(ctx)


def unit(id: str, **definition: Any) -> Callable[[UnitFn], FunctionUnit]:
    """Decorate ``async def fn(ctx) -> UnitOutput | str`` into a FunctionUnit.

    Keyword arguments are passed to :class:`UnitDefinition` (version, tools, models,
    context, limits, min_auth_level, delegates_to, ...).
    """

    def wrap(fn: UnitFn) -> FunctionUnit:
        desc = definition.pop("description", None) or (fn.__doc__ or "").strip()
        return FunctionUnit(UnitDefinition(id=id, engine="function", description=desc, **definition), fn)

    return wrap


async def tool_loop(
    ctx: ExecutionContext,
    *,
    alias: str,
    system: str | None = None,
    messages: list[Message] | None = None,
    temperature: float | None = None,
) -> UnitOutput:
    """Minimal model <-> tool loop on harness choke points (no framework needed).

    Bounded by the unit's step/model/tool budgets and deadline. Returns a
    ``needs_input`` output if a tool requires user confirmation.
    """
    if messages is None:
        packet = await ctx.build_context()
        system, messages = packet.system, list(packet.messages)
    else:
        messages = list(messages)
    specs = ctx.tools.specs()
    citations: list[Citation] = []
    while True:
        ctx.check()
        ctx.budget.step()
        response = await ctx.models.generate(ModelRequest(
            alias=alias, system=system, messages=messages, tools=specs, temperature=temperature,
        ))
        calls = response.tool_calls
        if not calls:
            return UnitOutput.text(response.text, generated_by="model", citations=citations)
        messages.append(response.message)
        parts: list[ToolResultPart] = []
        for call in calls:
            ctx.events.progress("tool", tool=call.name)
            result = await ctx.tools.invoke(call.name, call.args, caller="model")
            if result.challenge is not None:
                return UnitOutput.ask(InputRequest(kind="confirm", prompt_key=result.challenge.summary_key,
                                                   confirmation=result.challenge))
            if result.citation is not None:
                citations.append(result.citation)
            parts.append(ToolResultPart(id=call.id, name=call.name, result=result.to_model_payload()))
        messages.append(Message(role="tool", parts=parts))


__all__ = ["FunctionUnit", "Unit", "tool_loop", "unit"]
