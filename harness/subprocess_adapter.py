"""CLI subprocess seam: exact command, pre-start registration, result capture.

The only core interface is ``<python> -m robot_factory_arena.cli run`` plus the
sealed JSON artifacts it writes. stdout/stderr are captured as external
reference files and are never parsed for score/outcome (design §4); the single
extracted ``strategy_context_digest`` line is identity binding only.
"""

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import versions
from .strategy_context import (
    DIGEST_RE,
    atomic_write_bytes,
    canonical_json_bytes,
    sha256_digest,
)

_DIGEST_LINE = re.compile(r"^strategy_context_digest: (null|sha256:[0-9a-f]{64})$")
_REQUIRED_META = (
    "condition",
    "seed",
    "agent_id",
    "attempt_ordinal",
    "argv",
    "context_path",
    "context_digest",
)


class LaunchError(ValueError):
    """Launch registration/observation invariant violated."""


@dataclass
class RunnerResult:
    exit_code: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes


@dataclass
class LaunchResult:
    exit_code: int | None
    timed_out: bool
    stdout_ref: Path
    stderr_ref: Path
    reported_context_digest: str | None


def build_command(config, seed, context_path, core_root, fault=None):
    """Exact frozen CLI command (§7); provider/model come only from the sealed config."""
    argv = [
        sys.executable,
        "-m",
        "robot_factory_arena.cli",
        "run",
        "--seed",
        str(seed),
        "--provider",
        config["provider"],
        "--model",
        config["model"],
        "--artifacts-root",
        str(core_root),
        "--strategy-context",
        str(context_path),
    ]
    if fault is not None:
        step, intent = fault
        argv += ["--fault-step", str(step), "--fault-intent", str(intent)]
    return argv


def local_runner(argv, timeout=None):
    """Default injectable process runner; tests inject fakes instead."""
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        stdout = expired.stdout if expired.stdout is not None else b""
        stderr = expired.stderr if expired.stderr is not None else b""
        return RunnerResult(None, True, stdout, stderr)
    return RunnerResult(done.returncode, False, done.stdout, done.stderr)


def _attempt_dir(tournament_root, attempt_id):
    return Path(tournament_root) / "attempts" / attempt_id


def launch_is_registered(tournament_root, attempt_id):
    return (_attempt_dir(tournament_root, attempt_id) / "launch.json").exists()


def register_launch(tournament_root, attempt_id, metadata):
    """Immutable attempt metadata, written before the process starts (§2.2)."""
    missing = [key for key in _REQUIRED_META if key not in metadata]
    if missing:
        raise LaunchError(f"launch metadata missing: {missing}")
    path = _attempt_dir(tournament_root, attempt_id) / "launch.json"
    if path.exists():
        raise LaunchError(
            f"attempt metadata immutable: {attempt_id} already registered"
        )
    stored = {
        "schema_version": versions.LAUNCH_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "registered_before_start": True,
        **metadata,
    }
    atomic_write_bytes(path, canonical_json_bytes(stored))
    return stored


def run_launch(tournament_root, attempt_id, argv, runner=local_runner, timeout=None):
    if not launch_is_registered(tournament_root, attempt_id):
        raise LaunchError("cannot launch before registration")
    result = runner(argv, timeout=timeout)
    directory = _attempt_dir(tournament_root, attempt_id)
    stdout_ref = directory / "stdout.bin"
    stderr_ref = directory / "stderr.bin"
    stdout_ref.write_bytes(result.stdout)
    stderr_ref.write_bytes(result.stderr)
    observed = {
        "schema_version": versions.LAUNCH_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "stdout_sha256": sha256_digest(result.stdout),
        "stderr_sha256": sha256_digest(result.stderr),
        "reported_context_digest": extract_reported_context_digest(
            result.stdout.decode("utf-8", "replace")
        ),
    }
    atomic_write_bytes(directory / "observed.json", canonical_json_bytes(observed))
    return LaunchResult(
        result.exit_code,
        result.timed_out,
        stdout_ref,
        stderr_ref,
        observed["reported_context_digest"],
    )


def extract_reported_context_digest(stdout_text):
    """Identity binding ONLY: the CLI's expected context digest line (Unit 1).

    Score/outcome authority stays with sealed receipts; this line never
    contributes to leaderboard values (§4).
    """
    for line in stdout_text.splitlines():
        match = _DIGEST_LINE.match(line.strip())
        if match:
            return match.group(1)
    return None


def bind_identity(launch_meta, reported, core_config, receipt):
    """Post-run binding record for the append-only attempt log (audit chain).

    Fail-closed (PR #18 blocker 1): a run whose CLI printed no digest line
    (``reported is None``) or a malformed one can never produce the
    mandatory binding that :func:`strategy_context.verify_context_chain`
    requires.
    """
    if reported is None:
        raise LaunchError("no strategy_context_digest line reported by the CLI")
    if reported != "null" and not (
        isinstance(reported, str) and DIGEST_RE.fullmatch(reported)
    ):
        raise LaunchError(f"CLI reported a malformed digest line: {reported!r}")
    return {
        "context_digest": launch_meta["context_digest"],
        "reported_digest": reported,
        "policy_digest": core_config["policy_digest"],
        "logical_run_id": receipt["logical_run_id"],
    }
