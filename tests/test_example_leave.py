from __future__ import annotations

import os

import leave_assistant as app
from sutra.testing import ScriptedModelProvider, make_request, principal, text_response, tool_call_response


async def test_demo_books_after_confirmation():
    app.BALANCES["emp-123"] = {"vacation": 12, "sick": 5}
    app.BOOKINGS.clear()
    await app.demo()
    assert app.BALANCES["emp-123"]["vacation"] == 9
    assert app.BOOKINGS[0]["employee_id"] == "emp-123"


async def test_model_cannot_book_for_someone_else():
    app.BALANCES["victim"] = {"vacation": 20, "sick": 0}
    provider = ScriptedModelProvider([
        tool_call_response("leave__balance", {"employee_id": "victim"}),
        text_response("done"),
    ])
    runtime = app.build(provider, confirm_secret=os.urandom(32))
    await runtime.run(make_request("balance?", app_id="leave-app"), principal(subject="emp-999"))
    result = provider.requests[1][1].messages[-1].parts[0].result
    assert result["data"] == {"vacation_days": 0, "sick_days": 0}
