from __future__ import annotations

import ast
import os
from pathlib import Path

from sutra.config import load_bundle, runtime_from_bundle
from sutra.testing import ScriptedModelProvider, make_request, principal, text_response, tool_call_response
from sutra.tools import ConfirmationService
from hello_app.tools import TOOLS

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "examples" / "hello_app" / "config"


async def test_example_bundle_runs_offline():
    bundle = load_bundle(BUNDLE)
    assert bundle.app.id == "hello-app" and len(bundle.hash) == 16
    provider = ScriptedModelProvider([
        tool_call_response("kb__search", {"query": "store hours"}),
        text_response("We are open 9-9."),
    ])
    runtime = runtime_from_bundle(bundle, tools=TOOLS, providers={"vertex": provider},
                                  confirmations=ConfirmationService(os.urandom(32)))
    result = await runtime.run(make_request("hours?", app_id="hello-app"), principal())
    assert result.status == "completed", result.error
    assert result.versions.bundle_hash == bundle.hash
    assert provider.requests[0][1].system.startswith("You are a concise help-center assistant.")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


ALLOWED_GOOGLE = {"engine_adk", "models/google_genai.py", "models/genai_translate.py"}


def test_vendor_sdks_only_behind_adapters():
    src = ROOT / "src" / "sutra"
    offenders = []
    for path in src.rglob("*.py"):
        rel = path.relative_to(src).as_posix()
        if any(rel.startswith(a) for a in ALLOWED_GOOGLE):
            continue
        if any(m.startswith(("google", "vertexai")) for m in _imports(path)):
            offenders.append(rel)
    assert offenders == []


def test_contracts_depend_only_on_stdlib_and_pydantic():
    for path in (ROOT / "src" / "sutra" / "contracts").rglob("*.py"):
        third_party = {m.split(".")[0] for m in _imports(path)} - {
            "__future__", "pydantic", "sutra", "datetime", "enum", "typing", "hashlib", "json", "os",
            "secrets", "time", "collections", "dataclasses", "re", "uuid", "base64", "abc"}
        assert not third_party, (path.name, third_party)
