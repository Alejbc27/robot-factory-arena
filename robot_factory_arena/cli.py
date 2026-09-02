"""Command-line interface: run, rerun, and replay (T-23, REQ-07/09/10).

Three DISTINCT operations (REQ-07):

- ``run`` executes one evidenced run (task + policy + config) through the
  proven verified-release flow (B5/T-20): Runner + RunEvidenceRecorder ->
  ``close()`` -> ``load_verification_artifacts`` -> ``Verifier.verify`` ->
  ``ReceiptWriter.write_final`` -> persist ``receipt.json`` -> operation-only
  replay -> ``replay-result.json`` -> static ``report.html`` -> binding
  re-verification with the receipt present.
- ``rerun`` executes a fresh physical execution under the SAME logical
  identity — it invokes the policy again, unlike replay — and compares every
  deterministic artifact (and the receipt minus ``execution_id``) against
  each prior execution of the same logical run (REQ-07 rerun equality).
- ``replay`` reads ONLY the recorded run artifacts — ``replay-input.json``
  and ``receipt.json`` — never imports or calls a policy/provider, reports
  whether the final-state digest, verified score, and outcome match the
  receipt, and rewrites ``replay-result.json`` (byte-identically when the
  evidence agrees). The documented offline instruction is::

      python3 -m robot_factory_arena.cli replay artifacts/runs/<logical_run_id>/<execution_id>

Module import discipline: this module does NOT import
``robot_factory_arena.policies`` at module scope. The reference policies are
imported lazily inside the run/rerun handlers only, so a replay process
provably never loads policy code (a process-level test asserts
``'robot_factory_arena.policies' not in sys.modules`` after replay).

Exit codes: 0 success (receipt released / rerun agreement / replay match);
1 evidence-level failure (invalid verification, rerun disagreement, replay
mismatch); 2 usage or artifact errors (argparse, bad seed, partial fault
flags, missing run directory, reused execution id, inconsistent replay
input). The declared artifacts of a completed run are the seven pre-final
B4 artifacts plus ``receipt.json``, ``replay-result.json``, and
``report.html``; the ``boundaries/`` commit-proof files are part of the run
evidence and are never optional (REQ-05, REQ-09).
"""
import argparse
import json
import re
import sys
import uuid
from pathlib import Path

from robot_factory_arena.contracts.policy import policy_manifest_digest
from robot_factory_arena.domain.task import generate_task
from robot_factory_arena.evidence.artifact_store import (
    RunEvidenceRecorder,
    atomic_write_bytes,
)
from robot_factory_arena.evidence.canonical import (
    canonical_json_bytes,
    digest_json,
    sha256_digest,
)
from robot_factory_arena.evidence.receipt import ReceiptWriter
from robot_factory_arena.evidence.replay import replay
from robot_factory_arena.evidence.report import render_report
from robot_factory_arena.faults.model import (
    FAULT_ID_RESPONSE_LOST,
    HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
    KIND_RESPONSE_LOST,
)
from robot_factory_arena.faults.tape import FaultTapeSpec, FaultTarget
from robot_factory_arena.runtime.runner import (
    EvidenceCallbacks,
    RunConfig,
    Runner,
    compute_logical_run_id,
)
from robot_factory_arena.verification.verifier import (
    Verifier,
    load_verification_artifacts,
)

POLICY_CHOICES = ("greedy-v0", "bounded-v0")
GRANTS = ("move", "pick", "place")
BUDGET = 100
RECEIPT_NAME = "receipt.json"
REPLAY_RESULT_NAME = "replay-result.json"
REPORT_NAME = "report.html"

_SEED_RE = re.compile(r"(0|[1-9][0-9]*)")

# Deterministic artifacts compared byte-for-byte across reruns. Physical
# receipts differ by design in exactly one field (execution_id, REQ-03) and
# are compared separately. report.html is deterministic because the report
# never renders the execution id (REQ-07) and its digest table carries only
# semantic digests.
_DETERMINISTIC_COMPARISON_FILES = (
    "config.json",
    "fault_tape.json",
    "final_state.json",
    "operation-log.json",
    "replay-input.json",
    "replay-result.json",
    REPORT_NAME,
    "task.json",
    "trace.jsonl",
)

# Artifacts digested for the report's digest table. receipt.json is excluded
# on purpose: its bytes embed the physical execution id, and deterministic
# report contents must not depend on it (REQ-07); the receipt's identity and
# semantic digest fields are rendered directly instead.
_DIGESTED_ARTIFACTS = (
    "config.json",
    "fault_tape.json",
    "final_state.json",
    "operation-log.json",
    "replay-input.json",
    "replay-result.json",
    "task.json",
    "trace.jsonl",
)


def _build_policy(policy_id):
    """Resolve a reference policy; policies load LAZILY (replay never loads
    policy code, see the module docstring)."""
    if policy_id == "greedy-v0":
        from robot_factory_arena.policies.greedy import GreedyPolicy

        return GreedyPolicy()
    if policy_id == "bounded-v0":
        from robot_factory_arena.policies.bounded import BoundedPolicy

        return BoundedPolicy()
    raise ValueError(f"unsupported policy: {policy_id!r}")


def _read_json(path: Path):
    """Read one declared run artifact; failures are loud (exit 2 via main)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read run artifact {path}: {error}") from error


def collect_artifact_digests(directory) -> dict:
    """Semantic digest table for the report (sorted, execution-id-free).

    JSON artifacts are digested through the canonical authority (identical to
    digesting their canonical bytes); ``trace.jsonl`` is digested over its raw
    LF-terminated bytes, matching ``receipt["trace_digest"]``.
    """
    directory = Path(directory)
    digests = {}
    for name in _DIGESTED_ARTIFACTS:
        path = directory / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if name.endswith(".jsonl"):
            digests[name] = sha256_digest(raw)
        else:
            digests[name] = digest_json(_read_json(path))
    return dict(sorted(digests.items()))


def _execute_run(artifacts_root, seed, policy_id, execution_id, fault_step,
                 fault_intent):
    """The verified-run composition (B5-proven order); returns (code, receipt,
    verification, directory)."""
    task = generate_task(seed)
    policy = _build_policy(policy_id)
    fault_spec = None
    if fault_step is not None:
        fault_spec = FaultTapeSpec(
            1,
            FAULT_ID_RESPONSE_LOST,
            HOOK_AFTER_COMMIT_BEFORE_RESPONSE,
            KIND_RESPONSE_LOST,
            FaultTarget(fault_step, fault_intent),
        )
    config = RunConfig(
        1, task.task_version, task.seed, policy_id,
        policy_manifest_digest(policy_id), BUDGET, GRANTS, fault_spec,
    )
    logical_run_id = compute_logical_run_id(task, config)
    recorder = RunEvidenceRecorder(
        artifacts_root, task, config, logical_run_id, execution_id
    )
    evidence = EvidenceCallbacks(**recorder.callback_map())
    result = Runner(evidence=evidence).run(task, policy, config, execution_id)
    recorder.close()
    del result  # the verifier's classification is authoritative (REQ-06)

    verification = Verifier().verify(
        load_verification_artifacts(recorder.directory)
    )
    if not verification.valid:
        print("verification: INVALID — no receipt released (REQ-06)")
        for violation in verification.violations:
            print(
                f"  violation: {violation.code} at {violation.location}: "
                f"{violation.detail}"
            )
        return 1, None, verification, recorder.directory

    receipt = ReceiptWriter.write_final(recorder.receipt_candidate, verification)
    atomic_write_bytes(
        recorder.directory / RECEIPT_NAME, canonical_json_bytes(receipt)
    )
    replayed = replay(
        _read_json(recorder.directory / "replay-input.json"), receipt=receipt
    )
    atomic_write_bytes(
        recorder.directory / REPLAY_RESULT_NAME,
        canonical_json_bytes(replayed.to_mapping()),
    )
    report_html = render_report(
        receipt, verification, collect_artifact_digests(recorder.directory)
    )
    atomic_write_bytes(
        recorder.directory / REPORT_NAME, report_html.encode("utf-8")
    )

    # Binding re-verification with receipt.json present (T-20-proven order).
    recheck = Verifier().verify(
        load_verification_artifacts(recorder.directory)
    )
    if not recheck.valid:
        print(
            "error: the released receipt failed binding re-verification",
            file=sys.stderr,
        )
        for violation in recheck.violations:
            print(f"  violation: {violation.code} at {violation.location}",
                  file=sys.stderr)
        return 1, None, verification, recorder.directory
    return 0, receipt, verification, recorder.directory


def _deterministic_mismatches(dir_a: Path, dir_b: Path):
    """Deterministic-artifact differences between two physical executions."""
    mismatches = []
    for name in _DETERMINISTIC_COMPARISON_FILES:
        path_a = dir_a / name
        path_b = dir_b / name
        if not path_a.is_file() or not path_b.is_file():
            mismatches.append(f"{name}: missing artifact")
        elif path_a.read_bytes() != path_b.read_bytes():
            mismatches.append(f"{name}: bytes differ")
    receipt_a = _read_json(dir_a / RECEIPT_NAME)
    receipt_b = _read_json(dir_b / RECEIPT_NAME)
    receipt_a.pop("execution_id", None)
    receipt_b.pop("execution_id", None)
    if canonical_json_bytes(receipt_a) != canonical_json_bytes(receipt_b):
        mismatches.append("receipt (minus execution_id): differs")
    return mismatches


def _compare_with_prior_runs(artifacts_root, logical_run_id, execution_id):
    """Report rerun equality against every prior execution (REQ-07)."""
    logical_dir = Path(artifacts_root) / "runs" / logical_run_id
    priors = []
    if logical_dir.is_dir():
        priors = sorted(
            path for path in logical_dir.iterdir()
            if path.is_dir() and path.name != execution_id
        )
    if not priors:
        print("deterministic agreement: true (no prior executions recorded)")
        return 0
    agreed = True
    for prior in priors:
        mismatches = _deterministic_mismatches(
            logical_dir / execution_id, prior
        )
        if mismatches:
            agreed = False
            print(f"deterministic agreement vs {prior.name}: false")
            for mismatch in mismatches:
                print(f"  MISMATCH {mismatch}")
        else:
            print(f"deterministic agreement vs {prior.name}: true")
    print(f"deterministic agreement: {str(agreed).lower()}")
    return 0 if agreed else 1


def _run_and_report(args, rerun: bool) -> int:
    if (args.fault_step is None) != (args.fault_intent is None):
        print(
            "error: --fault-step and --fault-intent must be provided together",
            file=sys.stderr,
        )
        return 2
    execution_id = args.execution_id or f"execution-{uuid.uuid4().hex}"
    verb = "rerun" if rerun else "run"
    code, receipt, verification, directory = _execute_run(
        args.artifacts_root, args.seed, args.policy, execution_id,
        args.fault_step, args.fault_intent,
    )
    if code != 0 or receipt is None:
        return code
    print(f"{verb}: seed={args.seed} policy={args.policy}")
    print(f"logical_run_id: {receipt['logical_run_id']}")
    print(f"execution_id: {execution_id}")
    print(f"run_directory: {directory}")
    print(f"outcome: {verification.outcome}")
    print(f"verified_score: {verification.verified_score}")
    print(f"verifier: valid ({verification.verifier_version})")
    print(f"report: {directory / REPORT_NAME}")
    if rerun:
        return _compare_with_prior_runs(
            args.artifacts_root, receipt["logical_run_id"], execution_id
        )
    return 0


def _cmd_run(args) -> int:
    return _run_and_report(args, rerun=False)


def _cmd_rerun(args) -> int:
    return _run_and_report(args, rerun=True)


def _cmd_replay(args) -> int:
    directory = Path(args.run_directory)
    if not directory.is_dir():
        print(f"error: run directory not found: {directory}", file=sys.stderr)
        return 2
    replay_input_path = directory / "replay-input.json"
    receipt_path = directory / RECEIPT_NAME
    for required in (replay_input_path, receipt_path):
        if not required.is_file():
            print(
                f"error: missing required run artifact: {required}",
                file=sys.stderr,
            )
            return 2
    replayed = replay(
        _read_json(replay_input_path), receipt=_read_json(receipt_path)
    )
    print(f"replay: {directory}")
    print(f"final_state_digest: {replayed.final_state_digest}")
    print(f"verified_score: {replayed.receipt_verified_score}")
    print(f"outcome: {replayed.receipt_outcome}")
    print(f"digest_match: {str(replayed.digest_matches).lower()}")
    print(f"outcome_match: {str(replayed.outcome_matches).lower()}")
    agreement = "match" if replayed.matches_receipt else "MISMATCH"
    print(f"receipt_agreement: {agreement}")
    atomic_write_bytes(
        directory / REPLAY_RESULT_NAME,
        canonical_json_bytes(replayed.to_mapping()),
    )
    print(f"replay-result: {directory / REPLAY_RESULT_NAME}")
    return 0 if replayed.matches_receipt else 1


def _seed_arg(value: str) -> str:
    if not isinstance(value, str) or _SEED_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            f"seed must be a non-negative decimal integer: {value!r}"
        )
    try:
        return str(int(value))  # canonical decimal text (no leading zeros)
    except ValueError as error:  # defensive: the regex already admits digits
        raise argparse.ArgumentTypeError(
            f"seed must be a non-negative decimal integer: {value!r}"
        ) from error


def _fault_step_arg(value: str) -> int:
    try:
        step = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"fault-step must be an integer: {value!r}"
        ) from None
    if not 1 <= step <= BUDGET:
        raise argparse.ArgumentTypeError(
            f"fault-step must lie within the locked 1..{BUDGET} policy budget"
        )
    return step


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--seed", required=True, type=_seed_arg,
        help="non-negative decimal task seed",
    )
    parser.add_argument(
        "--policy", default="greedy-v0", choices=POLICY_CHOICES,
        help="reference policy (default: greedy-v0)",
    )
    parser.add_argument(
        "--artifacts-root", default="artifacts",
        help="artifact store root (default: artifacts)",
    )
    parser.add_argument(
        "--execution-id", default=None,
        help="physical execution id (default: execution-<uuid4>)",
    )
    parser.add_argument(
        "--fault-step", type=_fault_step_arg, default=None,
        help="predeclared delivery policy step of the v0 response-loss tape",
    )
    parser.add_argument(
        "--fault-intent", default=None,
        help="predeclared intent token, e.g. place-order-00",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m robot_factory_arena.cli",
        description=(
            "Robot Factory & Arena v0: deterministic warehouse "
            "evidence-chain runs."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="execute one evidenced run and release its verified receipt",
    )
    _add_run_arguments(run_parser)
    run_parser.set_defaults(handler=_cmd_run)

    rerun_parser = subparsers.add_parser(
        "rerun",
        help=(
            "execute a fresh physical run under the same logical identity "
            "and compare deterministic artifacts with prior executions"
        ),
    )
    _add_run_arguments(rerun_parser)
    rerun_parser.set_defaults(handler=_cmd_rerun)

    replay_parser = subparsers.add_parser(
        "replay",
        help=(
            "operation-only offline replay of one recorded run directory "
            "(reads no policy and no provider)"
        ),
    )
    replay_parser.add_argument(
        "run_directory",
        help="artifacts/runs/<logical_run_id>/<execution_id>",
    )
    replay_parser.set_defaults(handler=_cmd_replay)
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        # Usage/artifact errors exit 2; evidence disagreement exits 1 from
        # the handlers. Genuine runs verify valid; anything here is loud.
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
