"""LangGraph topology for the R2/R1 tournament paths (design §6-§8).

This module is the ONLY LangGraph home in ``harness/``: it imports LangGraph
lazily inside :func:`build_graph`, so every pure node/route function below is
testable without the optional extra (the compile test skips locally and runs
in CI against langgraph 1.2.11).

Nodes own all writes; the conditional edges only inspect state/logs and route.
R2 is autonomous: one agent, no human prompt, no discoveries, no silent
fallback. R1 adds the human-interruption path: an injectable approval terminal
is prompted per work item; answers are controlled data (``harness.interruption``),
declines are explicit replayable events, and approved human guidance enters the
core ONLY through the canonical StrategyContext pipeline frozen at merge time.
Retry semantics are shared: only failures, timeouts, non-zero exits, and
missing/invalid/unresolved evidence route to a NEW attempt ordinal, until
exactly K launched attempts; a first valid receipt — completed or a safe abort
— is terminal and is never rerun.

Pair progress is reconstructed EXPLICITLY from the append-only attempt log
(PR #20): the latest launch with no evidence resolution is collected and
bound whether or not launch_observed exists; a latest launch with
evidence_bound is pair-terminal and the seed advances; evidence_unresolved
with launches < K yields a new attempt ordinal; evidence_unresolved at
exactly K is pair-terminal evidence_invalid and the seed advances. Every
crash window therefore resumes to the identical decision: the same
attempt_id is never relaunched, a bound terminal receipt is never repeated,
and the exact-K limit is never exceeded. The registered-but-unbound window
stays ambiguous: it is counted as a launch, never rerun under the same
``attempt_id``, and recovery records an explicit event. R1 approval state is
reconstructed from the decision log under the same contract: a standing
approval grant is never re-prompted after a crash, and a decline is never
silently re-proposed.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import (
    checkpointing,
    evidence,
    interruption,
    leaderboard,
    logs,
    strategy_context,
    subprocess_adapter,
    tournament,
)
from .checkpointing import (
    POST_ANSWER,
    POST_BINDING,
    POST_REGISTRATION,
    PRE_LAUNCH,
    PRE_PROMPT,
)
from .state import HarnessState, set_current

# Deterministic implemented-condition order for the work queue: the
# autonomous R2 reference pairs are deliberately grouped first so an
# unattended batch completes them before reaching R1 prompts. R3 stays an
# explicit deferral (Unit 5).
IMPLEMENTED_CONDITIONS = ("R2", "R1")
AGENT_ID = "agent-01"

_RETRY_ROUTES = {
    "retry": "register_launch",
    "next_work": "plan_context",
    "work_done": "aggregate_leaderboard",
}

_PLAN_ROUTES = {
    "merge_and_freeze_context": "merge_and_freeze_context",
    "collect_and_bind_evidence": "collect_and_bind_evidence",
    "retry_or_finish": "retry_or_finish",
    "approve_r1": "approve_r1",
}

_APPROVAL_ROUTES = {
    "approval_granted": "merge_and_freeze_context",
    "approval_edited": "approve_r1",
    "launch_declined": "retry_or_finish",
}


class HarnessGraphError(ValueError):
    """The graph cannot run (e.g. the optional LangGraph extra is absent)."""


@dataclass
class GraphDeps:
    """Bound resources for one tournament run; nodes receive it explicitly."""

    root: Path
    config: dict
    runner: Any
    timeout: int | None
    layout: dict
    tournament_root: Path
    attempt_log: logs.AppendOnlyLog
    decision_log: logs.AppendOnlyLog
    checkpoints: checkpointing.CheckpointStore
    expectations: dict
    approval: Any


def build_deps(
    root,
    config,
    runner=subprocess_adapter.local_runner,
    timeout=None,
    approval=None,
):
    """Validate the config and bind the sealed layout, logs, and checkpoints.

    Recovery contract (§8): append-only log continuity is verified BEFORE the
    logs are used, so a truncated or overwritten log fails closed at restart
    instead of silently replaying fewer records.
    """
    tournament.validate_config(config)
    root = Path(root)
    layout = tournament.ensure_layout(
        root, config["tournament_id"], config["artifact_root"]
    )
    attempt_log = logs.AppendOnlyLog(layout["attempt-log.jsonl"])
    decision_log = logs.AppendOnlyLog(layout["decision-log.jsonl"])
    attempt_log.verify()
    decision_log.verify()
    expectations = {}
    for record in decision_log.records():
        if record.get("event") == "expectations_recorded":
            expectations.update(
                prompt_digest=record["prompt_digest"],
                tool_schema_digest=record["tool_schema_digest"],
            )
    return GraphDeps(
        root=root,
        config=config,
        runner=runner,
        timeout=timeout,
        layout=layout,
        tournament_root=root / config["artifact_root"] / config["tournament_id"],
        attempt_log=attempt_log,
        decision_log=decision_log,
        checkpoints=checkpointing.CheckpointStore(layout["checkpoints"]),
        expectations=expectations,
        approval=approval,
    )


def work_items(config):
    """Deterministic work queue: implemented conditions × frozen seed order."""
    return [
        (condition, str(seed))
        for condition in IMPLEMENTED_CONDITIONS
        for seed in config["seeds"]
    ]


def _attempt_id(condition, seed, ordinal):
    return f"{condition.lower()}-{AGENT_ID}-seed-{seed}-attempt-{ordinal:02d}"


def _pair_launches(deps, condition, seed):
    return sorted(
        (
            record
            for record in deps.attempt_log.records()
            if record.get("event") == "launch_registered"
            and record.get("condition") == condition
            and record.get("seed") == seed
        ),
        key=lambda r: r["attempt_ordinal"],
    )


def _has_pair_event(deps, event, condition, seed):
    return any(
        record.get("event") == event
        and record.get("condition") == condition
        and record.get("seed") == seed
        for record in deps.decision_log.records()
    )


def _run_dirs(core_root):
    runs = Path(core_root) / "runs"
    if not runs.exists():
        return []
    found = []
    for logical in sorted(runs.iterdir()):
        if not logical.is_dir():
            continue
        for execution in sorted(logical.iterdir()):
            if execution.is_dir():
                found.append(f"{logical.name}/{execution.name}")
    return found


def _resolved_event(deps, attempt_id):
    """The latest evidence resolution record for attempt_id, or None."""
    found = None
    for record in deps.attempt_log.records():
        if record.get("attempt_id") == attempt_id and record.get("event") in (
            "evidence_bound",
            "evidence_unresolved",
        ):
            found = record
    return found


def _pair_state(deps, condition, seed):
    """Reconstruct (condition, seed) progress from the append-only log (PR #20).

    Returns exactly one of:

    ``fresh``    — no launch registered yet;
    ``collect``  — the latest launch has NO evidence resolution: collect and
                   bind whether or not launch_observed exists (crash windows);
    ``retry``    — the latest launch is evidence_unresolved and launches < K;
    ``terminal`` — the latest launch is evidence_bound (receipt terminal,
                   never rerun) or evidence_unresolved at exactly K (pair
                   terminal evidence_invalid); the seed advances.
    """
    launches = _pair_launches(deps, condition, seed)
    if not launches:
        return "fresh", None
    latest = launches[-1]
    resolution = _resolved_event(deps, latest["attempt_id"])
    if resolution is None:
        return "collect", latest
    if resolution["event"] == "evidence_bound":
        return "terminal", latest
    if len(launches) < deps.config["k_attempts"]:
        return "retry", latest
    return "terminal", latest


def _record_unresolved(state, deps, launch_record, reason):
    deps.attempt_log.append(
        {
            "event": "evidence_unresolved",
            "condition": launch_record["condition"],
            "seed": launch_record["seed"],
            "attempt_id": launch_record["attempt_id"],
            "attempt_ordinal": launch_record["attempt_ordinal"],
            "reason": reason,
        }
    )
    return set_current(
        state, last_evidence={"status": "unresolved"}, status="evidence_unresolved"
    )


def _record_crash_window(deps, launch_record):
    """The registered-unbound window is ambiguous: explicit unresolved + recovery."""
    attempt_id = launch_record["attempt_id"]
    if any(
        r.get("event") == "evidence_unresolved" and r.get("attempt_id") == attempt_id
        for r in deps.attempt_log.records()
    ):
        return
    deps.attempt_log.append(
        {
            "event": "evidence_unresolved",
            "condition": launch_record["condition"],
            "seed": launch_record["seed"],
            "attempt_id": attempt_id,
            "attempt_ordinal": launch_record["attempt_ordinal"],
            "reason": "crash_window_unobserved",
        }
    )
    deps.decision_log.append(
        {
            "event": "recovery",
            "attempt_id": attempt_id,
            "detail": "registered unbound crash window is ambiguous; "
            "the same attempt_id is never rerun; a new ordinal is required",
        }
    )


def _record_expectations_once(deps):
    if deps.expectations.get("prompt_digest") is None:
        return
    if any(
        r.get("event") == "expectations_recorded" for r in deps.decision_log.records()
    ):
        return
    deps.decision_log.append({"event": "expectations_recorded", **deps.expectations})


def node_freeze_tournament_config(state, deps):
    """Seal once, then reload + reject drift on every later entry (§5)."""
    try:
        tournament.seal(deps.root, deps.config)
    except tournament.TournamentError:
        sealed = tournament.load_sealed(
            deps.root, deps.config["tournament_id"], deps.config["artifact_root"]
        )
        tournament.reject_drift(sealed, deps.config)
    tournament.ensure_layout(
        deps.root, deps.config["tournament_id"], deps.config["artifact_root"]
    )
    deps.decision_log.append(
        {"event": "tournament_frozen", "tournament_id": deps.config["tournament_id"]}
    )
    return set_current(state, status="tournament_frozen")


def node_assign_work(state, deps):
    """R2/R1 are implemented; R3 defers explicitly, never silently (Unit 5)."""
    deferred = [c for c in deps.config["conditions"] if c not in IMPLEMENTED_CONDITIONS]
    if deferred:
        deps.decision_log.append({"event": "routes_deferred", "conditions": deferred})
    return set_current(
        state,
        work_index=0,
        work_count=len(work_items(deps.config)),
        status="work_assigned",
    )


def node_plan_context(state, deps):
    """Route the next work item; the resume ordinal comes from the log."""
    condition, seed = work_items(deps.config)[state["work_index"]]
    launches = _pair_launches(deps, condition, seed)
    ordinal = max((r["attempt_ordinal"] for r in launches), default=0)
    deps.decision_log.append(
        {
            "event": "context_planned",
            "condition": condition,
            "seed": seed,
            "source": "r1-approval-pipeline"
            if condition == "R1"
            else "null-first-route",
        }
    )
    return set_current(
        state,
        condition=condition,
        semilla=seed,
        agent_id=AGENT_ID,
        intentos=ordinal,
        attempt_id=None,
        last_evidence=None,
        status="context_planned",
    )


def _approved_guidance(deps, condition, seed):
    """Approved guidance for the pair: the latest pre-grant revision (§6).

    An edit REPLACES the pending guidance list; earlier revisions stay in the
    append-only log as history but can never resurrect into a later snapshot.
    Guidance recorded after the standing grant is ignored — approvals freeze
    the guidance at the moment of the grant.
    """
    texts = []
    for record in deps.decision_log.records():
        if record.get("condition") != condition or record.get("seed") != seed:
            continue
        if record.get("event") == "approval_edit":
            texts = list(record["guidance"])
        elif record.get("event") == "approval_granted":
            return texts
    return texts


def _proposal_round(deps, condition, seed):
    """Deterministic prompt counter: each prior answer marks one prompt."""
    return sum(
        1
        for record in deps.decision_log.records()
        if record.get("condition") == condition
        and record.get("seed") == seed
        and record.get("event")
        in ("approval_edit", "approval_granted", "launch_declined")
    )


def node_approve_r1(state, deps):
    """R1 interruption: the injectable terminal answers a controlled proposal.

    The answer is validated BEFORE any side effect beyond the pre-prompt
    checkpoint, so a hostile or buggy terminal can never reach the prompt, the
    observation, the provider request, or the score path: the only editable
    field is guidance text, and guidance enters exclusively through the
    canonical StrategyContext pipeline at merge time. ``yes`` grants exactly
    one proposal; ``edit`` logs the revision BEFORE its follow-up prompt;
    ``no`` records ``launch_declined`` — a real orchestration outcome, never
    a silent skip — and no subprocess attempt is allocated.
    """
    if deps.approval is None:
        raise HarnessGraphError(
            "R1 requires an injectable approval terminal; "
            "there is no silent auto-approval"
        )
    condition, seed = state["condition"], state["semilla"]
    round_number = _proposal_round(deps, condition, seed)
    view = {
        "condition": condition,
        "seed": seed,
        "proposal_round": round_number,
        "proposed_attempt_id": _attempt_id(
            condition, seed, len(_pair_launches(deps, condition, seed)) + 1
        ),
        "current_guidance": list(_approved_guidance(deps, condition, seed)),
        "editable_fields": ("guidance",),
        "frozen_fields": (
            "condition",
            "seed",
            "provider",
            "model",
            "generation",
            "k_attempts",
            "tool_grants",
            "fault_config",
            "seeds",
            "prompt_policy_version",
        ),
    }
    deps.checkpoints.write(
        PRE_PROMPT,
        {"condition": condition, "seed": seed, "proposal_round": round_number},
    )
    answer = deps.approval(view)
    interruption.validate_answer(answer)
    deps.checkpoints.write(
        POST_ANSWER,
        {
            "condition": condition,
            "seed": seed,
            "proposal_round": round_number,
            "decision": answer["decision"],
        },
    )
    if answer["decision"] == "yes":
        deps.decision_log.append(
            {
                "event": "approval_granted",
                "condition": condition,
                "seed": seed,
                "proposal_round": round_number,
            }
        )
        return set_current(state, status="approval_granted")
    if answer["decision"] == "edit":
        deps.decision_log.append(
            {
                "event": "approval_edit",
                "condition": condition,
                "seed": seed,
                "proposal_round": round_number,
                "guidance": list(answer["guidance"]),
            }
        )
        return set_current(state, status="approval_edited")
    deps.decision_log.append(
        {
            "event": "launch_declined",
            "condition": condition,
            "seed": seed,
            "proposal_round": round_number,
        }
    )
    return set_current(state, status="launch_declined")


def _next_snapshot_ordinal(deps):
    """Global monotonic snapshot ordinal derived from the append-only log."""
    return sum(
        1
        for record in deps.decision_log.records()
        if record.get("event") == "context_snapshot_frozen"
    )


def node_merge_and_freeze_context(state, deps):
    """Freeze the canonical snapshot before launch (§2.2).

    R2 freezes the canonical null first-route snapshot; R1 freezes the
    approved human guidance through the same validator, canonical builder,
    digest, and atomic freeze path — guidance has no other door. A no-guidance
    R1 approval freezes the same canonical ``null`` as R2.
    """
    condition, seed = state["condition"], state["semilla"]
    if condition == "R1":
        context = interruption.build_guidance_context(
            deps.config["tournament_id"], _approved_guidance(deps, condition, seed)
        )
    else:
        context = None
    ordinal = _next_snapshot_ordinal(deps)
    path = deps.layout["contexts"] / f"strategy-context-{ordinal:03d}.json"
    frozen = strategy_context.freeze_snapshot(context, path)
    ref = {**frozen, "snapshot_ordinal": ordinal}
    deps.decision_log.append(
        {
            "event": "context_snapshot_frozen",
            "condition": condition,
            "seed": seed,
            "snapshot_ordinal": ordinal,
            "context_digest": ref["digest"],
            "source": "r1-guidance" if condition == "R1" else "null-first-route",
        }
    )
    return set_current(state, context_snapshot_ref=ref, status="context_frozen")


def node_register_launch(state, deps):
    condition, seed = state["condition"], state["semilla"]
    launches = _pair_launches(deps, condition, seed)
    ordinal = len(launches) + 1
    attempt_id = _attempt_id(condition, seed, ordinal)
    ref = state["context_snapshot_ref"]
    deps.checkpoints.write(
        PRE_LAUNCH, {"attempt_id": attempt_id, "work_index": state["work_index"]}
    )
    subprocess_adapter.register_launch(
        deps.tournament_root,
        attempt_id,
        {
            "condition": condition,
            "seed": seed,
            "agent_id": AGENT_ID,
            "attempt_ordinal": ordinal,
            "argv": subprocess_adapter.build_command(
                deps.config, seed, ref["path"], deps.layout["core"]
            ),
            "context_path": ref["path"],
            "context_digest": ref["digest"],
            "core_run_dirs_before": _run_dirs(deps.layout["core"]),
        },
    )
    deps.attempt_log.append(
        {
            "event": "launch_registered",
            "condition": condition,
            "seed": seed,
            "agent_id": AGENT_ID,
            "attempt_id": attempt_id,
            "attempt_ordinal": ordinal,
            "context_digest": ref["digest"],
            "snapshot_ordinal": ref["snapshot_ordinal"],
        }
    )
    deps.checkpoints.write(POST_REGISTRATION, {"attempt_id": attempt_id})
    return set_current(
        state, intentos=ordinal, attempt_id=attempt_id, status="launch_registered"
    )


def node_launch_subprocess(state, deps):
    attempt_id = state["attempt_id"]
    registered = subprocess_adapter.load_registration(deps.tournament_root, attempt_id)
    result = subprocess_adapter.run_launch(
        deps.tournament_root,
        attempt_id,
        registered["argv"],
        runner=deps.runner,
        timeout=deps.timeout,
    )
    deps.attempt_log.append(
        {
            "event": "launch_observed",
            "condition": state["condition"],
            "seed": state["semilla"],
            "attempt_id": attempt_id,
            "attempt_ordinal": state["intentos"],
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }
    )
    return set_current(state, status="launch_observed")


def node_collect_and_bind_evidence(state, deps):
    condition, seed = state["condition"], state["semilla"]
    target = _pair_launches(deps, condition, seed)[-1]
    attempt_id = target["attempt_id"]
    observed_path = deps.tournament_root / "attempts" / attempt_id / "observed.json"
    if not observed_path.exists():
        _record_crash_window(deps, target)
        return set_current(
            state, last_evidence={"status": "unresolved"}, status="evidence_unresolved"
        )
    try:
        observed = json.loads(observed_path.read_bytes())
    except json.JSONDecodeError as exc:
        return _record_unresolved(
            state, deps, target, f"observed.json unreadable: {exc}"
        )
    if not isinstance(observed, dict):
        return _record_unresolved(state, deps, target, "observed.json is not an object")
    # A timeout or non-zero exit resolves the evidence BEFORE any artifact
    # binding (PR #20): a runner that writes a perfectly valid run triple
    # and then fails must never produce terminal evidence_bound.
    for field in ("exit_code", "timed_out"):
        if field not in observed:
            return _record_unresolved(
                state, deps, target, f"observed.json missing field: {field}"
            )
    if observed["timed_out"]:
        return _record_unresolved(state, deps, target, "subprocess_timeout")
    if observed["exit_code"] != 0:
        return _record_unresolved(state, deps, target, "subprocess_nonzero_exit")
    launch_meta = subprocess_adapter.load_registration(deps.tournament_root, attempt_id)
    new_dirs = [
        d
        for d in _run_dirs(deps.layout["core"])
        if d not in set(launch_meta["core_run_dirs_before"])
    ]
    if len(new_dirs) != 1:
        return _record_unresolved(
            state,
            deps,
            target,
            f"expected exactly one new run dir, found {len(new_dirs)}",
        )
    run_dir = deps.layout["core"] / "runs" / new_dirs[0]
    try:
        receipt = evidence.load_receipt(run_dir)
    except evidence.EvidenceError as exc:
        return _record_unresolved(state, deps, target, exc.reason)
    core_config = {"policy_digest": receipt["policy_digest"]}
    try:
        binding = subprocess_adapter.bind_identity(
            launch_meta, observed["reported_context_digest"], core_config, receipt
        )
        strategy_context.verify_context_chain(
            launch_meta["context_path"], launch_meta, binding, core_config, receipt
        )
    except (subprocess_adapter.LaunchError, strategy_context.ContextError) as exc:
        return _record_unresolved(state, deps, target, str(exc))
    result = evidence.bind_attempt_evidence(
        run_dir, deps.config, launch_meta, binding, deps.expectations
    )
    if result.status == "bound":
        deps.expectations = evidence.absorb_expectations(deps.expectations, result)
        _record_expectations_once(deps)
        deps.attempt_log.append(
            {
                "event": "evidence_bound",
                "condition": condition,
                "seed": seed,
                "attempt_id": attempt_id,
                "attempt_ordinal": target["attempt_ordinal"],
                "outcome": result.outcome,
                "verified_score": result.verified_score,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.total_tokens,
                "latency_ms": result.latency_ms,
                "core_receipt_digest": result.core_receipt_digest,
                "llm_receipt_digest": result.llm_receipt_digest,
                "policy_digest": result.policy_digest,
                "logical_run_id": result.logical_run_id,
                "run_dir": new_dirs[0],
            }
        )
        last = {
            "status": "bound",
            "outcome": result.outcome,
            "verified_score": result.verified_score,
        }
    else:
        return _record_unresolved(state, deps, target, result.reason)
    deps.checkpoints.write(
        POST_BINDING, {"attempt_id": attempt_id, "evidence_status": result.status}
    )
    return set_current(state, last_evidence=last, status="evidence_collected")


def _record_pair_terminal(deps, latest, launches):
    """Idempotent per terminal attempt: a restart after a terminal crash must
    not append a second decision for the same pair."""
    if any(
        record.get("event") == "attempt_terminal"
        and record.get("attempt_id") == latest["attempt_id"]
        for record in deps.decision_log.records()
    ):
        return
    resolution = _resolved_event(deps, latest["attempt_id"])
    record = {"event": "attempt_terminal", "attempt_id": latest["attempt_id"]}
    if resolution is not None and resolution["event"] == "evidence_bound":
        record["outcome"] = resolution.get("outcome")
    else:
        record["reason"] = "k_exhausted"
        record["launched"] = len(launches)
    deps.decision_log.append(record)


def node_retry_or_finish(state, deps):
    """The append-only log is the single source of truth for terminal vs retry
    (PR #20), so a crash before this node resumes to the identical decision —
    recorded exactly once per pair. A fully declined pair is terminal with no
    attempt record: the decline decision advances the work queue."""
    condition, seed = state["condition"], state["semilla"]
    kind, latest = _pair_state(deps, condition, seed)
    launches = _pair_launches(deps, condition, seed)
    if kind == "fresh" and _has_pair_event(deps, "launch_declined", condition, seed):
        if not _has_pair_event(deps, "pair_declined", condition, seed):
            deps.decision_log.append(
                {"event": "pair_declined", "condition": condition, "seed": seed}
            )
        kind, latest = "terminal", None
    if kind == "retry":
        if not any(
            record.get("event") == "retry_decision"
            and record.get("condition") == condition
            and record.get("seed") == seed
            and record.get("next_attempt_ordinal") == len(launches) + 1
            for record in deps.decision_log.records()
        ):
            deps.decision_log.append(
                {
                    "event": "retry_decision",
                    "condition": condition,
                    "seed": seed,
                    "next_attempt_ordinal": len(launches) + 1,
                }
            )
        return set_current(state, status="retry")
    if kind != "terminal":
        raise HarnessGraphError(
            f"retry_or_finish reached with pair state {kind!r} for seed {seed}"
        )
    if latest is not None:
        _record_pair_terminal(deps, latest, launches)
    index = state["work_index"] + 1
    if index < state["work_count"]:
        return set_current(state, work_index=index, status="next_work")
    return set_current(state, status="work_done")


def node_aggregate_leaderboard(state, deps):
    declines = [
        r for r in deps.decision_log.records() if r.get("event") == "launch_declined"
    ]
    rows = leaderboard.aggregate(deps.attempt_log.records(), declines, deps.config)
    leaderboard.write_leaderboard(deps.layout["leaderboard.json"], rows, deps.config)
    deps.decision_log.append({"event": "leaderboard_written", "rows": len(rows)})
    return set_current(state, status="leaderboard_written")


def route_after_plan(state, deps):
    """Routes purely from reconstructed log state — never in-memory state —
    so a restart resumes to the identical edge (PR #20; R1 extension: a
    standing grant or decline is replayed, never re-prompted)."""
    condition, seed = state["condition"], state["semilla"]
    kind, _ = _pair_state(deps, condition, seed)
    if kind == "collect":
        return "collect_and_bind_evidence"
    if kind == "terminal":
        return "retry_or_finish"
    if kind == "retry":
        return "merge_and_freeze_context"
    if condition == "R1":
        if _has_pair_event(deps, "launch_declined", condition, seed):
            return "retry_or_finish"
        if _has_pair_event(deps, "approval_granted", condition, seed):
            return "merge_and_freeze_context"
        return "approve_r1"
    return "merge_and_freeze_context"


def route_after_approval(state):
    return _APPROVAL_ROUTES[state["status"]]


def route_after_retry(state):
    return _RETRY_ROUTES[state["status"]]


_NODES = {
    "freeze_tournament_config": node_freeze_tournament_config,
    "assign_work": node_assign_work,
    "plan_context": node_plan_context,
    "approve_r1": node_approve_r1,
    "merge_and_freeze_context": node_merge_and_freeze_context,
    "register_launch": node_register_launch,
    "launch_subprocess": node_launch_subprocess,
    "collect_and_bind_evidence": node_collect_and_bind_evidence,
    "retry_or_finish": node_retry_or_finish,
    "aggregate_leaderboard": node_aggregate_leaderboard,
}


def build_graph(deps):
    """Compile the LangGraph topology — the only LangGraph import in harness/."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise HarnessGraphError(
            "the optional 'harness' extra (langgraph 1.2.11) is not installed"
        ) from exc

    functions = _NODES
    builder = StateGraph(HarnessState)
    for name, function in functions.items():
        builder.add_node(name, lambda current, fn=function: fn(current, deps))
    builder.add_edge(START, "freeze_tournament_config")
    builder.add_edge("freeze_tournament_config", "assign_work")
    builder.add_edge("assign_work", "plan_context")
    builder.add_conditional_edges(
        "plan_context",
        lambda current: route_after_plan(current, deps),
        _PLAN_ROUTES,
    )
    builder.add_conditional_edges(
        "approve_r1",
        route_after_approval,
        _APPROVAL_ROUTES,
    )
    builder.add_edge("merge_and_freeze_context", "register_launch")
    builder.add_edge("register_launch", "launch_subprocess")
    builder.add_edge("launch_subprocess", "collect_and_bind_evidence")
    builder.add_edge("collect_and_bind_evidence", "retry_or_finish")
    builder.add_conditional_edges("retry_or_finish", route_after_retry, _RETRY_ROUTES)
    builder.add_edge("aggregate_leaderboard", END)
    return builder.compile()
