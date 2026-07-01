import { CONFIG } from "./config.js";
import { RovScene } from "./scene.js";
import { setServices, pilotAxes, snapshotImage, createRecorder, makeFullscreen } from "./core.js";
import { telemetryPage } from "./pages/telemetry.js";
import { missionPage } from "./pages/mission.js";
import { cameraPage } from "./pages/camera.js";
import { setupPage, loadSetup } from "./pages/setup.js";

/*  elemen DOM  */
const $ = (id) => document.getElementById(id);
const els = {
  link: $("linkPill"), linkLabel: $("linkLabel"),
  heading: $("vHeading"), depth: $("vDepth"), alt: $("vAlt"), roll: $("vRoll"),
  pitch: $("vPitch"), temp: $("vTemp"), volt: $("vVolt"), lat: $("vLat"),
  identTeam: $("identTeam"), identUni: $("identUni"),
  clockDate: $("clockDate"), clockTime: $("clockTime"),
  hudHeading: $("hudHeading"), hudRoll: $("hudRoll"), hudPitch: $("hudPitch"),
  miniCompass: $("miniCompass"), miniCompassNeedle: $("miniCompassNeedle"),
  miniCompassDir: $("miniCompassDir"), miniCompassValue: $("miniCompassValue"),
  camRes: $("camRes"), camRecIndicator: $("camRecIndicator"),
  tapeScale: $("tapeScale"), tapeVal: $("tapeVal"),
  camImg: $("camImg"), camNoSignal: $("camNoSignal"), camTag: $("camTag"),
  modelTag: $("modelTag"), log: $("log"),
  btnLight: $("btnLight"), btnArm: $("btnArm"), btnStop: $("btnStop"),
  armLabel: $("armLabel"),
  btnMode: $("btnMode"), modeLabel: $("modeLabel"), btnMute: $("btnMute"),
  btnSnap: $("btnSnap"), btnRec: $("btnRec"), btnHud: $("btnHud"),
  camStage: $("camStage"), btnCamFull: $("btnCamFull"), camFullLabel: $("camFullLabel"),
  pilotPanel: $("pilotPanel"), btnPilotFull: $("btnPilotFull"), pilotFullLabel: $("pilotFullLabel"),
  pilotPipImg: $("pilotPipImg"), pilotPipNo: $("pilotPipNo"),
  ctrlTitle: $("ctrlTitle"), ctrlBadge: $("ctrlBadge"),
  axSurge: $("axSurge"), axSway: $("axSway"), axYaw: $("axYaw"), axVert: $("axVert"),
  btnGripOpen: $("btnGripOpen"), btnGripClose: $("btnGripClose"),
};

/* ====================== PAGE NAVIGATION ====================== */
const pages = {
  control: $("page-control"),
  camera: $("page-camera"),
  mission: $("page-mission"),
  telemetry: $("page-telemetry"),
  setup: $("page-setup"),
};

const navLinks = document.querySelectorAll(".sidebar__link");

// modul per-halaman (Control tidak punya modul; logikanya inline di app.js)
const pageModules = {
  camera: cameraPage,
  mission: missionPage,
  telemetry: telemetryPage,
  setup: setupPage,
};
const initedModules = new Set();
let activeModule = null;

function showPage(pageName) {
  // Hide all pages
  Object.values(pages).forEach(page => {
    if (page) page.style.display = "none";
  });

  // Show selected page
  if (pages[pageName]) {
    pages[pageName].style.display = "grid";
  }

  // Update nav highlight
  navLinks.forEach(link => {
    const linkPage = link.getAttribute("data-page");
    if (linkPage === pageName) {
      link.classList.add("sidebar__link--active");
    } else {
      link.classList.remove("sidebar__link--active");
    }
  });

  // Store current page
  sessionStorage.setItem("current-page", pageName);

  // Hentikan render-loop halaman sebelumnya, init lazy + tampilkan yang baru
  if (activeModule && activeModule.onHide) { try { activeModule.onHide(); } catch (e) {} }
  activeModule = null;
  const mod = pageModules[pageName];
  if (mod) {
    if (!initedModules.has(pageName)) {
      try { mod.init(pages[pageName]); initedModules.add(pageName); }
      catch (e) { console.error(`init ${pageName} gagal`, e); log(`Gagal inisialisasi halaman ${pageName}`, "err"); }
    }
    if (initedModules.has(pageName)) { activeModule = mod; if (mod.onShow) mod.onShow(); }
  }
}

// Initialize page navigation
navLinks.forEach(link => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    const pageName = link.getAttribute("data-page");
    showPage(pageName);
  });
});

// Restore last visited page on load
window.addEventListener("load", () => {
  const savedPage = sessionStorage.getItem("current-page") || "control";
  showPage(savedPage);
});

/*  scene 3D  */
let scene = null;

function initScene() {
  if (!scene && $("stage")) {
    scene = new RovScene($("stage"));
    if (CONFIG.MODEL_URL) scene.loadModel(CONFIG.MODEL_URL, (t) => (els.modelTag.textContent = t));
  }
}

/*  console log  */
function log(msg, level = "") {
  const li = document.createElement("li");
  const t = new Date().toLocaleTimeString("id-ID", { hour12: false });
  li.innerHTML = `<time>${t}</time><span class="lv-${level}">${msg}</span>`;
  els.log.prepend(li);
  while (els.log.children.length > 80) els.log.lastChild.remove();
}

/*  state UI  */
const state = { light: false, armed: false, hud: true, recording: false };

function setLink(mode) {
  els.link.dataset.state = mode;
  els.linkLabel.textContent =
    mode === "on" ? "ONLINE" : mode === "demo" ? "SIMULASI"
    : mode === "stale" ? "SINYAL HILANG" : "OFFLINE";
}

function setTheme(name) {
  document.body.dataset.theme = name;
  localStorage.setItem("hydroship-theme", name);
}

function loadTheme() {
  const saved = localStorage.getItem("hydroship-theme");
  setTheme(saved === "light" ? "light" : "dark");
}

function num(v, d = 1) {
  return (v === null || v === undefined || Number.isNaN(v)) ? "—" : v.toFixed(d);
}

/*  depth tape  */
function buildTape() {
  const frag = document.createDocumentFragment();
  for (let m = -1; m <= 12; m++) {
    const mark = document.createElement("div");
    mark.className = "tape__mark" + (m % 1 === 0 ? " tape__mark--major" : "");
    mark.dataset.m = m;
    mark.textContent = m >= 0 ? m.toFixed(0) + " m" : "";
    frag.appendChild(mark);
  }
  els.tapeScale.appendChild(frag);
}
buildTape();
const PX_PER_M = 48;
function updateTape(depth) {
  const h = els.tapeScale.parentElement.clientHeight;
  els.tapeScale.querySelectorAll(".tape__mark").forEach((el) => {
    const m = parseFloat(el.dataset.m);
    el.style.top = (h / 2 + (m - depth) * PX_PER_M) + "px";
  });
  els.tapeVal.textContent = num(depth, 2) + " m";
}

/*  render telemetri  */
let lastTelemetry = 0;
function applyTelemetry(d) {
  const isDemo = !!d.__demo;
  // jika ini telemetry nyata (bukan simulasi) dan simulator sedang berjalan,
  // hentikan simulator agar data nyata tampil konsisten
  if (!isDemo && demo) {
    stopDemo();
    setLink("on");
    log("Telemetri nyata diterima — hentikan simulasi", "ok");
  }
  // pulih dari kondisi "stale" (telemetri sempat berhenti lalu masuk lagi)
  if (linkStale) {
    linkStale = false;
    if (!isDemo && !demo) { setLink("on"); log("Telemetri pulih", "ok"); }
  }
  lastTelemetry = performance.now();
  els.heading.textContent = num(d.heading, 0);
  els.depth.textContent = num(d.depth, 2);
  // altitude = ketinggian ROV (titik tengah) di atas dasar kolam
  if (els.alt) {
    const alt = Number.isFinite(d.depth) ? Math.max(0, CONFIG.POOL_DEPTH - d.depth) : null;
    els.alt.textContent = num(alt, 2);
  }
  els.roll.textContent = num(d.roll, 1);
  els.pitch.textContent = num(d.pitch, 1);
  els.temp.textContent = num(d.temp, 1);
  els.volt.textContent = num(d.voltage, 1);

  const heading = Number.isFinite(d.heading) ? ((d.heading % 360) + 360) % 360 : null;
  els.hudHeading.textContent = "HDG " + num(d.heading, 0) + "°";
  els.hudRoll.textContent = "R " + num(d.roll, 0) + "°";
  els.hudPitch.textContent = "P " + num(d.pitch, 0) + "°";

  // Compass needle direction was inverted; add 180° offset so needle
  // points to the model's forward direction correctly.
  if (heading !== null && els.miniCompassNeedle) {
    const displayH = (heading + 180) % 360; // flip
    els.miniCompassNeedle.style.transform = `rotate(${displayH}deg)`;
    const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    els.miniCompassDir.textContent = dirs[Math.floor((displayH + 22.5) / 45) % 8];
    els.miniCompassValue.textContent = `${Math.round(displayH)}°`;
  }

  if (scene) scene.setAttitude(d.roll, d.pitch, d.heading);
  updateTape(d.depth || 0);

  // alarm kedalaman berbahaya + kedip readout depth
  const danger = Number.isFinite(d.depth) && d.depth >= CONFIG.DANGER_DEPTH;
  depthAlarm(danger);
  els.depth.parentElement.classList.toggle("readout--danger", danger);

  // auto data-logging (saat autonomous + armed)
  if (autoCap.logging && Number.isFinite(d.depth)) {
    const alt = Math.max(0, CONFIG.POOL_DEPTH - d.depth);
    autoCap.rows.push([Date.now(), num(d.heading, 0), num(d.depth, 3), num(alt, 3), num(d.roll, 2), num(d.pitch, 2)].join(","));
  }

  if (typeof d.armed === "boolean") confirmArm(d.armed);
  if (typeof d.light === "boolean") confirmLight(d.light);

  // teruskan sampel ke modul halaman yang sudah di-init (buffering murah;
  // render sebenarnya digerbang oleh onShow/onHide)
  for (const name of initedModules) {
    const m = pageModules[name];
    if (m && m.onTelemetry) { try { m.onTelemetry(d); } catch (e) {} }
  }
}

function reflectArm(on) {
  const changed = state.armed !== on;
  state.armed = on;
  els.btnArm.setAttribute("aria-pressed", String(on));
  els.armLabel.textContent = on ? "ARMED" : "DISARMED";
  if (changed && typeof updateAutoCapture === "function") updateAutoCapture();
}
function reflectLight(on) {
  state.light = on;
  els.btnLight.setAttribute("aria-pressed", String(on));
}

/* ARM/LIGHT: UI dibalik optimistik saat diklik lalu ditandai "pending" sampai
   telemetri ROV mengonfirmasi. Jika ROV menolak (nilai beda) atau tak pernah
   meng-echo status dalam 2 dtk, operator diberi tahu agar tidak salah baca. */
const pending = {
  arm:   { active: false, expected: false, since: 0, btn: els.btnArm,   label: "ARM" },
  light: { active: false, expected: false, since: 0, btn: els.btnLight, label: "LIGHT" },
};
function markPending(key, expected) {
  const p = pending[key];
  p.active = true; p.expected = expected; p.since = performance.now();
  p.btn.classList.add("ctrl--pending");
}
function clearPending(key) {
  const p = pending[key];
  p.active = false; p.btn.classList.remove("ctrl--pending");
}
function confirmArm(on) {
  if (pending.arm.active) {
    if (on !== pending.arm.expected) log("ROV menolak/override ARM — sinkron ke status ROV", "warn");
    clearPending("arm");
  }
  reflectArm(on);
}
function confirmLight(on) {
  if (pending.light.active) {
    if (on !== pending.light.expected) log("ROV menolak/override LIGHT — sinkron ke status ROV", "warn");
    clearPending("light");
  }
  reflectLight(on);
}
// watchdog: bila konfirmasi tak datang, hentikan indikator pending + peringatkan
setInterval(() => {
  const now = performance.now();
  for (const key of ["arm", "light"]) {
    const p = pending[key];
    if (p.active && now - p.since > 2000) {
      clearPending(key);
      log(`Status ${p.label} belum dikonfirmasi ROV`, "warn");
    }
  }
}, 500);

/*  WebSocket  */
let ws = null, demo = null, pingT = 0, linkStale = false;
function connect() {
  try { ws = new WebSocket(CONFIG.WS_URL); }
  catch (e) { log("WS gagal dibuat", "err"); return scheduleReconnect(); }

  ws.onopen = () => {
    linkStale = false;
    setLink("on"); log("Terhubung ke server", "ok"); stopDemo();
    sendPing();
  };
  ws.onclose = () => { linkStale = false; setLink("off"); scheduleReconnect(); maybeDemo(); };
  ws.onerror = () => { log("Error koneksi WS", "err"); };
  ws.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "telemetry") applyTelemetry(msg.data);
    else if (msg.type === "pong") setLatency(performance.now() - msg.t);
    else if (msg.type === "event") log(msg.text, msg.level || "");
  };
}
let reconnectTimer = null;
function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 1500);
}
function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}
function sendCmd(name, value) {
  send({ type: "cmd", name, value });
  log(`CMD ${name} = ${value}`);
}

// sediakan log & sendCmd untuk modul halaman
setServices({ log, sendCmd });
function setLatency(ms) { els.lat.textContent = Math.round(ms); }

// ping berkala untuk ukur latency
setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) sendPing(); }, 1000);
function sendPing() { pingT = performance.now(); send({ type: "ping", t: pingT }); }

// deteksi link mati (telemetri berhenti) walau WS masih open.
// edge-triggered: hanya sekali saat transisi ke "stale" (tak spam tiap detik).
setInterval(() => {
  const online = ws && ws.readyState === WebSocket.OPEN;
  const stalled = performance.now() - lastTelemetry > 2500;
  if (online && stalled && !linkStale && !demo) {
    linkStale = true;
    setLink("stale");
    log("Telemetri terputus (timeout)", "warn");
    if (CONFIG.DEMO_ON_START && !demo) startDemo();
  }
}, 1000);

/*  simulator  */
function startDemo() {
  if (demo) return;
  setLink("demo");
  log("Mode simulasi aktif", "warn");
  let t = 0;
  demo = setInterval(() => {
    t += 0.05;
    applyTelemetry({
      heading: (90 + 40 * Math.sin(t * 0.2) + 360) % 360,
      depth: 2.5 + 1.5 * Math.sin(t * 0.15),
      roll: 8 * Math.sin(t * 0.7),
      pitch: 6 * Math.sin(t * 0.5 + 1),
      temp: 26 + Math.sin(t * 0.05),
      voltage: 15.6 + 0.2 * Math.sin(t),
      armed: state.armed, light: state.light,
      __demo: true,
    });
    setLatency(2 + Math.random() * 3);
  }, 50);
}
function stopDemo() {
  if (!demo) return;
  clearInterval(demo); demo = null;
}
function maybeDemo() { if (CONFIG.DEMO_ON_START && !demo) startDemo(); }

/*  kamera  */
// crossOrigin wajib agar snapshot/record (canvas) tidak ter-taint oleh stream
// MJPEG lintas-asal dari Raspi; onload/onerror dipasang sekali di sini.
els.camImg.crossOrigin = "anonymous";
els.camImg.onload = () => {
  els.camNoSignal.style.display = "none";
  els.camTag.textContent = "LIVE";
  try {
    const w = els.camImg.naturalWidth || els.camImg.width;
    const h = els.camImg.naturalHeight || els.camImg.height;
    if (els.camRes) els.camRes.textContent = `${w}×${h}`;
  } catch (e) {}
};
els.camImg.onerror = () => { els.camNoSignal.style.display = "flex"; els.camTag.textContent = "RTSP / MJPEG"; };

// (re)arahkan feed kamera Control ke CONFIG.CAMERA_URL saat ini. Dipanggil di
// awal dan setiap URL diubah (Setup/Camera) via event 'hydroship:camera-url'.
function applyControlCamera() {
  const url = CONFIG.CAMERA_URL;
  if (!url) {
    els.camImg.removeAttribute("src");
    els.camNoSignal.style.display = "flex";
    els.camTag.textContent = "RTSP / MJPEG";
    if (els.camRes) els.camRes.textContent = "—";
    return;
  }
  // bust cache agar re-apply URL sama tetap memicu load ulang
  els.camImg.src = url + (url.includes("?") ? "&" : "?") + "_t=" + Date.now();
}
applyControlCamera();
window.addEventListener("hydroship:camera-url", applyControlCamera);

/*  kontrol UI  */
els.btnLight.onclick = () => { const v = !state.light; reflectLight(v); markPending("light", v); sendCmd("light", v); };
els.btnArm.onclick = () => { const v = !state.armed; reflectArm(v); markPending("arm", v); sendCmd("arm", v); };
els.btnStop.onclick = () => {
  sendCmd("stop", true); reflectArm(false); markPending("arm", false);
  ["surge", "sway", "yaw", "vert"].forEach((a) => setAxis(a, 0));
  log("⏹ STOP — semua thruster netral", "err");
};

els.btnHud.onclick = () => {
  state.hud = !state.hud;
  els.btnHud.setAttribute("aria-pressed", String(state.hud));
  document.querySelector(".hud").style.display = state.hud ? "flex" : "none";
};
/* snapshot: download current frame (pakai util bersama core.js) */
function captureSnapshot() {
  if (!snapshotImage(els.camImg)) { log("Tidak ada frame untuk snapshot", "warn"); return; }
  log("Snapshot diambil", "ok");
  sendCmd("snapshot", true);
}

/* recording: rekam frame kamera ke WebM (util bersama core.js) */
let controlRecorder = null;
function startRecording() {
  controlRecorder = createRecorder(els.camImg);
  if (!controlRecorder.start()) { controlRecorder = null; log("Tidak ada frame untuk merekam", "warn"); return; }
  if (els.camRecIndicator) els.camRecIndicator.classList.add('active');
  log('Perekaman dimulai', 'ok');
  sendCmd('record', true);
}

function stopRecording() {
  if (controlRecorder) { controlRecorder.stop(); controlRecorder = null; }
  if (els.camRecIndicator) els.camRecIndicator.classList.remove('active');
  log('Perekaman berhenti', 'warn');
  sendCmd('record', false);
}

els.btnSnap.onclick = captureSnapshot;
els.btnRec.onclick = () => {
  state.recording = !state.recording;
  els.btnRec.setAttribute('aria-pressed', String(state.recording));
  els.btnRec.textContent = state.recording ? 'REC ●' : 'REC';
  if (state.recording) startRecording(); else stopRecording();
};

/* ====================== PILOT VIEWPORT ====================== */

/* mini live-stream (PiP) di sudut viewport saat fullscreen */
els.pilotPipImg.onerror = () => { els.pilotPipImg.style.display = "none"; els.pilotPipNo.style.display = "flex"; };
els.pilotPipImg.onload = () => { els.pilotPipImg.style.display = ""; els.pilotPipNo.style.display = "none"; };
function setPilotPip(on) {
  if (on && CONFIG.CAMERA_URL) {
    els.pilotPipImg.src = CONFIG.CAMERA_URL;       // umpan kamera live
  } else {
    els.pilotPipImg.removeAttribute("src");         // hentikan muat saat keluar / tanpa kamera
    els.pilotPipImg.style.display = "none";
    els.pilotPipNo.style.display = on ? "flex" : "none";
  }
}

/* Full Screen toggle for the digital twin viewport (native + fallback CSS) */
const pilotFs = makeFullscreen(els.pilotPanel, {
  onToggle: (fs) => {
    els.pilotFullLabel.textContent = fs ? "Exit Full" : "Full Screen";
    els.btnPilotFull.setAttribute("aria-pressed", String(fs));
    setPilotPip(fs);
    // beri waktu layout settle, lalu picu resize agar canvas 3D mengikuti
    setTimeout(() => window.dispatchEvent(new Event("resize")), 80);
  },
});
els.btnPilotFull.onclick = () => pilotFs.toggle();

/* mirror viewport pilot/digital-twin ke PiP saat kamera Control fullscreen */
let pilotMirrorRaf = null;
function pilotMirror(on) {
  const cv = document.getElementById("ctrlCamPipCanvas");
  if (on) {
    if (!cv || !scene) return;
    const ctx = cv.getContext("2d");
    const src = scene.renderer.domElement;
    const loop = () => {
      pilotMirrorRaf = requestAnimationFrame(loop);
      const w = cv.clientWidth, h = cv.clientHeight;
      if (!w || !h) return;
      if (cv.width !== w) cv.width = w;
      if (cv.height !== h) cv.height = h;
      try { ctx.drawImage(src, 0, 0, cv.width, cv.height); } catch (e) {}
    };
    loop();
  } else if (pilotMirrorRaf) {
    cancelAnimationFrame(pilotMirrorRaf);
    pilotMirrorRaf = null;
  }
}

/* Full Screen toggle untuk LIVE CAMERA di halaman Control */
const camFs = makeFullscreen(els.camStage, {
  onToggle: (fs) => {
    els.camFullLabel.textContent = fs ? "Exit Full" : "Full Screen";
    els.btnCamFull.setAttribute("aria-pressed", String(fs));
    pilotMirror(fs);
  },
});
els.btnCamFull.onclick = () => camFs.toggle();

/* pilot mode tabs: Standby | Dry Cal | Manual | Hold */
let pilotMode = "manual";
document.querySelectorAll("#modeBar .mode").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll("#modeBar .mode").forEach((b) => {
      b.classList.remove("mode--active");
      b.removeAttribute("aria-selected");
    });
    btn.classList.add("mode--active");
    btn.setAttribute("aria-selected", "true");
    pilotMode = btn.dataset.mode;
    sendCmd("mode", pilotMode);
    log(`Mode pilot: ${btn.textContent}`, "ok");
  };
});

/* controller tabs: Keyboard | Gamepad | Meta Quest */
let activeController = "Keyboard";
document.querySelectorAll(".ctab").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".ctab").forEach((b) => {
      b.classList.remove("ctab--active");
      b.removeAttribute("aria-selected");
    });
    btn.classList.add("ctab--active");
    btn.setAttribute("aria-selected", "true");
    activeController = btn.dataset.ctl;
    els.ctrlTitle.textContent = activeController;
    els.ctrlBadge.textContent = "Active: " + activeController;
    sendCmd("controller", activeController);
    log(`Controller: ${activeController}`, "");
  };
});

/* axis fields: Surge | Sway | Yaw | Vertical */
const axisEls = { surge: els.axSurge, sway: els.axSway, yaw: els.axYaw, vert: els.axVert };
function setAxis(name, value, live = false) {
  const el = axisEls[name];
  if (!el) return;
  el.value = String(value);
  el.classList.toggle("axis--live", live && value !== 0);
  if (name in pilotAxes) pilotAxes[name] = Number(value) || 0;
}
Object.entries(axisEls).forEach(([name, el]) => {
  el.addEventListener("change", () => {
    const v = Number(el.value) || 0;
    el.value = String(v);
    if (name in pilotAxes) pilotAxes[name] = v;
    sendCmd(name, v);
  });
});

/* keyboard piloting (hanya saat controller = Keyboard):
   W/S surge · A/D sway · Q/E yaw · R/F vertical — tahan untuk ±50, lepas untuk 0 */
const KEY_AXIS = {
  KeyW: ["surge", 50], KeyS: ["surge", -50],
  KeyD: ["sway", 50], KeyA: ["sway", -50],
  KeyE: ["yaw", 50], KeyQ: ["yaw", -50],
  KeyR: ["vert", 50], KeyF: ["vert", -50],
};
const heldKeys = new Set();
function pilotKeyActive(e) {
  return activeController === "Keyboard" && e.target === document.body && KEY_AXIS[e.code];
}
window.addEventListener("keydown", (e) => {
  if (!pilotKeyActive(e) || heldKeys.has(e.code)) return;
  heldKeys.add(e.code);
  const [axis, val] = KEY_AXIS[e.code];
  setAxis(axis, val, true);
  sendCmd(axis, val);
});
window.addEventListener("keyup", (e) => {
  if (!KEY_AXIS[e.code] || !heldKeys.has(e.code)) return;
  heldKeys.delete(e.code);
  const [axis] = KEY_AXIS[e.code];
  setAxis(axis, 0);
  sendCmd(axis, 0);
});

/* set surface level */
$("btnSetSurface").onclick = () => {
  sendCmd("set_surface", true);
  log("Surface level diset — Depth = 0", "ok");
};

/* gripper open/close (dipakai misi 2 & 5) — tombol + keyboard H/G */
els.btnGripOpen.onclick = () => { sendCmd("gripper", "open"); log("Gripper: OPEN", "ok"); };
els.btnGripClose.onclick = () => { sendCmd("gripper", "close"); log("Gripper: CLOSE", "ok"); };
window.addEventListener("keydown", (e) => {
  if (activeController !== "Keyboard" || e.target !== document.body) return;
  if (e.code === "KeyH") { e.preventDefault(); els.btnGripOpen.click(); }
  else if (e.code === "KeyG") { e.preventDefault(); els.btnGripClose.click(); }
});

/* viewport toggles: Follow ROV | Preview AIR | Echo */
function toggleChip(id, onLabel) {
  const el = $(id);
  el.onclick = () => {
    const on = el.getAttribute("aria-pressed") !== "true";
    el.setAttribute("aria-pressed", String(on));
    log(`${onLabel}: ${on ? "ON" : "OFF"}`);
  };
}
toggleChip("btnFollow", "Follow ROV");
toggleChip("btnPreviewAir", "Preview AIR");
toggleChip("btnEcho", "Echo");

// keselamatan: tombol Spasi = STOP
window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && e.target === document.body) { e.preventDefault(); els.btnStop.click(); }
});

/* ============ identitas tim + jam (KKI 2026) ============ */
function initIdentity() {
  if (els.identTeam) els.identTeam.textContent = CONFIG.TEAM_NAME || "Nama Tim";
  if (els.identUni) els.identUni.textContent = CONFIG.UNIVERSITY || "Perguruan Tinggi";
}
function tickClock() {
  const now = new Date();
  if (els.clockDate) els.clockDate.textContent = now.toLocaleDateString("id-ID", { weekday: "long", day: "2-digit", month: "short", year: "numeric" });
  if (els.clockTime) els.clockTime.textContent = now.toLocaleTimeString("id-ID", { hour12: false });
}

/* ============ alarm audio kedalaman berbahaya ============ */
const alarm = { ctx: null, osc: null, on: false, muted: false };
function depthAlarm(active) {
  if (active && !alarm.muted) {
    if (alarm.on) return;
    try {
      alarm.ctx = alarm.ctx || new (window.AudioContext || window.webkitAudioContext)();
      const o = alarm.ctx.createOscillator(), g = alarm.ctx.createGain();
      o.type = "square"; o.frequency.value = 880;
      g.gain.value = 0.05;
      o.connect(g); g.connect(alarm.ctx.destination);
      // beep berulang via LFO sederhana
      const lfo = alarm.ctx.createOscillator(), lg = alarm.ctx.createGain();
      lfo.frequency.value = 3; lg.gain.value = 0.05;
      lfo.connect(lg); lg.connect(g.gain);
      o.start(); lfo.start();
      alarm.osc = { o, lfo };
      alarm.on = true;
    } catch (e) {}
  } else if (alarm.on) {
    try { alarm.osc.o.stop(); alarm.osc.lfo.stop(); } catch (e) {}
    alarm.osc = null; alarm.on = false;
  }
}
els.btnMute.onclick = () => {
  alarm.muted = !alarm.muted;
  els.btnMute.setAttribute("aria-pressed", String(alarm.muted));
  if (alarm.muted) depthAlarm(false);
  log(`Alarm kedalaman ${alarm.muted ? "dibisukan" : "diaktifkan"}`, alarm.muted ? "warn" : "ok");
};

/* ============ toggle Manual / Autonomous ============ */
let controlMode = "manual";
els.btnMode.onclick = () => {
  controlMode = controlMode === "manual" ? "autonomous" : "manual";
  els.modeLabel.textContent = controlMode.toUpperCase();
  els.btnMode.setAttribute("aria-pressed", String(controlMode === "autonomous"));
  sendCmd("control_mode", controlMode);
  log(`Mode kontrol: ${controlMode.toUpperCase()}`, "ok");
  updateAutoCapture();
};

/* ============ auto screenshot & data logging ============ */
const autoCap = { logging: false, rows: [], snapTimer: null };
function updateAutoCapture() {
  const shouldRun = controlMode === "autonomous" && state.armed;
  if (shouldRun && !autoCap.logging) {
    autoCap.logging = true;
    autoCap.rows = [];
    autoCap.snapTimer = setInterval(() => { snapshotImage(els.camImg, "hydroship_auto"); }, 15000);
    log("Auto-capture ON (autonomous + armed): logging + snapshot", "ok");
  } else if (!shouldRun && autoCap.logging) {
    autoCap.logging = false;
    if (autoCap.snapTimer) { clearInterval(autoCap.snapTimer); autoCap.snapTimer = null; }
    exportAutoLog();
  }
}
function exportAutoLog() {
  if (!autoCap.rows.length) return;
  const header = "timestamp,heading,depth,altitude,roll,pitch";
  const blob = new Blob([header + "\n" + autoCap.rows.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `hydroship_autolog_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  log(`Auto-log diekspor (${autoCap.rows.length} baris)`, "ok");
}

/*  mulai  */
log("HYDROSHIP dashboard siap", "ok");
loadSetup();
initIdentity();
tickClock();
setInterval(tickClock, 1000);
loadTheme();
initScene();
connect();
maybeDemo();
