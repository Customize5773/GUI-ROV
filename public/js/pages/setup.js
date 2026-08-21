// setup.js — Halaman Setup & Config.
// Tiap kartu fungsional: nilai disimpan ke CONFIG + localStorage dan dikirim ke
// ROV via sendCmd. Termasuk identitas tim (tampil di header).
import { CONFIG } from "../config.js";
import { log, sendCmd, wsSend } from "../core.js";

const LS_KEY = "hydroship-setup";

/* Padanan PID_PARAM_MAP di rov_pid.py. Ketiga sisi (agent, mock SIM, GUI)
   harus memakai nama param yang sama; kalau berbeda, form akan diam-diam
   menampilkan gain yang bukan gain yang ditulis. */
const PID_FIELD_BY_PARAM = {
  ATC_RAT_YAW_P: { id: "suYawP", path: ["yaw", "p"] },
  ATC_RAT_YAW_I: { id: "suYawI", path: ["yaw", "i"] },
  ATC_RAT_YAW_D: { id: "suYawD", path: ["yaw", "d"] },
  ATC_RAT_RLL_P: { id: "suRollP", path: ["roll", "p"] },
  ATC_RAT_RLL_I: { id: "suRollI", path: ["roll", "i"] },
  ATC_RAT_RLL_D: { id: "suRollD", path: ["roll", "d"] },
  ATC_RAT_PIT_P: { id: "suPitchP", path: ["pitch", "p"] },
  ATC_RAT_PIT_I: { id: "suPitchI", path: ["pitch", "i"] },
  ATC_RAT_PIT_D: { id: "suPitchD", path: ["pitch", "d"] },
  PSC_ACCZ_P: { id: "suDepP", path: ["depth", "p"] },
  PSC_ACCZ_I: { id: "suDepI", path: ["depth", "i"] },
  PSC_ACCZ_D: { id: "suDepD", path: ["depth", "d"] },
};
const PID_PARAM_NAMES = Object.keys(PID_FIELD_BY_PARAM);

/* Posisi & fungsi axis motor 1-6 sesuai tabel faktor BlueROV1 resmi
   (frame `bluerov`, FRAME_TYPE 0) — ardupilot.org/sub/docs/sub-frames.html.
   Motor 1&2 = surge+yaw (horizontal, kiri-kanan tengah); 3&4 = heave depan
   (vertikal); 5 = heave belakang-tengah (vertikal); 6 = lateral/sway
   (horizontal, tengah). `pair` mengelompokkan yang counter-rotate untuk
   pewarnaan diagram (A: 2&4, B: 1&3, C: 6&5) — lihat juga tabel di
   CONTROL-MAPPING.md §5.1. */
const MOTOR_LAYOUT = [
  { id: 4, x: 55,  y: 42,  label: "T4", type: "vertical",   pair: "A", axis: "Heave (depan-kiri)" },
  { id: 3, x: 145, y: 42,  label: "T3", type: "vertical",   pair: "B", axis: "Heave (depan-kanan)" },
  { id: 2, x: 30,  y: 100, label: "T2", type: "horizontal", pair: "A", axis: "Surge + Yaw (kiri)" },
  { id: 6, x: 100, y: 100, label: "T6", type: "horizontal", pair: "C", axis: "Lateral / Sway (tengah)" },
  { id: 1, x: 170, y: 100, label: "T1", type: "horizontal", pair: "B", axis: "Surge + Yaw (kanan)" },
  { id: 5, x: 100, y: 146, label: "T5", type: "vertical",   pair: "C", axis: "Heave (belakang-tengah)" },
];

/* Ikon SVG per orientasi thruster (dipusatkan di 0,0, discale/translate saat
   dirender). Vertikal: kipas 4 mata angin (dilihat dari atas). Horizontal:
   bowtie dua segitiga berhadapan (dorong depan-belakang/samping). */
function motorIcon(type) {
  if (type === "vertical") {
    return `<g class="motor-fin">
      <line x1="0" y1="-10" x2="0" y2="10"/>
      <line x1="-10" y1="0" x2="10" y2="0"/>
      <line x1="-7" y1="-7" x2="7" y2="7"/>
      <line x1="-7" y1="7" x2="7" y2="-7"/>
    </g>`;
  }
  return `<g class="motor-fin">
    <polygon points="-9,-5 -1,0 -9,5"/>
    <polygon points="9,-5 1,0 9,5"/>
  </g>`;
}

function saveSetup() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      TEAM_NAME: CONFIG.TEAM_NAME, UNIVERSITY: CONFIG.UNIVERSITY,
      CAMERAS: CONFIG.CAMERAS, THRUSTER: CONFIG.THRUSTER,
      POOL_DEPTH: CONFIG.POOL_DEPTH, DANGER_DEPTH: CONFIG.DANGER_DEPTH,
    }));
    /* PID SENGAJA TIDAK ikut disimpan: sumber kebenarannya sekarang flight
       controller, dibaca ulang tiap kali halaman ini dibuka. Menyimpannya
       berarti nilai basi di localStorage bisa menimpa nilai FC yang benar —
       termasuk skala LAMA yang berbahaya (yaw.p 2.0) dari versi sebelum
       gain dipetakan ke param ArduSub. */
  } catch (_) {}
}
export function loadSetup() {
  try {
    const s = JSON.parse(localStorage.getItem(LS_KEY) || "null");
    if (!s) return;
    if (typeof s.TEAM_NAME === "string") CONFIG.TEAM_NAME = s.TEAM_NAME;
    if (typeof s.UNIVERSITY === "string") CONFIG.UNIVERSITY = s.UNIVERSITY;
    if (Array.isArray(s.CAMERAS)) CONFIG.CAMERAS = s.CAMERAS;
    if (s.THRUSTER) Object.assign(CONFIG.THRUSTER, s.THRUSTER);
    // s.PID sengaja DIABAIKAN — lihat catatan di saveSetup(). Entri lama yang
    // masih ada di localStorage operator lama ikut terbuang di sini.
    if (Number.isFinite(s.POOL_DEPTH)) CONFIG.POOL_DEPTH = s.POOL_DEPTH;
    if (Number.isFinite(s.DANGER_DEPTH)) CONFIG.DANGER_DEPTH = s.DANGER_DEPTH;
    // s.DEPTH_DEFAULT sengaja DIABAIKAN: setpoint depth-set tidak lagi berasal
    // dari angka yang diketik, melainkan dari tombol SET. Entri lama di
    // localStorage operator ikut terbuang di sini.
  } catch (_) {}
}

const numField = (id, label, val, step = "1", unit = "") => `
  <label class="field field--sm"><span>${label}${unit ? ` <small>${unit}</small>` : ""}</span>
    <input id="${id}" type="number" step="${step}" value="${val}" /></label>`;

export const setupPage = {
  els: {},

  init(root) {
    loadSetup();
    const host = location.hostname || "localhost";
    const T = CONFIG.THRUSTER, P = CONFIG.PID;
    const lanUrl = `http://${host}:${location.port || 8080}`;

    root.innerHTML = `
      <div class="setup">
        <div class="setup__grid">

          <!-- TEAM IDENTITY -->
          <div class="card">
            <span class="panel__eyebrow">TEAM IDENTITY</span>
            <h3 class="card__title">Tim &amp; Perguruan Tinggi</h3>
            <label class="field"><span>Nama Tim</span><input id="suTeam" type="text" value="${CONFIG.TEAM_NAME || ""}" /></label>
            <label class="field"><span>Perguruan Tinggi</span><input id="suUni" type="text" value="${CONFIG.UNIVERSITY || ""}" /></label>
            <button class="btn-wide" id="suApplyIdent">Apply</button>
          </div>

          <!-- CAMERA STREAM -->
          <div class="card">
            <span class="panel__eyebrow">CAMERA STREAM</span>
            <h3 class="card__title">Bottom &amp; Wall Cameras</h3>
            <p class="card__desc">URL stream MJPEG/WebRTC dari Raspberry Pi.</p>
            <label class="field field--grow"><span>CAM 1 — BOTTOM</span>
              <input id="suCam0" type="text" placeholder="http://192.168.2.2:8080/stream" value="${(CONFIG.CAMERAS[0]||{}).url || ""}" /></label>
            <label class="field field--grow"><span>CAM 2 — WALL</span>
              <input id="suCam1" type="text" placeholder="http://192.168.2.3:8080/stream" value="${(CONFIG.CAMERAS[1]||{}).url || ""}" /></label>
            <div class="card__row">
              <button class="btn-wide btn-wide--inline" id="suApplyCam">Apply</button>
              <button class="chip" id="suOpenCam">Open Camera Page</button>
            </div>
          </div>

          <!-- THRUSTER SETUP -->
          <div class="card">
            <span class="panel__eyebrow">THRUSTER SETUP</span>
            <h3 class="card__title">ArduSub Mixer Gain</h3>
            <p class="card__desc">Frame, batas PWM, gain &amp; arah putar (maks 6 thruster).</p>
            <label class="field"><span>Frame</span>
              <select id="suFrame">
                <option ${T.frame === "bluerov" ? "selected" : ""}>bluerov</option>
                <option ${T.frame === "Vectored_6DOF" ? "selected" : ""}>Vectored_6DOF</option>
                <option ${T.frame === "Vectored" ? "selected" : ""}>Vectored</option>
                <option ${T.frame === "Custom" ? "selected" : ""}>Custom</option>
              </select></label>
            <div class="card__row card__row--wrap">
              ${numField("suPwmMin", "PWM Min", T.pwmMin, "10", "us")}
              ${numField("suPwmNeutral", "Neutral", T.pwmNeutral, "10", "us")}
              ${numField("suPwmMax", "PWM Max", T.pwmMax, "10", "us")}

             <div class="thruster-gain-wrap">
              ${numField("suGain", "Gain", T.gain, "5", "%")}

              <div class="thruster-gain-buttons">
                <button
                  id="suGainDown"
                  class="thruster-gain-btn thruster-gain-btn--down"
                  type="button"
                  title="Kurangi Gain 10%"
                >−</button>

                <button
                  id="suGainUp"
                  class="thruster-gain-btn thruster-gain-btn--up"
                  type="button"
                  title="Tambah Gain 10%"
                >+</button>
              </div>
            </div>
            </div>
            <label class="card__label">Reverse arah thruster</label>
            <div class="toggles" id="suReverse"></div>
            <button class="btn-wide" id="suApplyThruster">Apply Thruster Config</button>
          </div>

          <!-- THRUSTER TEST -->
          <div class="card">
            <span class="panel__eyebrow">THRUSTER TEST</span>
            <h3 class="card__title">Uji Spin Per-Thruster</h3>
            <p class="card__desc">
              Meniru Motor Test QGroundControl/ArduSub: geser slider tiap thruster
              untuk memutarnya sebentar (atas = maju, bawah = mundur) — pastikan
              baling-baling &amp; area sekitar bebas hambatan dulu. Angka % di
              bawah slider menunjukkan throttle yang sedang dikirim. Slider
              kembali ke tengah otomatis; thruster berhenti sendiri setelah
              durasi singkat. Centang "Rev" untuk membalik polaritas satu
              thruster (terhubung ke toggle "Reverse arah thruster" di kartu
              THRUSTER SETUP — satu sumber kebenaran, panel ini alat uji, bukan
              konfigurasi baru).
            </p>
            <span class="badge" id="suMotorTestWarn">Slider aktif hanya saat wahana ARMED — uji di darat/tertambat</span>
            <svg id="suMotorSvg" viewBox="0 0 200 190" class="motor-diagram">
              <rect x="10" y="14" width="180" height="164" rx="16" fill="none" stroke="currentColor" opacity="0.3"/>
              <polygon class="hull-front-marker" points="100,4 93,16 107,16"/>
              ${MOTOR_LAYOUT.map((m) => `
                <g transform="translate(${m.x},${m.y})">
                  <circle id="motorDot${m.id}" cx="0" cy="0" r="14" class="motor-dot motor-dot--pair${m.pair}"></circle>
                  ${motorIcon(m.type)}
                </g>
                <text x="${m.x}" y="${m.y + 24}" text-anchor="middle" font-size="11">${m.label}</text>
              `).join("")}
            </svg>
            <label class="field field--sm"><span>Durasi tiap uji <small>s</small></span>
              <input id="suMtDuration" type="number" step="0.1" min="0.2" max="2" value="1" /></label>
            <div id="suMotorButtons"></div>
          </div>

          <!-- PID SETUP -->
          <div class="card">
            <span class="panel__eyebrow">PID SETUP</span>
            <h3 class="card__title">Hold Control Gains</h3>
            <p class="card__desc">
              Gain kontrol hold Yaw, Roll, Pitch &amp; Depth. Nilainya dibaca langsung dari
              flight controller — Yaw = <code>ATC_RAT_YAW_*</code>, Roll = <code>ATC_RAT_RLL_*</code>,
              Pitch = <code>ATC_RAT_PIT_*</code>, Depth = <code>PSC_ACCZ_*</code>.
              Untuk param lain pakai halaman <b>Vehicle</b>.
            </p>
            <span class="badge" id="suPidSrc">Belum dibaca dari FC</span>
            <label class="card__label">Yaw <small>ATC_RAT_YAW</small></label>
            <div class="card__row card__row--wrap">
              ${numField("suYawP", "P", P.yaw.p, "0.01")} ${numField("suYawI", "I", P.yaw.i, "0.001")} ${numField("suYawD", "D", P.yaw.d, "0.001")}
            </div>
            <label class="card__label">Roll <small>ATC_RAT_RLL</small></label>
            <div class="card__row card__row--wrap">
              ${numField("suRollP", "P", P.roll.p, "0.01")} ${numField("suRollI", "I", P.roll.i, "0.001")} ${numField("suRollD", "D", P.roll.d, "0.001")}
            </div>
            <label class="card__label">Pitch <small>ATC_RAT_PIT</small></label>
            <div class="card__row card__row--wrap">
              ${numField("suPitchP", "P", P.pitch.p, "0.01")} ${numField("suPitchI", "I", P.pitch.i, "0.001")} ${numField("suPitchD", "D", P.pitch.d, "0.001")}
            </div>
            <label class="card__label">Depth <small>PSC_ACCZ</small></label>
            <div class="card__row card__row--wrap">
              ${numField("suDepP", "P", P.depth.p, "0.01")} ${numField("suDepI", "I", P.depth.i, "0.01")} ${numField("suDepD", "D", P.depth.d, "0.01")}
            </div>
            <div class="card__row">
              <button class="btn-wide btn-wide--inline" id="suApplyPid">Apply PID Gains</button>
              <button class="chip" id="suReadPid">Baca dari FC</button>
            </div>
          </div>

          <!-- TEST POOL -->
          <div class="card">
            <span class="panel__eyebrow">TEST POOL</span>
            <h3 class="card__title">Pool &amp; Danger Depth</h3>
            <p class="card__desc">Kedalaman kolam (kalibrasi altitude + batas atas depth-set)
              &amp; ambang alarm. Setpoint depth-set sendiri direkam dari tombol SET di
              dashboard, bukan diisi di sini.</p>
            <div class="card__row card__row--wrap">
              ${numField("suPool", "Pool depth", CONFIG.POOL_DEPTH, "0.1", "m")}
              ${numField("suDanger", "Danger depth", CONFIG.DANGER_DEPTH, "0.1", "m")}
            </div>
            <button class="btn-wide" id="suApplyPool">Apply</button>
            <span class="card__info" id="suPoolInfo">Pool ${CONFIG.POOL_DEPTH.toFixed(2)} m · Alarm ≥ ${CONFIG.DANGER_DEPTH.toFixed(2)} m</span>
          </div>

          <!-- MOBILE COMPANION -->
          <div class="card">
            <span class="panel__eyebrow">MOBILE COMPANION</span>
            <h3 class="card__title">Viewer Access</h3>
            <p class="card__desc">Buka dashboard dari perangkat lain di jaringan umbilical yang sama.</p>
            <div class="card__row">
              <span class="badge badge--ok card__badge" id="suViewerBadge"><span class="dot"></span> ACCESS OPEN</span>
              <a class="card__link" id="suViewerLink" href="${lanUrl}" target="_blank" rel="noopener">${lanUrl}</a>
            </div>
            <div class="card__row">
              <button class="chip" id="suCopyLink">Copy Link</button>
              <button class="chip" id="suToggleAccess" aria-pressed="true">Access: Open</button>
            </div>
          </div>

        </div>
      </div>`;

    /* TEAM IDENTITY */
    root.querySelector("#suApplyIdent").onclick = () => {
      CONFIG.TEAM_NAME = root.querySelector("#suTeam").value.trim() || "Nama Tim";
      CONFIG.UNIVERSITY = root.querySelector("#suUni").value.trim() || "Perguruan Tinggi";
      const t = document.getElementById("identTeam"), u = document.getElementById("identUni");
      if (t) t.textContent = CONFIG.TEAM_NAME;
      if (u) u.textContent = CONFIG.UNIVERSITY;
      saveSetup();
      log("Identitas tim disimpan", "ok");
    };

    /* CAMERA */
    root.querySelector("#suApplyCam").onclick = () => {
      [0, 1].forEach((i) => {
        const url = root.querySelector(`#suCam${i}`).value.trim();
        if (CONFIG.CAMERAS[i]) CONFIG.CAMERAS[i].url = url;
        if (i === 0) CONFIG.CAMERA_URL = url;
      });
      saveSetup();
      // beri tahu halaman Control untuk mengarahkan ulang feed kamera-nya
      window.dispatchEvent(new Event("hydroship:camera-url"));
      log("URL kamera disimpan", "ok");
    };
    root.querySelector("#suOpenCam").onclick = () => document.querySelector('.sidebar__link[data-page="camera"]')?.click();

    /* THRUSTER */
    const revWrap = root.querySelector("#suReverse");
    CONFIG.THRUSTER.reversed.forEach((on, i) => {
      const b = document.createElement("button");
      b.className = "toggle" + (on ? " toggle--on" : "");
      b.textContent = "T" + (i + 1);
      b.setAttribute("aria-pressed", String(on));
      b.onclick = () => {
        const v = b.getAttribute("aria-pressed") !== "true";
        b.setAttribute("aria-pressed", String(v));
        b.classList.toggle("toggle--on", v);
      };
      revWrap.appendChild(b);
    });

    root.querySelector("#suApplyThruster").onclick = () => {
      const min = parseInt(root.querySelector("#suPwmMin").value, 10);
      const neu = parseInt(root.querySelector("#suPwmNeutral").value, 10);
      const max = parseInt(root.querySelector("#suPwmMax").value, 10);
      const gain = parseInt(root.querySelector("#suGain").value, 10);

      if (![min, neu, max].every(Number.isFinite) || !(min < neu && neu < max)) {
        log("PWM tidak valid (Min < Neutral < Max)", "warn");
        return;
      }

      if (!Number.isFinite(gain)) {
        log("Gain tidak valid", "warn");
        return;
      }

      Object.assign(CONFIG.THRUSTER, {
        pwmMin: min,
        pwmNeutral: neu,
        pwmMax: max,
        gain: Math.max(0, Math.min(100, gain)),
        reversed: [...revWrap.children].map(
          (b) => b.getAttribute("aria-pressed") === "true"
        ),
      });

      saveSetup();

      const motors = {};

      CONFIG.THRUSTER.reversed.forEach((rev, index) => {
        motors[String(index + 1)] = rev ? -1 : 1;
      });

      wsSend({
        type: "cmd",
        name: "thruster_config",
        gain: CONFIG.THRUSTER.gain,
        motors
      });

      log(
        `Thruster configuration dikirim — gain ${CONFIG.THRUSTER.gain}%`,
        "ok"
      );
    };

    root.querySelector("#suGainUp")?.addEventListener("click", () => {
  const input = root.querySelector("#suGain");
  const current = Number(input?.value ?? CONFIG.THRUSTER.gain);

  const gain = Math.min(100, current + 10);

  if (input) input.value = gain;
  CONFIG.THRUSTER.gain = gain;

  /* Sengaja TANPA `motors`. Tombol gain dulu ikut mengirim seluruh peta
     MOT_n_DIRECTION hasil rekaan localStorage — dan itu MENULIS ULANG arah
     putar keenam thruster di Pixhawk hanya karena operator menaikkan daya
     10%.

     Ini bukan bahaya teoretis. reversed[] hidup di localStorage browser, BUKAN
     dibaca dari FC, jadi browser yang belum pernah menekan tombol Rev akan
     mengirim "semua +1" dan diam-diam membalik motor yang di FC memang -1.
     Trial 21 Agu 2026 memperlihatkan akibatnya: gain dinaikkan 80% -> 100%
     di antara ROV6.log dan ROV7.log, dan roll ALT_HOLD saat stik netral
     jatuh dari +0,20 ± 2,23 ke -3,39 ± 2,73 tanpa satu pun gain PID diubah.

     Arah putar thruster hanya boleh berubah lewat tombol Apply yang eksplisit
     di panel Thruster, tidak pernah sebagai efek samping perintah lain. */
  wsSend({
    type: "cmd",
    name: "thruster_config",
    gain
  });

  saveSetup();
  log(`Thruster Gain → ${gain}%`, "ok");
});

root.querySelector("#suGainDown")?.addEventListener("click", () => {
  const input = root.querySelector("#suGain");
  const current = Number(input?.value ?? CONFIG.THRUSTER.gain);

  const gain = Math.max(0, current - 10);

  if (input) input.value = gain;
  CONFIG.THRUSTER.gain = gain;

  // Tanpa `motors` — lihat alasan lengkap di handler #suGainUp di atas.
  wsSend({
    type: "cmd",
    name: "thruster_config",
    gain
  });

  saveSetup();
  log(`Thruster Gain → ${gain}%`, "ok");
});

    /* THRUSTER TEST — slider dua-arah + checkbox Reversed per thruster,
       meniru Motor Test QGroundControl/ArduSub. Diagnostik saja: checkbox
       Reversed di sini adalah TOMBOL YANG SAMA dengan toggle "Reverse arah
       thruster" di kartu THRUSTER SETUP (revWrap.children[i]) — bukan state
       kedua, supaya tidak ada dua sumber kebenaran untuk reversed[]. */
    const MOTOR_TEST_THROTTLE_MAX = 20; // selaras MOTOR_TEST_MAX_THROTTLE di rov_motor_test.py
    // ArduSub TIDAK menjalankan motor test "sekali kirim, muter N detik" —
    // param duration/timeout kita diABAIKAN firmware (lihat handle_do_motor_test
    // di ArduSub/motors.cpp: "timeout_s = command.param4; // not used").
    // Yang sebenarnya dipakai: GUI harus kirim ULANG command ini >=2Hz selama
    // motor diminta muter (persis Motor Test QGroundControl). Berhenti kirim
    // >500ms -> firmware anggap "Motor test timed out!" DAN men-disarm ROV
    // (AP::arming().disarm(...MOTORTEST)). Itu bukan bug, itu memang perilaku
    // safety ArduSub — makanya lepas slider = ROV perlu di-ARM ulang sebelum
    // test/kendali berikutnya.
    const MOTOR_TEST_STREAM_MS = 200; // < 500ms batas timeout, kasih margin
    // Cooldown 10s ArduSub HANYA dikenakan kalau init_motor_test() gagal
    // sebelumnya (bukan wajib setelah tiap test sukses) — lihat
    // last_do_motor_test_fail_ms di source. Jadi kita tidak lagi mengunci
    // slider preventif; cukup kunci reaktif kalau FC benar2 menolak.
    const MOTOR_TEST_FAIL_COOLDOWN_MS = 10500;
    const motorButtonsWrap = root.querySelector("#suMotorButtons");
    motorButtonsWrap.className = "motor-test-row";
    let motorTestCooldownUntil = 0;
    const allMotorSliders = [];
    const allMotorStatusEls = [];
    MOTOR_LAYOUT.forEach((m) => {
      const revBtn = revWrap.children[m.id - 1];
      const col = document.createElement("div");
      col.className = "motor-test-col";
      col.innerHTML = `
        <span class="card__label">${m.label}</span>
        <div class="motor-slider-wrap">
          <input type="range" class="motor-slider motor-slider--v" orient="vertical"
                 min="-${MOTOR_TEST_THROTTLE_MAX}" max="${MOTOR_TEST_THROTTLE_MAX}" value="0" step="1" data-motor="${m.id}" />
          <span class="motor-pct" id="suMotorPct${m.id}">0%</span>
        </div>
        <label class="field field--sm field--inline"><input type="checkbox" data-reversed-for="${m.id}" ${revBtn.getAttribute("aria-pressed") === "true" ? "checked" : ""} /> Rev</label>
        <span class="card__info card__info--sm" id="suMotorStatus${m.id}"></span>
        <small>${m.axis}</small>`;
      motorButtonsWrap.appendChild(col);

      const slider = col.querySelector(".motor-slider");
      const pctEl = col.querySelector(".motor-pct");
      const revCheckbox = col.querySelector("[data-reversed-for]");
      const statusEl = col.querySelector("#suMotorStatus" + m.id);
      const dot = document.getElementById("motorDot" + m.id);
      allMotorSliders.push(slider);
      allMotorStatusEls.push(statusEl);

      // Checkbox ini cuma "wajah kedua" dari tombol T-n yang sudah ada di
      // kartu THRUSTER SETUP — klik di sini menekan tombol aslinya juga.
      revCheckbox.onchange = () => {
        revBtn.setAttribute("aria-pressed", String(revCheckbox.checked));
        revBtn.classList.toggle("toggle--on", revCheckbox.checked);
      };

      const stopVisual = () => dot.classList.remove("motor-dot--active", "motor-dot--fwd", "motor-dot--rev");

      let streamTimer = null;
      const stopStream = () => {
        if (streamTimer) { clearInterval(streamTimer); streamTimer = null; }
        stopVisual();
        if (statusEl.textContent === "menguji...") {
          statusEl.textContent = "";
          log(`T${m.id}: slider dilepas — ROV akan DISARM otomatis (timeout ArduSub), ARM ulang sebelum lanjut`, "warn");
        }
      };

      // Kirim ulang command SELAMA slider ditahan, bukan sekali saat digeser:
      // ArduSub butuh >=2Hz do_motor_test masuk atau dianggap timeout (lihat
      // catatan MOTOR_TEST_STREAM_MS di atas).
      const sendMotorTestTick = () => {
        if (Date.now() < motorTestCooldownUntil) return;

        const raw = Number(slider.value);
        if (raw === 0) { stopStream(); return; }

        const reversed = revCheckbox.checked;
        // Slider "maju" selalu berarti maju secara visual bagi operator;
        // koreksi polaritas kalau thruster ditandai Reversed, sama seperti QGC.
        const effective = reversed ? -raw : raw;
        const throttle = Math.min(MOTOR_TEST_THROTTLE_MAX, Math.abs(effective));
        const direction = effective >= 0 ? "forward" : "reverse";

        // duration dikirim tapi diabaikan firmware — dipertahankan supaya
        // payload konsisten dengan rov_motor_test.py di sisi Pi.
        wsSend({ type: "cmd", name: "motor_test", motor: m.id, throttle, duration: 1.0, direction });

        pctEl.textContent = `${direction === "forward" ? "▲" : "▼"} ${throttle}%`;
        dot.classList.add("motor-dot--active", direction === "forward" ? "motor-dot--fwd" : "motor-dot--rev");
        statusEl.textContent = "menguji...";
      };

      // Gerbang ARMED dicek lewat `disabled` (lihat syncArmedState di bawah),
      // bukan di sini — kalau dicek tiap tick oninput, tiap tick memaksa
      // slider.value = 0 selama drag berlangsung dan terasa seperti macet.
      slider.oninput = () => {
        const raw = Number(slider.value);
        if (raw === 0) { pctEl.textContent = "0%"; stopStream(); return; }

        sendMotorTestTick(); // kirim tick pertama langsung, jangan tunggu interval
        if (!streamTimer) streamTimer = setInterval(sendMotorTestTick, MOTOR_TEST_STREAM_MS);

        const reversed = revCheckbox.checked;
        const effective = reversed ? -raw : raw;
        const direction = effective >= 0 ? "forward" : "reverse";
        log(`Uji T${m.id} ${direction === "forward" ? "maju" : "mundur"} ${Math.min(MOTOR_TEST_THROTTLE_MAX, Math.abs(effective))}% (ditahan)`, "");
      };

      // Kembali ke tengah saat dilepas — meniru slider QGC yang snap ke 0.
      // `mouseleave` sengaja TIDAK dipakai: trek slider tipis, gerakan mouse
      // sedikit melenceng saat drag vertikal gampang memicunya dan me-reset
      // slider secara prematur di tengah-tengah drag.
      const resetSlider = () => { slider.value = 0; pctEl.textContent = "0%"; stopStream(); };
      slider.addEventListener("pointerup", resetSlider);
      slider.addEventListener("touchend", resetSlider);
      slider.addEventListener("change", resetSlider);
    });

    // Slider hanya bisa digeser saat ARMED — direfleksikan via `disabled`
    // (bukan dicek berulang di dalam oninput, lihat catatan di atas) dan
    // disinkronkan reaktif tanpa perlu reload halaman.
    const armBtnEl = document.getElementById("btnArm");
    const syncMotorTestArmedState = () => {
      const armed = armBtnEl?.getAttribute("aria-pressed") === "true";
      const cooling = Date.now() < motorTestCooldownUntil;
      allMotorSliders.forEach((s) => { s.disabled = !armed || cooling; });
    };
    if (armBtnEl) {
      new MutationObserver(syncMotorTestArmedState)
        .observe(armBtnEl, { attributes: true, attributeFilter: ["aria-pressed"] });
    }
    syncMotorTestArmedState();

    // Dipanggil REAKTIF saat FC benar-benar menolak motor test (bukan lagi
    // preventif setelah tiap test sukses — lihat catatan
    // MOTOR_TEST_FAIL_COOLDOWN_MS di atas). Kunci semua slider karena
    // cooldown ArduSub berlaku global, bukan per-motor.
    let motorTestCooldownTimer = null;
    function startMotorTestFailCooldown() {
      motorTestCooldownUntil = Date.now() + MOTOR_TEST_FAIL_COOLDOWN_MS;
      syncMotorTestArmedState();
      clearInterval(motorTestCooldownTimer);
      motorTestCooldownTimer = setInterval(() => {
        const remaining = motorTestCooldownUntil - Date.now();
        if (remaining <= 0) {
          clearInterval(motorTestCooldownTimer);
          syncMotorTestArmedState();
          allMotorStatusEls.forEach((el) => { if (el.textContent.startsWith("cooldown")) el.textContent = ""; });
          return;
        }
        const secs = Math.ceil(remaining / 1000);
        allMotorStatusEls.forEach((el) => { el.textContent = `cooldown ${secs}s`; });
      }, 250);
    }
    this._startMotorTestFailCooldown = startMotorTestFailCooldown;

    /* PID */
    this.els.pidSrc = root.querySelector("#suPidSrc");

    /* Tandai kolom yang sudah disentuh operator tapi belum di-Apply, supaya
       param_batch yang masuk belakangan tidak menghapus angka yang sedang
       disiapkan. Menjaga `document.activeElement` saja tidak cukup: begitu
       operator mengklik kolom lain, fokusnya pindah dan nilainya jadi rawan
       tertimpa oleh batch berikutnya (mis. saat halaman Vehicle memuat ulang
       seluruh tabel param). */
    this.pidDirty = new Set();
    for (const name of PID_PARAM_NAMES) {
      const input = root.querySelector(`#${PID_FIELD_BY_PARAM[name].id}`);
      if (input) input.addEventListener("input", () => this.pidDirty.add(name));
    }

    // Tombol ini niat eksplisit operator untuk membuang draft dan memakai
    // nilai FC — jadi boleh menimpa kolom yang sudah disentuh.
    root.querySelector("#suReadPid").onclick = () => {
      this.pidDirty.clear();
      this.readPidFromVehicle();
    };

    root.querySelector("#suApplyPid").onclick = () => {
      /* Sengaja BUKAN `parseFloat(...) || 0`: kolom kosong atau salah ketik
         akan diam-diam jadi 0, dan 0 adalah nilai yang sah untuk beberapa gain
         (mis. ATC_RAT_YAW_P = 0 mematikan kendali rate yaw). Kolom yang tidak
         berisi angka harus menggagalkan perintah, bukan menulis 0. */
      const kosong = [];
      const g = (id, label) => {
        const v = parseFloat(root.querySelector(id).value);
        if (!Number.isFinite(v)) kosong.push(label);
        return v;
      };
      const next = {
        yaw: { p: g("#suYawP", "Yaw P"), i: g("#suYawI", "Yaw I"), d: g("#suYawD", "Yaw D") },
        roll: { p: g("#suRollP", "Roll P"), i: g("#suRollI", "Roll I"), d: g("#suRollD", "Roll D") },
        pitch: { p: g("#suPitchP", "Pitch P"), i: g("#suPitchI", "Pitch I"), d: g("#suPitchD", "Pitch D") },
        depth: { p: g("#suDepP", "Depth P"), i: g("#suDepI", "Depth I"), d: g("#suDepD", "Depth D") },
      };
      if (kosong.length) { log(`PID tidak dikirim — kolom kosong/tidak valid: ${kosong.join(", ")}`, "warn"); return; }

      CONFIG.PID = next;
      // Sudah dikirim: kolom tidak lagi "draft", jadi echo dari FC boleh
      // menimpanya (dan memang harus — itu bukti nilai benar-benar masuk).
      this.pidDirty.clear();
      // PID tidak ikut saveSetup(): sumber kebenarannya FC, bukan localStorage.
      sendCmd("pid", CONFIG.PID);
      log("PID dikirim — menunggu konfirmasi FC", "");
    };

    /* POOL + DANGER */
    this.els.poolInfo = root.querySelector("#suPoolInfo");
    root.querySelector("#suApplyPool").onclick = () => {
      const pool = parseFloat(root.querySelector("#suPool").value);
      const danger = parseFloat(root.querySelector("#suDanger").value);
      if (!Number.isFinite(pool) || pool < 0) { log("Pool depth tidak valid", "warn"); return; }
      CONFIG.POOL_DEPTH = pool;
      if (Number.isFinite(danger) && danger > 0) CONFIG.DANGER_DEPTH = danger;
      this.els.poolInfo.textContent = `Pool ${CONFIG.POOL_DEPTH.toFixed(2)} m · Alarm ≥ ${CONFIG.DANGER_DEPTH.toFixed(2)} m`;
      saveSetup();
      // beri tahu Control agar depth-tape di-skala ulang mengikuti pool depth baru
      window.dispatchEvent(new Event("hydroship:pool-depth"));
      sendCmd("pool_depth", CONFIG.POOL_DEPTH);
      log(`Pool ${CONFIG.POOL_DEPTH.toFixed(2)} m, danger ${CONFIG.DANGER_DEPTH.toFixed(2)} m`, "ok");
    };

    /* MOBILE COMPANION */
    root.querySelector("#suCopyLink").onclick = async () => {
      try { await navigator.clipboard.writeText(root.querySelector("#suViewerLink").href); log("Link viewer disalin", "ok"); }
      catch (_) { log("Gagal menyalin link", "warn"); }
    };
    const accBtn = root.querySelector("#suToggleAccess"), accBadge = root.querySelector("#suViewerBadge");
    accBtn.onclick = () => {
      const open = accBtn.getAttribute("aria-pressed") !== "true";
      accBtn.setAttribute("aria-pressed", String(open));
      accBtn.textContent = open ? "Access: Open" : "Access: Closed";
      accBadge.className = "badge card__badge " + (open ? "badge--ok" : "badge--fault");
      accBadge.innerHTML = `<span class="dot"></span> ${open ? "ACCESS OPEN" : "ACCESS CLOSED"}`;
      sendCmd("viewer_access", open);
      log(`Viewer access ${open ? "dibuka" : "ditutup"}`, open ? "ok" : "warn");
    };

    this.readPidFromVehicle();
  },

  /* Baca ulang tiap kali halaman dibuka: init() hanya jalan sekali seumur
     sesi, jadi tanpa ini form jadi basi setelah FC tersambung ulang atau
     setelah param diubah dari halaman Vehicle. */
  onShow() { this.readPidFromVehicle(); },

  /* param_get bersifat fire-and-forget: agent mengirim param_request_read_send
     tanpa retry, jadi SATU paket hilang di link serial/UDP = tidak ada
     PARAM_VALUE = badge diam di "Belum dibaca dari FC" selamanya, tanpa
     operator tahu apakah FC bisu atau dia yang lupa menekan tombol. Satu retry
     + satu pesan kegagalan menutup lubang itu. */
  readPidFromVehicle(retry = true) {
    for (const name of PID_PARAM_NAMES) sendCmd("param_get", name);

    clearTimeout(this._pidReadTimer);
    this._pidReadSeq = (this._pidReadSeq || 0) + 1;
    const seq = this._pidReadSeq;
    this._pidReadTimer = setTimeout(() => {
      // Batch yang datang lebih dulu menaikkan _pidReadSeq lewat onParamBatch,
      // jadi timer yang basi tidak boleh berbuat apa-apa.
      if (seq !== this._pidReadSeq || this._pidReadOk === seq) return;
      if (retry) { this.readPidFromVehicle(false); return; }
      this.els.pidSrc.textContent = "FC tidak menjawab";
      this.els.pidSrc.className = "badge badge--warn";
    }, 2000);
  },

  /* Isi kolom PID dari PARAM_VALUE yang dikirim wahana.
     Dicocokkan berdasarkan NAMA, bukan `index`: di wahana nyata jawaban
     param_get lewat jalur batch yang sama dengan param_list sehingga membawa
     index asli dari FC, sedangkan mock SIM mengirim -1. Nama satu-satunya
     kunci yang konsisten di kedua sisi. */
  onParamBatch(msg) {
    if (!msg || !Array.isArray(msg.params) || !this.els.pidSrc) return;

    let terisi = 0;
    for (const p of msg.params) {
      const field = PID_FIELD_BY_PARAM[p && p.name];
      if (!field) continue;

      const input = document.getElementById(field.id);
      if (!input || !Number.isFinite(Number(p.value))) continue;

      /* Jangan menimpa yang sedang disiapkan operator. param_batch bisa datang
         kapan saja — terutama saat halaman Vehicle memuat ulang seluruh tabel
         param — dan menimpa angka yang sedang diketik akan menghapusnya tanpa
         jejak. Tombol "Baca dari FC" membersihkan penanda ini kalau operator
         memang ingin membuang draft-nya. */
      if (document.activeElement === input || this.pidDirty.has(p.name)) continue;

      input.value = String(Number(Number(p.value).toFixed(6)));
      const [axis, gain] = field.path;
      CONFIG.PID[axis][gain] = Number(p.value);
      terisi++;
    }

    if (terisi) {
      this._pidReadOk = this._pidReadSeq;   // matikan timer "FC tidak menjawab"
      const jam = new Date().toLocaleTimeString();
      this.els.pidSrc.textContent = `Dari FC · ${jam}`;
      this.els.pidSrc.className = "badge badge--ok";
    }
  },

  /* Balasan MAV_CMD_DO_MOTOR_TEST (nyata) atau mock SIM — lihat panel
     THRUSTER TEST. Cuma memperbarui badge kecil per-thruster. */
  onMotorTestAck(msg) {
    if (!msg || !msg.motor) return;
    const el = document.getElementById("suMotorStatus" + msg.motor);
    if (!el) return;
    el.textContent = msg.ok ? "OK" : `gagal${msg.reason ? ": " + msg.reason : ""}`;
  },

  // Dipicu app.js saat STATUSTEXT FC menandakan motor test ditolak
  // (cooldown / initialization failed) — lihat catatan MOTOR_TEST_FAIL_COOLDOWN_MS
  // di init(). Mengunci semua slider selama masa cooldown ArduSub.
  onMotorTestFail() {
    if (typeof this._startMotorTestFailCooldown === "function") {
      this._startMotorTestFailCooldown();
    }
  },

  onThrusterGainTelemetry(gain) {
    const value = Number(gain);
    if (!Number.isFinite(value)) return;

    const clamped = Math.max(0, Math.min(100, value));

    const input = document.getElementById("suGain");
    if (input) input.value = clamped;

    CONFIG.THRUSTER.gain = clamped;
  },
};
