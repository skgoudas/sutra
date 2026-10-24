from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from sutra import ConfigError, unit
from sutra.engine_adk import AdkUnit, SutraAdkModel, adk_tools
from sutra.testing import build_test_runtime, make_request, principal, text_response, tool_call_response
from hello_app.tools import kb_search


def _adk_unit(tools=None, model_alias="default"):
    definition = unit("adk", tools=["kb.search"], models=["default"])(lambda ctx: None).definition
    agent = LlmAgent(name="helpdesk", model=SutraAdkModel(model=model_alias), instruction="Help.",
                     tools=tools if tools is not None else adk_tools([kb_search]))
    return AdkUnit(definition, agent)


async def test_adk_unit_runs_through_sutra_choke_points():
    runtime, provider, telemetry = build_test_runtime([_adk_unit()], tools=[kb_search])
    provider.push(tool_call_response("kb__search", {"query": "returns"}), text_response("30 days"))
    events = [e async for e in runtime.submit(make_request("returns?"), principal())]
    result = events[-1].payload["result"]
    assert result["status"] == "completed", result
    assert result["messages"][0]["text"] == "30 days"
    assert result["usage"]["tool_calls"] == 1 and result["usage"]["model_calls"] == 2
    assert telemetry.find("sutra.tool") and len(telemetry.find("sutra.model")) == 2
    assert any(e.type == "progress" for e in events)


def test_adk_tree_rejects_raw_tools_and_foreign_models():
    def raw(x: str) -> str:
        """raw"""
        return x

    with pytest.raises(ConfigError):
        _adk_unit(tools=[FunctionTool(raw)])
    with pytest.raises(ConfigError):
        _adk_unit(model_alias="unlisted")
