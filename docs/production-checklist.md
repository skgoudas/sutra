# Production checklist

Sutra 0.1 is the *minimum viable harness*. Its controls are enforced in code and tested, but some stores are still
in-memory. Go through this list before you serve real users.

## Known limitations in 0.1

| Area | Limitation | Consequence | What to do |
|---|---|---|---|
| Session history | `InMemorySessionStore` | History is lost on restart and is not shared between instances. | Run one instance, or implement the `SessionStore` protocol on a shared database. |
| Idempotency | `InMemoryIdempotencyStore` | Retries that reach a different instance run the tool again. | Same as above (`IdempotencyStore` protocol). |
| Conversation lock | `InMemoryConversationLock` | Concurrent turns of the same conversation on different instances are not serialized. | Same as above (`ConversationLock` protocol). |
| Confirmation tokens | Single-use tracking is per process | A token could be replayed once on each other instance before it expires. | Run one instance, or back confirmation replay tracking with a shared store. |
| Results | `InMemoryResultStore` | Results are not retrievable after a restart. | Implement `ResultStore`. |
| Server | FastAPI adapter | Not hardened for direct internet exposure. It has no rate limiting or CORS policy. | Put it behind an authenticating gateway or load balancer. |

The example Cloud Run manifest pins `maxScale: 1` for these reasons.

## Identity

- [ ] Use `JwtAssertionVerifier` with `jwks_url` and asymmetric algorithms (`RS256`, `ES256`). Use shared HS256 keys
      only for local development.
- [ ] Set the `audience` and `issuer` to values only your front door issues.
- [ ] Use a `SubjectHasher` key of at least 32 random bytes from a secret manager. Keep it stable: rotating it changes
      every `subject_hmac` and so orphans existing sessions.
- [ ] Set `min_auth_level` on units and `ToolAuth.min_auth_level` on tools that touch personal data.

## Tools

- [ ] Every identity-scoped argument (user id, tenant) is a `bound_args` entry, never a model-supplied field.
- [ ] Every write tool has `side_effect="write"`. Writes with user-visible consequences have
      `requires_confirmation=True`.
- [ ] The `ConfirmationService` secret is at least 32 random bytes, is held in a secret manager and stays stable across
      revisions.
- [ ] `result_projection` limits what the model sees from large or sensitive results.
- [ ] Timeouts and retries are set for each downstream system. Write tools should only be retried if they are
      idempotent.

## Units and models

- [ ] Each unit lists only the tools, model aliases and delegates it needs.
- [ ] `ExecutionLimits` (steps, calls, tokens, cost, timeout) are tuned for the use case. The defaults are
      conservative.
- [ ] Model aliases have a fallback target where availability matters.
- [ ] A `PriceCard` is configured so that cost budgets and `sutra.model.cost_usd_micros` are meaningful.

## Policy

- [ ] Policy documents are versioned and reviewed like code. `PolicyEngine.version` appears in every result.
- [ ] There are rules for channel- or tenant-specific restrictions (for example, read-only channels).

## Observability

- [ ] Install the `otel` extra and use `OTelTelemetry` with an exporter for your backend.
- [ ] Add service name, version and environment labels to all spans and metrics (see
      [examples/it_helpdesk_server.py](../examples/it_helpdesk_server.py)).
- [ ] Alert on `sutra.runs` with failed status, `sutra.run.latency_ms` p95, tool error rate and cost.
- [ ] Configure an `AuditSink` that writes to durable, access-controlled storage.
- [ ] Tests use `InMemoryTelemetry(strict=True)` so that a new attribute cannot leak content.

## Evaluation

- [ ] An offline evaluation suite with the scripted provider runs in CI for every change to prompts, tools or
      policies.
- [ ] Online validators and a redacting `EvalSink` are configured where you need production quality monitoring.

## Deployment

- [ ] The container runs as a non-root user and installs only the extras it needs.
- [ ] Secrets come from a secret manager, not from the image or plain environment files.
- [ ] `/healthz` and `/readyz` are wired to your platform's probes. `/readyz` reports the loaded bundle hash.
