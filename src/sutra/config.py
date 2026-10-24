"""Load a versioned app config bundle (``apps/<app>/config``) and build a Runtime from it.

Layout::

    app.yaml            id, deadlines, fallback text, dispatch rules, default unit
    models.yaml         aliases: [{alias, targets: [...], max_output_tokens}]
    units/*.yaml        UnitDefinition fields + ``entrypoint: "pkg.module:build"``
    policies/*.yaml     {version, rules: [...]}
    prompts/*.md|*.txt  prompt id = file stem

Requires the ``yaml`` extra. Bundle files are trusted, code-reviewed configuration.
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from sutra.contracts.models import ModelAlias
from sutra.contracts.tools import ToolDefinition
from sutra.contracts.units import UnitDefinition
from sutra.dispatch import Rule, RulesDispatch, StickyDispatch
from sutra.harness import ConfigError, build_runtime
from sutra.policy.engine import PolicyDocument, PolicyEngine
from sutra.runtime.runtime import Runtime, RuntimeSettings
from sutra.runtime.unit import Unit


class AppConfig(BaseModel):
    id: str
    default_unit: str
    max_deadline_ms: int = 30000
    fallback_text: str = "Sorry, I can't help with that right now. Please try again."
    eval_sample_rate: float = 1.0
    sticky_dispatch: bool = True
    dispatch_rules: list[Rule] = Field(default_factory=list)


class UnitManifest(UnitDefinition):
    entrypoint: str = Field(pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


@dataclass(frozen=True)
class BuildContext:
    """Passed to unit factories: ``def build(bc: BuildContext) -> Unit``."""

    definition: UnitDefinition
    prompts: Mapping[str, str]
    tools: Mapping[str, ToolDefinition]  # allowlisted tools only

    def prompt(self, prompt_id: str) -> str:
        return self.prompts[prompt_id]


@dataclass(frozen=True)
class Bundle:
    root: Path
    app: AppConfig
    units: list[UnitManifest]
    models: list[ModelAlias]
    policies: list[PolicyDocument]
    prompts: dict[str, str]
    hash: str


def _read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _bundle_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        h.update(p.relative_to(root).as_posix().encode() + b"\x00" + p.read_bytes() + b"\x00")
    return h.hexdigest()[:16]


def load_bundle(path: str | Path) -> Bundle:
    root = Path(path).resolve()
    if not (root / "app.yaml").is_file():
        raise ConfigError(f"{root}: app.yaml not found")
    try:
        app = AppConfig.model_validate(_read_yaml(root / "app.yaml"))
        models_doc = _read_yaml(root / "models.yaml") if (root / "models.yaml").is_file() else {}
        models = [ModelAlias.model_validate(a) for a in models_doc.get("aliases", [])]
        units = [UnitManifest.model_validate(_read_yaml(p)) for p in sorted((root / "units").glob("*.yaml"))]
        policies = [PolicyDocument.model_validate(_read_yaml(p))
                    for p in sorted((root / "policies").glob("*.yaml"))]
    except (ValueError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid bundle {root}: {exc}") from None
    prompts = {p.stem: p.read_text(encoding="utf-8")
               for p in sorted((root / "prompts").glob("*")) if p.suffix in {".md", ".txt"}}
    if not units:
        raise ConfigError(f"{root}: no units defined")
    return Bundle(root, app, units, models, policies, prompts, _bundle_hash(root))


def _import(entrypoint: str) -> Callable[[BuildContext], Unit]:
    module, _, attr = entrypoint.partition(":")
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"cannot import unit entrypoint {entrypoint}: {exc}") from None


def runtime_from_bundle(
    bundle: Bundle,
    *,
    tools: Iterable[ToolDefinition] = (),
    **kw: Any,
) -> Runtime:
    """Instantiate units via their entrypoints and build a validated Runtime."""
    tools = list(tools)
    tool_map = {t.name: t for t in tools}
    units: list[Unit] = []
    for manifest in bundle.units:
        definition = UnitDefinition.model_validate(manifest.model_dump(exclude={"entrypoint"}))
        bc = BuildContext(definition=definition, prompts=bundle.prompts,
                          tools={n: tool_map[n] for n in definition.tools if n in tool_map})
        unit = _import(manifest.entrypoint)(bc)
        if unit.definition.ref != definition.ref:
            raise ConfigError(f"entrypoint {manifest.entrypoint} returned unit {unit.definition.ref}, "
                              f"expected {definition.ref}")
        units.append(unit)
    by_id = {u.definition.id: u for u in units}
    if bundle.app.default_unit not in by_id:
        raise ConfigError(f"default_unit {bundle.app.default_unit!r} is not defined")
    strategies: list[Any] = [StickyDispatch()] if bundle.app.sticky_dispatch else []
    if bundle.app.dispatch_rules:
        strategies.append(RulesDispatch(bundle.app.dispatch_rules))
    settings = RuntimeSettings(app_id=bundle.app.id, bundle_hash=bundle.hash,
                               max_deadline_ms=bundle.app.max_deadline_ms,
                               fallback_text=bundle.app.fallback_text,
                               eval_sample_rate=bundle.app.eval_sample_rate)
    return build_runtime(
        app_id=bundle.app.id,
        units=[by_id[bundle.app.default_unit], *(u for u in units if u.definition.id != bundle.app.default_unit)],
        tools=tools,
        models=bundle.models,
        dispatch=strategies,
        default_unit=bundle.app.default_unit,
        prompts=bundle.prompts,
        policy=kw.pop("policy", None) or PolicyEngine(bundle.policies),
        settings=settings,
        **kw,
    )
