"""Static contract tests for the Langfuse PR smoke workflow and entrypoint.

Docker-free text/AST checks pinning the approved CI-LF-01 gate: exactly
``pull_request`` (with the explicit smoke-affecting path allowlist) and
``workflow_dispatch``; never ``schedule`` or ``push``; no version input; least
privilege (``contents: read``); immutable 40-hex action SHAs with dropped
checkout credentials; Python 3.14; a 40-minute bounded job; per-PR/run
concurrency that queues instead of cancels; exactly one no-argument smoke
invocation; and no secrets, artifact-publishing, issue, or branch-protection
automation. The check stays an optional red check: no ``continue-on-error``
and no required-check or promotion behavior appears anywhere.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "langfuse-smoke.yml"
SCRIPT = ROOT / "scripts" / "langfuse_smoke.py"

APPROVED_PATHS = (
    '"observability/**"',
    '"tests/observability/**"',
    '".github/workflows/langfuse-smoke.yml"',
    '"scripts/langfuse_smoke.py"',
    '"pyproject.toml"',
    '"docs/observability.md"',
)
CHECKOUT_SHA = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
SETUP_PYTHON_SHA = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"


def _trigger(text):
    """Everything before the top-level ``permissions:`` key."""
    return text.split("\npermissions:", 1)[0]


class WorkflowTriggerContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.trigger = _trigger(cls.text)

    def test_exactly_pull_request_and_manual_dispatch(self):
        self.assertRegex(self.trigger, r"(?m)^on:\s*$")
        self.assertRegex(self.trigger, r"(?m)^  pull_request:\s*$")
        self.assertRegex(self.trigger, r"(?m)^  workflow_dispatch:\s*$")
        self.assertNotRegex(self.trigger, r"(?m)^  (schedule|push):\s*$")
        self.assertNotIn("cron", self.trigger)

    def test_exact_smoke_affecting_path_allowlist(self):
        paths_block = self.trigger.split("paths:", 1)[1]
        listed = tuple(
            line.strip().removeprefix("- ").strip()
            for line in paths_block.splitlines()
            if line.strip().startswith("- ")
        )
        self.assertEqual(listed, APPROVED_PATHS)
        for pattern in ("*.lock", "package-lock.json", "poetry.lock"):
            self.assertNotIn(pattern, self.trigger)

    def test_no_version_or_configuration_input(self):
        self.assertNotIn("workflow_call", self.trigger)
        self.assertNotRegex(self.text, r"(?i)langfuse[_-]?version")
        self.assertNotRegex(self.text, r"(?m)^\s+(inputs|with: version):")


class WorkflowSecurityAndBoundsContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_least_privilege_contents_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s{4}\w+: (write|read)")

    def test_actions_are_immutable_sha_pinned_and_drops_credentials(self):
        uses = [
            line.split("uses:", 1)[1].strip().split(" #")[0]
            for line in self.text.splitlines()
            if "uses:" in line
        ]
        self.assertEqual(uses, [CHECKOUT_SHA, SETUP_PYTHON_SHA])
        for value in uses:
            self.assertRegex(value, r"^[^@]+@[0-9a-f]{40}$")
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('python-version: "3.14"', self.text)

    def test_bounded_job_with_per_pr_queuing_concurrency(self):
        self.assertRegex(self.text, r"(?m)^  smoke:\s*$")
        self.assertRegex(self.text, r"timeout-minutes:\s*40\b")
        self.assertIn("runs-on: ubuntu-24.04", self.text)
        self.assertIn(
            "group: langfuse-smoke-${{ github.event.pull_request.number"
            " || github.run_id }}",
            self.text,
        )
        self.assertIn("cancel-in-progress: false", self.text)

    def test_one_no_argument_invocation_and_no_forbidden_automation(self):
        runs = [
            line.strip()
            for line in self.text.splitlines()
            if line.strip().startswith("run:")
        ]
        self.assertEqual(runs, ["run: python3 scripts/langfuse_smoke.py"])
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("continue-on-error", self.text)
        for forbidden in (
            "actions/github-script",
            "gh api",
            "actions/upload-artifact",
            "peter-evans",
        ):
            self.assertNotIn(forbidden, self.text)


class EntrypointContract(unittest.TestCase):
    def test_script_has_no_secret_options_and_bounded_timeout(self):
        source = SCRIPT.read_text(encoding="utf-8")
        options = re.findall(r'add_argument\(\s*"([^"\s]+)"', source)
        self.assertEqual(options, ["--compose-file", "--report", "--timeout-seconds"])
        for forbidden in ("--langfuse-version", "--public-key", "--secret-key"):
            self.assertNotIn(forbidden, options)
        self.assertIn("parser.error", source)

    def test_exit_codes_are_documented_and_mapped(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("exit_code_for", source)
        self.assertIn("raise SystemExit(main())", source)
        self.assertRegex(source, r"(?m)^import (argparse|os|sys)$")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
