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
:root {
  color-scheme: dark;
  --ink: #f6f7eb;
  --muted: #91a4ad;
  --line: #2d4059;
  --panel: #16232b;
  --panel-raised: #203040;
  --accent: #4cc9f0;
  --warm: #f2c14e;
  --success: #80ed99;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: radial-gradient(circle at 50% -10%, #263b43 0, #101820 48%, #0b1115 100%);
  color: var(--ink);
  font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
main { width: min(640px, calc(100vw - 24px)); margin: 24px auto 40px; }
.visualizer-shell { display: grid; gap: 10px; }
.masthead { padding: 4px 2px 8px; }
.kicker { margin: 0 0 8px; color: var(--accent); font-size: 11px; letter-spacing: .16em; }
h1 { margin: 0; color: var(--ink); font-size: clamp(24px, 6vw, 34px); letter-spacing: -.05em; line-height: 1; }
.subtitle { margin: 10px 0 0; max-width: 56ch; color: var(--muted); }
.scene-frame {
  overflow: hidden;
  border: 1px solid #466071;
  background: #0d151a;
  box-shadow: 0 18px 40px rgba(0, 0, 0, .32), 0 0 0 4px rgba(76, 201, 240, .04);
}
canvas { display: block; width: 100%; height: auto; image-rendering: pixelated; image-rendering: crisp-edges; }
.scene-legend { display: flex; flex-wrap: wrap; gap: 12px; padding: 8px 12px; border-top: 1px solid var(--line); color: var(--muted); font-size: 11px; }
.visualizer-unavailable { padding: 56px 24px; color: var(--warm); font-size: 13px; text-align: center; }
.legend-key { display: inline-flex; align-items: center; gap: 6px; }
.legend-swatch { width: 9px; height: 9px; border: 1px solid rgba(255,255,255,.32); }
.legend-robot { background: var(--accent); }
.legend-shelf { background: #8f5b34; }
.legend-depot { background: #7aa2a8; }
.legend-cargo { background: #f72585; }
.legend-trail { background: #4cc9f0; }
.controls, .status-panel, .terminal-panel, .details-panel {
  border: 1px solid var(--line);
  background: rgba(22, 35, 43, .92);
}
.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px; }
button, select {
  border: 1px solid #466071;
  border-radius: 2px;
  background: #203040;
  color: var(--ink);
  font: inherit;
  padding: 6px 9px;
}
button { cursor: pointer; }
button:hover, button:focus-visible, select:focus-visible { border-color: var(--accent); outline: 2px solid rgba(76, 201, 240, .22); outline-offset: 1px; }
.speed-control { display: inline-flex; align-items: center; gap: 7px; margin-left: auto; color: var(--muted); }
.status-panel, .terminal-panel { padding: 12px; }
.panel-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.panel-heading strong { color: var(--ink); letter-spacing: .04em; }
#status-text, #details-summary { color: var(--muted); font-size: 12px; }
.overview-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 12px; }
.overview-card { min-width: 0; padding: 9px; border: 1px solid #2a3b45; background: #101a20; }
.eyebrow { display: block; color: var(--muted); font-size: 10px; letter-spacing: .12em; text-transform: uppercase; }
.overview-value { display: block; overflow: hidden; margin-top: 4px; color: var(--ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
#mission-progress { color: var(--success); }
.signal-row { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 12px 0 0; color: var(--muted); font-size: 11px; }
.signal-row span::before { content: "•"; margin-right: 5px; color: var(--success); }
.ownership { color: var(--warm); font-size: 10px; letter-spacing: .1em; }
#terminal-text { display: block; margin-top: 8px; color: var(--ink); }
.terminal-note { margin: 8px 0 0; color: var(--muted); font-size: 11px; }
.details-panel { background: rgba(13, 21, 26, .78); }
.details-panel summary { padding: 11px 12px; color: var(--muted); cursor: pointer; list-style-position: inside; }
.details-panel summary:hover, .details-panel summary:focus-visible { color: var(--ink); outline: 1px solid var(--accent); outline-offset: -1px; }
.details-panel[open] summary { border-bottom: 1px solid var(--line); color: var(--ink); }
#frame-details { overflow-x: auto; margin: 0; padding: 12px; color: #bdcbd0; font: 12px/1.7 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; white-space: pre-wrap; }
@media (max-width: 480px) {
  main { width: min(640px, calc(100vw - 16px)); margin-top: 14px; }
  .overview-grid { grid-template-columns: 1fr; }
  .speed-control { margin-left: 0; }
}
</style>
</head>
<body>
<main>
<div class="visualizer-shell">
<header class="masthead">
<p class="kicker">OFFLINE / SAFE PROJECTION</p>
<h1>Mission replay</h1>
<p class="subtitle">Watch the robot work through a deterministic warehouse run. The arena is the primary view; evidence stays available without competing for attention.</p>
</header>
<section class="scene-frame" aria-label="Arena replay">
<canvas id="visualizer-canvas" width="640" height="480" aria-label="2D mission replay arena">Canvas 2D is required.</canvas>
<div id="visualizer-unavailable" class="visualizer-unavailable" role="status" hidden>Presentation unavailable. Embedded projection input is invalid or could not be initialized; no replay data was rendered. This visualizer is non-authoritative.</div>
<div id="scene-legend" class="scene-legend" aria-label="Arena legend">
<span class="legend-key"><i class="legend-swatch legend-robot"></i>robot</span>
<span class="legend-key"><i class="legend-swatch legend-shelf"></i>shelf</span>
<span class="legend-key"><i class="legend-swatch legend-depot"></i>depot</span>
<span class="legend-key"><i class="legend-swatch legend-cargo"></i>inventory</span>
<span class="legend-key"><i class="legend-swatch legend-trail"></i>path trail</span>
</div>
</section>
<div class="controls" aria-label="Timeline controls">
<button id="play" type="button">Play</button><button id="pause" type="button">Pause</button><button id="step-forward" type="button">Step forward</button>
<label class="speed-control">Speed <select id="speed"><option value="0.5">0.5x</option><option value="1" selected>1x</option><option value="2">2x</option><option value="4">4x</option></select></label>
</div>
<section class="status-panel overview-panel" aria-label="Replay overview">
<div class="panel-heading"><strong>Replay overview</strong><span id="status-text"></span></div>
<div class="overview-grid">
<div class="overview-card"><span class="eyebrow">Mission progress</span><strong id="mission-progress" class="overview-value"></strong></div>
<div class="overview-card"><span class="eyebrow">Robot position</span><strong id="robot-status" class="overview-value"></strong></div>
<div class="overview-card"><span class="eyebrow">Inventory</span><strong id="inventory-status" class="overview-value"></strong></div>
</div>
<div class="signal-row"><span>report signal: verification validity</span><span>report signal: replay agreement</span><span>report signal: receipt binding</span><span>report signal: visualizer is non-authoritative</span></div>
</section>
<section class="terminal-panel overview-panel" aria-label="Verifier result">
<div class="panel-heading"><strong>Verifier result</strong><span class="ownership">VERIFIER-OWNED</span></div>
<span id="terminal-text"></span>
<p id="terminal-note" class="terminal-note">Score and outcome are displayed exactly as claimed by the independent verifier. This visualizer does not author them.</p>
</section>
<details id="frame-details-panel" class="details-panel">
<summary>Frame evidence <span id="details-summary"></span></summary>
<pre id="frame-details" aria-live="polite"></pre>
</details>
</div>
</main>
<script id="visualizer-projection-hex" type="application/octet-stream">__PROJECTION_HEX__</script>
<script>
"use strict";
const GRID_SIZE = 10;
const CELL_SIZE = 44;
const ORIGIN_X = 20;
const ORIGIN_Y = 20;
const HUD_X = 484;
const ORDER_SLOT_COUNT = 10;
const ORDER_GRID_COLUMNS = 2;
const ORDER_GRID_ROWS = 5;
const ORDER_GRID_Y = 128;
const ORDER_SLOT_WIDTH = 64;
const ORDER_SLOT_HEIGHT = 28;
const ORDER_SLOT_GAP = 4;
const ROBOT_SECTION_Y = 304;
const VERIFIER_SECTION_Y = 410;
const NOMINAL_FRAME_MS = 500;
const PATH_TRAIL_LENGTH = 6;
const TARGET_CUE_SIZE = 38;
const TARGET_CUE_ARM = 8;
const PALETTE = {
  background: "#101820", floor: "#203040", floorAlt: "#243746",
  floorSeam: "#2f4a55", metal: "#70858b", metalDark: "#3b5158", hazard: "#ffca3a",
  lane: "#6f7d72", laneEdge: "#40554f", grid: "#2d4059", shelf: "#8f5b34",
  shelfDark: "#5e432f", shelfHighlight: "#ba7a46", item: "#f2c14e", crateDark: "#8d6825",
  robot: "#4cc9f0", robotDark: "#166078", carried: "#f72585", depot: "#7aa2a8",
  depotDark: "#35535c", pending: "#f6f7eb", completed: "#80ed99", rejection: "#ffca3a",
  fault: "#ff595e", retry: "#f2c14e", trail: "#4cc9f0", warm: "#f2c14e",
  hud: "#101a20", hudLine: "#38515f", muted: "#91a4ad"
};
let projection = null;
let canvas = null;
let context = null;
let statusText = null;
let missionProgress = null;
let robotStatus = null;
let inventoryStatus = null;
let terminalText = null;
let terminalNote = null;
let frameDetails = null;
let detailsSummary = null;
let speed = null;
let selectedFrame = 0;
let timer = null;

function setPresentationText(element, text) {
  if (element !== null) element.textContent = text;
}
function showUnavailableState() {
  pause();
  if (canvas !== null) {
    canvas.hidden = true;
    if (context !== null) context.clearRect(0, 0, canvas.width, canvas.height);
  }
  const unavailable = document.getElementById("visualizer-unavailable");
  if (unavailable !== null) unavailable.hidden = false;
  const sceneLegend = document.getElementById("scene-legend");
  if (sceneLegend !== null) sceneLegend.hidden = true;
  [
    document.getElementById("play"),
    document.getElementById("pause"),
    document.getElementById("step-forward"),
    document.getElementById("speed")
  ].forEach((control) => { if (control !== null) control.disabled = true; });
  setPresentationText(statusText, "presentation unavailable · embedded projection is invalid or unavailable");
  setPresentationText(missionProgress, "Unavailable");
  setPresentationText(robotStatus, "Unavailable");
  setPresentationText(inventoryStatus, "Unavailable");
  setPresentationText(terminalText, "Verifier-owned result unavailable; visualizer is non-authoritative.");
  setPresentationText(terminalNote, "No verifier-owned score or outcome is available from the unavailable embedded projection.");
  setPresentationText(frameDetails, "No frame evidence is available because the embedded presentation could not be initialized.");
  setPresentationText(detailsSummary, " · unavailable");
}

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
function cellOrigin(position) {
  return { x: ORIGIN_X + position[0] * CELL_SIZE, y: ORIGIN_Y + position[1] * CELL_SIZE };
}
function drawIndustrialTile(column, row) {
  const x = ORIGIN_X + column * CELL_SIZE;
  const y = ORIGIN_Y + row * CELL_SIZE;
  const alternate = (row + column) % 2 === 0;
  context.fillStyle = alternate ? PALETTE.floor : PALETTE.floorAlt;
  context.fillRect(x + 1, y + 1, CELL_SIZE - 2, CELL_SIZE - 2);
  context.fillStyle = alternate ? PALETTE.floorSeam : PALETTE.metalDark;
  context.fillRect(x + 3, y + 3, CELL_SIZE - 6, 2);
  context.fillStyle = PALETTE.metal;
  context.fillRect(x + 5, y + CELL_SIZE - 5, CELL_SIZE - 10, 1);
  context.fillStyle = PALETTE.metalDark;
  context.fillRect(x + 5, y + 7, 2, 2);
  context.fillRect(x + CELL_SIZE - 7, y + 7, 2, 2);
  if ((row * GRID_SIZE + column) % 5 === 0) {
    context.fillStyle = PALETTE.metal;
    context.fillRect(x + 16, y + 16, 12, 2);
    context.fillRect(x + 21, y + 11, 2, 12);
  }
}
function drawHazardStripe(x, y, width, height) {
  context.fillStyle = PALETTE.hazard;
  context.fillRect(x, y, width, height);
  context.fillStyle = PALETTE.metalDark;
  for (let stripe = -height; stripe < width; stripe += 10) {
    context.beginPath();
    context.moveTo(x + stripe, y + height);
    context.lineTo(x + stripe + height, y);
    context.lineTo(x + stripe + height - 3, y);
    context.lineTo(x + stripe - 3, y + height);
    context.fill();
  }
}
function drawFloor() {
  context.fillStyle = PALETTE.background;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = PALETTE.floor;
  context.fillRect(ORIGIN_X, ORIGIN_Y, GRID_SIZE * CELL_SIZE, GRID_SIZE * CELL_SIZE);
  for (let row = 0; row < GRID_SIZE; row += 1) {
    for (let column = 0; column < GRID_SIZE; column += 1) drawIndustrialTile(column, row);
  }
  context.fillStyle = PALETTE.laneEdge;
  for (let row = 2; row < GRID_SIZE; row += 3) {
    const laneY = ORIGIN_Y + row * CELL_SIZE + Math.floor(CELL_SIZE / 2) - 1;
    context.fillRect(ORIGIN_X + 4, laneY, GRID_SIZE * CELL_SIZE - 8, 3);
    context.fillStyle = PALETTE.lane;
    for (let segment = 0; segment < GRID_SIZE; segment += 2) {
      context.fillRect(ORIGIN_X + segment * CELL_SIZE + 9, laneY, CELL_SIZE - 18, 1);
    }
    context.fillStyle = PALETTE.laneEdge;
  }
  drawHazardStripe(ORIGIN_X + 2, ORIGIN_Y + GRID_SIZE * CELL_SIZE - 7, GRID_SIZE * CELL_SIZE - 4, 5);
  context.strokeStyle = PALETTE.grid;
  context.lineWidth = 1;
  for (let line = 0; line <= GRID_SIZE; line += 1) {
    context.beginPath(); context.moveTo(ORIGIN_X + line * CELL_SIZE, ORIGIN_Y); context.lineTo(ORIGIN_X + line * CELL_SIZE, ORIGIN_Y + GRID_SIZE * CELL_SIZE); context.stroke();
    context.beginPath(); context.moveTo(ORIGIN_X, ORIGIN_Y + line * CELL_SIZE); context.lineTo(ORIGIN_X + GRID_SIZE * CELL_SIZE, ORIGIN_Y + line * CELL_SIZE); context.stroke();
  }
  context.font = "10px ui-monospace, monospace";
  for (let index = 0; index < GRID_SIZE; index += 1) {
    drawText(String(index), ORIGIN_X + index * CELL_SIZE + 4, ORIGIN_Y - 6, PALETTE.muted);
    drawText(String(index), ORIGIN_X - 12, ORIGIN_Y + index * CELL_SIZE + 13, PALETTE.muted);
  }
}
function drawCrateSprite(x, y, color, label) {
  context.fillStyle = "rgba(0, 0, 0, .34)";
  context.fillRect(x + 3, y + 4, 21, 20);
  context.fillStyle = PALETTE.crateDark;
  context.fillRect(x + 2, y + 2, 21, 20);
  context.fillStyle = color;
  context.fillRect(x + 4, y + 4, 17, 16);
  context.fillStyle = "rgba(255, 255, 255, .2)";
  context.fillRect(x + 5, y + 5, 15, 2);
  context.fillRect(x + 5, y + 17, 15, 2);
  context.strokeStyle = PALETTE.crateDark;
  context.strokeRect(x + 4.5, y + 4.5, 16, 15);
  context.beginPath(); context.moveTo(x + 5, y + 5); context.lineTo(x + 20, y + 19); context.stroke();
  context.beginPath(); context.moveTo(x + 20, y + 5); context.lineTo(x + 5, y + 19); context.stroke();
  context.fillStyle = PALETTE.pending;
  context.fillRect(x + 2, y + 9, 2, 5);
  context.fillRect(x + 23, y + 9, 2, 5);
  if (label) {
    context.font = "bold 7px ui-monospace, monospace";
    drawText(label, x + 7, y + 15, PALETTE.pending);
  }
}
function drawHazardDepotSprite(position) {
  const origin = cellOrigin(position);
  context.fillStyle = "rgba(0, 0, 0, .34)";
  context.fillRect(origin.x + 5, origin.y + 7, CELL_SIZE - 9, CELL_SIZE - 8);
  context.fillStyle = PALETTE.depotDark;
  context.fillRect(origin.x + 3, origin.y + 3, CELL_SIZE - 6, CELL_SIZE - 6);
  context.fillStyle = PALETTE.depot;
  context.fillRect(origin.x + 7, origin.y + 7, CELL_SIZE - 14, CELL_SIZE - 14);
  drawHazardStripe(origin.x + 8, origin.y + 9, CELL_SIZE - 16, 5);
  context.fillStyle = PALETTE.depotDark;
  context.fillRect(origin.x + 11, origin.y + 18, CELL_SIZE - 22, 3);
  context.fillRect(origin.x + 11, origin.y + 27, CELL_SIZE - 22, 3);
  context.fillStyle = PALETTE.hazard;
  context.fillRect(origin.x + 10, origin.y + 34, 4, 3);
  context.fillRect(origin.x + 30, origin.y + 34, 4, 3);
  context.strokeStyle = PALETTE.pending;
  context.lineWidth = 2;
  context.strokeRect(origin.x + 6.5, origin.y + 6.5, CELL_SIZE - 13, CELL_SIZE - 13);
  context.font = "bold 7px ui-monospace, monospace";
  drawText("DEPOT", origin.x + 9, origin.y + 26, PALETTE.pending);
}
function drawDepotSprite(position) {
  drawHazardDepotSprite(position);
}
function drawShelfSprite(shelf) {
  const origin = cellOrigin(shelf.position);
  context.fillStyle = "rgba(0, 0, 0, .3)";
  context.fillRect(origin.x + 6, origin.y + 8, CELL_SIZE - 10, CELL_SIZE - 8);
  context.fillStyle = PALETTE.shelfDark;
  context.fillRect(origin.x + 4, origin.y + 4, CELL_SIZE - 8, CELL_SIZE - 8);
  context.fillStyle = shelf.item_id === null ? PALETTE.shelfDark : PALETTE.shelf;
  context.fillRect(origin.x + 7, origin.y + 7, CELL_SIZE - 14, CELL_SIZE - 14);
  context.fillStyle = PALETTE.shelfHighlight;
  context.fillRect(origin.x + 8, origin.y + 8, CELL_SIZE - 16, 3);
  context.fillStyle = PALETTE.shelfDark;
  context.fillRect(origin.x + 8, origin.y + 18, CELL_SIZE - 16, 3);
  context.fillRect(origin.x + 8, origin.y + 29, CELL_SIZE - 16, 3);
  context.fillStyle = PALETTE.shelfHighlight;
  context.fillRect(origin.x + 7, origin.y + 7, 3, CELL_SIZE - 14);
  context.fillRect(origin.x + CELL_SIZE - 10, origin.y + 7, 3, CELL_SIZE - 14);
  context.fillStyle = PALETTE.pending;
  context.fillRect(origin.x + 8, origin.y + 9, 2, 2);
  context.fillRect(origin.x + CELL_SIZE - 10, origin.y + 9, 2, 2);
  context.fillRect(origin.x + 8, origin.y + 31, 2, 2);
  context.fillRect(origin.x + CELL_SIZE - 10, origin.y + 31, 2, 2);
  context.strokeStyle = "rgba(255, 255, 255, .2)";
  context.strokeRect(origin.x + 4.5, origin.y + 4.5, CELL_SIZE - 9, CELL_SIZE - 9);
  context.font = "9px ui-monospace, monospace";
  drawText(shelf.shelf_id.slice(-2), origin.x + 7, origin.y + CELL_SIZE - 5, PALETTE.pending);
  if (shelf.item_id !== null) drawCrateSprite(origin.x + 11, origin.y + 10, PALETTE.item, shelf.item_id.slice(-2));
}
function drawRobotSprite(robot) {
  const origin = cellOrigin(robot.position);
  context.fillStyle = "rgba(0, 0, 0, .3)";
  context.fillRect(origin.x + 6, origin.y + 8, CELL_SIZE - 10, CELL_SIZE - 8);
  context.fillStyle = PALETTE.robotDark;
  context.fillRect(origin.x + 8, origin.y + 9, 28, 28);
  context.fillStyle = PALETTE.robot;
  context.fillRect(origin.x + 11, origin.y + 11, 22, 22);
  context.fillStyle = PALETTE.robotDark;
  context.fillRect(origin.x + 14, origin.y + 5, 16, 6);
  context.fillRect(origin.x + 21, origin.y + 1, 2, 5);
  context.fillStyle = PALETTE.hazard;
  context.fillRect(origin.x + 20, origin.y, 4, 3);
  context.fillStyle = PALETTE.robotDark;
  context.fillRect(origin.x + 9, origin.y + 34, 7, 4);
  context.fillRect(origin.x + 29, origin.y + 34, 7, 4);
  context.fillStyle = PALETTE.background;
  context.fillRect(origin.x + 15, origin.y + 17, 5, 5);
  context.fillRect(origin.x + 24, origin.y + 17, 5, 5);
  context.fillStyle = PALETTE.pending;
  context.fillRect(origin.x + 18, origin.y + 27, 9, 3);
  context.fillStyle = PALETTE.robotDark;
  context.fillRect(origin.x + 14, origin.y + 24, 16, 2);
  context.fillStyle = PALETTE.hazard;
  context.fillRect(origin.x + 16, origin.y + 25, 3, 1);
  context.fillRect(origin.x + 25, origin.y + 25, 3, 1);
  context.strokeStyle = PALETTE.pending;
  context.strokeRect(origin.x + 10.5, origin.y + 10.5, 23, 23);
  if (robot.inventory_item_id !== null) drawCrateSprite(origin.x + 12, origin.y - 1, PALETTE.carried, robot.inventory_item_id.slice(-2));
}
function drawMovementPip(position, index, count) {
  const point = cellOrigin(position);
  context.save();
  context.globalAlpha = .2 + (index + 1) / (count * 7);
  context.fillStyle = PALETTE.trail;
  context.fillRect(point.x + 14, point.y + 14, 16, 16);
  context.fillStyle = PALETTE.pending;
  context.fillRect(point.x + 20, point.y + 20, 4, 4);
  context.restore();
}
function drawPathTrail(world, frame) {
  if (frame === null) return;
  const positions = [];
  let lastPositionKey = null;
  for (let index = selectedFrame; index >= 0 && positions.length < PATH_TRAIL_LENGTH; index -= 1) {
    const position = projection.frames[index].state_after.robot.position;
    const positionKey = position.join(",");
    if (positionKey === lastPositionKey) continue;
    lastPositionKey = positionKey;
    positions.unshift(position);
  }
  if (positions.length === 0) return;
  positions.forEach((position, index) => drawMovementPip(position, index, positions.length));
}
function moveTarget(position, direction) {
  const delta = { north: [0, -1], east: [1, 0], south: [0, 1], west: [-1, 0] }[direction];
  if (!delta) return null;
  const target = [position[0] + delta[0], position[1] + delta[1]];
  return target[0] < 0 || target[1] < 0 || target[0] >= GRID_SIZE || target[1] >= GRID_SIZE ? null : target;
}
function safeTargetForFrame(frame, world) {
  const call = frame === null ? null : frame.tool_call;
  if (call === null || call.tool === null || call.arguments === null) return null;
  if (call.tool === "move") {
    const position = moveTarget(frame.state_before.robot.position, call.arguments.direction);
    return position === null ? null : { position: position, label: "MOVE", color: PALETTE.trail };
  }
  if (call.tool === "pick") {
    const shelf = world.shelves.find((candidate) => candidate.shelf_id === call.arguments.shelf_id);
    return shelf === undefined ? null : { position: shelf.position, label: "PICK", color: PALETTE.item };
  }
  if (call.tool === "place") {
    return { position: world.robot.base_position, label: "PLACE", color: PALETTE.completed };
  }
  return null;
}
function drawTargetCue(target) {
  if (target === null) return;
  const point = cellOrigin(target.position);
  const inset = (CELL_SIZE - TARGET_CUE_SIZE) / 2;
  const left = point.x + inset;
  const top = point.y + inset;
  const right = left + TARGET_CUE_SIZE;
  const bottom = top + TARGET_CUE_SIZE;
  context.save();
  context.strokeStyle = target.color;
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(left, top + TARGET_CUE_ARM);
  context.lineTo(left, top);
  context.lineTo(left + TARGET_CUE_ARM, top);
  context.moveTo(right - TARGET_CUE_ARM, top);
  context.lineTo(right, top);
  context.lineTo(right, top + TARGET_CUE_ARM);
  context.moveTo(right, bottom - TARGET_CUE_ARM);
  context.lineTo(right, bottom);
  context.lineTo(right - TARGET_CUE_ARM, bottom);
  context.moveTo(left + TARGET_CUE_ARM, bottom);
  context.lineTo(left, bottom);
  context.lineTo(left, bottom - TARGET_CUE_ARM);
  context.stroke();
  context.restore();
}
const CLASSIFICATION_STATUS = Object.freeze({
  committed: { label: "COMMITTED", color: PALETTE.completed },
  rejected: { label: "REJECTED", color: PALETTE.rejection },
  malformed: { label: "MALFORMED", color: PALETTE.rejection },
  unsupported: { label: "UNSUPPORTED", color: PALETTE.rejection },
  invalid: { label: "INVALID", color: PALETTE.rejection },
  unauthorized: { label: "UNAUTHORIZED", color: PALETTE.rejection },
  idempotent_replay: { label: "IDEMPOTENT REPLAY", color: PALETTE.retry }
});
function classificationStatus(frame) {
  if (frame === null) return { label: "INITIAL", color: PALETTE.muted };
  return CLASSIFICATION_STATUS[frame.classification] || { label: "UNKNOWN", color: PALETTE.muted };
}
function compactActionId(value, prefix) {
  const text = valueText(value, "?");
  const marker = prefix + "-";
  return text.startsWith(marker) ? prefix.charAt(0).toUpperCase() + text.slice(marker.length) : text;
}
function actionArgumentText(frame) {
  if (frame === null || frame.tool_call === null) return "none";
  const call = frame.tool_call;
  if (call.tool === null || call.arguments === null) return valueText(call.tool, "none");
  switch (call.tool) {
    case "move":
      return "MOVE " + valueText(call.arguments.direction, "?").toUpperCase();
    case "pick":
      return "PICK " + compactActionId(call.arguments.shelf_id, "shelf") + " / " + compactActionId(call.arguments.item_id, "item");
    case "place":
      return "PLACE " + compactActionId(call.arguments.order_id, "order");
    default:
      return valueText(call.tool, "none");
  }
}
function normalizedActionText(frame) { return actionArgumentText(frame); }
function subeventStatus(frame) {
  if (frame === null) return [];
  return frame.subevents.map((event) => ({
    label: event.kind === "fault_injected" ? "FAULT" : event.kind === "internal_retry" ? "RETRY" : "SUBEVENT",
    color: event.kind === "fault_injected" ? PALETTE.fault : PALETTE.retry
  }));
}
function drawHudSubevents(frame) {
  subeventStatus(frame).slice(0, 2).forEach((event, index) => {
    const x = HUD_X + 82 + index * 30;
    if (event.label === "FAULT") drawText("FAULT", x, 124, event.color);
    else drawText("RETRY", x, 124, event.color);
  });
}
function drawWorld(world, frame) {
  drawFloor();
  drawPathTrail(world, frame);
  drawDepotSprite(world.robot.base_position);
  for (const shelf of world.shelves) drawShelfSprite(shelf);
  drawRobotSprite(world.robot);
  drawTargetCue(safeTargetForFrame(frame, world));
}
function drawHud(world, frame) {
  const result = projection.terminal_result;
  const completed = world.completed_orders.length;
  const total = world.orders.length;
  const status = classificationStatus(frame);
  context.fillStyle = PALETTE.hud;
  context.fillRect(HUD_X, 0, canvas.width - HUD_X, canvas.height);
  context.strokeStyle = PALETTE.hudLine;
  context.beginPath(); context.moveTo(HUD_X, 0); context.lineTo(HUD_X, canvas.height); context.stroke();
  context.font = "bold 11px ui-monospace, monospace";
  drawText("MISSION", HUD_X + 12, 28, PALETTE.robot);
  context.font = "bold 24px ui-monospace, monospace";
  drawText(completed + "/" + total, HUD_X + 12, 59, PALETTE.completed);
  context.font = "10px ui-monospace, monospace";
  drawText("ORDERS COMPLETE", HUD_X + 14, 76, PALETTE.muted);
  context.fillStyle = PALETTE.hudLine;
  context.fillRect(HUD_X + 12, 86, 132, 4);
  context.fillStyle = PALETTE.completed;
  context.fillRect(HUD_X + 12, 86, total ? 132 * completed / total : 0, 4);
  context.font = "bold 7px ui-monospace, monospace";
  drawText("ACTION", HUD_X + 12, 100, PALETTE.warm);
  drawText("STATUS", HUD_X + 82, 100, status.color);
  context.font = "6px ui-monospace, monospace";
  drawText(actionArgumentText(frame), HUD_X + 12, 112, PALETTE.pending);
  drawText(status.label, HUD_X + 82, 112, status.color);
  drawHudSubevents(frame);
  drawText("ORDER QUEUE", HUD_X + 12, 124, PALETTE.warm);
  context.font = "8px ui-monospace, monospace";
  for (let index = 0; index < ORDER_SLOT_COUNT; index += 1) {
    const column = index % ORDER_GRID_COLUMNS;
    const row = Math.floor(index / ORDER_GRID_COLUMNS);
    const slotX = HUD_X + 12 + column * (ORDER_SLOT_WIDTH + ORDER_SLOT_GAP);
    const slotY = ORDER_GRID_Y + row * (ORDER_SLOT_HEIGHT + ORDER_SLOT_GAP);
    const order = world.orders[index] || null;
    context.fillStyle = PALETTE.background;
    context.fillRect(slotX, slotY, ORDER_SLOT_WIDTH, ORDER_SLOT_HEIGHT);
    context.strokeStyle = PALETTE.hudLine;
    context.strokeRect(slotX + 0.5, slotY + 0.5, ORDER_SLOT_WIDTH - 1, ORDER_SLOT_HEIGHT - 1);
    if (order === null) {
      drawText("—", slotX + 8, slotY + 18, PALETTE.muted);
      continue;
    }
    const done = world.completed_orders.includes(order.order_id);
    context.fillStyle = done ? PALETTE.completed : PALETTE.item;
    context.fillRect(slotX + 5, slotY + 7, 6, 6);
    drawText(order.order_id, slotX + 15, slotY + 12, done ? PALETTE.completed : PALETTE.pending);
    drawText(order.item_id + " x" + order.quantity, slotX + 15, slotY + 23, PALETTE.muted);
  }
  context.strokeStyle = PALETTE.hudLine;
  context.beginPath(); context.moveTo(HUD_X + 12, ROBOT_SECTION_Y); context.lineTo(canvas.width - 12, ROBOT_SECTION_Y); context.stroke();
  const robot = world.robot;
  context.font = "10px ui-monospace, monospace";
  drawText("ROBOT", HUD_X + 12, ROBOT_SECTION_Y + 23, PALETTE.robot);
  drawText("POS  " + robot.position.join(", "), HUD_X + 12, ROBOT_SECTION_Y + 42, PALETTE.pending);
  drawText("LOAD " + valueText(robot.inventory_item_id, "empty"), HUD_X + 12, ROBOT_SECTION_Y + 61, robot.inventory_item_id === null ? PALETTE.muted : PALETTE.carried);
  drawText("VERIFIER-OWNED", HUD_X + 12, VERIFIER_SECTION_Y, PALETTE.warm);
  drawText("score " + result.verified_score, HUD_X + 12, VERIFIER_SECTION_Y + 19, PALETTE.pending);
  drawText("outcome " + result.outcome, HUD_X + 12, VERIFIER_SECTION_Y + 38, PALETTE.pending);
}
function detailLines(frame, world) {
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
  drawWorld(world, frame);
  drawHud(world, frame);
  const result = projection.terminal_result;
  const status = classificationStatus(frame);
  statusText.textContent = "frame " + (frame ? frame.policy_step : "initial") + " · action " + normalizedActionText(frame) + " · status " + status.label;
  missionProgress.textContent = world.completed_orders.length + " / " + world.orders.length + " orders";
  robotStatus.textContent = "(" + world.robot.position.join(", ") + ")";
  inventoryStatus.textContent = valueText(world.robot.inventory_item_id, "empty");
  terminalText.textContent = "Verifier-owned score: " + result.verified_score + "; Verifier-owned outcome: " + result.outcome + "; authority: " + result.outcome_authority + "; verifier version " + result.verifier_version + " status " + result.verifier_status + "; completed " + result.completed_count + "/" + result.total_count + ".";
  frameDetails.textContent = detailLines(frame, world).join("\\n");
  detailsSummary.textContent = " · " + (frame ? "frame " + frame.policy_step + " / " + projection.frames.length : "initial world");
}
function pause() { if (timer !== null) { clearInterval(timer); timer = null; } }
function stepForward() { if (projection.frames.length) selectedFrame = (selectedFrame + 1) % projection.frames.length; render(); }
function play() { pause(); timer = setInterval(stepForward, NOMINAL_FRAME_MS / Number(speed.value)); }
function initializePresentation() {
  try {
    canvas = document.getElementById("visualizer-canvas");
    statusText = document.getElementById("status-text");
    missionProgress = document.getElementById("mission-progress");
    robotStatus = document.getElementById("robot-status");
    inventoryStatus = document.getElementById("inventory-status");
    terminalText = document.getElementById("terminal-text");
    terminalNote = document.getElementById("terminal-note");
    frameDetails = document.getElementById("frame-details");
    detailsSummary = document.getElementById("details-summary");
    speed = document.getElementById("speed");
    if (canvas === null || typeof canvas.getContext !== "function") return showUnavailableState();
    context = canvas.getContext("2d");
    if (context === null) return showUnavailableState();

    const embeddedProjection = document.getElementById("visualizer-projection-hex");
    if (embeddedProjection === null || typeof embeddedProjection.textContent !== "string") return showUnavailableState();
    const hex = embeddedProjection.textContent;
    if (hex.length % 2 !== 0 || !/^[0-9a-f]*$/i.test(hex)) return showUnavailableState();
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) bytes[i / 2] = Number.parseInt(hex.slice(i, i + 2), 16);
    projection = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (projection === null || typeof projection !== "object") return showUnavailableState();

    document.getElementById("play").addEventListener("click", play);
    document.getElementById("pause").addEventListener("click", pause);
    document.getElementById("step-forward").addEventListener("click", stepForward);
    speed.addEventListener("change", function () { if (timer !== null) play(); });
    render();
  } catch (_error) {
    showUnavailableState();
  }
}
initializePresentation();
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
