"""Immutable validated evidence model and authoritative row assembly.

Slice A2b of Unit A (``stage4-langfuse-tracing``): frozen records for the
validated export model — guarded text, digest-bound provider requests,
receipt-owned outcomes and scores, trace-only rows — plus ``assemble_items``
(leaderboard rows bound to sealed run evidence) and ``build_trace_records``
(metadata-only, trace-ready records). ``plain`` is the single JSON-safe
projection and fails closed rather than ever serializing guarded text.

Trace-only identity amendment (v1): the design's ``trace-v1`` input list gives
trace-only rows null attempt/execution/digest slots, so two declined rows of
one tournament would collide on one ``tr_`` ID. ``ids.trace_only_trace_id``
derives their identity instead under the distinct ``trace-only-v1`` version
tag from the row's stable pair identity (condition, seed) plus the
leaderboard's authoritative attempt ordinals and declined-launch count; the
records below carry exactly those fields so Unit B can compose IDs without
re-reading artifacts.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from .artifacts import (
    IDENTITY_MISMATCH,
    MISSING_ARTIFACT,
    RECEIPT_NAME,
    SECRET_DETECTED,
    UNSUPPORTED_SCHEMA,
    ArtifactError,
    SkipRecord,
    discover_evidence,
    launch_path,
    load_launch,
    load_leaderboard,
)

TEXT_CAPABLE_FIELDS = ("raw_visible_response", "tool_call.arguments")

SCORE_NAME = "verified_score"
SCORE_DATA_TYPE = "NUMERIC"


class GuardedText:
    """Immutable text-capable value that fails closed under serialization.

    Wraps optional free text (responses, tool arguments) so it can exist in
    the validated model yet never leak: repr/str withhold content, attribute
    mutation is refused, and :func:`plain` raises ``secret_detected`` rather
    than projecting it. ``None`` values never become GuardedText; the null
    slot itself is preserved by the holding record.
    """

    __slots__ = ("_value",)

    def __init__(self, value):
        object.__setattr__(self, "_value", value)

    def __setattr__(self, name, value):
        raise AttributeError("GuardedText is immutable")

    def __delattr__(self, name):
        raise AttributeError("GuardedText is immutable")

    @property
    def value(self):
        return self._value

    @property
    def has_text(self):
        return self._value is not None

    def __repr__(self):
        return "GuardedText(<withheld>)"

    def __str__(self):
        return "<guarded-text>"

    def __eq__(self, other):
        if not isinstance(other, GuardedText):
            return NotImplemented
        return self._value == other._value

    def __hash__(self):
        return hash(("GuardedText", self._value))


def _frozen_map(value):
    """Read-only mapping view for required mapping slots (never None)."""
    return MappingProxyType(dict(value))


def _frozen_optional(value):
    """Read-only mapping view; None passes through as declared absence."""
    return None if value is None else _frozen_map(value)


@dataclass(frozen=True)
class ToolCall:
    """Structural tool call: name plus metadata; argument text stays guarded."""

    name: str
    structural_fields: Mapping
    arguments: GuardedText | None


@dataclass(frozen=True)
class ProviderRequest:
    """One digest-bound provider request; text only in guarded slots."""

    policy_step: int
    provider: str
    model: str
    request_id: str
    latency_ms: int
    usage: Mapping
    raw_visible_response: GuardedText | None
    tool_call: ToolCall | None
    error: Mapping | None


@dataclass(frozen=True)
class EvidenceDigests:
    """All relevant evidence digests; null slots are declared absence."""

    core_receipt: str | None
    provider_evidence: str | None
    llm_receipt: str | None
    policy: str | None
    strategy_context: str | None
    prompt: str | None
    tool_schema: str | None


def _null_digests():
    return EvidenceDigests(None, None, None, None, None, None, None)


@dataclass(frozen=True)
class ValidatedRun:
    """One bound leaderboard row joined with its receipt-owned run evidence."""

    tournament_id: str
    condition: str
    seed: str
    status: str
    outcome: str
    verified_score: int
    attempt_id: str
    attempt_ordinal: int
    execution_id: str
    logical_run_id: str
    requests: tuple
    launch: Mapping
    digests: EvidenceDigests
    attempts_used: int
    declined_launches: int
    harness_version: str
    core_version: str


@dataclass(frozen=True)
class TraceOnlyRow:
    """Leaderboard row with no bound run evidence; nothing is manufactured.

    ``outcome`` keeps the row's declared (always-null) leaderboard slot — an
    explicit null is authoritative absence. Receipt-owned slots the row cannot
    declare (verified_score, usage, latency) simply have no field, so no
    projection can invent a zero or a synthetic score for them.
    """

    tournament_id: str
    condition: str
    seed: str
    status: str
    outcome: str | None
    attempt_refs: tuple
    attempts_used: int
    declined_launches: int
    harness_version: str
    core_version: str
    digests: EvidenceDigests


@dataclass(frozen=True)
class GenerationRecord:
    """Metadata-only generation: renamed usage, structural tool call, no text."""

    policy_step: int
    provider: str
    model: str
    request_id: str
    latency_ms: int
    usage: Mapping
    tool_call: Mapping | None
    error: Mapping | None


@dataclass(frozen=True)
class ScoreRecord:
    """Receipt-owned verified score exactly as the design's score event."""

    name: str
    value: int
    data_type: str
    status: str
    source: str
    receipt_digest: str


@dataclass(frozen=True)
class TraceRecord:
    """Trace-ready projection; trace-only rows carry empty generations/scores."""

    tournament_id: str
    condition: str
    seed: str
    status: str
    outcome: str | None
    attempt_id: str | None
    attempt_ordinal: int | None
    execution_id: str | None
    logical_run_id: str | None
    generations: tuple
    scores: tuple
    digests: EvidenceDigests
    attempts_used: int
    declined_launches: int
    harness_version: str
    core_version: str


def _row_common(leaderboard, row):
    """Declared row/tournament identity shared by every item kind."""
    return {
        "tournament_id": leaderboard["tournament_id"],
        "condition": row["condition"],
        "seed": row["seed"],
        "status": row["status"],
        "attempts_used": row["attempts_used"],
        "declined_launches": row["declined_launches"],
        "harness_version": row["harness_version"],
        "core_version": row["core_version"],
    }


def _frozen_refs(refs):
    return tuple(_frozen_map(ref) for ref in refs)


def _checked_launch(root, row, attempt_id, attempt_ordinal):
    """Load one attempt launch and require agreement with the row identity."""
    launch = load_launch(root, attempt_id)
    if (
        launch["attempt_id"] != attempt_id
        or launch["condition"] != row["condition"]
        or launch["seed"] != row["seed"]
        or launch["attempt_ordinal"] != attempt_ordinal
    ):
        raise ArtifactError(
            IDENTITY_MISMATCH,
            f"launch {attempt_id} disagrees with the leaderboard row",
            launch_path(root, attempt_id),
        )
    return launch


def _require_launches(root, row):
    """Unbound rows with attempts must present agreeing launch evidence."""
    for ref in row["attempt_refs"]:
        _checked_launch(root, row, ref["attempt_id"], ref["attempt_ordinal"])


def _tool_call(tool_call):
    structural = {
        key: value
        for key, value in tool_call.items()
        if key not in ("name", "arguments")
    }
    arguments = tool_call.get("arguments")
    return ToolCall(
        name=tool_call["name"],
        structural_fields=_frozen_map(structural),
        arguments=None if arguments is None else GuardedText(arguments),
    )


def _provider_request(request):
    tool_call = request.get("tool_call")
    visible = request["raw_visible_response"]
    error = request.get("error")
    return ProviderRequest(
        policy_step=request["policy_step"],
        provider=request["provider"],
        model=request["model"],
        request_id=request["request_id"],
        latency_ms=request["latency_ms"],
        usage=_frozen_map(request["usage"]),
        raw_visible_response=None if visible is None else GuardedText(visible),
        tool_call=None if tool_call is None else _tool_call(tool_call),
        error=_frozen_optional(error),
    )


def _assemble_bound(root, row, runs_by_digest, common):
    """Bind one bound row to its run, terminal launch, and receipt authority."""
    matches = runs_by_digest.get(row["core_receipt_digest"], [])
    if not matches:
        raise ArtifactError(
            MISSING_ARTIFACT,
            "no run evidence carries the row's core_receipt_digest",
            root / "core" / "runs",
        )
    if len(matches) > 1:
        raise ArtifactError(
            IDENTITY_MISMATCH,
            "row core_receipt_digest matches multiple runs",
            root / "core" / "runs",
        )
    run = matches[0]
    receipt = run["receipt"]
    receipt_path = run["run_dir"] / RECEIPT_NAME
    agreements = (
        ("llm_receipt_digest", row["llm_receipt_digest"] == run["llm_receipt_digest"]),
        ("seed", row["seed"] == receipt["seed"]),
        ("outcome", row["outcome"] == receipt["outcome"]),
        ("verified_score", row["verified_score"] == receipt["verified_score"]),
        ("policy_digest", row["policy_digest"] == receipt["policy_digest"]),
    )
    for label, agrees in agreements:
        if not agrees:
            raise ArtifactError(
                IDENTITY_MISMATCH,
                f"row {label} disagrees with the receipt",
                receipt_path,
            )
    terminal = max(
        (ref for ref in row["attempt_refs"] if ref["evidence_status"] == "bound"),
        key=lambda ref: ref["attempt_ordinal"],
    )
    launch = _checked_launch(
        root, row, terminal["attempt_id"], terminal["attempt_ordinal"]
    )
    if (
        row["strategy_context_digest"] is not None
        and row["strategy_context_digest"] != launch["context_digest"]
    ):
        raise ArtifactError(
            IDENTITY_MISMATCH,
            "row strategy_context_digest disagrees with the launch context",
            launch_path(root, terminal["attempt_id"]),
        )
    llm = run["llm_receipt"]
    return ValidatedRun(
        **common,
        outcome=receipt["outcome"],
        verified_score=receipt["verified_score"],
        attempt_id=terminal["attempt_id"],
        attempt_ordinal=terminal["attempt_ordinal"],
        execution_id=run["execution_id"],
        logical_run_id=run["logical_run_id"],
        requests=tuple(
            _provider_request(request) for request in run["provider_run"]["requests"]
        ),
        launch=_frozen_map(launch),
        digests=EvidenceDigests(
            core_receipt=run["core_receipt_digest"],
            provider_evidence=run["provider_evidence_digest"],
            llm_receipt=run["llm_receipt_digest"],
            policy=row["policy_digest"],
            strategy_context=row["strategy_context_digest"],
            prompt=llm["prompt_digest"],
            tool_schema=llm["tool_schema_digest"],
        ),
    )


def _assemble_row(root, leaderboard, row, runs_by_digest):
    """One leaderboard row -> ValidatedRun or TraceOnlyRow, or raises."""
    common = _row_common(leaderboard, row)
    if row["core_receipt_digest"] is not None:
        return _assemble_bound(root, row, runs_by_digest, common)
    _require_launches(root, row)
    return TraceOnlyRow(
        **common,
        outcome=row["outcome"],
        attempt_refs=_frozen_refs(row["attempt_refs"]),
        digests=_null_digests(),
    )


def assemble_items(root):
    """Bind leaderboard rows to sealed run evidence; return ``(items, skips)``.

    A leaderboard that fails to load yields one safe skip and no items.
    ``discover_evidence`` skips pass through; bound rows attach by
    ``core_receipt_digest`` (0 matches -> ``missing_artifact`` at
    ``core/runs``; >1 -> ``identity_mismatch``), must agree with the receipt
    on llm digest/seed/outcome/verified_score/policy, and join their terminal
    (max-ordinal bound) attempt's launch with seed/condition/ordinal agreement.
    An unbound row stays trace-only: attempts_used > 0 requires each
    referenced attempt's launch to exist and agree; attempts_used == 0
    (declined) needs none. Statuses pass through exactly, never normalized.
    """
    root = Path(root)
    try:
        leaderboard = load_leaderboard(root)
    except ArtifactError as exc:
        return [], [SkipRecord(exc.path, exc.reason)]
    evidence, skips = discover_evidence(root)
    runs_by_digest = {}
    for run in evidence:
        runs_by_digest.setdefault(run["core_receipt_digest"], []).append(run)
    items = []
    for row in leaderboard["rows"]:
        try:
            items.append(_assemble_row(root, leaderboard, row, runs_by_digest))
        except ArtifactError as exc:
            skips.append(SkipRecord(exc.path, exc.reason))
    return items, skips


def _generation(request):
    tool_call = request.tool_call
    return GenerationRecord(
        policy_step=request.policy_step,
        provider=request.provider,
        model=request.model,
        request_id=request.request_id,
        latency_ms=request.latency_ms,
        usage=_frozen_map(
            {
                "input": request.usage["input_tokens"],
                "output": request.usage["output_tokens"],
                "total": request.usage["total_tokens"],
            }
        ),
        tool_call=None
        if tool_call is None
        else _frozen_map(
            {"name": tool_call.name, "structural_fields": tool_call.structural_fields}
        ),
        error=request.error,
    )


def _trace_record(item):
    """Project one validated item; trace-only rows get empty generations."""
    bound = isinstance(item, ValidatedRun)
    generations = (
        tuple(_generation(request) for request in item.requests) if bound else ()
    )
    scores = ()
    if bound:
        # A bound item always carries its receipt digest; the record contract
        # guarantees a non-null score receipt digest.
        scores = (
            ScoreRecord(
                name=SCORE_NAME,
                value=item.verified_score,
                data_type=SCORE_DATA_TYPE,
                status="valid",
                source="receipt",
                receipt_digest=cast(str, item.digests.core_receipt),
            ),
        )
    return TraceRecord(
        tournament_id=item.tournament_id,
        condition=item.condition,
        seed=item.seed,
        status=item.status,
        outcome=item.outcome,
        attempt_id=item.attempt_id if bound else None,
        attempt_ordinal=item.attempt_ordinal if bound else None,
        execution_id=item.execution_id if bound else None,
        logical_run_id=item.logical_run_id if bound else None,
        generations=generations,
        scores=scores,
        digests=item.digests,
        attempts_used=item.attempts_used,
        declined_launches=item.declined_launches,
        harness_version=item.harness_version,
        core_version=item.core_version,
    )


def build_trace_records(items):
    """Project validated items into frozen, metadata-only ``TraceRecord`` s."""
    return tuple(_trace_record(item) for item in items)


def plain(value):
    """JSON-safe projection of model data; guarded text fails closed."""
    if isinstance(value, GuardedText):
        raise ArtifactError(
            SECRET_DETECTED, "guarded text is never serializable", "<guarded-text>"
        )
    if is_dataclass(value) and not isinstance(value, type):
        return {
            entry.name: plain(getattr(value, entry.name)) for entry in fields(value)
        }
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ArtifactError(
        UNSUPPORTED_SCHEMA,
        f"no JSON-safe projection for {type(value).__name__}",
        "<value>",
    )
