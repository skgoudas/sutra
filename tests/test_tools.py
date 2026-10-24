from __future__ import annotations

import os

from pydantic import BaseModel

from sutra import AuthLevel, ErrorCode, SutraError, ToolCallContext, tool, tool_loop, unit
from sutra.policy import PolicyEngine
from sutra.tools import ConfirmationService, InMemoryAuditSink
from sutra.testing import build_test_runtime, make_request, principal, text_response, tool_call_response
from hello_app.tools import TICKETS, kb_search, ticket_create


@unit("helpdesk", tools=["kb.search", "ticket.create"], models=["default"])
async def helpdesk(ctx):
    return await tool_loop(ctx, alias="default")


def _runtime(**kw):
    kw.setdefault("confirmations", ConfirmationService(os.urandom(32)))
    return build_test_runtime([helpdesk], tools=[kb_search, ticket_create], **kw)


async def test_model_tool_loop_with_citation():
    runtime, provider, telemetry = _runtime()
    provider.push(tool_call_response("kb__search", {"query": "store hours"}), text_response("8am-8pm"))
    result = await runtime.run(make_request("hours?"), principal())
    assert result.status == "completed"
    assert result.messages[0].citations[0].source == "kb.search@1.0.0"
    assert result.usage.tool_calls == 1 and result.usage.model_calls == 2
    tool_msg = provider.requests[1][1].messages[-1]
    assert tool_msg.role == "tool" and tool_msg.parts[0].result["ok"] is True
    assert telemetry.find("sutra.tool")[0].attributes["sutra.tool.outcome"] == "ok"


def test_bound_args_hidden_from_model_schema():
    spec = ticket_create.spec_for_model()
    assert "user_id" not in spec.parameters["properties"]
    assert spec.parameters["required"] == ["summary"]


async def test_tool_not_in_allowlist_is_denied():
    @unit("narrow", tools=["kb.search"], models=["default"])
    async def narrow(ctx):
        return await tool_loop(ctx, alias="default")

    runtime, provider, _ = build_test_runtime([narrow], tools=[kb_search, ticket_create],
                                              confirmations=ConfirmationService(os.urandom(32)))
    provider.push(tool_call_response("ticket__create", {"summary": "x"}), text_response("sorry"))
    await runtime.run(make_request("x"), principal())
    payload = provider.requests[1][1].messages[-1].parts[0].result
    assert payload == {"ok": False, "error": {"code": "POLICY_DENIED", "retryable": False}}


async def test_write_requires_confirmation_then_executes_once_with_bound_subject():
    audit = InMemoryAuditSink()
    runtime, provider, _ = _runtime(audit=audit)
    p = principal(subject="real-user")
    # Turn 1: model tries to create a ticket (and tries to spoof user_id) -> confirmation required.
    provider.push(tool_call_response("ticket__create", {"summary": "broken", "user_id": "victim"}))
    r1 = await runtime.run(make_request("open a ticket", conversation_id="t"), p)
    assert r1.status == "needs_input" and r1.pending_input.kind == "confirm"
    token = r1.pending_input.confirmation.token

    # Turn 2: client returns the token; same call now executes.
    provider.push(tool_call_response("ticket__create", {"summary": "broken"}), text_response("Ticket opened"))
    before = len(TICKETS)
    r2 = await runtime.run(make_request("yes", conversation_id="t", confirmations=(token,)), p)
    assert r2.status == "completed"
    assert len(TICKETS) == before + 1
    created = list(TICKETS.values())[-1]
    assert created.user_id == "real-user"
    assert {a.decision for a in audit.records} == {"require_confirmation", "allow"}

    # Turn 3: token is single-use.
    provider.push(tool_call_response("ticket__create", {"summary": "broken"}))
    r3 = await runtime.run(make_request("again", conversation_id="t", confirmations=(token,)), p)
    assert r3.status == "needs_input"


async def test_confirmation_token_bound_to_args():
    runtime, provider, _ = _runtime()
    p = principal()
    provider.push(tool_call_response("ticket__create", {"summary": "A"}))
    r1 = await runtime.run(make_request("x", conversation_id="b"), p)
    token = r1.pending_input.confirmation.token
    provider.push(tool_call_response("ticket__create", {"summary": "DIFFERENT"}))
    r2 = await runtime.run(make_request("y", conversation_id="b", confirmations=(token,)), p)
    assert r2.status == "needs_input"


async def test_write_tool_needs_authenticated_user():
    runtime, provider, _ = _runtime()
    provider.push(tool_call_response("ticket__create", {"summary": "x"}), text_response("please sign in"))
    await runtime.run(make_request("x"), principal(AuthLevel.ANONYMOUS))
    payload = provider.requests[1][1].messages[-1].parts[0].result
    assert payload["error"]["code"] == "AUTH_LEVEL_INSUFFICIENT"


async def test_policy_rule_denies_writes_on_kiosk():
    policy = PolicyEngine.from_dicts([{"version": "t1", "rules": [{
        "id": "kiosk", "actions": ["tool.invoke"], "effect": "deny", "reason_code": "read_only",
        "match": {"resource.side_effect": "write", "context.channel": "kiosk"}}]}])
    runtime, provider, _ = _runtime(policy=policy)
    provider.push(tool_call_response("ticket__create", {"summary": "x"}), text_response("cannot"))
    result = await runtime.run(make_request("x", channel="kiosk"), principal())
    assert provider.requests[1][1].messages[-1].parts[0].result["error"]["code"] == "POLICY_DENIED"
    assert result.versions.policy == "t1"


class FlakyArgs(BaseModel):
    n: int


attempts: list[int] = []


@tool("flaky.read", retry={"max_attempts": 3, "base_delay_ms": 1, "max_delay_ms": 2})
async def flaky(args: FlakyArgs, call: ToolCallContext) -> dict:
    """Fails twice, then succeeds."""
    attempts.append(call.attempt)
    if call.attempt < 3:
        raise SutraError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return {"n": args.n}


@tool("bad.args")
async def bad_args(args: FlakyArgs) -> dict:
    """Never reached with invalid args."""
    return {}


async def test_retry_then_success_and_invalid_args():
    @unit("u", tools=["flaky.read", "bad.args"], models=["default"])
    async def u(ctx):
        ok = await ctx.tools.invoke("flaky.read", {"n": 1})
        bad = await ctx.tools.invoke("bad.args", {"n": "not-a-number"})
        return f"{ok.ok}:{ok.attempts}:{bad.error.code}:{bad.data['invalid_fields']}"

    runtime, _, _ = build_test_runtime([u], tools=[flaky, bad_args])
    result = await runtime.run(make_request("x"), principal())
    assert result.messages[0].text == "True:3:INVALID_REQUEST:['n']"


async def test_loop_guard_degrades_run():
    runtime, provider, _ = _runtime()
    provider.push(*[tool_call_response("kb__search", {"query": "same"}) for _ in range(4)])
    result = await runtime.run(make_request("x"), principal())
    assert result.status == "degraded" and result.error.code == "LOOP_DETECTED"


async def test_step_budget_stops_runaway_loop():
    @unit("tight", tools=["kb.search"], models=["default"], limits={"max_steps": 2})
    async def tight(ctx):
        return await tool_loop(ctx, alias="default")

    runtime, provider, _ = build_test_runtime([tight], tools=[kb_search])
    provider.push(*[tool_call_response("kb__search", {"query": f"q{i}"}) for i in range(5)])
    result = await runtime.run(make_request("x"), principal())
    assert result.error.code == "BUDGET_EXCEEDED" and result.usage.steps == 2
