# Contracts

The code below is Python-flavored pseudocode in Pydantic style. It is **design, not implementation**. Names may change during the walking-skeleton milestone (H1).

## 1. Minimum onboarding contract

There is **no base class**. A unit (agent or workflow) is onboarded when it provides four things:

1. **Manifest.** A `UnitDefinition` (data, in the app bundle).
2. **Factory.** `build(BuildContext) -> Runnable`. The result can be:
   - an ADK agent or workflow;
   - any object implementing `Unit.run`.
3. **Typed I/O.** An input schema, plus a result that conforms to `AgentResult`.
4. **Eval suite reference.** At least one deterministic scenario set.

Everything else comes from the harness:

- identity and policy;
- tools, models and context;
- budgets and events;
- telemetry and the eval pipeline.

Internally a unit may be a graph, a single LLM agent with tools, or plain code.

### The word "agent"

"Agent" is used for three different things:

| Term | Meaning | Lifetime |
|---|---|---|
| Unit | Versioned definition (manifest + code + prompts + allowlist + limits + evals) | Git, then a bundle |
| Execution | One unit running inside a run, with its own IDs, budget slice and state | Milliseconds to minutes |
| Process | Container instance hosting the runtime for one app | Minutes to hours |

---

## 2. Request, result and events

```python
class UserInput(BaseModel, extra="forbid", frozen=True):
    text: constr(max_length=MAX_INPUT_CHARS)
    attachments: list[AttachmentRef] = []

class AgentRequest(BaseModel, extra="forbid", frozen=True):
    schema_version: Literal["1"]
    request_id: Ulid
    turn_id: UUID                          # client idempotency key
    conversation_id: ConversationId
    app_id: AppId
    channel: Channel
    locale: LocaleTag
    input: UserInput
    client_hints: dict[str, JsonScalar] = {}   # UNTRUSTED; app-defined, schema-validated per app
    deadline: AwareDatetime
    # No identity fields. Identity lives only in ExecutionContext.principal.

RunStatus = Literal["completed", "needs_input", "degraded", "escalated", "failed", "cancelled"]

class Citation(BaseModel):
    source: str                            # tool or provider name + version
    ref: str                               # opaque id
    fetched_at: AwareDatetime

class ResponseMessage(BaseModel):
    text: str
    citations: list[Citation] = []
    generated_by: Literal["template", "model"]

class InputRequest(BaseModel):
    kind: Literal["clarify", "choose", "confirm", "auth", "form"]
    prompt_key: str
    options: list[Option] = []
    confirmation: ConfirmationChallenge | None = None

class Usage(BaseModel):
    model_calls: int; tool_calls: int; steps: int
    input_tokens: int; cached_input_tokens: int; output_tokens: int
    cost_usd_micros: int

class VersionStamp(BaseModel):
    bundle_hash: str; units: list[str]; prompts: list[str]; models: list[str]; policy: str

class AgentResult(BaseModel):
    status: RunStatus
    messages: list[ResponseMessage]
    ui_blocks: list[UiBlock] = []          # app-registered, versioned schemas
    pending_input: InputRequest | None = None
    error: SutraError | None = None
    entrypoint: DispatchDecision
    usage: Usage
    versions: VersionStamp

class RunEvent(BaseModel):
    seq: int; run_id: RunId; ts: AwareDatetime
    type: Literal["run.started", "progress", "message.delta", "message.completed",
                  "ui.block", "input.required", "run.completed", "run.failed"]
    payload: dict                          # typed per event type
```

**Rules:**

- `RunEvent` is the only output stream.
- AG-UI, SSE, typed RPC streaming and A2A task updates are all adapters over `RunEvent`.
- A run emits exactly one terminal event, and that event carries the `AgentResult`.

---

## 3. Unit definition

```python
class ExecutionLimits(BaseModel):
    deadline_ms: int; max_steps: int; max_model_calls: int; max_tool_calls: int
    max_input_tokens: int; max_output_tokens: int; max_cost_usd_micros: int
    max_delegations: int = 0; max_delegation_depth: int = 0
    max_retries_total: int = 2

class ContextSpec(BaseModel):
    providers: list[ContextProviderRef]
    budgets: dict[ContextKind, int]        # tokens per kind
    history: HistoryPolicy                 # last_n_turns, summarize_after_tokens
    freshness_s: dict[ContextKind, int]

class UnitDefinition(BaseModel, extra="forbid"):
    id: UnitId; version: SemVer; owner: TeamId
    kind: Literal["llm_agent", "graph_workflow", "dynamic_workflow", "function"]
    description: str
    entrypoint: ImportPath                 # "<app>.units.<name>:build"
    input_schema: SchemaRef; output_schema: SchemaRef
    model_alias: ModelAlias | None
    prompt: PromptRef | None
    tools: list[ToolName]                  # allowlist
    delegates: list[UnitId] = []           # units this unit may call
    context: ContextSpec
    limits: ExecutionLimits
    min_auth_level: AuthLevel
    side_effects: Literal["none", "idempotent_writes", "writes_with_confirmation"]
    data_access: DataAccessDecl            # data classes read and written
    resumable: bool = False
    eval_suites: list[EvalSuiteRef]
    labels: dict[str, str] = {}

class BuildContext(Protocol):              # passed to factories at startup
    definition: UnitDefinition
    tool_specs: Callable[[], list[ToolSpecForModel]]
    engine: EngineBindings                 # ADK plugin set, session service, model adapter

class Unit(Protocol):
    definition: UnitDefinition
    def run(self, ctx: "ExecutionContext") -> AsyncIterator[RunEvent]:
        """Honor ctx.deadline and ctx.cancel; end with exactly one terminal event."""
```

**Adapters implementing `Unit`:**

- `AdkUnit`:
  - wraps an ADK 2.0 agent or workflow;
  - registers the harness plugins;
  - maps ADK events to `RunEvent`;
  - maps human-input interrupts to `input.required`;
  - enables resumability when the manifest sets `resumable`.
- `FunctionUnit`: a plain async generator. Useful for deterministic flows and tests.
- `RemoteUnit`: typed RPC to another Sutra service.
- `A2AUnit`: an A2A 1.0 client.

---

## 4. Execution context

```python
class Principal(BaseModel, frozen=True):
    kind: Literal["end_user", "employee", "service", "anonymous"]
    subject: Sensitive[str]                # never loggable
    subject_hmac: str
    auth_level: AuthLevel
    scopes: frozenset[str]
    tenant: TenantId
    resource_bindings: frozenset[str] = frozenset()
    assertion: Sensitive[str]              # for token exchange only
    expires_at: AwareDatetime

class ExecutionIds(BaseModel, frozen=True):
    request_id: Ulid; conversation_id: ConversationId; run_id: RunId
    execution_id: str; parent_execution_id: str | None

class ExecutionContext(Protocol):
    request: AgentRequest                  # untrusted content
    principal: Principal                   # trusted identity
    ids: ExecutionIds
    trace: TraceContext
    deadline: Deadline                     # remaining_ms(), child(max_ms)
    cancel: CancellationToken
    budget: BudgetMeter                    # charge(kind, n) raises BudgetExceeded
    config: ResolvedConfig
    flags: FlagView
    tools: ToolExecutor                    # scoped to this unit's allowlist
    models: ModelClient                    # scoped to this unit's alias and budget
    context: ContextBuilder
    events: EventSink
    def for_child(self, child: UnitDefinition) -> "ExecutionContext":
        """New execution_id; tools = parent ∩ child allowlist; budget and deadline sliced;
        delegation depth incremented and checked."""
```

Permissions **narrow, never widen**, as execution moves down the tree.

---

## 5. Tools

```python
class ToolAuth(BaseModel):
    mode: Literal["delegated", "service_with_signed_context", "service_only", "none"]
    audience: str | None
    scopes: frozenset[str]
    min_auth_level: AuthLevel
    resource_binding: str | None = None    # arg that must match principal.resource_bindings

class RetryPolicy(BaseModel):
    max_attempts: conint(ge=1, le=3) = 2
    on: frozenset[ErrorCode] = {"DEPENDENCY_TIMEOUT", "DEPENDENCY_UNAVAILABLE", "RATE_LIMITED"}
    backoff_ms: tuple[int, int] = (50, 400)
    require_idempotent: bool = True

class ToolDefinition(BaseModel, extra="forbid"):
    name: ToolName; version: SemVer; owner: TeamId
    description_for_model: str
    args_model: type[BaseModel]            # model-visible, strict
    bound_args: dict[str, Binding]         # injected from ctx; not model-visible
    result_model: type[BaseModel]
    result_projection: Projection          # fields allowed back to model and context
    side_effect: Literal["read", "write"]
    requires_confirmation: bool = False
    auth: ToolAuth
    timeout_ms: int
    retry: RetryPolicy
    breaker_key: str
    data_classification: DataClass
    transport: Literal["typed_client", "openapi", "mcp", "a2a"]

class ToolResult(BaseModel):
    ok: bool
    data: BaseModel | None                 # projected
    citations: list[Citation] = []
    error: SutraError | None = None
    latency_ms: int

class ToolExecutor(Protocol):
    def specs_for_model(self) -> list[ToolSpecForModel]: ...
    async def invoke(self, ctx: ExecutionContext, tool: ToolName,
                     args: Mapping[str, Any], *, caller: Literal["node", "model"]) -> ToolResult: ...
```

**Invocation order:**

1. Allowlist.
2. Argument validation.
3. Inject bound arguments.
4. `PolicyEngine.decide(tool.invoke)`.
5. Check the confirmation token, if required.
6. Charge the budget.
7. Check the breaker.
8. Acquire credentials.
9. Call, with timeout and retry.
10. Project the result.
11. Audit (for writes and denials).
12. Emit the span.

---

## 6. Models

```python
class ModelCapabilities(BaseModel):
    structured_output: bool; tool_calling: bool; context_cache: bool
    reasoning_budget: bool; max_context_tokens: int; modalities: frozenset[str]

class ModelTarget(BaseModel):              # resolved from alias config
    provider: Literal["vertex", "vllm", "other"]; model: str; region: str
    capabilities: ModelCapabilities; price: PriceCard; timeout_ms: int

class ModelRequest(BaseModel):
    alias: ModelAlias                      # e.g. "small-fast", "general", "reasoning"
    packet: ContextPacket
    tools: list[ToolSpecForModel] = []
    response_schema: SchemaRef | None = None
    max_output_tokens: int
    required_capabilities: frozenset[str] = frozenset()
    provider_options: dict[str, Any] = {}  # namespaced, e.g. "vertex.thinking_budget"

class ModelResponse(BaseModel):
    content: list[Part]; tool_calls: list[ToolCall]; finish_reason: str
    usage: TokenUsage; target: ModelTarget
    latency_ms: int; cost_usd_micros: int; fallback_used: bool

class ModelClient(Protocol):
    async def generate(self, ctx: ExecutionContext, req: ModelRequest) -> ModelResponse: ...
    def stream(self, ctx: ExecutionContext, req: ModelRequest) -> AsyncIterator[ModelChunk]: ...
```

**ADK integration [A: validate in the H1 spike].**

- Preferred: a custom ADK model adapter wrapping `ModelClient`, so ADK agents use the same choke point.
- Fallback: native ADK models, with the `before_model`, `after_model` and `on_model_error` plugin hooks doing accounting, budget and fallback.

---

## 7. Context

```python
class ContextKind(StrEnum):
    INSTRUCTIONS = "instructions"; POLICY = "policy"; APP_CONFIG = "app_config"
    CONVERSATION = "conversation"; TASK_STATE = "task_state"; TOOL_RESULTS = "tool_results"
    RETRIEVED = "retrieved"; PREFERENCES = "preferences"

class Provenance(BaseModel):
    source: str; fetched_at: AwareDatetime; ttl_s: int
    classification: DataClass; principal_scope: str | None; version: str | None

class ContextItem(BaseModel):
    kind: ContextKind; content: str | BaseModel; tokens: int; priority: int
    trusted: bool                          # False: fenced as data
    cache_scope: Literal["public", "app", "user", "none"]
    provenance: Provenance

class ContextProvider(Protocol):
    kind: ContextKind
    async def fetch(self, ctx: ExecutionContext, q: ContextQuery) -> list[ContextItem]: ...

class ContextPacket(BaseModel):
    stable_prefix: list[Segment]; dynamic: list[Segment]
    token_report: dict[ContextKind, int]; dropped: list[DroppedItem]
    manifest: list[Provenance]

class ContextBuilder(Protocol):
    async def build(self, ctx: ExecutionContext, spec: ContextSpec,
                    extra: Sequence[ContextItem] = ()) -> ContextPacket: ...
```

See [04-context-and-state.md](04-context-and-state.md).

---

## 8. Policy

```python
class PolicyInput(BaseModel, frozen=True):
    action: Literal["run.start", "dispatch", "delegate", "tool.invoke",
                    "context.read", "state.write", "response.emit"]
    principal: PrincipalClaims             # no raw subject
    app: AppId; unit: UnitId
    resource: ResourceDescriptor
    attributes: dict[str, str | int | bool]

class PolicyDecision(BaseModel, frozen=True):
    effect: Literal["allow", "deny", "require_confirmation", "require_step_up"]
    reason_code: str
    obligations: list[Obligation] = []     # redact, mask, audit level

class PolicyEngine(Protocol):
    def decide(self, i: PolicyInput) -> PolicyDecision: ...   # sync, in-process, no I/O
```

---

## 9. Dispatch

```python
class DispatchDecision(BaseModel):
    unit: UnitId; unit_version: SemVer
    strategy: str                          # "fixed" | "rules" | "classifier" | "llm" | "sticky"
    confidence: float | None
    candidates: list[tuple[UnitId, float]] = []
    clarify: InputRequest | None = None

class DispatchStrategy(Protocol):
    async def select(self, ctx: ExecutionContext) -> DispatchDecision | None: ...
    # None = abstain, so the next strategy in the chain runs
```

---

## 10. Telemetry and evaluation

```python
class Telemetry(Protocol):
    def span(self, name: str, kind: SpanKind, attrs: SafeAttributes) -> ContextManager[Span]: ...
    def inject(self, headers: MutableMapping[str, str]) -> None: ...   # traceparent only
    def metric(self, name: MetricName, value: float, labels: BoundedLabels) -> None: ...

class SafeAttributes(Mapping[str, AttrValue]):
    """Construction validates: key allowlisted, type matches, not Sensitive, length capped."""

class Validator(Protocol):                 # synchronous; may block or replace the response
    def check(self, result: AgentResult, evidence: list[Citation]) -> ValidatorResult: ...

class EvalRecord(BaseModel):
    ids: ExecutionIds; versions: VersionStamp; entrypoint: DispatchDecision
    tool_calls: list[ToolCallSummary]; validators: list[ValidatorResult]
    response_redacted: str; usage: Usage; latency: LatencyBreakdown; status: RunStatus

class EvaluationHook(Protocol):            # asynchronous; never blocks the user
    def on_run_completed(self, record: EvalRecord) -> None: ...
```

---

## 11. Error taxonomy

| Code | Retryable | Default behavior |
|---|---|---|
| `INVALID_REQUEST` | No | Generic error |
| `UNAUTHENTICATED`, `AUTH_LEVEL_INSUFFICIENT` | No | `input.required{auth}` |
| `POLICY_DENIED`, `NOT_FOUND_OR_FORBIDDEN` | No | Neutral "can't access that" message |
| `CONFIRMATION_REQUIRED` | No | `input.required{confirm}` |
| `DEADLINE_EXCEEDED`, `CANCELLED` | No | Partial result or none |
| `BUDGET_EXCEEDED`, `LOOP_DETECTED` | No | Best partial result |
| `RATE_LIMITED`, `DEPENDENCY_TIMEOUT`, `DEPENDENCY_UNAVAILABLE` | Yes (bounded) | Degraded result |
| `MODEL_UNAVAILABLE`, `MODEL_OUTPUT_INVALID` | Fallback alias, one repair attempt, or template | Degraded result |
| `INTERNAL` | No | Generic error with reference code |

## 12. Contract versioning

- Contracts are published as a package and exported as JSON Schema.
- Additive changes are minor versions. Removals or semantic changes are major versions, with one overlap release.
- Persisted data (`AgentResult`, session events) carries `schema_version`.
