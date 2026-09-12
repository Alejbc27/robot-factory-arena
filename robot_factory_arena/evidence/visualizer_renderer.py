"""Deterministic, projection-only offline Canvas visualizer renderer."""

import base64
from importlib import resources

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
  --page: #06111e;
  --surface: #081724;
  --surface-raised: #0c1d2c;
  --frame: #245d83;
  --frame-bright: #367ca6;
  --frame-dark: #0a2b45;
  --ink: #f3f0dc;
  --muted: #89a4b6;
  --cyan: #48d9ff;
  --green: #5bf29b;
  --amber: #f6c84c;
  --magenta: #ff4f9f;
  --danger: #ff6262;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--page);
  color: var(--ink);
  font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
/* The 484×480 backing canvas keeps the 10×10 world at 44px cells plus 20px margins. */
main { width: min(1120px, calc(100vw - 24px)); margin: 24px auto 40px; }
.visualizer-shell {
  overflow: hidden;
  border: 2px solid var(--frame);
  background: var(--surface);
  box-shadow: inset 0 0 0 2px var(--frame-dark), 0 10px 0 var(--frame-dark);
}
.masthead {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: end;
  padding: 22px 24px 20px;
  border-bottom: 2px solid var(--frame);
  background: var(--surface-raised);
  box-shadow: inset 0 -2px 0 var(--frame-dark);
}
.kicker {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--cyan);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .2em;
}
h1 {
  margin: 0;
  color: var(--ink);
  font-size: clamp(28px, 4vw, 48px);
  letter-spacing: -.07em;
  line-height: 1;
  text-shadow: 2px 2px 0 var(--frame-dark);
}
.subtitle { max-width: 54ch; margin: 0; color: var(--muted); text-align: right; }
.replay-stage {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 340px);
  min-width: 0;
}
.arena-column { min-width: 0; border-right: 2px solid var(--frame); background: var(--page); }
.scene-frame { overflow: hidden; background: var(--page); }
canvas {
  display: block;
  width: 100%;
  height: auto;
  image-rendering: pixelated;
  image-rendering: crisp-edges;
}
.visualizer-unavailable {
  padding: 56px 24px;
  color: var(--amber);
  font-size: 13px;
  text-align: center;
}
.scene-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  padding: 11px 16px;
  border-top: 2px solid var(--frame);
  background: var(--surface-raised);
  color: var(--muted);
  font-size: 11px;
}
.legend-key { display: inline-flex; align-items: center; gap: 7px; }
.legend-swatch { width: 10px; height: 10px; border: 2px solid var(--frame-bright); }
.legend-robot { background: var(--cyan); }
.legend-shelf { background: #8f5b34; }
.legend-depot { background: #7aa2a8; }
.legend-cargo { background: var(--magenta); }
.legend-trail { background: var(--cyan); }
.mission-hud {
  min-width: 0;
  padding: 16px;
  background: var(--surface);
  box-shadow: inset 2px 0 0 var(--frame-dark);
}
.hud-section {
  padding: 14px 0;
  border-bottom: 2px solid var(--frame-dark);
}
.hud-section:first-child { padding-top: 0; }
.hud-section:last-child { border-bottom: 0; padding-bottom: 0; }
.hud-heading {
  margin: 0 0 10px;
  color: var(--cyan);
  font-size: 11px;
  letter-spacing: .16em;
}
.hud-completed {
  color: var(--green);
  font-size: clamp(30px, 4vw, 42px);
  font-weight: 700;
  letter-spacing: -.08em;
  line-height: 1;
  text-shadow: 2px 2px 0 var(--frame-dark);
}
.progress-track {
  height: 12px;
  margin-top: 12px;
  border: 2px solid var(--frame);
  background: var(--frame-dark);
  box-shadow: inset 0 0 0 2px var(--page);
}
#hud-progress-bar { width: 0; height: 100%; background: var(--green); }
.action-status-strip { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 10px; align-items: center; }
.action-status-strip .hud-heading { grid-column: 1 / -1; margin-bottom: 3px; }
.action-status-pair { min-width: 0; }
.hud-label { color: var(--amber); font-size: 10px; font-weight: 700; letter-spacing: .12em; }
.hud-value { min-width: 0; overflow: hidden; color: var(--ink); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
#hud-action { color: var(--ink); }
#hud-status { font-weight: 700; }
.status-initial, .status-unknown { color: var(--muted); }
.status-committed { color: var(--green); }
.status-rejected, .status-malformed, .status-unsupported, .status-invalid, .status-unauthorized { color: var(--danger); }
.status-idempotent_replay { color: var(--amber); }
.subevent-slots { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.subevent-slot {
  min-height: 34px;
  padding: 8px;
  border: 2px solid var(--frame);
  background: var(--surface-raised);
  color: var(--muted);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .1em;
}
.subevent-fault { border-color: var(--danger); color: var(--danger); }
.subevent-retry { border-color: var(--amber); color: var(--amber); }
.order-queue { display: grid; gap: 8px; }
.order-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.order-slot {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr) minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  min-height: 32px;
  padding: 6px 8px;
  border: 2px solid var(--frame-dark);
  background: var(--page);
  box-shadow: inset 0 0 0 1px var(--frame);
  font-size: 10px;
}
.order-state { color: var(--amber); font-weight: 700; }
.order-id { overflow: hidden; color: var(--ink); text-overflow: ellipsis; white-space: nowrap; }
.order-item { overflow: hidden; color: var(--muted); text-overflow: ellipsis; white-space: nowrap; }
.order-slot.is-complete { border-color: var(--green); }
.order-slot.is-complete .order-state { color: var(--green); }
.order-slot.is-empty { opacity: .55; }
.robot-readout { display: grid; gap: 8px; }
.readout-line { display: flex; justify-content: space-between; gap: 12px; }
.readout-label { color: var(--muted); font-size: 10px; letter-spacing: .12em; }
.readout-value { overflow: hidden; color: var(--ink); text-overflow: ellipsis; white-space: nowrap; }
#hud-robot-load:not(.is-empty) { color: var(--magenta); }
.verifier-readout { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.verifier-card { padding: 9px; border: 2px solid var(--frame); background: var(--surface-raised); }
.verifier-card .readout-label { display: block; }
#hud-score, #hud-outcome { display: block; margin-top: 5px; color: var(--ink); font-size: 12px; font-weight: 700; }
.controls {
  display: grid;
  grid-template-columns: repeat(3, minmax(120px, 1fr)) minmax(150px, auto);
  gap: 10px;
  padding: 14px 16px;
  border-top: 2px solid var(--frame);
  background: var(--surface-raised);
  box-shadow: inset 0 2px 0 var(--frame-dark);
}
button, select {
  min-height: 56px;
  border: 2px solid var(--frame);
  border-radius: 0;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  padding: 8px 12px;
  box-shadow: inset 0 0 0 2px var(--frame-dark);
}
button { cursor: pointer; }
button:hover, button:focus-visible, select:focus-visible { border-color: var(--cyan); outline: 2px solid var(--cyan); outline-offset: 2px; }
#play { border-color: var(--green); color: var(--green); }
.speed-control { display: grid; align-content: center; gap: 5px; color: var(--muted); font-size: 11px; letter-spacing: .12em; text-transform: uppercase; }
.speed-control select { width: 100%; min-height: 42px; padding: 6px 9px; }
.details-panel {
  border-top: 2px solid var(--frame);
  background: var(--page);
  box-shadow: inset 0 2px 0 var(--frame-dark);
}
.details-panel summary {
  padding: 14px 16px;
  color: var(--muted);
  cursor: pointer;
  list-style-position: inside;
}
.details-panel summary:hover, .details-panel summary:focus-visible { color: var(--ink); outline: 2px solid var(--cyan); outline-offset: -2px; }
.details-panel[open] summary { border-bottom: 2px solid var(--frame); color: var(--ink); }
.details-body { display: grid; gap: 12px; padding: 14px 16px 18px; }
.details-label { color: var(--amber); font-size: 10px; font-weight: 700; letter-spacing: .14em; }
#status-text, #terminal-text { display: block; color: var(--muted); font-size: 12px; }
#terminal-text { color: var(--ink); }
#frame-details {
  overflow-x: auto;
  margin: 0;
  padding: 12px;
  border: 2px solid var(--frame-dark);
  background: var(--surface);
  color: var(--muted);
  font: 12px/1.7 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  white-space: pre-wrap;
}
@media (max-width: 900px) {
  main { width: min(720px, calc(100vw - 16px)); margin-top: 14px; }
  .masthead { grid-template-columns: 1fr; align-items: start; }
  .subtitle { text-align: left; }
  .replay-stage { grid-template-columns: 1fr; }
  .arena-column { border-right: 0; border-bottom: 2px solid var(--frame); }
  .mission-hud { box-shadow: inset 0 2px 0 var(--frame-dark); }
}
@media (max-width: 560px) {
  .controls { grid-template-columns: 1fr 1fr; }
  .speed-control { grid-column: 1 / -1; }
  .order-slot { grid-template-columns: 54px minmax(0, 1fr); }
  .order-item { grid-column: 2; }
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
<div class="replay-stage">
<section class="arena-column" aria-label="Arena replay">
<div class="scene-frame">
<canvas id="visualizer-canvas" width="484" height="480" aria-label="2D mission replay arena">Canvas 2D is required.</canvas>
<div id="visualizer-unavailable" class="visualizer-unavailable" role="status" hidden>Presentation unavailable. Embedded projection input is invalid or could not be initialized; no replay data was rendered. This visualizer is non-authoritative.</div>
</div>
<div class="scene-legend" aria-label="Arena legend">
<span class="legend-key"><i class="legend-swatch legend-robot"></i>robot</span>
<span class="legend-key"><i class="legend-swatch legend-shelf"></i>shelf</span>
<span class="legend-key"><i class="legend-swatch legend-depot"></i>depot</span>
<span class="legend-key"><i class="legend-swatch legend-cargo"></i>inventory</span>
<span class="legend-key"><i class="legend-swatch legend-trail"></i>path trail</span>
</div>
</section>
<aside class="mission-hud" aria-label="Mission telemetry">
<section class="hud-section mission-summary">
<h2 class="hud-heading">MISSION</h2>
<strong id="hud-completed" class="hud-completed"></strong>
<div class="progress-track" aria-label="Mission progress"><div id="hud-progress-bar"></div></div>
</section>
<section class="hud-section action-status-strip">
<h2 class="hud-heading">ACTION / STATUS</h2>
<div class="action-status-pair"><span class="hud-label">ACTION</span><strong id="hud-action" class="hud-value"></strong></div>
<div class="action-status-pair"><span class="hud-label">STATUS</span><strong id="hud-status" class="hud-value"></strong></div>
</section>
<section class="hud-section">
<h2 class="hud-heading">FAULT / RETRY</h2>
<div id="hud-subevents" class="subevent-slots" aria-live="polite">
<span id="hud-event-0" class="subevent-slot">—</span>
<span id="hud-event-1" class="subevent-slot">—</span>
</div>
</section>
<section class="hud-section order-queue">
<h2 class="hud-heading">ORDER QUEUE</h2>
<div class="order-list">
<div class="order-slot" data-order-index="0"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
<div class="order-slot" data-order-index="1"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
<div class="order-slot" data-order-index="2"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
<div class="order-slot" data-order-index="3"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
<div class="order-slot" data-order-index="4"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
<div class="order-slot" data-order-index="5"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
<div class="order-slot" data-order-index="6"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
<div class="order-slot" data-order-index="7"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
<div class="order-slot" data-order-index="8"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
<div class="order-slot" data-order-index="9"><span class="order-state">—</span><span class="order-id">—</span><span class="order-item">—</span></div>
</div>
</section>
<section class="hud-section">
<h2 class="hud-heading">ROBOT</h2>
<div class="robot-readout">
<div class="readout-line"><span class="readout-label">POS</span><strong id="hud-robot-position" class="readout-value"></strong></div>
<div class="readout-line"><span class="readout-label">LOAD</span><strong id="hud-robot-load" class="readout-value"></strong></div>
</div>
</section>
<section class="hud-section">
<h2 class="hud-heading">VERIFIER-OWNED</h2>
<div class="verifier-readout">
<div class="verifier-card"><span class="readout-label">SCORE</span><strong id="hud-score"></strong></div>
<div class="verifier-card"><span class="readout-label">OUTCOME</span><strong id="hud-outcome"></strong></div>
</div>
</section>
</aside>
</div>
<div class="controls" aria-label="Timeline controls">
<button id="play" class="control-play" type="button">Play</button><button id="pause" type="button">Pause</button><button id="step-forward" type="button">Step forward</button>
<label class="speed-control">Speed <select id="speed"><option value="0.5">0.5x</option><option value="1" selected>1x</option><option value="2">2x</option><option value="4">4x</option></select></label>
</div>
<details id="frame-details-panel" class="details-panel">
<summary>Evidence / technical replay details <span id="details-summary"></span></summary>
<div class="details-body">
<div><span class="details-label">REPLAY STATUS</span><span id="status-text"></span></div>
<div><span class="details-label">VERIFIER RESULT</span><span id="terminal-text"></span></div>
<div><span class="details-label">FRAME DETAILS</span><pre id="frame-details" aria-live="polite"></pre></div>
</div>
</details>
</div>
</main>
<script id="visualizer-projection-hex" type="application/octet-stream">__PROJECTION_HEX__</script>
<script>
"use strict";
const ATLAS_DATA_URI = "__ATLAS_DATA_URI__";
const ATLAS_RECTS = Object.freeze({
  robot_front: [0, 0, 112, 123], robot_east: [112, 0, 112, 108],
  robot_north: [224, 0, 112, 108], robot_west: [337, 0, 113, 108],
  depot: [450, 0, 169, 157],
  rack_0: [0, 157, 154, 150], rack_1: [154, 157, 153, 150], rack_2: [307, 157, 154, 150],
  crate_0: [461, 157, 97, 97], crate_1: [558, 157, 97, 97], crate_2: [655, 157, 97, 97], crate_3: [461, 255, 97, 97],
  floor_0: [0, 337, 139, 135], floor_1: [139, 337, 139, 135], floor_2: [277, 337, 139, 135]
});
const GRID_SIZE = 10;
const CELL_SIZE = 44;
const ORIGIN_X = 20;
const ORIGIN_Y = 20;
const GRID_RECT = Object.freeze({ x: ORIGIN_X, y: ORIGIN_Y, width: GRID_SIZE * CELL_SIZE, height: GRID_SIZE * CELL_SIZE });
const ORDER_SLOT_COUNT = 10;
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
let terminalText = null;
let frameDetails = null;
let detailsSummary = null;
let hudCompleted = null;
let hudProgressBar = null;
let hudAction = null;
let hudStatus = null;
let hudRobotPosition = null;
let hudRobotLoad = null;
let hudScore = null;
let hudOutcome = null;
let speed = null;
let timer = null;
let selectedFrame = 0;
let atlasImage = null;
let atlasReady = false;

function query(selector) { return document.querySelector(selector); }
function setPresentationText(element, text) {
  if (element !== null) element.textContent = text;
}
function showUnavailableState() {
  pause();
  if (canvas !== null) {
    canvas.hidden = true;
    if (context !== null) context.clearRect(0, 0, canvas.width, canvas.height);
  }
  const unavailable = query("#visualizer-unavailable");
  if (unavailable !== null) unavailable.hidden = false;
  const sceneLegend = query(".scene-legend");
  if (sceneLegend !== null) sceneLegend.hidden = true;
  ["#play", "#pause", "#step-forward", "#speed"].forEach((selector) => {
    const control = query(selector);
    if (control !== null) {
      control.disabled = true;
      if (control.classList) control.classList.add("is-disabled");
    }
  });
  setPresentationText(statusText, "presentation unavailable · embedded projection is invalid or unavailable");
  setPresentationText(terminalText, "Verifier-owned result unavailable; visualizer is non-authoritative.");
  setPresentationText(hudCompleted, "Unavailable");
  setPresentationText(hudRobotPosition, "Unavailable");
  setPresentationText(hudRobotLoad, "Unavailable");
  setPresentationText(hudScore, "Unavailable");
  setPresentationText(hudOutcome, "Unavailable");
  setPresentationText(hudAction, "Unavailable");
  setPresentationText(hudStatus, "UNAVAILABLE");
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
function tileRect(position) {
  const origin = cellOrigin(position);
  return { x: origin.x, y: origin.y, width: CELL_SIZE, height: CELL_SIZE };
}
function bottomCenteredRect(tile, width, height) {
  return { x: tile.x + Math.floor((tile.width - width) / 2), y: tile.y + tile.height - height, width: width, height: height };
}
function rackDestination(position) { return bottomCenteredRect(tileRect(position), 38, 40); }
function depotDestination(position) { return bottomCenteredRect(tileRect(position), 40, 40); }
function robotDestination(position) { return bottomCenteredRect(tileRect(position), 36, 38); }
function rackCargoDestination(destination) {
  return { x: destination.x + 7, y: destination.y + 9, width: 24, height: 24 };
}
function robotCargoDestination(destination) {
  return { x: destination.x + 6, y: destination.y + 3, width: 24, height: 24 };
}
function stableIndex(value, count) {
  let total = 0;
  for (let index = 0; index < value.length; index += 1) total = (total * 31 + value.charCodeAt(index)) >>> 0;
  return total % count;
}
function drawAtlasSprite(name, x, y, width, height) {
  const rect = ATLAS_RECTS[name];
  if (!atlasReady || atlasImage === null || rect === undefined) return false;
  context.drawImage(atlasImage, rect[0], rect[1], rect[2], rect[3], x, y, width, height);
  return true;
}
function drawIndustrialTile(column, row) {
  const x = ORIGIN_X + column * CELL_SIZE;
  const y = ORIGIN_Y + row * CELL_SIZE;
  if (drawAtlasSprite("floor_" + ((column * 3 + row) % 3), x, y, CELL_SIZE, CELL_SIZE)) return;
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
  if (drawAtlasSprite("crate_" + stableIndex(String(label || "cargo"), 4), x, y, 25, 25)) return;
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
function drawDepotSprite(position) {
  const destination = depotDestination(position);
  if (drawAtlasSprite("depot", destination.x, destination.y, destination.width, destination.height)) return;
  context.fillStyle = PALETTE.depotDark;
  context.fillRect(destination.x, destination.y, destination.width, destination.height);
  context.fillStyle = PALETTE.depot;
  context.fillRect(destination.x + 4, destination.y + 4, destination.width - 8, destination.height - 8);
  context.strokeStyle = PALETTE.pending;
  context.lineWidth = 2;
  context.strokeRect(destination.x + 5, destination.y + 5, destination.width - 10, destination.height - 10);
}
function drawRackShell(destination, shelfId) {
  const drawn = drawAtlasSprite("rack_" + stableIndex(shelfId, 3), destination.x, destination.y, destination.width, destination.height);
  // Atlas racks have baked crates. This opaque interior reconstruction makes the shell empty.
  context.fillStyle = PALETTE.shelfDark;
  context.fillRect(destination.x + 6, destination.y + 9, destination.width - 12, destination.height - 16);
  context.fillStyle = PALETTE.shelfHighlight;
  context.fillRect(destination.x + 3, destination.y + 4, destination.width - 6, 3);
  context.fillRect(destination.x + 4, destination.y + 18, destination.width - 8, 3);
  context.fillRect(destination.x + 4, destination.y + 31, destination.width - 8, 3);
  return drawn;
}
function drawShelfSprite(shelf) {
  const destination = rackDestination(shelf.position);
  drawRackShell(destination, shelf.shelf_id);
  if (shelf.item_id !== null) {
    const cargo = rackCargoDestination(destination);
    drawCrateSprite(cargo.x, cargo.y, PALETTE.item, shelf.item_id);
  }
}
function robotFacing(frames, frameIndex) {
  let facing = "robot_front";
  for (let index = 1; index <= frameIndex; index += 1) {
    const before = frames[index - 1].state_after.robot.position;
    const after = frames[index].state_after.robot.position;
    const deltaX = after[0] - before[0];
    const deltaY = after[1] - before[1];
    if (deltaX > 0 && deltaY === 0) facing = "robot_east";
    else if (deltaX < 0 && deltaY === 0) facing = "robot_west";
    else if (deltaY > 0 && deltaX === 0) facing = "robot_front";
    else if (deltaY < 0 && deltaX === 0) facing = "robot_north";
  }
  return facing;
}
function drawRobotShell(destination, facing) {
  const drawn = drawAtlasSprite(facing, destination.x, destination.y, destination.width, destination.height);
  // The source robots include magenta cargo. Opaque roof compositing removes it for empty robots.
  context.fillStyle = PALETTE.robotDark;
  context.fillRect(destination.x + 7, destination.y + 1, 22, 15);
  context.fillStyle = PALETTE.robot;
  context.fillRect(destination.x + 10, destination.y + 3, 16, 10);
  context.fillStyle = PALETTE.pending;
  context.fillRect(destination.x + 17, destination.y + 3, 2, 10);
  return drawn;
}
function drawRobotSprite(robot) {
  const destination = robotDestination(robot.position);
  if (drawRobotShell(destination, robotFacing(projection.frames, selectedFrame))) {
    if (robot.inventory_item_id !== null) {
      const cargo = robotCargoDestination(destination);
      drawCrateSprite(cargo.x, cargo.y, PALETTE.carried, robot.inventory_item_id);
    }
    return;
  }
  context.fillStyle = PALETTE.robotDark;
  context.fillRect(destination.x, destination.y, destination.width, destination.height);
  context.fillStyle = PALETTE.robot;
  context.fillRect(destination.x + 4, destination.y + 7, destination.width - 8, destination.height - 11);
  context.fillStyle = PALETTE.pending;
  context.fillRect(destination.x + 11, destination.y + 16, 14, 3);
  if (robot.inventory_item_id !== null) {
    const cargo = robotCargoDestination(destination);
    drawCrateSprite(cargo.x, cargo.y, PALETTE.carried, robot.inventory_item_id);
  }
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
function renderStatusClass(frame) {
  const classification = frame === null ? "initial" : CLASSIFICATION_STATUS[frame.classification] ? frame.classification : "unknown";
  ["initial", "unknown", "committed", "rejected", "malformed", "unsupported", "invalid", "unauthorized", "idempotent_replay"].forEach((name) => hudStatus.classList.remove("status-" + name));
  hudStatus.classList.add("status-" + classification);
}
function renderMissionHud(world, frame) {
  const completed = world.completed_orders.length;
  const total = world.orders.length;
  const status = classificationStatus(frame);
  hudCompleted.textContent = completed + "/" + total;
  hudProgressBar.style.width = (total ? (completed / total) * 100 : 0) + "%";
  hudAction.textContent = actionArgumentText(frame);
  hudStatus.textContent = status.label;
  renderStatusClass(frame);
  const events = subeventStatus(frame).slice(0, 2);
  [0, 1].forEach((index) => {
    const slot = query("#hud-event-" + index);
    if (slot === null) return;
    slot.classList.remove("subevent-fault", "subevent-retry", "subevent-empty");
    const event = events[index] || null;
    slot.textContent = event === null ? "—" : event.label;
    slot.classList.add(event === null ? "subevent-empty" : event.label === "FAULT" ? "subevent-fault" : "subevent-retry");
  });
}
function renderOrderQueue(world) {
  for (let index = 0; index < ORDER_SLOT_COUNT; index += 1) {
    const slot = query('.order-slot[data-order-index="' + index + '"]');
    if (slot === null) continue;
    const state = slot.querySelector(".order-state");
    const orderId = slot.querySelector(".order-id");
    const orderItem = slot.querySelector(".order-item");
    const order = world.orders[index] || null;
    slot.classList.remove("is-complete", "is-pending", "is-empty");
    if (order === null) {
      setPresentationText(state, "—");
      setPresentationText(orderId, "—");
      setPresentationText(orderItem, "—");
      slot.classList.add("is-empty");
      continue;
    }
    const done = world.completed_orders.includes(order.order_id);
    setPresentationText(state, done ? "DONE" : "PENDING");
    setPresentationText(orderId, order.order_id);
    setPresentationText(orderItem, order.item_id + " x" + order.quantity);
    slot.classList.add(done ? "is-complete" : "is-pending");
  }
}
function renderRobotHud(world) {
  hudRobotPosition.textContent = "(" + world.robot.position.join(", ") + ")";
  hudRobotLoad.textContent = valueText(world.robot.inventory_item_id, "empty");
  hudRobotLoad.classList.remove("is-empty", "is-loaded");
  hudRobotLoad.classList.add(world.robot.inventory_item_id === null ? "is-empty" : "is-loaded");
}
function renderVerifierHud() {
  const result = projection.terminal_result;
  hudScore.textContent = String(result.verified_score);
  hudOutcome.textContent = result.outcome;
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
function renderReplayMetadata(frame, world) {
  const result = projection.terminal_result;
  const status = classificationStatus(frame);
  statusText.textContent = "frame " + (frame ? frame.policy_step : "initial") + " · action " + normalizedActionText(frame) + " · status " + status.label;
  terminalText.textContent = "Verifier-owned score: " + result.verified_score + "; Verifier-owned outcome: " + result.outcome + "; authority: " + result.outcome_authority + "; verifier version " + result.verifier_version + " status " + result.verifier_status + "; completed " + result.completed_count + "/" + result.total_count + ".";
  frameDetails.textContent = detailLines(frame, world).join("\\n");
  detailsSummary.textContent = " · " + (frame ? "frame " + frame.policy_step + " / " + projection.frames.length : "initial world");
}
function drawWorld(world, frame) {
  drawFloor();
  drawPathTrail(world, frame);
  drawDepotSprite(world.robot.base_position);
  for (const shelf of world.shelves) drawShelfSprite(shelf);
  drawRobotSprite(world.robot);
  drawTargetCue(safeTargetForFrame(frame, world));
}
function render() {
  const frame = projection.frames.length ? projection.frames[selectedFrame] : null;
  const world = worldForFrame();
  drawWorld(world, frame);
  renderMissionHud(world, frame);
  renderOrderQueue(world);
  renderRobotHud(world);
  renderVerifierHud();
  renderReplayMetadata(frame, world);
}
function loadAtlas() {
  if (!ATLAS_DATA_URI || typeof Image !== "function") return;
  const image = new Image();
  image.onload = function () { atlasImage = image; atlasReady = true; if (projection !== null) render(); };
  image.onerror = function () { atlasImage = null; atlasReady = false; if (projection !== null) render(); };
  image.src = ATLAS_DATA_URI;
}
function pause() { if (timer !== null) { clearInterval(timer); timer = null; } }
function stepForward() { if (projection.frames.length) selectedFrame = (selectedFrame + 1) % projection.frames.length; render(); }
function play() { pause(); timer = setInterval(stepForward, NOMINAL_FRAME_MS / Number(speed.value)); }
function initializePresentation() {
  try {
    canvas = query("#visualizer-canvas");
    statusText = query("#status-text");
    terminalText = query("#terminal-text");
    frameDetails = query("#frame-details");
    detailsSummary = query("#details-summary");
    hudCompleted = query("#hud-completed");
    hudProgressBar = query("#hud-progress-bar");
    hudAction = query("#hud-action");
    hudStatus = query("#hud-status");
    hudRobotPosition = query("#hud-robot-position");
    hudRobotLoad = query("#hud-robot-load");
    hudScore = query("#hud-score");
    hudOutcome = query("#hud-outcome");
    speed = query("#speed");
    if (canvas === null || typeof canvas.getContext !== "function") return showUnavailableState();
    context = canvas.getContext("2d");
    if (context === null) return showUnavailableState();

    const embeddedProjection = query("#visualizer-projection-hex");
    if (embeddedProjection === null || typeof embeddedProjection.textContent !== "string") return showUnavailableState();
    const hex = embeddedProjection.textContent;
    if (hex.length % 2 !== 0 || !/^[0-9a-f]*$/i.test(hex)) return showUnavailableState();
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) bytes[i / 2] = Number.parseInt(hex.slice(i, i + 2), 16);
    projection = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (projection === null || typeof projection !== "object") return showUnavailableState();
    loadAtlas();

    query("#play").addEventListener("click", play);
    query("#pause").addEventListener("click", pause);
    query("#step-forward").addEventListener("click", stepForward);
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


def _atlas_data_uri() -> str:
    """Load the packaged atlas without depending on the caller's working directory."""
    try:
        atlas = resources.files("robot_factory_arena.evidence").joinpath(
            "assets/tactical_reference_atlas.png"
        ).read_bytes()
    except OSError:
        return ""
    return "data:image/png;base64," + base64.b64encode(atlas).decode("ascii")


def render_visualizer(projection: VisualizerProjection) -> bytes:
    """Return deterministic self-contained HTML for one validated projection."""
    if not isinstance(projection, VisualizerProjection):
        raise TypeError("projection must be a VisualizerProjection")
    return (
        _TEMPLATE.replace("__PROJECTION_HEX__", canonical_projection_bytes(projection).hex())
        .replace("__ATLAS_DATA_URI__", _atlas_data_uri())
        .encode("utf-8")
    )
