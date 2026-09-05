"""LangGraph topology for the R2/R1/R3 tournament paths (design §6-§8).

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
silently re-proposed. The answer itself is durable first: the POST_ANSWER
checkpoint stores the full validated answer write-ahead (guidance
included), and a restart replays an answer whose decision event never
reached the append-only log — the human is never re-prompted for an answer
already given, and the replay is idempotent (PR #22).

The R3 round barrier opens a round r > 1 only when every fixed branch EITHER
is terminal from an earlier round OR has launched and resolved its round-(r-1)
attempt: a prior-round launch without evidence resolution waits
(``evidence_pending``) and a fresh active branch that never launched its prior
round also blocks the round (``missing_prior_round``) — the barrier drains
deterministically to that branch's own plan/launch flow so its missing attempt
runs and resolves before the round can freeze (PR #24 review round 2). R3
discovery admission is write-ahead: the candidate channel is validated once
and sealed into the append-only discovery log (``discovery_prepared``) BEFORE
the terminal ``evidence_bound`` append, so a crash in the bind→admit window
promotes exactly once from the sealed bytes on restart and discoveries.json is
never re-read after the bind.
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
    versions,
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
# unattended batch completes them before reaching R1 prompts, and the R3
# fixed three-branch rounds run last (Unit 5).
IMPLEMENTED_CONDITIONS = ("R2", "R1", "R3")
AGENT_ID = "agent-01"

_RETRY_ROUTES = {
    "retry": "register_launch",
    "next_work": "plan_context",
    "work_done": "aggregate_leaderboard",
}
# LangGraph's path_map is indexed by the ROUTER'S RETURN VALUE. The retry
# router returns the mapped DESTINATION (the walker/driver contract), so the
# compiled path_map must be identity over destinations — passing the
# status-keyed _RETRY_ROUTES makes langgraph raise KeyError on the first
# routed destination ("plan_context") in the compiled path only.
_RETRY_PATH_ROUTES = {
    destination: destination for destination in _RETRY_ROUTES.values()
}

_PLAN_ROUTES = {
    "merge_and_freeze_context": "merge_and_freeze_context",
    "barrier_merge_r3": "barrier_merge_r3",
    "collect_and_bind_evidence": "collect_and_bind_evidence",
    "retry_or_finish": "retry_or_finish",
    "approve_r1": "approve_r1",
}

_APPROVAL_ROUTES = {
    "approval_granted": "merge_and_freeze_context",
    "approval_edited": "approve_r1",
    "launch_declined": "retry_or_finish",
}
# LangGraph path_map is indexed by the ROUTER'S RETURN VALUE; approval and
# retry routers return mapped DESTINATIONS, so their compiled path maps must
# be identity over destinations (see _RETRY_PATH_ROUTES below).
_APPROVAL_PATH_ROUTES = {
    destination: destination for destination in _APPROVAL_ROUTES.values()
}
_BARRIER_ROUTES = {
    "register_launch": "register_launch",
    "collect_and_bind_evidence": "collect_and_bind_evidence",
    "plan_context": "plan_context",
}
_ANSWER_EVENTS = ("approval_edit", "approval_granted", "launch_declined")


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
    discovery_log: logs.AppendOnlyLog
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
    discovery_log = logs.AppendOnlyLog(layout["discoveries.jsonl"])
    attempt_log.verify()
    decision_log.verify()
    discovery_log.verify()
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
        discovery_log=discovery_log,
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


def _agent_id(config, condition, seed):
    """R3 owns one fixed agent per seed (agent-01->18, agent-02->24,
    agent-03->30) with no duplicate ownership; every other condition runs the
    single reference agent."""
    if condition != "R3":
        return AGENT_ID
    try:
        index = config["seeds"].index(int(seed))
    except ValueError as exc:
        raise HarnessGraphError(
            f"R3 seed {seed!r} is not owned by any fixed branch"
        ) from exc
    return f"agent-{index + 1:02d}"


def _attempt_id(config, condition, seed, ordinal):
    agent = _agent_id(config, condition, seed)
    return f"{condition.lower()}-{agent}-seed-{seed}-attempt-{ordinal:02d}"


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
    """R2/R1/R3 are implemented; an unknown condition defers explicitly,
    never silently."""
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
            "source": (
                "r1-approval-pipeline"
                if condition == "R1"
                else "r3-round-barrier"
                if condition == "R3"
                else "null-first-route"
            ),
        }
    )
    return set_current(
        state,
        condition=condition,
        semilla=seed,
        agent_id=_agent_id(deps.config, condition, seed),
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


def _has_answer_event(deps, condition, seed, round_number):
    """Whether the decision event for this exact prompt slot already exists."""
    return any(
        record.get("event") in _ANSWER_EVENTS
        and record.get("condition") == condition
        and record.get("seed") == seed
        and record.get("proposal_round") == round_number
        for record in deps.decision_log.records()
    )


def _unconsumed_post_answer(deps, condition, seed, round_number):
    """The durable write-ahead answer for this prompt slot, if any (PR #22).

    POST_ANSWER is written BEFORE its decision event, so a crash in that
    window would lose the human's answer to a re-prompt. The record for this
    exact (condition, seed, proposal_round) slot is returned only while its
    decision event is still absent from the append-only log; once the event
    lands, the record is consumed and restart routing reads the log.
    """
    if _has_answer_event(deps, condition, seed, round_number):
        return None
    found = None
    for record in deps.checkpoints.records():
        if record.get("boundary") != POST_ANSWER:
            continue
        snapshot = record.get("state")
        if not isinstance(snapshot, dict):
            continue
        if (
            snapshot.get("condition") == condition
            and snapshot.get("seed") == seed
            and snapshot.get("proposal_round") == round_number
        ):
            found = snapshot
    if found is None:
        return None
    answer = found.get("answer")
    if not isinstance(answer, dict):
        raise HarnessGraphError(
            "durable POST_ANSWER record carries no replayable answer object"
        )
    return answer


def _record_answer(deps, condition, seed, round_number, answer):
    """The single decision-log write door for one answered prompt: the live
    terminal path and the write-ahead replay append exactly this event."""
    decision = answer["decision"]
    if decision == "yes":
        deps.decision_log.append(
            {
                "event": "approval_granted",
                "condition": condition,
                "seed": seed,
                "proposal_round": round_number,
            }
        )
        return "approval_granted"
    if decision == "edit":
        deps.decision_log.append(
            {
                "event": "approval_edit",
                "condition": condition,
                "seed": seed,
                "proposal_round": round_number,
                "guidance": list(answer["guidance"]),
            }
        )
        return "approval_edited"
    deps.decision_log.append(
        {
            "event": "launch_declined",
            "condition": condition,
            "seed": seed,
            "proposal_round": round_number,
        }
    )
    return "launch_declined"


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

    The validated answer is persisted write-ahead in the POST_ANSWER
    checkpoint (the full answer, guidance included) BEFORE its decision
    event is appended. A restart that finds a POST_ANSWER whose decision
    event never reached the append-only log replays that exact durable
    answer instead of re-prompting the human (PR #22); the replay is
    idempotent, so a double restart never duplicates the event.
    """
    if deps.approval is None:
        raise HarnessGraphError(
            "R1 requires an injectable approval terminal; "
            "there is no silent auto-approval"
        )
    condition, seed = state["condition"], state["semilla"]
    round_number = _proposal_round(deps, condition, seed)
    durable = _unconsumed_post_answer(deps, condition, seed, round_number)
    if durable is not None:
        interruption.validate_answer(durable)
        return set_current(
            state,
            status=_record_answer(deps, condition, seed, round_number, durable),
        )
    view = {
        "condition": condition,
        "seed": seed,
        "proposal_round": round_number,
        "proposed_attempt_id": _attempt_id(
            deps.config, condition, seed, len(_pair_launches(deps, condition, seed)) + 1
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
            "answer": dict(answer),
        },
    )
    return set_current(
        state, status=_record_answer(deps, condition, seed, round_number, answer)
    )


def _next_snapshot_ordinal(deps):
    """Global monotonic snapshot ordinal derived from the append-only log."""
    return sum(
        1
        for record in deps.decision_log.records()
        if record.get("event") == "context_snapshot_frozen"
    )


def _r3_block_start(config):
    """First work-queue index of the R3 fixed three-branch block."""
    return len(work_items(config)) - len(config["seeds"])


def _r3_active(deps):
    """Whether any fixed branch can still launch a future round."""
    return any(
        _pair_state(deps, "R3", str(seed))[0] in ("fresh", "collect", "retry")
        for seed in deps.config["seeds"]
    )


def _round_snapshot_ref(deps, round_number):
    """The frozen snapshot reference for an R3 round, rebuilt from the
    append-only decision log; None while the round is not frozen yet."""
    for record in deps.decision_log.records():
        if (
            record.get("event") == "context_snapshot_frozen"
            and record.get("condition") == "R3"
            and record.get("round") == round_number
        ):
            ordinal = record["snapshot_ordinal"]
            return {
                "path": str(
                    deps.layout["contexts"] / f"strategy-context-{ordinal:03d}.json"
                ),
                "digest": record["context_digest"],
                "snapshot_ordinal": ordinal,
            }
    return None


def _r3_round_pending(deps, round_number):
    """Branches still blocking the round barrier for the opening round
    ``round_number``. For every round r > 1 each fixed branch must EITHER be
    terminal from an earlier round (completed or K-exhausted) OR have launched
    AND resolved its required prior attempt (ordinal r - 1): a prior-round
    launch whose evidence never resolved waits with ``evidence_pending``, and
    a fresh active branch that never launched its prior round waits with
    ``missing_prior_round`` — a round can never freeze without every branch's
    prior-round evidence (explicit waiting, never a silent skip)."""
    waiting = []
    for raw_seed in deps.config["seeds"]:
        seed = str(raw_seed)
        required = round_number - 1
        launches = _pair_launches(deps, "R3", seed)
        prior = next(
            (item for item in launches if item["attempt_ordinal"] == required),
            None,
        )
        if prior is not None:
            if _resolved_event(deps, prior["attempt_id"]) is None:
                waiting.append(
                    {
                        "seed": seed,
                        "attempt_id": prior["attempt_id"],
                        "reason": "evidence_pending",
                    }
                )
            continue
        kind, latest = _pair_state(deps, "R3", seed)
        if kind == "terminal":
            continue
        if kind == "collect":
            if latest is None:
                raise HarnessGraphError(
                    f"R3 round {round_number} collect state cannot be "
                    f"reconstructed for seed {seed}"
                )
            waiting.append(
                {
                    "seed": seed,
                    "attempt_id": latest["attempt_id"],
                    "reason": "evidence_pending",
                }
            )
            continue
        if required >= 1:
            waiting.append(
                {
                    "seed": seed,
                    "attempt_id": None,
                    "reason": "missing_prior_round",
                }
            )
    return waiting


def _r3_first_pending(deps, round_number):
    """The first entry blocking the round barrier, ordered by
    ``(attempt_ordinal, seed)`` — the deterministic barrier drain target;
    None while the round is not blocked."""
    entries = _r3_round_pending(deps, round_number)
    if not entries:
        return None
    ranked = []
    for entry in entries:
        if entry["attempt_id"] is None:
            ranked.append((round_number - 1, entry["seed"], entry))
            continue
        launch = next(
            item
            for item in _pair_launches(deps, "R3", entry["seed"])
            if item["attempt_id"] == entry["attempt_id"]
        )
        ranked.append((launch["attempt_ordinal"], entry["seed"], entry))
    return min(ranked, key=lambda item: (item[0], item[1]))[2]


_CANDIDATE_KEYS = {"schema_version", "discoveries"}
_CANDIDATE_RECORD_KEYS = {"explorer_id", "discovery_ordinal", "text"}


def _natural(value, minimum):
    return not isinstance(value, bool) and isinstance(value, int) and value >= minimum


def _load_candidates(deps, attempt_id):
    """Harness-owned discovery channel for one attempt (design §6): a branch
    output artifact read fail-closed; its absence simply means the branch
    found nothing. Discovery text never touches core state, receipts, or
    replay inputs — only this tournament-side artifact."""
    path = deps.tournament_root / "attempts" / attempt_id / "discoveries.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_bytes())
    except json.JSONDecodeError as exc:
        raise HarnessGraphError(
            f"discovery channel artifact unreadable: {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != _CANDIDATE_KEYS:
        raise HarnessGraphError(
            "discovery channel artifact must have exactly {schema_version, discoveries}"
        )
    version = payload["schema_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != versions.DISCOVERIES_SCHEMA_VERSION
    ):
        raise HarnessGraphError("discovery channel schema_version mismatch")
    if not isinstance(payload["discoveries"], list):
        raise HarnessGraphError("discovery channel discoveries must be a list")
    for candidate in payload["discoveries"]:
        if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_RECORD_KEYS:
            raise HarnessGraphError(
                "discovery candidate must have exactly "
                "{explorer_id, discovery_ordinal, text}"
            )
        if (
            not isinstance(candidate["explorer_id"], str)
            or not candidate["explorer_id"]
        ):
            raise HarnessGraphError("discovery explorer_id must be a non-empty string")
        if not _natural(candidate["discovery_ordinal"], 0):
            raise HarnessGraphError("discovery discovery_ordinal must be an int >= 0")
        if not isinstance(candidate["text"], str) or not candidate["text"]:
            raise HarnessGraphError("discovery text must be a non-empty string")
    return payload["discoveries"]


def _discovery_identity(record):
    return (
        record["source_attempt_id"],
        record["explorer_id"],
        record["discovery_ordinal"],
    )


def _enriched_discovery(deps, launch, candidate, bound):
    """The §2.1 discovery record for one candidate, mirror-validated BEFORE it
    can be admitted or frozen: producing_condition exactly R3, producing
    attempt ordinal >= 1, typed receipt digests, unique declared identity."""
    record = {
        "source_attempt_id": launch["attempt_id"],
        "explorer_id": candidate["explorer_id"],
        "discovery_ordinal": candidate["discovery_ordinal"],
        "producing_condition": "R3",
        "producing_agent_id": launch["agent_id"],
        "producing_seed": launch["seed"],
        "producing_attempt_ordinal": launch["attempt_ordinal"],
        "evidence": {
            "run_status": "finished",
            "core_receipt_digest": bound["core_receipt_digest"],
            "llm_receipt_digest": bound["llm_receipt_digest"],
        },
        "text": candidate["text"],
    }
    probe = {
        "schema_version": versions.CONTEXT_SCHEMA_VERSION,
        "source_tournament_id": deps.config["tournament_id"],
        "guidance": [],
        "discoveries": [record],
    }
    try:
        strategy_context.validate_context(probe)
    except strategy_context.ContextError as exc:
        raise HarnessGraphError(
            f"discovery violates the AMEND-4A-3 mirror contract: {exc}"
        ) from exc
    return record


def _prepared_discoveries(deps, attempt_id):
    """The immutable sealed discovery content for one attempt (the write-ahead
    seam), or None while the attempt never sealed anything. Multiple identical
    ``discovery_prepared`` records collapse to one; conflicting content fails
    closed."""
    prepared = []
    for record in deps.discovery_log.records():
        if (
            record.get("event") == "discovery_prepared"
            and record.get("attempt_id") == attempt_id
        ):
            discoveries = record.get("discoveries")
            if not isinstance(discoveries, list):
                raise HarnessGraphError(
                    f"sealed discovery record for {attempt_id} is malformed"
                )
            prepared.append(discoveries)
    if not prepared:
        return None
    first = prepared[0]
    for other in prepared[1:]:
        if strategy_context.canonical_json_bytes(
            other
        ) != strategy_context.canonical_json_bytes(first):
            raise HarnessGraphError(
                f"conflicting sealed discovery content for {attempt_id}"
            )
    return first


def _seal_discoveries(deps, launch, result):
    """Write-ahead admission seam (PR #24 review round 2): the attempt's
    candidate channel is validated ONCE and its enriched records are SEALED
    into the append-only discovery log BEFORE the terminal evidence_bound
    append. A crash in the bind→admit window can therefore always promote the
    discovery from these immutable bytes on restart, and the mutable
    discoveries.json is never re-read after the bind. Sealing is idempotent
    per attempt: an identical repeat skips, conflicting content fails
    closed."""
    attempt_id = launch["attempt_id"]
    digests = {
        "core_receipt_digest": result.core_receipt_digest,
        "llm_receipt_digest": result.llm_receipt_digest,
    }
    enriched = [
        _enriched_discovery(deps, launch, candidate, digests)
        for candidate in _load_candidates(deps, attempt_id)
    ]
    if not enriched:
        return
    prior = _prepared_discoveries(deps, attempt_id)
    if prior is not None:
        if strategy_context.canonical_json_bytes(
            prior
        ) != strategy_context.canonical_json_bytes(enriched):
            raise HarnessGraphError(
                f"conflicting sealed discovery content for {attempt_id}"
            )
        return
    deps.discovery_log.append(
        {
            "event": "discovery_prepared",
            "attempt_id": attempt_id,
            "condition": "R3",
            "seed": launch["seed"],
            "agent_id": launch["agent_id"],
            "attempt_ordinal": launch["attempt_ordinal"],
            "discoveries": enriched,
        }
    )


def record_discoveries(deps, attempt_id=None):
    """Promote every finished receipt-bound R3 run's SEALED discovery content
    into the DiscoveryStore exactly once (§8 node 9).

    ADMISSION into the DiscoveryStore is separate from NEXT-ROUND SNAPSHOT
    creation. Admission content comes ONLY from the immutable
    ``discovery_prepared`` records that the write-ahead seam sealed before
    each bind (see :func:`_seal_discoveries`): the mutable discoveries.json is
    never re-read after the bind, so a late-appearing or mutated candidate
    file can never change what an admission — and therefore a snapshot —
    contains, and a bound attempt without sealed content has nothing to
    admit. The round barrier composes ONLY already-admitted records.
    Admission is idempotent per declared identity: an identical repeat skips,
    and conflicting content under one identity fails closed. ``attempt_id``
    targets one bound attempt (the default sweeps every bound attempt); a
    targeted unbound attempt fails closed."""
    existing = {}
    for record in deps.discovery_log.records():
        if record.get("event") == "discovery_admitted":
            existing[_discovery_identity(record["discovery"])] = record["discovery"]
    if attempt_id is None:
        bound = {
            r["attempt_id"]: r
            for r in deps.attempt_log.records()
            if r.get("event") == "evidence_bound" and r.get("condition") == "R3"
        }
    else:
        record = next(
            (
                r
                for r in deps.attempt_log.records()
                if r.get("event") == "evidence_bound"
                and r.get("condition") == "R3"
                and r.get("attempt_id") == attempt_id
            ),
            None,
        )
        if record is None:
            raise HarnessGraphError(
                f"cannot admit discoveries for unbound attempt {attempt_id}"
            )
        bound = {attempt_id: record}
    launched = {
        r["attempt_id"]: r
        for r in deps.attempt_log.records()
        if r.get("event") == "launch_registered" and r.get("condition") == "R3"
    }
    admitted = 0
    for bound_id in sorted(bound):
        launch = launched[bound_id]
        sealed = _prepared_discoveries(deps, bound_id)
        if sealed is None:
            continue
        for enriched in sealed:
            identity = _discovery_identity(enriched)
            prior = existing.get(identity)
            if prior is not None:
                if strategy_context.canonical_json_bytes(
                    prior
                ) != strategy_context.canonical_json_bytes(enriched):
                    raise HarnessGraphError(
                        f"conflicting duplicate discovery identity: {identity}"
                    )
                continue
            deps.discovery_log.append(
                {
                    "event": "discovery_admitted",
                    "attempt_id": bound_id,
                    "condition": "R3",
                    "seed": launch["seed"],
                    "discovery": enriched,
                }
            )
            existing[identity] = enriched
            admitted += 1
    return admitted


def _r3_merged_discoveries(deps):
    """All admitted discoveries in declared-identity order. Duplicates by
    identity collapse to one record; conflicting duplicates fail closed.
    Arrival/completion order is never an input."""
    merged = {}
    for record in deps.discovery_log.records():
        if record.get("event") != "discovery_admitted":
            continue
        discovery = record["discovery"]
        identity = _discovery_identity(discovery)
        prior = merged.get(identity)
        if prior is not None and strategy_context.canonical_json_bytes(
            prior
        ) != strategy_context.canonical_json_bytes(discovery):
            raise HarnessGraphError(
                f"conflicting duplicate discovery identity: {identity}"
            )
        merged[identity] = discovery
    return [merged[key] for key in sorted(merged)]


def _r3_round_context(deps):
    """The merged DiscoverySnapshot value for the next round: canonical null
    while nothing is admitted, else the exact §2.1 object. Only this
    tournament's receipt-bound discoveries can appear, and
    source_tournament_id is always the frozen current tournament (importing a
    prior tournament would require an explicit frozen context_source_policy,
    which the first slice does not provide)."""
    discoveries = _r3_merged_discoveries(deps)
    if not discoveries:
        return None
    return {
        "schema_version": versions.CONTEXT_SCHEMA_VERSION,
        "source_tournament_id": deps.config["tournament_id"],
        "guidance": [],
        "discoveries": discoveries,
    }


def node_barrier_merge_r3(state, deps):
    """R3 round barrier (design §6). The deterministic merge runs ONLY after
    every contributing branch round is resolved: for the opening round r > 1,
    every fixed branch must EITHER be terminal from an earlier round OR have
    launched and resolved its round-(r-1) attempt. A PRIOR-ROUND LAUNCH
    WITHOUT EVIDENCE RESOLUTION blocks the merge with ``evidence_pending``
    waiting evidence, and a fresh active branch that never launched its prior
    round blocks it with ``missing_prior_round`` — the barrier then DRAINS
    deterministically by routing to the first pending entry by
    ``(attempt_ordinal, seed)``: to that attempt's collector (a bind, or the
    fail-closed U3 crash-window classification for the registered-unobserved
    window), or to the branch's own plan/launch flow so its missing attempt
    runs and resolves before the round can freeze. An already-resolved retry
    pair never blocks — it participates in the next round. Before composing,
    the barrier promotes every bound attempt's SEALED discovery content
    exactly once (the restart crash window of the write-ahead admission seam),
    so the merge composes only ALREADY-ADMITTED records; it sorts by declared
    identity ``(source_attempt_id, explorer_id, discovery_ordinal)`` with no
    LLM coordinator, ranking, dedup-by-content, or completion-timing input;
    and ONE identical snapshot per round is frozen before any branch of that
    round launches, so an in-flight run can never contribute to (or see) its
    own round's snapshot. The merge is restart-idempotent: a crash after the
    atomic file write but before the frozen event rewrites byte-identical
    content at the same ordinal, and a crash after the event reuses the frozen
    reference from the log."""
    condition, seed = state["condition"], state["semilla"]
    round_number = len(_pair_launches(deps, condition, seed)) + 1
    # restart crash-window promotion (write-ahead admission seam): a bind
    # whose targeted admission was interrupted promotes exactly once from the
    # sealed bytes BEFORE the round composes or drains
    record_discoveries(deps)
    ref = _round_snapshot_ref(deps, round_number)
    if ref is not None:
        return set_current(state, context_snapshot_ref=ref, status="context_frozen")
    waiting = _r3_round_pending(deps, round_number)
    if waiting:
        already = any(
            record.get("event") == "round_barrier_waiting"
            and record.get("round") == round_number
            and record.get("waiting") == waiting
            for record in deps.decision_log.records()
        )
        if not already:
            deps.decision_log.append(
                {
                    "event": "round_barrier_waiting",
                    "round": round_number,
                    "waiting": waiting,
                }
            )
        pending = _r3_first_pending(deps, round_number)
        if pending is None:
            raise HarnessGraphError(
                f"R3 round {round_number} pending launch cannot be "
                f"reconstructed: {waiting}"
            )
        kind, latest = _pair_state(deps, "R3", pending["seed"])
        if pending["reason"] == "missing_prior_round":
            # the branch never launched its prior round: route through its
            # own plan/launch flow so the missing attempt runs and resolves
            # BEFORE this round can freeze
            if kind not in ("fresh", "retry"):
                raise HarnessGraphError(
                    f"R3 round {round_number} pending launch cannot be "
                    f"reconstructed: {waiting}"
                )
            return set_current(
                state,
                condition="R3",
                semilla=pending["seed"],
                agent_id=_agent_id(deps.config, "R3", pending["seed"]),
                work_index=work_items(deps.config).index(("R3", pending["seed"])),
                status="barrier_missing_round",
            )
        if (
            kind != "collect"
            or latest is None
            or latest["attempt_id"] != pending["attempt_id"]
        ):
            raise HarnessGraphError(
                f"R3 round {round_number} pending launch cannot be "
                f"reconstructed: {waiting}"
            )
        return set_current(
            state,
            condition="R3",
            semilla=pending["seed"],
            agent_id=_agent_id(deps.config, "R3", pending["seed"]),
            status="barrier_drain",
        )
    context = _r3_round_context(deps)
    ordinal = _next_snapshot_ordinal(deps)
    path = deps.layout["contexts"] / f"strategy-context-{ordinal:03d}.json"
    try:
        frozen = strategy_context.freeze_snapshot(context, path)
    except strategy_context.ContextError as exc:
        raise HarnessGraphError(
            f"context snapshot {path} cannot be frozen: {exc}"
        ) from exc
    deps.decision_log.append(
        {
            "event": "context_snapshot_frozen",
            "condition": "R3",
            "seed": seed,
            "round": round_number,
            "snapshot_ordinal": ordinal,
            "context_digest": frozen["digest"],
            "source": "r3-merge",
        }
    )
    return set_current(
        state,
        context_snapshot_ref={**frozen, "snapshot_ordinal": ordinal},
        status="context_frozen",
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
    try:
        frozen = strategy_context.freeze_snapshot(context, path)
    except strategy_context.ContextError as exc:
        raise HarnessGraphError(
            f"context snapshot {path} cannot be frozen: {exc}"
        ) from exc
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
    attempt_id = _attempt_id(deps.config, condition, seed, ordinal)
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
            "agent_id": _agent_id(deps.config, condition, seed),
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
            "agent_id": _agent_id(deps.config, condition, seed),
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
        if condition == "R3":
            # write-ahead admission seam (PR #24 review round 2): the
            # candidate channel is validated ONCE and its enriched records
            # are sealed into the append-only discovery log BEFORE the
            # terminal evidence_bound append, so a crash in the bind→admit
            # window can always promote exactly once from the sealed bytes
            # on restart — discoveries.json is never re-read after the bind
            _seal_discoveries(deps, target, result)
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
        if condition == "R3":
            # deterministic ADMISSION at bind time (§8 node 9): the
            # just-bound attempt's candidates enter the DiscoveryStore now,
            # never via a later barrier sweep of files
            record_discoveries(deps, attempt_id)
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
    if kind == "retry" and condition == "R3":
        # one launch per branch per round: this branch's retry is its NEXT
        # round, re-entered through the barrier after the queue wraps
        index = state["work_index"] + 1
        if index < state["work_count"]:
            return set_current(state, work_index=index, status="next_work")
        return set_current(
            state, work_index=_r3_block_start(deps.config), status="next_work"
        )
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
    if condition == "R3" and index >= state["work_count"]:
        # the round-major queue wraps for the next round while any branch can
        # still launch; an all-terminal field finishes the tournament
        if _r3_active(deps):
            return set_current(
                state, work_index=_r3_block_start(deps.config), status="next_work"
            )
        return set_current(state, status="work_done")
    if index < state["work_count"]:
        return set_current(state, work_index=index, status="next_work")
    return set_current(state, status="work_done")


def node_aggregate_leaderboard(state, deps):
    # mandatory terminal admission sweep: a bind whose targeted admission
    # was interrupted by a crash is admitted exactly once before the rows
    # freeze (idempotent; a no-op for tournaments without bound R3 runs)
    record_discoveries(deps)
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
        return "barrier_merge_r3" if condition == "R3" else "merge_and_freeze_context"
    if condition == "R1":
        if _has_pair_event(deps, "launch_declined", condition, seed):
            return "retry_or_finish"
        if _has_pair_event(deps, "approval_granted", condition, seed):
            return "merge_and_freeze_context"
        return "approve_r1"
    if condition == "R3":
        return "barrier_merge_r3"
    return "merge_and_freeze_context"


def route_after_approval(state):
    return _APPROVAL_ROUTES[state["status"]]


def route_after_barrier(state):
    if state["status"] == "barrier_missing_round":
        return "plan_context"
    if state["status"] == "barrier_drain":
        return "collect_and_bind_evidence"
    return "register_launch"


def route_after_retry(state):
    return _RETRY_ROUTES[state["status"]]


_NODES = {
    "freeze_tournament_config": node_freeze_tournament_config,
    "assign_work": node_assign_work,
    "plan_context": node_plan_context,
    "approve_r1": node_approve_r1,
    "barrier_merge_r3": node_barrier_merge_r3,
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
        _APPROVAL_PATH_ROUTES,
    )
    builder.add_edge("merge_and_freeze_context", "register_launch")
    builder.add_conditional_edges(
        "barrier_merge_r3",
        route_after_barrier,
        _BARRIER_ROUTES,
    )
    builder.add_edge("register_launch", "launch_subprocess")
    builder.add_edge("launch_subprocess", "collect_and_bind_evidence")
    builder.add_edge("collect_and_bind_evidence", "retry_or_finish")
    builder.add_conditional_edges(
        "retry_or_finish", route_after_retry, _RETRY_PATH_ROUTES
    )
    builder.add_edge("aggregate_leaderboard", END)
    return builder.compile()
