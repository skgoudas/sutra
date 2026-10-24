# Runtime Lifecycle and Execution

## 1. Run lifecycle

| Phase | What happens | Fails |
|---|---|---|
| **1. Accept** | <ul><li>Parse the envelope into `AgentRequest` (strict schema, size caps).</li><li>Verify the principal assertion.</li><li>Clamp the deadline to the app maximum.</li><li>Request idempotency: replay the stored result if one exists, or attach to the in-flight run.</li><li>Acquire the per-conversation run lock (one active run per conversation).</li></ul> | `INVALID_REQUEST`, `UNAUTHENTICATED`, 409 if locked |
| **2. Dispatch** | <ul><li>Pick the entrypoint unit using the app's dispatch chain.</li><li>Check `min_auth_level` and kill switches.</li></ul> | `input.required{auth}`, configured fallback unit |
| **3. Execute** | <ul><li>Build the `ExecutionContext` (principal, budget, deadline, scoped tools and models).</li><li>Call `Unit.run`.</li><li>Stream `RunEvent`s.</li></ul> | Per the error taxonomy |
| **4. Validate** | Run synchronous validators on the result (schema, citation presence, forbidden content, app-specific checks). A failure means one repair attempt or a template fallback. | `degraded` |
| **5. Finalize** | <ul><li>Persist `AgentResult` for replay.</li><li>Append session events.</li><li>Emit the terminal event.</li><li>Release the lock.</li><li>Close spans.</li><li>Enqueue the sampled `EvalRecord`.</li></ul> | Persistence failure is logged; the user still gets the result |

**Invariants:**

- Exactly one terminal event per run.
- Every phase has a span.
- Budget and deadline are checked before every model call and every tool call.
- Cancellation (client disconnect or an explicit cancel) propagates to ADK cancel, in-flight model calls and tool calls.

## 2. Budgets and deadlines

| Budget | Default source | Enforced by |
|---|---|---|
| Wall clock | `min(request.deadline, unit.limits.deadline_ms)` | `Deadline`; child calls get `deadline.child(max_ms)` |
| Steps, model calls, tool calls | `ExecutionLimits` | `BudgetMeter` + ADK budget plugin |
| Tokens and cost | `ExecutionLimits` | `ModelClient` pre-check (estimated) and post-charge (actual) |
| Delegations and depth | `ExecutionLimits` | `ExecutionContext.for_child` |
| Retries | `max_retries_total` per run + per-tool `RetryPolicy` | `ToolExecutor`, `ModelClient` |
| Loop guard | Same tool with the same arguments 2 times or more, or the same model output twice | Budget plugin, which raises `LOOP_DETECTED` |

On exhaustion, the unit receives an exception it may catch **once** to produce a partial result. The runtime enforces the hard stop.

## 3. Execution patterns the harness supports

| Pattern | When to use | Harness support | Cost and complexity |
|---|---|---|---|
| **Single LLM agent with tools** | Open-ended Q&A or assistance over a few tools | `AdkUnit` wrapping an ADK LLM agent | Lowest to build |
| **Deterministic workflow with LLM steps** | Known procedures where correctness matters | ADK 2.0 graph workflow, or a `FunctionUnit` | Most predictable and testable |
| **Dispatcher + several units** | Several distinct task types in one app | Dispatch chain (see §4) | Moderate |
| **Bounded delegation (planner)** | Multi-part requests needing several units | `delegates` allowlist; `max_delegations`, `max_delegation_depth`; `for_child` narrowing | Higher. Opt-in per unit. |
| **Remote unit** | Isolation, a different team, or different scaling | `RemoteUnit` (typed RPC) | Network hop |
| **External agent** | Another org or vendor, or a non-Sutra stack | `A2AUnit`, or an A2A server adapter | Protocol and identity overhead |
| **Async job** (later) | Work longer than the interactive deadline | Pub/Sub + worker + resumable checkpoints | Deferred |

**Default recommendation:** start with a single agent or a deterministic workflow behind `fixed` dispatch. Add the dispatcher and delegation only when an app has more than one task type.

## 4. Dispatcher

The dispatcher is **optional and pluggable**. An app configures an ordered chain. Each strategy either returns a decision or abstains.

| Strategy | Latency | Use |
|---|---|---|
| `fixed` | ~0 | A single-unit app |
| `sticky` | ~0 | Follow-up turns go to the unit awaiting input or the last unit. The unit may reject with `not_mine`. |
| `rules` | < 1 ms | Explicit UI actions, structured commands, high-precision patterns |
| `classifier` | 5–30 ms [A] | A trained model on the app's taxonomy, with calibrated confidence |
| `llm` | 300–800 ms [A] | A small model with enum output, for ambiguous or paraphrased input |
| `clarify` | n/a | Returns `input.required{choose}` from the top candidates. At most one clarification per turn. |

**Generic thresholds** (per-app config) [R]:

- Accept a classifier result when confidence ≥ `accept` and the margin ≥ `margin`.
- Otherwise, use the LLM with a timeout.
- On LLM timeout, accept the classifier's top-1 if confidence ≥ `fallback`.
- Otherwise, clarify.
- A second consecutive clarification goes to the app's `default_unit`.

Every decision is recorded (`DispatchDecision`) and evaluated like any other component.

## 5. Delegation rules

- A unit can delegate only to units listed in its manifest `delegates`.
- `for_child` gives the child:
  - tools = parent allowlist ∩ child allowlist;
  - budget and deadline = a slice of the parent's remainder;
  - the same principal (never elevated).
- Default `max_delegation_depth` = 1. The dispatcher is never re-entered from inside a run.
- Delegation is an in-process call by default. The target's location (`in_process`, `remote`, `a2a`) is config.

## 6. Remote units and A2A

| Boundary | Mechanism | Identity |
|---|---|---|
| Same app, same team | In-process call | Same `ExecutionContext` (narrowed) |
| Same org, different service, both Sutra | Typed RPC (`RemoteUnit`) with shared contract schemas; `traceparent` propagated | Token exchange to the callee's audience. The callee re-verifies. |
| Different org, vendor, or non-Sutra stack | A2A 1.0 [V] |<ul><li>OAuth2 or mTLS.</li><li>Signed Agent Card.</li><li>The callee authorizes every operation.</li><li>`AUTH_REQUIRED` is mapped to `input.required{auth}` and is never treated as a grant.</li></ul> |

A2A is **not** used just because a system has several logical agents.

## 7. Human input and confirmation

- Units request input by yielding `input.required` (clarify, choose, form, auth, confirm).
- With ADK this comes from human-input nodes or action confirmation [V].
- The run ends with `needs_input`. The next turn resumes the same unit (sticky dispatch). Resumable units restore from their checkpoint.
- **Write tools that require confirmation:**
  1. `ToolExecutor` issues a single-use `ConfirmationChallenge`. It is bound by HMAC to (principal, tool, canonical args, expiry).
  2. The tool executes only when the next turn presents the matching token.
- Escalation to a human is an app tool (for example, opening a ticket). The harness only provides the `escalated` status.

## 8. Streaming

- Units emit `progress`, `message.delta`, `ui.block` and `input.required` events.
- Channel adapters translate them:
  - SSE with the harness event names (default);
  - AG-UI for apps whose frontend uses it [V];
  - typed-RPC streams;
  - A2A task status updates.
- Back-pressure: the adapter buffers up to N events. A slow client causes cancellation, not unbounded memory.
