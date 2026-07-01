// setup.js — Halaman Setup & Config.
// Tiap kartu fungsional: nilai disimpan ke CONFIG + localStorage dan dikirim ke
// ROV via sendCmd. Termasuk identitas tim (tampil di header).
import { CONFIG } from "../config.js";
import { log, sendCmd } from "../core.js";

const LS_KEY = "hydroship-setup";

function saveSetup() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      TEAM_NAME: CONFIG.TEAM_NAME, UNIVERSITY: CONFIG.UNIVERSITY,
      CAMERAS: CONFIG.CAMERAS, THRUSTER: CONFIG.THRUSTER, PID: CONFIG.PID,
      POOL_DEPTH: CONFIG.POOL_DEPTH, DANGER_DEPTH: CONFIG.DANGER_DEPTH,
    }));
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
    if (s.PID) CONFIG.PID = s.PID;
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
              <input id="suCam0" type="text" placeholder="http://192.168.2.2:8080/?action=stream" value="${(CONFIG.CAMERAS[0]||{}).url || ""}" /></label>
            <label class="field field--grow"><span>CAM 2 — WALL</span>
              <input id="suCam1" type="text" placeholder="http://192.168.2.3:8080/?action=stream" value="${(CONFIG.CAMERAS[1]||{}).url || ""}" /></label>
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
                <option ${T.frame === "Vectored" ? "selected" : ""}>Vectored</option>
                <option ${T.frame === "Vectored_6DOF" ? "selected" : ""}>Vectored_6DOF</option>
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
            <p class="card__desc">Gain kontrol hold untuk Yaw &amp; Depth.</p>
            <label class="card__label">Yaw</label>
            <div class="card__row card__row--wrap">
              ${numField("suYawP", "P", P.yaw.p, "0.1")} ${numField("suYawI", "I", P.yaw.i, "0.01")} ${numField("suYawD", "D", P.yaw.d, "0.1")}
            </div>
            <label class="card__label">Depth</label>
            <div class="card__row card__row--wrap">
              ${numField("suDepP", "P", P.depth.p, "0.1")} ${numField("suDepI", "I", P.depth.i, "0.01")} ${numField("suDepD", "D", P.depth.d, "0.1")}
            </div>
            <button class="btn-wide" id="suApplyPid">Apply PID Gains</button>
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
        frame: root.querySelector("#suFrame").value, pwmMin: min, pwmNeutral: neu, pwmMax: max,
        gain: Math.max(0, Math.min(200, gain || 100)),
        reversed: [...revWrap.children].map((b) => b.getAttribute("aria-pressed") === "true"),
      });
      saveSetup();
      sendCmd("thruster_config", CONFIG.THRUSTER);
      log(`Thruster config dikirim — ${CONFIG.THRUSTER.frame}, gain ${CONFIG.THRUSTER.gain}%`, "ok");
    };

    /* PID */
    root.querySelector("#suApplyPid").onclick = () => {
      const g = (id) => parseFloat(root.querySelector(id).value) || 0;
      CONFIG.PID = { yaw: { p: g("#suYawP"), i: g("#suYawI"), d: g("#suYawD") }, depth: { p: g("#suDepP"), i: g("#suDepI"), d: g("#suDepD") } };
      saveSetup();
      sendCmd("pid", CONFIG.PID);
      log("PID dikirim", "ok");
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
  },
};
