# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the
full conventions.

## Commands

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"   # install with all extras and test tools
.venv/bin/python -m pytest -q                           # run the full suite (offline, a few seconds)
.venv/bin/python -m pytest tests/test_tools.py -q       # run one file
.venv/bin/python examples/leave_assistant.py            # smallest end-to-end example
```

Always run the full test suite before you finish a change.

## Layout

- `src/sutra/contracts/`: pydantic contracts. They are the public API surface, so change them with care.
- `src/sutra/runtime/`: `Runtime`, `ExecutionContext`, budgets, deadlines, stores.
- `src/sutra/tools/`: `@tool`, `ToolExecutor`, confirmation, credentials, breakers, audit.
- `src/sutra/models/`: `ModelClient` and providers.
- `src/sutra/context/`, `policy/`, `identity/`, `telemetry/`, `eval/`, `config.py`, `dispatch.py`, `harness.py`.
- `src/sutra/engine_adk/`: Google ADK adapter. `src/sutra/server/`: FastAPI adapter.
- `src/sutra/testing/`: offline test kit (`build_test_runtime`, `ScriptedModelProvider`, `make_request`, `principal`).
- `examples/`: these are on the pytest path and imported by tests, so do not break them.
- `docs/design/`: design documents. `docs/*.md`: user guides.

## Invariants (do not break)

1. **Choke points.** All model calls go through `ModelClient`, all tool calls through `ToolExecutor`, all context
   through `ContextBuilder`, and all authorization through `PolicyEngine`. Never call a provider or a tool handler
   directly from unit or engine code.
2. **Deny by default.** A unit can only use tools, model aliases and delegates listed in its `UnitDefinition`.
3. **Identity.** Identity comes only from the verified `Principal`. Never read it from the request body or model
   output. Inject it into tools with `bound_args`. Bound args must never appear in the model-visible tool schema.
4. **Confirmation.** Tools with `requires_confirmation=True` must not run without a matching, unexpired, unused token.
5. **Telemetry.** Only `sutra.*` and `gen_ai.*` scalar attributes. Never emit prompt or response text, tool args,
   raw subjects, `SecretStr` values or exception messages. `InMemoryTelemetry(strict=True)` in tests enforces this.
6. **Optional dependencies.** `import sutra` must work with only `pydantic` installed. Import extras
   (`google.adk`, `fastapi`, `opentelemetry`, `jwt`, `yaml`, `google.genai`) lazily inside their adapter modules.
7. **No framework types in contracts.** ADK, OTel and FastAPI types stay inside their adapters.

## Writing tests

- Use `sutra.testing.build_test_runtime([...units], tools=[...])` and script model responses with
  `text_response(...)` and `tool_call_response(name, args)`.
- Add negative tests for any security-relevant change (denied tool, missing confirmation, insufficient auth level,
  rejected attribute).
- Keep tests offline. Do not add tests that need cloud credentials.

## Do not

- Add new runtime dependencies to `[project].dependencies`. Use an optional extra instead.
- Commit secrets, real tokens, internal hostnames or company-specific data. Use `example.com`/`corp.example` domains.
- Edit `CHANGELOG.md` release sections. Add entries under **Unreleased** only.
