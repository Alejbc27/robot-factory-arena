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
            '<canvas id="visualizer-canvas" width="640" height="480" aria-label="2D mission replay arena">',
            "const GRID_SIZE = 10;",
            "const CELL_SIZE = 44;",
            "const ORIGIN_X = 20;",
            "const ORIGIN_Y = 20;",
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

    def test_renderer_uses_procedural_warehouse_art_and_hud_feedback(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        for marker in (
            "function drawFloor",
            "function drawDepotSprite",
            "function drawShelfSprite",
            "function drawCrateSprite",
            "function drawRobotSprite",
            "function drawPathTrail",
            "const PATH_TRAIL_LENGTH = 6;",
            "function classificationStatus",
            "const CLASSIFICATION_STATUS = Object.freeze({",
            "function normalizedActionText",
            "function drawHud(world, frame)",
            "projection.frames[index]",
            "state_after.robot.position",
            "frame.classification",
            "frame.subevents",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertIn("drawPathTrail(world, frame)", html)
        self.assertIn("drawHud(world, frame)", html)
        self.assertNotIn("drawFrameFeedback(frame)", html)

    def test_renderer_moves_feedback_from_arena_badge_to_hud_action_and_status(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        self.assertNotIn("function drawFrameFeedback", html)
        self.assertNotIn("drawFrameFeedback(frame)", html)
        self.assertIn("function drawHud(world, frame)", html)
        self.assertIn('drawText("ACTION"', html)
        self.assertIn('drawText("STATUS"', html)
        self.assertIn("normalizedActionText(frame)", html)
        self.assertIn("classificationStatus(frame)", html)
        for classification in (
            "committed",
            "rejected",
            "malformed",
            "unsupported",
            "invalid",
            "unauthorized",
            "idempotent_replay",
        ):
            with self.subTest(classification=classification):
                self.assertIn(classification + ":", html)

    def test_renderer_keeps_six_positions_with_consecutive_deduped_pips(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        self.assertIn("let lastPositionKey = null;", html)
        self.assertIn(
            "for (let index = selectedFrame; index >= 0 && positions.length < PATH_TRAIL_LENGTH; index -= 1)",
            html,
        )
        self.assertIn("if (positionKey === lastPositionKey) continue;", html)
        self.assertIn("lastPositionKey = positionKey;", html)
        self.assertIn("positions.unshift(position)", html)
        self.assertIn("function drawMovementPip", html)
        self.assertIn("drawMovementPip(position, index, positions.length)", html)
        self.assertNotIn("const seenPositions = new Set();", html)
        self.assertNotIn("projection.frames.slice(Math.max", html)

    def test_renderer_formats_current_safe_action_arguments_for_the_hud(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        for marker in (
            "function actionArgumentText(frame)",
            'case "move":',
            'case "pick":',
            'case "place":',
            'return "MOVE "',
            'return "PICK "',
            'return "PLACE "',
            "call.arguments.direction",
            "call.arguments.shelf_id",
            "call.arguments.item_id",
            "call.arguments.order_id",
            "drawText(actionArgumentText(frame)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_renderer_separates_classification_from_fault_and_retry_hud_subevents(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        for marker in (
            "function subeventStatus(frame)",
            'event.kind === "fault_injected"',
            'event.kind === "internal_retry"',
            'drawText("STATUS",',
            'drawText("FAULT",',
            'drawText("RETRY",',
            "drawHudSubevents(frame)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertIn('drawText(status.label, HUD_X + 82, 112', html)

    def test_renderer_draws_target_cues_only_from_safe_tool_call_arguments(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        for marker in (
            "function safeTargetForFrame(frame, world)",
            "const call = frame === null ? null : frame.tool_call;",
            "call.arguments.direction",
            "call.arguments.shelf_id",
            "call.arguments.order_id",
            "function drawTargetCue(target)",
            "drawTargetCue(safeTargetForFrame(frame, world))",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        for forbidden in (
            "frame.committed_domain_events",
            "frame.state_delta",
            "frame.attempts",
        ):
            self.assertNotIn(forbidden, html[html.index("function safeTargetForFrame"):html.index("function drawTargetCue")])

    def test_renderer_uses_fixed_ten_order_grid_before_fixed_sections(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        for marker in (
            "const ORDER_SLOT_COUNT = 10;",
            "const ORDER_GRID_COLUMNS = 2;",
            "const ORDER_GRID_ROWS = 5;",
            "const ORDER_GRID_Y = 128;",
            "const ORDER_SLOT_WIDTH = 64;",
            "const ORDER_SLOT_HEIGHT = 28;",
            "const ORDER_SLOT_GAP = 4;",
            "const ROBOT_SECTION_Y = 304;",
            "const VERIFIER_SECTION_Y = 410;",
            "for (let index = 0; index < ORDER_SLOT_COUNT; index += 1)",
            "const column = index % ORDER_GRID_COLUMNS;",
            "const row = Math.floor(index / ORDER_GRID_COLUMNS);",
            "const slotY = ORDER_GRID_Y + row * (ORDER_SLOT_HEIGHT + ORDER_SLOT_GAP);",
            "context.fillRect(slotX, slotY, ORDER_SLOT_WIDTH, ORDER_SLOT_HEIGHT);",
            "world.orders[index] || null",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertNotIn("let orderY = 135", html)
        self.assertNotIn("orderY += 36", html)

        def constant(name):
            match = re.search(rf"const {name} = (\d+);", html)
            self.assertIsNotNone(match, name)
            return int(match.group(1))

        slot_count = constant("ORDER_SLOT_COUNT")
        columns = constant("ORDER_GRID_COLUMNS")
        rows = constant("ORDER_GRID_ROWS")
        grid_y = constant("ORDER_GRID_Y")
        slot_height = constant("ORDER_SLOT_HEIGHT")
        slot_gap = constant("ORDER_SLOT_GAP")
        robot_section_y = constant("ROBOT_SECTION_Y")
        verifier_section_y = constant("VERIFIER_SECTION_Y")
        grid_bottom = grid_y + rows * slot_height + (rows - 1) * slot_gap
        self.assertEqual(slot_count, columns * rows)
        self.assertEqual(slot_count, 10)
        self.assertEqual(grid_bottom, 284)
        self.assertLess(grid_bottom, robot_section_y)
        self.assertLess(robot_section_y, verifier_section_y)

    def test_renderer_makes_arena_primary_and_details_collapsible(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        for marker in (
            'class="visualizer-shell"',
            'aria-label="2D mission replay arena"',
            'id="mission-progress"',
            '<details id="frame-details-panel" class="details-panel">',
            '<summary>Frame evidence',
            '<pre id="frame-details"',
            'frameDetails.textContent = detailLines(frame, world).join("\\n")',
            'detailsSummary.textContent',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertNotIn("drawText(lines[index]", html)

    def test_renderer_keeps_verifier_owned_result_in_the_visible_overview(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection).decode("utf-8")
        self.assertIn('class="terminal-panel overview-panel"', html)
        self.assertIn('terminalText.textContent =', html)
        self.assertIn("Verifier-owned score", html)
        self.assertIn("Verifier-owned outcome", html)

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
