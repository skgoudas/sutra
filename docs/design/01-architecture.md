# Harness Architecture

## 1. Layers

| Layer | Owns | Owned by |
|---|---|---|
| **Channel adapters** | Translate transport to and from harness contracts: SSE / AG-UI for UIs, typed RPC for Sutra-to-Sutra calls, A2A for external agents | Platform |
| **Runtime shell** | Ingress, dispatcher, run lifecycle, deadlines, cancellation, budgets, event stream, config loading | Platform |
| **Engine adapter** | Runs units on ADK 2.0. Maps ADK events to `RunEvent`. Registers harness plugins. | Platform |
| **Harness library** | `ContextBuilder`, `ModelClient`, `ToolExecutor`, `PolicyEngine`, `Telemetry`, eval hooks, testing kit | Platform |
| **App units** | Agents and workflows, prompts, tool definitions, policies, evals | App team |
| **Dependencies** | Domain APIs, knowledge sources, model providers, Postgres, observability backends | Existing or managed |

The harness has **no knowledge of any domain**:

- no intent taxonomy;
- no domain objects;
- no UI components.

Applications provide those through manifests and code.

## 2. Component view

```mermaid
flowchart TB
    subgraph CLIENT["Clients"]
        UIC["Web, app, chat or API client"]
        PEER["Other agents or services"]
    end
    subgraph EDGE["Existing edge"]
        GW["API gateway / BFF<br/>end-user authentication"]
        IDP["Identity provider and STS"]
    end
    subgraph RUNTIME["Sutra runtime - one service per app"]
        ING["Ingress<br/>envelope, deadline, idempotency"]
        ADP["Channel adapters<br/>SSE, AG-UI, typed RPC, A2A"]
        DSP["Dispatcher<br/>fixed, rules, classifier, LLM"]
        LCY["Run lifecycle<br/>budgets, cancel, events"]
        ENG["Execution engine adapter<br/>ADK 2.0"]
        UNITS["App units<br/>agents and workflows"]
        CTX["ContextBuilder"]
        MDL["ModelClient"]
        TEX["ToolExecutor - PEP"]
        POL["PolicyEngine"]
        TEL["Telemetry and eval hooks"]
    end
    subgraph STATE["State"]
        PG[("Postgres<br/>sessions, checkpoints, idempotency")]
    end
    subgraph EXT["App-owned dependencies"]
        API["Domain APIs"]
        KB["Retrieval or knowledge sources"]
    end
    subgraph MODELS["Model providers"]
        VTX["Vertex AI"]
    end
    subgraph OBS["Observability"]
        OTC["OTel collector"]
        GCO["Cloud Trace, Logging, Monitoring"]
        BQ[("Restricted interaction log")]
    end
    subgraph CP["Control plane - V1 Git and CI"]
        BND["Immutable bundle"]
        FF["Flags and kill switches"]
    end
    UIC --> GW --> ADP
    PEER --> ADP
    GW -. "assertion" .-> IDP
    ADP --> ING --> DSP --> LCY --> ENG --> UNITS
    UNITS --> CTX
    UNITS --> MDL
    UNITS --> TEX
    TEX --> POL
    CTX --> POL
    ING --> POL
    TEX -. "token exchange" .-> IDP
    TEX --> API
    CTX --> KB
    CTX --> PG
    LCY --> PG
    MDL --> VTX
    TEL --> OTC --> GCO
    TEL --> BQ
    BND --> ING
    FF -.-> DSP
```

## 3. Components

| Component | Responsibility | Key rule |
|---|---|---|
| **Ingress** | Validates the envelope, verifies the principal assertion, enforces deadline caps and request idempotency, starts the run span | Rejects anything it cannot verify. Clients never supply identity in the body. |
| **Channel adapters** | Map `RunEvent` to SSE, AG-UI, typed RPC or A2A | Units never emit protocol-specific events |
| **Dispatcher** | Selects the entrypoint unit. Strategies: `fixed` (single unit), `rules`, `classifier`, `llm`, `tiered`. | Optional. A single-unit app uses `fixed` and pays no routing cost. |
| **Run lifecycle** | `accept → dispatch → execute → validate → finalize`. Owns budgets, deadlines, cancellation, the event sequence and the result store. | Exactly one terminal event per run |
| **Engine adapter** | Wraps ADK 2.0 agents and workflows as the harness `Unit`. Registers plugins: safety, policy guard, budget, telemetry. | The only package that imports ADK |
| **ContextBuilder** | Assembles per-step context from declared providers, with authorization recheck, trust labelling, budgets and stable-prefix ordering | A unit receives only what its `ContextSpec` declares |
| **ModelClient** | Model aliases, capability checks, fallback, timeouts, token and cost accounting, structured output | No raw model IDs in app code |
| **ToolExecutor** | Allowlist, argument validation, bound arguments, policy, credentials, timeout, retry, circuit breaker, idempotency, confirmation, audit | The only path to tool credentials |
| **PolicyEngine** | Synchronous, in-process decisions: allow, deny, require confirmation, require step-up, plus obligations | No I/O on the request path |
| **Telemetry** | OTel spans, metrics and logs following the GenAI semantic conventions. `SafeAttributes` allowlist. | No content in spans |
| **Eval hooks** | Synchronous validators before emission. Asynchronous sampled eval records after emission. | Never blocks the user on async evaluation |

## 4. Extension points (what an app plugs in)

| Extension | Interface | Typical example |
|---|---|---|
| Unit | Manifest + `build(BuildContext)` factory | An LLM agent with two tools; a graph workflow |
| Tool | `ToolDefinition` + typed client | Read-only lookup against an internal API |
| Context provider | `ContextProvider` | Knowledge-base search; user preferences |
| Dispatcher strategy | `DispatchStrategy` | Rules file; trained classifier |
| Validator | `Validator` | "The answer cites at least one retrieved source" |
| Policy rules | YAML in the bundle | "Tool X requires auth level `authenticated`" |
| UI payload types | Versioned `UiBlock` schemas | Cards, lists, forms |
| Channel adapter | `ChannelAdapter` | Slack or Teams bot, voice |

## 5. Generic run sequence

```mermaid
sequenceDiagram
    autonumber
    participant CL as Client
    participant ED as Edge
    participant IN as Ingress
    participant DS as Dispatcher
    participant LC as Lifecycle
    participant U as Unit
    participant CB as ContextBuilder
    participant MC as ModelClient
    participant TX as ToolExecutor
    participant DEP as Dependency
    participant OB as Telemetry
    CL->>ED: Turn with user credentials
    ED->>IN: Envelope, principal assertion, traceparent
    IN->>IN: Validate envelope, verify assertion, idempotency check
    alt Duplicate request
        IN-->>CL: Replay stored result
    else New request
        IN->>OB: Start run span
        IN->>DS: Select entrypoint
        DS-->>LC: Entrypoint unit and version
        LC->>LC: Policy run.start, open budget and deadline
        LC->>U: run with ExecutionContext
        U->>CB: Build context for this step
        CB-->>U: ContextPacket
        U->>MC: Generate with alias
        alt Model error or timeout
            MC->>MC: Fallback alias within budget
        end
        MC-->>U: Response with tool call
        U->>TX: Invoke tool
        TX->>TX: Allowlist, schema, policy, budget, credentials
        alt Policy denies
            TX-->>U: POLICY_DENIED
        else Allowed
            TX->>DEP: Call with scoped credentials and timeout
            DEP-->>TX: Result or error
            TX-->>U: Projected ToolResult
        end
        U-->>LC: Stream events, then AgentResult
        LC->>LC: Validators, persist result
        LC-->>CL: RunEvents over channel adapter
        LC->>OB: Close spans, metrics, eval record
    end
```

## 6. Deployment (V1)

- One Cloud Run service per application. It contains the runtime shell, the harness library and all of that app's units.
- An OTel collector sidecar [A: confirm the current Cloud Run multi-container limits].
- Postgres (Cloud SQL) for sessions, checkpoints and idempotency.
- Vertex AI for models.
- The existing API gateway and identity provider at the edge.
- Secrets in Secret Manager, accessed with workload identity.

A "logical agent" is **not** a deployment unit. Many units share one process.

## 7. Evolution without rewrites

```mermaid
flowchart LR
    subgraph L1["Stage 1 - V1"]
        A1["App service<br/>runtime + all units in-process"]
    end
    subgraph L2["Stage 2 - isolate one unit"]
        A2["App service"] -- "typed RPC via RemoteUnit" --> B2["Unit service<br/>same contracts"]
    end
    subgraph L3["Stage 3 - interoperate"]
        A3["App service"] -- "A2A 1.0" --> C3["External or other-org agent"]
        D3["Other-org agent"] -- "A2A 1.0" --> A3
    end
    subgraph L4["Stage 4 - async work"]
        A4["App service"] -- "Pub/Sub job" --> W4["Worker<br/>durable checkpoints"]
    end
    L1 --> L2 --> L3 --> L4
```

Where a unit runs is a **config decision**. The caller always holds a `Unit` interface: in-process, `RemoteUnit` (typed RPC), or `A2AUnit`.
