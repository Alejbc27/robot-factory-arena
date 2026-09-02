"""Verifier dependency denylist tests (REQ-06, REQ-08).

T-18/T-21: the verifier's independence is enforced structurally. The closed
import allowlist for ``robot_factory_arena/verification/*.py`` is exactly:

- the Python standard library,
- ``robot_factory_arena.contracts.outcomes`` (shared frozen value objects),
- ``robot_factory_arena.evidence.canonical`` (the one shared serialization
  authority — canonical JSON/SHA-256 must be interpreted identically),
- ``robot_factory_arena.domain.task`` (the golden-frozen REQ-01 seed formulas;
  verifier and runtime must interpret a seed identically),
- sibling ``robot_factory_arena.verification.*`` modules.

Everything else — in particular ``runtime.*``, ``policies.*``,
``domain.transition``, runtime scoring, and the evidence writers — fails the
gate. Dynamic import escapes (importlib/``__import__``/``eval``/``exec``) fail
closed too. The forged-artifact test constructs mappings only.
"""

import ast
import pathlib
import unittest

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "robot_factory_arena"
VERIFICATION_ROOT = PACKAGE_ROOT / "verification"

# The closed verification import allowlist (absolute module names).
ALLOWED_PROJECT_IMPORTS = frozenset(
    {
        "robot_factory_arena.contracts.outcomes",
        "robot_factory_arena.evidence.canonical",
        "robot_factory_arena.domain.task",
    }
)
FORBIDDEN_PREFIXES = (
    "robot_factory_arena.runtime",
    "robot_factory_arena.policies",
    "robot_factory_arena.domain.transition",
    "robot_factory_arena.evidence.trace",
    "robot_factory_arena.evidence.receipt",
    "robot_factory_arena.evidence.replay",
    "robot_factory_arena.evidence.artifact_store",
    "robot_factory_arena.evidence.report",
    "robot_factory_arena.faults",
    "robot_factory_arena.cli",
)
FORBIDDEN_TRANSITION_IMPORT = "robot_factory_arena.domain.transition"
DYNAMIC_IMPORT_NAMES = frozenset({"__import__", "eval", "exec"})


def _module_name(node):
    """Absolute dotted name for an Import/ImportFrom node (None for relative)."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return None  # relative import inside the package (verification.*)
        return [node.module or ""]
    return []


def _verification_sources():
    return sorted(VERIFICATION_ROOT.glob("*.py"))


def _project_import_problems(source, filename="<synthetic>"):
    """Absolute project imports in ``source`` outside the closed allowlist.

    The single checker used by the real-source gate AND by the synthetic
    fail-closed tests, so the gate is known-good even while the real sources
    stay clean.
    """
    tree = ast.parse(source, filename=filename)
    problems = []
    for node in ast.walk(tree):
        for name in _module_name(node) or []:
            if not name.startswith("robot_factory_arena"):
                continue  # stdlib
            if name.startswith("robot_factory_arena.verification."):
                continue  # own modules
            if name in ALLOWED_PROJECT_IMPORTS:
                continue
            problems.append(name)
    return problems


class TestVerifierImportDenylist(unittest.TestCase):
    """The dependency gate fails closed on forbidden verifier imports."""

    def test_verification_modules_exist(self):
        names = {path.name for path in _verification_sources()}
        # The declared B5 module set is complete: verifier, independent
        # invariants, verifier-owned scoring (plus the package init).
        self.assertEqual(
            names,
            {"__init__.py", "verifier.py", "invariants.py", "scoring.py"},
        )

    def test_imports_stay_inside_the_closed_allowlist(self):
        for path in _verification_sources():
            problems = _project_import_problems(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
            self.assertEqual(
                problems,
                [],
                f"{path.name}: imports outside the verifier allowlist (REQ-06)",
            )

    def test_denylist_names_fail_closed(self):
        for path in _verification_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for name in _module_name(node) or []:
                    for prefix in FORBIDDEN_PREFIXES:
                        self.assertFalse(
                            name == prefix or name.startswith(prefix + "."),
                            f"{path.name}: forbidden verifier import {name!r}",
                        )
                    self.assertNotEqual(
                        name,
                        FORBIDDEN_TRANSITION_IMPORT,
                        f"{path.name}: verifier must not import the transition "
                        "authority (REQ-06 independence)",
                    )

    def test_no_dynamic_import_escape_hatches(self):
        for path in _verification_sources():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "importlib":
                    self.fail(f"{path.name}: importlib import is forbidden")
                if isinstance(node, ast.Name) and node.id in DYNAMIC_IMPORT_NAMES:
                    self.fail(f"{path.name}: dynamic {node.id!r} is forbidden")
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr
                    in {
                        "import_module",
                    }
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "importlib"
                ):
                    self.fail(f"{path.name}: importlib.import_module is forbidden")

    def test_runtime_scoring_is_never_imported(self):
        # Runtime scoring is reachable only through the runtime package; the
        # runtime ban above already fails closed. This test documents the
        # intent explicitly for the release gate (REQ-06 score ownership).
        for path in _verification_sources():
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("runtime.scoring", source)
            self.assertNotIn("runtime import", source)


    def test_denylist_fails_closed_on_synthetic_forbidden_imports(self):
        """A forbidden import appearing in a verifier module is detected.

        Proven against synthetic modules so the checker itself fails
        closed even while the real sources stay clean: every denylisted
        family (runtime, policies, domain.transition, evidence writers)
        and runtime scoring is flagged by name.
        """
        forbidden = {
            "from robot_factory_arena.runtime.tool_bay import ToolBay":
                "robot_factory_arena.runtime.tool_bay",
            "import robot_factory_arena.runtime.scoring":
                "robot_factory_arena.runtime.scoring",
            "from robot_factory_arena.policies.greedy import GreedyPolicy":
                "robot_factory_arena.policies.greedy",
            "from robot_factory_arena.domain.transition import apply":
                "robot_factory_arena.domain.transition",
            "from robot_factory_arena.evidence.trace import TraceWriter":
                "robot_factory_arena.evidence.trace",
            "from robot_factory_arena.runtime.runner import Runner":
                "robot_factory_arena.runtime.runner",
        }
        for source, expected_name in forbidden.items():
            with self.subTest(source=source):
                self.assertEqual(
                    _project_import_problems(source), [expected_name]
                )

    def test_allowlisted_imports_produce_no_problems(self):
        self.assertEqual(
            _project_import_problems(
                "from robot_factory_arena.contracts.outcomes import Counters\n"
                "from robot_factory_arena.evidence.canonical import digest_json\n"
                "from robot_factory_arena.domain.task import generate_task\n"
                "from robot_factory_arena.verification.scoring import (\n"
                "    compute_official_score,\n"
                ")\n"
                "import json\n"
            ),
            [],
        )


class TestForgedArtifactsNeedNoRuntime(unittest.TestCase):
    """Verifier tests run against forged mappings without runtime objects."""

    def test_forged_set_verifies_without_runtime_construction(self):
        # Hand-built forged artifact mappings only: a receipt claiming a score
        # the evidence cannot support is rejected without any runtime object.
        from robot_factory_arena.domain.task import generate_task
        from robot_factory_arena.evidence.canonical import digest_json
        from robot_factory_arena.verification.verifier import (
            VerificationArtifacts,
            Verifier,
        )

        task = generate_task("18").to_mapping()
        config = {
            "schema_version": 1,
            "task_version": "warehouse-v0",
            "seed": "18",
            "policy_id": "greedy-v0",
            "policy_digest": "sha256:" + "3" * 64,
            "budget": 100,
            "grants": ["move", "pick", "place"],
            "fault_tape_spec": None,
        }
        empty_log = {
            "schema_version": 1,
            "logical_run_id": "lr_" + "cd" * 32,
            "operations": [],
        }
        initial_digest = digest_json(task["initial_state"])
        trace = [
            {
                "schema_version": 1,
                "seq": 0,
                "logical_run_id": empty_log["logical_run_id"],
                "policy_step": None,
                "event": "run_started",
                "operation_key": None,
                "tool": None,
                "arguments": None,
                "state_before": None,
                "state_after": initial_digest,
                "payload": {
                    "policy_id": "greedy-v0",
                    "policy_digest": config["policy_digest"],
                    "budget": 100,
                    "grants": ["move", "pick", "place"],
                },
                "prev_event_hash": None,
            },
        ]
        previous = None
        chained = []
        for seq, record in enumerate(trace):
            body = {k: v for k, v in record.items() if k != "event_hash"}
            body["seq"] = seq
            body["prev_event_hash"] = previous
            body["event_hash"] = digest_json(body)
            chained.append(body)
            previous = body["event_hash"]
        run_finished = {
            "schema_version": 1,
            "logical_run_id": empty_log["logical_run_id"],
            "policy_step": None,
            "event": "run_finished",
            "operation_key": None,
            "tool": None,
            "arguments": None,
            "state_before": None,
            "state_after": initial_digest,
            "payload": {
                "outcome": "completed",
                "counters": {
                    "policy_steps": 0,
                    "tool_attempts": 0,
                    "committed_effects": 0,
                    "internal_retries": 0,
                    "contract_violations": 0,
                    "terminal_checks": 1,
                },
            },
            "prev_event_hash": previous,
        }
        body = {k: v for k, v in run_finished.items() if k != "event_hash"}
        body["seq"] = 1
        body["event_hash"] = digest_json(body)
        chained.append(body)
        replay_input = {
            "schema_version": 1,
            "logical_run_id": empty_log["logical_run_id"],
            "task_identity": {
                "task_version": "warehouse-v0",
                "seed": "18",
                "config_digest": digest_json(config),
            },
            "grants": ["move", "pick", "place"],
            "budget": 100,
            "fault_tape": None,
            "operations": [],
        }
        result = Verifier().verify(
            VerificationArtifacts(
                task=task,
                config=config,
                trace_records=tuple(chained),
                final_state=task["initial_state"],
                operation_log=empty_log,
                replay_input=replay_input,
                fault_tape=None,
                receipt=None,
                boundaries={},
            )
        )
        # Nothing was ever delivered: the forged "completed" claim must not
        # stand, and no official score exists for inconsistent evidence.
        self.assertFalse(result.valid)
        self.assertEqual(result.outcome, "unresolved_outcome")
        self.assertIsNone(result.verified_score)
        self.assertIn("artifact_mismatch", sorted(v.code for v in result.violations))
