"""Contract tests for the projection-only offline visualizer renderer."""

import re
import unittest

from robot_factory_arena.evidence.visualizer_projection import (
    canonical_projection_bytes,
    validate_projection,
)
from tests.test_visualizer_projection import committed_projection, projection


class VisualizerRendererTests(unittest.TestCase):
    def setUp(self):
        self.projection = validate_projection(projection())

    def test_renderer_embeds_only_canonical_projection_hex(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection)
        match = re.search(
            rb'<script id="visualizer-projection-hex" '
            rb'type="application/octet-stream">([0-9a-f]+)</script>',
            html,
        )
        assert match is not None, "canonical hex payload is absent"
        self.assertEqual(
            bytes.fromhex(match.group(1).decode("ascii")),
            canonical_projection_bytes(self.projection),
        )
        self.assertEqual(render_visualizer(self.projection), html)

    def test_renderer_has_fixed_offline_canvas_ui_contract(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        for marker in (
            '<canvas id="visualizer-canvas" width="640" height="480">',
            "const GRID_SIZE = 10;",
            "const CELL_SIZE = 40;",
            "const ORIGIN_X = 16;",
            "const ORIGIN_Y = 16;",
            "#101820",
            "#203040",
            "#2d4059",
            "#8f5b34",
            "#5e432f",
            "#f2c14e",
            "#4cc9f0",
            "#f72585",
            "#f6f7eb",
            "#80ed99",
            "#ffca3a",
            "#ff595e",
            "image-rendering: pixelated",
            ">Play<",
            ">Pause<",
            ">Step forward<",
            '<option value="0.5">0.5x</option>',
            '<option value="1" selected>1x</option>',
            '<option value="2">2x</option>',
            '<option value="4">4x</option>',
            "Verifier-owned score",
            "Verifier-owned outcome",
            "report signal: verification validity",
            "report signal: replay agreement",
            "report signal: receipt binding",
            "report signal: visualizer is non-authoritative",
            "textContent",
            "fillText",
            "committed events",
            "fault/retry subevents",
            "state delta",
            "policy budget",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_renderer_is_projection_only_and_has_no_external_or_raw_evidence_access(
        self,
    ):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        forbidden = (
            "src=",
            "href=",
            "http://",
            "https://",
            "fetch(",
            "XMLHttpRequest",
            "WebSocket",
            "import(",
            "serviceWorker",
            "innerHTML",
            "trace.jsonl",
            "receipt.json",
            "replay-input.json",
            "operation-log.json",
            "task.json",
            "config.json",
            "report.html",
            "execution_id",
            "generated_at",
            "hostname",
            "latency",
            "receipt_digest",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, html)

    def test_renderer_rejects_raw_mapping(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        with self.assertRaises(TypeError):
            render_visualizer(self.projection.to_mapping())


class VisualizerRendererRemediationTests(unittest.TestCase):
    """Verify-report 7011: normative display fields must be visibly rendered.

    The renderer is projection-only, so the only honest static proof that a
    value is displayed is that the template reads and projects the exact
    spec-normative projection field into panel text.
    """

    def setUp(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        self.committed = validate_projection(committed_projection())
        self.html = render_visualizer(self.committed).decode("utf-8")

    def test_normalized_tool_call_arguments_are_displayed(self):
        self.assertIn(".arguments", self.html)
        self.assertIn("JSON.stringify", self.html)

    def test_state_delta_details_are_displayed(self):
        for marker in (
            "position_before",
            "position_after",
            "inventory_before",
            "inventory_after",
            "item_id_before",
            "item_id_after",
            "completed_orders_added",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_fault_and_retry_subevent_details_are_displayed(self):
        for marker in (
            ".fault_id",
            ".hook",
            ".fault_kind",
            ".attempt_index",
            ".status",
            ".reason",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_terminal_panel_shows_verifier_version_and_completion_counts(self):
        for marker in (
            "verifier_version",
            "verifier_status",
            "completed_count",
            "total_count",
            "Verifier-owned score",
            "Verifier-owned outcome",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_budget_used_remaining_and_maximum_are_displayed(self):
        for marker in (
            "policy_steps_before",
            "policy_steps_remaining_before",
            "policy_steps_maximum",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_committed_fixture_html_stays_deterministic(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        again = render_visualizer(validate_projection(committed_projection()))
        self.assertEqual(render_visualizer(self.committed), again)

    def test_empty_pending_order_list_renders_explicit_none_fallback(self):
        """Report 7024: the none fallback must guard the pending list itself.

        JS binds ``+`` tighter than ``||``, so the ungrouped expression
        rendered ``"orders pending "`` (always truthy) for an empty list
        and never reached the ``none`` fallback.
        """
        self.assertIn('"orders pending " + (world.orders.filter', self.html)
        self.assertIn(
            '.map((order) => order.order_id).join(",") || "none"),', self.html
        )
        # Non-empty lists still display the filtered, joined order ids.
        self.assertIn(
            ".filter((order) => !world.completed_orders.includes(order.order_id))",
            self.html,
        )


if __name__ == "__main__":
    unittest.main()
