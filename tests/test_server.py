from __future__ import annotations

import json
import os
import time

import httpx
import jwt
import pytest

from sutra import unit
from sutra.identity import JwtAssertionVerifier, SubjectHasher
from sutra.server import create_app
from sutra.testing import build_test_runtime

KEY = os.urandom(32)


@unit("echo")
async def echo(ctx):
    ctx.events.progress("working")
    return f"echo: {ctx.input_text}"


@pytest.fixture
def client():
    runtime, _, _ = build_test_runtime([echo])
    verifier = JwtAssertionVerifier(audience="test-app", issuer="fd", algorithms=["HS256"], key=KEY,
                                    hasher=SubjectHasher(KEY))
    app = create_app(runtime, verifier, max_body_bytes=2048)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _auth():
    now = int(time.time())
    token = jwt.encode({"sub": "u1", "aud": "test-app", "iss": "fd", "iat": now, "exp": now + 60,
                        "auth_level": "authenticated"}, KEY, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


BODY = {"conversation_id": "c1", "turn_id": "t1", "input": {"text": "hi"}}


async def test_json_run(client):
    r = await client.post("/v1/runs", json=BODY, headers=_auth())
    assert r.status_code == 200
    assert r.json()["messages"][0]["text"] == "echo: hi"


async def test_sse_run(client):
    r = await client.post("/v1/runs", json=BODY | {"turn_id": "t2"},
                          headers=_auth() | {"Accept": "text/event-stream"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    assert [e["type"] for e in events][0] == "run.started"
    assert events[-1]["type"] == "run.completed"


async def test_missing_or_bad_assertion_is_401(client):
    assert (await client.post("/v1/runs", json=BODY)).status_code == 401
    r = await client.post("/v1/runs", json=BODY, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and "nope" not in r.text


async def test_oversized_body_is_413(client):
    big = BODY | {"input": {"text": "x" * 4000}}
    assert (await client.post("/v1/runs", json=big, headers=_auth())).status_code == 413


async def test_invalid_body_is_422_without_echo(client):
    r = await client.post("/v1/runs", json=BODY | {"app_id": "spoofed", "secret": "s3cr3t"}, headers=_auth())
    assert r.status_code == 422
    assert "s3cr3t" not in r.text
    assert set(r.json()["invalid_fields"]) >= {"app_id", "secret"}


async def test_health(client):
    assert (await client.get("/healthz")).status_code == 200
