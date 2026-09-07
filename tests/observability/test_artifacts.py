"""Slice A1 artifact-contract tests over copies of the sealed unit3 fixture.

The committed fixture is read-only input: every test copies it into a
temporary run root and mutates only the copy.
"""

import json
import shutil
import tempfile
import unittest
from dataclasses import astuple
from pathlib import Path

from observability import (
    ArtifactError,
    SkipRecord,
    artifacts,
    assemble_items,
    build_trace_records,
    canonical_json_bytes,
    digest_json,
    model,
    plain,
    sha256_digest,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "unit3-sealed"


def _fixture_json(name):
    return json.loads((FIXTURE / name).read_text())


def _write_canonical(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _run_dir(root):
    receipt = _fixture_json("receipt.json")
    return (
        Path(root)
        / "core"
        / "runs"
        / receipt["logical_run_id"]
        / receipt["execution_id"]
    )


def _reseal(run_dir):
    """Recompute the llm-receipt digest bindings over the current copy."""
    raw = (run_dir / "receipt.json").read_bytes()
    provider = json.loads((run_dir / "llm" / "provider-run.json").read_text())
    llm = json.loads((run_dir / "llm" / "llm-receipt.json").read_text())
    llm["core_receipt_digest"] = sha256_digest(raw)
    llm["provider_evidence_digest"] = digest_json(provider)
    llm.pop("llm_receipt_digest")
    llm["llm_receipt_digest"] = digest_json(llm)
    _write_canonical(run_dir / "llm" / "llm-receipt.json", llm)
    return (
        llm["core_receipt_digest"],
        llm["provider_evidence_digest"],
        llm["llm_receipt_digest"],
    )


def build_root(prefix, *, patch=None, post_patch=None):
    """Copy the sealed fixture into a fresh temp run root; mutate the copy."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    run_dir = _run_dir(tmp)
    shutil.copytree(FIXTURE, run_dir)
    if patch is not None:
        patch(run_dir)
    digests = _reseal(run_dir)
    if post_patch is not None:
        post_patch(run_dir)
    return tmp, digests


def add_execution(root, exec_id):
    """Second execution of the same logical run, resealed consistently."""
    dst = _run_dir(root).parent / exec_id
    shutil.copytree(FIXTURE, dst)
    for rel in ("receipt.json", "llm/provider-run.json", "llm/llm-receipt.json"):
        p = dst / rel
        value = json.loads(p.read_text())
        value["execution_id"] = exec_id
        _write_canonical(p, value)
    return _reseal(dst)


class TestCanonicalContract(unittest.TestCase):
    def test_canonical_bytes_rules(self):
        self.assertEqual(canonical_json_bytes({"b": 1, "a": None}), b'{"a":null,"b":1}')
        self.assertEqual(canonical_json_bytes({"k": "é"}), '{"k":"é"}'.encode())
        with self.assertRaises(ArtifactError) as raised:
            canonical_json_bytes({"x": 0.5})
        self.assertEqual(raised.exception.reason, "non_canonical_json")

    def test_sealed_fixture_binds_authoritatively(self):
        root, digests = build_root("obsa-happy-")
        evidence, skips = artifacts.discover_evidence(root)
        self.assertEqual(skips, [])
        self.assertEqual(len(evidence), 1)
        bound = evidence[0]
        receipt = bound["receipt"]
        self.assertEqual(receipt["outcome"], "completed")
        self.assertEqual(receipt["verified_score"], 100)
        self.assertEqual(receipt["verification"]["status"], "valid")
        self.assertEqual(receipt["seed"], "18")
        self.assertEqual(bound["execution_id"], "sealed-fixture-01")
        self.assertEqual(bound["logical_run_id"], receipt["logical_run_id"])
        (request,) = bound["provider_run"]["requests"]
        self.assertEqual(request["provider"], "fake")
        self.assertEqual(request["model"], "fake-v1")
        self.assertEqual(request["request_id"], "req-0")
        self.assertEqual(request["policy_step"], 0)
        self.assertEqual(request["latency_ms"], 42)
        self.assertEqual(
            request["usage"],
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        self.assertIsNone(request["error"])
        self.assertIsNone(request["tool_call"])
        llm = bound["llm_receipt"]
        self.assertEqual(bound["core_receipt_digest"], digests[0])
        self.assertEqual(bound["provider_evidence_digest"], digests[1])
        self.assertEqual(bound["llm_receipt_digest"], digests[2])
        self.assertEqual(
            llm["prompt_digest"], _fixture_json("llm/llm-receipt.json")["prompt_digest"]
        )
        self.assertEqual(
            llm["tool_schema_digest"],
            _fixture_json("llm/llm-receipt.json")["tool_schema_digest"],
        )

    def test_discovery_order_is_identity_sorted(self):
        root, _ = build_root("obsa-order-")
        add_execution(root, "sealed-fixture-02")
        evidence, skips = artifacts.discover_evidence(root)
        self.assertEqual(skips, [])
        found = [(e["logical_run_id"], e["execution_id"]) for e in evidence]
        self.assertEqual(found, sorted(found))
        self.assertEqual(found[0][1], "sealed-fixture-01")


class TestAdversarialSkips(unittest.TestCase):
    def _assert_skip(self, root, reason):
        evidence, skips = artifacts.discover_evidence(root)
        self.assertEqual(evidence, [])
        self.assertEqual([s.reason for s in skips], [reason])
        self.assertIsInstance(skips[0], SkipRecord)
        self.assertIn(skips[0].reason, artifacts.REASONS)
        return skips

    def test_stable_skip_reasons(self):
        def delete_provider(d):
            (d / "llm" / "provider-run.json").unlink()

        def malformed(d):
            (d / "receipt.json").write_bytes(b'{"execution_id": ')

        def spaced(d):
            value = json.loads((d / "receipt.json").read_text())
            (d / "receipt.json").write_bytes(json.dumps(value, sort_keys=True).encode())

        def floated(d):
            value = json.loads((d / "receipt.json").read_text())
            value["residual"] = 0.5
            (d / "receipt.json").write_bytes(
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            )

        def del_seed(d):
            value = json.loads((d / "receipt.json").read_text())
            del value["seed"]
            _write_canonical(d / "receipt.json", value)

        def schema_two(d):
            value = json.loads((d / "receipt.json").read_text())
            value["schema_version"] = 2
            _write_canonical(d / "receipt.json", value)

        def tamper_provider(d):
            provider = json.loads((d / "llm" / "provider-run.json").read_text())
            provider["requests"][0]["latency_ms"] = 43
            _write_canonical(d / "llm" / "provider-run.json", provider)

        def disagree_identity(d):
            path = d / "llm" / "llm-receipt.json"
            llm = json.loads(path.read_text())
            llm["execution_id"] = "execution-disagree"
            llm.pop("llm_receipt_digest")
            llm["llm_receipt_digest"] = digest_json(llm)
            _write_canonical(path, llm)

        def secret(d):
            provider = json.loads((d / "llm" / "provider-run.json").read_text())
            provider["requests"][0]["raw_visible_response"] = "sk-live-secret-token"
            _write_canonical(d / "llm" / "provider-run.json", provider)

        cases = {
            "missing_artifact": dict(post_patch=delete_provider),
            "malformed_json": dict(patch=malformed),
            "non_canonical_json": dict(patch=spaced),
            "non_canonical_json_float": dict(patch=floated),
            "missing_field": dict(patch=del_seed),
            "unsupported_schema": dict(patch=schema_two),
            "digest_mismatch": dict(post_patch=tamper_provider),
            "identity_mismatch": dict(post_patch=disagree_identity),
            "secret_detected": dict(patch=secret),
        }
        for expected, kwargs in cases.items():
            with self.subTest(reason=expected):
                root, _ = build_root(f"obsa-{expected}-", **kwargs)
                skips = self._assert_skip(root, expected.split("_float")[0])
                self.assertNotIn("sk-live-secret-token", repr(skips))

    def test_null_raw_visible_response_stays_null(self):
        def null_response(d):
            provider = json.loads((d / "llm" / "provider-run.json").read_text())
            provider["requests"][0]["raw_visible_response"] = None
            _write_canonical(d / "llm" / "provider-run.json", provider)

        root, _ = build_root("obsa-null-", patch=null_response)
        evidence, skips = artifacts.discover_evidence(root)
        self.assertEqual(skips, [])
        (request,) = evidence[0]["provider_run"]["requests"]
        self.assertIsNone(request["raw_visible_response"])
        self.assertNotEqual(request["latency_ms"], 0)


# --- Slice A2: leaderboard/launch loaders and authoritative row assembly ---

_TOURNAMENT = "tournament-observed"
_DIGEST = "sha256:" + "ab" * 32


def _launch_value(attempt_id, condition, seed, ordinal):
    return {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "registered_before_start": True,
        "condition": condition,
        "seed": seed,
        "agent_id": f"{condition}-agent",
        "attempt_ordinal": ordinal,
        "argv": ["python3", "-m", "robot_factory_arena"],
        "context_path": "contexts/frozen.json",
        "context_digest": _DIGEST,
    }


def write_launch(root, attempt_id, condition, seed, ordinal, **overrides):
    value = _launch_value(attempt_id, condition, seed, ordinal)
    value.update(overrides)
    _write_canonical(Path(root) / "attempts" / attempt_id / "launch.json", value)
    return value


def _row(condition, seed, status, **overrides):
    row = {
        "condition": condition,
        "seed": seed,
        "status": status,
        "outcome": None,
        "verified_score": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "latency_ms": None,
        "attempts_used": 0,
        "declined_launches": 1,
        "core_receipt_digest": None,
        "llm_receipt_digest": None,
        "policy_digest": None,
        "strategy_context_digest": None,
        "harness_version": "h-test",
        "core_version": "c-test",
        "attempt_refs": [],
    }
    row.update(overrides)
    return row


def write_leaderboard(root, rows):
    document = {
        "schema_version": 1,
        "tournament_id": _TOURNAMENT,
        "harness_version": rows[0]["harness_version"] if rows else "h-test",
        "core_version": rows[0]["core_version"] if rows else "c-test",
        "rows": rows,
    }
    _write_canonical(Path(root) / "leaderboard.json", document)
    return document


class TestLeaderboardLaunchLoaders(unittest.TestCase):
    def test_rows_must_be_sorted_unique_pairs(self):
        for rows in (
            [_row("R2", "2", "declined"), _row("R1", "3", "declined")],
            [_row("R1", "3", "declined"), _row("R1", "3", "declined")],
        ):
            with self.subTest(rows=rows):
                root, _ = build_root("obsl-pairs-")
                write_leaderboard(root, rows)
                with self.assertRaises(ArtifactError) as raised:
                    artifacts.load_leaderboard(root)
                self.assertEqual(raised.exception.reason, "unsupported_schema")

    def test_row_authority_and_field_rules(self):
        cases = {
            "unbound_row_claiming_outcome": [
                _row("R1", "3", "declined", outcome="completed")
            ],
            "half_bound_digests": [
                _row("R1", "3", "declined", core_receipt_digest=_DIGEST)
            ],
            "malformed_digest": [
                _row(
                    "R1",
                    "3",
                    "declined",
                    core_receipt_digest="sha256:xyz",
                    llm_receipt_digest="sha256:xyz",
                )
            ],
            "missing_field": [
                {k: v for k, v in _row("R1", "3", "declined").items() if k != "status"}
            ],
            "unknown_evidence_status": [
                _row(
                    "R1",
                    "3",
                    "declined",
                    attempts_used=1,
                    declined_launches=0,
                    attempt_refs=[
                        {
                            "attempt_id": "a",
                            "attempt_ordinal": 0,
                            "evidence_status": "maybe",
                        }
                    ],
                )
            ],
        }
        for label, rows in cases.items():
            with self.subTest(case=label):
                root, _ = build_root(f"obsl-{label}-")
                write_leaderboard(root, rows)
                with self.assertRaises(ArtifactError) as raised:
                    artifacts.load_leaderboard(root)
                self.assertIn(
                    raised.exception.reason,
                    ("unsupported_schema", "missing_field"),
                )

    def test_launch_validation(self):
        root, _ = build_root("obsl-launch-")
        write_launch(root, "attempt-a1", "R1", 9, 0)
        launch = artifacts.load_launch(root, "attempt-a1")
        self.assertEqual(launch["seed"], 9)
        self.assertTrue(launch["registered_before_start"])
        with self.assertRaises(ArtifactError) as raised:
            artifacts.load_launch(root, "attempt-absent")
        self.assertEqual(raised.exception.reason, "missing_artifact")
        value = _launch_value("attempt-a2", "R1", "9", 1)
        del value["context_digest"]
        _write_canonical(Path(root) / "attempts" / "attempt-a2" / "launch.json", value)
        with self.assertRaises(ArtifactError) as raised:
            artifacts.load_launch(root, "attempt-a2")
        self.assertEqual(raised.exception.reason, "missing_field")
        write_launch(root, "attempt-a3", "R1", "9", 2, context_digest="nope")
        with self.assertRaises(ArtifactError) as raised:
            artifacts.load_launch(root, "attempt-a3")
        self.assertEqual(raised.exception.reason, "unsupported_schema")


# --- Slice A2b: validated model, row assembly, and trace records ---

_GHOST = "sha256:" + "cd" * 32
_OTHER = "sha256:" + "ef" * 32


def _bound_row(**overrides):
    """Aborted row bound to the fixture run; digests resolved by _sealed_root."""
    row = _row(
        "R1",
        "18",
        "aborted",
        attempts_used=1,
        declined_launches=0,
        outcome="completed",
        verified_score=100,
        policy_digest=_fixture_json("receipt.json")["policy_digest"],
        core_receipt_digest="pending",
        llm_receipt_digest="pending",
        attempt_refs=[
            {
                "attempt_id": "attempt-t1",
                "attempt_ordinal": 0,
                "evidence_status": "bound",
            }
        ],
    )
    row.update(overrides)
    return row


def _unresolved_row():
    """Unbound row whose single attempt reference needs launch evidence."""
    return _row(
        "R3",
        "20",
        "unresolved",
        attempts_used=1,
        attempt_refs=[
            {
                "attempt_id": "attempt-u1",
                "attempt_ordinal": 0,
                "evidence_status": "unresolved",
            }
        ],
    )


def _variant_rows():
    """Aborted bound + declined trace-only + unresolved-with-launch rows."""
    return [_bound_row(), _row("R2", "19", "declined"), _unresolved_row()]


def _sealed_root(prefix, rows, extra_launches=(), **build_kwargs):
    """Fixture-copy root: reseal, resolve pending row digests, write rows.

    Returns ``(root, digests)`` like :func:`build_root`. Each row digest slot
    still holding the ``"pending"`` placeholder is resolved independently, so
    a row may override one slot (e.g. a ghost llm digest) and keep it.
    """
    root, digests = build_root(prefix, **build_kwargs)
    write_launch(root, "attempt-t1", "R1", "18", 0)
    for attempt_id, condition, seed, ordinal in extra_launches:
        write_launch(root, attempt_id, condition, seed, ordinal)
    resolved = []
    for row in rows:
        if row["core_receipt_digest"] == "pending":
            row["core_receipt_digest"] = digests[0]
        if row["llm_receipt_digest"] == "pending":
            row["llm_receipt_digest"] = digests[2]
        resolved.append(row)
    write_leaderboard(root, resolved)
    return root, digests


def _variant_root(prefix, **build_kwargs):
    root, _ = _sealed_root(
        prefix,
        _variant_rows(),
        extra_launches=(("attempt-u1", "R3", "20", 0),),
        **build_kwargs,
    )
    return root


def _projected(value):
    """``plain`` narrowed to its object-projection case for assertions."""
    projected = plain(value)
    assert isinstance(projected, dict)
    return projected


class TestRowAssembly(unittest.TestCase):
    """Slice A2b: authoritative binding of leaderboard rows to run evidence."""

    def test_bound_row_maps_authoritative_item(self):
        root, digests = _sealed_root("obsrow-bound-", [_bound_row()])
        items, skips = assemble_items(root)
        self.assertEqual(skips, [])
        (item,) = items
        self.assertIsInstance(item, model.ValidatedRun)
        receipt = _fixture_json("receipt.json")
        llm = _fixture_json("llm/llm-receipt.json")
        self.assertEqual(
            (
                item.tournament_id,
                item.condition,
                item.seed,
                item.status,
                item.outcome,
                item.verified_score,
                item.attempt_id,
                item.attempt_ordinal,
                item.execution_id,
                item.logical_run_id,
            ),
            (
                _TOURNAMENT,
                "R1",
                "18",
                "aborted",
                "completed",
                100,
                "attempt-t1",
                0,
                "sealed-fixture-01",
                receipt["logical_run_id"],
            ),
        )
        (request,) = item.requests
        self.assertEqual(
            (request.provider, request.model, request.request_id, request.policy_step),
            ("fake", "fake-v1", "req-0", 0),
        )
        self.assertEqual(request.latency_ms, 42)
        self.assertEqual(
            request.usage,
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        self.assertIsInstance(request.raw_visible_response, model.GuardedText)
        self.assertTrue(request.raw_visible_response.has_text)
        self.assertEqual(request.raw_visible_response.value, "ok")
        self.assertIsNone(request.tool_call)
        self.assertIsNone(request.error)
        self.assertEqual(
            item.digests,
            model.EvidenceDigests(
                core_receipt=digests[0],
                provider_evidence=digests[1],
                llm_receipt=digests[2],
                policy=receipt["policy_digest"],
                strategy_context=None,
                prompt=llm["prompt_digest"],
                tool_schema=llm["tool_schema_digest"],
            ),
        )
        self.assertEqual(item.launch["context_digest"], _DIGEST)

    def test_row_variants_stay_distinguishable_in_one_root(self):
        items, skips = assemble_items(_variant_root("obsrow-variants-"))
        self.assertEqual(skips, [])
        self.assertEqual(
            [(type(item).__name__, item.status) for item in items],
            [
                ("ValidatedRun", "aborted"),
                ("TraceOnlyRow", "declined"),
                ("TraceOnlyRow", "unresolved"),
            ],
        )
        bound, declined, unresolved = items
        self.assertEqual(
            (bound.attempts_used, declined.attempts_used, unresolved.attempts_used),
            (1, 0, 1),
        )
        self.assertIsNone(declined.outcome)
        for row in (declined, unresolved):
            self.assertEqual(astuple(row.digests), (None,) * 7)

    def test_unresolved_requires_agreeing_launch(self):
        absent, _ = _sealed_root("obsrow-unresolved-", [_unresolved_row()])
        items, skips = assemble_items(absent)
        self.assertEqual(items, [])
        self.assertEqual([skip.reason for skip in skips], ["missing_artifact"])
        self.assertTrue(skips[0].path.endswith("attempts/attempt-u1/launch.json"))
        disagree, _ = _sealed_root(
            "obsrow-unresolved-dis-",
            [_unresolved_row()],
            extra_launches=(("attempt-u1", "RX", "20", 0),),
        )
        items, skips = assemble_items(disagree)
        self.assertEqual(items, [])
        self.assertEqual([skip.reason for skip in skips], ["identity_mismatch"])

    def test_declined_trace_only_has_no_manufactured_values(self):
        root, _ = _sealed_root("obsrow-declined-", [_row("R2", "19", "declined")])
        items, skips = assemble_items(root)
        self.assertEqual(skips, [])
        (row,) = items
        self.assertIsInstance(row, model.TraceOnlyRow)
        mapping = _projected(row)
        self.assertIsNone(mapping["outcome"])
        self.assertEqual(
            mapping["digests"],
            dict.fromkeys(
                (
                    "core_receipt",
                    "provider_evidence",
                    "llm_receipt",
                    "policy",
                    "strategy_context",
                    "prompt",
                    "tool_schema",
                )
            ),
        )
        for absent in ("verified_score", "latency_ms", "usage"):
            self.assertNotIn(absent, mapping)
        self.assertIn('"outcome":null', canonical_json_bytes(mapping).decode("utf-8"))

    def test_ghost_digest_row_skips_while_bound_row_stays(self):
        root, _ = _sealed_root(
            "obsrow-ghost-",
            [
                _bound_row(),
                _bound_row(
                    condition="R2",
                    seed="19",
                    core_receipt_digest=_GHOST,
                    llm_receipt_digest=_GHOST,
                ),
            ],
        )
        items, skips = assemble_items(root)
        self.assertEqual([type(item).__name__ for item in items], ["ValidatedRun"])
        self.assertEqual(items[0].condition, "R1")
        self.assertEqual([skip.reason for skip in skips], ["missing_artifact"])
        self.assertTrue(skips[0].path.endswith("core/runs"))
        evidence, _ = artifacts.discover_evidence(root)
        self.assertEqual(len(evidence), 1)

    def test_row_receipt_disagreements_are_identity_mismatches(self):
        cases = {
            "score": dict(verified_score=99),
            "llm_digest": dict(llm_receipt_digest=_GHOST),
            "seed": dict(seed="19"),
            "strategy_context": dict(strategy_context_digest=_OTHER),
        }
        for label, overrides in cases.items():
            with self.subTest(case=label):
                root, _ = _sealed_root(
                    f"obsrow-dis-{label}-", [_bound_row(**overrides)]
                )
                items, skips = assemble_items(root)
                self.assertEqual(items, [])
                self.assertEqual(
                    [skip.reason for skip in skips],
                    ["identity_mismatch"],
                )
                if label == "strategy_context":
                    self.assertTrue(
                        skips[0].path.endswith("attempts/attempt-t1/launch.json")
                    )
                else:
                    self.assertEqual(
                        skips[0].path, str(_run_dir(root) / "receipt.json")
                    )

    def test_guarded_text_never_reaches_a_mapping(self):
        def with_tool_call(d):
            provider = json.loads((d / "llm" / "provider-run.json").read_text())
            provider["requests"][0]["tool_call"] = {
                "name": "bin",
                "arguments": "bin-3",
                "call_id": "call-1",
            }
            _write_canonical(d / "llm" / "provider-run.json", provider)

        root, _ = _sealed_root("obsrow-guarded-", [_bound_row()], patch=with_tool_call)
        items, skips = assemble_items(root)
        self.assertEqual(skips, [])
        (item,) = items
        (request,) = item.requests
        self.assertEqual(
            (request.tool_call.name, request.tool_call.structural_fields),
            ("bin", {"call_id": "call-1"}),
        )
        self.assertIsInstance(request.tool_call.arguments, model.GuardedText)
        self.assertEqual(request.tool_call.arguments.value, "bin-3")
        self.assertNotIn("bin-3", repr(item))
        (record,) = build_trace_records(items)
        (generation,) = record.generations
        self.assertEqual(
            generation.tool_call,
            {"name": "bin", "structural_fields": {"call_id": "call-1"}},
        )
        rendered = canonical_json_bytes(plain(record)).decode("utf-8")
        for forbidden in ("arguments", "bin-3", "raw_visible_response"):
            self.assertNotIn(forbidden, rendered)
        with self.assertRaises(ArtifactError) as raised:
            plain(item)
        self.assertEqual(raised.exception.reason, "secret_detected")

    def test_guarded_text_semantics(self):
        guarded = model.GuardedText("ok")
        self.assertEqual(guarded.value, "ok")
        self.assertTrue(guarded.has_text)
        self.assertNotIn("ok", repr(guarded))
        self.assertEqual(repr(guarded), "GuardedText(<withheld>)")
        self.assertEqual(str(guarded), "<guarded-text>")
        self.assertFalse(model.GuardedText(None).has_text)
        with self.assertRaises(AttributeError):
            guarded._value = "mutated"
        self.assertEqual(
            model.TEXT_CAPABLE_FIELDS,
            ("raw_visible_response", "tool_call.arguments"),
        )

    def test_explicit_null_response_stays_null(self):
        def null_response(d):
            provider = json.loads((d / "llm" / "provider-run.json").read_text())
            provider["requests"][0]["raw_visible_response"] = None
            _write_canonical(d / "llm" / "provider-run.json", provider)

        root, _ = _sealed_root("obsrow-null-", [_bound_row()], patch=null_response)
        items, skips = assemble_items(root)
        self.assertEqual(skips, [])
        (item,) = items
        self.assertIsNone(item.requests[0].raw_visible_response)
        rendered = canonical_json_bytes(_projected(item)).decode("utf-8")
        self.assertIn('"raw_visible_response":null', rendered)
        (record,) = build_trace_records(items)
        rendered = canonical_json_bytes(plain(record)).decode("utf-8")
        self.assertNotIn("raw_visible_response", rendered)

    def test_trace_records_preserve_authority_and_emptiness(self):
        items, skips = assemble_items(_variant_root("obsrow-trace-"))
        self.assertEqual(skips, [])
        bound, declined, unresolved = build_trace_records(items)
        self.assertEqual(
            [record.status for record in (bound, declined, unresolved)],
            ["aborted", "declined", "unresolved"],
        )
        self.assertEqual(len(bound.generations), 1)
        (generation,) = bound.generations
        self.assertFalse(hasattr(generation, "raw_visible_response"))
        self.assertEqual(
            (
                generation.provider,
                generation.model,
                generation.request_id,
                generation.policy_step,
                generation.latency_ms,
            ),
            ("fake", "fake-v1", "req-0", 0, 42),
        )
        self.assertEqual(generation.usage, {"input": 10, "output": 5, "total": 15})
        (score,) = bound.scores
        self.assertEqual(
            (
                score.name,
                score.value,
                score.data_type,
                score.status,
                score.source,
                score.receipt_digest,
            ),
            (
                "verified_score",
                100,
                "NUMERIC",
                "valid",
                "receipt",
                bound.digests.core_receipt,
            ),
        )
        self.assertEqual(bound.outcome, "completed")
        self.assertEqual(
            (bound.attempt_id, bound.execution_id),
            ("attempt-t1", "sealed-fixture-01"),
        )
        for record in (declined, unresolved):
            self.assertEqual((record.generations, record.scores), ((), ()))
            self.assertIsNone(record.attempt_id)
            self.assertIsNone(record.attempt_ordinal)
            self.assertIsNone(record.execution_id)
            self.assertIsNone(record.logical_run_id)
            self.assertIsNone(record.outcome)
            self.assertIsNone(record.digests.core_receipt)
        canonical_json_bytes(plain([bound, declined, unresolved]))

    def test_missing_leaderboard_is_one_safe_skip(self):
        root, _ = build_root("obsrow-noleader-")
        items, skips = assemble_items(root)
        self.assertEqual(items, [])
        self.assertEqual([skip.reason for skip in skips], ["missing_artifact"])
        self.assertTrue(skips[0].path.endswith("leaderboard.json"))


if __name__ == "__main__":
    unittest.main()
