"""Deterministic, projection-only offline Canvas visualizer renderer."""

from robot_factory_arena.evidence.visualizer_projection import (
  VisualizerProjection,
  canonical_projection_bytes,
)

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robot Run Visualizer</title>
<style>
:root { color-scheme: dark; }
body { margin: 0; background: #101820; color: #f6f7eb; font: 14px monospace; }
main { width: 640px; margin: 16px auto; }
canvas { display: block; image-rendering: pixelated; image-rendering: crisp-edges; }
.controls, .status-panel, .terminal-panel { margin-top: 8px; padding: 8px; border: 1px solid #2d4059; background: #203040; }
button, select { font: inherit; margin-right: 6px; }
</style>
</head>
<body>
<main>
<canvas id="visualizer-canvas" width="640" height="480">Canvas 2D is required.</canvas>
<div class="controls" aria-label="Timeline controls">
<button id="play" type="button">Play</button><button id="pause" type="button">Pause</button><button id="step-forward" type="button">Step forward</button>
<label>Speed <select id="speed"><option value="0.5">0.5x</option><option value="1" selected>1x</option><option value="2">2x</option><option value="4">4x</option></select></label>
</div>
<section class="status-panel"><strong>Status panel</strong><span id="status-text"></span><br>report signal: verification validity<br>report signal: replay agreement<br>report signal: receipt binding<br>report signal: visualizer is non-authoritative</section>
<section class="terminal-panel"><strong>Terminal panel</strong><span id="terminal-text"></span><br>Verifier-owned score and Verifier-owned outcome are displayed exactly as projection claims.</section>
</main>
<script id="visualizer-projection-hex" type="application/octet-stream">__PROJECTION_HEX__</script>
<script>
"use strict";
const GRID_SIZE = 10;
const CELL_SIZE = 40;
const ORIGIN_X = 16;
const ORIGIN_Y = 16;
const NOMINAL_FRAME_MS = 500;
const PALETTE = {
  background: "#101820", floor: "#203040", grid: "#2d4059", shelf: "#8f5b34",
  emptyShelf: "#5e432f", item: "#f2c14e", robot: "#4cc9f0", carried: "#f72585",
  pending: "#f6f7eb", completed: "#80ed99", rejection: "#ffca3a", fault: "#ff595e"
};
const hex = document.getElementById("visualizer-projection-hex").textContent;
const bytes = new Uint8Array(hex.length / 2);
for (let i = 0; i < hex.length; i += 2) bytes[i / 2] = Number.parseInt(hex.slice(i, i + 2), 16);
const projection = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
const canvas = document.getElementById("visualizer-canvas");
const context = canvas.getContext("2d");
const statusText = document.getElementById("status-text");
const terminalText = document.getElementById("terminal-text");
const speed = document.getElementById("speed");
let selectedFrame = 0;
let timer = null;

    function valueText(value, fallback) { return value === null || value === "" ? fallback : String(value); }
    function argumentsText(callArguments) { return callArguments === null ? "n/a" : JSON.stringify(callArguments); }
    function deltaDetail(delta) {
      const shelves = delta.shelves.map((shelf) => shelf.shelf_id + " item " + valueText(shelf.item_id_before, "empty") + " -> " + valueText(shelf.item_id_after, "empty")).join("; ");
      return "robot " + delta.robot.position_before.join(",") + " -> " + delta.robot.position_after.join(",") + " inventory " + valueText(delta.robot.inventory_before, "none") + " -> " + valueText(delta.robot.inventory_after, "none") + "; shelves " + (shelves || "none") + "; orders added " + (delta.completed_orders_added.join(",") || "none");
    }
    function subeventText(event) {
      const fault = event.fault_id === null ? "" : " fault " + event.fault_id + " hook " + event.hook + " kind " + event.fault_kind;
      return event.kind + " attempt " + event.attempt_index + " status " + event.status + " reason " + valueText(event.reason, "none") + fault;
    }
    function worldForFrame() { return projection.frames.length ? projection.frames[selectedFrame].state_after : projection.initial_world; }
function drawText(text, x, y, color) { context.fillStyle = color || PALETTE.pending; context.fillText(String(text), x, y); }
function drawWorld(world) {
  context.fillStyle = PALETTE.background;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = PALETTE.floor;
  context.fillRect(ORIGIN_X, ORIGIN_Y, GRID_SIZE * CELL_SIZE, GRID_SIZE * CELL_SIZE);
  context.strokeStyle = PALETTE.grid;
  for (let line = 0; line <= GRID_SIZE; line += 1) {
    context.beginPath(); context.moveTo(ORIGIN_X + line * CELL_SIZE, ORIGIN_Y); context.lineTo(ORIGIN_X + line * CELL_SIZE, ORIGIN_Y + GRID_SIZE * CELL_SIZE); context.stroke();
    context.beginPath(); context.moveTo(ORIGIN_X, ORIGIN_Y + line * CELL_SIZE); context.lineTo(ORIGIN_X + GRID_SIZE * CELL_SIZE, ORIGIN_Y + line * CELL_SIZE); context.stroke();
  }
  for (const shelf of world.shelves) {
    const x = ORIGIN_X + shelf.position[0] * CELL_SIZE;
    const y = ORIGIN_Y + shelf.position[1] * CELL_SIZE;
    context.fillStyle = shelf.item_id === null ? PALETTE.emptyShelf : PALETTE.shelf;
    context.fillRect(x + 4, y + 4, CELL_SIZE - 8, CELL_SIZE - 8);
    if (shelf.item_id !== null) { context.fillStyle = PALETTE.item; context.fillRect(x + 14, y + 14, 12, 12); }
  }
  const robot = world.robot;
  context.fillStyle = PALETTE.robot;
  context.fillRect(ORIGIN_X + robot.position[0] * CELL_SIZE + 9, ORIGIN_Y + robot.position[1] * CELL_SIZE + 9, 22, 22);
  if (robot.inventory_item_id !== null) { context.fillStyle = PALETTE.carried; context.fillRect(ORIGIN_X + robot.position[0] * CELL_SIZE + 14, ORIGIN_Y + robot.position[1] * CELL_SIZE + 2, 12, 8); }
}
function panelLines(frame, world) {
  const counters = frame ? frame.counters.after : projection.counters;
  const budget = frame ? frame.observation : { policy_steps_before: 0, policy_steps_remaining_before: projection.logical_run.budget.maximum, policy_steps_maximum: projection.logical_run.budget.maximum };
  const call = frame ? frame.tool_call : { tool: null, arguments: null, operation_key: null };
  const delta = frame ? frame.state_delta : { changed: false, step_before: world.step, step_after: world.step, shelves: [], completed_orders_added: [] };
  return [
    "frame " + (frame ? String(selectedFrame + 1) + "/" + projection.frames.length : "initial"),
    "policy budget " + budget.policy_steps_before + "/" + budget.policy_steps_maximum + " remaining " + budget.policy_steps_remaining_before,
    "call " + valueText(call.tool, "n/a") + " args " + argumentsText(call.arguments) + " key " + valueText(call.operation_key, "n/a"),
    "classification " + valueText(frame && frame.classification, "initial") + " reason " + valueText(frame && frame.reason, "none"),
    "state delta " + (delta.changed ? "changed" : "no change") + " step " + delta.step_before + " -> " + delta.step_after,
    "delta detail " + (frame ? deltaDetail(delta) : "initial world"),
    "robot " + world.robot.position.join(",") + " inventory " + valueText(world.robot.inventory_item_id, "none"),
    "orders pending " + (world.orders.filter((order) => !world.completed_orders.includes(order.order_id)).map((order) => order.order_id).join(",") || "none"),
    "orders completed " + (world.completed_orders.join(",") || "none"),
    "policy_steps " + counters.policy_steps, "tool_attempts " + counters.tool_attempts,
    "committed_effects " + counters.committed_effects, "internal_retries " + counters.internal_retries,
    "contract_violations " + counters.contract_violations, "terminal_checks " + counters.terminal_checks,
    "committed events " + (frame ? frame.committed_domain_events.map((event) => event.event_type).join(",") || "none" : "none"),
    "fault/retry subevents " + (frame ? frame.subevents.map(subeventText).join(" | ") || "none" : "none")
  ];
}
function render() {
  const frame = projection.frames.length ? projection.frames[selectedFrame] : null;
  const world = worldForFrame();
  drawWorld(world);
  context.font = "12px monospace";
  const lines = panelLines(frame, world);
  for (let index = 0; index < lines.length && index < 23; index += 1) drawText(lines[index], 432, 24 + index * 19, index === 3 && frame && frame.classification !== "committed" ? PALETTE.rejection : PALETTE.pending);
  statusText.textContent = " frame " + (frame ? frame.policy_step : "initial") + "; status " + valueText(frame && frame.classification, "initial") + ".";
  const result = projection.terminal_result;
  terminalText.textContent = " Verifier-owned score: " + result.verified_score + "; Verifier-owned outcome: " + result.outcome + "; authority: " + result.outcome_authority + "; verifier version " + result.verifier_version + " status " + result.verifier_status + "; completed " + result.completed_count + "/" + result.total_count + ".";
}
function pause() { if (timer !== null) { clearInterval(timer); timer = null; } }
function stepForward() { if (projection.frames.length) selectedFrame = (selectedFrame + 1) % projection.frames.length; render(); }
function play() { pause(); timer = setInterval(stepForward, NOMINAL_FRAME_MS / Number(speed.value)); }
document.getElementById("play").addEventListener("click", play);
document.getElementById("pause").addEventListener("click", pause);
document.getElementById("step-forward").addEventListener("click", stepForward);
speed.addEventListener("change", function () { if (timer !== null) play(); });
render();
</script>
</body>
</html>
"""


def render_visualizer(projection: VisualizerProjection) -> bytes:
  """Return deterministic self-contained HTML for one validated projection."""
  if not isinstance(projection, VisualizerProjection):
    raise TypeError("projection must be a VisualizerProjection")
  return _TEMPLATE.replace(
    "__PROJECTION_HEX__", canonical_projection_bytes(projection).hex()
  ).encode("utf-8")
