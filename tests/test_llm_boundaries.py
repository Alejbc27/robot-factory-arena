"""Phase 4A provider-boundary tests (T4A-01/T4A-03, P4A-REQ-01/09).

The ``providers`` package stays outside the frozen core: an AST denylist
scans EVERY provider module for imports of ``domain``/``runtime``/``evidence``
/``verification``/``cli``; the contract and fake modules are stdlib-only with
no transport capability; and a subprocess tripwire proves importing AND
invoking the fake loads no forbidden core module and runs with credentials
absent from the environment. Contract rejections (envelope construction
raises) are distinct from policy rejections (normalized by the runner, Batch
B) — malformed provider data can never enter the envelope.
"""
import ast
import importlib
import inspect
import os
import pkgutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PROVIDERS_PACKAGE = "robot_factory_arena.providers"

FORBIDDEN_CORE_PREFIXES = (
    "robot_factory_arena.domain",
    "robot_factory_arena.runtime",
    "robot_factory_arena.evidence",
    "robot_factory_arena.verification",
    "robot_factory_arena.cli",
)

# adapter.py/fake.py are pure contract/logic: no transport capability at all.
# The live urllib transport belongs exclusively to providers.openai (AMEND-4A-1).
TRANSPORT_FREE_MODULES = (
    "robot_factory_arena.providers.adapter",
    "robot_factory_arena.providers.fake",
)
_TRANSPORT_ROOTS = {"socket", "ssl", "urllib", "http", "asyncio", "requests"}

# Exact import allowlists: the fake provider must not even import ``os``.
_MODULE_ALLOWLISTS = {
    "robot_factory_arena.providers.adapter": {
        "collections.abc",
        "dataclasses",
        "types",
        "typing",
    },
    "robot_factory_arena.providers.fake": {
        "robot_factory_arena.providers.adapter",
    },
}


def _provider_module_names():
    package = importlib.import_module(PROVIDERS_PACKAGE)
    return [
        f"{PROVIDERS_PACKAGE}.{name}"
        for finder, name, is_package in pkgutil.iter_modules(package.__path__)
    ]


def _module_imports(module_name):
    module = importlib.import_module(module_name)
    tree = ast.parse(inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


class TestProviderImportBoundaries(unittest.TestCase):
    """No provider module may reach the frozen core; AST scan covers all."""

    def test_boundary_scan_covers_every_provider_module(self):
        names = _provider_module_names()
        self.assertGreaterEqual(
            len(names), 2, "adapter and fake (at minimum) must be present"
        )
        for module_name in names:
            with self.subTest(module=module_name):
                importlib.import_module(module_name)

    def test_provider_modules_never_import_the_frozen_core(self):
        for module_name in _provider_module_names():
            imported = _module_imports(module_name)
            for forbidden in FORBIDDEN_CORE_PREFIXES:
                with self.subTest(module=module_name, forbidden=forbidden):
                    offending = [
                        name
                        for name in imported
                        if name == forbidden or name.startswith(forbidden + ".")
                    ]
                    self.assertEqual(offending, [])

    def test_contract_and_fake_modules_are_transport_free(self):
        for module_name in TRANSPORT_FREE_MODULES:
            imported = _module_imports(module_name)
            with self.subTest(module=module_name):
                for root in _TRANSPORT_ROOTS:
                    self.assertFalse(
                        any(
                            name == root or name.startswith(root + ".")
                            for name in imported
                        ),
                        (module_name, root),
                    )

    def test_provider_modules_import_only_their_allowlist(self):
        for module_name, allowed in _MODULE_ALLOWLISTS.items():
            if module_name not in sys.modules and not _exists(module_name):
                continue
            with self.subTest(module=module_name):
                extra = _module_imports(module_name) - allowed
                self.assertEqual(extra, set())

    def test_providers_package_is_importable_standalone(self):
        module = importlib.import_module(PROVIDERS_PACKAGE)
        self.assertIsNotNone(module.__doc__)


def _exists(module_name):
    try:
        importlib.import_module(module_name)
    except ImportError:
        return False
    return True


LLM_POLICY_ALLOWLIST = {
    "dataclasses",
    "robot_factory_arena.contracts.tool_call",
    "robot_factory_arena.evidence.canonical",
    "robot_factory_arena.providers.adapter",
}


class TestLLMPolicyBoundaries(unittest.TestCase):
    """T4A-04: policies/llm.py imports only the allowed contract surface.

    The task allowlist names the existing policy/tool-call contracts and the
    canonical digest authority; the provider request envelope joins it because
    P4A-REQ-02 requires LLMPolicy to construct exactly one ProviderRequest.
    No runtime, verifier, domain, or providers-sidecar import is permitted.
    """

    def test_llm_policy_imports_only_the_allowed_surface(self):
        imported = _module_imports("robot_factory_arena.policies.llm")
        extra = imported - LLM_POLICY_ALLOWLIST
        self.assertEqual(extra, set())

    def test_llm_policy_never_imports_frozen_core_or_sidecars(self):
        imported = _module_imports("robot_factory_arena.policies.llm")
        for forbidden in (
            "robot_factory_arena.domain",
            "robot_factory_arena.runtime",
            "robot_factory_arena.evidence.trace",
            "robot_factory_arena.evidence.receipt",
            "robot_factory_arena.evidence.artifact_store",
            "robot_factory_arena.verification",
            "robot_factory_arena.providers.evidence",
            "robot_factory_arena.providers.fake",
            "robot_factory_arena.providers.openai",
            "robot_factory_arena.cli",
        ):
            with self.subTest(forbidden=forbidden):
                offending = [
                    name
                    for name in imported
                    if name == forbidden or name.startswith(forbidden + ".")
                ]
                self.assertEqual(offending, [])


class TestFakeProviderSubprocessTripwire(unittest.TestCase):
    """Importing AND invoking the fake loads no core module, reads no secret."""

    def test_import_and_invoke_load_no_forbidden_core_modules(self):
        probe = (
            "import sys;"
            "from robot_factory_arena.providers.fake import FakeProvider;"
            "from robot_factory_arena.providers.adapter import ProviderRequest;"
            "provider = FakeProvider();"
            "result = provider.invoke(ProviderRequest("
            "provider='fake', model='fake-v1', request_id='request-1',"
            "observation={}, prompt='p', tool_schema={}));"
            "assert result.error is not None;"
            "assert result.error.code == 'script_exhausted', result.error.code;"
            "forbidden = sorted("
            "m for m in sys.modules"
            " if m.startswith(('robot_factory_arena.domain',"
            "'robot_factory_arena.runtime', 'robot_factory_arena.evidence',"
            "'robot_factory_arena.verification')));"
            "assert not forbidden, forbidden;"
            "print('FAKE_CORE_FREE_OK')"
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if not any(
                marker in key.upper()
                for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
            )
        }
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("FAKE_CORE_FREE_OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
