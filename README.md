# Sutra

[![CI](https://github.com/skgoudas/sutra/actions/workflows/ci.yml/badge.svg)](https://github.com/skgoudas/sutra/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](pyproject.toml)

**A thin, secure harness for running LLM agents in production.**

Sutra does not decide *what* your agent does. It guarantees *how* any agent runs: with a verified identity, with only
the tools and models it is allowed to use, within deadlines and budgets, with consistent telemetry, and from
versioned configuration. Bring your prompts, tools and agents (plain Python or Google ADK). Sutra handles the rest.

> **Status: 0.1 (alpha).** The minimum viable harness is implemented and tested. Session, idempotency, lock and
> confirmation stores are in-memory, so run a single instance or provide shared stores. See the
> [production checklist](docs/production-checklist.md).

## Why Sutra

- **Deny by default, enforced in the runtime.** A unit (agent) can only call the tools, model aliases and delegates
  it lists. Safety never depends on the model behaving.
- **Identity the model can't touch.** Identity comes from a verified JWT. `bound_args` inject it into tool calls and
  hide those fields from the model.
- **Confirmed writes.** Side-effecting tools can require an HMAC-bound, expiring, single-use confirmation tied to the
  exact arguments.
- **Budgets and deadlines.** Steps, model calls, tool calls, tokens, cost and wall-clock time are enforced for every
  unit.
- **Safe telemetry.** OpenTelemetry spans and metrics with an attribute allowlist. No prompts, responses, arguments
  or raw user ids.
- **Framework behind an adapter.** Write units as plain async functions or run Google ADK 2.0 agents and workflows.
  Framework types stay out of the contracts.
- **Small core.** The core depends only on `pydantic`. Everything else is an optional extra.

## Install

Sutra needs Python 3.12 or newer.

```sh
git clone https://github.com/skgoudas/sutra.git && cd sutra
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q
```

Extras: `vertex` (Gemini / Vertex AI), `adk` (Google ADK), `server` (FastAPI), `jwt`, `otel`, `yaml`, `all`, `dev`.

## Quick start

```python
from pydantic import BaseModel
from sutra import tool, tool_loop, unit

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

Run it offline with a scripted model, then against Gemini, in [Getting started](docs/getting-started.md).

## How it fits together

```text
request + verified principal
        │
        ▼
     Runtime ──► Dispatcher ──► Unit ──► ModelClient ──► model provider
        │                        ├─────► ToolExecutor ──► your tools
        │                        └─────► ContextBuilder ─► context providers
        └── PolicyEngine, budgets, deadline, events, telemetry, audit, eval
```

Each concern has exactly one choke point, so policy, budgets and telemetry apply no matter how a unit is written.
See [Concepts](docs/concepts.md).

## Package map

| Module | Responsibility |
|---|---|
| `sutra.contracts` | Versioned pydantic contracts (request, result, events, units, tools, models, context, policy, errors) |
| `sutra.runtime` | `Runtime` lifecycle, `ExecutionContext`, deadline / cancellation / budgets, stores |
| `sutra.units` | `@unit`, `FunctionUnit`, `tool_loop` |
| `sutra.dispatch` | Fixed, sticky and rules dispatch |
| `sutra.tools` | `@tool`, `ToolExecutor` (PEP), breakers, credentials, confirmation, audit |
| `sutra.models` | `ModelClient` (aliases, fallback, budgets, cost), Gemini/Vertex provider |
| `sutra.context` | `ContextBuilder` (authorize, fence untrusted data, budget, assemble) |
| `sutra.policy` | Synchronous in-process `PolicyEngine` |
| `sutra.identity` | JWT principal assertion verifier, subject hashing |
| `sutra.telemetry` | Telemetry facade with attribute allowlist; OTel backend |
| `sutra.eval` | Online validators, redaction, interaction records |
| `sutra.config` | Config bundle loader -> Runtime |
| `sutra.engine_adk` | Google ADK adapter (`AdkUnit`, `SutraAdkModel`, `SutraAdkTool`) |
| `sutra.server` | FastAPI channel adapter (`POST /v1/runs`, JSON or SSE) |
| `sutra.testing` | Scripted model provider and test factories |

## Examples

| Example | Shows |
|---|---|
| [examples/leave_assistant.py](examples/leave_assistant.py) | Smallest end-to-end app: read tool, confirmed write tool, identity-bound args. Runs offline: `.venv/bin/python examples/leave_assistant.py`. |
| [examples/hello_app](examples/hello_app) | YAML config bundle with a policy rule, a plain-Python unit and an ADK unit. |
| [examples/it_helpdesk_workflow.py](examples/it_helpdesk_workflow.py) | Multi-agent ADK 2.0 `Workflow` (triage, routing, parallel research, join, confirmed ticket branch) as a Sutra `AdkUnit`. |
| [examples/it_helpdesk_server.py](examples/it_helpdesk_server.py) | The workflow served over HTTP with JWT auth and OpenTelemetry; deploys with [deploy/it-helpdesk](deploy/it-helpdesk/README.md). |

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Install, first unit, offline run, real model, HTTP server |
| [Concepts](docs/concepts.md) | Units, tools, models, context, identity, policy, runtime, telemetry |
| [Production checklist](docs/production-checklist.md) | Known limitations and pre-launch checks |
| [Design documents](docs/design/README.md) | Architecture, contracts, security model, roadmap |
| [Vision](VISION.md) | Principles and non-goals |

## Security

Sutra treats the model as untrusted. Authorization, identity binding, confirmation and budgets are enforced in the
runtime. To report a vulnerability, follow [SECURITY.md](SECURITY.md). Do not open a public issue.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).
Coding agents should also read [AGENTS.md](AGENTS.md). Release notes are in [CHANGELOG.md](CHANGELOG.md).

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
