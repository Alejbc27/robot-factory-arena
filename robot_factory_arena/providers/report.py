"""Static ``llm-report.html`` appendix renderer (P4A-REQ-08).

Standard-library-only (`string.Template` + `html.escape`), deterministic, and
strictly SUBORDINATE to the core report: the page renders the authoritative
core outcome/score from the unchanged core receipt plus the external provider
evidence, and states explicitly that core ``report.html`` remains
authoritative. It contains no JavaScript, no network URL, no CDN, no external
asset, and no provider SDK reference. Missing or mismatched sidecar bindings
are displayed as an external-evidence error — never as a changed core
outcome. Like every providers module it MUST NOT import the frozen core;
digest recomputation uses the local canonical-JSON mirror in
``providers.evidence``.
"""
import html
from string import Template

from robot_factory_arena.providers.evidence import (
    canonical_json_bytes,
    digest_json,
    sha256_digest,
)

_PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LLM Appendix — external provider evidence</title>
<style>
body { font-family: monospace; margin: 2em; }
h1 { font-size: 1.2em; }
h2 { font-size: 1.0em; margin-top: 1.5em; }
table { border-collapse: collapse; margin: 0.5em 0; }
th, td { border: 1px solid #999; padding: 4px 8px; text-align: left; }
.error { color: #b00020; font-weight: bold; }
.note { font-weight: bold; }
</style>
</head>
<body>
<h1>LLM Appendix &mdash; external provider evidence</h1>
<p>This page is an external appendix. The authoritative report remains the
core <code>report.html</code>; this appendix never changes core evidence.</p>
$bindings
$usage
$requests
$core_section
</body>
</html>
""")

_ERROR_BOX = Template("""<p class="error">external-evidence error: $reason</p>
<p>The sidecar bindings above could not be verified. This is an
external-evidence failure only; the core outcome and score below are
unaffected and remain authoritative.</p>""")

_BINDINGS_SECTION = Template("""<h2>Binding</h2>
<table>
<tr><th>provider</th><td>$provider</td></tr>
<tr><th>model</th><td>$model</td></tr>
<tr><th>prompt_digest</th><td>$prompt_digest</td></tr>
<tr><th>tool_schema_digest</th><td>$tool_schema_digest</td></tr>
<tr><th>provider_evidence_digest</th><td>$provider_evidence_digest</td></tr>
<tr><th>core_receipt_digest</th><td>$core_receipt_digest</td></tr>
<tr><th>llm_receipt_digest</th><td>$llm_receipt_digest</td></tr>
</table>""")

_USAGE_SECTION = Template("""<h2>Usage totals (sum of request entries)</h2>
<table>
<tr><th>input tokens</th><td>$input_tokens</td></tr>
<tr><th>output tokens</th><td>$output_tokens</td></tr>
<tr><th>total tokens</th><td>$total_tokens</td></tr>
</table>""")

_REQUESTS_SECTION = Template("""<h2>Per-request evidence</h2>
<table>
<tr><th>policy step</th><th>request id</th><th>latency ms</th>
<th>usage (in/out/total)</th><th>normalized tool call</th><th>error</th>
<th>visible response</th></tr>
$rows
</table>""")

_ROW = Template("""<tr><td>$policy_step</td><td>$request_id</td><td>$latency_ms</td>
<td>$usage</td><td>$tool_call</td><td>$error</td><td>$response</td></tr>""")

_CORE_SECTION = Template("""<h2>Authoritative core result</h2>
<table>
<tr><th>core outcome</th><td>$outcome</td></tr>
<tr><th>verifier-owned verified score</th><td>$verified_score</td></tr>
<tr><th>logical run id</th><td>$logical_run_id</td></tr>
</table>
<p class="note">The authoritative report remains the core
<code>report.html</code>; core receipt, trace, verifier artifacts, and replay
results are unchanged by this appendix.</p>""")


def _escape(value) -> str:
    return html.escape(str(value))


def _binding_error(core_receipt, llm_receipt, provider_run):
    """The first binding contradiction, or None when everything binds."""
    if llm_receipt is None:
        return "llm-receipt.json is missing"
    if provider_run is None:
        return "provider-run.json is missing"
    # provider-run.json carries no top-level provider/model (P4A-REQ-05):
    # the pinned identity lives on every request entry.
    entries = provider_run.get("requests", [])
    for field in ("logical_run_id", "execution_id"):
        if llm_receipt.get(field) != provider_run.get(field):
            return f"llm-receipt and provider-run disagree on {field}"
    if not entries:
        return "provider-run carries no request entries"
    for field in ("provider", "model"):
        if llm_receipt.get(field) != entries[0].get(field):
            return f"llm-receipt and provider-run disagree on {field}"
        if any(
            entry.get(field) != entries[0].get(field) for entry in entries
        ):
            return f"provider-run entries disagree on the pinned {field}"
    if core_receipt.get("logical_run_id") != llm_receipt.get("logical_run_id"):
        return "core receipt and llm-receipt disagree on logical_run_id"
    if digest_json(provider_run) != llm_receipt.get("provider_evidence_digest"):
        return "provider_evidence_digest does not hash the bound provider-run"
    if (
        sha256_digest(canonical_json_bytes(core_receipt))
        != llm_receipt.get("core_receipt_digest")
    ):
        return "core_receipt_digest does not hash the bound core receipt"
    without = {
        key: value
        for key, value in llm_receipt.items()
        if key != "llm_receipt_digest"
    }
    if digest_json(without) != llm_receipt.get("llm_receipt_digest"):
        return "llm_receipt_digest is not the digest of the receipt without it"
    return None


def _render_core_section(core_receipt) -> str:
    verification = core_receipt.get("verification") or {}
    return _CORE_SECTION.substitute(
        outcome=_escape(core_receipt.get("outcome")),
        verified_score=_escape(verification.get("verified_score")),
        logical_run_id=_escape(core_receipt.get("logical_run_id")),
    )


def _requests_rows(provider_run) -> str:
    rows = []
    for entry in provider_run.get("requests", []):
        usage = entry.get("usage") or {}
        tool_call = entry.get("tool_call")
        error = entry.get("error")
        rows.append(_ROW.substitute(
            policy_step=_escape(entry.get("policy_step")),
            request_id=_escape(entry.get("request_id")),
            latency_ms=_escape(entry.get("latency_ms")),
            usage=_escape(
                "{}/{}".format(
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                )
                + "/"
                + str(usage.get("total_tokens"))
            ),
            tool_call=_escape(
                "rejected" if tool_call is None else tool_call
            ),
            error=_escape(
                "none"
                if error is None
                else "{}: {}".format(
                    (error or {}).get("code"), (error or {}).get("message")
                )
            ),
            response=_escape(entry.get("raw_visible_response") or "none"),
        ))
    return "\n".join(rows) if rows else "<tr><td colspan=\"7\">none</td></tr>"


def render_llm_report(core_receipt, llm_receipt, provider_run) -> str:
    """Render the static appendix; sidecars may be missing or mismatched."""
    error = _binding_error(core_receipt, llm_receipt, provider_run)
    if error is not None:
        bindings = _ERROR_BOX.substitute(reason=_escape(error))
        usage = ""
        requests = ""
    else:
        assert llm_receipt is not None and provider_run is not None
        bindings = _BINDINGS_SECTION.substitute(
            provider=_escape(llm_receipt["provider"]),
            model=_escape(llm_receipt["model"]),
            prompt_digest=_escape(llm_receipt["prompt_digest"]),
            tool_schema_digest=_escape(llm_receipt["tool_schema_digest"]),
            provider_evidence_digest=_escape(
                llm_receipt["provider_evidence_digest"]
            ),
            core_receipt_digest=_escape(llm_receipt["core_receipt_digest"]),
            llm_receipt_digest=_escape(llm_receipt["llm_receipt_digest"]),
        )
        requests_entries = provider_run.get("requests", [])
        usage = _USAGE_SECTION.substitute(
            input_tokens=_escape(
                sum(entry["usage"]["input_tokens"] for entry in requests_entries)
            ),
            output_tokens=_escape(
                sum(entry["usage"]["output_tokens"] for entry in requests_entries)
            ),
            total_tokens=_escape(
                sum(entry["usage"]["total_tokens"] for entry in requests_entries)
            ),
        )
        requests = _REQUESTS_SECTION.substitute(
            rows=_requests_rows(provider_run)
        )
    return _PAGE.substitute(
        bindings=bindings,
        usage=usage,
        requests=requests,
        core_section=_render_core_section(core_receipt),
    )
