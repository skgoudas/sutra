"""Unit factories referenced from ``config/units/*.yaml`` entrypoints."""

from __future__ import annotations

from sutra import ExecutionContext, FunctionUnit, UnitOutput, tool_loop
from sutra.config import BuildContext


def build_helpdesk(bc: BuildContext) -> FunctionUnit:
    """Plain-Python unit: harness tool loop over the governed model and tools."""

    async def run(ctx: ExecutionContext) -> UnitOutput:
        return await tool_loop(ctx, alias="general")

    return FunctionUnit(bc.definition, run)


def build_helpdesk_adk(bc: BuildContext):  # noqa: ANN201
    """Same capability on Google ADK; Sutra still owns models, tools and policy."""
    from google.adk.agents import LlmAgent

    from sutra.engine_adk import AdkUnit, SutraAdkModel, adk_tools

    agent = LlmAgent(
        name="helpdesk",
        model=SutraAdkModel(model="general"),
        instruction=bc.prompt("helpdesk"),
        tools=adk_tools(list(bc.tools.values())),
    )
    return AdkUnit(bc.definition, agent)
