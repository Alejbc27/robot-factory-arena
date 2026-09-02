"""Static, dependency-free HTML run report (T-22, REQ-09, REQ-10).

``render_report(receipt, verification, artifact_digests)`` composes one
offline HTML document from stdlib string templating (``string.Template``)
with every interpolated value HTML-escaped. The report renders the
VERIFICATION RESULT — score, outcome, completion counts, violations, verifier
status/version come from the ``VerificationResult`` object only, never from
receipt claims — plus run identity, raw counters, the artifact digest table,
and the v0 honesty limitations from the receipt mapping and the digest table.

Honesty boundary (REQ-10): four SEPARATE signal sections (task quality,
efficiency, robustness, contract hygiene) are never collapsed into one
number; no composite score exists anywhere in the document. Limitations state
that tamper evidence is not authentication, that no exactly-once guarantee or
hostile-Python sandbox is claimed, that the evaluation seed (29) is
plumbing-only, and that no scientific REFUGIO conclusion is made.

Determinism (REQ-07): the physical execution id and the directory path are
deliberately excluded from the rendered bytes — the receipt's ``execution_id``
field is never rendered, and the digest table carries only semantic digests —
so identical evidence renders byte-identical HTML on every machine. There is
no JavaScript, no CSS/CDN asset, and no URL of any kind: the file opens
offline as plain static HTML.
"""
import html as _html
from collections.abc import Mapping
from string import Template

from robot_factory_arena.contracts.outcomes import VerificationResult

# The single v0 demonstrator tape, documented whenever a tape is bound.
FAULT_ID_V0 = "response_lost_after_commit"
FAULT_KIND_V0 = "response_lost"
FAULT_HOOK_V0 = "after_commit_before_response"

COUNTER_NAMES = (
    "policy_steps",
    "tool_attempts",
    "committed_effects",
    "internal_retries",
    "contract_violations",
    "terminal_checks",
)

# Exact v0 limitations (REQ-09/REQ-10); order is fixed for determinism.
LIMITATIONS = (
    "Tamper evidence is not authentication: the SHA-256 trace chain and the "
    "artifact digest bindings detect edits, but they do not authenticate who "
    "produced the artifacts.",
    "No exactly-once guarantee is made: the persistence boundary is a "
    "durability and audit proof, not a transaction or an exactly-once "
    "delivery system.",
    "No hostile-Python sandbox guarantee: policy outputs are semantically "
    "untrusted, but policy source is not treated as hostile malware, and no "
    "hostile arbitrary-Python isolation is claimed.",
    "The evaluation seed (29) is plumbing-only: evaluation runs exercise the "
    "plumbing and carry no tuning or experimental claim.",
    "No scientific REFUGIO conclusion is claimed: the project is "
    "paper-inspired and measures only its own deterministic demonstrator.",
    "The four signals are reported separately and are never collapsed into "
    "one number; no composite score is computed or authoritative.",
)

_PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Robot Factory &amp; Arena — v0 run report</title>
</head>
<body>
<h1>Robot Factory &amp; Arena — v0 run report</h1>
<p class="honesty-banner">Deterministic warehouse evidence-chain demonstrator
(v0). Policy outputs are semantically untrusted; the score below is owned by
the independent verifier, and the four signals stay separate. This document
makes no production-security, exactly-once, hostile-code-sandbox, or
scientific claim.</p>

<section id="run-identity">
<h2>Run identity</h2>
<table>
<tr><th>task_version</th><td>$task_version</td></tr>
<tr><th>seed</th><td>$seed</td></tr>
<tr><th>policy_id</th><td>$policy_id</td></tr>
<tr><th>budget (policy_steps maximum)</th><td>$budget</td></tr>
<tr><th>logical_run_id</th><td>$logical_run_id</td></tr>
</table>
<p>The physical execution id is deliberately omitted: deterministic report
contents never depend on it (REQ-07).</p>
</section>

<section id="signal-task-quality">
<h2>Signal 1 — task quality</h2>
<p>Verified score (verifier-owned): <strong>$score</strong></p>
<p>Score formula: floor(100 * unique valid completed orders / total orders)</p>
<p>Orders completed: $completed / $total</p>
</section>

<section id="signal-efficiency">
<h2>Signal 2 — efficiency</h2>
<p>Policy steps used: $policy_steps / $budget</p>
<p>Tool attempts (including internal retries): $tool_attempts</p>
<p>Committed effects: $committed_effects</p>
<p>Actions per delivery (tool attempts / completed deliveries): $ratio</p>
</section>

<section id="signal-robustness">
<h2>Signal 3 — robustness</h2>
<p>Fault tape: $fault_line</p>
<p>Internal retries: $internal_retries</p>
<p>Outcome (verifier classification): <strong>$outcome</strong></p>
</section>

<section id="signal-contract-hygiene">
<h2>Signal 4 — contract hygiene</h2>
<p>Contract violations (protocol): $contract_violations</p>
<p>Audit: $audit</p>
$violations_block
</section>

<section id="raw-counters">
<h2>Raw counters</h2>
<table>
<tr><th>policy_steps</th><td>$c_policy_steps</td></tr>
<tr><th>tool_attempts</th><td>$c_tool_attempts</td></tr>
<tr><th>committed_effects</th><td>$c_committed_effects</td></tr>
<tr><th>internal_retries</th><td>$c_internal_retries</td></tr>
<tr><th>contract_violations</th><td>$c_contract_violations</td></tr>
<tr><th>terminal_checks</th><td>$c_terminal_checks</td></tr>
</table>
</section>

<section id="artifact-digests">
<h2>Artifact digests</h2>
$digest_rows
</section>

<section id="verifier">
<h2>Verifier</h2>
<p>Status: <strong>$verifier_status</strong> (version $verifier_version)</p>
<p>Violations recorded: $violations_count</p>
</section>

<section id="limitations">
<h2>Limitations (v0 honesty boundary)</h2>
<ul>
$limitation_items
</ul>
</section>

</body>
</html>
""")


def _esc(value) -> str:
    """Escape every interpolated value; the report trusts nothing."""
    return _html.escape(str(value), quote=True)


def _counter(counters, name) -> str:
    """One raw counter as text; missing/invalid data renders as n/a."""
    if not isinstance(counters, Mapping):
        return "n/a"
    value = counters.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return "n/a"
    return str(value)


def _score_text(score) -> str:
    if score is None:
        return "no official score (verification invalid or ambiguous)"
    return str(score)


def _ratio_text(counters: Mapping, completed_deliveries: int) -> str:
    """Actions per delivery (REQ-09): tool attempts over the verifier's
    completed deliveries. ``committed_effects`` counts every successful
    move/pick/place and is NOT a delivery denominator (W-01)."""
    attempts = counters.get("tool_attempts") if isinstance(counters, Mapping) else None
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (attempts, completed_deliveries)
    ):
        return "n/a"
    if completed_deliveries == 0:
        return "n/a (no completed deliveries)"
    return f"{attempts}/{completed_deliveries}"


def _fault_line(receipt: Mapping) -> str:
    if receipt.get("fault_tape_digest") is None:
        return "none"
    return (
        f"{FAULT_ID_V0} (kind {FAULT_KIND_V0}, hook {FAULT_HOOK_V0}); "
        f"resolved tape digest {_esc(receipt.get('fault_tape_digest'))}"
    )


def _audit_text(verification: VerificationResult) -> str:
    if verification.valid:
        return (
            "complete — the independent verifier re-derived the trace chain, "
            "all artifact bindings, the persistence-boundary proofs, and the "
            "score from the artifacts alone."
        )
    return (
        "INCOMPLETE — verification invalid: the evidence set did not pass the "
        "independent release gate, so no receipt was released and no score is "
        "official."
    )


def _violations_block(verification: VerificationResult) -> str:
    if not verification.violations:
        return "<p>Verifier violations: none</p>"
    rows = "".join(
        "<tr>"
        f"<td>{_esc(violation.code)}</td>"
        f"<td>{_esc(violation.location)}</td>"
        f"<td>{_esc(violation.detail)}</td>"
        "</tr>"
        for violation in verification.violations
    )
    return (
        "<table><tr><th>code</th><th>location</th><th>detail</th></tr>"
        f"{rows}</table>"
    )


def _digest_rows(artifact_digests) -> str:
    if not isinstance(artifact_digests, Mapping) or not artifact_digests:
        return "<p>No artifact digests supplied.</p>"
    rows = "".join(
        f"<tr><th>{_esc(name)}</th><td>{_esc(digest)}</td></tr>"
        for name, digest in sorted(artifact_digests.items())
    )
    return f"<table>{rows}</table>"


def render_report(
    receipt: Mapping,
    verification: VerificationResult,
    artifact_digests: Mapping,
) -> str:
    """Render one run's static HTML report (design §2 ``evidence/report.py``).

    ``verification`` MUST be a real ``VerificationResult``: the rendered
    score/outcome/violations are the verifier's, never the receipt's claims
    (T-22 hard constraint). ``receipt`` supplies identity, counters, and fault
    binding; ``artifact_digests`` maps artifact name to semantic digest and is
    rendered sorted. Every dynamic value is escaped; the output opens offline
    with no JavaScript, no CDN, and no URLs.
    """
    if not isinstance(verification, VerificationResult):
        raise TypeError("render_report requires a VerificationResult")
    if not isinstance(receipt, Mapping):
        raise TypeError("receipt must be a mapping")
    raw_counters = receipt.get("counters")
    counters = raw_counters if isinstance(raw_counters, Mapping) else {}
    limitation_items = "".join(
        f"<li>{_esc(limitation)}</li>" for limitation in LIMITATIONS
    )
    return _PAGE.substitute(
        task_version=_esc(receipt.get("task_version", "n/a")),
        seed=_esc(receipt.get("seed", "n/a")),
        policy_id=_esc(receipt.get("policy_id", "n/a")),
        budget=_esc(receipt.get("budget", "n/a")),
        logical_run_id=_esc(receipt.get("logical_run_id", "n/a")),
        score=_esc(_score_text(verification.verified_score)),
        completed=_esc(verification.completed_count),
        total=_esc(verification.total_count),
        policy_steps=_counter(counters, "policy_steps"),
        tool_attempts=_counter(counters, "tool_attempts"),
        committed_effects=_counter(counters, "committed_effects"),
        ratio=_esc(_ratio_text(counters, verification.completed_count)),
        fault_line=_fault_line(receipt),
        internal_retries=_counter(counters, "internal_retries"),
        outcome=_esc(verification.outcome),
        contract_violations=_counter(counters, "contract_violations"),
        audit=_esc(_audit_text(verification)),
        violations_block=_violations_block(verification),
        c_policy_steps=_counter(counters, "policy_steps"),
        c_tool_attempts=_counter(counters, "tool_attempts"),
        c_committed_effects=_counter(counters, "committed_effects"),
        c_internal_retries=_counter(counters, "internal_retries"),
        c_contract_violations=_counter(counters, "contract_violations"),
        c_terminal_checks=_counter(counters, "terminal_checks"),
        digest_rows=_digest_rows(artifact_digests),
        verifier_status=_esc("valid" if verification.valid else "invalid"),
        verifier_version=_esc(verification.verifier_version),
        violations_count=_esc(len(verification.violations)),
        limitation_items=limitation_items,
    )
