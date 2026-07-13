import { CONFIG } from "./config.js";
import { RovScene } from "./scene.js";
import { setServices, pilotAxes, snapshotImage, createRecorder, makeFullscreen, camProxy } from "./core.js";
import { telemetryPage } from "./pages/telemetry.js";
import { missionPage } from "./pages/mission.js";
import { cameraPage } from "./pages/camera.js";
import { setupPage, loadSetup } from "./pages/setup.js";
import { joystickPage,handleJoystickConfigMessage} from "./pages/joystick.js";
import { joystickState,updateJoystickStateFromGamepad,getActiveButtonLayerName,} from "./joystick-state.js";
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
  axSurge: $("axSurge"), axSway: $("axSway"), axYaw: $("axYaw"), axHeave: $("axHeave"),
  btnGripOpen: $("btnGripOpen"), btnGripClose: $("btnGripClose"),
};

/* ====================== PAGE NAVIGATION ====================== */
const pages = {
  control: $("page-control"),
  camera: $("page-camera"),
  mission: $("page-mission"),
  telemetry: $("page-telemetry"),
  setup: $("page-setup"),
  joystick: $("page-joystick"),
};

const navLinks = document.querySelectorAll(".sidebar__link");

// modul per-halaman (Control tidak punya modul; logikanya inline di app.js)
const pageModules = {
  camera: cameraPage,
  mission: missionPage,
  telemetry: telemetryPage,
  setup: setupPage,
  joystick: joystickPage,
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
  ws.onclose = () => { linkStale = false; setLink("off"); scheduleReconnect(); maybeDemo(); };
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
function sendCmd(name, value, quiet = false) {
  send({ type: "cmd", name, value });
  if (!quiet) log(`CMD ${name} = ${value}`);
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
  // bust cache agar re-apply URL sama tetap memicu load ulang; ambil lewat proxy same-origin
  const bust = url + (url.includes("?") ? "&" : "?") + "_t=" + Date.now();
  els.camImg.src = camProxy(bust);
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
    // batasi entri manual ke rentang perintah valid −100..100
    const v = Math.max(-100, Math.min(100, Math.round(Number(el.value) || 0)));
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
const GP_DEADZONE = 0.12;

function clamp100(v) {
  return Math.max(-100, Math.min(100, Math.round(v)));
}

function axisToPercent(v) {
  const n = Number(v) || 0;
  if (Math.abs(n) < GP_DEADZONE) return 0;

  const sign = Math.sign(n);
  const mag = Math.abs(n);

  // remap setelah deadzone
  const scaled = (mag - GP_DEADZONE) / (1 - GP_DEADZONE);
  return clamp100(sign * scaled * 100);
}

function getMappedJoystickAxes() {
  updateJoystickStateFromGamepad();

  return {
    surge: axisToPercent(joystickState.mapped.surge),
    sway:  axisToPercent(joystickState.mapped.sway),
    yaw:   axisToPercent(joystickState.mapped.yaw),
    heave: axisToPercent(joystickState.mapped.heave),
  };
}

/* loop khusus untuk halaman joystick / panel tester
   supaya status connected, axis preview, dan tester tombol tetap hidup
   walaupun activeController belum dipilih ke Gamepad */
function pollJoystickPanel() {
  updateJoystickStateFromGamepad();
  requestAnimationFrame(pollJoystickPanel);
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

const gpLast = { surge: 0, sway: 0, yaw: 0, heave: 0 };

/* E-Stop mengunci joystick sampai operator arm ulang (lihat btnStop/btnArm). */
let estopLatched = false;

/* Throttle pengiriman axis ke server ~15 Hz. Meski axis ditahan konstan,
   kita tetap resend supaya Pi menerima MANUAL_CONTROL berkelanjutan dan tidak
   masuk fail-safe timeout. */
const GP_SEND_HZ = 15;
const GP_SEND_INTERVAL = 1000 / GP_SEND_HZ;
let gpLastSent = 0;

// status tombol fisik frame sebelumnya
const gpBtnPrev = {};

function gpPressed(pad, idx) {
  const b = pad.buttons[idx];
  const now = !!(b && (b.pressed || b.value > 0.5));
  const was = !!gpBtnPrev[idx];
  gpBtnPrev[idx] = now;
  return now && !was;
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
  sendCmd("pilot_mode", "manual");
  log("Pilot mode: MANUAL", "ok");
  return;
}

case "mode_stabilize": {
  sendCmd("pilot_mode", "stabilize");
  log("Pilot mode: STABILIZE", "ok");
  return;
}

case "mode_depth_hold": {
  sendCmd("pilot_mode", "depth_hold");
  log("Pilot mode: DEPTH HOLD", "ok");
  return;
}

    case "input_hold_set": {
      sendCmd("input_hold_set", true);
      log("Input hold set", "ok");
      return;
    }

    /* ================= CAMERA / MOUNT ================= */
    case "mount_tilt_up": {
      sendCmd("mount_tilt", mode === "hold" ? { dir: "up", hold: true } : "up");
      return;
    }

    case "mount_tilt_down": {
      sendCmd("mount_tilt", mode === "hold" ? { dir: "down", hold: true } : "down");
      return;
    }

    case "mount_center": {
      sendCmd("mount_center", true);
      log("Mount center", "ok");
      return;
    }

    /* ================= ACTUATOR ================= */
    case "actuator1_inc": {
      sendCmd("actuator1", mode === "hold" ? { dir: "inc", hold: true } : "inc");
      return;
    }

    case "actuator1_dec": {
      sendCmd("actuator1", mode === "hold" ? { dir: "dec", hold: true } : "dec");
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
      sendCmd("gain", mode === "hold" ? { dir: "inc", hold: true } : "inc");
      return;
    }

    case "gain_dec": {
      sendCmd("gain", mode === "hold" ? { dir: "dec", hold: true } : "dec");
      return;
    }
  }
}

function executeJoystickRelease(action) {
  if (!action || action === "no_function") return;

  switch (action) {
    case "mount_tilt_up":
    case "mount_tilt_down":
      sendCmd("mount_tilt", { dir: "stop" });
      return;

    case "actuator1_inc":
    case "actuator1_dec":
      sendCmd("actuator1", { dir: "stop" });
      return;
  }
}

function processMappedGamepadButtons() {
  const layerName = getActiveButtonLayerName();
  const rows = joystickState.buttonConfig?.[layerName] || [];

  for (const row of rows) {
    if (!row) continue;

    const btnIndex = Number(row.button);
    if (!Number.isInteger(btnIndex) || btnIndex < 0) continue;

    const current = !!joystickState.rawButtons?.[btnIndex]?.pressed;

    if (current) {
  console.log(
    "[JOY]",
    "layer =", layerName,
    "button =", btnIndex,
    "action =", row.action,
    "mode =", row.mode
  );
}

    const prev = !!gpBtnPrev[btnIndex];

    const rising = current && !prev;
    const falling = !current && prev;

    if (row.mode === "hold") {
  // selama tombol ditekan, kirim terus command hold
  if (current) {
    executeJoystickAction(row.action, "hold");
  }

  // saat tombol dilepas, kirim stop sekali
  if (falling) {
    executeJoystickRelease(row.action);
  }
} else {
      // toggle = sekali saat rising edge
      if (rising) {
        executeJoystickAction(row.action, "toggle");
      }
    }
  }

  // update cache tombol fisik setelah semua aksi diproses
  (joystickState.rawButtons || []).forEach((b, idx) => {
    gpBtnPrev[idx] = !!(b && b.pressed);
  });
}

function pollGamepad() {
  requestAnimationFrame(pollGamepad);

  // update state gamepad dulu supaya panel joystick + runtime pakai data yang sama
  updateJoystickStateFromGamepad();

  // thruster control hanya aktif kalau dashboard controller = Gamepad
  if (activeController !== "Gamepad") return;
  if (!joystickState.connected) return;
  if (!joystickState.enabled) return;

  /* Otoritas GUI vs FSM (mirip prinsip gripper): joystick HANYA boleh
     menggerakkan ROV saat mode kontrol = Manual dan E-Stop tidak aktif.
     Saat autonomous / E-Stop, pastikan axis dinetralkan sekali lalu diam. */
  if (controlMode !== "manual" || estopLatched) {
    if (gpLast.surge || gpLast.sway || gpLast.yaw || gpLast.heave) {
      neutralizeGamepadAxes();
    }
    return;
  }

  /* ================= AXIS ================= */
  const next = {
    surge: axisToPercent(joystickState.mapped.surge),
    sway:  axisToPercent(joystickState.mapped.sway),
    yaw:   axisToPercent(joystickState.mapped.yaw),
    heave: axisToPercent(joystickState.mapped.heave),
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

  /* ================= BUTTON MAPPING ================= */
  processMappedGamepadButtons();
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

/* penting:
   - pollJoystickPanel = untuk halaman joystick / tester / mapping
   - pollGamepad       = untuk kontrol thruster saat mode Gamepad aktif */
requestAnimationFrame(pollJoystickPanel);
requestAnimationFrame(pollGamepad);

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
