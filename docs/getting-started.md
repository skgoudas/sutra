# Getting started

This guide builds a one-tool agent, runs it offline with a scripted model, and then points it at a real model.

## 1. Install

Sutra needs Python 3.12 or newer. The core package depends only on `pydantic`. Integrations are optional extras.

```sh
pip install -e ".[dev]"            # from a clone: everything, plus test tools
pip install -e ".[vertex]"         # core + Google GenAI / Vertex AI provider
```

| Extra | Adds |
|---|---|
| `vertex` | `google-genai` model provider |
| `adk` | Google ADK 2.0 engine adapter |
| `server` | FastAPI / uvicorn HTTP adapter |
| `jwt` | JWT principal verification |
| `otel` | OpenTelemetry telemetry backend |
| `yaml` | YAML config bundles |
| `all` | All of the above |

## 2. Write a tool and a unit

A **tool** is a typed async function. Its first parameter is a pydantic model, and its docstring becomes the
description the model sees. A **unit** is an agent. It declares which tools and model aliases it may use, and the
runtime denies everything else.

```python
import asyncio

from pydantic import BaseModel

from sutra import tool, tool_loop, unit
from sutra.testing import build_test_runtime, make_request, principal, text_response, tool_call_response


class LookupArgs(BaseModel):
    city: str


class Weather(BaseModel):
    summary: str


@tool("weather.lookup")
async def weather_lookup(args: LookupArgs) -> Weather:
    """Look up today's weather for a city."""
    return Weather(summary=f"Sunny in {args.city}")


@unit("weather", tools=["weather.lookup"], models=["default"])
async def weather(ctx):
    """Answers weather questions."""
    return await tool_loop(ctx, alias="default")
```

`tool_loop` is a small model-and-tool loop. Every model call goes through `ModelClient` and every tool call through
`ToolExecutor`, so the unit's allowlists, budgets and deadline apply. You can also write the unit body yourself with
`ctx.models.generate(...)` and `ctx.tools.invoke(...)`, or use an ADK agent through `sutra.engine_adk.AdkUnit`.

## 3. Run it offline

`sutra.testing` gives you a runtime with a scripted model, so you need no credentials:

```python
async def main():
    runtime, model, telemetry = build_test_runtime([weather], tools=[weather_lookup])
    model.push(
        tool_call_response("weather.lookup", {"city": "Lisbon"}),
        text_response("It is sunny in Lisbon."),
    )
    result = await runtime.run(make_request("Weather in Lisbon?"), principal())
    print(result.status, result.messages[0].text, result.messages[0].citations)
    print(sorted({s.name for s in telemetry.spans}))


asyncio.run(main())
```

Output:

```text
completed It is sunny in Lisbon. [Citation(source='weather.lookup@1.0.0', ...)]
['sutra.context', 'sutra.model', 'sutra.run', 'sutra.tool', 'sutra.unit']
```

Write your unit tests the same way.

## 4. Use a real model

Model aliases decouple units from concrete models. Register an alias and a provider with `build_runtime`:

```python
from sutra import ModelAlias, ModelTarget, build_runtime
from sutra.models.google_genai import GoogleGenAIProvider

general = ModelAlias(alias="default", targets=[
    ModelTarget(provider="vertex", model="gemini-3.1-flash-lite", region="global"),
])

runtime = build_runtime(
    app_id="weather-app",
    units=[weather],
    tools=[weather_lookup],
    models=[general],
    providers={"vertex": GoogleGenAIProvider(project="my-gcp-project")},
)
```

Authenticate with `gcloud auth application-default login` before you run it locally. Add more `ModelTarget`s to an
alias to get ordered fallback.

## 5. Serve it over HTTP

```python
import os

from sutra.identity import JwtAssertionVerifier, SubjectHasher
from sutra.server import create_app

verifier = JwtAssertionVerifier(
    audience="weather-app",
    issuer="https://issuer.example.com",
    jwks_url="https://issuer.example.com/.well-known/jwks.json",
    hasher=SubjectHasher(os.environ["SUTRA_SUBJECT_HASH_KEY"].encode()),  # 32+ bytes
)
app = create_app(runtime, verifier)  # POST /v1/runs (JSON or SSE), /healthz, /readyz
```

The server never trusts identity from the request body. Every request needs a bearer JWT, which the
`JwtAssertionVerifier` checks. See [examples/it_helpdesk_server.py](../examples/it_helpdesk_server.py) for a complete
entry point and [deploy/it-helpdesk](../deploy/it-helpdesk/README.md) for a container deployment.

## Next steps

- [Concepts](concepts.md): identity-bound tool arguments, confirmations, policy, context and telemetry.
- [examples/leave_assistant.py](../examples/leave_assistant.py): a confirmed write tool, end to end.
- [Production checklist](production-checklist.md).
