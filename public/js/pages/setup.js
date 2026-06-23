// setup.js — Halaman Setup: 7 kartu konfigurasi (sebagian interaktif).
import { CONFIG } from "../config.js";
import { log } from "../core.js";

export const setupPage = {
  els: {},

  init(root) {
    const host = location.hostname || "localhost";
    root.innerHTML = `
      <div class="setup">
        <div class="setup__grid">

          <div class="card">
            <span class="panel__eyebrow">CAMERA STREAM</span>
            <h3 class="card__title">Raspberry Camera</h3>
            <p class="card__desc">Umpan WebRTC/MJPEG dari Raspberry Pi pada ROV.</p>
            <a class="card__link" id="suCamLink" href="#" target="_blank" rel="noopener">${CONFIG.CAMERA_URL || "stream URL belum diset"}</a>
            <button class="btn-wide" id="suOpenCam">Open Camera Setup</button>
          </div>

          <div class="card">
            <span class="panel__eyebrow">UNITY STREAM</span>
            <h3 class="card__title">Unity VR</h3>
            <label class="card__label">Unity stream URL</label>
            <input class="card__input" id="suUnity" type="text" placeholder="Not set yet" />
            <button class="btn-wide" id="suApplyUnity">Apply Unity Stream</button>
          </div>

          <div class="card">
            <span class="panel__eyebrow">THRUSTER SETUP</span>
            <h3 class="card__title">ArduSub Mixer Gain</h3>
            <p class="card__desc">Konfigurasi mixer & gain thruster untuk ArduSub.</p>
            <span class="card__info">ArduSub default · Power 200 us</span>
            <button class="btn-wide" id="suOpenThruster">Open Thruster Setup</button>
          </div>

          <div class="card">
            <span class="panel__eyebrow">PID SETUP</span>
            <h3 class="card__title">Hold Control Gains</h3>
            <p class="card__desc">Gain kontrol hold untuk yaw &amp; depth.</p>
            <span class="card__info">Yaw 2.00 · Depth 10.00</span>
            <button class="btn-wide" id="suOpenPid">Open PID Setup</button>
          </div>

          <div class="card">
            <span class="panel__eyebrow">TEST POOL</span>
            <h3 class="card__title">Pool Depth</h3>
            <p class="card__desc">Kedalaman kolam uji untuk kalibrasi skala depth.</p>
            <div class="card__row">
              <input class="card__input card__input--sm" id="suPool" type="number" step="0.1" min="0" value="${CONFIG.POOL_DEPTH.toFixed(1)}" />
              <span class="card__unit">m</span>
              <button class="btn-wide btn-wide--inline" id="suApplyPool">Apply</button>
            </div>
            <span class="card__info" id="suPoolInfo">Pool depth ${CONFIG.POOL_DEPTH.toFixed(2)} m</span>
            <small class="card__note">Dipakai untuk menormalkan tampilan depth tape.</small>
          </div>

          <div class="card">
            <span class="panel__eyebrow">MOBILE COMPANION</span>
            <h3 class="card__title">Viewer Access</h3>
            <p class="card__desc">Akses pemantauan read-only dari perangkat lain.</p>
            <span class="badge badge--ok card__badge"><span class="dot"></span> ACCESS OPEN</span>
            <a class="card__link" href="http://${host}:3000/view.html" target="_blank" rel="noopener">http://${host}:3000/view.html</a>
          </div>

          <div class="card card--wide">
            <span class="panel__eyebrow">DIGITAL TWIN MODEL</span>
            <h3 class="card__title">LSTM Model Manager</h3>
            <p class="card__desc">Kelola model LSTM untuk deteksi fault pose-change pada halaman Telemetry. Impor folder model lalu pilih untuk diaktifkan.</p>
            <label class="card__label">Available model</label>
            <div class="card__row">
              <select class="card__input" id="suModel"><option>No model installed</option></select>
              <button class="chip" id="suUseModel">Use Selected Model</button>
              <button class="chip" id="suImportModel">Import Model Folder</button>
              <button class="chip" id="suRefreshModel">Refresh</button>
            </div>
            <span class="card__warn" id="suModelWarn">⚠ No valid LSTM model is installed</span>
            <small class="card__note">Model di-load oleh server; tampilan ini memakai estimasi smoothing sampai model nyata terpasang.</small>
          </div>

        </div>
      </div>`;

    // Camera link + open
    this.els.camLink = root.querySelector("#suCamLink");
    if (CONFIG.CAMERA_URL) this.els.camLink.href = CONFIG.CAMERA_URL;
    root.querySelector("#suOpenCam").onclick = () => {
      document.querySelector('.sidebar__link[data-page="camera"]')?.click();
    };

    // Unity
    root.querySelector("#suApplyUnity").onclick = () => {
      const url = root.querySelector("#suUnity").value.trim();
      CONFIG.UNITY_URL = url;
      log(url ? `Unity stream diset: ${url}` : "Unity stream dikosongkan", url ? "ok" : "warn");
    };

    // Pool depth
    this.els.poolInfo = root.querySelector("#suPoolInfo");
    root.querySelector("#suApplyPool").onclick = () => {
      const v = parseFloat(root.querySelector("#suPool").value);
      if (!Number.isFinite(v) || v < 0) { log("Pool depth tidak valid", "warn"); return; }
      CONFIG.POOL_DEPTH = v;
      this.els.poolInfo.textContent = `Pool depth ${v.toFixed(2)} m`;
      log(`Pool depth diset ${v.toFixed(2)} m`, "ok");
    };

    // Stub buttons (log intent)
    const stub = (id, msg) => { const b = root.querySelector(id); if (b) b.onclick = () => log(msg, ""); };
    stub("#suOpenThruster", "Buka Thruster Setup (belum tersedia)");
    stub("#suOpenPid", "Buka PID Setup (belum tersedia)");
    stub("#suUseModel", "Tidak ada model untuk dipilih");
    stub("#suImportModel", "Import Model Folder (belum tersedia)");
    stub("#suRefreshModel", "Refresh daftar model — tidak ada model terpasang");
  },
};
