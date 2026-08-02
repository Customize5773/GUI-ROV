// app.js — dashboard utama Hydroship ROV
import { CONFIG } from "./config.js";
import { RovScene } from "./scene.js";
import { setServices, pilotAxes, snapshotImage, createRecorder, makeFullscreen, camProxy } from "./core.js";
import { telemetryPage } from "./pages/telemetry.js";
import { missionPage } from "./pages/mission.js";
import { cameraPage } from "./pages/camera.js";
import { replayPage } from "./pages/replay.js";
import { setupPage, loadSetup } from "./pages/setup.js";
import { vehiclePage } from "./pages/vehicle.js";
import { analyzePage } from "./pages/analyze.js";
import { joystickPage,handleJoystickConfigMessage} from "./pages/joystick.js";
import { joystickState,updateJoystickStateFromGamepad,getActiveButtonLayerName,isJoystickUsable,} from "./joystick-state.js";
import { Manipulator } from "./manipulator/manipulator.js";
import { ACRO_CONFIRM, ARDUSUB_MODE_TO_TAB, RISKY_ARDUSUB_MODES } from "/shared/rov-modes.js";

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
  btnCamSwitch: $("btnCamSwitch"),
  camStage: $("camStage"), btnCamFull: $("btnCamFull"), camFullLabel: $("camFullLabel"),
  pilotPanel: $("pilotPanel"), btnPilotFull: $("btnPilotFull"), pilotFullLabel: $("pilotFullLabel"),
  pilotPipImg: $("pilotPipImg"), pilotPipNo: $("pilotPipNo"),
  ctrlTitle: $("ctrlTitle"), ctrlBadge: $("ctrlBadge"),
  axSurge: $("axSurge"), axSway: $("axSway"), axYaw: $("axYaw"), axHeave: $("axHeave"),
  btnGripOpen: $("btnGripOpen"), btnGripClose: $("btnGripClose"),
  mission5State: $("mission5State"), mission5Cam: $("mission5Cam"),
  mission5Z: $("mission5Z"), mission5OffX: $("mission5OffX"), mission5OffY: $("mission5OffY"),
  depthTarget: $("vDepthTarget"),
  modeActual: $("modeActual"), modeWarn: $("modeWarn"),
};

/* ====================== PAGE NAVIGATION ====================== */
const pages = {
  control: $("page-control"),
  camera: $("page-camera"),
  mission: $("page-mission"),
  telemetry: $("page-telemetry"),
  setup: $("page-setup"),
  vehicle: $("page-vehicle"),
  analyze: $("page-analyze"),
  joystick: $("page-joystick"),
  replay: $("page-replay"),
};

const navLinks = document.querySelectorAll(".sidebar__link");

// modul per-halaman (Control tidak punya modul; logikanya inline di app.js)
const pageModules = {
  camera: cameraPage,
  mission: missionPage,
  telemetry: telemetryPage,
  setup: setupPage,
  vehicle: vehiclePage,
  analyze: analyzePage,
  joystick: joystickPage,
  replay: replayPage,
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

/* Navigasi antar halaman dari dalam modul halaman (halaman Vehicle menautkan
   ke Setup & Joystick alih-alih menduplikasi form-nya). Lewat event supaya
   modul halaman tidak perlu mengimpor app.js — pola yang sama dengan
   "hydroship:pool-depth" & "hydroship:camera-url". */
window.addEventListener("hydroship:goto-page", (e) => {
  if (e.detail && pages[e.detail]) showPage(e.detail);
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

/*  depth tape — skala mengikuti kedalaman kolam (CONFIG.POOL_DEPTH) supaya
    berguna baik di kolam dangkal KKI (~0.9 m) maupun kolam uji yang lebih dalam. */
let TAPE;
function computeTape() {
  const d = CONFIG.POOL_DEPTH || 3;
  if (d <= 2) return { min: -0.2, max: d + 0.3, minor: 0.1, major: 0.5, px: 200 };
  if (d <= 5) return { min: -0.5, max: d + 0.5, minor: 0.5, major: 1,   px: 90 };
  return { min: -1, max: d + 1, minor: 1, major: 2, px: 48 };
}
function buildTape() {
  TAPE = computeTape();
  els.tapeScale.innerHTML = "";   // rebuild bersih (dipanggil ulang saat pool depth diubah)
  const frag = document.createDocumentFragment();
  const steps = Math.round((TAPE.max - TAPE.min) / TAPE.minor);
  for (let i = 0; i <= steps; i++) {
    const m = Math.round((TAPE.min + i * TAPE.minor) * 1000) / 1000;
    const isMajor = Math.abs(m / TAPE.major - Math.round(m / TAPE.major)) < 1e-6;
    const mark = document.createElement("div");
    mark.className = "tape__mark" + (isMajor ? " tape__mark--major" : "");
    mark.dataset.m = m;
    mark.textContent = (isMajor && m >= 0) ? m.toFixed(TAPE.minor < 1 ? 1 : 0) + " m" : "";
    frag.appendChild(mark);
  }
  els.tapeScale.appendChild(frag);
}
buildTape();
// rescale saat pool depth diubah di halaman Setup
window.addEventListener("hydroship:pool-depth", buildTape);
function updateTape(depth) {
  const h = els.tapeScale.parentElement.clientHeight;
  els.tapeScale.querySelectorAll(".tape__mark").forEach((el) => {
    const m = parseFloat(el.dataset.m);
    el.style.top = (h / 2 + (m - depth) * TAPE.px) + "px";
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

  // Mode pilot: satu-satunya penggerak sorotan tab adalah mode yang dilaporkan
  // Pixhawk lewat HEARTBEAT, bukan klik operator.
  if (typeof d.mode === "string") {
    if (d.mode !== lastPilotMode) {
      lastPilotMode = d.mode;
      log(`Mode pilot aktif: ${d.mode}`, RISKY_ARDUSUB_MODES.has(d.mode) ? "warn" : "ok");
    }
    syncModeTabs(d.mode);
  }

  applyMission5(d.mission5);

  // teruskan sampel ke modul halaman yang sudah di-init (buffering murah;
  // render sebenarnya digerbang oleh onShow/onHide)
  for (const name of initedModules) {
    const m = pageModules[name];
    if (m && m.onTelemetry) { try { m.onTelemetry(d); } catch (e) {} }
  }

  els.depthTarget.textContent = num(d.depth_target, 2);
}

/* panel Mission 5 (docking/unhook) — m5 = {state, active_cam, distance_z, offset_x, offset_y} */
function applyMission5(m5) {
  if (!els.mission5State) return;
  if (!m5) {
    els.mission5State.textContent = "IDLE";
    els.mission5State.className = "badge";
    els.mission5Cam.textContent = "—";
    els.mission5Z.textContent = "—";
    els.mission5OffX.textContent = "—";
    els.mission5OffY.textContent = "—";
    return;
  }
  const state = m5.state || "IDLE";
  els.mission5State.textContent = state;
  els.mission5State.className =
    state === "ABORT" ? "badge badge--fault" :
    state === "DONE" ? "badge badge--ok" :
    state === "IDLE" ? "badge" : "badge badge--active";
  els.mission5Cam.textContent = m5.active_cam ? `CAM ${m5.active_cam === "BOTTOM" ? "0: BOTTOM" : "1: WALL"}` : "—";
  els.mission5Z.textContent = num(m5.distance_z, 2);
  els.mission5OffX.textContent = num(m5.offset_x, 1);
  els.mission5OffY.textContent = num(m5.offset_y, 1);
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
  try {
    ws = new WebSocket(CONFIG.WS_URL);
    window.ws = ws;
  } catch (e) {
    log("WS gagal dibuat", "err");
    return scheduleReconnect();
  }

  ws.onopen = () => {
    linkStale = false;
    setLink("on"); log("Terhubung ke server", "ok"); stopDemo();
    sendPing();
  };
  ws.onclose = () => {
    linkStale = false;
    setLink("off");
    /* Link putus = GUI tidak lagi punya otoritas kontrol. Kunci E-Stop dan
       netralkan axis lokal supaya saat WS tersambung lagi joystick tidak
       langsung mengirim nilai lama; operator harus ARM ulang dulu. */
    if (!estopLatched) log("Koneksi putus — joystick dikunci sampai ARM ulang", "warn");
    estopLatched = true;
    neutralizeGamepadAxes();
    scheduleReconnect();
    maybeDemo();
  };
  ws.onerror = () => { log("Error koneksi WS", "err"); };
  ws.onmessage = (ev) => {
  let msg;
  try {
    msg = JSON.parse(ev.data);
  } catch {
    return;
  }

  if (msg.type === "telemetry") {
    applyTelemetry(msg.data);
  }
  else if (msg.type === "pong") {
    setLatency(performance.now() - msg.t);
  }
  else if (msg.type === "event") {
    log(msg.text, msg.level || "");
  }
  else if (msg.type === "joystick_config") {
    handleJoystickConfigMessage(msg.data);
    log("Joystick config diterima dari server", "ok");
  }
  else if (msg.type === "record_status") {
    // status rekaman server → halaman Replay (jika sudah di-init). Aman walau
    // halaman Replay belum pernah dibuka.
    try { if (replayPage.onRecordStatus) replayPage.onRecordStatus(msg.data); } catch (e) {}
  }
  /* Kanal QGC-lite. Semuanya diarahkan lewat toPage(), yang hanya mengirim ke
     halaman yang SUDAH di-init — kedua halaman meminta datanya sendiri saat
     dibuka, jadi pesan yang datang sebelum itu memang tidak ada gunanya. */
  else if (msg.type === "param_batch") { toPage("vehicle", "onParamBatch", msg); }
  else if (msg.type === "param_ack")   { toPage("vehicle", "onParamAck", msg); }
  else if (msg.type === "mavlink_msg") { toPage("analyze", "onMavlinkMsg", msg); }
  else if (msg.type === "statustext") {
    // STATUSTEXT dari FC: inilah cara ArduSub melaporkan penolakan param &
    // error pre-arm. Tanpa ini pesannya cuma muncul di stdout Raspberry Pi.
    // severity MAVLink: 0..3 darurat/kritis, 4 warning, 5+ informasi.
    const sev = Number(msg.severity);
    log(`FC: ${msg.text}`, sev <= 3 ? "err" : sev === 4 ? "warn" : "");
  }
};
}

/* Kirim pesan ke satu modul halaman, hanya bila halaman itu sudah di-init. */
function toPage(name, method, arg) {
  if (!initedModules.has(name)) return;
  const mod = pageModules[name];
  if (!mod || !mod[method]) return;
  try { mod[method](arg); } catch (e) { console.error(`${name}.${method} gagal`, e); }
}

let reconnectTimer = null;
function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 1500);
}
function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}
function sendCmd(name, value, quiet = false) {
  send({ type: "cmd", name, value });
  if (!quiet) log(`CMD ${name} = ${value}`);
}
function sendPacket(packet, quiet = false) {
  if (!packet) return;

  send(packet);

  if (!quiet) {
    console.log("[MANIPULATOR]", packet);
    log(`CMD ${packet.name} = ${packet.value}`);
  }
}

// sediakan log, sendCmd & send (WS mentah) untuk modul halaman
setServices({ log, sendCmd, send });
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
  // depth mengikuti kedalaman kolam agar ALT & alarm realistis (kolam KKI ~0.9 m)
  const dmid = (CONFIG.POOL_DEPTH || 3) * 0.5;
  const damp = (CONFIG.POOL_DEPTH || 3) * 0.4;
  demo = setInterval(() => {
    t += 0.05;
    applyTelemetry({
      heading: (90 + 40 * Math.sin(t * 0.2) + 360) % 360,
      depth: dmid + damp * Math.sin(t * 0.15),
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
// Feed diambil lewat proxy same-origin (camProxy), jadi snapshot/record (canvas)
// tidak ter-taint tanpa perlu crossOrigin. onload/onerror dipasang sekali di sini.
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

let controlCamIndex = 0;

function getControlCameraSources() {
  const urls = [];
  (CONFIG.CAMERAS || []).forEach((cam) => {
    if (cam && cam.url) urls.push(cam.url);
  });
  if (CONFIG.CAMERA_URL) urls.push(CONFIG.CAMERA_URL);
  return urls.filter((url, idx, arr) => url && arr.indexOf(url) === idx);
}

function syncControlCameraButton() {
  if (!els.btnCamSwitch) return;
  const sources = getControlCameraSources();
  const canSwitch = sources.length > 1;
  // Visibilitas dikontrol CSS: tampil HANYA saat fullscreen DAN ada >1 kamera
  // (.cam:fullscreen .cam__switch.is-multi). Jangan set display inline agar
  // tidak menimpa aturan fullscreen tsb.
  els.btnCamSwitch.classList.toggle("is-multi", canSwitch);
  els.btnCamSwitch.textContent = canSwitch ? `CAM ${controlCamIndex + 1}` : "CAM 1";
}

// (re)arahkan feed kamera Control ke CONFIG.CAMERA_URL saat ini. Dipanggil di
// awal dan setiap URL diubah (Setup/Camera) via event 'hydroship:camera-url'.
function applyControlCamera() {
  const sources = getControlCameraSources();
  if (!sources.length) {
    els.camImg.removeAttribute("src");
    els.camNoSignal.style.display = "flex";
    els.camTag.textContent = "RTSP / MJPEG";
    if (els.camRes) els.camRes.textContent = "—";
    syncControlCameraButton();
    return;
  }

  const matchIdx = sources.indexOf(CONFIG.CAMERA_URL);
  if (matchIdx >= 0) controlCamIndex = matchIdx;
  else if (controlCamIndex >= sources.length) controlCamIndex = 0;

  const url = sources[controlCamIndex] || sources[0];
  CONFIG.CAMERA_URL = url;
  els.camTag.textContent = `CAM ${controlCamIndex + 1}`;

  // bust cache agar re-apply URL sama tetap memicu load ulang; ambil lewat proxy same-origin
  const bust = url + (url.includes("?") ? "&" : "?") + "_t=" + Date.now();
  els.camImg.src = camProxy(bust);
  syncControlCameraButton();
}

if (els.btnCamSwitch) {
  els.btnCamSwitch.onclick = () => {
    const sources = getControlCameraSources();
    if (sources.length < 2) return;
    controlCamIndex = (controlCamIndex + 1) % sources.length;
    CONFIG.CAMERA_URL = sources[controlCamIndex];
    applyControlCamera();
    log(`Kamera kontrol: ${controlCamIndex + 1}`, "ok");
  };
}

applyControlCamera();
window.addEventListener("hydroship:camera-url", applyControlCamera);

/*  kontrol UI  */
els.btnLight.onclick = () => { const v = !state.light; reflectLight(v); markPending("light", v); sendCmd("light", v); };
els.btnArm.onclick = () => {
  const v = !state.armed;
  // arming ulang melepas kunci E-Stop sehingga joystick boleh aktif lagi
  if (v) estopLatched = false;
  reflectArm(v); markPending("arm", v); sendCmd("arm", v);
};
els.btnStop.onclick = () => {
  // E-Stop mengunci joystick: tidak boleh meng-override sampai operator arm ulang
  estopLatched = true;
  sendCmd("stop", true); reflectArm(false); markPending("arm", false);
  neutralizeGamepadAxes();
  ["surge", "sway", "yaw", "heave"].forEach((a) => setAxis(a, 0));
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
    els.pilotPipImg.src = camProxy(CONFIG.CAMERA_URL); // umpan kamera live (via proxy same-origin)
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

/* Saat LIVE CAMERA fullscreen, operator tak lagi melihat digital twin (pilot).
   PiP di pojok menampilkan mirror scene 3D ROV Control (pilot) supaya attitude
   ROV tetap terpantau sambil menonton kamera layar penuh. */
let controlCamPiPRaf = null;
function renderControlCamPiP(on) {
  const cv = document.getElementById("ctrlCamPipCanvas");
  const no = document.getElementById("ctrlCamPipNo");
  if (!cv) return;

  if (on) {
    const ctx = cv.getContext("2d");
    const loop = () => {
      controlCamPiPRaf = requestAnimationFrame(loop);
      const w = cv.clientWidth, h = cv.clientHeight;
      if (!w || !h) return;
      if (cv.width !== w) cv.width = w;
      if (cv.height !== h) cv.height = h;
      ctx.clearRect(0, 0, cv.width, cv.height);
      // sumber = canvas WebGL digital twin (RovScene, render kontinu di scene.js)
      const src = scene && scene.renderer && scene.renderer.domElement;
      if (src && src.width && src.height) {
        try { ctx.drawImage(src, 0, 0, cv.width, cv.height); } catch (e) {}
        if (no) no.style.display = "none";
      } else if (no) {
        no.style.display = "flex";
      }
    };
    loop();
  } else if (controlCamPiPRaf) {
    cancelAnimationFrame(controlCamPiPRaf);
    controlCamPiPRaf = null;
  }
  if (!on && no) no.style.display = "none";
}

/* Full Screen toggle untuk LIVE CAMERA di halaman Control */
const camFs = makeFullscreen(els.camStage, {
  onToggle: (fs) => {
    els.camFullLabel.textContent = fs ? "Exit Full" : "Full Screen";
    els.btnCamFull.setAttribute("aria-pressed", String(fs));
    renderControlCamPiP(fs);
  },
});
els.btnCamFull.onclick = () => camFs.toggle();

/* pilot mode tabs: Manual | Stabilize | Depth Hold | Acro
 *
 * Sorotan tab TIDAK diset saat diklik. Klik hanya MEMINTA mode; yang menyorot
 * adalah syncModeTabs() dari HEARTBEAT Pixhawk (lihat applyTelemetry). Dengan
 * begitu tab GUI dan tombol gamepad selalu menunjukkan mode yang sama dengan
 * yang benar-benar dijalankan wahana — kalau Pixhawk menolak, tab tidak
 * berbohong. Selama menunggu konfirmasi, tab yang diminta ditandai "pending".
 */
requestPilotMode.pending = null;
requestPilotMode.pendingSince = 0;

function requestPilotMode(mode, label) {
  // ACRO: tanpa stabilisasi attitude dan throttle netral TIDAK menahan
  // kedalaman — di kolam dangkal ini paling mudah membuat ROV terguling.
  // Berlaku untuk SEMUA jalur input (tab GUI maupun tombol gamepad) — lihat
  // case "mode_acro" di executeJoystickAction, yang memanggil fungsi ini
  // juga supaya gerbang konfirmasi konsisten di kedua sisi.
  if (mode === "acro" && !confirm(ACRO_CONFIRM)) {
    log("Mode ACRO dibatalkan", "warn");
    return;
  }
  requestPilotMode.pending = mode;
  requestPilotMode.pendingSince = performance.now();
  sendCmd("pilot_mode", mode);
  log(`Minta mode pilot: ${label}`, "ok");
  syncModeTabs(lastPilotMode);
}

// ARDUSUB_MODE_TO_TAB / RISKY_ARDUSUB_MODES / ACRO_CONFIRM sekarang di
// shared/rov-modes.js (padanan rov_modes.py sisi Python), diimpor di atas.

// Pixhawk tidak menerima mode yang diminta dalam waktu ini -> beri peringatan.
const MODE_CONFIRM_TIMEOUT_MS = 2000;

let lastPilotMode = null;
let modeTimeoutWarned = false;

function syncModeTabs(actualMode) {
  const actual = typeof actualMode === "string" ? actualMode : null;
  const activeTab = actual ? ARDUSUB_MODE_TO_TAB[actual] : null;

  // Permintaan sudah terkonfirmasi Pixhawk -> tidak ada yang pending lagi.
  if (requestPilotMode.pending && requestPilotMode.pending === activeTab) {
    requestPilotMode.pending = null;
    modeTimeoutWarned = false;
  }

  document.querySelectorAll("#modeBar .mode").forEach((b) => {
    const isActive = b.dataset.mode === activeTab;
    const isPending = b.dataset.mode === requestPilotMode.pending;
    b.classList.toggle("mode--active", isActive);
    b.classList.toggle("mode--pending", isPending && !isActive);
    if (isActive) b.setAttribute("aria-selected", "true");
    else b.removeAttribute("aria-selected");
  });

  // Mode aktual apa adanya — termasuk mode di luar keempat tab (SURFACE,
  // POSHOLD, ...) yang memang tidak punya tab sendiri.
  if (els.modeActual) els.modeActual.textContent = actual || "—";

  if (els.modeWarn) {
    const risky = actual && RISKY_ARDUSUB_MODES.has(actual);
    els.modeWarn.hidden = !risky;
    if (risky) els.modeWarn.textContent = `⚠ ${actual} — TANPA STABILISASI`;
  }

  // Diminta tapi tak kunjung dikonfirmasi: kemungkinan firmware menolak
  // (mis. ACRO tidak tersedia di build ini) atau link command putus.
  if (
    requestPilotMode.pending &&
    !modeTimeoutWarned &&
    performance.now() - requestPilotMode.pendingSince > MODE_CONFIRM_TIMEOUT_MS
  ) {
    modeTimeoutWarned = true;
    log(`Pixhawk belum mengonfirmasi mode ${requestPilotMode.pending.toUpperCase()}`, "warn");
  }
}

document.querySelectorAll("#modeBar .mode").forEach((btn) => {
  btn.onclick = () => requestPilotMode(btn.dataset.mode, btn.textContent.trim());
});

/* controller tabs: Keyboard | Gamepad */
let activeController = "Keyboard";
document.querySelectorAll(".ctab").forEach((btn) => {
  btn.onclick = () => {
    const prev = activeController;
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

    // saat keluar dari Gamepad, netralkan axis agar thruster tak "nyangkut"
    // di nilai defleksi joystick terakhir
    if (prev === "Gamepad" && activeController !== "Gamepad") neutralizeGamepadAxes();
    // saat masuk Gamepad, laporkan status pad + indikator badge
    if (activeController === "Gamepad") logGamepadStatus();
  };
});

/* axis fields: Surge | Sway | Yaw | Vertical */
const axisEls = { surge: els.axSurge, sway: els.axSway, yaw: els.axYaw, heave: els.axHeave };
function setAxis(name, value, live = false) {
  const el = axisEls[name];
  if (!el) return;
  el.value = String(value);
  el.classList.toggle("axis--live", live && value !== 0);
  if (name in pilotAxes) pilotAxes[name] = Number(value) || 0;
}
Object.entries(axisEls).forEach(([name, el]) => {
  if (!el) return;
  el.addEventListener("change", () => {
    const v = clamp(Number(el.value) || 0, -1000, 1000);
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
  KeyR: ["heave", 50], KeyF: ["heave", -50],
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

/* ====================== GAMEPAD PILOTING ======================
   aktif hanya saat controller = Gamepad. Layout standar (Xbox-style):
   hasil mapping axis diambil dari joystick-state / halaman joystick.
   Panel joystick tetap bisa membaca gamepad walaupun activeController
   dashboard masih Keyboard. */
/* Deadzone/expo diterapkan di mapAxisValue() (joystick-state.js) supaya angka
   yang dilihat operator di halaman Joystick persis sama dengan yang dikirim. */

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, Math.round(v)));
}

function getMappedJoystickAxes() {
  updateJoystickStateFromGamepad();

  return {
    surge: joystickState.mapped.surge,
    sway:  joystickState.mapped.sway,
    yaw:   joystickState.mapped.yaw,
    heave: joystickState.mapped.heave,
  };
}

function firstGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  for (const p of pads) if (p && p.connected) return p;
  return null;
}

function setGamepadBadge(connected) {
  if (activeController !== "Gamepad") return;
  els.ctrlBadge.textContent = connected ? "Active: Gamepad ●" : "Active: Gamepad";
}

function logGamepadStatus() {
  const p = firstGamepad();
  setGamepadBadge(!!p);

  if (p) {
    log(`Gamepad aktif: ${p.id}`, "ok");
  } else {
    log("Gamepad dipilih — sambungkan & tekan tombol untuk mengaktifkan", "warn");
  }
}

const gpLast = { surge: 0, sway: 0, yaw: 0, heave: 0};

/* Perintah gripper terakhir yang dikirim ("open" | "close" | null).
   Dipakai untuk dedupe: tombol mode "hold" dan keyboard bisa memanggil aksi
   gripper berulang, sedangkan yang dibutuhkan hanya satu perintah posisi. */
/* E-Stop mengunci joystick sampai operator arm ulang (lihat btnStop/btnArm). */
let estopLatched = false;

/* Throttle pengiriman axis ke server ~15 Hz. Meski axis ditahan konstan,
   kita tetap resend supaya Pi menerima MANUAL_CONTROL berkelanjutan dan tidak
   masuk fail-safe timeout. */
const GP_SEND_HZ = 15;
const GP_SEND_INTERVAL = 1000 / GP_SEND_HZ;
let gpLastSent = 0;

/* Membaca navigator.getGamepads() mengalokasikan array baru tiap panggilan,
   jadi tidak perlu dilakukan 60x/detik. 30 Hz sudah 2x laju kirim (15 Hz)
   sehingga tidak menambah latensi yang terasa. */
const GP_POLL_HZ = 30;
const GP_POLL_INTERVAL = 1000 / GP_POLL_HZ;
let gpLastPoll = 0;

// status tombol fisik frame sebelumnya
const gpBtnPrev = {};

function gpPressed(pad, idx) {
  const b = pad.buttons[idx];
  const now = !!(b && (b.pressed || b.value > 0.5));
  const was = !!gpBtnPrev[idx];
  gpBtnPrev[idx] = now;
  return now && !was;
}

/* Satu-satunya jalan keluar perintah gripper (tombol GUI, keyboard H/G,
   tombol gamepad, axis analog) supaya dedupe konsisten antar sumber input.

   cmd  : "open" | "close" | angka -1000..1000 (axis analog)
   force: true untuk klik tombol GUI eksplisit — selalu kirim + beri log,
          walau posisinya sama, agar operator dapat umpan balik.
   Return true bila perintah benar-benar dikirim. */

function sendRotate(cmd) {

    if (controlMode !== "manual" || estopLatched)
        return false;

    switch (cmd) {

        case "left":
            sendPacket(Manipulator.rotateLeft(), true);
            break;

        case "right":
            sendPacket(Manipulator.rotateRight(), true);
            break;

        case "stop":
            sendPacket(Manipulator.stopRotate(), true);
            break;

        default:
            return false;
    }

    return true;

}

function neutralizeGamepadAxes() {
  for (const a of ["surge", "sway", "yaw", "heave"]) {
    gpLast[a] = 0;
    setAxis(a, 0);
    sendCmd(a, 0, true);
  }

  for (const k in gpBtnPrev) delete gpBtnPrev[k];
}

function executeJoystickAction(action, mode = "toggle") {
  console.log("[ACTION]", action, mode);
  if (!action || action === "no_function") return;

  switch (action) {
    /* ================= ARM / DISARM ================= */
    case "arm": {
      if (!state.armed) els.btnArm.click();
      return;
    }

    case "disarm": {
      if (state.armed) els.btnArm.click();
      return;
    }

    /* ================= CONTROL MODE ================= */
    case "mode_manual": {
      requestPilotMode("manual", "MANUAL");
      return;
    }

    case "mode_stabilize": {
      requestPilotMode("stabilize", "STABILIZE");
      return;
    }

    case "mode_depth_hold": {
      requestPilotMode("depth_hold", "DEPTH HOLD");
      return;
    }

    case "mode_acro": {
      // Sama seperti tab GUI: lewat requestPilotMode() supaya dialog
      // konfirmasi ACRO konsisten di semua jalur input, bukan hanya tab.
      requestPilotMode("acro", "ACRO");
      return;
    }

    case "input_hold_set": {
      sendCmd("input_hold_set", true);
      log("Input hold set", "ok");
      return;
    }

    /* ================= CAMERA / MOUNT ================= */
    case "mount_tilt_up": {
      sendRotate("left");
      return;
    }

    case "mount_tilt_down": {
      sendRotate("right");
      return;
    }

    case "mount_center": {
      sendCmd("mount_center", true);
      log("Mount center", "ok");
      return;
    }

    /* ================= ACTUATOR ================= */
    case "actuator1_inc": {
      sendPacket(Manipulator.openGrip(), true);
      return;
    }

    case "actuator1_dec": {
      sendPacket(Manipulator.closeGrip(), true);
      return;
    }

    /* ================= GRIPPER ================= */
    case "grip_open": {
        const pkt = Manipulator.openGrip();
        console.log("OPEN =", pkt);
        sendPacket(pkt);
        return;
    }

    case "grip_close": {
        const pkt = Manipulator.closeGrip();
        console.log("CLOSE =", pkt);
        sendPacket(pkt);
        return;
    }

    /* ================= LIGHT ================= */
    case "lights_brighter": {
      sendCmd("light_level", mode === "hold" ? { dir: "up", hold: true } : "up");
      return;
    }

    case "lights_dimmer": {
      sendCmd("light_level", mode === "hold" ? { dir: "down", hold: true } : "down");
      return;
    }

    /* ================= GAIN ================= */
    case "gain_inc": {
      sendCmd("gain_inc", true);
      return;
    }

    case "gain_dec": {
      sendCmd("gain_dec", true);
      return;
    }
  }
}

function executeJoystickRelease(action) {
    if (!action || action === "no_function") return;

    switch (action) {

        case "mount_tilt_up":
        case "mount_tilt_down":
            sendPacket(Manipulator.stopRotate(), true);
            return;

        /* grip_open/grip_close adalah nama HASIL migrasi dari actuator1_*.
           Tanpa case ini, gripper mode "hold" tidak pernah berhenti saat
           tombol dilepas. Nama lama tetap diterima untuk profil yang belum
           sempat dimigrasikan. */
        case "grip_open":
        case "grip_close":
        case "actuator1_inc":
        case "actuator1_dec":
            sendPacket(Manipulator.stopGrip(), true);
            return;
    }
}

function isGripAction(action) {
  return action === "grip_open" || action === "grip_close";
}

/* Proses tombol gamepad yang aksinya lolos `accept`.

   Cache edge (gpBtnPrev) TIDAK di-update di sini — commitButtonCache() yang
   melakukannya sekali per frame. Sebabnya: tombol gripper diproses di jalur
   terpisah dari tombol lain (lihat pollGamepad), dan kalau masing-masing
   jalur meng-update cache, jalur yang jalan lebih dulu akan "memakan" rising
   edge sehingga jalur kedua tidak pernah melihatnya. */
function processMappedGamepadButtons(accept = () => true) {
  const layerName = getActiveButtonLayerName();
  const rows = joystickState.buttonConfig?.[layerName] || [];

  for (const row of rows) {
    if (!row) continue;
    if (!accept(row.action)) continue;

    const btnIndex = Number(row.button);
    if (!Number.isInteger(btnIndex) || btnIndex < 0) continue;

    const current = !!joystickState.rawButtons?.[btnIndex]?.pressed;
    const prev = !!gpBtnPrev[btnIndex];

    const rising = current && !prev;
    const falling = !current && prev;

    if (row.mode === "hold") {

      // hanya sekali saat tombol mulai ditekan
      if (rising) {
          executeJoystickAction(row.action, "hold");
      }

      // hanya sekali saat tombol dilepas
      if (falling) {
          executeJoystickRelease(row.action);
      }

    }
  
    else {

      // toggle = sekali saat rising edge
      if (rising) {
        executeJoystickAction(row.action, "toggle");
      }
    }
  }
}

// update cache tombol fisik setelah SEMUA jalur aksi diproses
function commitButtonCache() {
  (joystickState.rawButtons || []).forEach((b, idx) => {
    gpBtnPrev[idx] = !!(b && b.pressed);
  });
}

/* Peringatan mode non-standard: sekali per sambungan, jangan tiap frame. */
let warnedNonStandard = false;

function warnNonStandardOnce() {
  if (warnedNonStandard || !joystickState.nonStandard) return;
  warnedNonStandard = true;
  log(
    "Gamepad bukan mode standard — geser saklar F310 ke posisi X lalu " +
    "cabut-pasang USB. Buka halaman Joystick untuk memakai paksa.",
    "err",
  );
}

window.addEventListener("gamepaddisconnected", () => { warnedNonStandard = false; });

function pollGamepad() {
  requestAnimationFrame(pollGamepad);

  const nowPoll = performance.now();
  if (nowPoll - gpLastPoll < GP_POLL_INTERVAL) return;
  gpLastPoll = nowPoll;

  // update state gamepad dulu supaya panel joystick + runtime pakai data yang sama
  updateJoystickStateFromGamepad();

  if (!joystickState.connected) return;
  if (!joystickState.enabled) return;

  /* Gate mode X. F310 di posisi D (DirectInput) melaporkan mapping kosong dan
     indeks axis/tombol-nya berbeda antar OS, sehingga profil yang sama jadi
     "salah tombol" tanpa gejala yang jelas — persis kelas bug yang ingin
     dihilangkan. Halaman Joystick menampilkan banner + tombol override. */
  if (!isJoystickUsable()) {
    if (gpLast.surge || gpLast.sway || gpLast.yaw || gpLast.heave) {
      neutralizeGamepadAxes();
    }
    warnNonStandardOnce();
    return;
  }

  /* ================= AUX: GRIPPER =================
     Tidak digerbangi activeController, jadi gripper tetap bisa dioperasikan
     dari gamepad walau tab controller sedang di Keyboard (dan sebaliknya —
     lihat handler keyboard H/G). Otoritas manual/E-Stop tetap ditegakkan di
     dalam sendGripper(). */
  processMappedGamepadButtons(isGripAction);

  // thruster control hanya aktif kalau dashboard controller = Gamepad
  if (activeController !== "Gamepad") {
    commitButtonCache();
    return;
  }

  /* Otoritas GUI vs FSM (mirip prinsip gripper): joystick HANYA boleh
     menggerakkan ROV saat mode kontrol = Manual dan E-Stop tidak aktif.
     Saat autonomous / E-Stop, pastikan axis dinetralkan sekali lalu diam. */
  if (controlMode !== "manual" || estopLatched) {
    if (gpLast.surge || gpLast.sway || gpLast.yaw || gpLast.heave) {
      neutralizeGamepadAxes();
    }
    commitButtonCache();
    return;
  }

  /* ================= AXIS ================= */
  const next = {
    surge: joystickState.mapped.surge,
    sway:  joystickState.mapped.sway,
    yaw:   joystickState.mapped.yaw,
    heave: joystickState.mapped.heave,
  };

  let changed = false;
  for (const a of ["surge", "sway", "yaw", "heave"]) {
    if (next[a] !== gpLast[a]) {
      gpLast[a] = next[a];
      setAxis(a, next[a], true);
      changed = true;
    }
  }

  // Kirim saat berubah, ATAU secara periodik (~15 Hz) walau axis ditahan,
  // agar MANUAL_CONTROL di Pi terus mengalir dan tidak masuk fail-safe.
  const nowT = performance.now();
  if (changed || nowT - gpLastSent >= GP_SEND_INTERVAL) {
    gpLastSent = nowT;
    for (const a of ["surge", "sway", "yaw", "heave"]) {
      sendCmd(a, gpLast[a], true);
    }
  }

  /* ================= BUTTON MAPPING =================
     Aksi gripper sudah diproses di jalur AUX di atas, jadi di sini hanya
     sisanya — supaya satu rising edge tidak dieksekusi dua kali. */
  processMappedGamepadButtons((a) => !isGripAction(a));
  commitButtonCache();
}

window.addEventListener("gamepadconnected", (e) => {
  log(`Gamepad tersambung: ${e.gamepad.id}`, "ok");
  setGamepadBadge(true);
});

window.addEventListener("gamepaddisconnected", (e) => {
  log(`Gamepad terputus: ${e.gamepad.id}`, "warn");
  setGamepadBadge(false);

  if (activeController === "Gamepad") {
    neutralizeGamepadAxes();
  }
});

/* Satu loop saja: pollGamepad menyegarkan joystickState (dipakai badge,
   panel tester, dan kontrol thruster). Halaman joystick punya loop sendiri
   saat di-mount, lihat pages/joystick.js. */
requestAnimationFrame(pollGamepad);

/* set surface level */
$("btnSetSurface").onclick = () => {
  sendCmd("set_surface", true);
  log("Surface level diset — Depth = 0", "ok");
};

/* gripper open/close (dipakai misi 2 & 5) — tombol + keyboard H/G */
/* ===================== MANIPULATOR ===================== */

/* ---------- Grip Open ---------- */
els.btnGripOpen.addEventListener("pointerdown", () => {
    sendPacket(Manipulator.openGrip());
    log("Grip OPEN", "ok");
});

els.btnGripOpen.addEventListener("pointerup", () => {
    sendPacket(Manipulator.stopGrip(), true);
});

els.btnGripOpen.addEventListener("pointerleave", () => {
    sendPacket(Manipulator.stopGrip(), true);
});

/* ---------- Grip Close ---------- */
els.btnGripClose.addEventListener("pointerdown", () => {
    sendPacket(Manipulator.closeGrip());
    log("Grip CLOSE", "ok");
});

els.btnGripClose.addEventListener("pointerup", () => {
    sendPacket(Manipulator.stopGrip(), true);
});

els.btnGripClose.addEventListener("pointerleave", () => {
    sendPacket(Manipulator.stopGrip(), true);
});

window.addEventListener("keydown", (e) => {
  if (e.target !== document.body) return;
  if (e.code === "KeyH") { e.preventDefault(); els.btnGripOpen.click(); }
  else if (e.code === "KeyG") { e.preventDefault(); els.btnGripClose.click(); }
});

/* viewport toggles: Follow ROV | Preview AIR | Echo → aksi nyata di scene 3D */
function toggleChip(id, onLabel, onToggle) {
  const el = $(id);
  el.onclick = () => {
    const on = el.getAttribute("aria-pressed") !== "true";
    el.setAttribute("aria-pressed", String(on));
    if (onToggle) { try { onToggle(on); } catch (e) {} }
    log(`${onLabel}: ${on ? "ON" : "OFF"}`);
  };
}
toggleChip("btnFollow", "Follow ROV", (on) => scene && scene.setFollow(on));
toggleChip("btnPreviewAir", "Preview AIR", (on) => scene && scene.setPreviewAir(on));
toggleChip("btnEcho", "Echo", (on) => scene && scene.setEcho(on));

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
      // browser memulai AudioContext "suspended" sampai ada interaksi → resume dulu
      if (alarm.ctx.state === "suspended") alarm.ctx.resume();
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
