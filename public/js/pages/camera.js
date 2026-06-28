import { CONFIG } from "../config.js";
import { pilotAxes, log, num, snapshotImage, createRecorder, makeFullscreen } from "../core.js";

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
          <div class="cam__nosignal" id="cpNoSignal">
          <span>ROV CAMERA NOT CONNECTED</span>
          </div>
          <div class="campage__bar">
            <button class="chip" id="cpSnap">Snapshot</button>
            <button class="chip" id="cpRecBtn" aria-pressed="false">Record</button>
            <button class="chip" id="cpHud" aria-pressed="true">HUD</button>
          </div>

          <!-- PiP kamera lain (tampil saat fullscreen) -->
          <div class="pip" id="camPip" aria-hidden="true">
            <div class="pip__resize" id="camPipResize" title="Tarik untuk ubah ukuran"></div>
            <img id="camPipImg" alt="Kamera lain" />
            <div class="pip__nosignal" id="camPipNo">NO CAM</div>
            <span class="pip__label" id="camPipLabel">CAM 2</span>
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
    this.els.pip = root.querySelector("#camPip");
    this.els.pipImg = root.querySelector("#camPipImg");
    this.els.pipNo = root.querySelector("#camPipNo");
    this.els.pipLabel = root.querySelector("#camPipLabel");
    this.els.pipImg.onerror = () => { this.els.pipImg.style.display = "none"; this.els.pipNo.style.display = "flex"; };
    this.els.pipImg.onload = () => { this.els.pipImg.style.display = ""; this.els.pipNo.style.display = "none"; };
    this._setupPipInteraction();

    this.els.img.onload = () => {
      this.els.noSignal.style.display = "none";
      this.els.res.textContent = `${this.els.img.naturalWidth}×${this.els.img.naturalHeight}`;
      this.fpsCount++;
    };
    this.els.img.onerror = () => { this.els.noSignal.style.display = "flex"; this.els.res.textContent = "—"; };

    root.querySelector("#camStart").onclick = () => this._toggleStream();
    root.querySelector("#camApply").onclick = () => this._applyUrl();
    const fullBtn = root.querySelector("#camFull");
    this._fs = makeFullscreen(this.els.viewport, {
      onToggle: (fs) => {
        fullBtn.textContent = fs ? "⛶ Exit Full Screen" : "⛶ Full Screen";
        fullBtn.setAttribute("aria-pressed", String(fs));
        this.fsOn = fs;
        this._updatePip();
      },
    });
    fullBtn.onclick = () => this._fs.toggle();
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
    this._updatePip();
  },

  /* PiP menampilkan kamera "lain" (dengan 2 kamera: lawan dari yang aktif) */
  _updatePip() {
    const cams = CONFIG.CAMERAS || [];
    if (cams.length < 2) { this.els.pipImg.removeAttribute("src"); return; }
    const other = (this.active + 1) % cams.length;
    this.els.pipLabel.textContent = cams[other].id;
    if (this.fsOn && cams[other].url) {
      this.els.pipImg.src = cams[other].url;
    } else {
      this.els.pipImg.removeAttribute("src");
      this.els.pipImg.style.display = "none";
      this.els.pipNo.style.display = this.fsOn ? "flex" : "none";
    }
  },

  _loadActive() {
    const cam = (CONFIG.CAMERAS || [])[this.active] || { url: "" };
    if (!cam.url) { this.els.noSignal.style.display = "flex"; this.els.img.removeAttribute("src"); return; }
    this.els.img.src = cam.url;
  },

  /* PiP interaktif (berguna saat fullscreen):
     - klik (tanpa geser)  -> tukar kamera utama dengan kamera PiP
     - tahan & geser        -> pindahkan posisi mini display (di-clamp ke viewport)
     - tarik sudut kiri-atas -> ubah ukuran (tinggi ikut rasio 16:9) */
  _setupPipInteraction() {
    const pip = this.els.pip, vp = this.els.viewport;
    if (!pip || !vp) return;
    let dragging = false, moved = false, sx = 0, sy = 0, ox = 0, oy = 0;
    pip.style.cursor = "grab";
    pip.style.touchAction = "none";

    pip.addEventListener("pointerdown", (e) => {
      dragging = true; moved = false;
      sx = e.clientX; sy = e.clientY;
      const r = pip.getBoundingClientRect(), vr = vp.getBoundingClientRect();
      ox = r.left - vr.left; oy = r.top - vr.top;
      pip.style.left = ox + "px"; pip.style.top = oy + "px";
      pip.style.right = "auto"; pip.style.bottom = "auto";
      pip.style.cursor = "grabbing";
      try { pip.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault();
    });
    pip.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
      const vr = vp.getBoundingClientRect();
      const nx = Math.max(0, Math.min(ox + dx, vr.width - pip.offsetWidth));
      const ny = Math.max(0, Math.min(oy + dy, vr.height - pip.offsetHeight));
      pip.style.left = nx + "px"; pip.style.top = ny + "px";
    });
    const end = (e) => {
      if (!dragging) return;
      dragging = false;
      pip.style.cursor = "grab";
      try { pip.releasePointerCapture(e.pointerId); } catch (_) {}
      if (!moved) {
        const cams = CONFIG.CAMERAS || [];
        if (cams.length >= 2) this._select((this.active + 1) % cams.length); // tukar kamera
      }
    };
    pip.addEventListener("pointerup", end);
    pip.addEventListener("pointercancel", end);

    // pegangan resize (kiri-atas)
    const handle = pip.querySelector("#camPipResize");
    if (handle) {
      let rz = false, rsx = 0, rw = 0;
      handle.addEventListener("pointerdown", (e) => {
        rz = true; rsx = e.clientX; rw = pip.offsetWidth;
        try { handle.setPointerCapture(e.pointerId); } catch (_) {}
        e.preventDefault(); e.stopPropagation();   // jangan picu drag/swap
      });
      handle.addEventListener("pointermove", (e) => {
        if (!rz) return;
        const vr = vp.getBoundingClientRect();
        const w = Math.max(140, Math.min(rw + (rsx - e.clientX), vr.width * 0.85));
        pip.style.width = w + "px";
        e.stopPropagation();
      });
      const rend = (e) => {
        if (!rz) return; rz = false;
        try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
        if (pip.style.left) {
          const vr = vp.getBoundingClientRect();
          pip.style.left = Math.max(0, Math.min(parseFloat(pip.style.left) || 0, vr.width - pip.offsetWidth)) + "px";
          pip.style.top = Math.max(0, Math.min(parseFloat(pip.style.top) || 0, vr.height - pip.offsetHeight)) + "px";
        }
      };
      handle.addEventListener("pointerup", rend);
      handle.addEventListener("pointercancel", rend);
    }
  },

  _toggleStream() {
    this.streaming = !this.streaming;
    this.els.state.textContent = this.streaming ? "LIVE" : "IDLE";
    this.els.state.classList.toggle("badge--active", this.streaming);
    if (this.streaming) { this._loadActive(); log("Stream kamera dimulai", "ok"); }
    else { this.els.img.removeAttribute("src"); this.els.noSignal.style.display = "flex"; log("Stream kamera dihentikan", "warn"); }
    this._updatePip();
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

  _updateInfo() {
    if (this.els.ci_stream) this.els.ci_stream.textContent = this.streaming ? `Live · ${this.fps} fps` : "Waiting";
    if (this.els.ci_rec) this.els.ci_rec.textContent = this.recording ? "Recording" : "Idle";
    if (this.els.ci_cmd) {
      const a = pilotAxes;
      this.els.ci_cmd.textContent = `Su${Math.round(a.surge)} Sw${Math.round(a.sway)} Y${Math.round(a.yaw)} V${Math.round(a.vert)}`;
    }
  },
};
