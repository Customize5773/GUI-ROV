// camera.js — Halaman Camera: satu feed besar + pengaturan sumber/stream.
import { CONFIG } from "../config.js";
import { pilotAxes, log, num, snapshotImage, createRecorder } from "../core.js";

export const cameraPage = {
  active: 0,
  streaming: false,
  hud: true,
  recorder: null,
  recording: false,
  fpsCount: 0,
  fps: 0,
  fpsTimer: null,
  els: {},

  init(root) {
    const cams = CONFIG.CAMERAS || [];
    root.innerHTML = `
      <div class="campage">
        <div class="campage__head">
          <div>
            <span class="panel__eyebrow">VISION</span>
            <h2 class="tele__title">Live Camera</h2>
          </div>
          <div class="campage__sources" id="camSources"></div>
          <div class="campage__head-actions">
            <button class="chip chip--go" id="camStart">Start Stream</button>
            <span class="badge" id="camState">IDLE</span>
            <button class="chip" id="camFull">⛶ Full Screen</button>
          </div>
        </div>

        <div class="campage__viewport" id="camViewport">
          <img id="cpImg" alt="Umpan kamera ROV" />
          <div class="cam__overlay">
            <span class="cam__res" id="cpRes">—</span>
            <span class="cam__rec" id="cpRec"></span>
          </div>
          <div class="hud">
            <div class="hud__compass" id="cpHeading">HDG —°</div>
            <div class="hud__rp"><span id="cpDepth">D —m</span><span id="cpRP">R —° P —°</span></div>
          </div>
          <div class="cam__nosignal" id="cpNoSignal"><span>ROV CAMERA NOT CONNECTED</span><small>set stream URL below</small></div>
          <div class="campage__bar">
            <button class="chip" id="cpSnap">Snapshot</button>
            <button class="chip" id="cpRecBtn" aria-pressed="false">Record</button>
            <button class="chip" id="cpHud" aria-pressed="true">HUD</button>
          </div>
        </div>

        <div class="campage__cfg">
          <label class="field field--grow"><span>Stream URL (sumber aktif)</span>
            <input id="camUrl" type="text" placeholder="http://192.168.2.2:8080/?action=stream" />
          </label>
          <button class="btn-wide btn-wide--inline" id="camApply">Apply</button>
        </div>

        <div class="infogrid" id="camInfo"></div>
      </div>`;

    // sumber kamera
    const srcWrap = root.querySelector("#camSources");
    cams.forEach((c, i) => {
      const b = document.createElement("button");
      b.className = "chip" + (i === 0 ? " chip--active" : "");
      b.textContent = c.id;
      b.onclick = () => this._select(i);
      srcWrap.appendChild(b);
    });
    this.els.sources = [...srcWrap.children];

    this.els.img = root.querySelector("#cpImg");
    this.els.res = root.querySelector("#cpRes");
    this.els.rec = root.querySelector("#cpRec");
    this.els.noSignal = root.querySelector("#cpNoSignal");
    this.els.state = root.querySelector("#camState");
    this.els.heading = root.querySelector("#cpHeading");
    this.els.depth = root.querySelector("#cpDepth");
    this.els.rp = root.querySelector("#cpRP");
    this.els.url = root.querySelector("#camUrl");
    this.els.viewport = root.querySelector("#camViewport");
    this.els.hudEl = root.querySelector(".campage__viewport .hud");

    this.els.img.onload = () => {
      this.els.noSignal.style.display = "none";
      this.els.res.textContent = `${this.els.img.naturalWidth}×${this.els.img.naturalHeight}`;
      this.fpsCount++;
    };
    this.els.img.onerror = () => { this.els.noSignal.style.display = "flex"; this.els.res.textContent = "—"; };

    root.querySelector("#camStart").onclick = () => this._toggleStream();
    root.querySelector("#camApply").onclick = () => this._applyUrl();
    root.querySelector("#camFull").onclick = () => this._fullscreen();
    root.querySelector("#cpSnap").onclick = () => {
      if (!snapshotImage(this.els.img)) { log("Tidak ada frame untuk snapshot", "warn"); return; }
      log("Snapshot kamera diambil", "ok");
    };
    const recBtn = root.querySelector("#cpRecBtn");
    recBtn.onclick = () => this._toggleRecord(recBtn);
    const hudBtn = root.querySelector("#cpHud");
    hudBtn.onclick = () => {
      this.hud = !this.hud;
      hudBtn.setAttribute("aria-pressed", String(this.hud));
      this.els.hudEl.style.display = this.hud ? "flex" : "none";
    };

    // info grid
    const cells = [
      ["STREAM", "Waiting", "stream"], ["RECORDER", "Idle", "rec"], ["LINK", "Offline sim", "link"],
      ["CONTROL", "Keyboard", "ctl"], ["COMMAND", "Su0 Sw0 Y0 V0", "cmd"], ["POWER/FAULT", "0.0A · PWM 0", "pwr"],
    ];
    const infoWrap = root.querySelector("#camInfo");
    cells.forEach(([k, v, id]) => {
      const el = document.createElement("div");
      el.className = "infocell";
      el.innerHTML = `<span class="infocell__k">${k}</span><span class="infocell__v" id="ci-${id}">${v}</span>`;
      infoWrap.appendChild(el);
      this.els[`ci_${id}`] = el.querySelector(`#ci-${id}`);
    });

    this._select(0);
  },

  onShow() {
    this.fpsTimer = setInterval(() => { this.fps = this.fpsCount; this.fpsCount = 0; this._updateInfo(); }, 1000);
  },
  onHide() {
    if (this.fpsTimer) { clearInterval(this.fpsTimer); this.fpsTimer = null; }
  },

  onTelemetry(d) {
    this.els.heading.textContent = "HDG " + num(d.heading, 0) + "°";
    this.els.depth.textContent = "D " + num(d.depth, 2) + "m";
    this.els.rp.textContent = `R ${num(d.roll, 0)}° P ${num(d.pitch, 0)}°`;
  },

  _select(i) {
    this.active = i;
    this.els.sources.forEach((b, j) => b.classList.toggle("chip--active", j === i));
    const cam = (CONFIG.CAMERAS || [])[i] || { url: "" };
    this.els.url.value = cam.url || "";
    if (this.streaming) this._loadActive();
  },

  _loadActive() {
    const cam = (CONFIG.CAMERAS || [])[this.active] || { url: "" };
    if (!cam.url) { this.els.noSignal.style.display = "flex"; this.els.img.removeAttribute("src"); return; }
    this.els.img.src = cam.url;
  },

  _toggleStream() {
    this.streaming = !this.streaming;
    this.els.state.textContent = this.streaming ? "LIVE" : "IDLE";
    this.els.state.classList.toggle("badge--active", this.streaming);
    if (this.streaming) { this._loadActive(); log("Stream kamera dimulai", "ok"); }
    else { this.els.img.removeAttribute("src"); this.els.noSignal.style.display = "flex"; log("Stream kamera dihentikan", "warn"); }
    this._updateInfo();
  },

  _applyUrl() {
    const url = this.els.url.value.trim();
    (CONFIG.CAMERAS || [])[this.active] && (CONFIG.CAMERAS[this.active].url = url);
    if (this.active === 0) CONFIG.CAMERA_URL = url;
    log(`URL ${(CONFIG.CAMERAS[this.active] || {}).id || "CAM"} diset`, "ok");
    if (this.streaming) this._loadActive();
  },

  _toggleRecord(btn) {
    this.recording = !this.recording;
    btn.setAttribute("aria-pressed", String(this.recording));
    btn.textContent = this.recording ? "Record ●" : "Record";
    if (this.recording) {
      this.recorder = createRecorder(this.els.img);
      if (!this.recorder.start()) { this.recording = false; btn.setAttribute("aria-pressed", "false"); btn.textContent = "Record"; this.recorder = null; log("Tidak ada frame untuk merekam", "warn"); return; }
      this.els.rec.classList.add("active");
      log("Perekaman kamera dimulai", "ok");
    } else {
      if (this.recorder) { this.recorder.stop(); this.recorder = null; }
      this.els.rec.classList.remove("active");
      log("Perekaman kamera berhenti", "warn");
    }
    this._updateInfo();
  },

  _fullscreen() {
    if (document.fullscreenElement === this.els.viewport) document.exitFullscreen?.();
    else this.els.viewport.requestFullscreen?.().catch(() => log("Fullscreen ditolak browser", "warn"));
  },

  _updateInfo() {
    if (this.els.ci_stream) this.els.ci_stream.textContent = this.streaming ? `Live · ${this.fps} fps` : "Waiting";
    if (this.els.ci_rec) this.els.ci_rec.textContent = this.recording ? "Recording" : "Idle";
    if (this.els.ci_cmd) {
      const a = pilotAxes;
      this.els.ci_cmd.textContent = `Su${Math.round(a.surge)} Sw${Math.round(a.sway)} Y${Math.round(a.yaw)} V${Math.round(a.vert)}`;
    }
  },
};
