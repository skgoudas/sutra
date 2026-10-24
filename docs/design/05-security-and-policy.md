# Security and Policy

## 1. Threat model (summary)

| Threat | Primary control | Secondary control |
|---|---|---|
| Caller impersonates another user | Signed principal assertion verified at ingress; identity never taken from the body | Domain APIs verify delegated tokens |
| Confused deputy (an agent uses broad service rights for a user) | Delegated, audience-scoped tokens via token exchange | Bound arguments; resource bindings |
| Prompt injection via user input, retrieved docs or tool output | Tool allowlist + bound args + policy in `ToolExecutor` (independent of the model) | Trust labelling and fencing; safety screening (for example Model Armor [V]) |
| Data exfiltration through model output | Result projection, output validators, redaction obligations | Restricted logging; no content in spans |
| Cross-user data leakage via caches or state | Scoped cache keys, authorization recheck on read, per-conversation ownership checks | Isolation tests in CI |
| Runaway cost or loops | Budgets, loop guard, delegation limits | Per-principal rate limits at the edge |
| Unauthorized side effects | Confirmation tokens, `side_effects` declaration, write audit | Kill switches per tool |
| Supply chain (MCP servers, third-party agents) | Pinned tool names and versions; no dynamic discovery in production; A2A callee authorization | Egress allowlist |

## 2. Identity

### Principal kinds

- `end_user`
- `employee`
- `service`
- `anonymous`

### Auth levels (app-configurable ladder)

`anonymous` < `verified` < `authenticated` < `step_up`

### Flow

1. The edge authenticates the user with the existing IdP.
2. The edge (or STS) issues a **short-lived principal assertion**:
   - signed JWT;
   - `aud` = the app runtime;
   - `exp` ≤ 5 minutes;
   - claims: `sub`, `tenant`, `auth_level`, `amr`, `scopes`.
3. Ingress verifies the signature, audience, expiry and tenant. It then binds `conversation_id` to `subject_hmac` (ownership check on every turn).
4. `Principal.subject` is a `Sensitive` type. It cannot be logged, put in a span or sent to a model.

## 3. Tool authorization patterns

| Pattern | Use when | Status |
|---|---|---|
| **Delegated token (RFC 8693 token exchange)**: `sub` = user, `act` = runtime, single audience, minimal scopes, TTL ≤ 2 minutes | Any tool touching user-owned data | **Default** |
| **Service identity + signed user context**, verified by the API | The API cannot accept delegated tokens yet | Interim only, with a tracked exception per API |
| **Service identity only** | Public or app-level data with no user dimension (for example a public catalog or docs search) | Allowed; `data_classification` must be public or internal |
| **None** | Pure functions (calculators, formatters) | Allowed |
| Broad service account + an unsigned user-ID header or argument | n/a | **Prohibited** |

**Rules:**

- Credentials are acquired only inside `ToolExecutor.invoke`.
- Exchanged tokens are cached per (principal, audience) for their lifetime, and never shared across principals.
- Arguments that identify the user (for example `user_id`) are **bound arguments**. They are injected from the principal and never model-visible.
- A downstream "not found" and "forbidden" are both surfaced as `NOT_FOUND_OR_FORBIDDEN`.

## 4. Policy engine

- **In-process, synchronous, deny by default** (V1). Rules are declarative YAML in the app bundle, compiled to predicates at startup and unit-tested in CI.
- Evaluated at these points:
  - `run.start`
  - `dispatch`
  - `delegate`
  - `tool.invoke`
  - `context.read`
  - `state.write`
  - `response.emit`
- Effects:
  - `allow`
  - `deny`
  - `require_confirmation`
  - `require_step_up`
- **Obligations** are instructions attached to a decision: redact fields, mask values, set the audit level.
- **Bundle integrity.** If the policy bundle fails to load or verify, the service is not ready (fail closed).
- Domain authorization stays in domain APIs. Harness policy is defense in depth.

### Must be enforced synchronously in the runtime

| Policy | Enforcement point |
|---|---|
| Auth level for a unit or tool | Dispatcher, `ToolExecutor` |
| Tool allowlist | `ToolExecutor` + ADK `before_tool` plugin |
| Scopes, audience, resource binding | `ToolExecutor` + token exchange |
| Confirmation for writes | `ToolExecutor` |
| Budgets and loop guard | `BudgetMeter` |
| Kill switches (app, unit, tool, model alias) | Dispatcher, `ToolExecutor`, `ModelClient` |
| Input and output safety screening | Ingress, before/after-model plugins |
| Redaction obligations | `Telemetry`, `SafeAttributes`, collector processor |

## 5. Prompt-injection posture

1. **Assume the model can be manipulated.** Every security property must hold even if the model issues the worst possible tool call allowed by its allowlist.
2. Keep allowlists minimal per unit. Read and write tools are split across units where practical.
3. Untrusted content is fenced and labelled. Tool results are projected to whitelisted fields.
4. Writes need a confirmation token generated by the runtime, not by the model.
5. Safety screening runs on input and on retrieved or tool text before model calls (configurable per app).

## 6. Data handling

| Data | Spans and metrics | App logs | Interaction log (restricted) | Audit |
|---|---|---|---|---|
| Raw user identifier | Never | Never | Never (HMAC only) | HMAC + reference to the IdP subject |
| User text and model output | Never | Never | Redacted | Not stored |
| Tool arguments and results | Shape and outcome only | Shape only | Redacted, projected | For writes: canonical args hash + outcome |
| Tokens and secrets | Never | Never | Never | Never |

- Redaction happens twice: `SafeAttributes` in-process, and a redaction processor in the collector.
- Retention per data class is set by the privacy team [A: input needed].

## 7. Secrets and keys

- Workload identity; no static keys.
- Secret Manager for third-party credentials.
- KMS for signing keys (assertions, confirmation tokens).
- Rotation: assertion keys via JWKS with overlap; HMAC keys versioned, with the key ID embedded.
