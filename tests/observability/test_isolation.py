"""Unit C isolation tests (task 4): AST/import guards over the three trees.

Scans every applicable Python file under the frozen ``robot_factory_arena/`` and
``harness/`` trees and the runtime ``observability/`` package (never the test
package) and proves:

- no frozen source imports ``observability`` in any form;
- runtime ``observability`` imports only the Python standard library (absolute
  import roots must be stdlib names; relative imports are its own package), so
  no Langfuse SDK and no third-party dependency can hide there;
- frozen test paths stay runnable with an import blocker installed, i.e. they
  never require the observability layer to exist or be invoked.

No LLM, no live service, no retained process (the subprocess result is fully
consumed and awaited).
"""

import ast
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FROZEN_TREES = (REPO_ROOT / "robot_factory_arena", REPO_ROOT / "harness")
RUNTIME_TREE = REPO_ROOT / "observability"
STDLIB_ROOTS = frozenset(sys.stdlib_module_names)

# Runs one frozen unittest module under a meta_path blocker that raises on any
# attempt to import the observability package: the module passing proves it
# neither imports nor requires observability at any import time.
_RUNNER_TEMPLATE = (
    "import sys\n"
    "class _Block:\n"
    "    def find_spec(self, fullname, path=None, target=None):\n"
    "        if fullname.split('.')[0] == {package!r}:\n"
    "            raise ImportError({package!r} + ' is forbidden here')\n"
    "sys.meta_path.insert(0, _Block())\n"
    "import unittest\n"
    "unittest.main(module=None, argv=['x', '-q', {module!r}])\n"
)


def _python_files(*trees):
    """Every project ``*.py`` under the given trees, deterministic order."""
    for tree in trees:
        for path in sorted(tree.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def _import_roots(path):
    """Top-level module names imported by one file (relative imports skipped)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _run_blocked(module):
    """Run ``module`` under the observability import blocker; return the result."""
    script = _RUNNER_TEMPLATE.format(package="observability", module=module)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


class TestFrozenTreesNeverImportObservability(unittest.TestCase):
    def test_no_frozen_source_imports_observability(self):
        offenders = {
            str(path.relative_to(REPO_ROOT))
            for path in _python_files(*FROZEN_TREES)
            if "observability" in _import_roots(path)
        }
        self.assertEqual(offenders, set())

    def test_blocker_actually_blocks_observability(self):
        """Sanity: the guard raises when observability IS imported."""
        blocker = _RUNNER_TEMPLATE.format(
            package="observability", module="tests.test_harness_isolation"
        ).split("import unittest")[0]
        script = blocker + (
            "try:\n"
            "    import observability\n"
            "except ImportError:\n"
            "    raise SystemExit(3)\n"
            "raise SystemExit(0)\n"
        )
        blocked = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(blocked.returncode, 3)


class TestRuntimePackageIsStdlibOnly(unittest.TestCase):
    def test_runtime_imports_are_stdlib_or_relative(self):
        foreign = {
            str(path.relative_to(REPO_ROOT)): sorted(_import_roots(path) - STDLIB_ROOTS)
            for path in _python_files(RUNTIME_TREE)
            if _import_roots(path) - STDLIB_ROOTS
        }
        self.assertEqual(foreign, {})

    def test_no_langfuse_sdk_or_frozen_import_in_runtime(self):
        forbidden = {"langfuse", "robot_factory_arena", "harness"}
        offenders = {
            str(path.relative_to(REPO_ROOT)): sorted(_import_roots(path) & forbidden)
            for path in _python_files(RUNTIME_TREE)
            if _import_roots(path) & forbidden
        }
        self.assertEqual(offenders, {})


class TestFrozenSuiteIndependentOfObservability(unittest.TestCase):
    def test_frozen_isolation_module_passes_with_observability_blocked(self):
        done = _run_blocked("tests.test_harness_isolation")
        self.assertEqual(
            done.returncode,
            0,
            f"frozen tests must run without observability:\n{done.stderr}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
