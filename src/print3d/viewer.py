"""A page to turn the part around in: jobs/<slug>/view.html.

One self-contained file, so it opens from disk in a browser or in an agent's
preview pane (Hermes desktop's `open_preview`). The STLs travel inside it as
base64; three.js comes from a CDN.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from print3d.filaments import colour_hex
from print3d.handoff import _parts

THREE = "https://cdn.jsdelivr.net/npm/three@0.160.0"


def _stl(job_dir: Path, part: dict[str, Any], single: bool) -> Path | None:
    if single:
        stl = job_dir / "mesh" / f"{job_dir.name}.stl"
    else:
        stl = job_dir / "mesh" / "assembly" / f"{part['name']}.stl"
    return stl if stl.is_file() else None


def build_viewer(job_dir: Path, report: dict[str, Any]) -> Path | None:
    """Write view.html for the job's mesh, every part where it sits assembled. None without meshes."""
    parts = _parts(report, job_dir.name)
    single = not report.get("parts")
    items = []
    for part in parts:
        stl = _stl(job_dir, part, single)
        if stl is None:
            return None
        items.append({
            "name": part["name"],
            "group": part["object"],
            "color": part["color"],
            "hex": colour_hex(part["color"]),
            "stl": base64.b64encode(stl.read_bytes()).decode("ascii"),
        })
    metrics = report.get("slice_metrics") or {}
    data = {
        "title": report["brief"]["prompt"],
        "status": report.get("status"),
        "minutes": metrics.get("estimated_minutes"),
        "grams": metrics.get("filament_g"),
        "parts": items,
    }
    payload = json.dumps(data).replace("</", "<\\/")
    out = job_dir / "view.html"
    out.write_text(PAGE.replace("__THREE__", THREE).replace("__DATA__", payload), encoding="utf-8")
    return out


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>print3d viewer</title>
<style>
:root { --bg: #f4f4f2; --panel: rgba(255,255,255,.88); --ink: #1d1d1b; --sub: #6b6b66; --line: #d9d9d4; --grid: #c9c9c3; --mark: #e0482b; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #18181a; --panel: rgba(32,32,35,.88); --ink: #ececea; --sub: #9a9a96; --line: #3a3a3e; --grid: #3a3a3e; --mark: #ff6a4d; }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--ink);
  font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; overflow: hidden; }
canvas { display: block; }
#panel { position: absolute; top: 12px; left: 12px; max-width: min(320px, calc(100% - 24px));
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px;
  backdrop-filter: blur(8px); }
#panel:not([open]) { padding: 8px 12px; }
summary { display: flex; align-items: flex-start; gap: 8px; list-style: none; cursor: pointer; }
summary::-webkit-details-marker { display: none; }
.head { flex: 1; min-width: 0; }
.chev { flex: none; width: 16px; height: 16px; margin-top: 1px; color: var(--sub); transition: transform .15s; }
#panel[open] .chev { transform: rotate(180deg); }
#panel:not([open]) :is(h1, .sub) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
h1 { font-size: 14px; margin: 0 0 4px; font-weight: 600; }
.sub { color: var(--sub); }
.parts { margin: 10px 0 0; padding: 0; list-style: none; }
.parts label { display: flex; align-items: center; gap: 8px; padding: 2px 0; cursor: pointer; }
.swatch { width: 12px; height: 12px; border-radius: 3px; border: 1px solid var(--line); flex: none; }
.row { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
input[type=range] { flex: 1; min-width: 100px; }
button { font: inherit; color: var(--ink); background: transparent; border: 1px solid var(--line);
  border-radius: 6px; padding: 3px 9px; cursor: pointer; }
button[aria-pressed=true] { background: var(--line); }
#hint { position: absolute; bottom: 10px; left: 0; right: 0; text-align: center; color: var(--sub); pointer-events: none; }
#readout { position: absolute; transform: translate(-50%, calc(-100% - 10px)); background: var(--panel);
  border: 1px solid var(--line); border-radius: 6px; padding: 3px 9px; white-space: nowrap;
  pointer-events: none; font-variant-numeric: tabular-nums; }
body.measuring canvas { cursor: crosshair; }
#error { position: absolute; inset: 0; display: none; align-items: center; justify-content: center; padding: 24px; text-align: center; }
</style>
<script type="importmap">
{ "imports": { "three": "__THREE__/build/three.module.js", "three/addons/": "__THREE__/examples/jsm/" } }
</script>
</head>
<body>
<details id="panel" open>
  <summary>
    <div class="head"><h1 id="title"></h1><div class="sub" id="stats"></div></div>
    <svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
  </summary>
  <ul class="parts" id="parts"></ul>
  <div class="row" id="explode-row" hidden>
    <span>Explode</span><input type="range" id="explode" min="0" max="1.5" step="0.01" value="0">
  </div>
  <div class="row">
    <button id="reset">Reset view</button>
    <button id="wire" aria-pressed="false">Wireframe</button>
    <button id="grid-btn" aria-pressed="true">Grid</button>
    <button id="measure" aria-pressed="false">Measure</button>
  </div>
</details>
<script>
// The panel folds to its title, and starts folded in a narrow pane such as the
// Hermes desktop preview. Set before the first paint, so it does not animate;
// only a click is saved, so rebuilds of the page keep the user's own choice.
(() => {
  const panel = document.getElementById('panel');
  let saved = null;
  try { saved = localStorage.getItem('print3d.panel'); } catch {}
  panel.open = saved ? saved === 'open' : window.innerWidth >= 700;
  panel.querySelector('summary').addEventListener('click', () => {
    try { localStorage.setItem('print3d.panel', panel.open ? 'closed' : 'open'); } catch {}
  });
})();
</script>
<div id="hint">Drag to rotate · scroll to zoom · right-drag to pan</div>
<div id="readout" hidden></div>
<div id="error">Could not load three.js from the CDN. The viewer needs an internet connection the first time.</div>
<script type="application/json" id="data">__DATA__</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';

const DATA = JSON.parse(document.getElementById('data').textContent);
const fmt = (n, d = 1) => Number(n).toFixed(d);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(window.devicePixelRatio);
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 10000);
camera.up.set(0, 0, 1);  // Blender and the bed are Z-up
scene.add(camera);
scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8a80, 1.6));
const key = new THREE.DirectionalLight(0xffffff, 1.8);
key.position.set(1, 1, 2);
camera.add(key);  // the light turns with the view, so no face goes black

// Every part where it sits assembled; the whole is centred and stood on the bed.
const loader = new STLLoader();
const meshes = DATA.parts.map(part => {
  const bytes = Uint8Array.from(atob(part.stl), c => c.charCodeAt(0));
  const geometry = loader.parse(bytes.buffer);
  const material = new THREE.MeshStandardMaterial({ color: part.hex, roughness: 0.55, metalness: 0.05 });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.userData = part;
  scene.add(mesh);
  return mesh;
});
const box = new THREE.Box3();
meshes.forEach(m => box.expandByObject(m));
const shift = new THREE.Vector3(-(box.min.x + box.max.x) / 2, -(box.min.y + box.max.y) / 2, -box.min.z);
meshes.forEach(m => { m.geometry.translate(shift.x, shift.y, shift.z); m.geometry.computeBoundingBox(); });
box.translate(shift);
const size = box.getSize(new THREE.Vector3());
const centre = box.getCenter(new THREE.Vector3());
const span = Math.max(size.length(), 1);

// Each fused group moves away from the middle along the line through its own centre.
const groups = {};
meshes.forEach(m => (groups[m.userData.group] ??= []).push(m));
const away = {};
for (const [name, members] of Object.entries(groups)) {
  const g = new THREE.Box3();
  members.forEach(m => g.union(m.geometry.boundingBox));
  away[name] = g.getCenter(new THREE.Vector3()).sub(centre);
}
const lowest = Math.min(...Object.values(away).map(v => v.z));
Object.values(away).forEach(v => { v.z -= lowest; });  // nothing sinks through the bed
function explode(k) {
  meshes.forEach(m => m.position.copy(away[m.userData.group]).multiplyScalar(k));
}

const cell = size.x > 150 || size.y > 150 ? 20 : 10;
const gridSize = Math.ceil(Math.max(size.x, size.y) * 1.6 / cell) * cell;
const grid = new THREE.GridHelper(gridSize, gridSize / cell,
  getComputedStyle(document.documentElement).getPropertyValue('--grid').trim(),
  getComputedStyle(document.documentElement).getPropertyValue('--grid').trim());
grid.rotation.x = Math.PI / 2;
scene.add(grid);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
function reset() {
  const dir = new THREE.Vector3(1, -1.2, 0.9).normalize();
  camera.position.copy(centre).addScaledVector(dir, span * 1.9);
  controls.target.copy(centre);
  controls.update();
}

document.getElementById('title').textContent = DATA.title;
document.getElementById('title').title = DATA.title;  // the whole of it while folded to one line
const stats = [`${fmt(size.x)} × ${fmt(size.y)} × ${fmt(size.z)} mm`];
if (DATA.minutes != null) stats.push(`${Math.round(DATA.minutes)} min`);
if (DATA.grams != null) stats.push(`${fmt(DATA.grams)} g`);
document.getElementById('stats').textContent = stats.join(' · ');

if (meshes.length > 1) {
  const list = document.getElementById('parts');
  meshes.forEach(m => {
    const li = document.createElement('li');
    li.innerHTML = `<label><input type="checkbox" checked><span class="swatch"></span><span></span></label>`;
    li.querySelector('.swatch').style.background = m.userData.hex;
    li.querySelector('span:last-child').textContent = `${m.userData.name} (${m.userData.color})`;
    li.querySelector('input').onchange = e => { m.visible = e.target.checked; };
    list.append(li);
  });
}
if (Object.keys(groups).length > 1) {
  document.getElementById('explode-row').hidden = false;
  document.getElementById('explode').oninput = e => explode(Number(e.target.value));
}
document.getElementById('reset').onclick = reset;
const toggle = (id, apply) => {
  const btn = document.getElementById(id);
  btn.onclick = () => { const on = btn.getAttribute('aria-pressed') !== 'true'; btn.setAttribute('aria-pressed', on); apply(on); };
};
toggle('wire', on => meshes.forEach(m => { m.material.wireframe = on; }));
toggle('grid-btn', on => { grid.visible = on; });

// Measure: two clicks on the mesh, and the distance between them. A click within
// a few pixels of a corner of the face under it lands on that corner, so edge to
// edge reads exactly; anywhere else it takes the point on the surface.
const markColour = getComputedStyle(document.documentElement).getPropertyValue('--mark').trim();
const picks = [];
const dots = new THREE.Points(new THREE.BufferGeometry(),
  new THREE.PointsMaterial({ color: markColour, size: 9, sizeAttenuation: false, depthTest: false }));
const rule = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: markColour, depthTest: false }));
[dots, rule].forEach(o => { o.renderOrder = 1; o.frustumCulled = false; scene.add(o); });
const readout = document.getElementById('readout');
const hint = document.getElementById('hint');
const hintText = hint.textContent;
let measuring = false;

function drawPicks() {
  dots.geometry.setFromPoints(picks);
  rule.geometry.setFromPoints(picks.length === 2 ? picks : []);
  readout.hidden = picks.length < 2;
  if (picks.length < 2) return;
  const d = picks[1].clone().sub(picks[0]);
  readout.innerHTML = `<b>${fmt(d.length(), 2)} mm</b> <span class="sub">` +
    `Δx ${fmt(Math.abs(d.x))} · Δy ${fmt(Math.abs(d.y))} · Δz ${fmt(Math.abs(d.z))}</span>`;
}
function clearPicks() { picks.length = 0; drawPicks(); }

const ray = new THREE.Raycaster();
function pick(x, y) {
  const r = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2((x - r.left) / r.width * 2 - 1, -(y - r.top) / r.height * 2 + 1);
  ray.setFromCamera(ndc, camera);
  const [hit] = ray.intersectObjects(meshes.filter(m => m.visible), false);  // the raycaster ignores .visible
  if (!hit) return null;
  const position = hit.object.geometry.attributes.position;
  let best = hit.point, bestPx = 8;
  for (const i of [hit.face.a, hit.face.b, hit.face.c]) {
    const corner = new THREE.Vector3().fromBufferAttribute(position, i).applyMatrix4(hit.object.matrixWorld);
    const s = corner.clone().project(camera);
    const px = Math.hypot((s.x - ndc.x) * r.width / 2, (s.y - ndc.y) * r.height / 2);
    if (px < bestPx) { best = corner; bestPx = px; }
  }
  return best.clone();
}

// A click, not the end of a drag that turned the view.
let down = null;
renderer.domElement.addEventListener('pointerdown', e => { down = e.button === 0 ? [e.clientX, e.clientY] : null; });
renderer.domElement.addEventListener('pointerup', e => {
  if (!measuring || !down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
  const p = pick(e.clientX, e.clientY);
  if (!p) return;
  if (picks.length === 2) picks.length = 0;
  picks.push(p);
  drawPicks();
});
toggle('measure', on => {
  measuring = on;
  document.body.classList.toggle('measuring', on);
  hint.textContent = on ? 'Click two points to measure · Esc to clear' : hintText;
  clearPicks();
});
window.addEventListener('keydown', e => { if (e.key === 'Escape') clearPicks(); });
document.getElementById('explode').addEventListener('input', clearPicks);  // the points would stay behind

function placeReadout() {
  if (picks.length < 2) return;
  const mid = picks[0].clone().add(picks[1]).multiplyScalar(0.5).project(camera);
  readout.style.left = `${(mid.x + 1) / 2 * window.innerWidth}px`;
  readout.style.top = `${(1 - mid.y) / 2 * window.innerHeight}px`;
}

function resize() {
  renderer.setSize(window.innerWidth, window.innerHeight);
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
resize();
reset();
renderer.setAnimationLoop(() => { controls.update(); placeReadout(); renderer.render(scene, camera); });
</script>
<script>
// A module that never ran means the CDN import failed.
window.addEventListener('load', () => setTimeout(() => {
  if (!document.querySelector('canvas')) document.getElementById('error').style.display = 'flex';
}, 1500));
</script>
</body>
</html>
"""
