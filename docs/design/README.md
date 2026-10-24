# Sutra: Agent Harness Design

- **Status:** Draft v0.2. Python SDK v0.1 implements the minimum viable harness (see [section 8](#8-implementation-status-python-sdk-v01)).
- **Date:** 14 Oct 2026
- **Scope:** the harness only, independent of any use case

"Sutra" is a working name.

> **Change from v0.1.** The first draft was shaped around a single customer-support use case. The harness is now designed independently of any use case. Nothing in this folder may depend on a specific domain.

---

## 1. What the harness is

The harness is the **shared runtime and library** that every agent application runs on. It does not decide *what* an agent does. It guarantees *how* any agent runs:

- with a verified identity;
- with only the tools and data it is allowed to use;
- within deadlines and budgets;
- with consistent telemetry, error handling and evaluation hooks;
- from versioned, reviewable configuration.

An application team brings:

- prompts;
- tools (domain API clients);
- workflows or agents;
- evaluation cases.

The harness brings everything else.

## 2. Design principles

1. **Thin harness, thick contracts.** Stable, typed contracts between components. The internals of an agent are free.
2. **No base class.** Onboarding means providing a manifest, a factory, typed I/O and an evaluation suite (see [02-contracts.md](02-contracts.md#1-minimum-onboarding-contract)).
3. **One choke point per concern.**
   - All model calls go through `ModelClient`.
   - All tool calls go through `ToolExecutor`.
   - All context goes through `ContextBuilder`.
   - All policy decisions go through `PolicyEngine`.
4. **Deny by default, enforced in the runtime.** Safety and authorization never depend on the model behaving.
5. **Framework behind an adapter.** ADK 2.0 is the default engine. Harness contracts do not leak framework types.
6. **Libraries before services.** Add a network service only when a stated trigger fires.
7. **Everything versioned, everything traced.** Every result can be tied to the bundle, prompt, model and policy versions that produced it.
8. **Simple first.** The harness must serve a single-agent, read-only use case with minimal configuration. Complex orchestration is opt-in.

## 3. Explicit non-goals for V1

- Agent marketplace or catalog UI.
- Visual workflow builder.
- Autonomous multi-agent planning (open-ended or recursive).
- Cross-cloud or multi-framework portability.
- A separate control-plane microservice.
- A separate model gateway or agent gateway service.
- Long-term semantic memory.
- Self-hosted inference.

## 4. Documents

| # | Document | Covers |
|---|---|---|
| 1 | [01-architecture.md](01-architecture.md) | Layers, components, extension points, diagrams |
| 2 | [02-contracts.md](02-contracts.md) | Onboarding contract and component interfaces |
| 3 | [03-runtime-and-execution.md](03-runtime-and-execution.md) | Run lifecycle, execution patterns, dispatch, delegation, remote agents |
| 4 | [04-context-and-state.md](04-context-and-state.md) | Context assembly, sessions, checkpoints, caching |
| 5 | [05-security-and-policy.md](05-security-and-policy.md) | Identity, tool authorization, policy, data handling, prompt injection |
| 6 | [06-observability-reliability-evaluation.md](06-observability-reliability-evaluation.md) | Telemetry contract, failure handling, evaluation |
| 7 | [07-configuration-and-control-plane.md](07-configuration-and-control-plane.md) | Manifests, bundles, flags, rollout, control-plane evolution |
| 8 | [08-technology-decisions.md](08-technology-decisions.md) | Technology choices and ADRs |
| 9 | [09-mvh-nfr-roadmap.md](09-mvh-nfr-roadmap.md) | Minimum viable harness, harness NFRs, repository layout, roadmap, first use-case selection |

## 5. Evidence legend

| Tag | Meaning |
|---|---|
| [V] | Verified against current public documentation (14 Oct 2026) |
| [A] | Assumption; must be confirmed |
| [R] | Recommendation or provisional value |

## 6. Verified framework facts relied on

| Fact | Source |
|---|---|
| ADK 2.0 is GA for Python (May 2026), Go (Jun 2026) and TypeScript (Aug 2026). It adds graph workflows (nodes: agents, tools, functions, human input; edges with routes) and dynamic and collaborative workflows. | adk.dev/2.0, adk.dev/graphs |
| ADK 2.0 breaking changes:<ul><li>The event schema gains `node_info` and `output`.</li><li>1.x run overrides are silently bypassed.</li><li>Broad `except Exception` disables node retries.</li><li>Catching `BaseException` breaks human input (`NodeInterruptedError`).</li></ul> | adk.dev/2.0 |
| ADK plugins are global, are registered on `App`, and run before agent callbacks. Hooks: user message, run, agent, model, tool, error and event. Prebuilt plugins include Model Armor, BigQuery analytics, context filter, tracing and reflect-and-retry. | adk.dev/plugins |
| ADK resumability (`ResumabilityConfig`). Tools may execute more than once on resume, so writes need idempotency. | adk.dev/runtime/resume |
| ADK supports Vertex AI, LiteLLM, vLLM, Apigee AI Gateway, context caching and compaction, MCP and OpenAPI tools, action confirmation, A2A, evaluation with user and environment simulation, and deployment to Agent Runtime, Cloud Run or GKE. | adk.dev |
| A2A 1.0:<ul><li>JSON-RPC, gRPC and HTTP+JSON bindings.</li><li>`A2A-Version` header.</li><li>Signed Agent Cards.</li><li>Every operation must be authorized.</li><li>`AUTH_REQUIRED` is a task state, not a grant.</li></ul> | a2a-protocol.org/latest/specification |
| AG-UI is an agent-to-UI event protocol with a first-party ADK integration. | docs.ag-ui.com |
| OTel GenAI semantic conventions (agent, model, tool and MCP spans and metrics) are maintained in a separate repository. | github.com/open-telemetry/semantic-conventions-genai |
| LangGraph has checkpointers (Postgres, SQLite), interrupts and stores. | docs.langchain.com/oss/python/langgraph/persistence |

## 7. Glossary

| Term | Meaning |
|---|---|
| **Application (app)** | A product surface built on the harness (for example an internal help assistant). It owns a config bundle and a deployment. |
| **Unit** | A logical agent or workflow: a versioned manifest plus code, prompts, a tool allowlist, limits and eval suites. |
| **Run** | One execution of a turn: from accepted request to terminal event. |
| **Execution** | One unit executing within a run. A run may contain several, through delegation. |
| **Entrypoint** | The unit that receives a run. Chosen by the dispatcher. |
| **Bundle** | An immutable, content-hashed set of manifests, prompts, policies and model aliases for one app. |
| **PEP** | Policy enforcement point. `ToolExecutor`, `ContextBuilder` and ingress are PEPs. |

## 8. Implementation status (Python SDK v0.1)

Code lives in [../../src/sutra](../../src/sutra), with tests in [../../tests](../../tests) and an example app in [../../examples/hello_app](../../examples/hello_app).

### Deviations from the design text

| Topic | Design | SDK v0.1 |
|---|---|---|
| Unit interface | `run(ctx, input) -> output` | `async run(ctx) -> UnitOutput \| str`. Input is read from `ctx.request`; streaming events are emitted through `ctx.events`. |
| Write idempotency key | Per tool call | `sha256(app_id, conversation_id, turn_id, call_signature)`. A retried turn replays the stored result instead of writing again. |
| Loop guard | "Repeated identical calls" | The run fails with `LOOP_DETECTED` when a call with the same tool and canonical args would exceed `max_identical_tool_calls` (default 2). |
| Policy default | Deny by default | Deny by default is enforced by allowlists: units, tools, model aliases and delegates. After the built-in checks and rules, `PolicyEngine` allows. |
| Confirmation tokens | Single use | They are HMAC-bound to the principal, tool and args, and expire. Single use is tracked in memory per process, so a multi-instance deployment needs a shared store. |

### Not yet implemented

- Postgres session and result stores, and a distributed conversation lock (in-memory only).
- Dispatch by LLM or classifier (fixed, sticky and rules dispatch are implemented).
- Streaming model calls; shared caches.
- `RemoteUnit` / A2A.
- For ADK units, `ContextBuilder` output is passed only as a fenced user message, not as ADK instructions.
