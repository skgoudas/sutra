from __future__ import annotations

import os

import pytest
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.workflow import START, Workflow

import it_helpdesk_workflow as desk
from sutra import ConfigError
from sutra.contracts.errors import ErrorCode, SutraError
from sutra.engine_adk import AdkUnit, SutraAdkModel
from sutra.testing import make_request, principal


class Recording(desk.OfflineModel):
    def __init__(self) -> None:
        self.requests = []

    async def generate(self, target, request):
        self.requests.append(request)
        return await super().generate(target, request)


async def _turn(runtime, text, conversation, confirmations=()):
    request = make_request(text, app_id="it-desk-app", conversation_id=conversation, confirmations=confirmations)
    nodes, result = [], None
    async for event in runtime.submit(request, principal(subject="emp-123")):
        if event.type == "progress" and event.payload.get("stage") == "node":
            nodes.append(event.payload["node"])
        elif "result" in event.payload:
            result = event.payload["result"]
    return nodes, result


@pytest.fixture
def runtime_and_model():
    desk.TICKETS.clear()
    model = Recording()
    return desk.build(model, confirm_secret=os.urandom(32)), model


async def test_question_fans_out_and_joins(runtime_and_model):
    runtime, model = runtime_and_model
    nodes, r = await _turn(runtime, "My VPN keeps dropping, help", "c1")
    assert r["status"] == "completed", r
    assert {"triage", "researcher", "check_status", "join", "writer"} <= set(nodes)
    assert "open_ticket" not in nodes
    assert "degraded" in r["messages"][0]["text"]  # only the writer's text is the answer
    assert r["usage"]["model_calls"] == 4 and r["usage"]["tool_calls"] == 2
    # triage's output_schema reaches the model as a JSON schema
    assert model.requests[0].response_schema["properties"]["category"]["enum"] == ["question", "incident", "other"]


async def test_incident_needs_confirmation_then_opens_bound_ticket(runtime_and_model):
    runtime, _ = runtime_and_model
    _, r1 = await _turn(runtime, "My laptop screen is cracked", "c2")
    assert r1["status"] == "needs_input" and r1["pending_input"]["kind"] == "confirm"
    assert desk.TICKETS == []
    token = r1["pending_input"]["confirmation"]["token"]

    nodes, r2 = await _turn(runtime, "yes", "c2", (token,))  # history lets triage re-classify "yes"
    assert r2["status"] == "completed", r2
    assert nodes[-1] == "open_ticket" and "INC-00001" in r2["messages"][0]["text"]
    assert desk.TICKETS == [{"employee_id": "emp-123", "device": "laptop", "urgency": "high"}]


async def test_out_of_scope_uses_function_node_only(runtime_and_model):
    runtime, _ = runtime_and_model
    nodes, r = await _turn(runtime, "Book me a meeting room", "c3")
    assert nodes[-1] == "hand_off" and "4357" in r["messages"][0]["text"]
    assert r["usage"]["model_calls"] == 1


async def test_sutra_error_inside_workflow_keeps_its_code():
    class Down:
        async def generate(self, target, request):
            raise SutraError(ErrorCode.MODEL_UNAVAILABLE, "down")

    runtime = desk.build(Down(), confirm_secret=os.urandom(32))
    _, r = await _turn(runtime, "VPN?", "c4")
    assert r["error"]["code"] == "MODEL_UNAVAILABLE"


def test_workflow_nodes_are_validated():
    def raw(x: str) -> str:
        """raw"""
        return x

    bad_tool = LlmAgent(name="a", model=SutraAdkModel(model="fast"), instruction="x", tools=[FunctionTool(raw)])
    with pytest.raises(ConfigError, match="not a SutraAdkTool"):
        AdkUnit(desk.UNIT, Workflow(name="w", edges=[(START, bad_tool)]))

    bad_alias = LlmAgent(name="b", model=SutraAdkModel(model="other"), instruction="x")
    with pytest.raises(ConfigError, match="not in unit models"):
        AdkUnit(desk.UNIT, Workflow(name="w", edges=[(START, bad_alias)]))

    foreign = LlmAgent(name="c", model="gemini-2.0-flash", instruction="x")
    nested = Workflow(name="inner", edges=[(START, foreign)])
    with pytest.raises(ConfigError, match="must be SutraAdkModel"):
        AdkUnit(desk.UNIT, Workflow(name="w", edges=[(START, nested)]))
