"""Leave assistant: the smallest useful Sutra app.

An employee asks about their leave balance and books time off.

- `leave.balance` is a read tool.
- `leave.request` is a write tool that needs the user's confirmation.
- Both get the employee id from the verified identity, never from the model.

Run offline (scripted model):        python examples/leave_assistant.py
Run against Gemini on Vertex AI:     GOOGLE_CLOUD_PROJECT=<project> python examples/leave_assistant.py
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from sutra import AuthLevel, ToolAuth, ToolCallContext, build_runtime, tool, tool_loop, unit
from sutra.contracts import ModelAlias, ModelTarget
from sutra.tools import ConfirmationService

# --- 1. Your systems (stand-ins for the real HR API) -----------------------------------------

BALANCES: dict[str, dict[str, int]] = {"emp-123": {"vacation": 12, "sick": 5}}
BOOKINGS: list[dict] = []

# --- 2. Tools: typed args, typed result, docstring = description for the model ----------------


class BalanceArgs(BaseModel):
    employee_id: str  # bound from the principal; hidden from the model


class Balance(BaseModel):
    vacation_days: int
    sick_days: int


@tool("leave.balance", bound_args={"employee_id": "principal.subject"},
      auth=ToolAuth(min_auth_level=AuthLevel.AUTHENTICATED))
async def leave_balance(args: BalanceArgs, call: ToolCallContext) -> Balance:
    """Get the signed-in employee's remaining leave days."""
    b = BALANCES.get(args.employee_id, {"vacation": 0, "sick": 0})
    return Balance(vacation_days=b["vacation"], sick_days=b["sick"])


class RequestArgs(BaseModel):
    employee_id: str  # bound from the principal
    kind: Literal["vacation", "sick"]
    start: date = Field(description="First day off (YYYY-MM-DD)")
    end: date = Field(description="Last day off (YYYY-MM-DD)")

    @model_validator(mode="after")
    def _ordered(self) -> RequestArgs:
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        return self


class Booking(BaseModel):
    booking_id: str
    status: Literal["booked", "rejected"]
    days: int
    remaining: int


@tool("leave.request", side_effect="write", requires_confirmation=True,
      bound_args={"employee_id": "principal.subject"},
      auth=ToolAuth(min_auth_level=AuthLevel.AUTHENTICATED))
async def leave_request(args: RequestArgs, call: ToolCallContext) -> Booking:
    """Book leave for the signed-in employee. The user must confirm before it is booked."""
    days = (args.end - args.start).days + 1
    balance = BALANCES.setdefault(args.employee_id, {"vacation": 0, "sick": 0})
    if balance[args.kind] < days:
        return Booking(booking_id="", status="rejected", days=days, remaining=balance[args.kind])
    balance[args.kind] -= days
    BOOKINGS.append(args.model_dump(mode="json"))
    return Booking(booking_id=f"L-{len(BOOKINGS):04d}", status="booked", days=days,
                   remaining=balance[args.kind])


# --- 3. The unit: one model + tool loop, with an allowlist and limits --------------------------

INSTRUCTIONS = """You are an HR leave assistant.
Use leave.balance to answer balance questions. Use leave.request to book leave.
Today's date is {today}. Answer in one or two sentences."""


@unit("leave", tools=["leave.balance", "leave.request"], models=["general"],
      context={"instructions": "leave"}, limits={"max_tool_calls": 4})
async def leave_unit(ctx):
    return await tool_loop(ctx, alias="general")


# --- 4. Wire the runtime ---------------------------------------------------------------------

GENERAL = ModelAlias(alias="general", targets=[
    ModelTarget(provider="vertex", model="gemini-3.1-flash-lite", region="global"),
])


def build(model_provider, *, confirm_secret: bytes):
    return build_runtime(
        app_id="leave-app",
        units=[leave_unit],
        tools=[leave_balance, leave_request],
        models=[GENERAL],
        providers={"vertex": model_provider},
        prompts={"leave": INSTRUCTIONS.format(today=date.today().isoformat())},
        confirmations=ConfirmationService(confirm_secret),
    )


# --- 5. Demo: two turns (ask -> confirm) -----------------------------------------------------


def scripted_model():
    """Plays the model's part so the demo runs without credentials."""
    from sutra.testing import ScriptedModelProvider, text_response, tool_call_response

    booking = {"kind": "vacation", "start": "2026-11-03", "end": "2026-11-05"}
    return ScriptedModelProvider([
        # turn 1: check the balance, then try to book -> harness asks the user to confirm
        tool_call_response("leave__balance"),
        tool_call_response("leave__request", booking),
        # turn 2: user confirmed; the same call now runs
        tool_call_response("leave__request", booking),
        text_response("Booked 3 vacation days (3-5 Nov). You have 9 vacation days left."),
    ])


async def demo() -> None:
    from sutra.testing import make_request, principal

    if project := os.environ.get("GOOGLE_CLOUD_PROJECT"):
        from sutra.models.google_genai import GoogleGenAIProvider
        provider = GoogleGenAIProvider(project=project)
    else:
        provider = scripted_model()

    runtime = build(provider, confirm_secret=os.urandom(32))
    # Demo identity. A real app gets the Principal from JwtAssertionVerifier (see sutra.server).
    me = principal(subject="emp-123")
    conversation = "conv-demo"

    r1 = await runtime.run(make_request("Book vacation 3-5 Nov 2026 if I have enough days",
                                        app_id="leave-app", conversation_id=conversation), me)
    print(f"turn 1: {r1.status}")
    if r1.status != "needs_input" or r1.pending_input.kind != "confirm":
        print("  ", " ".join(m.text for m in r1.messages), f"[{r1.error.code}]" if r1.error else "")
        return
    challenge = r1.pending_input.confirmation
    print(f"   confirm {challenge.tool}? (token expires {challenge.expires_at:%H:%M:%S})")

    r2 = await runtime.run(make_request("Yes, book it", app_id="leave-app", conversation_id=conversation,
                                        confirmations=(challenge.token,)), me)
    print(f"turn 2: {r2.status}: {r2.messages[0].text if r2.messages else ''}")
    print(f"   usage: {r2.usage.model_calls} model calls, {r2.usage.tool_calls} tool calls")
    print(f"   balances now: {BALANCES['emp-123']}, bookings: {BOOKINGS}")


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(demo())
