"""``build_runtime``: assemble a Runtime from parts, validating configuration at startup."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from sutra.context.builder import ContextBuilder, ContextProvider
from sutra.contracts.models import ModelAlias
from sutra.contracts.tools import ToolDefinition
from sutra.dispatch import Dispatcher, DispatchStrategy, FixedDispatch, StickyDispatch
from sutra.eval.core import EvalSink, Redactor, Validator
from sutra.models.client import ModelClient, ModelProvider
from sutra.policy.engine import PolicyEngine
from sutra.runtime.context import Services
from sutra.runtime.events import FlagProvider, StaticFlags
from sutra.runtime.runtime import Runtime, RuntimeSettings
from sutra.runtime.stores import (
    ConversationLock,
    IdempotencyStore,
    InMemoryConversationLock,
    InMemoryResultStore,
    InMemorySessionStore,
    ResultStore,
    SessionStore,
)
from sutra.runtime.unit import Unit
from sutra.telemetry.core import NoopTelemetry, Telemetry
from sutra.tools.audit import AuditSink
from sutra.tools.breaker import BreakerRegistry
from sutra.tools.confirmation import ConfirmationService
from sutra.tools.credentials import CredentialBroker
from sutra.tools.executor import ToolExecutor


class ConfigError(ValueError):
    pass


def build_runtime(
    *,
    app_id: str,
    units: Sequence[Unit],
    tools: Iterable[ToolDefinition] = (),
    models: Iterable[ModelAlias] = (),
    providers: Mapping[str, ModelProvider] | None = None,
    dispatch: Sequence[DispatchStrategy] | None = None,
    default_unit: str | None = None,
    context_providers: Iterable[ContextProvider] = (),
    prompts: Mapping[str, str] | None = None,
    policy: PolicyEngine | None = None,
    telemetry: Telemetry | None = None,
    flags: FlagProvider | None = None,
    credentials: CredentialBroker | None = None,
    confirmations: ConfirmationService | None = None,
    audit: AuditSink | None = None,
    idempotency: IdempotencyStore | None = None,
    sessions: SessionStore | None = None,
    results: ResultStore | None = None,
    lock: ConversationLock | None = None,
    validators: Sequence[Validator] = (),
    eval_sink: EvalSink | None = None,
    redactor: Redactor | None = None,
    settings: RuntimeSettings | None = None,
) -> Runtime:
    """Build a Runtime. Raises ``ConfigError`` on any inconsistency (fail at startup, not per request)."""
    if not units:
        raise ConfigError("at least one unit is required")
    if settings is not None and settings.app_id != app_id:
        raise ConfigError("settings.app_id does not match app_id")
    telemetry = telemetry or NoopTelemetry()
    flags = flags or StaticFlags()
    policy = policy or PolicyEngine()
    breakers = BreakerRegistry()
    unit_map: dict[str, Unit] = {}
    for u in units:
        if u.definition.id in unit_map:
            raise ConfigError(f"duplicate unit {u.definition.id}")
        unit_map[u.definition.id] = u

    try:
        executor = ToolExecutor(tools, policy=policy, telemetry=telemetry, flags=flags, credentials=credentials,
                                confirmations=confirmations, idempotency=idempotency, audit=audit,
                                breakers=breakers, app_id=app_id)
        model_client = ModelClient(models, dict(providers or {}), telemetry=telemetry, flags=flags,
                                   breakers=breakers)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    context = ContextBuilder(context_providers, prompts=prompts, telemetry=telemetry)

    errors: list[str] = []
    for uid, u in unit_map.items():
        d = u.definition
        errors += [f"unit {uid}: unknown tool {t!r}" for t in d.tools if t not in executor.names]
        errors += [f"unit {uid}: unknown model alias {m!r}" for m in d.models if m not in model_client.aliases]
        errors += [f"unit {uid}: unknown delegate {x!r}" for x in d.delegates_to if x not in unit_map]
        try:
            context.validate_spec(d.context, f"unit {uid}")
        except ValueError as exc:
            errors.append(str(exc))

    default = default_unit or units[0].definition.id
    strategies = list(dispatch) if dispatch is not None else (
        [FixedDispatch(default)] if len(units) == 1 else [StickyDispatch()])
    dispatcher = Dispatcher(strategies, default)
    errors += [f"dispatch references unknown unit {x!r}" for x in dispatcher.unit_ids() if x not in unit_map]
    if errors:
        raise ConfigError("; ".join(errors))

    services = Services(app_id=app_id, tools=executor, models=model_client, context=context, policy=policy,
                        telemetry=telemetry, flags=flags, units=unit_map)
    return Runtime(
        settings=settings or RuntimeSettings(app_id=app_id),
        services=services,
        dispatcher=dispatcher,
        sessions=sessions or InMemorySessionStore(),
        results=results or InMemoryResultStore(),
        lock=lock or InMemoryConversationLock(),
        validators=validators,
        eval_sink=eval_sink,
        redactor=redactor,
    )
