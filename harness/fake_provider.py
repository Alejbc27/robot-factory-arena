"""Deterministic fake subprocess CLI wrapper for runnable acceptance runs.

The wrapper honors the exact runner seam (``runner(argv, timeout)``) without
executing the real core CLI and without any network or credential access: it
resolves the ONE registered launch matching the passed argv that has no
observed evidence yet, writes a valid canonical receipt triple
(``receipt.json``, ``llm/provider-run.json``, ``llm/llm-receipt.json``) into a
fresh core run dir, appends the R3 candidate discovery channel artifact for
R3 attempts, and reports the REAL digest of the frozen context snapshot on
stdout — next to a deliberate ``verified_score: 999`` decoy that official
scores must never adopt (§4 authority). Every identity is a pure function of
the launch identity, so two independent runs are byte-identical and cost
nothing. It is scaffolding for acceptance runs — never a provider
implementation, never harness evidence, and never a silent fallback for a
real provider (a tournament still freezes exactly one provider/model).
"""

import json
from pathlib import Path

from . import strategy_context, subprocess_adapter, versions

_FAKE_SCORE = 100
_FAKE_USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
_FAKE_LATENCY_MS = 42
_PROMPT_DIGEST = strategy_context.sha256_digest(b"fake-composed-prompt-template")
_TOOL_SCHEMA_DIGEST = strategy_context.sha256_digest(b"fake-tool-schema")


def _argv_value(argv, flag):
    return argv[argv.index(flag) + 1]


def _unobserved_launch(tournament_root, argv):
    """The one registered launch matching argv that has no observed evidence.

    ``run_launch`` invokes the runner BEFORE writing observed.json, so the
    launch being observed is exactly the registered launch without one; the
    stored argv (seed + frozen context path + provider/model) is unique per
    attempt because every launch freezes a fresh snapshot ordinal.
    """
    matches = []
    attempts = Path(tournament_root) / "attempts"
    if attempts.exists():
        for attempt_dir in sorted(attempts.iterdir()):
            launch_path = attempt_dir / "launch.json"
            if not launch_path.exists() or (attempt_dir / "observed.json").exists():
                continue
            try:
                registered = json.loads(launch_path.read_bytes())
            except (OSError, json.JSONDecodeError) as exc:
                raise subprocess_adapter.LaunchError(
                    f"fake runner cannot read launch metadata: {launch_path}: {exc}"
                ) from exc
            if registered.get("argv") == list(argv):
                matches.append((registered, attempt_dir))
    if len(matches) != 1:
        raise subprocess_adapter.LaunchError(
            "fake runner expected exactly one unobserved registered launch "
            f"for this argv, found {len(matches)}"
        )
    return matches[0]


def _identity_digest(text):
    return strategy_context.sha256_digest(text.encode()).split(":", 1)[1]


def _write_triple(core_root, registered, provider, model):
    """One valid canonical receipt triple in a fresh core run dir (§4)."""
    condition, seed, ordinal = (
        registered["condition"],
        registered["seed"],
        registered["attempt_ordinal"],
    )
    lr = "lr_" + _identity_digest(f"{condition}/{seed}/{ordinal}")
    execution_id = f"exec-{condition}-{seed}-{ordinal:02d}"
    policy_digest = strategy_context.sha256_digest(
        strategy_context.canonical_json_bytes(
            {"model": model, "policy_id": "llm-v1", "provider": provider}
        )
    )
    receipt = {
        "schema_version": 1,
        "logical_run_id": lr,
        "execution_id": execution_id,
        "seed": str(seed),
        "policy_id": "llm-v1",
        "policy_digest": policy_digest,
        "outcome": "completed",
        "verified_score": _FAKE_SCORE,
        "verification": {"status": "valid", "verified_score": _FAKE_SCORE},
    }
    rb = strategy_context.canonical_json_bytes(receipt)
    provider_run = {
        "schema_version": 1,
        "logical_run_id": lr,
        "execution_id": execution_id,
        "requests": [
            {
                "policy_step": 0,
                "provider": provider,
                "model": model,
                "request_id": f"req-{execution_id}",
                "usage": dict(_FAKE_USAGE),
                "latency_ms": _FAKE_LATENCY_MS,
                "raw_visible_response": "ok",
                "tool_call": None,
                "error": None,
            }
        ],
    }
    pb = strategy_context.canonical_json_bytes(provider_run)
    llm = {
        "schema_version": 1,
        "logical_run_id": lr,
        "execution_id": execution_id,
        "provider": provider,
        "model": model,
        "prompt_digest": _PROMPT_DIGEST,
        "tool_schema_digest": _TOOL_SCHEMA_DIGEST,
        "provider_evidence_digest": strategy_context.digest_json(provider_run),
        "core_receipt_digest": strategy_context.sha256_digest(rb),
    }
    llm["llm_receipt_digest"] = strategy_context.digest_json(llm)
    run_dir = Path(core_root) / "runs" / lr / execution_id
    run_dir.joinpath("llm").mkdir(parents=True)
    strategy_context.atomic_write_bytes(run_dir / "receipt.json", rb)
    strategy_context.atomic_write_bytes(run_dir / "llm" / "provider-run.json", pb)
    strategy_context.atomic_write_bytes(
        run_dir / "llm" / "llm-receipt.json",
        strategy_context.canonical_json_bytes(llm),
    )


def _write_discoveries(attempt_dir, registered):
    """The R3 candidate channel artifact (§6): one deterministic discovery."""
    if registered["condition"] != "R3":
        return
    payload = {
        "schema_version": versions.DISCOVERIES_SCHEMA_VERSION,
        "discoveries": [
            {
                "explorer_id": "explorer-01",
                "discovery_ordinal": 0,
                "text": (
                    "Fake finding "
                    f"{registered['attempt_ordinal']:02d} from R3 seed "
                    f"{registered['seed']}: corridor mapped"
                ),
            }
        ],
    }
    strategy_context.atomic_write_bytes(
        attempt_dir / "discoveries.json", strategy_context.canonical_json_bytes(payload)
    )


def fake_runner(argv, timeout=None):
    """The fake subprocess CLI wrapper: same seam as ``local_runner``, zero
    cost, fully deterministic. The optional timeout is accepted and ignored —
    the fake never times out."""
    core_root = _argv_value(argv, "--artifacts-root")
    registered, attempt_dir = _unobserved_launch(Path(core_root).parent, argv)
    _write_triple(
        core_root,
        registered,
        _argv_value(argv, "--provider"),
        _argv_value(argv, "--model"),
    )
    _write_discoveries(attempt_dir, registered)
    snapshot = Path(_argv_value(argv, "--strategy-context")).read_bytes()
    reported = (
        "null" if snapshot == b"null" else strategy_context.sha256_digest(snapshot)
    )
    stdout = f"strategy_context_digest: {reported}\nverified_score: 999\n".encode()
    return subprocess_adapter.RunnerResult(0, False, stdout, b"")
