# Technology Decisions

## 1. Selection summary

| Area | Recommended (V1) | Alternatives | Lock-in | Change when |
|---|---|---|---|---|
| Agent framework | **ADK 2.0 (Python)** behind `Unit` contracts | LangGraph; plain code | Moderate, contained by `AdkUnit` | ADR-001 |
| Workflow engine | ADK graph workflows, or `FunctionUnit` for simple flows | LangGraph; Temporal or Cloud Workflows for long-running durability | Low | Multi-day durable flows are needed |
| Model access | **In-process `ModelClient` → Vertex AI** | LiteLLM Proxy; Apigee AI Gateway [V: ADK integration]; direct SDKs | Low (aliases) | ADR-003 |
| Inference | **Vertex AI managed** | vLLM on GKE [V: ADK supports it] | Moderate | High-volume narrow tasks where a small open model is cheaper at equal quality; data residency |
| Tools | **Typed clients via `ToolExecutor`** | MCP; OpenAPI tools [V] | Low | ADR-005 |
| Agent-to-agent | **In-process / typed RPC**; A2A 1.0 at interop boundaries | A2A everywhere | Low | ADR-004 |
| State | **Postgres (Cloud SQL)** | Firestore; AlloyDB; Redis | Low | ADR-006 |
| Caching | Provider prefix cache + in-process LRU | Memorystore | Low | ADR-006 |
| Observability | **OTel + Google Cloud Observability + BigQuery interaction log** | Langfuse (self-hosted); LangSmith | Low | ADR-007 |
| UI streaming | **SSE with `RunEvent`**; AG-UI adapter available | WebSockets | Low | Full-duplex voice. Then add a WebSocket or live adapter. |
| Policy | **In-process YAML rules** | OPA or Rego; Cedar; CEL | Low | Multi-language apps or a central policy authority |
| Secrets | Secret Manager + workload identity + KMS | Vault | Low | Enterprise mandate |
| Deployment | **Cloud Run per app** + collector sidecar | GKE; Vertex Agent Runtime [V] | Low to moderate | ADR-008 |
| Language | **Python** | TypeScript or Go ports | n/a | ADR-009 |

---

## ADR-001: ADK 2.0 as the default engine

**Context.** The organization already uses ADK [A]. ADK 2.0 provides:

- graph and dynamic workflows;
- human input;
- node retries;
- resumability;
- global plugins;
- evaluation with simulation;
- A2A support;
- AG-UI integration [V].

LangGraph offers mature checkpointers, interrupts and time travel [V].

**Decision.**

- ADK 2.0 is the single default engine.
- It is wrapped by `AdkUnit`. No ADK types appear outside the adapter package.
- No multi-framework runtime in V1.

**Consequences.**

- The ADK 2.0 caveats must be respected [V]:
  - JSON session events;
  - no reliance on 1.x run overrides;
  - no broad `except` in nodes;
  - no catching `BaseException`;
  - idempotent tools for resume.
- Plugin order: safety → policy guard → budget → telemetry.

**Reconsider if:**

- a required capability is missing for 2 or more quarters;
- breaking changes cost more than 1 sprint per quarter;
- a non-Python app with committed staffing needs a framework ADK serves poorly.

The response is a second `Unit` adapter for the affected units, not a platform migration.

---

## ADR-002: Simple execution by default; orchestration is opt-in

**Context.** The common failure mode of agent platforms is a central planner in front of everything. It brings extra latency and cost and is non-deterministic.

**Decision.**

- The default is `fixed` dispatch to one unit (an LLM agent or a deterministic workflow).
- Multi-unit dispatch (rules → classifier → LLM → clarify) and bounded delegation are opt-in per app, with limits in the manifest.
- No open-ended autonomous planning in V1.

**Consequences.** Apps with several task types must define a taxonomy and evaluate dispatch.

**Reconsider if:** bounded delegation measurably fails on valuable tasks that an evaluated planner handles better.

---

## ADR-003: No gateway service in V1

**Context.** An **agent gateway** fronts agent endpoints. A **model gateway** fronts providers. Both add a network hop and a service to operate.

**Decision.**

- In-process `ModelClient` (aliases, fallback, accounting, budgets).
- The existing API gateway for ingress.
- Provider quotas and IAM as provider-side controls.

**Reconsider if** any two of these hold:

- 3 or more apps or languages need shared quotas or keys;
- more than 2 external providers;
- network-level prompt inspection is mandated;
- cross-app caching is needed.

Preferred options at that point: Apigee AI Gateway (enterprise API management, ADK integration [V]) or LiteLLM Proxy (provider breadth). Either is inserted behind `ModelClient`.

---

## ADR-004: Typed RPC by default; A2A at interoperability boundaries

**Decision.**

- In-process calls within an app.
- Typed RPC (`RemoteUnit`) between Sutra services.
- A2A 1.0 only for other orgs, vendors or non-Sutra stacks, or where discovery and long-running task semantics are required [V].

**Rationale.** A2A's value is interoperability. Inside one platform it adds serialization, an identity surface and debugging cost.

**Reconsider if** the enterprise standardizes all agent-to-agent traffic on A2A for governance or discovery.

---

## ADR-005: Typed clients by default; MCP for shared or external catalogs

**Decision.**

- First-party APIs use typed clients (existing SDKs or OpenAPI-generated) registered as `ToolDefinition`s.
- MCP is used when:
  - tools are shared across several apps or clients;
  - tools are provided by third parties;
  - non-Python consumers need them.
- **All tools go through `ToolExecutor`, MCP included.** MCP tools are pinned by name and version, and dynamic discovery is off in production.

**Reconsider if** a governed enterprise MCP hub exists with 3 or more consumers.

---

## ADR-006: Postgres for state; no Redis or vector DB in the harness

**Decision.**

- Postgres (Cloud SQL) for sessions, checkpoints, idempotency and summaries.
- Provider prefix caching and an in-process LRU.
- Retrieval, profiles and vectors are app-owned providers.

**Reconsider if:**

- Postgres p99 exceeds 20 ms or connections are exhausted at peak, after tuning. Then add a Memorystore session cache, or move to AlloyDB.
- The measured shared-cache hit-rate projection exceeds 50% with meaningful savings. Then add Memorystore.

---

## ADR-007: OTel + Google Cloud Observability + BigQuery

**Decision.**

- OTel GenAI semantic conventions.
- Collector sidecar exporting to Cloud Trace, Logging and Monitoring.
- A restricted BigQuery interaction log (an own sink, or the ADK BigQuery analytics plugin with redaction [V]).
- No AI-observability SaaS in V1.

**Reconsider if:**

- annotation throughput becomes a bottleneck;
- 2 or more teams need a prompt-experiment UI;
- the median time to explain a bad answer exceeds 15 minutes with the current tools.

In that case, self-hosted Langfuse is preferred, for data control.

---

## ADR-008: Cloud Run per app

**Decision.**

- One Cloud Run service per app with a collector sidecar [A: verify current multi-container limits].
- Minimum instances above zero for interactive apps.
- Cloud Run jobs for offline evals.

**Reconsider if:**

- **Move to GKE** if any of these hold:
  - self-hosted GPU inference;
  - very high sustained concurrency where tuning saves cost;
  - gateway collectors for tail sampling;
  - an org mandate.
- **Use Vertex Agent Runtime** for small internal apps that accept its constraints.
- **Use async workers** if work regularly exceeds request timeouts [A: verify current Cloud Run limits].

---

## ADR-009: Python only in V1

**Decision.**

- The harness is Python.
- Contracts are exported as JSON Schema, which is the source of truth.
- Non-Python consumers integrate via typed RPC or A2A.

**Reconsider if** a non-Python app has committed staffing. Then generate types from the schemas and port only `ToolExecutor`, `PolicyEngine` and telemetry.

---

## ADR-010: Delegated tool authorization by default

**Decision.**

- Token exchange to audience-scoped, short-lived delegated tokens for any user-owned data.
- A signed user context is allowed only as a time-boxed exception.
- Broad service accounts with unsigned user identifiers are prohibited. See [05-security-and-policy.md](05-security-and-policy.md#3-tool-authorization-patterns).

**Reconsider if** STS latency or availability cannot meet an app's SLO. Then use the interim pattern with API-side verification while the STS is fixed.
