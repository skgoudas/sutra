# Configuration and Control Plane

## 1. Principle

- The **runtime plane** executes and enforces.
- The **control plane** defines, versions, approves and distributes.
- In V1 the control plane is **Git, CI, an artifact registry and the existing feature-flag system**. It is not a service.
- The runtime never calls the control plane on the request path. It loads an immutable, content-hashed bundle at startup.

## 2. App bundle layout

```text
apps/<app>/config/
├── app.yaml            # app id, tenant(s), locales, channels, deadline caps, default limits
├── dispatch.yaml       # strategy chain, thresholds, default_unit, fallbacks, kill-switch keys
├── units/*.yaml        # UnitDefinition manifests
├── tools/*.yaml        # ToolDefinition manifests (args/result models referenced by import path)
├── context.yaml        # provider registrations, budgets, freshness classes
├── models.yaml         # alias -> provider/model/version, fallback chain, capability needs
├── policies/*.yaml     # policy rules
├── validators.yaml     # validator registrations per unit
└── prompts/**          # versioned prompt files
```

**Minimal app.** A single-unit app needs only:

- `app.yaml`
- one unit
- its tools
- `models.yaml`
- a prompt

Dispatch defaults to `fixed`.

### Example of a minimal app

Illustrative only:

```yaml
# app.yaml
id: hello-help
channels: [sse]
limits: { deadline_ms: 15000, max_model_calls: 4, max_tool_calls: 4, max_cost_usd_micros: 20000 }

# units/help_agent.yaml
id: help_agent
version: 0.1.0
kind: llm_agent
entrypoint: hello_help.units.help_agent:build
model_alias: general
prompt: prompts/help_agent.md
tools: [kb.search]
context: { providers: [conversation, kb], budgets: { retrieved: 4000, conversation: 1500 } }
min_auth_level: authenticated
side_effects: none
eval_suites: [evals/help_agent]
```

## 3. Control-plane capabilities

| Capability | V1 form | Later form (trigger) |
|---|---|---|
| Unit and tool registry | Manifests in Git, validated in CI. A generated catalog JSON ships with the bundle. | Read-only cross-app registry (3 or more apps) |
| Model aliases | `models.yaml` per app + an org-wide allowed-model list in the shared CI template | Central model catalog with approvals |
| Prompt versioning | Files in Git; hash recorded on spans | Prompt experiment UI (see ADR-007 triggers) |
| Policies | YAML + CI tests + CODEOWNERS (security) | Policy distribution service (OPA or Cedar) for multi-language apps |
| Feature flags and kill switches | Existing flag system [A]. Local TTL ≤ 30 s. Fail-safe defaults. | Same |
| Rollout | Cloud Run revisions + traffic split + flag percentages | Progressive delivery automation |
| Eval gates | CI jobs with per-unit thresholds | Eval service and dashboards |
| Change audit | PR review, signed images, deploy log | Approval workflow in a portal |
| Cost reporting | BigQuery views over telemetry | Chargeback |

## 4. Bundle lifecycle

```text
PR (code + config + prompts + policies + evals)
  → CI: schema validation · import rules · policy tests · contract tests · eval gates · security scan
  → image build with bundle baked in (bundle_sha as label and env)
  → deploy revision at 0% → shadow (optional) → canary 1% → 10% → 50% → 100%
  → rollback = shift traffic to previous revision
```

### CI validation of the bundle

- Every tool in a unit allowlist exists.
- Every write tool has `requires_confirmation`, or an approved exception.
- No raw model IDs appear outside `models.yaml`.
- Every unit has at least one eval suite.
- `data_access` is consistent with the tools' `data_classification`.
- Delegation graph: no cycles, and depth within limits.
- Policy rules compile, and their tests pass.

### Operating rules

- Emergency changes (kill switches) use flags, not redeploys.
- No hot reload of prompts or policies in V1. Provenance matters more than reload speed.

## 5. Evolution

| Stage | Control plane | Trigger |
|---|---|---|
| 1 (V1) | Git + CI + flags + revisions | n/a |
| 2 | Shared CI template and contract package used by every app | Second app |
| 3 | Read-only registry indexing all bundles | 3 or more apps, or an audit need |
| 4 | Managing control plane: approvals, policy distribution, quotas, chargeback | 5 or more apps or multiple languages, plus central governance needs and platform staffing |
