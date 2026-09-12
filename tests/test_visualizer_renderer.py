"""Contract tests for the projection-only offline visualizer renderer."""

import base64
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import unittest

from robot_factory_arena.evidence.visualizer_projection import (
    canonical_projection_bytes,
    validate_projection,
)
from tests.test_visualizer_projection import projection


def execute_renderer_script(html, projection_hex, probe=""):
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.DOTALL)
    script = scripts[-1] + probe
    harness = """
const elements = new Map();
function makeElement(textContent = "") {
  const classes = new Set();
  const children = new Map();
  return {
    textContent,
    hidden: false,
    disabled: false,
    value: "",
    style: { width: "" },
    classList: {
      add(...names) { names.forEach((name) => classes.add(name)); },
      remove(...names) { names.forEach((name) => classes.delete(name)); }
    },
    children,
    addEventListener() {},
    querySelector(selector) { return children.get(selector) || null; }
  };
}
const context = new Proxy({ clearRect() {} }, {
  get(target, property) {
    if (!(property in target)) target[property] = () => {};
    return target[property];
  }
});
const canvas = makeElement();
canvas.width = 484;
canvas.height = 480;
canvas.getContext = () => context;
for (const id of [
  "status-text", "terminal-text", "frame-details", "details-summary",
  "hud-completed", "hud-progress-bar", "hud-action", "hud-status",
  "hud-robot-position", "hud-robot-load", "hud-score", "hud-outcome",
  "hud-event-0", "hud-event-1", "speed", "visualizer-unavailable",
  "scene-legend", "play", "pause", "step-forward"
]) elements.set("#" + id, makeElement());
for (let index = 0; index < 10; index += 1) {
  const slot = makeElement();
  slot.children.set(".order-state", makeElement());
  slot.children.set(".order-id", makeElement());
  slot.children.set(".order-item", makeElement());
  elements.set('.order-slot[data-order-index="' + index + '"]', slot);
}
elements.get("#visualizer-unavailable").hidden = true;
elements.set("#visualizer-canvas", canvas);
elements.set("#visualizer-projection-hex", makeElement(PROJECTION_HEX));
elements.get("#speed").value = "1";
globalThis.document = {
  querySelector(selector) {
    return elements.get(selector) || null;
  }
};
const rendererScript = RENDERER_SCRIPT;
eval(rendererScript);
process.stdout.write(JSON.stringify({
  unavailable: elements.get("#visualizer-unavailable").hidden === false,
  status: elements.get("#status-text").textContent,
  mission: elements.get("#hud-completed").textContent,
  robot: elements.get("#hud-robot-position").textContent,
  inventory: elements.get("#hud-robot-load").textContent,
  terminal: elements.get("#terminal-text").textContent,
  controlsDisabled: elements.get("#play").disabled && elements.get("#pause").disabled && elements.get("#step-forward").disabled && elements.get("#speed").disabled,
  probe: globalThis.__rendererProbe || null
}));
""".replace("PROJECTION_HEX", json.dumps(projection_hex)).replace(
        "RENDERER_SCRIPT", json.dumps(script)
    )
    completed = subprocess.run(
        ["node"],
        input=harness,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


class VisualizerRendererTests(unittest.TestCase):
    def setUp(self):
        self.projection = validate_projection(projection())

    def render(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        return render_visualizer(self.projection).decode("utf-8")

    def test_renderer_embeds_only_canonical_projection_hex(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        html = render_visualizer(self.projection)
        match = re.search(
            rb'<script id="visualizer-projection-hex" '
            rb'type="application/octet-stream">([0-9a-f]+)</script>',
            html,
        )
        self.assertIsNotNone(match, "canonical hex payload is absent")
        self.assertEqual(
            bytes.fromhex(match.group(1).decode("ascii")),
            canonical_projection_bytes(self.projection),
        )
        self.assertEqual(render_visualizer(self.projection), html)

    def test_renderer_fails_open_for_invalid_embedded_projection_input(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        if shutil.which("node") is None:
            self.skipTest("node is required to execute the embedded renderer")
        html = render_visualizer(self.projection).decode("utf-8")
        invalid_inputs = (
            "0",  # odd-length hex
            "gg",  # non-hex input
            "ff",  # invalid UTF-8 with fatal decoding
            b"{".hex(),  # invalid JSON
            b"{}".hex(),  # parsed JSON that fails renderer initialization
        )
        for invalid_input in invalid_inputs:
            with self.subTest(invalid_input=invalid_input):
                state = execute_renderer_script(html, invalid_input)
                self.assertTrue(state["unavailable"])
                self.assertEqual(state["mission"], "Unavailable")
                self.assertEqual(state["robot"], "Unavailable")
                self.assertEqual(state["inventory"], "Unavailable")
                self.assertIn("non-authoritative", state["terminal"])
                self.assertTrue(state["controlsDisabled"])

    def test_renderer_valid_projection_initializes_deterministically(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        if shutil.which("node") is None:
            self.skipTest("node is required to execute the embedded renderer")
        html = render_visualizer(self.projection).decode("utf-8")
        state = execute_renderer_script(
            html, canonical_projection_bytes(self.projection).hex()
        )
        self.assertFalse(state["unavailable"])
        self.assertIn("Verifier-owned score", state["terminal"])
        self.assertNotEqual(state["mission"], "Unavailable")

    def test_renderer_uses_world_only_canvas(self):
        html = self.render()

        self.assertIn(
            '<canvas id="visualizer-canvas" width="484" height="480"', html
        )
        self.assertIn("const GRID_SIZE = 10;", html)
        self.assertIn("const CELL_SIZE = 44;", html)
        self.assertIn("const ORIGIN_X = 20;", html)
        self.assertIn("const ORIGIN_Y = 20;", html)
        self.assertIn("function drawWorld(world, frame)", html)
        self.assertIn("drawFloor();", html)
        self.assertIn("drawRobotSprite(world.robot);", html)

    def test_renderer_uses_html_mission_hud(self):
        html = self.render()

        self.assertIn('class="mission-hud" aria-label="Mission telemetry"', html)
        for label in (
            "MISSION",
            "ACTION",
            "STATUS",
            "FAULT / RETRY",
            "ORDER QUEUE",
            "ROBOT",
            "VERIFIER-OWNED",
        ):
            with self.subTest(label=label):
                self.assertIn(label, html)
        for element_id in (
            "hud-completed",
            "hud-progress-bar",
            "hud-action",
            "hud-status",
            "hud-subevents",
            "hud-robot-position",
            "hud-robot-load",
            "hud-score",
            "hud-outcome",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn('id="' + element_id + '"', html)

    def test_renderer_does_not_draw_telemetry_over_canvas(self):
        html = self.render()

        self.assertNotIn("function drawHud", html)
        self.assertNotIn("function drawHudSubevents", html)
        self.assertNotIn("HUD_X", html)
        self.assertNotIn("ORDER_GRID_Y", html)
        self.assertNotIn("ROBOT_SECTION_Y", html)
        self.assertNotIn("VERIFIER_SECTION_Y", html)
        self.assertNotIn("drawText(\"MISSION\"", html)

    def test_renderer_has_exactly_ten_order_slots(self):
        html = self.render()
        slots = re.findall(
            r'<div class="order-slot" data-order-index="(\d+)">', html
        )

        self.assertEqual(slots, [str(index) for index in range(10)])
        self.assertEqual(len(re.findall(r'class="order-slot"', html)), 10)
        self.assertIn("function renderOrderQueue(world)", html)
        self.assertIn("slot.querySelector(\".order-state\")", html)
        self.assertIn("slot.querySelector(\".order-id\")", html)
        self.assertIn("slot.querySelector(\".order-item\")", html)

    def test_renderer_updates_hud_via_text_content(self):
        html = self.render()

        self.assertNotIn("innerHTML", html)
        self.assertIn("textContent", html)
        self.assertIn("classList", html)
        self.assertIn("hudProgressBar.style.width", html)
        self.assertIn("document.querySelector", html)
        self.assertNotIn("getElementById", html)

    def test_renderer_preserves_verifier_authority(self):
        html = self.render()

        verifier_hud = html[html.index("function renderVerifierHud"):html.index("function detailLines")]
        self.assertIn("const result = projection.terminal_result;", verifier_hud)
        self.assertIn("hudScore.textContent = String(result.verified_score);", verifier_hud)
        self.assertIn("hudOutcome.textContent = result.outcome;", verifier_hud)
        self.assertNotIn("world.verified_score", verifier_hud)
        self.assertNotIn("world.outcome", verifier_hud)

    def test_renderer_keeps_details_available_but_secondary(self):
        html = self.render()

        self.assertIn('<details id="frame-details-panel" class="details-panel">', html)
        self.assertIn("Evidence / technical replay details", html)
        self.assertIn('<span id="status-text">', html)
        self.assertIn('<span id="terminal-text">', html)
        self.assertIn('<pre id="frame-details"', html)
        self.assertNotIn("status-panel", html)
        self.assertNotIn("terminal-panel", html)
        self.assertIn("function renderReplayMetadata(frame, world)", html)
        self.assertIn('frameDetails.textContent = detailLines(frame, world).join("\\n")', html)

    def test_renderer_is_byte_deterministic(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        first = render_visualizer(self.projection)
        second = render_visualizer(validate_projection(projection()))
        self.assertEqual(first, second)

    def test_renderer_embeds_the_packaged_atlas_once(self):
        html = self.render()
        asset = Path("robot_factory_arena/evidence/assets/tactical_reference_atlas.png")
        atlas = asset.read_bytes()
        payloads = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html)

        self.assertTrue(asset.is_file())
        self.assertLess(len(atlas), 852 * 1024)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(hashlib.sha256(base64.b64decode(payloads[0])).digest(), hashlib.sha256(atlas).digest())
        self.assertIn('resources.files("robot_factory_arena.evidence")', Path("robot_factory_arena/evidence/visualizer_renderer.py").read_text())
        self.assertIn('"robot_factory_arena.evidence" = ["assets/tactical_reference_atlas.png"]', Path("pyproject.toml").read_text())

    def test_renderer_uses_atlas_crop_maps_for_every_dynamic_world_sprite(self):
        html = self.render()
        for name in (
            "robot_front", "robot_east", "robot_north", "robot_west", "depot",
            "rack_0", "rack_1", "rack_2", "crate_0", "crate_1", "crate_2", "crate_3",
            "floor_0", "floor_1", "floor_2",
        ):
            with self.subTest(name=name):
                self.assertIn(name, html)
        self.assertIn("context.drawImage(atlasImage", html)
        self.assertIn('stableIndex(shelfId, 3)', html)
        self.assertIn("stableIndex(String(label || \"cargo\"), 4)", html)

    def test_renderer_computes_contained_tile_destinations(self):
        if shutil.which("node") is None:
            self.skipTest("node is required to execute the embedded renderer")
        state = execute_renderer_script(
            self.render(), canonical_projection_bytes(self.projection).hex(),
            "\nglobalThis.__rendererProbe = { rack: rackDestination([2, 3]), depot: depotDestination([2, 3]), robot: robotDestination([2, 3]) };",
        )
        rack, depot, robot = state["probe"]["rack"], state["probe"]["depot"], state["probe"]["robot"]
        self.assertEqual((rack["width"], rack["height"]), (38, 40))
        self.assertEqual((depot["width"], depot["height"]), (40, 40))
        self.assertEqual((robot["width"], robot["height"]), (36, 38))
        for rectangle in (rack, depot, robot):
            self.assertGreaterEqual(rectangle["x"], 20 + 2 * 44)
            self.assertGreaterEqual(rectangle["y"], 20 + 3 * 44)
            self.assertLessEqual(rectangle["x"] + rectangle["width"], 20 + 3 * 44)
            self.assertLessEqual(rectangle["y"] + rectangle["height"], 20 + 4 * 44)

    def test_renderer_has_no_atlas_decoration_destinations_in_grid(self):
        html = self.render()
        for decoration in ("hazard_corner", "wall_corner", "wall_short", "wall_low", "wall_wide", "wall_panel", "frame_wide", "frame_medium", "frame_square"):
            with self.subTest(decoration=decoration):
                self.assertNotIn(decoration, html)
        self.assertIn("const GRID_RECT = Object.freeze", html)
        self.assertIn('drawAtlasSprite("floor_" + ((column * 3 + row) % 3), x, y, CELL_SIZE, CELL_SIZE)', html)

    def test_renderer_draws_cargo_only_for_nonempty_entities(self):
        html = self.render()
        rack_body = html[html.index("function drawShelfSprite"):html.index("function robotFacing")]
        robot_body = html[html.index("function drawRobotSprite"):html.index("function drawMovementPip")]
        self.assertIn("if (shelf.item_id !== null)", rack_body)
        self.assertIn("if (robot.inventory_item_id !== null)", robot_body)
        self.assertIn("function drawRackShell", html)
        self.assertIn("function drawRobotShell", html)
        self.assertIn("opaque interior reconstruction makes the shell empty", html)
        self.assertIn("Opaque roof compositing removes it for empty robots", html)

    def test_renderer_uses_separate_atlas_cargo_only_when_loaded(self):
        if shutil.which("node") is None:
            self.skipTest("node is required to execute the embedded renderer")
        probe = """
atlasReady = true; atlasImage = {};
const crops = [];
context.drawImage = (...args) => crops.push(args[1]);
drawShelfSprite({ shelf_id: "shelf-empty", position: [1, 1], item_id: null });
const emptyRack = crops.slice(); crops.length = 0;
drawShelfSprite({ shelf_id: "shelf-loaded", position: [1, 1], item_id: "item-00" });
const loadedRack = crops.slice(); crops.length = 0;
drawRobotSprite({ position: [1, 1], inventory_item_id: null });
const emptyRobot = crops.slice(); crops.length = 0;
drawRobotSprite({ position: [1, 1], inventory_item_id: "item-00" });
const loadedRobot = crops.slice();
globalThis.__rendererProbe = { emptyRack, loadedRack, emptyRobot, loadedRobot };
"""
        state = execute_renderer_script(self.render(), canonical_projection_bytes(self.projection).hex(), probe)
        for key in ("emptyRack", "emptyRobot"):
            self.assertEqual(len(state["probe"][key]), 1, key)
            self.assertLess(state["probe"][key][0], 461, key)
        for key in ("loadedRack", "loadedRobot"):
            self.assertEqual(len(state["probe"][key]), 2, key)
            self.assertGreaterEqual(state["probe"][key][1], 461, key)

    def test_renderer_facing_uses_last_actual_transition_and_retains_it_when_stationary(self):
        if shutil.which("node") is None:
            self.skipTest("node is required to execute the embedded renderer")
        probe = """
const frame = (position) => ({ state_after: { robot: { position } } });
globalThis.__rendererProbe = {
  east: robotFacing([frame([2, 2]), frame([3, 2])], 1),
  west: robotFacing([frame([2, 2]), frame([1, 2])], 1),
  south: robotFacing([frame([2, 2]), frame([2, 3])], 1),
  north: robotFacing([frame([2, 2]), frame([2, 1])], 1),
  retained: robotFacing([frame([2, 2]), frame([3, 2]), frame([3, 2]), frame([3, 2])], 3),
  initial: robotFacing([frame([2, 2])], 0)
};
"""
        state = execute_renderer_script(self.render(), canonical_projection_bytes(self.projection).hex(), probe)
        self.assertEqual(state["probe"], {"east": "robot_east", "west": "robot_west", "south": "robot_front", "north": "robot_north", "retained": "robot_east", "initial": "robot_front"})

    def test_renderer_keeps_atlas_load_failure_fail_open(self):
        html = self.render()
        self.assertIn('image.onerror = function () { atlasImage = null; atlasReady = false; if (projection !== null) render(); };', html)
        self.assertNotIn("return showUnavailableState();\n  image.onerror", html)

    def test_renderer_uses_compact_dashboard_layout(self):
        html = self.render()
        self.assertIn("/* The 484×480 backing canvas", html)
        self.assertIn(".action-status-strip { display: grid; grid-template-columns: repeat(2", html)
        self.assertIn(".order-list { display: grid; grid-template-columns: repeat(2", html)
        self.assertEqual(html.count('class="order-slot"'), 10)

    def test_renderer_uses_retro_tactical_shell_layout(self):
        html = self.render()

        for marker in (
            "--page: #06111e",
            "--surface: #081724",
            "--surface-raised: #0c1d2c",
            "--frame: #245d83",
            "--frame-bright: #367ca6",
            "--frame-dark: #0a2b45",
            "--ink: #f3f0dc",
            "--muted: #89a4b6",
            "--cyan: #48d9ff",
            "--green: #5bf29b",
            "--amber: #f6c84c",
            "--magenta: #ff4f9f",
            "--danger: #ff6262",
"main { width: min(1120px, calc(100vw - 24px));",
            "grid-template-columns: minmax(0, 1fr) minmax(300px, 340px);",
            ".arena-column { min-width: 0; border-right: 2px solid",
            "image-rendering: pixelated",
            "OFFLINE / SAFE PROJECTION",
            "text-shadow: 2px 2px 0",
            "min-height: 56px",
            "#play { border-color: var(--green);",
            "@media (max-width: 900px)",
            "grid-template-columns: 1fr;",
            "border-bottom: 2px solid var(--frame)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertNotIn("radial-gradient", html)
        self.assertNotIn("animation", html)
        self.assertIn("function render()", html)
        render_body = html[html.index("function render()") : html.index("function pause()")]
        for call in (
            "drawWorld(world, frame);",
            "renderMissionHud(world, frame);",
            "renderOrderQueue(world);",
            "renderRobotHud(world);",
            "renderVerifierHud();",
            "renderReplayMetadata(frame, world);",
        ):
            self.assertIn(call, render_body)

    def test_renderer_keeps_procedural_warehouse_art_and_target_cues(self):
        html = self.render()

        for marker in (
            "function drawFloor",
            "function drawIndustrialTile",
            "function drawShelfSprite",
            "function drawCrateSprite",
            "function drawRobotSprite",
            "function drawPathTrail",
            "const PATH_TRAIL_LENGTH = 6;",
            "function classificationStatus",
            "const CLASSIFICATION_STATUS = Object.freeze({",
            "function actionArgumentText(frame)",
            "function subeventStatus(frame)",
            "function safeTargetForFrame(frame, world)",
            "function drawTargetCue(target)",
            "function moveTarget(position, direction)",
            'north: [0, -1], east: [1, 0], south: [0, 1], west: [-1, 0]',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertIn("drawTargetCue(safeTargetForFrame(frame, world));", html)
        self.assertNotIn("const seenPositions = new Set();", html)
        self.assertIn("if (positionKey === lastPositionKey) continue;", html)
        self.assertIn("positions.unshift(position);", html)

    def test_renderer_keeps_target_cues_after_world_sprites(self):
        html = self.render()
        world_body = html[html.index("function drawWorld"):html.index("function render()")]
        target_index = world_body.index("drawTargetCue(safeTargetForFrame(frame, world));")

        for sprite in ("drawFloor", "drawPathTrail", "drawDepotSprite", "drawShelfSprite", "drawRobotSprite"):
            with self.subTest(sprite=sprite):
                self.assertLess(world_body.index(sprite), target_index)
        self.assertLess(world_body.index("drawFloor();"), world_body.index("drawPathTrail(world, frame);"))
        self.assertLess(world_body.index("drawPathTrail(world, frame);"), world_body.index("drawDepotSprite(world.robot.base_position);"))
        cue = html[html.index("function drawTargetCue"):html.index("const CLASSIFICATION_STATUS")]
        self.assertIn("context.beginPath();", cue)
        self.assertIn("context.lineTo", cue)
        self.assertIn("context.stroke();", cue)
        for forbidden in ("strokeRect", "fillRect", "setLineDash", "drawText(target.label"):
            self.assertNotIn(forbidden, cue)

    def test_renderer_preserves_action_format_and_classification_taxonomy(self):
        html = self.render()

        for marker in (
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
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
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
        self.assertIn("renderStatusClass(frame);", html)

    def test_renderer_separates_fault_and_retry_from_status(self):
        html = self.render()

        for marker in (
            "function subeventStatus(frame)",
            'event.kind === "fault_injected"',
            'event.kind === "internal_retry"',
            'const events = subeventStatus(frame).slice(0, 2);',
            'slot.classList.add(event === null ? "subevent-empty" : event.label === "FAULT" ? "subevent-fault" : "subevent-retry");',
            'id="hud-event-0"',
            'id="hud-event-1"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertIn('id="hud-status"', html)
        status_start = html.index('id="hud-status"')
        status_section = html[status_start : html.index("</section>", status_start)]
        self.assertNotIn("FAULT", status_section)
        self.assertNotIn("RETRY", status_section)

    def test_renderer_details_preserve_normative_replay_fields(self):
        html = self.render()

        for marker in (
            ".arguments",
            "JSON.stringify",
            "position_before",
            "position_after",
            "inventory_before",
            "inventory_after",
            "item_id_before",
            "item_id_after",
            "completed_orders_added",
            ".fault_id",
            ".hook",
            ".fault_kind",
            ".attempt_index",
            ".status",
            ".reason",
            "policy_steps_before",
            "policy_steps_remaining_before",
            "policy_steps_maximum",
            "verifier_version",
            "verifier_status",
            "completed_count",
            "total_count",
            "Verifier-owned score",
            "Verifier-owned outcome",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertNotIn("LOG", html)
        self.assertNotIn("TICK", html)
        # Base64 atlas bytes may coincidentally contain uppercase text fragments.

    def test_renderer_rejects_raw_mapping(self):
        from robot_factory_arena.evidence.visualizer_renderer import render_visualizer

        with self.assertRaises(TypeError):
            render_visualizer(self.projection.to_mapping())


if __name__ == "__main__":
    unittest.main()
