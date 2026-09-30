// Forge viewer: plays a .vbuild's assembly steps over its 3D model.
//
// The GLB has one node per part (named by ref, plus "shell" and "lid"); the
// manifest says which step reveals which node and wire. Each step animates:
// the shell prints layer by layer, parts drop into place, wires draw
// themselves from part to pin, the board pulses while it is flashed, the lid
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
try { if (token) sessionStorage.setItem("forge-token", token); else token = sessionStorage.getItem("forge-token") || ""; } catch {}

const DURATION = { print: 3.0, flash: 2.6, close: 1.4, default: 1.6 };
const HOLD = 0.9;   // seconds to rest on a finished step while playing

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
let nodes = {};          // name -> { obj, base: Vector3, step }
let wires = [];          // { mesh, step, count }
let cur = 0, t = 0, playing = !reduceMotion, hold = 0, printPlane = null, root = null;

function clearScene() {
  if (root) scene.remove(root);
  wires.forEach((w) => scene.remove(w.mesh));
  nodes = {}; wires = []; root = null;
}

function frame(object, outer) {
  // Frame everything, including probes and pumps that sit outside the box.
  const bounds = new THREE.Box3().setFromObject(object);
  const c = bounds.getCenter(new THREE.Vector3());
  const s = bounds.getSize(new THREE.Vector3());
  const size = Math.max(s.x, s.y, outer[2]);
  controls.target.set(c.x, c.y, outer[2] / 3);
  // High enough to see down into the open box.
  camera.position.set(c.x + size * 0.35, c.y - size * 0.95, outer[2] + size * 1.25);
  sun.position.set(c.x + size * 2, c.y - size * 1.5, size * 3);
  const extent = Math.ceil(Math.max(s.x, s.y) * 1.8 / 10) * 10;
  const grid = new THREE.GridHelper(extent, extent / 10, 0x999999, 0xbbbbbb);
  grid.rotation.x = Math.PI / 2;
  grid.position.set(c.x, c.y, -0.05);
  grid.material.transparent = true; grid.material.opacity = 0.35;
  root.add(grid);
}

function tube(points, color) {
  const path = new THREE.CurvePath();
  for (let i = 0; i < points.length - 1; i++) {
    path.add(new THREE.LineCurve3(new THREE.Vector3(...points[i]), new THREE.Vector3(...points[i + 1])));
  }
  const geo = new THREE.TubeGeometry(path, 90, 0.55, 6, false);
  const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color, roughness: 0.5 }));
  return { mesh, count: geo.index.count };
}

async function show(manifest, fileMap) {
  man = manifest; files = fileMap;
  clearScene();
  $("drop").hidden = true;
  $("overlay").hidden = false;
  $("title").textContent = man.name;
  $("sub").textContent = `${man.board?.name || ""} · planned by ${man.planner || "Forge"}`;
  document.title = `${man.name} · Forge`;

  const glb = files[man.model];
  if (!glb) throw new Error("This .vbuild has no 3D model.");
  const gltf = await new GLTFLoader().parseAsync(glb.buffer.slice(glb.byteOffset, glb.byteOffset + glb.byteLength), "");
  root = new THREE.Group();
  root.add(gltf.scene);
  scene.add(root);

  // Which step first shows each node / wire.
  const firstStep = {}, wireStep = {};
  man.steps.forEach((s, i) => {
    (s.show || []).forEach((n) => { if (!(n in firstStep)) firstStep[n] = i; });
    (s.wires || []).forEach((r) => { if (!(r in wireStep)) wireStep[r] = i; });
  });
  const [, , Ho] = man.box.outer;
  printPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), Ho + 1);
  for (const name of ["shell", "lid", ...Object.keys(man.nodes)]) {
    const obj = gltf.scene.getObjectByName(name);
    if (!obj) continue;
    obj.traverse((m) => {
      if (m.isMesh) {
        m.material = m.material.clone();
        m.material.userData.baseOpacity = m.material.opacity;
        if (name === "shell") m.material.clippingPlanes = [printPlane];
      }
    });
    nodes[name] = { obj, base: obj.position.clone(), step: firstStep[name] ?? 0 };
  }
  for (const w of man.wires || []) {
    const { mesh, count } = tube(w.points, w.color);
    mesh.visible = false;
    scene.add(mesh);
    wires.push({ mesh, count, step: wireStep[w.ref] ?? 0, ref: w.ref });
  }
  frame(gltf.scene, man.box.outer);
  renderSteps();
  renderMachines();
  go(0);
}

// --- timeline ----------------------------------------------------------------------
const ease = (x) => 1 - Math.pow(1 - Math.min(Math.max(x, 0), 1), 3);

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

function apply() {
  if (!man) return;
  const step = man.steps[cur] || {};
  const Ho = man.box.outer[2];
  for (const [name, n] of Object.entries(nodes)) {
    const { obj, base } = n;
    obj.position.copy(base);
    setOpacity(obj, 1);
    if (n.step > cur) { obj.visible = false; continue; }
    obj.visible = true;
    if (n.step < cur) continue;
    // Revealed by the current step: animate it in.
    if (step.action === "print" && name === "shell") {
      printPlane.constant = Ho * ease(t) + 0.01;
    } else if (step.action === "close" && name === "lid") {
      obj.position.z = base.z + (1 - ease(t)) * 70;
      setOpacity(obj, Math.min(1, t * 2));
    } else {
      obj.position.z = base.z + (1 - ease(t * 1.4)) * 55;
      setOpacity(obj, Math.min(1, t * 3));
    }
  }
  if (step.action !== "print" && printPlane) printPlane.constant = Ho + 1;
  for (const w of wires) {
    w.mesh.visible = w.step <= cur;
    const k = w.step < cur ? 1 : ease((t - 0.45) / 0.55);
    w.mesh.geometry.setDrawRange(0, Math.floor(w.count * k / 3) * 3);
  }
  const board = nodes.U1?.obj;
  if (board) glow(board, step.action === "flash" ? (0.5 + 0.5 * Math.sin(t * Math.PI * 10)) * (1 - t * 0.6) : 0);

  $("cap-title").textContent = step.title || "";
  $("cap-text").textContent = step.text || "";
  const last = cur === man.steps.length - 1 && t >= 1;
  $("badge").textContent = last ? "Built" : `Step ${cur + 1} of ${man.steps.length}`;
  $("badge").className = last ? "ok" : "";
  [...$("steps").children].forEach((li, i) => {
    li.classList.toggle("cur", i === cur);
    li.classList.toggle("done", i < cur || (i === cur && t >= 1));
  });
}

function go(i, instant = false) {
  cur = Math.max(0, Math.min(i, man.steps.length - 1));
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
    const step = man.steps[cur];
    if (t < 1) {
      t = Math.min(1, t + dt / (DURATION[step.action] || DURATION.default));
      apply();
    } else if (cur < man.steps.length - 1) {
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
  man.steps.forEach((s, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="n">${i + 1}</span><span></span>`;
    li.lastChild.textContent = s.title;
    li.onclick = () => { setPlaying(false); go(i, true); };
    ol.appendChild(li);
  });
}

$("play").onclick = () => {
  if (!man) return;
  if (!playing && cur === man.steps.length - 1 && t >= 1) go(0);
  setPlaying(!playing);
};
$("prev").onclick = () => { if (man) { setPlaying(false); go(cur - 1, true); } };
$("next").onclick = () => { if (man) { setPlaying(false); go(cur + 1, true); } };
$("restart").onclick = () => { if (man) { go(0); setPlaying(true); } };

// --- machines --------------------------------------------------------------------
let status = { desktop: false };

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-Forge-Token": token, ...(opts.headers || {}) } });
  const data = await r.json().catch(() => ({}));
  if (!r.ok && data.log === undefined) throw new Error(data.detail || r.statusText);
  return data;
}

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

async function renderMachines() {
  const box = $("machines");
  box.hidden = false;
  const board = man.machines?.board || {};
  const fw = board.file;
  if (!status.desktop) {
    // Downloads come straight out of the loaded file, so this works on a
    // static host with no Forge server behind it.
    box.innerHTML = `<h2>Your machines</h2><div class="row" id="m-files"></div>
      <p class="hint">Open this .vbuild in the Forge desktop app to print the enclosure and flash the board directly.</p>`;
    const add = (p, label) => {
      if (!files[p]) return;
      const a = document.createElement("a");
      a.className = "btn ghost"; a.textContent = label;
      a.download = p.split("/").pop();
      a.href = URL.createObjectURL(new Blob([files[p]]));
      $("m-files").appendChild(a);
    };
    add("enclosure/print_plate.3mf", "Printer file (.3mf)");
    if (fw) add(fw, "Board firmware");
    return;
  }
  box.innerHTML = `<h2>Your machines</h2>
    <div class="row"><button id="m-print">Print enclosure</button><button class="ghost" id="m-slicer">Open in my slicer</button></div>
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
      if (r.ok && path.endsWith("flash")) { go(man.steps.findIndex((s) => s.action === "flash")); setPlaying(true); }
    } catch (e) { logOut(e.message, false); }
    btn.disabled = false;
  };
  $("m-scan").onclick = scan;
  $("m-print").onclick = (e) => run(e.target, "/api/machines/print", {});
  $("m-slicer").onclick = (e) => run(e.target, "/api/machines/print", { open_in_slicer: true });
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
  if (status.static) $("home").textContent = "Forge home";   // no Forge server: the link goes to the site
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

window.forgeViewer = { go: (i) => go(i, true), pause: () => setPlaying(false), state: () => ({ cur, t, steps: man?.steps.length }) };
