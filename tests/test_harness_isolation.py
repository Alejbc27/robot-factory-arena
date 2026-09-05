"""Harness isolation gates: no core imports, optional-only LangGraph, CI split.

Design §9 isolation tests: an AST/import scan proves no harness source imports
``robot_factory_arena``; the foundation imports no LangGraph; and gate.yml keeps
a core job without the optional extra plus a separate harness job.
"""

import ast
import importlib.util
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = REPO_ROOT / "harness"
GATE = REPO_ROOT / ".github" / "workflows" / "gate.yml"

# Optional extra: harness tests must skip cleanly when it is absent.
# find_spec avoids a static import the local resolver cannot satisfy.
LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None


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
        self.assertGreaterEqual(len(sources), 11)
        for expected in (
            "__init__.py",
            "state.py",
            "tournament.py",
            "subprocess_adapter.py",
            "checkpointing.py",
            "logs.py",
            "versions.py",
            "strategy_context.py",
            "evidence.py",
            "leaderboard.py",
            "graph.py",
            "interruption.py",
        ):
            self.assertIn(HARNESS_DIR / expected, sources)

    def test_no_harness_source_imports_robot_factory_arena(self):
        for path in _harness_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            bad = _imported_roots(tree) & {"robot_factory_arena"}
            self.assertEqual(bad, set(), f"{path.name} imports core: {bad}")

    def test_langgraph_imports_are_scoped_to_graph_py(self):
        # graph.py is the ONLY LangGraph home (lazy import behind the optional
        # extra); every other harness source stays stdlib-only, and NO source
        # may import the core.
        for path in _harness_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            self.assertNotIn("robot_factory_arena", _imported_roots(tree), str(path))
            if path.name != "graph.py":
                self.assertNotIn("langgraph", _imported_roots(tree), str(path))


@unittest.skipUnless(
    LANGGRAPH_AVAILABLE, "optional harness extra (langgraph) not installed"
)
class TestOptionalExtra(unittest.TestCase):
    def test_langgraph_imports_when_extra_installed(self):
        import importlib
        import importlib.metadata

        module = importlib.import_module("langgraph")
        self.assertIsNotNone(module)
        # Namespace packages expose no top-level __version__; the
        # distribution metadata is the authoritative version source.
        version = importlib.metadata.version("langgraph")
        self.assertRegex(version, r"^\d+\.\d+\.\d+")


class TestGateWorkflowSplit(unittest.TestCase):
    def test_core_job_runs_without_optional_extra(self):
        text = GATE.read_text(encoding="utf-8")
        core = re.search(r"unittest-gate:.*?(?=\n  \w[-\w]*:|\Z)", text, re.S)
        if core is None:
            self.fail("unittest-gate job missing from gate.yml")
        self.assertNotIn("harness", core.group(0))
        self.assertIn("unittest discover -s tests", core.group(0))

    def test_harness_job_installs_extra_before_harness_tests(self):
        text = GATE.read_text(encoding="utf-8")
        job = re.search(r"harness-gate:.*?(?=\n  \w[-\w]*:|\Z)", text, re.S)
        if job is None:
            self.fail("harness-gate job missing from gate.yml")
        self.assertIn("[harness]", job.group(0))
        self.assertIn("test_harness", job.group(0))

    def test_harness_gate_runs_all_harness_modules(self):
        # PR #20 blocker 4: the harness-gate job must execute every harness
        # module WITH langgraph installed, so test_build_graph_compiles
        # reports ok there instead of being skipped everywhere. Unit 4 adds
        # the R1 approval-path module to the same gate (the recovery module
        # joins in the attempt-2 slice).
        text = GATE.read_text(encoding="utf-8")
        job = re.search(r"harness-gate:.*?(?=\n  \w[-\w]*:|\Z)", text, re.S)
        if job is None:
            self.fail("harness-gate job missing from gate.yml")
        for module in (
            "tests.test_harness_foundation",
            "tests.test_harness_isolation",
            "tests.test_harness_evidence",
            "tests.test_harness_r2",
            "tests.test_harness_leaderboard",
            "tests.test_harness_r1",
        ):
            self.assertIn(module, job.group(0))


if __name__ == "__main__":
    unittest.main()
