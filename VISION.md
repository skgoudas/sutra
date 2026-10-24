# Vision

Sutra is a **thin harness** for running LLM agents in production. It does not decide *what* an agent does. It
guarantees *how* any agent runs:

- with a verified identity;
- with only the tools, models and data it is allowed to use;
- within deadlines and budgets;
- with consistent telemetry, error handling and evaluation hooks;
- from versioned, reviewable configuration.

Application teams bring prompts, tools, workflows and evaluation cases. The harness brings everything else.

## Principles

1. **Thin harness, thick contracts.** The contracts between components are stable and typed. Agent internals are
   free.
2. **No base class.** A unit is a definition plus an async function, or an adapter around a framework agent.
3. **One choke point per concern.** Models, tools, context and policy each have exactly one path.
4. **Deny by default, enforced in the runtime.** Safety never depends on the model behaving.
5. **Framework behind an adapter.** Google ADK is the first engine adapter. Harness contracts do not leak framework
   types.
6. **Libraries before services.** Sutra is a library. Add a network service only when there is a concrete need.
7. **Everything versioned, everything traced.** Every result ties back to the bundle, prompt, model and policy
   versions that produced it.
8. **Simple first.** A single read-only agent should need almost no configuration. Complex orchestration is opt-in.

## Non-goals (for now)

- An agent marketplace, catalog UI or visual workflow builder.
- Open-ended autonomous multi-agent planning.
- A separate control-plane, model-gateway or agent-gateway service.
- Long-term semantic memory.
- Self-hosted inference.

## Direction

The near-term roadmap is in [docs/design/09-mvh-nfr-roadmap.md](docs/design/09-mvh-nfr-roadmap.md). Themes:

- shared, durable stores (sessions, idempotency, locks, confirmation replay) for multi-instance deployments;
- more model providers and engine adapters behind the existing contracts;
- offline evaluation tooling built on the online `EvalRecord` stream;
- hardening the server adapter for direct internet exposure.
