// app.js — dashboard utama Hydroship ROV
import { CONFIG } from "./config.js";
import { RovScene } from "./scene.js";
import { setServices, pilotAxes, snapshotImage, createRecorder, makeFullscreen, camProxy, setPyQr, setClientQr, getQrState, decodeClientQr } from "./core.js";
import { telemetryPage } from "./pages/telemetry.js";
import { missionPage } from "./pages/mission.js";
import { cameraPage } from "./pages/camera.js";
import { replayPage } from "./pages/replay.js";
import { setupPage, loadSetup, autonomyMotionConfig } from "./pages/setup.js";
import { vehiclePage } from "./pages/vehicle.js";
import { analyzePage } from "./pages/analyze.js";
import { joystickPage,handleJoystickConfigMessage} from "./pages/joystick.js";
import { joystickState,updateJoystickStateFromGamepad,getActiveButtonLayerName,isJoystickUsable,} from "./joystick-state.js";
import { Manipulator } from "./manipulator/manipulator.js";
import { ARDUSUB_MODE_TO_TAB } from "/shared/rov-modes.js";
import { HEADING_DEADBAND_DEG, headingError } from "/shared/rov-heading.js";

// Gain tampilan artificial horizon. Harus cocok dengan offset ladder ±10°
// di style.css (.attitude__ai-ladder--p10/--m10 = ±15px). Naikkan kalau
// gerakan pitch terasa terlalu halus di layar kecil.
const AI_PX_PER_DEG = 1.5;

/*  elemen DOM  */
const $ = (id) => document.getElementById(id);
const els = {
  link: $("linkPill"), linkLabel: $("linkLabel"),
  heading: $("vHeading"), depth: $("vDepth"), alt: $("vAlt"), roll: $("vRoll"),
  pitch: $("vPitch"), temp: $("vTemp"), volt: $("vVolt"), lat: $("vLat"),
  pi: $("vPi"),
  identTeam: $("identTeam"), identUni: $("identUni"),
  clockDate: $("clockDate"), clockTime: $("clockTime"),
  hudHeading: $("hudHeading"), hudRoll: $("hudRoll"), hudPitch: $("hudPitch"),
  hudGain: $("hudGain"), hudDrift: $("hudDrift"),
  miniInstruments: $("miniInstruments"),
  miniCompass: $("miniCompass"), miniCompassDial: $("miniCompassDial"),
  miniCompassStatus: $("miniCompassStatus"), miniCompassBug: $("miniCompassBug"),
  miniCompassErr: $("miniCompassErr"),
  miniAIBall: $("miniAIBall"), miniAIRollPtr: $("miniAIRollPtr"),
  camRes: $("camRes"), camRecIndicator: $("camRecIndicator"),
  tapeScale: $("tapeScale"), tapeVal: $("tapeVal"),
  camImg: $("camImg"), camNoSignal: $("camNoSignal"), camTag: $("camTag"),
  hookBboxCanvas: $("hookBboxCanvas"),
  camContrast: $("camContrast"),
  modelTag: $("modelTag"), log: $("log"),
  btnArm: $("btnArm"), btnStop: $("btnStop"),
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
  mission5TimeLeft: $("mission5TimeLeft"),
  m2Fails: $("m2Fails"), m2Score: $("m2Score"), m3Fails: $("m3Fails"), m3Score: $("m3Score"),
  runLastFile: $("runLastFile"), runLastResult: $("runLastResult"),
  runLastScore: $("runLastScore"), runLastDur: $("runLastDur"), runLastQr: $("runLastQr"),
  depthTarget: $("vDepthTarget"),
  depthTargetInput: $("depthTargetInput"),
  vQR: $("vQR"), qrReadout: $("qrReadout"), vQRSide: $("vQRSide"), qrDot: $("qrDot"), qrPreview: $("qrPreview"),
  vQRFocus: $("vQRFocus"), qrFocusReadout: $("qrFocusReadout"),
  depthHoldBadge: $("depthHoldBadge"),
  poolDepthBadge: $("poolDepthBadge"),
  cmdLinkBanner: $("cmdLinkBanner"),
  markBadge: $("markBadge"),
  modeActual: $("modeActual"),
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
let currentPageName = "control";

function showPage(pageName) {
  currentPageName = pageName;
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

  // Pasang/lepas feed kamera Control mengikuti halaman aktif — satu titik, jadi
  // semua rute masuk/keluar Control ikut tertangani.
  applyControlCamera();

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

/* Badge LINK. Idempoten: dipanggil tiap paket telemetry, tapi hanya menyentuh
   DOM saat statusnya benar-benar berubah. */
let lastLinkMode = null;
function setLink(mode) {
  if (mode === lastLinkMode) return;
  lastLinkMode = mode;
  els.link.dataset.state = mode;
  els.linkLabel.textContent =
    mode === "on" ? "ONLINE" : mode === "demo" ? "SIMULASI"
    : mode === "stale" ? "SINYAL HILANG"
    : mode === "fc-down" ? "PIXHAWK PUTUS" : "OFFLINE";
}

/* Tiga link yang bisa putus sendiri-sendiri, dan dulu ketiganya tampil sebagai
   satu pesan yang sama:
     - Pi -> GUI  (telemetry berhenti sampai)     -> watchdog 2,5 detik
     - Pi -> Pixhawk (fc_link dari agent)         -> di sini
     - GUI -> Pi  (cmd_link)                      -> applyCmdLink
   Sejak agent mengirim telemetry dari thread sendiri, putusnya link Pixhawk
   TIDAK lagi menghentikan telemetry — jadi GUI masih menerima paket berisi
   angka attitude/depth TERAKHIR. Nilai itu tidak boleh tampil seolah hidup. */
let fcLinkDown = false;
function applyFcLink(d) {
  // Agent lama tidak mengirim fc_link — jangan mengarang status untuk mereka.
  if (typeof d.fc_link !== "string") return;
  const down = d.fc_link === "down";
  if (down !== fcLinkDown) {
    fcLinkDown = down;
    log(down
      ? "Link Pixhawk putus — attitude/depth membeku di nilai terakhir"
      : "Link Pixhawk pulih", down ? "err" : "ok");
  }
  /* Penanda di body, bukan di satu elemen: yang membeku saat FC putus adalah
     SEMUA bacaan turunan FC (heading, depth, roll, pitch, kompas, horizon),
     dan CSS bisa menandai seluruhnya sekaligus tanpa daftar elemen di JS. */
  document.body.classList.toggle("fc-down", down);
}

function setTheme(name) {
  document.body.dataset.theme = name;
  localStorage.setItem("hydroship-theme", name);
}

function loadTheme() {
  const saved = localStorage.getItem("hydroship-theme");
  setTheme(saved === "light" ? "light" : "dark");
}

function setCamContrast(pct) {
  els.camImg.style.filter = `contrast(${pct}%)`;
  localStorage.setItem("hydroship-cam-contrast", pct);
}

function loadCamContrast() {
  const saved = Number(localStorage.getItem("hydroship-cam-contrast")) || 100;
  els.camContrast.value = saved;
  setCamContrast(saved);
}
els.camContrast.addEventListener("input", () => setCamContrast(els.camContrast.value));

function num(v, d = 1) {
  return (v === null || v === undefined || Number.isNaN(v)) ? "—" : v.toFixed(d);
}

/*  depth tape — skala mengikuti kedalaman kolam (CONFIG.POOL_DEPTH) supaya
    berguna baik di kolam dangkal KKI (~0.9 m) maupun kolam uji yang lebih dalam. */
let TAPE;
/* Tinggi viewport tape hanya berubah saat window/layout berubah, bukan tiap
   paket telemetri. Dulu updateTape() membaca clientHeight 10x/detik TEPAT
   sesudah applyTelemetry menulis puluhan style — pola baca-sesudah-tulis yang
   memaksa browser me-reflow secara sinkron di tengah handler pesan, sehingga
   pesan berikutnya (termasuk pong pengukur LAT) harus antre di belakangnya.
   0 = perlu dibaca ulang. Dideklarasikan SEBELUM buildTape() dipanggil di
   bawah, karena buildTape me-reset-nya. */
let tapeHeight = 0;
window.addEventListener("resize", () => { tapeHeight = 0; });

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
    /* Posisi tiap mark relatif terhadap skala ditulis SEKALI di sini. Yang
       bergerak saat kedalaman berubah cuma satu hal: seluruh skalanya. */
    mark.style.top = ((m - TAPE.min) * TAPE.px) + "px";
    mark.textContent = (isMajor && m >= 0) ? m.toFixed(TAPE.minor < 1 ? 1 : 0) + " m" : "";
    frag.appendChild(mark);
  }
  els.tapeScale.appendChild(frag);
  tapeHeight = 0;   // tinggi lama tak berlaku untuk skala baru
}
buildTape();
// rescale saat pool depth diubah di halaman Setup
window.addEventListener("hydroship:pool-depth", buildTape);

function updateTape(depth) {
  if (!tapeHeight) tapeHeight = els.tapeScale.parentElement.clientHeight;
  /* Semua mark bergeser dengan delta yang sama, jadi satu transform pada
     kontainer setara dengan menulis `top` di tiap mark — tapi hanya satu
     properti, dan transform ditangani compositor (CSS .tape__scale memang
     sudah menyiapkan will-change: transform). */
  els.tapeScale.style.transform =
    `translateY(${tapeHeight / 2 - (depth - TAPE.min) * TAPE.px}px)`;
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
    if (!isDemo && !demo) log("Telemetri pulih", "ok");
  }
  lastTelemetry = performance.now();
  applyFcLink(d);
  /* Badge diturunkan dari kedua kondisi sekaligus. Kalau ditulis berurutan
     (telemetry pulih -> "on", lalu fc down -> "stale"), pemulihan telemetry
     saat Pixhawk MASIH putus akan meninggalkan badge di "ONLINE". */
  if (!isDemo && !demo) setLink(fcLinkDown ? "fc-down" : "on");
  // d.depth sudah ditare backend (lihat command `set_surface`, rov_agent.py) —
  // tare dua kali di sini akan memotong depth yang sama dua kali lipat.
  els.heading.textContent = num(d.heading, 0);
  els.depth.textContent = num(d.depth, 2);
  if (els.depthTarget) {
    els.depthTarget.textContent =
      Number.isFinite(d.depth_target) ? num(d.depth_target, 2) : "—";
  }
  // altitude = ketinggian ROV (titik tengah) di atas dasar kolam
  if (els.alt) {
    const alt = Number.isFinite(d.depth) ? Math.max(0, CONFIG.POOL_DEPTH - d.depth) : null;
    els.alt.textContent = num(alt, 2);
  }
  els.roll.textContent = num(d.roll, 1);
  els.pitch.textContent = num(d.pitch, 1);
  els.temp.textContent = num(d.temp, 1);
  // Nilai sub-3 V tidak mungkin merupakan baterai utama 4S saat FC masih
  // hidup; itu biasanya ADC/battery monitor yang belum valid. Tampilkan kosong
  // dan jangan picu alarm palsu, tetapi pertahankan alarm untuk data yang valid.
  const v = Number.isFinite(d.voltage) && d.voltage >= 3 ? d.voltage : null;
  els.volt.textContent = num(v, 1);
  // tegangan hanya berguna kalau kelihatan saat turun: kritis diberi kedip
  // yang sama dengan alarm kedalaman, waspada cukup warna. null -> netral.
  const vCrit = Number.isFinite(v) && v <= CONFIG.VOLT_CRIT;
  els.volt.classList.toggle("is-warn", Number.isFinite(v) && v <= CONFIG.VOLT_WARN && !vCrit);
  els.volt.parentElement.classList.toggle("readout--danger", vCrit);

  /* Status Pi: satu sel berisi dua angka (CPU% dan suhu SoC). Suhu yang bikin
     Pi menurunkan clock, CPU yang bikin loop kontrol telat — dua-duanya muncul
     ke pilot sebagai "ROV lag", jadi keduanya perlu terlihat sekaligus.
     Ambang suhu dari dokumentasi Pi (70 mulai throttle, 80 throttle keras). */
  const piCpu = d.pi_cpu, piTemp = d.pi_temp;
  const piKnown = Number.isFinite(piCpu) || Number.isFinite(piTemp);
  els.pi.textContent = piKnown
    ? `${Number.isFinite(piCpu) ? piCpu.toFixed(0) + "%" : "—"} · ${Number.isFinite(piTemp) ? piTemp.toFixed(0) + "°C" : "—"}`
    : "—";
  const piCrit = Number.isFinite(piTemp) && piTemp >= CONFIG.PI_TEMP_CRIT;
  const piWarn = !piCrit && (
    (Number.isFinite(piTemp) && piTemp >= CONFIG.PI_TEMP_WARN) ||
    (Number.isFinite(piCpu) && piCpu >= CONFIG.PI_CPU_WARN));
  els.pi.classList.toggle("is-warn", piWarn);
  els.pi.parentElement.classList.toggle("readout--danger", piCrit);

  const heading = Number.isFinite(d.heading) ? ((d.heading % 360) + 360) % 360 : null;
  els.hudHeading.textContent = "HDG " + num(d.heading, 0) + "°";
  els.hudRoll.textContent = "R " + num(d.roll, 0) + "°";
  els.hudPitch.textContent = "P " + num(d.pitch, 0) + "°";

  // Drift dari optical flow kamera bawah (rov_drift.py). drift_source:
  // "flow" = bacaan visual segar, "imu" = tambalan celah singkat (accel
  // terintegrasi, lihat integrate_accel di rov_drift.py — bukan dead-
  // reckoning berkepanjangan, cuma jaga HUD tidak jatuh ke "tidak ada data"
  // untuk gangguan sesaat), "none" = benar-benar tak ada bacaan.
  if (els.hudDrift) {
    const source = d.drift_source;
    const hasDrift = (source === "flow" || source === "imu")
      && Number.isFinite(d.drift_vx) && Number.isFinite(d.drift_vy);
    if (hasDrift) {
      const speed = Math.hypot(d.drift_vx, d.drift_vy);
      const dirDeg = Math.round((Math.atan2(d.drift_vy, d.drift_vx) * 180 / Math.PI + 360) % 360);
      const tag = source === "imu" ? " (IMU)" : "";
      els.hudDrift.textContent = `DRIFT ${speed.toFixed(2)} m/s ${dirDeg}°${tag}`;
      els.hudDrift.removeAttribute("data-stale");
    } else {
      els.hudDrift.textContent = "DRIFT —";
      els.hudDrift.setAttribute("data-stale", "1");
    }
  }

  // Kompas gaya QGroundControl: dial berputar berlawanan arah heading,
  // pointer merah tetap diam di atas menunjukkan heading saat ini.
  // Tanpa heading valid instrumen HARUS jadi OFF — jangan biarkan dial
  // membeku di rotasi terakhir seolah datanya masih hidup.
  if (els.miniCompassDial) {
    if (heading !== null) {
      els.miniCompassDial.style.transform = `rotate(${-heading}deg)`;
      els.miniCompassStatus.textContent = `${Math.round(heading)}°`;
    } else {
      els.miniCompassStatus.textContent = "OFF";
    }
    els.miniCompass.dataset.state = heading !== null ? "on" : "off";

    // Heading bug: setpoint POSHOLD (d.heading_target) yang selama ini cuma
    // masuk kolom CSV. Bug adalah anak dial, jadi cukup diputar ke bearing-nya
    // — rotasi kartu yang menempatkannya di posisi benar secara otomatis.
    const err = headingError(d.heading_target, heading);
    if (err === null) {
      els.miniCompassBug.hidden = true;
      els.miniCompassErr.hidden = true;
      els.miniCompass.removeAttribute("data-hold");
    } else {
      els.miniCompassBug.hidden = false;
      els.miniCompassBug.style.setProperty("--a", `${d.heading_target}deg`);
      // armed = setpoint ada tapi overlay belum mengoreksi; engaged = sedang menahan
      els.miniCompass.dataset.hold = d.poshold === true ? "engaged" : "armed";

      const onTarget = Math.abs(err) <= HEADING_DEADBAND_DEG;
      els.miniCompassErr.hidden = false;
      els.miniCompassErr.textContent = onTarget
        ? "ON TARGET"
        : `${err > 0 ? "+" : ""}${Math.round(err)}°`;
      els.miniCompassErr.classList.toggle("is-ontarget", onTarget);
    }
  }

  // Artificial horizon. Urutan transform disengaja: CSS jalan kanan-ke-kiri,
  // jadi pitch digeser dulu baru diputar roll — ladder ikut sumbu tegak
  // instrumen yang sudah miring, seperti attitude indicator sungguhan.
  if (els.miniAIBall) {
    const roll = Number.isFinite(d.roll) ? d.roll : 0;
    const pitch = clamp(Number.isFinite(d.pitch) ? d.pitch : 0, -90, 90);
    els.miniAIBall.style.transform =
      `rotate(${-roll}deg) translateY(${pitch * AI_PX_PER_DEG}px)`;
    // pointer roll menunjuk skala yang diam di bezel
    els.miniAIRollPtr.style.transform =
      `translate(-50%, -50%) rotate(${-roll}deg) translateY(-51px)`;
  }

  if (scene) scene.setAttitude(d.roll, d.pitch, d.heading);
  updateTape(d.depth || 0);

  // alarm kedalaman berbahaya + kedip readout depth
  const danger = Number.isFinite(d.depth) && d.depth >= CONFIG.DANGER_DEPTH;
  depthAlarm(danger);
  els.depth.parentElement.classList.toggle("readout--danger", danger);

  if (typeof d.armed === "boolean") confirmArm(d.armed);
  if (typeof d.light === "boolean") confirmLight(d.light);

  // Mode pilot: satu-satunya penggerak sorotan tab adalah mode yang dilaporkan
  // Pixhawk lewat HEARTBEAT, bukan klik operator.
  // ...kecuali POSHOLD, yang TIDAK terlihat di HEARTBEAT: ia berjalan di
  // ALT_HOLD dan hanya ditandai flag `poshold` dari agent. Tetap prinsip yang
  // sama — sorotan mengikuti apa yang dilaporkan wahana, bukan klik operator.
  if (typeof d.poshold === "boolean") lastPosHold = d.poshold;

  if (typeof d.mode === "string") {
    if (d.mode !== lastPilotMode) {
      lastPilotMode = d.mode;
      log(`Mode pilot aktif: ${d.mode}`, "ok");
    }
    syncModeTabs(d.mode, lastPosHold);
  }

  applyMission5(d.mission5);
  // Worker YOLO laptop masuk sebagai d.hook_xy dan tetap aktif saat FSM idle.
  if (d.hook_xy && d.hook_xy.bbox) {
    drawHookBbox({ ...d.hook_xy, active_cam: "WALL" });
  }
  applyMissionCounter(d.mission_counter);

  // teruskan sampel ke modul halaman yang sudah di-init (buffering murah;
  // render sebenarnya digerbang oleh onShow/onHide)
  for (const name of initedModules) {
    const m = pageModules[name];
    if (m && m.onTelemetry) { try { m.onTelemetry(d); } catch (e) {} }
  }

  if (Number.isFinite(Number(d.thruster_gain))) {
    els.hudGain.textContent = `GAIN ${Math.round(Number(d.thruster_gain))}%`;
    toPage("setup", "onThrusterGainTelemetry", d.thruster_gain);
  }

  els.depthTarget.textContent = num(d.depth_target, 2);
  applyDepthHold(d);
  applyMarkBadge(d);
  applyPoolDepth(d);
  applyCmdLink(d);
}

/* Badge status depth-hold, read-only — tidak ada saklar manual lagi.
   d.depth_hold datang langsung dari depth_hold_mode_ok() (rov_agent.py):
   true berarti mode ArduSub sedang ALT_HOLD-capable dan bias sedang mengalir. */
/* Status MARK gantungan. Tanpa mark, M5_REDIVE tidak punya arah dan hanya
   menyapu pelan sampai timeout — jadi ini HARUS terbaca sebelum operator
   menekan AUTONOMOUS, bukan ditemukan setelah wahana menyelam dan gagal. */
function applyMarkBadge(d) {
  if (!els.markBadge) return;
  const hdg = d.marked_heading;
  const dep = d.marked_depth;
  const marked = Number.isFinite(hdg) && Number.isFinite(dep);
  els.markBadge.textContent = marked
    ? `MARK ${hdg.toFixed(0)}\u00B0 / ${dep.toFixed(2)} m`
    : "BELUM DI-MARK";
  els.markBadge.classList.toggle("badge--ok", marked);
}

function applyDepthHold(d) {
  if (!els.depthHoldBadge) return;

  const holding = d.depth_hold === true;

  els.depthHoldBadge.textContent = holding ? "DEPTH-HOLD ON" : "DEPTH-HOLD OFF";
  els.depthHoldBadge.classList.toggle("badge--ok", holding);
}

/* Echo pool_depth dari wahana (rov_agent.py state["pool_depth"]) — GUI cuma
   MENGIRIM nilai ini (lihat sendCmd("pool_depth", ...) di onopen & setup.js),
   sebelumnya tak pernah dikonfirmasi kembali kalau wahana benar menerimanya. */
function applyPoolDepth(d) {
  if (!els.poolDepthBadge) return;
  const dep = d.pool_depth;
  const known = Number.isFinite(dep);
  els.poolDepthBadge.textContent = known ? `KOLAM ${dep.toFixed(2)} m` : "KOLAM —";
  els.poolDepthBadge.classList.toggle("badge--ok", known);
}

/* banner "LINK PERINTAH TERPUTUS" — nyala saat Pi substitusi axis netral
   karena link joystick/dashboard timeout (fail-safe di rov_agent.py). */
function applyCmdLink(d) {
  if (!els.cmdLinkBanner) return;
  els.cmdLinkBanner.hidden = d.cmd_link !== "stale";
}

/* overlay bbox+confidence+keypoints deteksi hook (kamera WALL) di atas #camImg — nilai
   tambah kepercayaan pilot saat autonomous, tak cuma angka offset/distance
   sebagai teks. #camImg pakai object-fit:cover jadi skala harus max(sx,sy)
   + centering letterbox, BUKAN stretch naif seperti buffer scanControlQR. */
/* Ukuran tampil #camImg di-cache. drawHookBbox dipanggil sampai 20x/detik
   (tiap paket telemetry yang membawa hook_xy + tiap pesan hook_vision), dan
   getBoundingClientRect() memaksa reflow sinkron setiap kali — di dalam
   handler pesan WebSocket, jadi pesan berikutnya ikut tertahan. Ukurannya
   sendiri hanya berubah saat layout berubah, dan ResizeObserver melaporkan
   itu tepat waktu (ganti halaman, fullscreen, resize window) tanpa polling. */
let camImgBox = { w: 0, h: 0 };
if (els.camImg && typeof ResizeObserver === "function") {
  new ResizeObserver((entries) => {
    const r = entries[entries.length - 1].contentRect;
    camImgBox = { w: r.width, h: r.height };
  }).observe(els.camImg);
}

function camImgSize() {
  // Fallback (browser tanpa ResizeObserver, atau sebelum callback pertama):
  // baca langsung — benar, sekadar tidak gratis.
  if (camImgBox.w && camImgBox.h) return camImgBox;
  if (!els.camImg) return { w: 0, h: 0 };
  const r = els.camImg.getBoundingClientRect();
  return { w: r.width, h: r.height };
}

function drawHookBbox(m5) {
  const cv = els.hookBboxCanvas;
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const rect = camImgSize();
  if (cv.width !== rect.w) cv.width = rect.w;
  if (cv.height !== rect.h) cv.height = rect.h;
  ctx.clearRect(0, 0, cv.width, cv.height);

  const bbox = m5 && m5.bbox, conf = m5 && m5.confidence;
  const sw = els.camImg.naturalWidth, sh = els.camImg.naturalHeight;
  if (!bbox || !sw || !sh || !m5 || (m5.active_cam && m5.active_cam !== "WALL")) return;
  const shownCamera = (CONFIG.CAMERAS || []).find((c) => c.url === CONFIG.CAMERA_URL);
  if (shownCamera && String(shownCamera.role || "").toUpperCase() !== "WALL") return;

  const scale = Math.max(cv.width / sw, cv.height / sh);
  const ox = (cv.width - sw * scale) / 2, oy = (cv.height - sh * scale) / 2;
  const [x, y, w, h] = bbox;
  const rx = x * scale + ox, ry = y * scale + oy, rw = w * scale, rh = h * scale;

  const c = conf == null ? 0 : conf;
  const color = c >= 0.7 ? "#2ee6a6" : c >= 0.4 ? "#f5c518" : "#ff4d4f";
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.strokeRect(rx, ry, rw, rh);
  ctx.fillStyle = color;
  ctx.font = "12px 'JetBrains Mono', monospace";
  ctx.fillText(`HOOK ${(c * 100).toFixed(0)}%`, rx, ry > 14 ? ry - 4 : ry + rh + 14);

  // YOLOv8-Pose worker mengirim titik pada koordinat frame asli.
  // Titik ini hanya visualisasi/telemetri, bukan command gerak.
  if (Array.isArray(m5.keypoints)) {
    ctx.fillStyle = "#ffd21f";
    ctx.font = "11px 'JetBrains Mono', monospace";
    for (const kp of m5.keypoints) {
      const kx = Number(kp && kp.x), ky = Number(kp && kp.y);
      if (!Number.isFinite(kx) || !Number.isFinite(ky)) continue;
      const px = kx * scale + ox, py = ky * scale + oy;
      ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI * 2); ctx.fill();
      ctx.fillText(String(kp.id ?? ""), px + 6, py - 5);
    }
  }
}

/* panel Mission 5 (docking/unhook) — m5 = {state, active_cam, distance_z, offset_x, offset_y} */
function applyMission5(m5) {
  setPyQr(m5 && m5.qr_data, m5 && m5.qr_wall);
  renderQrReadout();
  renderQrPreviewImage(getQrState().raw);

  if (!els.mission5State) return;
  if (!m5) {
    els.mission5State.textContent = "IDLE";
    els.mission5State.className = "badge";
    els.mission5Cam.textContent = "—";
    els.mission5Z.textContent = "—";
    els.mission5OffX.textContent = "—";
    els.mission5OffY.textContent = "—";
    els.mission5TimeLeft.textContent = "—";
    els.mission5TimeLeft.className = "readout__v";
    drawHookBbox(null);
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
  // Pagar C (time-budget dinamis, lihat fsm/mission5.py TIME_BUDGET_TOTAL) —
  // merah begitu sisa < 30s supaya pilot lihat degradasi dini akan/sedang terjadi.
  const tLeft = m5.time_left;
  els.mission5TimeLeft.textContent = tLeft == null ? "—" : Math.round(tLeft);
  els.mission5TimeLeft.className = "readout__v" + (tLeft != null && tLeft < 30 ? " is-fault" : "");
  drawHookBbox(m5);

  // Run baru saja berakhir → tarik ringkasannya. Ditunda sesaat karena FSM menulis
  // event `end` setelah state jadi DONE/ABORT (saat proses menutup run log).
  if ((state === "DONE" || state === "ABORT") && state !== _lastM5State)
    setTimeout(refreshLastRun, 1500);
  _lastM5State = state;
}

/* Counter trial Misi 2/3 (Guidebook KKI 2026 §4.7.4) — mc = {m2_fails, m2_score, m3_fails, m3_score} */
function applyMissionCounter(mc) {
  if (!els.m2Fails) return;
  els.m2Fails.textContent = mc ? mc.m2_fails + 1 : 1;
  els.m2Score.textContent = mc ? mc.m2_score : 15;
  els.m3Fails.textContent = mc ? mc.m3_fails + 1 : 1;
  els.m3Score.textContent = mc ? mc.m3_score : 15;
}

/* Ringkasan run autonomous terakhir — historis, jadi lewat HTTP (bukan WS live).
   Angkanya dihitung tools/analyze_run.py agar identik dgn laporan CLI. */
let _lastM5State = null;

async function refreshLastRun() {
  if (!els.runLastFile) return;
  let r;
  try {
    r = (await (await fetch("/api/runs")).json())[0];
  } catch { return; }               // server tanpa endpoint / offline → biarkan "—"
  if (!r) return;

  els.runLastFile.textContent = r.file.replace(/^run_|\.jsonl$/g, "");
  els.runLastFile.title = `config: ${(r.config_files || []).join(", ") || "default"}`;
  const gagal = r.terpotong || r.state_akhir === "ABORT";
  els.runLastResult.textContent =
    r.terpotong ? "TERPOTONG" : `${r.state_akhir}${r.dock_used_fallback ? " (fallback)" : ""}`;
  els.runLastResult.className = "readout__v readout__v--text" + (gagal ? " is-fault" : "");
  els.runLastScore.textContent = (r.skor && r.skor.total != null) ? r.skor.total : "—";
  els.runLastDur.textContent = num(r.durasi_s, 1);
  els.runLastQr.textContent = num(r.qr_rate_pct, 1);
}

function reflectArm(on) {
  state.armed = on;
  els.btnArm.setAttribute("aria-pressed", String(on));
  els.armLabel.textContent = on ? "ARMED" : "DISARMED";
}
function reflectLight(on) {
  state.light = on;
}

/* ARM/LIGHT: UI dibalik optimistik saat diklik lalu ditandai "pending" sampai
   telemetri ROV mengonfirmasi. Jika ROV menolak (nilai beda) atau tak pernah
   meng-echo status dalam 2 dtk, operator diberi tahu agar tidak salah baca. */
const pending = {
  arm:   { active: false, expected: false, since: 0, btn: els.btnArm,   label: "ARM" },
  light: { active: false, expected: false, since: 0, btn: null, label: "LIGHT" },
};
function markPending(key, expected) {
  const p = pending[key];
  p.active = true; p.expected = expected; p.since = performance.now();
  p.btn?.classList.add("ctrl--pending");
}
function clearPending(key) {
  const p = pending[key];
  p.active = false; p.btn?.classList.remove("ctrl--pending");
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

/* indikasi QR di strip Control. Sumber data disatukan di core.js
   (setPyQr/setClientQr/getQrState) supaya konsisten dengan halaman Camera:
   pipeline Python (mission5.py, via applyMission5 di atas) diutamakan
   selama masih segar (misi autonomous jalan) karena itu deteksi ROV
   sungguhan; scan jsQR lokal terhadap #camImg jadi fallback saat FSM
   Python belum/tidak aktif, supaya operator tetap lihat indikasi saat manual. */
const qrScanCanvas = document.createElement("canvas");
let _lastQrScan = 0;

function renderQrReadout() {
  if (!els.vQR) return;
  const { raw, side, source, changeType } = getQrState();
  if (raw) {
    els.vQR.textContent = raw;
    els.vQR.title = `${raw} — sumber: ${source === "python" ? "vision Python" : "scan lokal (browser)"}`;
    els.qrReadout.classList.add("is-ok");
  } else {
    els.vQR.textContent = "—";
    els.vQR.removeAttribute("title");
    els.qrReadout.classList.remove("is-ok");
  }
  els.qrReadout.classList.toggle("is-py", source === "python");
  if (els.vQRSide) {
    els.vQRSide.textContent = side || "";
    els.vQRSide.classList.toggle("qr__side--ok", !!side);
    els.vQRSide.hidden = !side;
  }
  if (els.qrDot) {
    els.qrDot.className = "qr-dot" + (changeType === "new" ? " qr-dot--new" : changeType === "same" ? " qr-dot--same" : "");
  }
}

/* Preview QR — sekedar pembacaan hasil decode. Bila payload QR adalah LINK ke
   gambar (data-URL image atau URL http/https — halaman web yang menampilkan
   gambar sekalipun, resolusi gambarnya dilakukan server via /qr/preview karena
   browser tak bisa fetch lintas-origin) tampilkan gambar tsb di canvas; selain
   itu (JSON KKI / huruf sisi / teks) cukup render teks hasil decode. Ini murni
   view berpasif thd hasil decode, bukan screenshot deteksi. */
let _qrPreviewImg = null;
let _qrPreviewRaw = null;
const QR_PREVIEW_PROXY = "/qr/preview?url=";

// URL sumber yang boleh digambar: data-URL image langsung, URL http(s) lewat
// proxy same-origin (server mengikuti redirect & mengekstrak gambar dari
// halaman HTML). Selain itu → null (bukan gambar, render teks).
function qrPreviewSrc(raw) {
  if (!raw) return null;
  const s = String(raw).trim();
  if (/^data:image\/[^;]+;base64,/.test(s)) return s;
  if (/^https?:\/\//i.test(s)) return QR_PREVIEW_PROXY + encodeURIComponent(s);
  return null;
}

// muat sumber gambar (data-URL atau URL hasil proxy) sekali, cache di Image.
// DrawImage gambar cross-origin tetap sah (canvas mungkin ter-taint tapi kita
// tak pernah membaca pikselnya), jadi tak bergantung header CORS di host
// gambar publik macam etsy / cdn3.me-qr.com.
function loadQrPreviewImage(raw) {
  if (_qrPreviewImg && _qrPreviewImg._src === raw) return Promise.resolve(_qrPreviewImg);
  return new Promise((resolve) => {
    const img = new Image();
    let done = false;
    // resolusi server bisa dua-langkah (redirect + HTML→gambar), kasih kelonggaran
    const timer = setTimeout(() => { if (!done) { done = true; resolve(null); } }, 20000);
    img._src = raw;
    img.onload = () => { if (done) return; done = true; clearTimeout(timer); _qrPreviewImg = img; resolve(img); };
    img.onerror = () => { if (done) return; done = true; clearTimeout(timer); resolve(null); };
    img.src = qrPreviewSrc(raw) || raw;
  });
}

function paintQrText(pCtx, pw, ph, text) {
  pCtx.fillStyle = "rgba(255,255,255,.97)";
  pCtx.fillRect(0, 0, pw, ph);
  pCtx.fillStyle = "#101418";
  pCtx.textAlign = "center";
  pCtx.textBaseline = "middle";
  const words = String(text).split(/\s+/);
  const lines = [];
  let line = "";
  for (const w of words) {
    const test = line ? line + " " + w : w;
    pCtx.font = "10px ui-monospace, monospace";
    if (pCtx.measureText(test).width <= pw - 12) { line = test; }
    else { if (line) lines.push(line); line = w; }
  }
  if (line) lines.push(line);
  const lh = 14;
  let y = ph / 2 - ((lines.length - 1) * lh) / 2;
  for (const l of lines.slice(0, Math.floor(ph / lh))) {
    pCtx.font = "10px ui-monospace, monospace";
    pCtx.fillText(l, pw / 2, y);
    y += lh;
  }
}

function renderQrPreviewImage(raw) {
  if (!els.qrPreview) return;
  const pCtx = els.qrPreview.getContext("2d");
  const pw = els.qrPreview.width, ph = els.qrPreview.height;

  if (!raw) {
    pCtx.clearRect(0, 0, pw, ph);
    _qrPreviewRaw = null;
    return;
  }

  if (qrPreviewSrc(raw)) {
    // payload = link gambar/halaman → tampilkan gambar hasil decode (via proxy).
    // Fetch gambar hanya diulang saat payload berubah; render akhir canvas tetap
    // selalu terjadi tiap scan supaya tidak pernah menggantung kosong.
    if (_qrPreviewRaw !== raw) {
      loadQrPreviewImage(raw).then((img) => {
        if (!els.qrPreview || getQrState().raw !== raw) return;
        const c2 = els.qrPreview.getContext("2d");
        const w2 = els.qrPreview.width, h2 = els.qrPreview.height;
        c2.clearRect(0, 0, w2, h2);
        // Hindari NaN bila server balas 200 image/* tapi body kosong/0×0:
        // tanpa guard width/height, drawImage dengan NaN akan diam-diam no-op
        // dan canvas tampak kosong (transparan mengikuti bg GUI).
        if (img && img.naturalWidth > 0 && img.naturalHeight > 0) {
          const sc = Math.min(w2 / img.width, h2 / img.height);
          const dw = img.width * sc, dh = img.height * sc;
          c2.drawImage(img, (w2 - dw) / 2, (h2 - dh) / 2, dw, dh);
          _qrPreviewRaw = raw;
        } else {
          // gagal dimuat / body kosong (hotlink/CORS/timeout) → teks link hasil
          // decode di atas background putih. _qrPreviewRaw TIDAK diset supaya
          // scan berikutnya tetap mencoba lagi (bukan terkunci kosong permanen).
          paintQrText(c2, w2, h2, raw);
        }
      });
    }
    return;
  }

  // payload biasa (JSON / sisi / teks) → render teks hasil decode jadi gambar
  pCtx.clearRect(0, 0, pw, ph);
  paintQrText(pCtx, pw, ph, raw);
  _qrPreviewRaw = raw;
}

/* Skor ketajaman ("focus peaking" ala kamera foto) — varians Laplacian 4-neighbor
   di atas grayscale. Proxy standar "seberapa tajam", tanpa training/model apa pun.
   Skala BEDA dari cv2.Laplacian(CV_64F).var() Python (kernel & ukuran gambar beda) —
   dipakai RELATIF (lihat renderFocusReadout), bukan dibandingkan lintas-bahasa.
   Perhitungannya sendiri kini di qr-worker.js, dari pembacaan piksel yang sama
   dengan decode QR — nilainya identik, hanya tidak lagi memblokir main thread. */

/* Ambang RELATIF thd puncak yang baru terlihat (bukan angka mutlak di-hardcode) --
   pencahayaan/kamera beda bikin skala mentah beda, "mendekati puncak terakhir"
   lebih tahan variasi drpd angka tetap. Meluruh pelan supaya kalau operator geser
   ke target lain, puncak lama tak nyangkut selamanya sbg acuan palsu. */
let _focusRecentMax = 0;
const FOCUS_DECAY_PER_TICK = 0.98;   // ~200ms/tick -> puncak lama meluruh dlm puluhan detik

function renderFocusReadout(score) {
  if (!els.vQRFocus) return;
  _focusRecentMax = Math.max(score, _focusRecentMax * FOCUS_DECAY_PER_TICK);
  els.vQRFocus.textContent = Math.round(score);
  const isSharp = _focusRecentMax > 0 && score >= 0.9 * _focusRecentMax;
  if (els.qrFocusReadout) els.qrFocusReadout.classList.toggle("is-ok", isSharp);
}

async function scanControlQR() {
  if (currentPageName !== "control") return;
  if (document.hidden) return;   // jendela GUI di belakang popout kamera
  if (!els.camImg || !els.camImg.naturalWidth) return;
  const now = performance.now();
  if (now - _lastQrScan < 200) return;
  _lastQrScan = now;
  try {
    // decode + skor fokus dikerjakan di worker (qr-worker.js) dari SATU pembacaan
    // piksel; dulu main thread memanggil getImageData dua kali di sini.
    const { qr, sharpness } = await decodeClientQr(els.camImg, qrScanCanvas, 1280, { sharpness: true });
    setClientQr(qr ? qr.data : null);
    renderQrReadout();
    if (sharpness !== null) renderFocusReadout(sharpness);
    renderQrPreviewImage(getQrState().raw);
  } catch (e) { /* frame belum siap / cross-origin, lewati */ }
}
setInterval(scanControlQR, 200);

/*  WebSocket  */
let ws = null, demo = null, pingT = 0, linkStale = false;
function sendHookVisionConfig() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const wall = (CONFIG.CAMERAS || []).find((c) => String(c.role || "").toUpperCase() === "WALL");
  if (wall && wall.url) ws.send(JSON.stringify({ type: "hook_vision_config", url: wall.url }));
}
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
    sendHookVisionConfig();
    /* Beri tahu wahana kedalaman kolamnya. Di sisi ROV nilai ini membatasi
       depth_target supaya tombol SET tidak bisa merekam setpoint jauh
       melewati dasar. Dikirim di onopen (bukan sekali saat load) supaya ikut
       terkirim ulang setelah reconnect — rov_agent.py kehilangan nilainya
       kalau prosesnya sempat restart. */
    if (Number.isFinite(CONFIG.POOL_DEPTH)) sendCmd("pool_depth", CONFIG.POOL_DEPTH, true);
    if (CONFIG.AUTONOMY_MOTION_CONFIGURED && CONFIG.AUTONOMY_MOTION) {
      sendCmd("mission5_motion", autonomyMotionConfig(CONFIG.AUTONOMY_MOTION), true);
    }
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
    // Median dari sesi yang sudah putus bukan latensi apa pun — kosongkan,
    // jangan biarkan angka lama tertinggal seolah link masih terukur.
    resetLatency();
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
  else if (msg.type === "hook_vision") {
    // Worker YOLO laptop punya kanalnya sendiri: overlay tetap hidup saat
    // telemetri Pi mati/putus (uji darat kamera-saja), tidak menunggu hook_xy
    // yang cuma ikut menumpang paket telemetry.
    if (msg.data && msg.data.bbox) drawHookBbox({ ...msg.data, active_cam: "WALL" });
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
  else if (msg.type === "param_batch") {
    toPage("vehicle", "onParamBatch", msg);
    // Setup ikut mendengarkan: keenam gain PID-nya adalah param FC juga, jadi
    // form-nya ikut ter-update walau param diubah dari halaman Vehicle.
    toPage("setup", "onParamBatch", msg);
  }
  else if (msg.type === "param_ack") {
    /* log() di sini, BUKAN di dalam halaman: hasil Apply PID harus tetap
       terlihat di console walau halaman Vehicle belum pernah dibuka —
       toPage() membuang pesan untuk halaman yang belum di-init. */
    if (msg.ok) log(`Param ${msg.name} tersimpan di FC`, "ok");
    else log(`Param ${msg.name} GAGAL: ${msg.reason || "ditolak FC"}`, "err");
    toPage("vehicle", "onParamAck", msg);
  }
  else if (msg.type === "motor_test_ack") {
    // Balasan panel Thruster Test (Setup) — MAV_CMD_DO_MOTOR_TEST nyata atau mock SIM.
    if (msg.ok) log(`Uji thruster T${msg.motor} OK`, "ok");
    else log(`Uji thruster T${msg.motor} GAGAL: ${msg.reason || "tidak ada respon"}`, "err");
    toPage("setup", "onMotorTestAck", msg);
  }
   else if (msg.type === "camera_resolution_ack") {
    // Balasan panel Camera Stream (Setup) — restart mjpg-streamer di Pi.
    if (msg.ok) log(`Resolusi CAM ${Number(msg.camera) + 1} -> ${msg.resolution} OK`, "ok");
    else log(`Resolusi CAM ${Number(msg.camera) + 1} GAGAL: ${msg.reason || "tidak ada respon"}`, "err");
    toPage("setup", "onCameraResolutionAck", msg);
  }

  else if (msg.type === "mavlink_msg") { toPage("analyze", "onMavlinkMsg", msg); }
  else if (msg.type === "statustext") {
    // STATUSTEXT dari FC: inilah cara ArduSub melaporkan penolakan param &
    // error pre-arm. Tanpa ini pesannya cuma muncul di stdout Raspberry Pi.
    // severity MAVLink: 0..3 darurat/kritis, 4 warning, 5+ informasi.
    const sev = Number(msg.severity);
    log(`FC: ${msg.text}`, sev <= 3 ? "err" : sev === 4 ? "warn" : "");
    // ArduSub menolak motor test (mis. "10 second cooldown required...",
    // "motor test initialization failed!") lewat STATUSTEXT terpisah dari
    // motor_test_ack — ack cuma menandakan command TERKIRIM, bukan diterima
    // FC. Deteksi di sini supaya panel Thruster Test bisa mengunci slider
    // reaktif alih-alih menebak cooldown di muka.
    const t = String(msg.text || "").toLowerCase();
    if (t.includes("cooldown") || t.includes("motor test initialization failed")) {
      toPage("setup", "onMotorTestFail", msg);
    }
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
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
    return true;
  }
  log(`Tidak terkirim (koneksi terputus): ${obj.name || obj.type}`, "err");
  return false;
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
/* Pembacaan LATENCY.

   Dulu: satu ping per detik, dan ANGKA MENTAH sampel itu yang ditampilkan.
   Satu sampel per detik adalah penaksir yang buruk — kalau pong-nya kebetulan
   tiba saat main thread sedang menggambar satu frame, angkanya melonjak,
   padahal link-nya sendiri tidak berubah. Yang terbaca operator jadi loncat-
   loncat antara ~1 dan belasan ms tanpa sebab yang bisa ditindaklanjuti.

   Sekarang: ping 5 Hz, tampilkan MEDIAN 10 sampel terakhir (jendela ~2 detik)
   — cara baku `ping` melaporkan latensi link. Median tahan terhadap satu
   pencilan tapi tetap naik begitu latensi sungguhan naik. */
const LAT_WINDOW = 10;
const latSamples = [];

function setLatency(ms) {
  latSamples.push(ms);
  if (latSamples.length > LAT_WINDOW) latSamples.shift();
  const sorted = [...latSamples].sort((a, b) => a - b);
  const median = sorted[sorted.length >> 1];
  // Di LAN lokal nilainya pecahan milidetik; membulatkannya ke 0 menyembunyikan
  // perbedaan antara "0,4 ms" dan "4 ms" yang justru ingin dilihat.
  els.lat.textContent = median < 10 ? median.toFixed(1) : String(Math.round(median));
}

function resetLatency() {
  latSamples.length = 0;
  els.lat.textContent = "—";
}

// ping berkala untuk ukur latency
const PING_INTERVAL_MS = 200;
setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) sendPing(); }, PING_INTERVAL_MS);
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
  // Buang sampel latensi buatan simulator supaya median tidak tercampur
  // dengan pengukuran link sungguhan yang baru saja masuk.
  resetLatency();
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
  // <img> yang masih punya src tetap mendekode MJPEG walau halamannya
  // tersembunyi — beban dekode sia-sia yang berebut CPU dengan halaman lain
  // (pola sama camera.js::_applyStreamSrc). Lepas saat Control tak aktif.
  if (currentPageName !== "control") {
    els.camImg.removeAttribute("src");
    return;
  }
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

function cycleControlCamera(dir) {
  const sources = getControlCameraSources();
  if (sources.length < 2) return;
  controlCamIndex = (controlCamIndex + dir + sources.length) % sources.length;
  CONFIG.CAMERA_URL = sources[controlCamIndex];
  applyControlCamera();
  log(`Kamera kontrol: ${controlCamIndex + 1}`, "ok");
}

if (els.btnCamSwitch) {
  els.btnCamSwitch.onclick = () => cycleControlCamera(1);
}

/* Popout kamera ke jendela sendiri — untuk monitor tambahan. Proxy /cam berbagi
   satu koneksi upstream, jadi penonton tambahan tidak menambah beban kamera. */
const btnCamPopout = document.getElementById("btnCamPopout");
if (btnCamPopout) {
  btnCamPopout.onclick = () => {
    window.open(`cam.html?cam=${controlCamIndex}`, `hydroship-cam-${controlCamIndex}`,
                "width=1280,height=720");
  };
}

applyControlCamera();
window.addEventListener("hydroship:camera-url", applyControlCamera);
window.addEventListener("hydroship:camera-url", sendHookVisionConfig);

/*  kontrol UI  */
function toggleLight() { const v = !state.light; reflectLight(v); markPending("light", v); sendCmd("light", v); }
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
  log("⏹ EMERGENCY STOP — semua thruster netral", "err");
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
let pipLastDraw = 0;
function renderControlCamPiP(on) {
  const cv = document.getElementById("ctrlCamPipCanvas");
  const no = document.getElementById("ctrlCamPipNo");
  if (!cv) return;

  // scene.js berhenti render saat container-nya tak terlihat; PiP butuh tetap jalan
  if (scene && scene.setKeepAlive) scene.setKeepAlive(!!on);

  if (on) {
    const ctx = cv.getContext("2d");
    const loop = () => {
      controlCamPiPRaf = requestAnimationFrame(loop);
      if (document.hidden) return;
      // sumbernya (scene.js) sendiri hanya menggambar 30 fps — menyalin lebih
      // sering dari itu hanya menduplikasi frame yang sama
      const tNow = performance.now();
      if (tNow - pipLastDraw < 1000 / 30) return;
      pipLastDraw = tNow;
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

/* Pindahkan (bukan klon) mini AI + kompas ke dalam .cam saat kamera
   fullscreen, lalu kembalikan ke .stage saat keluar. Elemen fisiknya sama,
   jadi applyTelemetry() yang sudah menulis ke els.miniAIBall/miniCompassDial
   dst. via id tetap berfungsi tanpa duplikasi — instrumen hanya pindah
   parent, event listener dan referensi DOM tidak berubah. .stage
   sudah tersembunyi total begitu .cam fullscreen (beda panel), jadi tidak
   ada risiko instrumen "tampil dobel" di dua tempat sekaligus. */
const miniInstrumentsHome = els.miniInstruments && els.miniInstruments.parentElement;
function toggleMiniInstrumentsHost(fs) {
  if (!els.miniInstruments) return;
  const target = fs ? els.camStage : miniInstrumentsHome;
  if (target && els.miniInstruments.parentElement !== target) {
    target.appendChild(els.miniInstruments);
  }
}

/* Full Screen toggle untuk LIVE CAMERA di halaman Control */
const camFs = makeFullscreen(els.camStage, {
  onToggle: (fs) => {
    els.camFullLabel.textContent = fs ? "Exit Full" : "Full Screen";
    els.btnCamFull.setAttribute("aria-pressed", String(fs));
    renderControlCamPiP(fs);
    toggleMiniInstrumentsHost(fs);
  },
});
els.btnCamFull.onclick = () => camFs.toggle();

/* pilot mode tabs: Manual | Stabilize | Alt Hold | Pos Hold
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
  requestPilotMode.pending = mode;
  requestPilotMode.pendingSince = performance.now();
  sendCmd("pilot_mode", mode);
  log(`Minta mode pilot: ${label}`, "ok");
  syncModeTabs(lastPilotMode, lastPosHold);
}

// ARDUSUB_MODE_TO_TAB sekarang di shared/rov-modes.js (padanan rov_modes.py
// sisi Python), diimpor di atas.

// Pixhawk tidak menerima mode yang diminta dalam waktu ini -> beri peringatan.
const MODE_CONFIRM_TIMEOUT_MS = 2000;

/* Auto-repeat tombol mode "repeat" (D-pad depth). Jeda awal cukup panjang
   supaya tap tunggal tetap berarti SATU langkah 0.05 m, lalu pengulangan
   ~6.7 Hz (≈0.33 m/detik) — cukup cepat menempuh kolam 0.9 m, cukup lambat
   untuk dihentikan tepat waktu. */
const REPEAT_DELAY_MS = 400;
const REPEAT_INTERVAL_MS = 150;

let lastPilotMode = null;
// Overlay POSHOLD terakhir yang dilaporkan agent (lihat applyTelemetry).
let lastPosHold = false;
let modeTimeoutWarned = false;

function syncModeTabs(actualMode, posholdActive) {
  const actual = typeof actualMode === "string" ? actualMode : null;
  let activeTab = actual ? ARDUSUB_MODE_TO_TAB[actual] : null;

  /* ALT_HOLD bisa berarti dua tab: Alt Hold biasa, atau Pos Hold (ALT_HOLD +
     overlay heading-hold sisi Pi). ARDUSUB_MODE_TO_TAB tidak bisa membedakannya
     karena mode ArduSub-nya identik — flag dari agent yang memutuskan. */
  if (activeTab === "depth_hold" && posholdActive) activeTab = "poshold";

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

  // Mode aktual apa adanya — termasuk mode di luar tab yang ada (SURFACE,
  // POSHOLD, ...) yang memang tidak punya tab sendiri.
  if (els.modeActual) els.modeActual.textContent = actual || "—";

  // Diminta tapi tak kunjung dikonfirmasi: kemungkinan firmware menolak
  // atau link command putus.
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
let activeController = "Gamepad";
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

// GUI selalu start dalam mode Gamepad: samakan backend & badge status
// dengan seolah-olah tab Gamepad baru saja diklik, tanpa perlu klik manual.
sendCmd("controller", activeController);
logGamepadStatus();

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
/* Arrow ↑/↓ mengoperasikan DEPTH-SET (bukan axis heave, yang tetap di R/F).
   Peta terpisah dari KEY_AXIS supaya semantiknya tidak tercampur: ini event
   sekali-jalan, bukan nilai axis yang harus dinolkan saat tombol dilepas.

   ↑ = SET (rekam kedalaman saat ini), ↓ = ON/OFF. Sama seperti D-pad. */

function getDepthApplyTarget() {
  if (!els.depthTargetInput) return null;
  const target = Number(els.depthTargetInput.value);
  if (!Number.isFinite(target) || target < 0) {
    log("Target depth tidak valid. Masukkan angka >= 0 m.", "warn");
    return null;
  }
  return Math.round(target * 100) / 100;
}

function applyDepthTargetFromGui() {
  const target = getDepthApplyTarget();
  if (target == null) return false;
  sendCmd("depth_apply", target);
  log(`APPLY target depth: ${target.toFixed(2)} m`, "ok");
  return true;
}

/* ================= DEPTH TARGET BUTTONS ================= */

function applyDepthFromInput(inputId, label) {
  const input = document.getElementById(inputId);

  if (!input) {
    log(`${label}: input tidak ditemukan`, "err");
    return;
  }

  const target = Number(input.value);

  if (!Number.isFinite(target) || target < 0) {
    log(`${label}: target depth tidak valid`, "warn");
    return;
  }

  const depth = Math.round(target * 100) / 100;

  sendCmd("depth_apply", depth);

  log(`${label} → APPLY ${depth.toFixed(2)} m`, "ok");
}


/* DEPTH MASUK HOOK */
document.getElementById("btnDepthMasukHook")?.addEventListener("click", () => {
  applyDepthFromInput(
    "depthTargetInput",
    "DEPTH MASUK HOOK"
  );
});


/* DEPTH DASAR */
document.getElementById("btnDepthDasar")?.addEventListener("click", () => {
  applyDepthFromInput(
    "depthDasarInput",
    "DEPTH DASAR"
  );
});


/* DEPTH AMBIL HOOK */
document.getElementById("btnDepthAmbilHook")?.addEventListener("click", () => {
  applyDepthFromInput(
    "depthAmbilHookInput",
    "DEPTH AMBIL HOOK"
  );
});

const heldKeys = new Set();
function pilotKeyActive(e) {
  return activeController === "Keyboard" && e.target === document.body && KEY_AXIS[e.code];
}
function depthKeyActive(e) {
  return activeController === "Keyboard" && e.target === document.body && KEY_DEPTH[e.code];
}
window.addEventListener("keydown", (e) => {
  if (depthKeyActive(e)) {
    // Tanpa ini halaman ikut ter-scroll setiap kali operator mengatur kedalaman.
    e.preventDefault();
    // Auto-repeat OS DIABAIKAN (e.repeat): dulu menahan tombol memang berarti
    // "terus geser setpoint", tapi SET dan ON/OFF sekali-pencet — menahannya
    // hanya akan membuat saklar depth-set berkedip.
    if (e.repeat) return;
    // Arrow UP/DOWN = APPLY target absolut dari GUI.
    applyDepthTargetFromGui();
    return;
  }
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

/* Latensi stik->WS ditentukan laju POLL ini, bukan GP_SEND_HZ: begitu nilai axis
   berubah, sendCmd dipanggil saat itu juga (lihat gate `changed ||` di bawah);
   GP_SEND_HZ hanya laju resend untuk stik yang DITAHAN, supaya Pi tidak masuk
   fail-safe timeout. 30 Hz berarti input bisa tertahan sampai 33 ms sebelum
   terlihat. Alasan lama menahan di 30 Hz adalah alokasi array getGamepads() —
   biaya itu tidak berarti sekarang main thread tidak lagi tersumbat decode QR,
   dan 60 Hz memotong separuh penundaan deteksi. */
const GP_POLL_HZ = 60;
const GP_POLL_INTERVAL = 1000 / GP_POLL_HZ;
let gpLastPoll = 0;

// status tombol fisik frame sebelumnya
const gpBtnPrev = {};

/* Kapan tombol mode "repeat" boleh menembak lagi (timestamp performance.now()),
   per indeks tombol. Entri dihapus saat tombol dilepas. */
const gpRepeatAt = {};

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
  // Tanpa ini, tombol repeat yang sedang ditahan saat operator pindah ke
  // Keyboard akan langsung "jatuh tempo" begitu kembali ke Gamepad.
  for (const k in gpRepeatAt) delete gpRepeatAt[k];

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
      setControlMode("manual"); // abort sistem AUTONOMOUS kalau sedang aktif
      return;
    }

    case "toggle_control_mode": {
      els.btnMode.click();
      return;
    }

    // Emergency Stop: aksi terpisah dari mode_manual, supaya tombol yang
    // meminta pilot mode MANUAL dan tombol yang menghentikan seluruh
    // thruster tidak lagi menumpang pada action id yang sama.
    case "emergency_stop": {
      els.btnStop.click();
      return;
    }

    case "mode_stabilize": {
      requestPilotMode("stabilize", "STABILIZE");
      return;
    }

    case "mode_depth_hold": {
      requestPilotMode("depth_hold", "ALT HOLD");
      return;
    }

    case "mode_poshold": {
      requestPilotMode("poshold", "POS HOLD");
      return;
    }

    case "input_hold_set": {
      sendCmd("input_hold_set", true);
      log("Input hold set", "ok");
      return;
    }

    case "mount_center": {
      sendCmd("mount_center", true);
      log("Mount center", "ok");
      return;
    }

    /* ================= ON OFF CAMERA STREAM FOR SCAN QR ================= */
    case "camera_stream": {
      toggleCameraStreamFromJoystick();
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

    case "mount_tilt_up": {
        const pkt = Manipulator.rotateLeft();
        console.log("ROTATE LEFT =", pkt);
        sendPacket(pkt);
        return;
    }

    case "mount_tilt_down": {
        const pkt = Manipulator.rotateRight();
        console.log("ROTATE RIGHT =", pkt);
        sendPacket(pkt);
        return;
    }

    case "mount_tilt_stop": {
        const pkt = Manipulator.stopRotate();
        console.log("ROTATE STOP =", pkt);
        sendPacket(pkt, true);
        return;
    }

    /* ================= CAMERA SWITCH ================= */
    case "cam_prev":
    case "cam_next": {
      const dir = action === "cam_next" ? 1 : -1;
      if (pages.camera && pages.camera.style.display !== "none") {
        cameraPage.cycleCamera(dir);
      } else {
        cycleControlCamera(dir);
      }
      return;
    }

    case "camera_snapshot": {
      els.btnSnap.click();
      return;
    }

    case "toggle_record": {
      els.btnRec.click();
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

    case "toggle_light": {
      toggleLight();
      return;
    }

    case "thruster_gain_inc": {
      sendCmd("thruster_gain_inc", true);
      return;
    }

    case "thruster_gain_dec": {
      sendCmd("thruster_gain_dec", true);
      return;
    }

    /* ================= DEPTH TARGET PRESETS ================= */

    case "depth_masuk_hook": {
      const btn = document.getElementById("btnDepthMasukHook");

      if (btn) {
        btn.click();
        log("Joystick → DEPTH MASUK HOOK", "ok");
      } else {
        log("Button DEPTH MASUK HOOK tidak ditemukan", "err");
      }

      return;
    }

    case "depth_dasar": {
      const btn = document.getElementById("btnDepthDasar");

      if (btn) {
        btn.click();
        log("Joystick → DEPTH DASAR", "ok");
      } else {
        log("Button DEPTH DASAR tidak ditemukan", "err");
      }

      return;
    }

    case "depth_ambil_hook": {
      const btn = document.getElementById("btnDepthAmbilHook");

      if (btn) {
        btn.click();
        log("Joystick → DEPTH AMBIL HOOK", "ok");
      } else {
        log("Button DEPTH AMBIL HOOK tidak ditemukan", "err");
      }

      return;
    }
  }
}

function executeJoystickRelease(action) {
    if (!action || action === "no_function") return;

    switch (action) {

        /* grip_open/grip_close adalah nama HASIL migrasi dari actuator1_*.
           Tanpa case ini, gripper mode "hold" tidak pernah berhenti saat
           tombol dilepas. Nama lama tetap diterima untuk profil yang belum
           sempat dimigrasikan. */
        case "grip_open":
        case "grip_close":
            sendPacket(Manipulator.stopGrip(), true);

            return;
        case "mount_tilt_up":
        case "mount_tilt_down":
            sendPacket(Manipulator.stopRotate(), true);
            return;
        
    }
}

// Nama historis "grip" tapi sekarang mencakup semua aksi manipulator AUX
// (gripper + mount tilt) yang harus tetap bisa dipakai lepas dari otoritas
// manual/E-Stop navigasi ROV — lihat komentar di jalur AUX pada pollGamepad().
function isGripAction(action) {
  return (
    action === "grip_open" ||
    action === "grip_close" ||
    action === "mount_tilt_up" ||
    action === "mount_tilt_down"
  );
}

function toggleCameraStreamFromJoystick() {
  // Button 16 = Start/Stop stream kamera

  if (!initedModules.has("camera")) {
    showPage("camera");
  }

  if (!cameraPage || typeof cameraPage._toggleStream !== "function") {
    log("Kontrol stream kamera tidak tersedia", "err");
    return;
  }

  const wasStreaming = !!cameraPage.streaming;

  // Gunakan fungsi Start/Stop Stream yang sudah ada di camera.js
  cameraPage._toggleStream();

  if (cameraPage.streaming && !wasStreaming) {
    // Stream ON → pindah ke Camera
    showPage("camera");
    log("CAM 1 + CAM 2 STREAM ON — pindah ke Camera", "ok");
  } 
  else if (!cameraPage.streaming && wasStreaming) {
    // Stream OFF → kembali ke Control
    showPage("control");
    log("CAM 1 + CAM 2 STREAM OFF — kembali ke Control", "ok");
  }
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

    else if (row.mode === "repeat") {

      // sekali saat mulai ditekan, lalu berulang selama tetap ditahan
      if (rising) {
        gpRepeatAt[btnIndex] = performance.now() + REPEAT_DELAY_MS;
        executeJoystickAction(row.action, "repeat");
      } else if (current) {
        const due = gpRepeatAt[btnIndex];
        if (due != null && performance.now() >= due) {
          gpRepeatAt[btnIndex] = performance.now() + REPEAT_INTERVAL_MS;
          executeJoystickAction(row.action, "repeat");
        }
      }

      if (falling) delete gpRepeatAt[btnIndex];
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
    dari gamepad walau tab controller sedang di Keyboard.
  */
  processMappedGamepadButtons(isGripAction);

  // thruster control hanya aktif kalau dashboard controller = Gamepad
  if (activeController !== "Gamepad") {
    commitButtonCache();
    return;
  }

  /* Tombol non-gripper (mode_manual, emergency_stop, dll) HARUS tetap bisa
     ditekan walau mode = Autonomous / E-Stop aktif — itu justru satu-satunya
     cara pilot membatalkan autonomous dari joystick. Yang digerbangi cuma
     axis thruster di bawah. */
  processMappedGamepadButtons((a) => !isGripAction(a));

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

/* set surface level — backend menare state["depth"] dan mengonfirmasi lewat
   event "type":"event" (lihat handler `set_surface` di rov_agent.py), jadi
   log di sini tidak optimis di sisi klien lagi. */
$("btnSetSurface").onclick = () => {
  sendCmd("set_surface", true);
};

/* Counter trial Misi 2/3 — Control HANYA indikator (readout diisi lewat
   applyMissionCounter). Tombol aksinya ada di Setup, bukan di sini, supaya
   tidak tersenggol pilot saat pegang stik. */

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
function setControlMode(mode) {
  if (controlMode === mode) return;
  controlMode = mode;
  els.modeLabel.textContent = controlMode.toUpperCase();
  els.btnMode.setAttribute("aria-pressed", String(controlMode === "autonomous"));
  sendCmd("control_mode", controlMode);
  log(`Mode kontrol: ${controlMode.toUpperCase()}`, "ok");
}
els.btnMode.onclick = () => {
  setControlMode(controlMode === "manual" ? "autonomous" : "manual");
};

/* Ukur sumbatan main thread: buka GUI dengan ?perf=1 di URL. Setiap tugas yang
   memblokir >50 ms dilaporkan ke panel log. Badge LAT mengukur RTT ping/pong dari
   main thread, jadi tugas panjang di sini langsung menaikkan angka LAT — inilah
   cara membedakan "jaringan lambat" dari "browser sibuk". Mati secara default. */
if (new URLSearchParams(location.search).has("perf") && window.PerformanceObserver) {
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) log(`LONGTASK ${Math.round(e.duration)} ms`, "warn");
    }).observe({ entryTypes: ["longtask"] });
    log("Monitor longtask aktif (?perf=1)", "ok");
  } catch (_) { /* entryType tak didukung browser ini */ }
}

/*  mulai  */
log("HYDROSHIP dashboard siap", "ok");
loadSetup();
initIdentity();
tickClock();
setInterval(tickClock, 1000);
loadTheme();
loadCamContrast();
initScene();
connect();
refreshLastRun();
maybeDemo();
