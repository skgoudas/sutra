"""Example tools for the hello app. Real apps call their own services here."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sutra import AuthLevel, ToolAuth, ToolCallContext, tool

FAQ = {
    "hours": "Support is available 8am-8pm, Monday to Saturday.",
    "returns": "Most items can be returned within 15 days with a receipt.",
    "shipping": "Standard shipping takes 3-5 business days.",
}


class SearchArgs(BaseModel):
    query: str = Field(max_length=200, description="What the user is asking about")


class SearchResult(BaseModel):
    answers: list[str]


@tool("kb.search", timeout_ms=1000)
async def kb_search(args: SearchArgs, call: ToolCallContext) -> SearchResult:
    """Search the help-center knowledge base. Returns short factual answers."""
    q = args.query.lower()
    return SearchResult(answers=[v for k, v in FAQ.items() if k in q] or ["No matching article."])


class TicketArgs(BaseModel):
    user_id: str  # bound from the verified principal; never visible to the model
    summary: str = Field(max_length=500)


class TicketResult(BaseModel):
    ticket_id: str
    status: str


TICKETS: dict[str, TicketArgs] = {}


@tool(
    "ticket.create",
    side_effect="write",
    requires_confirmation=True,
    bound_args={"user_id": "principal.subject"},
    auth=ToolAuth(min_auth_level=AuthLevel.AUTHENTICATED),
    result_projection=frozenset({"ticket_id", "status"}),
)
async def ticket_create(args: TicketArgs, call: ToolCallContext) -> TicketResult:
    """Open a support ticket for the signed-in user."""
    ticket_id = f"T-{len(TICKETS) + 1:04d}"
    TICKETS[ticket_id] = args
    return TicketResult(ticket_id=ticket_id, status="open")


TOOLS = [kb_search, ticket_create]
