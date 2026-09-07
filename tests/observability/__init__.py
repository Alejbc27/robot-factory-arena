"""Observability unit tests (isolated; no frozen imports)."""

import importlib.util
import sys
from pathlib import Path

# ``unittest discover -s tests`` imports this subpackage as top-level
# ``observability``, shadowing the runtime package of the same name. Load the
# runtime package once under a private alias and forward its public API so
# ``from observability import ...`` binds to the real code under every
# invocation style (discover and targeted module runs).
_RUNTIME_ALIAS = "observability_runtime"
_runtime = sys.modules.get(_RUNTIME_ALIAS)
if _runtime is None:
    _init = Path(__file__).resolve().parents[2] / "observability" / "__init__.py"
    _spec = importlib.util.spec_from_file_location(_RUNTIME_ALIAS, str(_init))
    assert _spec is not None and _spec.loader is not None  # package init always loads
    _runtime = importlib.util.module_from_spec(_spec)
    sys.modules[_RUNTIME_ALIAS] = _runtime
    _spec.loader.exec_module(_runtime)

ArtifactError = _runtime.ArtifactError
REASONS = _runtime.REASONS
SkipRecord = _runtime.SkipRecord
artifacts = _runtime.artifacts
canonical_json_bytes = _runtime.canonical_json_bytes
digest_json = _runtime.digest_json
ids = _runtime.ids
load_launch = _runtime.load_launch
load_leaderboard = _runtime.load_leaderboard
model = _runtime.model
assemble_items = _runtime.assemble_items
build_trace_records = _runtime.build_trace_records
plain = _runtime.plain
sha256_digest = _runtime.sha256_digest
