from __future__ import annotations

from sutra import ErrorCode, ModelAlias, ModelRequest, ModelTarget, PriceCard, SutraError, unit
from sutra.contracts.models import Message
from sutra.testing import build_test_runtime, make_request, principal, text_response


def _alias():
    return ModelAlias(alias="default", targets=[
        ModelTarget(provider="fake", model="primary", price=PriceCard(input_per_mtok_usd=1, output_per_mtok_usd=10)),
        ModelTarget(provider="fake", model="secondary"),
    ])


@unit("m", models=["default"])
async def m(ctx):
    r = await ctx.models.generate(ModelRequest(alias="default", messages=[Message.user(ctx.input_text)]))
    return f"{r.text}|{r.target}"


async def test_fallback_on_retryable_error_and_cost_accounting():
    runtime, provider, telemetry = build_test_runtime([m], aliases=[_alias()])
    provider.push(SutraError(ErrorCode.RATE_LIMITED), text_response("hello", input_tokens=100, output_tokens=10))
    result = await runtime.run(make_request("x"), principal())
    assert result.messages[0].text == "hello|fake/secondary"
    assert result.versions.models == ["fake/secondary"]
    assert [s.attributes["sutra.model.fallback_index"] for s in telemetry.find("sutra.model")] == [0, 1]


async def test_cost_uses_price_card():
    runtime, provider, _ = build_test_runtime([m], aliases=[_alias()])
    provider.push(text_response("hi", input_tokens=1000, output_tokens=100))
    result = await runtime.run(make_request("x"), principal())
    assert result.usage.cost_usd_micros == 1000 * 1 + 100 * 10


async def test_non_retryable_error_does_not_fall_back():
    runtime, provider, _ = build_test_runtime([m], aliases=[_alias()])
    provider.push(SutraError(ErrorCode.MODEL_OUTPUT_INVALID), text_response("unused"))
    result = await runtime.run(make_request("x"), principal())
    assert result.status == "degraded" and result.error.code == "MODEL_OUTPUT_INVALID"
    assert len(provider.requests) == 1


async def test_alias_must_be_allowlisted():
    @unit("nomodel")
    async def nomodel(ctx):
        await ctx.models.generate(ModelRequest(alias="default", messages=[Message.user("x")]))
        return "x"

    runtime, _, _ = build_test_runtime([nomodel])
    result = await runtime.run(make_request("x"), principal())
    assert result.error.code == "POLICY_DENIED"


async def test_max_model_calls_budget():
    @unit("chatty", models=["default"], limits={"max_model_calls": 1})
    async def chatty(ctx):
        for _ in range(2):
            await ctx.models.generate(ModelRequest(alias="default", messages=[Message.user("x")]))
        return "x"

    runtime, provider, _ = build_test_runtime([chatty])
    provider.push(text_response("a"), text_response("b"))
    result = await runtime.run(make_request("x"), principal())
    assert result.error.code == "BUDGET_EXCEEDED"
