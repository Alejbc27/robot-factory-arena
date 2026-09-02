import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from robot_factory_arena import cli


class TestLlmUsageReporting(unittest.TestCase):
    def _provider_run(self, model, usages):
        return {
            "schema_version": 1,
            "logical_run_id": "lr_" + "a" * 64,
            "execution_id": "execution-test",
            "requests": [
                {
                    "model": model,
                    "usage": usage,
                }
                for usage in usages
            ],
        }

    def _write_provider_run(self, root, provider_run):
        llm_directory = Path(root) / "llm"
        llm_directory.mkdir()
        (llm_directory / "provider-run.json").write_text(
            json.dumps(provider_run), encoding="utf-8"
        )

    def test_aggregates_usage_across_ordered_requests(self):
        totals, model = cli._aggregate_llm_usage(
            self._provider_run(
                "gpt-5.6-luna",
                [
                    {"input_tokens": 80, "output_tokens": 10, "total_tokens": 90},
                    {"input_tokens": 6, "output_tokens": 5, "total_tokens": 11},
                ],
            )
        )

        self.assertEqual(model, "gpt-5.6-luna")
        self.assertEqual(
            totals,
            {"input_tokens": 86, "output_tokens": 15, "total_tokens": 101},
        )

    def test_luna_cost_arithmetic_is_reported_to_four_decimal_places(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_provider_run(
                root,
                self._provider_run(
                    "gpt-5.6-luna",
                    [
                        {
                            "input_tokens": 86582,
                            "output_tokens": 1735,
                            "total_tokens": 88317,
                        }
                    ],
                ),
            )
            output = StringIO()
            with redirect_stdout(output):
                cli._report_llm_usage(root)

        self.assertIn(
            "llm_usage: input_tokens=86582 output_tokens=1735 total_tokens=88317",
            output.getvalue(),
        )
        self.assertIn("llm_estimated_cost_usd: 0.0194", output.getvalue())

    def test_unknown_model_cost_is_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_provider_run(
                root,
                self._provider_run(
                    "future-model",
                    [{"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}],
                ),
            )
            output = StringIO()
            with redirect_stdout(output):
                cli._report_llm_usage(root)

        self.assertIn(
            "llm_estimated_cost_usd: unavailable (no pricing for future-model)",
            output.getvalue(),
        )

    def test_luna_run_with_no_usage_reports_zero_cost(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_provider_run(
                root,
                self._provider_run(
                    "gpt-5.6-luna",
                    [{"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}],
                ),
            )
            output = StringIO()
            with redirect_stdout(output):
                cli._report_llm_usage(root)

        self.assertIn(
            "llm_usage: input_tokens=0 output_tokens=0 total_tokens=0",
            output.getvalue(),
        )
        self.assertIn("llm_estimated_cost_usd: 0.0000", output.getvalue())

    def test_run_reporting_prints_usage_after_llm_report(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_provider_run(
                root,
                self._provider_run(
                    "gpt-5.6-luna",
                    [{"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}],
                ),
            )
            args = SimpleNamespace(
                provider="openai",
                model="gpt-5.6-luna",
                script=None,
                fault_step=None,
                fault_intent=None,
                execution_id="execution-test",
                seed="18",
                policy="greedy-v0",
                artifacts_root=root,
            )
            verification = SimpleNamespace(
                outcome="completed", verified_score=1, verifier_version="v0"
            )
            receipt = {"logical_run_id": "lr_" + "a" * 64}
            output = StringIO()
            with (
                patch.object(
                    cli,
                    "_execute_run",
                    return_value=(0, receipt, verification, Path(root)),
                ),
                redirect_stdout(output),
            ):
                result = cli._run_and_report(args, rerun=False)

        self.assertEqual(result, 0)
        lines = output.getvalue().splitlines()
        report_index = next(
            i for i, line in enumerate(lines) if line.startswith("llm_report:")
        )
        usage_index = next(
            i for i, line in enumerate(lines) if line.startswith("llm_usage:")
        )
        self.assertLess(report_index, usage_index)


if __name__ == "__main__":
    unittest.main()
