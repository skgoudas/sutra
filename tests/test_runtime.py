from __future__ import annotations

import asyncio

import pytest

from sutra import AuthLevel, ConfigError, ExecutionContext, UnitOutput, build_runtime, unit
from sutra.eval import InMemoryEvalSink, NonEmptyResponse
from sutra.runtime import StaticFlags
from sutra.testing import build_test_runtime, make_request, principal


@unit("echo", models=["default"])
async def echo(ctx: ExecutionContext) -> str:
    ctx.events.progress("thinking")
    return f"echo: {ctx.input_text}"


async def test_happy_path_events_and_result():
    runtime, _, telemetry = build_test_runtime([echo])
    events = [e async for e in runtime.submit(make_request("hi"), principal())]
    types = [e.type for e in events]
    assert types[0] == "run.started"
    assert types[-1] == "run.completed"
    assert sum(e.terminal for e in events) == 1
    assert [e.seq for e in events] == sorted(e.seq for e in events)
    result = events[-1].payload["result"]
    assert result["status"] == "completed"
    assert result["messages"][0]["text"] == "echo: hi"
    assert result["entrypoint"]["unit"] == "echo"
    assert result["versions"]["units"] == ["echo@0.1.0"]
    assert telemetry.find("sutra.run")[0].attributes["sutra.run.status"] == "completed"


async def test_turn_idempotency_returns_stored_result():
    calls = []

    @unit("counter")
    async def counter(ctx):
        calls.append(1)
        return "done"

    runtime, _, _ = build_test_runtime([counter])
    req = make_request("x", turn_id="turn-1", conversation_id="c-1")
    first = await runtime.run(req, principal())
    second = await runtime.run(req, principal())
    assert first.run_id == second.run_id
    assert len(calls) == 1


async def test_conversation_owned_by_another_principal_is_hidden():
    runtime, _, _ = build_test_runtime([echo])
    await runtime.run(make_request("a", conversation_id="conv-x"), principal(subject="alice"))
    result = await runtime.run(make_request("b", conversation_id="conv-x"), principal(subject="mallory"))
    assert result.status == "failed"
    assert result.error.code == "NOT_FOUND_OR_FORBIDDEN"


async def test_concurrent_turn_on_same_conversation_conflicts():
    gate = asyncio.Event()

    @unit("slow")
    async def slow(ctx):
        await gate.wait()
        return "ok"

    runtime, _, _ = build_test_runtime([slow])
    p = principal()
    first = asyncio.create_task(runtime.run(make_request("1", conversation_id="c"), p))
    await asyncio.sleep(0.01)
    second = await runtime.run(make_request("2", conversation_id="c"), p)
    gate.set()
    assert second.error.code == "CONFLICT"
    assert (await first).status == "completed"


async def test_deadline_produces_degraded_fallback():
    @unit("sleepy", limits={"timeout_ms": 50})
    async def sleepy(ctx):
        await asyncio.sleep(5)
        return "never"

    runtime, _, _ = build_test_runtime([sleepy])
    result = await runtime.run(make_request("x"), principal())
    assert result.status == "degraded"
    assert result.error.code == "DEADLINE_EXCEEDED"
    assert result.messages[0].generated_by == "template"


async def test_closing_stream_cancels_unit():
    started, cancelled = asyncio.Event(), asyncio.Event()

    @unit("long")
    async def long(ctx):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "x"

    runtime, _, _ = build_test_runtime([long])
    stream = runtime.submit(make_request("x"), principal())
    async for event in stream:
        if event.type == "progress":
            break
    await started.wait()
    await stream.aclose()
    await asyncio.wait_for(cancelled.wait(), 1)


async def test_insufficient_auth_level_asks_for_auth():
    @unit("account", min_auth_level=AuthLevel.AUTHENTICATED)
    async def account(ctx):
        raise AssertionError("must not run")

    runtime, _, _ = build_test_runtime([account])
    result = await runtime.run(make_request("x"), principal(AuthLevel.ANONYMOUS))
    assert result.status == "needs_input"
    assert result.pending_input.kind == "auth"


async def test_kill_switch_degrades():
    runtime, _, _ = build_test_runtime([echo], flags=StaticFlags({"unit.echo"}))
    result = await runtime.run(make_request("x"), principal())
    assert result.status == "degraded" and result.error.code == "KILL_SWITCH"


async def test_unit_crash_is_contained():
    @unit("boom")
    async def boom(ctx):
        raise RuntimeError("secret detail")

    runtime, _, _ = build_test_runtime([boom])
    result = await runtime.run(make_request("x"), principal())
    assert result.status == "failed" and result.error.code == "INTERNAL"
    assert "secret" not in result.model_dump_json()


async def test_validator_blocks_and_eval_record_is_redacted():
    @unit("empty")
    async def empty(ctx):
        return UnitOutput()

    sink = InMemoryEvalSink()
    runtime, _, _ = build_test_runtime([empty], validators=[NonEmptyResponse()], eval_sink=sink)
    result = await runtime.run(make_request("mail me at bob@example.com"), principal())
    await runtime.drain()
    assert result.status == "degraded"
    record = sink.records[0]
    assert "bob@example.com" not in record.request_redacted
    assert record.validators[0].passed is False


async def test_sticky_dispatch_returns_to_asking_unit():
    @unit("router")
    async def router(ctx):
        return "router"

    @unit("asker")
    async def asker(ctx):
        from sutra.contracts import InputRequest
        if ctx.state.get("x") is None and not ctx.conversation.turns:
            return UnitOutput.ask(InputRequest(kind="clarify", prompt_key="which"))
        return "asker answered"

    from sutra.dispatch import Rule, RulesDispatch, StickyDispatch

    runtime, _, _ = build_test_runtime(
        [router, asker], dispatch=[StickyDispatch(), RulesDispatch([Rule(unit="asker", pattern="ask")])],
        default_unit="router")
    p = principal()
    r1 = await runtime.run(make_request("please ask", conversation_id="s"), p)
    r2 = await runtime.run(make_request("blue", conversation_id="s"), p)
    assert r1.status == "needs_input" and r1.entrypoint.unit == "asker"
    assert r2.entrypoint.unit == "asker" and r2.entrypoint.strategy == "sticky"


async def test_delegation_is_allowlisted_and_depth_limited():
    @unit("child")
    async def child(ctx):
        return f"child depth {ctx.depth}"

    @unit("parent", delegates_to=["child"])
    async def parent(ctx):
        out = await ctx.delegate("child")
        return out.messages[0].text

    @unit("rogue")
    async def rogue(ctx):
        await ctx.delegate("child")
        return "x"

    runtime, _, _ = build_test_runtime([parent, child, rogue], default_unit="parent")
    result = await runtime.run(make_request("x"), principal())
    assert result.messages[0].text == "child depth 1"
    assert result.usage.delegations == 1

    runtime2, _, _ = build_test_runtime([rogue, child])
    denied = await runtime2.run(make_request("x"), principal())
    assert denied.error.code == "POLICY_DENIED"


def test_startup_validation_catches_bad_config():
    @unit("u", tools=["missing.tool"], models=["nope"])
    async def u(ctx):
        return "x"

    with pytest.raises(ConfigError) as exc:
        build_runtime(app_id="a", units=[u])
    assert "missing.tool" in str(exc.value) and "nope" in str(exc.value)


def test_request_rejects_oversized_input():
    with pytest.raises(ValueError):
        make_request("x" * 9000)
