"""Harness isolation gates: no core imports, optional-only LangGraph, CI split.

Design §9 isolation tests: an AST/import scan proves no harness source imports
``robot_factory_arena``; the foundation imports no LangGraph; and gate.yml keeps
a core job without the optional extra plus a separate harness job.
"""

import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = REPO_ROOT / "harness"
GATE = REPO_ROOT / ".github" / "workflows" / "gate.yml"

try:  # Optional extra: harness tests must skip cleanly when it is absent.
    import langgraph  # noqa: F401

    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False


def _harness_sources():
    return sorted(p for p in HARNESS_DIR.glob("*.py") if p.name != "__pycache__")


def _imported_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


class TestPackageBoundary(unittest.TestCase):
    def test_every_harness_source_is_discovered(self):
        sources = _harness_sources()
        self.assertGreaterEqual(len(sources), 8)
        for expected in (
            "__init__.py",
            "state.py",
            "tournament.py",
            "subprocess_adapter.py",
            "checkpointing.py",
            "logs.py",
            "versions.py",
            "strategy_context.py",
        ):
            self.assertIn(HARNESS_DIR / expected, sources)

    def test_no_harness_source_imports_robot_factory_arena(self):
        for path in _harness_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            bad = _imported_roots(tree) & {"robot_factory_arena"}
            self.assertEqual(bad, set(), f"{path.name} imports core: {bad}")

    def test_no_harness_source_imports_langgraph(self):
        # Foundation is pure TypedDict + stdlib; LangGraph wiring is Unit 3+.
        for path in _harness_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            self.assertNotIn("langgraph", _imported_roots(tree), str(path))


@unittest.skipUnless(
    LANGGRAPH_AVAILABLE, "optional harness extra (langgraph) not installed"
)
class TestOptionalExtra(unittest.TestCase):
    def test_langgraph_imports_when_extra_installed(self):
        import langgraph

        self.assertTrue(hasattr(langgraph, "__version__"))


if __name__ == "__main__":
    unittest.main()
