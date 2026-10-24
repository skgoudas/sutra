# Observability, Reliability and Evaluation

## 1. Telemetry contract

The contract follows the OTel GenAI semantic conventions [V]. The convention version is pinned in `sutra.telemetry` and recorded as a resource attribute. Harness-specific attributes use the `sutra.` prefix.

### Resource attributes

- `service.name`
- `service.version`
- `deployment.environment`
- `sutra.app.id`
- `sutra.bundle.sha`
- `sutra.semconv.genai.version`

### Spans

| Span | Key attributes |
|---|---|
| `sutra.run` (SERVER) | request, conversation and run IDs; channel; `sutra.auth_level`; `sutra.user.hmac`; deadline; status |
| `sutra.dispatch` | strategy, selected unit, confidence, top candidates, sticky |
| `invoke_agent {unit}` | `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name`, unit version, execution and parent IDs, budget used versus limit |
| `chat {model}` (CLIENT) | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, input / cached / output tokens, finish reasons, alias, prompt version, context tokens by kind, cost, fallback used |
| `execute_tool {tool}` | `gen_ai.tool.name`, `gen_ai.tool.call.id`, tool version, side effect, auth mode, policy decision, idempotency key hash, attempt, `error.type` |
| `sutra.context.build` | providers, tokens by kind, truncations, cache hits |
| `sutra.validate` | validator names and verdicts |
| Outbound HTTP or gRPC | Standard semantic conventions, with `traceparent` propagated |

**No prompt, response, tool argument or tool result content appears in spans.** Content goes only to the restricted interaction log, redacted.

### Metrics

Metrics use bounded labels only. User, session, run and request IDs are never metric labels.

| Metric | Labels |
|---|---|
| `sutra.run.duration`, `sutra.run.ttfb` | app, unit, status, channel |
| `sutra.dispatch.decisions` | app, strategy, unit |
| `gen_ai.client.operation.duration`, `gen_ai.client.token.usage` | alias, provider, unit |
| `sutra.tool.duration`, `sutra.tool.calls` | tool, outcome, error.type |
| `sutra.budget.exhausted` | app, unit, kind |
| `sutra.policy.decisions` | app, action, effect, reason_code |
| `sutra.breaker.state` | dependency |
| `sutra.cost.usd_micros` | app, unit, alias |

### Logs and records

- **App logs:** structured JSON with trace and span IDs, redacted.
- **Interaction log** (BigQuery, restricted dataset, column-level access): one row per run, holding:
  - versions;
  - dispatch decision;
  - redacted input and output;
  - context manifest;
  - tool outcomes;
  - validator results;
  - eval scores;
  - feedback.
- **Audit log** (locked retention): writes, denials, step-ups and confirmations.

### Sampling

- Parent-based. 100% while an app is in pilot, then a ratio sized to cost.
- Errors, denials and budget exhaustion always appear in the interaction log, regardless of trace sampling.
- Tail sampling is deferred until a gateway collector is justified.

## 2. Reliability

### Failure handling defaults

| Failure | Harness behavior |
|---|---|
| Model 5xx or timeout | One jittered retry if the deadline allows. Then the alias fallback. Then the unit's template fallback, if defined. Otherwise `degraded`. |
| Model 429 | Honor `Retry-After` only if it fits the deadline. Otherwise use the fallback alias. |
| Tool timeout (read) | One retry if remaining deadline ≥ 2× the tool's p95. Otherwise return a partial result. |
| Tool timeout (write) | **No blind retry.** Look up the outcome by idempotency key. |
| Partial dependency failure | The unit returns what it has, with unknowns marked. Status `degraded`. |
| Duplicate request | Replay the stored result, or attach to the in-flight run. |
| Cancellation | Propagate to ADK cancel, model calls and tool calls. |
| Invalid model output | One repair attempt with the validation error. Then a template, or `degraded`. |
| Loop | `LOOP_DETECTED`, then a partial result. |
| Session store down | Read-only units run statelessly (no history). Writes are refused. |
| Crash mid-run | Resumable units resume from their checkpoint; tool idempotency makes reruns safe [V]. Others: the client retries with the same `turn_id`. |

### Retry discipline

- **One retry layer per call.** HTTP clients under `ToolExecutor` do not retry on their own.
- Retry budget: at most 10% extra calls per dependency per instance [R].
- Never retry 4xx, except 408, 429 and 503.
- ADK node retries are used only on idempotent nodes. No broad `except` inside nodes, because it disables retries [V].

### Circuit breakers [R, provisional]

- One breaker per `breaker_key` (dependency or model alias).
- Opens at ≥ 50% failures over ≥ 20 calls in 10 s.
- Half-open after 15 s, with 3 probes.
- Per-instance state in V1.

### Idempotency

- **Run:** `(conversation_id, turn_id)` maps to the stored result.
- **Tool:**
  - `key = H(run_id, execution_id, tool, canonical_args)`.
  - The key is sent to APIs that support idempotency keys.
  - For APIs that don't, `ToolExecutor` keeps `pending → done` records.

## 3. Evaluation architecture

| Layer | What | When | Gate |
|---|---|---|---|
| Unit and contract tests | Units, tools, policies, context builders, redaction | CI | Must pass |
| Deterministic eval checks | <ul><li>Dispatch correctness.</li><li>Tool-call correctness (tool, args, bound args).</li><li>Answer consistent with tool facts or citations.</li><li>Forbidden content.</li><li>PII leakage.</li><li>Schema validity.</li><li>Latency and cost within budget.</li></ul> | CI | Per-unit thresholds |
| Trajectory evals | Expected tool sequences; ADK user and environment simulation [V] | CI (pinned models) + nightly | No regression beyond tolerance |
| LLM-as-judge | Only for qualities deterministic checks cannot measure (helpfulness, relevance, tone). Calibrated to ≥ 0.8 agreement with human labels before gating. | Nightly, pre-release | Advisory until calibrated |
| Safety and red-team | Injection via inputs, documents and tool text; cross-user probes | Nightly, pre-release | Zero cross-user leakage |
| Shadow | New unit versions on mirrored traffic, read-only tools only | Pre-canary | Agreement, latency |
| Canary | SLOs, online deterministic checks, sampled judges, feedback | Rollout | Automatic rollback on breach |

### Testing kit (harness-provided)

- `FakeModelClient`: scripted responses and tool calls.
- `RecordedTool`: replays captured or synthetic tool responses.
- A deterministic clock and IDs.
- A scenario runner that consumes `apps/<app>/evals/**/*.yaml`.
- A contract test kit that every unit must pass: honors the deadline, honors cancel, emits a single terminal event, uses no undeclared tools.

### From production failure to regression case

```text
signal (negative feedback, validator failure, guardrail block, escalation, low judge score)
  → triage view in the interaction log (restricted reviewers)
  → label root cause: dispatch | context | tool | model | policy | data
  → convert to synthetic case: PII replaced, tool responses recorded as fakes
  → add to apps/<app>/evals/<unit>/regression/ with an owner
  → CI runs it on every change; the fix PR must include it
```

- No raw production transcripts in repositories.
- Access to the interaction log is audited.
