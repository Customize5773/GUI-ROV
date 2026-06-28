// camera.js — Halaman Camera (KKI 2026): tampilkan 2 kamera bersamaan
// (CAM 1 = BOTTOM, CAM 2 = WALL) + deteksi QR Code di feed BOTTOM.
// QR menentukan sisi dinding (A/B/C/D) tempat payload digantung.
import { CONFIG } from "../config.js";
import { log, num, snapshotImage, makeFullscreen } from "../core.js";

export const cameraPage = {
  streaming: false,
  scanRaf: null,
  scanCanvas: null,
  lastQR: "",
  visible: false,
  els: {},

  init(root) {
    const cams = CONFIG.CAMERAS || [];
    root.innerHTML = `
      <div class="campage">
        <div class="campage__head">
          <div>
            <span class="panel__eyebrow">VISION</span>
            <h2 class="tele__title">Live Cameras &amp; QR</h2>
          </div>
          <div class="campage__head-actions">
            <button class="chip chip--go" id="camStart">Start Stream</button>
            <span class="badge" id="camState">IDLE</span>
          </div>
        </div>

        <div class="camgrid" id="camGrid"></div>

        <div class="qrpanel" id="qrPanel">
          <div class="qrpanel__main">
            <span class="panel__eyebrow">QR CODE DETECTION</span>
            <div class="qr__row">
              <div class="qr__side" id="qrSide">—</div>
              <div class="qr__info">
                <span class="qr__status" id="qrStatus">Menunggu feed BOTTOM…</span>
                <span class="qr__data" id="qrData">No QR detected</span>
                <span class="qr__time" id="qrTime"></span>
              </div>
            </div>
          </div>
          <div class="qrpanel__actions">
            <label class="chip" for="qrFile">Scan dari gambar</label>
            <input id="qrFile" type="file" accept="image/*" hidden />
            <button class="chip" id="qrClear">Clear</button>
          </div>
        </div>

        <div class="campage__cfg" id="camCfg"></div>
      </div>`;

    // dua sel kamera
    const grid = root.querySelector("#camGrid");
    this.els.cells = cams.map((c, i) => {
      const cell = document.createElement("div");
      cell.className = "camcell";
      cell.innerHTML = `
        <div class="camcell__bar">
          <span class="camcell__name">${c.id} <b>${c.role || ""}</b></span>
          <button class="chip chip--ghost" data-full="${i}">⛶</button>
        </div>
        <div class="camcell__view" id="camView${i}">
          <img id="camImg${i}" alt="${c.id}" />
          <div class="hud">
            <div class="hud__compass" id="camHdg${i}">HDG —°</div>
            <div class="hud__rp"><span id="camDepth${i}">D —m</span> <span id="camAlt${i}">ALT —m</span></div>
          </div>
          <div class="cam__nosignal" id="camNo${i}"><span>${(c.role || c.id)} NOT CONNECTED</span></div>
        </div>`;
      grid.appendChild(cell);
      const img = cell.querySelector(`#camImg${i}`);
      const no = cell.querySelector(`#camNo${i}`);
      img.onload = () => { no.style.display = "none"; };
      img.onerror = () => { no.style.display = "flex"; img.removeAttribute("src"); };
      // fullscreen per sel
      const view = cell.querySelector(`#camView${i}`);
      const fs = makeFullscreen(view);
      cell.querySelector(`[data-full="${i}"]`).onclick = () => fs.toggle();
      return { cell, img, no };
    });

    // config URL per kamera
    const cfg = root.querySelector("#camCfg");
    cams.forEach((c, i) => {
      const wrap = document.createElement("div");
      wrap.className = "camcfg__row";
      wrap.innerHTML = `
        <label class="field field--grow"><span>${c.id} — ${c.role || ""} URL</span>
          <input id="camUrl${i}" type="text" placeholder="http://192.168.2.2:8080/?action=stream" value="${c.url || ""}" />
        </label>
        <button class="btn-wide btn-wide--inline" data-apply="${i}">Apply</button>`;
      cfg.appendChild(wrap);
      wrap.querySelector(`[data-apply="${i}"]`).onclick = () => {
        const url = wrap.querySelector(`#camUrl${i}`).value.trim();
        c.url = url;
        if (i === 0) CONFIG.CAMERA_URL = url;
        this.els.cells[i].img.crossOrigin = "anonymous"; // izinkan getImageData utk QR (perlu CORS server)
        if (this.streaming && url) this.els.cells[i].img.src = url;
        log(`URL ${c.id} (${c.role}) diset`, "ok");
      };
    });

    // QR panel
    this.els.qrSide = root.querySelector("#qrSide");
    this.els.qrStatus = root.querySelector("#qrStatus");
    this.els.qrData = root.querySelector("#qrData");
    this.els.qrTime = root.querySelector("#qrTime");
    root.querySelector("#qrClear").onclick = () => this._setQR(null, "Menunggu feed BOTTOM…");
    root.querySelector("#qrFile").onchange = (e) => this._scanFile(e.target.files[0]);

    root.querySelector("#camStart").onclick = () => this._toggleStream();
    this.els.state = root.querySelector("#camState");

    this.scanCanvas = document.createElement("canvas");
  },

  onShow() { this.visible = true; if (!this.scanRaf) this._scanLoop(); },
  onHide() { this.visible = false; if (this.scanRaf) { cancelAnimationFrame(this.scanRaf); this.scanRaf = null; } },

  onTelemetry(d) {
    const alt = Number.isFinite(d.depth) ? Math.max(0, CONFIG.POOL_DEPTH - d.depth) : null;
    (this.els.cells || []).forEach((_, i) => {
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
      set(`camHdg${i}`, "HDG " + num(d.heading, 0) + "°");
      set(`camDepth${i}`, "D " + num(d.depth, 2) + "m");
      set(`camAlt${i}`, "ALT " + num(alt, 2) + "m");
    });
  },

  _toggleStream() {
    this.streaming = !this.streaming;
    this.els.state.textContent = this.streaming ? "LIVE" : "IDLE";
    this.els.state.classList.toggle("badge--active", this.streaming);
    (CONFIG.CAMERAS || []).forEach((c, i) => {
      const cell = this.els.cells[i];
      if (this.streaming && c.url) { cell.img.crossOrigin = "anonymous"; cell.img.src = c.url; }
      else { cell.img.removeAttribute("src"); cell.no.style.display = "flex"; }
    });
    log(this.streaming ? "Stream kamera dimulai" : "Stream kamera dihentikan", this.streaming ? "ok" : "warn");
  },

  /* loop scan QR dari kamera BOTTOM (indeks 0) */
  _scanLoop() {
    this.scanRaf = requestAnimationFrame(() => this._scanLoop());
    if (!this.visible || !window.jsQR) return;
    const img = this.els.cells && this.els.cells[0] && this.els.cells[0].img;
    if (!img || !img.naturalWidth) return;
    // throttle: ~6x/detik
    const now = performance.now();
    if (this._lastScan && now - this._lastScan < 160) return;
    this._lastScan = now;
    const code = this._decode(img);
    if (code) this._setQR(code.data, "QR terdeteksi");
    else if (this.els.qrStatus.textContent === "Menunggu feed BOTTOM…") this.els.qrStatus.textContent = "Memindai…";
  },

  _decode(source, w, h) {
    const cv = this.scanCanvas;
    cv.width = w || source.naturalWidth || source.width;
    cv.height = h || source.naturalHeight || source.height;
    const ctx = cv.getContext("2d", { willReadFrequently: true });
    try {
      ctx.drawImage(source, 0, 0, cv.width, cv.height);
      const data = ctx.getImageData(0, 0, cv.width, cv.height);
      return window.jsQR(data.data, cv.width, cv.height);
    } catch (e) {
      // CORS taint → getImageData diblokir
      this.els.qrStatus.textContent = "Feed cross-origin: aktifkan CORS di server kamera, atau pakai 'Scan dari gambar'";
      return null;
    }
  },

  _scanFile(file) {
    if (!file || !window.jsQR) return;
    const im = new Image();
    im.onload = () => {
      const code = this._decode(im, im.naturalWidth, im.naturalHeight);
      if (code) this._setQR(code.data, "QR dari gambar");
      else this._setQR(null, "Tidak ada QR pada gambar");
    };
    im.src = URL.createObjectURL(file);
  },

  /* tampilkan hasil QR + sisi A/B/C/D */
  _setQR(data, status) {
    this.els.qrStatus.textContent = status || "";
    if (!data) {
      this.els.qrSide.textContent = "—";
      this.els.qrSide.className = "qr__side";
      this.els.qrData.textContent = "No QR detected";
      this.els.qrTime.textContent = "";
      this.lastQR = "";
      return;
    }
    this.els.qrData.textContent = data;
    // ambil huruf A-D yang berdiri sendiri (tidak diapit huruf lain), mis. "A", "SIDE_B", "WALL-C"
    const m = String(data).toUpperCase().match(/(?<![A-Z])([ABCD])(?![A-Z])/);
    const side = m ? m[1] : "?";
    this.els.qrSide.textContent = side;
    this.els.qrSide.className = "qr__side qr__side--" + (side === "?" ? "unknown" : "ok");
    this.els.qrTime.textContent = new Date().toLocaleTimeString("id-ID", { hour12: false });
    if (data !== this.lastQR) { log(`QR terbaca: "${data}" → sisi ${side}`, "ok"); this.lastQR = data; }
  },
};
