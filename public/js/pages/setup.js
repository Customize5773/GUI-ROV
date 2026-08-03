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
  PSC_ACCZ_P: { id: "suDepP", path: ["depth", "p"] },
  PSC_ACCZ_I: { id: "suDepI", path: ["depth", "i"] },
  PSC_ACCZ_D: { id: "suDepD", path: ["depth", "d"] },
};
const PID_PARAM_NAMES = Object.keys(PID_FIELD_BY_PARAM);

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
              ${numField("suGain", "Gain", T.gain, "5", "%")}
            </div>
            <label class="card__label">Reverse arah thruster</label>
            <div class="toggles" id="suReverse"></div>
            <button class="btn-wide" id="suApplyThruster">Apply Thruster Config</button>
          </div>

          <!-- PID SETUP -->
          <div class="card">
            <span class="panel__eyebrow">PID SETUP</span>
            <h3 class="card__title">Hold Control Gains</h3>
            <p class="card__desc">
              Gain kontrol hold Yaw &amp; Depth. Nilainya dibaca langsung dari flight
              controller — Yaw = <code>ATC_RAT_YAW_*</code>, Depth = <code>PSC_ACCZ_*</code>.
              Untuk param lain pakai halaman <b>Vehicle</b>.
            </p>
            <span class="badge" id="suPidSrc">Belum dibaca dari FC</span>
            <label class="card__label">Yaw <small>ATC_RAT_YAW</small></label>
            <div class="card__row card__row--wrap">
              ${numField("suYawP", "P", P.yaw.p, "0.01")} ${numField("suYawI", "I", P.yaw.i, "0.001")} ${numField("suYawD", "D", P.yaw.d, "0.001")}
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
            <p class="card__desc">Kedalaman kolam (kalibrasi altitude) &amp; ambang alarm.</p>
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
      if (![min, neu, max].every(Number.isFinite) || !(min < neu && neu < max)) { log("PWM tidak valid (Min < Neutral < Max)", "warn"); return; }
      Object.assign(CONFIG.THRUSTER, {
          pwmMin: min,
          pwmNeutral: neu,
          pwmMax: max,
          gain: Math.max(0, Math.min(200, gain || 100)),
          reversed: [...revWrap.children].map((b) => b.getAttribute("aria-pressed") === "true"),
      });
      saveSetup();
      const motors = {};

      CONFIG.THRUSTER.reversed.forEach((rev, index) => {
          motors[String(index + 1)] = rev ? -1 : 1;
      });

      wsSend({
          type: "cmd",
          name: "thruster_config",
          motors
      });

      log("Thruster configuration dikirim", "ok");
      log(`Thruster config dikirim — ${CONFIG.THRUSTER.frame}, gain ${CONFIG.THRUSTER.gain}%`, "ok");
    };

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

  readPidFromVehicle() {
    for (const name of PID_PARAM_NAMES) sendCmd("param_get", name);
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
      const jam = new Date().toLocaleTimeString();
      this.els.pidSrc.textContent = `Dari FC · ${jam}`;
      this.els.pidSrc.className = "badge badge--ok";
    }
  },
};
