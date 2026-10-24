"""IT help desk: several agents wired as an ADK 2.0 workflow graph, run inside Sutra.

                         ┌─ question ─► researcher (LLM + kb.search) ─┐
    START ─► triage ─► route                                          ├─► join ─► writer (LLM)
           (LLM, typed)  │            check_status (code, service.status) ┘
                         ├─ incident ─► open_ticket (code, ticket.create, needs confirmation)
                         └─ other ────► hand_off (code, canned reply)

* ``triage`` returns a typed ``Triage`` object (structured output); ``route`` branches on it.
* ``researcher`` and ``check_status`` run in parallel; ``join`` merges their outputs for ``writer``.
* Function nodes call Sutra tools with ``call_tool``, so code-driven calls get the same
  allowlist, policy, identity binding and confirmation checks as model-driven ones.
* Every model call goes through Sutra model aliases (budget, fallback, cost, telemetry).

Run offline (stand-in model):        python examples/it_helpdesk_workflow.py
Run against Gemini on Vertex AI:     GOOGLE_CLOUD_PROJECT=<project> python examples/it_helpdesk_workflow.py
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.workflow import START, JoinNode, Workflow, node
from google.genai import types
from pydantic import BaseModel, Field

from sutra import AuthLevel, ToolAuth, ToolCallContext, UnitDefinition, build_runtime, tool
from sutra.contracts import ModelAlias, ModelTarget
from sutra.engine_adk import AdkUnit, SutraAdkModel, adk_tools, call_tool
from sutra.tools import ConfirmationService

# --- 1. Your systems (stand-ins) -------------------------------------------------------------

ARTICLES = {
    "KB-101": ("VPN keeps disconnecting",
               "Update the GlobalProtect client, switch the portal to vpn2.corp.example, "
               "and turn off Wi-Fi power saving."),
    "KB-102": ("Wi-Fi in the office", "Join CORP-SECURE with your network login; forget CORP-GUEST first."),
    "KB-103": ("Outlook won't sync", "Restart Outlook in safe mode and rebuild the OST profile."),
}
SERVICES = {"vpn": "degraded", "email": "operational", "wifi": "operational"}
TICKETS: list[dict] = []

# --- 2. Tools ---------------------------------------------------------------------------------


class SearchArgs(BaseModel):
    query: str = Field(description="A few keywords describing the problem")


class Article(BaseModel):
    id: str
    title: str
    steps: str


class SearchResult(BaseModel):
    articles: list[Article]


@tool("kb.search")
async def kb_search(args: SearchArgs, call: ToolCallContext) -> SearchResult:
    """Search the IT knowledge base for troubleshooting articles."""
    words = {w.strip(".,?!").lower() for w in args.query.split()}
    hits = [Article(id=k, title=t, steps=s) for k, (t, s) in ARTICLES.items()
            if words & {w.strip(".,").lower() for w in f"{t} {s}".split()}]
    return SearchResult(articles=hits[:2])


class StatusArgs(BaseModel):
    service: Literal["vpn", "email", "wifi"]


class ServiceStatus(BaseModel):
    service: str
    status: Literal["operational", "degraded", "down"]


@tool("service.status")
async def service_status(args: StatusArgs, call: ToolCallContext) -> ServiceStatus:
    """Current health of a corporate IT service."""
    return ServiceStatus(service=args.service, status=SERVICES[args.service])


class TicketArgs(BaseModel):
    employee_id: str  # bound from the principal; never chosen by a model
    device: Literal["laptop", "phone", "monitor"]
    urgency: Literal["low", "normal", "high"]


class Ticket(BaseModel):
    ticket_id: str
    sla_hours: int


@tool("ticket.create", side_effect="write", requires_confirmation=True,
      bound_args={"employee_id": "principal.subject"},
      auth=ToolAuth(min_auth_level=AuthLevel.AUTHENTICATED))
async def ticket_create(args: TicketArgs, call: ToolCallContext) -> Ticket:
    """Open a hardware repair ticket for the signed-in employee."""
    TICKETS.append(args.model_dump())
    return Ticket(ticket_id=f"INC-{len(TICKETS):05d}", sla_hours=4 if args.urgency == "high" else 24)


# --- 3. The workflow ---------------------------------------------------------------------------


class Triage(BaseModel):
    category: Literal["question", "incident", "other"] = Field(
        description="question: how-to or connectivity problem; incident: broken hardware; other: anything else")
    query: str = Field(description="Short knowledge-base search query")
    service: Literal["vpn", "email", "wifi", "none"]
    device: Literal["laptop", "phone", "monitor", "none"]
    urgency: Literal["low", "normal", "high"]


@node
async def route(node_input: Triage):
    yield Event(route=node_input.category, output=node_input)


async def check_status(node_input: Triage) -> dict:
    status = "not applicable"
    if node_input.service != "none":
        result = await call_tool("service.status", {"service": node_input.service})
        status = result.data["status"] if result.ok else "unknown"
    return {"question": node_input.query, "service": node_input.service, "status": status}


async def open_ticket(node_input: Triage) -> str | None:
    device = node_input.device if node_input.device != "none" else "laptop"
    result = await call_tool("ticket.create", {"device": device, "urgency": node_input.urgency})
    if result.challenge:
        return None  # Sutra stops the run and asks the user to confirm
    if not result.ok:
        return f"Sorry, I couldn't open a ticket ({result.error.code})."
    t = result.data
    return (f"Opened ticket {t['ticket_id']} for your {device} ({node_input.urgency} priority). "
            f"A technician will contact you within {t['sla_hours']} hours.")


def hand_off(node_input: Triage) -> str:
    return "That's outside what I can help with here. Please call the service desk on extension 4357."


TRIAGE = """You triage messages sent to the IT service desk.
Classify the employee's latest message, using the conversation so far for context."""

RESEARCHER = """You are an IT knowledge-base researcher. The input is a triage record.
Search with kb.search, then list the relevant fix steps as short bullets with the article id.
Do not address the employee."""

WRITER = """You are the IT service desk. The input holds research notes and the service status.
Write a friendly reply to the employee in at most four sentences.
If the service is degraded, say so first. Mention article ids."""


def build_workflow() -> Workflow:
    triage = LlmAgent(name="triage", model=SutraAdkModel(model="fast"), instruction=TRIAGE,
                      output_schema=Triage,
                      generate_content_config=types.GenerateContentConfig(temperature=0))
    researcher = LlmAgent(name="researcher", model=SutraAdkModel(model="fast"), instruction=RESEARCHER,
                          tools=adk_tools([kb_search]))
    writer = LlmAgent(name="writer", model=SutraAdkModel(model="general"), instruction=WRITER)
    join = JoinNode(name="join")
    return Workflow(name="it_desk", edges=[
        (START, triage, route, {"question": (researcher, check_status),
                                "incident": open_ticket,
                                "other": hand_off}),
        ((researcher, check_status), join, writer),
    ])


# --- 4. Wire the runtime ----------------------------------------------------------------------

UNIT = UnitDefinition(
    id="it-desk", engine="adk",
    tools=["kb.search", "service.status", "ticket.create"],  # allowlist for every node
    models=["fast", "general"],
    limits={"max_model_calls": 6, "max_tool_calls": 4},
)
FAST = ModelAlias(alias="fast", targets=[
    ModelTarget(provider="vertex", model="gemini-3.1-flash-lite", region="global")])
GENERAL = ModelAlias(alias="general", targets=[  # swap in a larger model for the writer if needed
    ModelTarget(provider="vertex", model="gemini-3.1-flash-lite", region="global")])


def build(model_provider, *, confirm_secret: bytes, telemetry=None):
    return build_runtime(
        app_id="it-desk-app",
        units=[AdkUnit(UNIT, build_workflow())],
        tools=[kb_search, service_status, ticket_create],
        models=[FAST, GENERAL],
        providers={"vertex": model_provider},
        confirmations=ConfirmationService(confirm_secret),
        telemetry=telemetry,
    )


# --- 5. Demo ----------------------------------------------------------------------------------


class OfflineModel:
    """Plays every agent's part so the demo runs without credentials (answers by role)."""

    async def generate(self, target, request):
        from sutra.testing import text_response, tool_call_response

        system, last = request.system or "", request.messages[-1]
        if system.startswith("You triage"):
            text = request.messages[0].text.lower()
            if "screen" in text or "broken" in text:
                t = Triage(category="incident", query="cracked laptop screen", service="none",
                           device="laptop", urgency="high")
            elif "vpn" in text:
                t = Triage(category="question", query="VPN disconnecting", service="vpn",
                           device="none", urgency="normal")
            else:
                t = Triage(category="other", query="", service="none", device="none", urgency="low")
            return text_response(t.model_dump_json())
        if system.startswith("You are an IT knowledge-base"):
            if last.role != "tool":
                return tool_call_response("kb__search", {"query": "VPN disconnecting"})
            return text_response("- KB-101: update GlobalProtect, use vpn2.corp.example, disable Wi-Fi power saving")
        return text_response("Heads-up: the VPN is currently degraded. Meanwhile, update GlobalProtect, "
                             "switch the portal to vpn2.corp.example and turn off Wi-Fi power saving (KB-101).")


async def ask(runtime, me, text: str, conversation: str, confirmations: tuple[str, ...] = ()):
    from sutra.testing import make_request

    request = make_request(text, app_id="it-desk-app", conversation_id=conversation,
                           confirmations=confirmations)
    path, result = [], None
    async for event in runtime.submit(request, me):
        if event.type == "progress" and event.payload.get("stage") == "node":
            path.append(event.payload["node"])
        elif "result" in event.payload:  # terminal event
            result = event.payload["result"]
    print(f"\n> {text}")
    print(f"  path:   {' -> '.join(dict.fromkeys(path))}")
    print(f"  status: {result['status']}  (model calls {result['usage']['model_calls']}, "
          f"tool calls {result['usage']['tool_calls']})")
    for m in result["messages"]:
        print(f"  reply:  {m['text']}")
    if result.get("error"):
        print(f"  error:  {json.dumps(result['error'])}")
    return result


async def demo() -> None:
    from sutra.testing import principal

    if project := os.environ.get("GOOGLE_CLOUD_PROJECT"):
        from sutra.models.google_genai import GoogleGenAIProvider
        provider = GoogleGenAIProvider(project=project)
    else:
        provider = OfflineModel()

    runtime = build(provider, confirm_secret=os.urandom(32))
    me = principal(subject="emp-123")

    # 1) question: parallel research + status check, merged by the writer
    await ask(runtime, me, "My VPN keeps disconnecting when I work from home. How do I fix it?", "conv-vpn")

    # 2) incident: the ticket tool needs confirmation, so the run pauses ...
    r = await ask(runtime, me, "My laptop screen is cracked and I can't work.", "conv-hw")
    pending = r.get("pending_input") or {}
    if pending.get("kind") != "confirm":
        return
    challenge = pending["confirmation"]
    print(f"  confirm: {challenge['tool']} (expires {challenge['expires_at']})")
    # ... and resumes once the user confirms with the token
    await ask(runtime, me, "Yes, please open the ticket.", "conv-hw", (challenge["token"],))
    print(f"\n  tickets: {TICKETS}")

    # 3) out of scope: handled by a plain function node, no extra model calls
    await ask(runtime, me, "Can you book me a meeting room?", "conv-other")


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(demo())
