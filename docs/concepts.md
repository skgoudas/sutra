# Concepts

Sutra separates **what an agent does** (your units, tools and prompts) from **how it runs** (identity, permissions,
budgets, telemetry). This page explains the building blocks. The design documents in [design/](design/README.md)
cover the reasoning behind them.

```text
request + verified principal
        │
        ▼
     Runtime ──► Dispatcher ──► Unit ──► ModelClient ──► provider (Vertex AI, ...)
        │                        │
        │                        ├─────► ToolExecutor ──► your tool handlers
        │                        └─────► ContextBuilder ─► context providers
        │
        └── PolicyEngine, budgets, deadline, events, telemetry, audit, eval  (applied at every arrow)
```

## Unit

A unit is an agent. It has a `UnitDefinition` (id, version, allowlists, limits) and a `run(ctx)` coroutine.

- `@unit("id", tools=[...], models=[...])` turns an async function into a `FunctionUnit`.
- `sutra.engine_adk.AdkUnit` wraps a Google ADK agent or ADK 2.0 `Workflow`.
- Units return `UnitOutput` or a plain string.

**Allowlists are the permission boundary.** A unit can only call the tools, model aliases and delegate units it
lists. Registering a tool in `build_runtime` makes it *available*. Listing it on a unit makes it *usable by that
unit*. A unit can use only tools that are in both places.

## Tool

A tool is a typed async function decorated with `@tool`:

```python
@tool("leave.request", side_effect="write", requires_confirmation=True,
      bound_args={"employee_id": "principal.subject"},
      auth=ToolAuth(min_auth_level=AuthLevel.AUTHENTICATED))
async def leave_request(args: RequestArgs, call: ToolCallContext) -> Booking:
    """Book leave for the signed-in employee."""
```

| Option | Effect |
|---|---|
| `side_effect` | `"read"` or `"write"`. Policy rules can match on it. |
| `bound_args` | Arguments filled from the verified principal or the request (`principal.subject`, `principal.tenant`, `request.locale`, ...). They are removed from the schema the model sees, so the model cannot set them. |
| `requires_confirmation` | The first call returns a confirmation challenge instead of running. The user's client sends the token back in `AgentRequest.confirmations`. The token is HMAC-bound to the principal, tool and exact arguments, expires, and can be used once. |
| `auth` | Minimum auth level, credential mode (`none`, `service`, `delegated`, `api_key`), audience and scopes. |
| `timeout_ms`, `retry`, `breaker_key` | Per-call timeout, retry on retryable errors, circuit breaker. |
| `result_projection`, `max_result_chars` | Limit which result fields, and how much text, the model sees. |
| `data_classification` | `public`, `internal`, `confidential` or `restricted`. Used by policy and context. |

Handlers receive a `ToolCallContext` (run id, attempt, timeout, locale, idempotency key, brokered credential). It does
not include the raw principal.

## Models

Units refer to **model aliases**, not model names. A `ModelAlias` has one or more `ModelTarget`s that are tried in
order (fallback). `ModelClient` enforces the unit's model-call, token and cost budgets and records `gen_ai.*` usage.
Providers implement one method, `generate(target, request)`. `sutra.models.google_genai.GoogleGenAIProvider` covers
Gemini on Vertex AI or the Gemini API.

## Context

`ContextBuilder` assembles what the model sees: the system instructions (a prompt id), conversation history (bounded
by `history_turns`) and items from context providers. It authorizes each item, fences untrusted content so the model
can tell it apart from instructions, and fits everything into `total_input_tokens`.

## Identity

The runtime takes a `Principal` that the caller has verified. The request body is never trusted for identity.
`JwtAssertionVerifier` checks a JWT from your trusted front door: algorithms are pinned, and `iss`, `aud`, `exp`,
`iat` and `sub` are required. `SubjectHasher` turns the subject into a keyed HMAC (`subject_hmac`), which is used for
storage keys, logs and telemetry. Auth levels are `anonymous` < `verified` < `authenticated` < `step_up`.

## Policy

`PolicyEngine` makes synchronous, in-process decisions for `run.start`, `dispatch`, `delegate`, `tool.invoke`,
`model.invoke`, `context.include` and `response.emit`. Decision order:

1. The resource's minimum auth level.
2. Declarative rules; the first match wins. Effects are `allow`, `deny`, `require_auth` or `require_confirmation`,
   with optional obligations.
3. Built-in confirmation for resources marked `requires_confirmation`.

```yaml
version: base-1
rules:
  - id: no-writes-on-kiosk
    actions: [tool.invoke]
    match:
      resource.side_effect: write
      context.channel: kiosk
    effect: deny
    reason_code: channel_read_only
```

Rules only add restrictions. Allowlists already deny anything not listed.

## Runtime and limits

`Runtime.run(request, principal)` returns an `AgentResult`. It handles dispatch, the per-conversation lock,
idempotency, the deadline, cancellation and budgets. Each unit has `ExecutionLimits`:

| Limit | Default |
|---|---|
| `max_steps` | 8 |
| `max_model_calls` | 6 |
| `max_tool_calls` | 10 |
| `max_identical_tool_calls` | 2 |
| `max_delegation_depth` | 1 |
| `max_input_tokens` / `max_output_tokens` | 60000 / 4000 |
| `max_cost_usd_micros` | 50000 ($0.05) |
| `timeout_ms` | 20000 |

`AgentResult.status` is one of `completed`, `needs_input` (for example a pending confirmation), `degraded`,
`escalated`, `failed` or `cancelled`. Failures carry a typed `ErrorCode`. `AgentResult.versions` records the bundle
hash and unit versions that produced the result.

## Dispatch

With several units, the `Dispatcher` picks one per turn: `FixedDispatch` (always the same unit), `StickyDispatch`
(return to the unit that asked the user for input on the previous turn) or `RulesDispatch` (deterministic rules on a
regex over the input text and on client hints). Client hints are untrusted. They select a unit but never grant access.

## Events

During a run, the runtime emits `RunEvent`s: `run.started`, `progress`, `message.delta`, `input.required`,
`run.completed` and `run.failed`. The HTTP server streams them as server-sent events.

## Telemetry, audit and evaluation

- **Telemetry**: spans `sutra.run`, `sutra.unit`, `sutra.model`, `sutra.tool`, `sutra.context` and `sutra.delegate`,
  plus counters and histograms for runs, tool calls, model calls, latency and cost. Only scalar `sutra.*` and
  `gen_ai.*` attributes pass the allowlist. Content, arguments, raw subjects and exception messages are never emitted.
  Use `OTelTelemetry` (the `otel` extra) in production and `InMemoryTelemetry(strict=True)` in tests.
- **Audit**: tool invocations are recorded to an `AuditSink` (`LoggingAuditSink` by default).
- **Evaluation**: online validators and an `EvalSink` receive redacted `EvalRecord`s for offline scoring.

## Configuration bundles

Instead of wiring everything in Python, an app can be a directory of YAML (`app.yaml`, `models.yaml`, `units/`,
`policies/`, `prompts/`) loaded with `sutra.config.load_bundle` and `runtime_from_bundle`. The bundle hash is recorded
in every result's `versions` and returned by `/readyz`. See [examples/hello_app](../examples/hello_app).
