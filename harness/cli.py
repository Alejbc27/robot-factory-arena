"""User-facing tournament entry point (design §10 rollout).

``python -m harness`` (or ``python -m harness.run``) drives ONE full
tournament from a frozen tournament config JSON: it binds the sealed layout,
append-only logs, and checkpoints (:func:`harness.graph.build_deps`), selects
the process runner (the deterministic fake wrapper for acceptance runs, or
the real core CLI subprocess via the existing argv), selects the injectable
R1 approval terminal (auto-decline, a scripted answers file, or stdin), and
drives the graph topology to the leaderboard. Fail-closed contract violations
exit non-zero AFTER printing the tournament evidence paths; usage errors exit
2; nothing is ever silently skipped, retried, or downgraded.
"""

import argparse
import json
import sys
from pathlib import Path

from . import (
    checkpointing,
    driver,
    fake_provider,
    graph,
    interruption,
    logs,
    strategy_context,
    subprocess_adapter,
    tournament,
)

_CONTRACT_ERRORS = (
    graph.HarnessGraphError,
    tournament.TournamentError,
    logs.LogIntegrityError,
    interruption.InterruptionError,
    strategy_context.ContextError,
    checkpointing.CheckpointError,
    subprocess_adapter.LaunchError,
)

_EVIDENCE_NAMES = (
    "config.json",
    "decision-log.jsonl",
    "attempt-log.jsonl",
    "discoveries.jsonl",
    "leaderboard.json",
    "contexts",
    "checkpoints",
    "attempts",
    "core",
)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Run one deterministic R1/R2/R3 tournament from a frozen config.",
    )
    parser.add_argument(
        "--config", required=True, help="tournament config JSON path (frozen §5 fields)"
    )
    parser.add_argument(
        "--root",
        default=".",
        help="directory that receives artifacts/ (default: the working directory)",
    )
    parser.add_argument(
        "--runner",
        choices=("fake", "core"),
        default="core",
        help="fake acceptance wrapper (zero cost) or the real core CLI subprocess",
    )
    # Deliberately NO --timeout flag: a subprocess timeout changes timed_out,
    # retries, K consumption and the leaderboard, so it is behavior-affecting
    # and belongs INSIDE the frozen execution profile, not in free CLI state.
    # deps.timeout stays an internal seam for tests/recovery only (review
    # finding: same identity must never run with different timeout limits).
    terminal = parser.add_mutually_exclusive_group()
    terminal.add_argument(
        "--non-interactive",
        action="store_true",
        help="auto-decline every R1 proposal (explicit logged declines)",
    )
    terminal.add_argument(
        "--answers",
        metavar="FILE",
        help="scripted R1 answers: a JSON list of answer objects, replayed in "
        "prompt order, defaulting to yes when exhausted",
    )
    return parser


def load_config(path):
    """Fail-closed config load: unreadable or invalid files never reach the graph.

    Raises OSError for unreadable paths and TournamentError (a ValueError) for
    malformed or invalid content; ``main`` maps both to usage exit 2.
    """
    try:
        config = json.loads(Path(path).read_bytes())
    except json.JSONDecodeError as exc:
        raise tournament.TournamentError(f"config is not valid JSON: {exc}") from exc
    tournament.validate_config(config)
    return config


def stdin_terminal(read=None, write=None):
    """Interactive R1 terminal (the default): prints each proposal view as
    canonical JSON and reads ONE JSON answer object per prompt. Answers obey
    the exact Unit-4 contract inside the graph node; a closed input fails
    closed here before any answer exists."""
    read = read or input
    write = write or (lambda text: print(text, file=sys.stderr))

    def terminal(view):
        write("R1 approval proposal — reply with one JSON answer object:")
        write(strategy_context.canonical_json_bytes(view).decode())
        try:
            line = read()
        except EOFError as exc:
            raise interruption.InterruptionError(
                "approval input closed without an answer"
            ) from exc
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise interruption.InterruptionError(
                f"approval answer is not JSON: {exc}"
            ) from exc

    return terminal


def answers_terminal(path):
    """Scripted answers seam (the Unit-4 scripted_terminal contract): the file
    is a JSON list of answer objects, each validated BEFORE any run side
    effect; answers replay in prompt order and later prompts default to yes.
    """
    try:
        answers = json.loads(Path(path).read_bytes())
    except json.JSONDecodeError as exc:
        raise interruption.InterruptionError(
            f"answers file is not valid JSON: {exc}"
        ) from exc
    if not isinstance(answers, list):
        raise interruption.InterruptionError("answers file must be a JSON list")
    for answer in answers:
        interruption.validate_answer(answer)
    calls = []

    def terminal(view):
        calls.append(dict(view))
        index = len(calls) - 1
        return dict(answers[index]) if index < len(answers) else {"decision": "yes"}

    terminal.calls = calls
    return terminal


def _terminal(args):
    if args.non_interactive:
        return interruption.auto_decline
    if args.answers is not None:
        return answers_terminal(args.answers)
    return stdin_terminal()


def _runner(name):
    return (
        fake_provider.fake_runner if name == "fake" else subprocess_adapter.local_runner
    )


def _print_evidence(args, config, exc):
    """Fail-closed exit evidence: where every artifact of this tournament lives."""
    print(f"harness: tournament failed closed: {exc}", file=sys.stderr)
    layout = tournament.expected_layout(
        Path(args.root), config["tournament_id"], config["artifact_root"]
    )
    print("tournament evidence (paths may not exist yet):", file=sys.stderr)
    for name in _EVIDENCE_NAMES:
        print(f"  {name}: {layout[name]}", file=sys.stderr)


def main(argv=None):
    """Entry point; returns the process exit code (0 ok, 1 fail-closed, 2 usage)."""
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        tournament.validate_runner_binding(config, args.runner)
    except OSError as exc:
        print(f"harness: cannot read tournament config: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"harness: invalid tournament config: {exc}", file=sys.stderr)
        return 2
    try:
        approval = _terminal(args)
    except OSError as exc:
        print(f"harness: cannot read scripted answers: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"harness: invalid scripted answers file: {exc}", file=sys.stderr)
        return 2
    try:
        deps = graph.build_deps(
            Path(args.root),
            config,
            runner=_runner(args.runner),
            approval=approval,
        )
        driver.run_tournament(deps)
    except _CONTRACT_ERRORS as exc:
        _print_evidence(args, config, exc)
        return 1
    layout = tournament.expected_layout(
        Path(args.root), config["tournament_id"], config["artifact_root"]
    )
    print(f"tournament {config['tournament_id']} complete")
    print(f"leaderboard: {layout['leaderboard.json']}")
    return 0
