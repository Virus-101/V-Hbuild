// V-Hbuild viewer: plays a .vbuild's assembly steps over its 3D model.
//
// The GLB has one node per part (named by ref), one per port, and the nodes
// of every cover ("shell"/"lid", "resin_shell"/"resin_lid", "laser_*"). The
// manifest says which step reveals which node and wire. Each step animates:
// the cover is printed layer by layer, resin-printed hanging from its plate,
// or laser-cut flat and folded up; ports slide into the wall, parts drop into
// place, wires draw themselves, the board pulses while it is flashed, the lid
// closes. Coordinates are millimetres, z up.
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/addons/OrbitControls.js";
import { GLTFLoader } from "/static/vendor/addons/GLTFLoader.js";
import { unzipSync, strFromU8 } from "/static/vendor/fflate.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
if (params.get("embed")) document.body.classList.add("embed");
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

let token = params.get("token") || "";
try { if (token) sessionStorage.setItem("vhbuild-token", token); else token = sessionStorage.getItem("vhbuild-token") || ""; } catch {}

const DURATION = { print: 3.0, resin: 3.6, laser: 4.2, laser_lid: 1.4, flash: 2.6, close: 1.4, default: 1.6 };
const HOLD = 0.9;   // seconds to rest on a finished step while playing
const PANEL_ORDER = ["laser_bottom", "laser_front", "laser_back", "laser_left", "laser_right"];

// --- three.js stage -----------------------------------------------------------
const stage = $("stage");
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.localClippingEnabled = true;
stage.prepend(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 1, 5000);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.autoRotateSpeed = 0.7;

scene.add(new THREE.HemisphereLight(0xffffff, 0x55504a, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
scene.add(sun);

function stageColor() {
  return new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue("--stage").trim() || "#e9e6df");
}
scene.background = stageColor();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { scene.background = stageColor(); });

function resize() {
  const { clientWidth: w, clientHeight: h } = stage;
  renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(stage);

// --- the build being shown -------------------------------------------------------
let man = null, files = null, buildId = params.get("build");
let steps = [];          // man.steps with the chosen cover's first and last step
let cover = "fdm";
let nodes = {};          // name -> { obj, base: Vector3, baseQ: Quaternion, step }
let wires = [];          // { mesh, step, count }
let cur = 0, t = 0, playing = !reduceMotion, hold = 0, root = null, gltfScene = null, grid = null;
const planes = { print: new THREE.Plane(new THREE.Vector3(0, 0, -1), 0),
                 resin: new THREE.Plane(new THREE.Vector3(0, 0, 1), 0),
                 laser: new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0) };
const props = {};        // build plate, laser beam: helpers drawn only while they work

function clearScene() {
  if (root) scene.remove(root);
  wires.forEach((w) => scene.remove(w.mesh));
  nodes = {}; wires = []; root = null; grid = null;
}

function coverNodes(id) { return man.covers?.[id]?.nodes || (id === "fdm" ? ["shell", "lid"] : []); }
function allCoverNodes() {
  const out = new Set(["shell", "lid"]);
  Object.keys(man.covers || {}).forEach((id) => coverNodes(id).forEach((n) => out.add(n)));
  return out;
}

function frame() {
  // Frame everything: probes outside, the resin plate, the laser cutting sheet.
  const [Lo, Wo, Ho] = man.box.outer;
  const bounds = new THREE.Box3();
  for (const [name, n] of Object.entries(nodes)) {
    if (allCoverNodes().has(name) && !coverNodes(cover).includes(name)) continue;
    bounds.expandByObject(n.obj);
  }
  const c3 = man.covers?.[cover];
  if (cover === "laser" && c3?.flat_box) {
    const [[x0, y0], [x1, y1]] = c3.flat_box;
    bounds.expandByPoint(new THREE.Vector3(x0, y0, 0)).expandByPoint(new THREE.Vector3(x1, y1, 0));
  }
  if (cover === "resin") bounds.expandByPoint(new THREE.Vector3(Lo / 2, Wo / 2, Ho + 50));
  const c = bounds.getCenter(new THREE.Vector3());
  const s = bounds.getSize(new THREE.Vector3());
  const size = Math.max(s.x, s.y, Ho);
  controls.target.set(c.x, c.y, Math.min(c.z, Ho / 2));
  // High enough to see down into the open box.
  camera.position.set(c.x + size * 0.35, c.y - size * 0.95, Ho + size * 1.25);
  sun.position.set(c.x + size * 2, c.y - size * 1.5, size * 3);
  if (grid) root.remove(grid);
  const extent = Math.ceil(Math.max(s.x, s.y) * 1.6 / 10) * 10;
  grid = new THREE.GridHelper(extent, extent / 10, 0x999999, 0xbbbbbb);
  grid.rotation.x = Math.PI / 2;
  grid.position.set(c.x, c.y, Math.min(bounds.min.z, 0) - 0.05);
  grid.material.transparent = true; grid.material.opacity = 0.35;
  root.add(grid);
}

function tube(points, color, radius) {
  const path = new THREE.CurvePath();
  for (let i = 0; i < points.length - 1; i++) {
    path.add(new THREE.LineCurve3(new THREE.Vector3(...points[i]), new THREE.Vector3(...points[i + 1])));
  }
  const geo = new THREE.TubeGeometry(path, 90, radius, 6, false);
  const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color, roughness: 0.5 }));
  return { mesh, count: geo.index.count };
}

function makeProps() {
  const [Lo, Wo, Ho] = man.box.outer;
  const dark = new THREE.MeshStandardMaterial({ color: 0x55585e, metalness: 0.6, roughness: 0.35 });
  props.plate = new THREE.Mesh(new THREE.BoxGeometry(Lo + 16, Wo + 16, 3), dark);
  props.plate.position.set(Lo / 2, Wo / 2, Ho + 46.5);
  const fb = man.covers?.laser?.flat_box;
  const depth = fb ? fb[1][1] - fb[0][1] : 100;
  props.beam = new THREE.Mesh(new THREE.BoxGeometry(0.9, depth + 6, 0.9),
    new THREE.MeshBasicMaterial({ color: 0xff2a1a }));
  props.beam.position.set(0, fb ? (fb[0][1] + fb[1][1]) / 2 : 0, 3.4);
  for (const p of Object.values(props)) { p.visible = false; root.add(p); }
}

function buildSteps() {
  const c = man.covers?.[cover];
  steps = man.steps.map((s) => (s.cover && c?.[s.cover]) ? c[s.cover] : s);
  const firstStep = {}, wireStep = {};
  steps.forEach((s, i) => {
    (s.show || []).forEach((n) => { if (!(n in firstStep)) firstStep[n] = i; });
    (s.wires || []).forEach((r) => { if (!(r in wireStep)) wireStep[r] = i; });
  });
  const others = allCoverNodes();
  coverNodes(cover).forEach((n) => others.delete(n));
  for (const [name, n] of Object.entries(nodes)) {
    n.step = others.has(name) ? Infinity : (firstStep[name] ?? 0);
  }
  for (const w of wires) w.step = wireStep[w.key] ?? wireStep[w.ref] ?? 0;
}

async function show(manifest, fileMap) {
  man = manifest; files = fileMap;
  clearScene();
  $("drop").hidden = true;
  $("overlay").hidden = false;
  $("title").textContent = man.name;
  $("sub").textContent = `${man.board?.name || ""} · planned by ${man.planner || "V-Hbuild"}`;
  document.title = `${man.name} · V-Hbuild`;

  const glb = files[man.model];
  if (!glb) throw new Error("This .vbuild has no 3D model.");
  const gltf = await new GLTFLoader().parseAsync(glb.buffer.slice(glb.byteOffset, glb.byteOffset + glb.byteLength), "");
  gltfScene = gltf.scene;
  root = new THREE.Group();
  root.add(gltf.scene);
  scene.add(root);

  for (const name of new Set([...allCoverNodes(), ...Object.keys(man.nodes)])) {
    const obj = gltf.scene.getObjectByName(name);
    if (!obj) continue;
    const plane = name === "shell" ? planes.print : name === "resin_shell" ? planes.resin
      : name.startsWith("laser_") ? planes.laser : null;
    obj.traverse((m) => {
      if (m.isMesh) {
        m.material = m.material.clone();
        m.material.userData.baseOpacity = m.material.opacity;
        if (plane) m.material.clippingPlanes = [plane];
      }
    });
    nodes[name] = { obj, base: obj.position.clone(), baseQ: obj.quaternion.clone(), step: 0 };
  }
  for (const w of man.wires || []) {
    const { mesh, count } = tube(w.points, w.color, w.cable ? 1.6 : 0.55);
    mesh.visible = false;
    scene.add(mesh);
    wires.push({ mesh, count, step: 0, key: w.id ?? w.ref, ref: w.ref });
  }
  makeProps();
  renderCovers();
  pickCover(chosenCover(), false);
}

function chosenCover() {
  let want = params.get("cover");
  try { want = want || localStorage.getItem("vhbuild-cover"); } catch {}
  return man.covers?.[want] ? want : (man.cover || "fdm");
}

function pickCover(id, remember = true) {
  cover = man.covers?.[id] ? id : "fdm";
  if (remember) { try { localStorage.setItem("vhbuild-cover", cover); } catch {} }
  if ($("cover")) $("cover").value = cover;
  const c = man.covers?.[cover];
  const note = $("cover-note");
  if (note) {
    note.textContent = c ? (c.fit_note || "") + (c.sheet_note ? " " + c.sheet_note : "") : "";
    note.className = c && c.fits === false ? "err" : "";
  }
  buildSteps();
  frame();
  renderSteps();
  renderMachines();
  go(0);
}

function renderCovers() {
  const row = $("cover-row"), sel = $("cover");
  const ids = Object.keys(man.covers || {});
  row.hidden = ids.length < 2;
  sel.innerHTML = ids.map((id) => `<option value="${id}">${esc(man.covers[id].label)}</option>`).join("");
}
$("cover").onchange = (e) => { pickCover(e.target.value); setPlaying(!reduceMotion); };

// --- timeline ----------------------------------------------------------------------
const ease = (x) => 1 - Math.pow(1 - Math.min(Math.max(x, 0), 1), 3);
const X = new THREE.Vector3(1, 0, 0);

function setOpacity(obj, k) {
  obj.traverse((m) => {
    if (!m.isMesh) return;
    const base = m.material.userData.baseOpacity ?? 1;
    m.material.transparent = k < 1 || base < 1;
    m.material.opacity = base * k;
  });
}

function glow(obj, k) {
  obj.traverse((m) => { if (m.isMesh && m.material.emissive) m.material.emissive.setRGB(0.1 * k, 0.9 * k, 0.35 * k); });
}

// Turn `obj` by `q` about the model point `pivot`, then move it by `offset`.
function turn(n, q, pivot, offset) {
  n.obj.quaternion.copy(n.baseQ).premultiply(q);
  n.obj.position.copy(n.base).add(pivot).add(offset).sub(pivot.clone().applyQuaternion(q));
}

// A laser panel at k = 0 lies flat on the cutting bed; at k = 1 it is in place.
function fold(n, pose, k) {
  const flatQ = new THREE.Quaternion(...pose.quat);
  const q = flatQ.slerp(new THREE.Quaternion(), k);
  const center = new THREE.Vector3(...pose.flat_center).lerp(new THREE.Vector3(...pose.center), k);
  turn(n, q, new THREE.Vector3(...pose.center), center.sub(new THREE.Vector3(...pose.center)));
}

function apply() {
  if (!man) return;
  const step = steps[cur] || {};
  const [Lo, Wo, Ho] = man.box.outer;
  const poses = man.covers?.laser?.poses || {};
  const makeIdx = steps.findIndex((s) => s.cover === "make");
  const closeIdx = steps.findIndex((s) => s.cover === "close");
  for (const p of Object.values(props)) p.visible = false;
  planes.print.constant = Ho + 1;
  planes.resin.constant = 1e5;
  planes.laser.constant = 1e5;

  for (const [name, n] of Object.entries(nodes)) {
    const { obj, base } = n;
    obj.position.copy(base);
    obj.quaternion.copy(n.baseQ);
    setOpacity(obj, 1);
    if (n.step > cur) { obj.visible = false; continue; }
    obj.visible = true;

    if (poses[name]) {
      // Laser panels: cut flat, folded up in the cut step; the top waits for the lid step.
      let k = 1;
      if (name === "laser_top") k = cur > closeIdx ? 1 : cur === closeIdx ? ease(t) : 0;
      else if (cur === makeIdx) k = ease((t - 0.42 - PANEL_ORDER.indexOf(name) * 0.07) / 0.3);
      fold(n, poses[name], k);
      if (cur === makeIdx && t < 0.4) {
        const fb = man.covers.laser.flat_box;
        const x = fb[0][0] + (fb[1][0] - fb[0][0]) * (t / 0.4);   // the head moves at a steady speed
        planes.laser.constant = x;
        props.beam.position.x = x;
        props.beam.visible = true;
      }
      continue;
    }
    if (n.step < cur) continue;
    // Revealed by the current step: animate it in.
    if (step.action === "print" && name === "shell") {
      planes.print.constant = Ho * ease(t) + 0.01;
    } else if (step.action === "resin" && name === "resin_shell") {
      // Grows down from a build plate, upside down, then turns over into place.
      const plateZ = Ho + 45, split = 0.7;
      const k = t < split ? 0 : ease((t - split) / (1 - split));
      if (t < split) {
        planes.resin.constant = -(plateZ - Ho * (t / split));   // one layer at a time
        props.plate.visible = true;
      }
      const q = new THREE.Quaternion().setFromAxisAngle(X, Math.PI * (1 - k));
      turn(n, q, new THREE.Vector3(Lo / 2, Wo / 2, Ho / 2), new THREE.Vector3(0, 0, (plateZ - Ho) * (1 - k)));
    } else if (step.action === "close" && step.show?.includes(name)) {
      obj.position.z = base.z + (1 - ease(t)) * 70;
      setOpacity(obj, Math.min(1, t * 2));
    } else if (man.nodes[name]?.port && !man.nodes[name]?.outside) {
      obj.position.x = base.x + (1 - ease(t * 1.3)) * 45;   // slides in through the wall
      setOpacity(obj, Math.min(1, t * 3));
    } else {
      obj.position.z = base.z + (1 - ease(t * 1.4)) * 55;
      setOpacity(obj, Math.min(1, t * 3));
    }
  }
  for (const w of wires) {
    w.mesh.visible = w.step <= cur;
    const k = w.step < cur ? 1 : ease((t - 0.45) / 0.55);
    w.mesh.geometry.setDrawRange(0, Math.floor(w.count * k / 3) * 3);
  }
  const board = nodes.U1?.obj;
  if (board) glow(board, step.action === "flash" ? (0.5 + 0.5 * Math.sin(t * Math.PI * 10)) * (1 - t * 0.6) : 0);

  $("cap-title").textContent = step.title || "";
  $("cap-text").textContent = step.text || "";
  const last = cur === steps.length - 1 && t >= 1;
  $("badge").textContent = last ? "Built" : `Step ${cur + 1} of ${steps.length}`;
  $("badge").className = last ? "ok" : "";
  [...$("steps").children].forEach((li, i) => {
    li.classList.toggle("cur", i === cur);
    li.classList.toggle("done", i < cur || (i === cur && t >= 1));
  });
}

function go(i, instant = false) {
  cur = Math.max(0, Math.min(i, steps.length - 1));
  t = instant || reduceMotion ? 1 : 0;
  hold = 0;
  apply();
}

function setPlaying(p) {
  playing = p;
  $("play").textContent = playing ? "Pause" : "Play";
}

let lastTime = performance.now();
function loop(now) {
  const dt = Math.min((now - lastTime) / 1000, 0.1);
  lastTime = now;
  if (man && playing) {
    const step = steps[cur];
    if (t < 1) {
      t = Math.min(1, t + dt / (DURATION[step.action] || DURATION.default));
      apply();
    } else if (cur < steps.length - 1) {
      hold += dt;
      if (hold > HOLD) go(cur + 1);
    } else {
      setPlaying(false);
    }
  }
  controls.autoRotate = playing;
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

function renderSteps() {
  const ol = $("steps");
  ol.innerHTML = "";
  steps.forEach((s, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="n">${i + 1}</span><span></span>`;
    li.lastChild.textContent = s.title;
    li.onclick = () => { setPlaying(false); go(i, true); };
    ol.appendChild(li);
  });
}

$("play").onclick = () => {
  if (!man) return;
  if (!playing && cur === steps.length - 1 && t >= 1) go(0);
  setPlaying(!playing);
};
$("prev").onclick = () => { if (man) { setPlaying(false); go(cur - 1, true); } };
$("next").onclick = () => { if (man) { setPlaying(false); go(cur + 1, true); } };
$("restart").onclick = () => { if (man) { go(0); setPlaying(true); } };

// --- machines --------------------------------------------------------------------
let status = { desktop: false };

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-VHbuild-Token": token, ...(opts.headers || {}) } });
  const data = await r.json().catch(() => ({}));
  if (!r.ok && data.log === undefined) throw new Error(data.detail || r.statusText);
  return data;
}

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

const COVER_FILES = {
  fdm: [["enclosure/print_plate.3mf", "Printer file (.3mf)"]],
  resin: [["enclosure/resin/shell.stl", "Shell (.stl)"], ["enclosure/resin/lid.stl", "Lid (.stl)"]],
  laser: [["enclosure/laser/panels.svg", "Cut sheet (.svg)"], ["enclosure/laser/panels.dxf", "Cut sheet (.dxf)"]],
};
const COVER_VERB = { fdm: "Print enclosure", resin: "Open in resin slicer", laser: "Open cut sheet" };

async function renderMachines() {
  const box = $("machines");
  box.hidden = false;
  const board = man.machines?.board || {};
  const fw = board.file;
  if (!status.desktop) {
    // Downloads come straight out of the loaded file, so this works on a
    // static host with no V-Hbuild server behind it.
    box.innerHTML = `<h2>Your machines</h2><div class="row" id="m-files"></div>
      <p class="hint">Open this .vbuild in the V-Hbuild desktop app to print the enclosure and flash the board directly.</p>`;
    const add = (p, label) => {
      if (!files[p]) return;
      const a = document.createElement("a");
      a.className = "btn ghost"; a.textContent = label;
      a.download = p.split("/").pop();
      a.href = URL.createObjectURL(new Blob([files[p]]));
      $("m-files").appendChild(a);
    };
    (COVER_FILES[cover] || COVER_FILES.fdm).forEach(([p, label]) => add(p, label));
    if (fw) add(fw, "Board firmware");
    return;
  }
  const fdm = cover === "fdm";
  box.innerHTML = `<h2>Your machines</h2>
    <div class="row"><button id="m-print">${COVER_VERB[cover] || COVER_VERB.fdm}</button>${fdm ? `<button class="ghost" id="m-slicer">Open in my slicer</button>` : ""}</div>
    <div class="row"><select id="m-port" aria-label="Board connection"><option>Looking for boards…</option></select>
      <button id="m-flash" ${fw ? "" : "disabled"}>Flash board</button><button class="ghost" id="m-scan" aria-label="Scan again">&#8635;</button></div>
    ${fw ? "" : `<p class="hint">No compiled firmware in this file.</p>`}
    <pre id="m-log" hidden></pre>`;
  const logOut = (text, ok) => {
    const el = $("m-log"); el.hidden = false;
    el.className = ok === null ? "" : ok ? "ok" : "err"; el.textContent = text;
  };
  const needId = async () => {
    if (buildId) return buildId;
    const r = await fetch("/api/vbuild", { method: "POST", body: rawFile });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Upload failed");
    return (buildId = data.id);
  };
  const scan = async () => {
    const sel = $("m-port");
    try {
      const m = await api("/api/machines");
      const opts = board.method === "uf2-drive"
        ? m.uf2.map((d) => [d.path, `${d.path} ${d.board}`])
        : m.serial.map((p) => [p.device, `${p.device} ${p.bridge || p.description}`]);
      sel.innerHTML = opts.length ? opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")
        : `<option value="">${board.method === "uf2-drive" ? "No board in BOOTSEL mode" : "No board connected"}</option>`;
    } catch (e) { sel.innerHTML = `<option value="">${esc(e.message)}</option>`; }
  };
  const run = async (btn, path, body) => {
    btn.disabled = true;
    logOut("Working…", null);
    try {
      const r = await api(path, { method: "POST", body: JSON.stringify({ build: await needId(), ...body }) });
      logOut(r.log, r.ok);
      if (r.ok && path.endsWith("flash")) { go(steps.findIndex((s) => s.action === "flash")); setPlaying(true); }
    } catch (e) { logOut(e.message, false); }
    btn.disabled = false;
  };
  $("m-scan").onclick = scan;
  $("m-print").onclick = (e) => run(e.target, "/api/machines/print", { cover });
  if (fdm) $("m-slicer").onclick = (e) => run(e.target, "/api/machines/print", { cover, open_in_slicer: true });
  $("m-flash").onclick = (e) => run(e.target, "/api/machines/flash", { target: $("m-port").value });
  scan();
}

// --- opening files -------------------------------------------------------------------
let rawFile = null;

function openBytes(bytes) {
  const entries = unzipSync(bytes);
  if (!entries["manifest.json"]) throw new Error("Not a .vbuild file (no manifest.json).");
  const manifest = JSON.parse(strFromU8(entries["manifest.json"]));
  if (manifest.format !== "vbuild") throw new Error("Not a .vbuild file.");
  delete entries["manifest.json"];
  return show(manifest, entries);
}

async function openFile(f) {
  try {
    rawFile = new Uint8Array(await f.arrayBuffer());
    buildId = null;
    $("download").hidden = true;
    await openBytes(rawFile);
    setPlaying(!reduceMotion);
  } catch (e) { alert(e.message); }
}

$("open").onclick = $("open2").onclick = () => $("file").click();
$("file").onchange = (e) => e.target.files[0] && openFile(e.target.files[0]);
stage.addEventListener("dragover", (e) => { e.preventDefault(); $("drop").classList.add("over"); });
stage.addEventListener("dragleave", () => $("drop").classList.remove("over"));
stage.addEventListener("drop", (e) => {
  e.preventDefault(); $("drop").classList.remove("over");
  if (e.dataTransfer.files[0]) openFile(e.dataTransfer.files[0]);
});

(async () => {
  resize();
  status = await fetch("/api/status").then((r) => r.json()).catch(() => ({ desktop: false, static: true }));
  if (status.static) $("home").textContent = "V-Hbuild home";   // no V-Hbuild server: the link goes to the site
  const src = params.get("src");
  if (src && !buildId) {
    // Only files from this same site: the viewer is not a proxy for arbitrary URLs.
    try {
      const url = new URL(src, location.href);
      if (url.origin !== location.origin) throw new Error("Examples must come from this site.");
      const r = await fetch(url);
      if (!r.ok) throw new Error(`Could not load ${url.pathname} (${r.status}).`);
      rawFile = new Uint8Array(await r.arrayBuffer());
      $("download").href = url.pathname;
      $("download").setAttribute("download", url.pathname.split("/").pop());
      $("download").hidden = false;
      await openBytes(rawFile);
    } catch (e) {
      $("drop").querySelector("b").textContent = e.message;
    }
  }
  if (buildId) {
    try {
      const r = await fetch(`/api/build/${buildId}.vbuild`);
      if (!r.ok) throw new Error((await r.json()).detail);
      rawFile = new Uint8Array(await r.arrayBuffer());
      $("download").href = `/api/build/${buildId}.vbuild`;
      $("download").hidden = false;
      await openBytes(rawFile);
    } catch (e) {
      $("drop").querySelector("b").textContent = e.message;
    }
  }
})();

window.vhbuildViewer = {
  go: (i) => go(i, true), pause: () => setPlaying(false), cover: (id) => pickCover(id, false),
  at: (i, k) => { cur = Math.max(0, Math.min(i, steps.length - 1)); t = k; apply(); },
  state: () => ({ cur, t, steps: steps.length, cover, covers: Object.keys(man?.covers || {}) }),
};
