"""LangGraph topology for the R2 autonomous tournament path (design §6-§8).

This module is the ONLY LangGraph home in ``harness/``: it imports LangGraph
lazily inside :func:`build_graph`, so every pure node/route function below is
testable without the optional extra (the compile test skips locally and runs
in CI against langgraph 1.2.11).

Nodes own all writes; the two conditional edges only inspect state/logs and
route. R2 is autonomous: one agent, no human prompt, no discoveries, no
silent fallback. Retry semantics: only failures, timeouts, non-zero exits,
and missing/invalid/unresolved evidence route to a NEW attempt ordinal,
until exactly K launched attempts; a first valid receipt — completed or a
safe abort — is terminal and is never rerun. The registered-but-unbound
crash window is ambiguous: it is counted as a launch, never rerun under the
same ``attempt_id``, and recovery records an explicit event plus a new
ordinal.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import (
    checkpointing,
    evidence,
    leaderboard,
    logs,
    strategy_context,
    subprocess_adapter,
    tournament,
)
from .checkpointing import POST_BINDING, POST_REGISTRATION, PRE_LAUNCH
from .state import HarnessState, set_current

CONDITION = "R2"
AGENT_ID = "agent-01"

_RETRY_ROUTES = {
    "retry": "register_launch",
    "next_work": "plan_context",
    "work_done": "aggregate_leaderboard",
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


def build_deps(root, config, runner=subprocess_adapter.local_runner, timeout=None):
    """Validate the config and bind the sealed layout, logs, and checkpoints."""
    tournament.validate_config(config)
    root = Path(root)
    layout = tournament.ensure_layout(
        root, config["tournament_id"], config["artifact_root"]
    )
    attempt_log = logs.AppendOnlyLog(layout["attempt-log.jsonl"])
    decision_log = logs.AppendOnlyLog(layout["decision-log.jsonl"])
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
    )


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


def _pending_attempt(deps, launches):
    observed = {
        r["attempt_id"]
        for r in deps.attempt_log.records()
        if r.get("event") == "launch_observed"
    }
    resolved = {
        r["attempt_id"]
        for r in deps.attempt_log.records()
        if r.get("event") in ("evidence_bound", "evidence_unresolved")
    }
    for record in reversed(launches):
        if (
            record["attempt_id"] not in observed
            and record["attempt_id"] not in resolved
        ):
            return record
    return None


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
    """R2 is the only implemented route; deferrals are explicit, never silent."""
    implemented = (CONDITION,)
    deferred = [c for c in deps.config["conditions"] if c not in implemented]
    if deferred:
        deps.decision_log.append({"event": "routes_deferred", "conditions": deferred})
    return set_current(
        state,
        work_index=0,
        work_count=len(deps.config["seeds"]),
        status="work_assigned",
    )


def node_plan_context(state, deps):
    """R2 first route: canonical null context; resume ordinal comes from the log."""
    seed = str(deps.config["seeds"][state["work_index"]])
    launches = _pair_launches(deps, CONDITION, seed)
    ordinal = max((r["attempt_ordinal"] for r in launches), default=0)
    deps.decision_log.append(
        {
            "event": "context_planned",
            "condition": CONDITION,
            "seed": seed,
            "source": "null-first-route",
        }
    )
    return set_current(
        state,
        condition=CONDITION,
        semilla=seed,
        agent_id=AGENT_ID,
        intentos=ordinal,
        attempt_id=None,
        last_evidence=None,
        status="context_planned",
    )


def node_merge_and_freeze_context(state, deps):
    """Freeze the canonical null snapshot before launch (§2.2)."""
    ordinal = state["work_index"]
    path = deps.layout["contexts"] / f"strategy-context-{ordinal:03d}.json"
    ref = strategy_context.freeze_snapshot(None, path)
    ref["snapshot_ordinal"] = ordinal
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


def node_retry_or_finish(state, deps):
    """First valid receipt (any outcome) is terminal; only failures retry to K."""
    condition, seed = state["condition"], state["semilla"]
    launches = _pair_launches(deps, condition, seed)
    last = state.get("last_evidence") or {}
    if last.get("status") == "bound":
        deps.decision_log.append(
            {
                "event": "attempt_terminal",
                "attempt_id": state["attempt_id"],
                "outcome": last["outcome"],
            }
        )
    elif len(launches) >= deps.config["k_attempts"]:
        deps.decision_log.append(
            {
                "event": "attempt_terminal",
                "attempt_id": state.get("attempt_id"),
                "reason": "k_exhausted",
                "launched": len(launches),
            }
        )
    else:
        deps.decision_log.append(
            {
                "event": "retry_decision",
                "condition": condition,
                "seed": seed,
                "next_attempt_ordinal": len(launches) + 1,
            }
        )
        return set_current(state, status="retry")
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
    """A registered attempt with no observation/evidence record routes to collect."""
    launches = _pair_launches(deps, state["condition"], state["semilla"])
    if _pending_attempt(deps, launches) is not None:
        return "collect_and_bind_evidence"
    return "merge_and_freeze_context"


def route_after_retry(state):
    return _RETRY_ROUTES[state["status"]]


_NODES = {
    "freeze_tournament_config": node_freeze_tournament_config,
    "assign_work": node_assign_work,
    "plan_context": node_plan_context,
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
        {
            "merge_and_freeze_context": "merge_and_freeze_context",
            "collect_and_bind_evidence": "collect_and_bind_evidence",
        },
    )
    builder.add_edge("merge_and_freeze_context", "register_launch")
    builder.add_edge("register_launch", "launch_subprocess")
    builder.add_edge("launch_subprocess", "collect_and_bind_evidence")
    builder.add_edge("collect_and_bind_evidence", "retry_or_finish")
    builder.add_conditional_edges("retry_or_finish", route_after_retry, _RETRY_ROUTES)
    builder.add_edge("aggregate_leaderboard", END)
    return builder.compile()
