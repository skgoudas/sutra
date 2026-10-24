"""HTTP channel adapter (FastAPI). Requires the ``server`` extra.

POST /v1/runs   Authorization: Bearer <principal assertion>
                Accept: text/event-stream -> SSE RunEvents, otherwise JSON AgentResult.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sutra.contracts.common import LocaleTag, SafeId, new_ulid
from sutra.contracts.errors import ErrorCode, SutraError
from sutra.contracts.request import AgentRequest, HintValue, UserInput
from sutra.identity.verifier import AssertionVerifier
from sutra.runtime.runtime import Runtime

_log = logging.getLogger("sutra.server")


class RunEnvelope(BaseModel):
    """Client-supplied fields only. Identity, app id and request id are set server-side."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: SafeId
    turn_id: SafeId
    input: UserInput
    channel: SafeId = "api"
    locale: LocaleTag = "en-US"
    client_hints: dict[str, HintValue] = Field(default_factory=dict)
    confirmations: tuple[str, ...] = ()
    deadline_ms: int | None = Field(default=None, ge=100)


def _error(status: int, code: ErrorCode, **extra: object) -> JSONResponse:
    body = {"error": SutraError(code).to_info().model_dump(mode="json"), **extra}
    return JSONResponse(body, status_code=status)


def create_app(runtime: Runtime, verifier: AssertionVerifier, *, max_body_bytes: int = 64 * 1024) -> FastAPI:
    app = FastAPI(title=f"sutra:{runtime.settings.app_id}", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ready", "bundle": runtime.settings.bundle_hash}

    @app.post("/v1/runs")
    async def create_run(request: Request):  # noqa: ANN202
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return _error(401, ErrorCode.UNAUTHENTICATED)
        try:
            principal = verifier.verify(token.strip())
        except SutraError:
            return _error(401, ErrorCode.UNAUTHENTICATED)

        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > max_body_bytes):
            return _error(413, ErrorCode.INVALID_REQUEST)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > max_body_bytes:
                return _error(413, ErrorCode.INVALID_REQUEST)
        try:
            envelope = RunEnvelope.model_validate_json(bytes(body))
        except ValidationError as exc:
            fields = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})
            return _error(422, ErrorCode.INVALID_REQUEST, invalid_fields=fields)

        max_ms = runtime.settings.max_deadline_ms
        deadline_ms = min(envelope.deadline_ms or max_ms, max_ms)
        agent_request = AgentRequest(
            request_id=new_ulid(),
            app_id=runtime.settings.app_id,
            deadline=datetime.now(UTC) + timedelta(milliseconds=deadline_ms),
            **envelope.model_dump(exclude={"deadline_ms"}),
        )

        if "text/event-stream" in request.headers.get("accept", ""):
            return StreamingResponse(
                _sse(runtime, agent_request, principal),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )
        result = await runtime.run(agent_request, principal)
        return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "no-store"})

    return app


async def _sse(runtime: Runtime, request: AgentRequest, principal) -> AsyncIterator[bytes]:  # noqa: ANN001
    events = runtime.submit(request, principal)
    try:
        async for event in events:
            data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
            yield f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n".encode()
    finally:
        await events.aclose()  # client disconnect -> cancels the run
