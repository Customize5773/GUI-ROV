// telemetry.js — Halaman Telemetry & Health.
// Grafik live Yaw/Depth/Pitch/Roll (nilai nyata) + pemantauan arus/status thruster.
// KKI 2026: maksimal 6 thruster.
import { log, num } from "../core.js";
import { DEFAULT_WINDOW, makeLineChart, pushRing, renderSeries } from "../chart-line.js";

const WINDOW = DEFAULT_WINDOW;
const OVERCURRENT = 10;   // A — ambang status thruster "High"

const CHANNELS = [
  { key: "yaw", title: "Yaw", unit: "°" },
  { key: "depth", title: "Depth", unit: "m" },
  { key: "pitch", title: "Pitch", unit: "°" },
  { key: "roll", title: "Roll", unit: "°" },
  { key: "pidRollP", title: "Roll PID P", unit: "" },
  { key: "pidRollI", title: "Roll PID I", unit: "" },
  { key: "pidRollD", title: "Roll PID D", unit: "" },
  { key: "pidPitchP", title: "Pitch PID P", unit: "" },
  { key: "pidPitchI", title: "Pitch PID I", unit: "" },
  { key: "pidPitchD", title: "Pitch PID D", unit: "" },
];

// Orientasi mengikuti tabel resmi di CONTROL-MAPPING.md §5.1 (Frame & mixing):
// T1/T2 = surge + yaw (horizontal), T3/T4/T5 = heave (vertical),
// T6 = lateral/sway satu-satunya (horizontal) — jangan drift dari tabel itu.
const THRUSTERS = [
  { id: "T1", type: "Horizontal" }, { id: "T2", type: "Horizontal" },
  { id: "T3", type: "Vertical" }, { id: "T4", type: "Vertical" },
  { id: "T5", type: "Vertical" }, { id: "T6", type: "Horizontal" },
];

export const telemetryPage = {
  charts: {},
  buf: {},
  capturing: false,
  samples: 0,
  csvRows: [],
  thrusters: null,   // arus thruster nyata (A) dari telemetri; null = belum ada data
  raf: null,
  visible: false,
  els: {},

  init(root) {
    CHANNELS.forEach((c) => (this.buf[c.key] = []));

    root.innerHTML = `
      <div class="tele">
        <div class="tele__head">
          <div>
            <span class="panel__eyebrow">TELEMETRY</span>
            <h2 class="tele__title">Live Pose &amp; Thruster Monitor</h2>
          </div>
          <span class="badge tele__status" id="teleStatus">Idle</span>
        </div>

        <div class="tele__controls">
          <label class="field field--sm"><span>Trial</span>
            <input id="teleTrial" type="number" value="1" min="1" />
          </label>
          <div class="tele__btns">
            <button class="chip chip--go" id="teleStart">Start</button>
            <button class="chip" id="teleStop">Stop</button>
            <button class="chip" id="teleExcel">Excel</button>
            <button class="chip" id="teleClear">Clear</button>
          </div>
          <span class="badge" id="teleSamples">0 sampel</span>
        </div>

        <div class="thrusters" id="teleThrusters"></div>

        <div class="charts" id="teleCharts"></div>
      </div>`;

    const tWrap = root.querySelector("#teleThrusters");
    THRUSTERS.forEach((t) => {
      const el = document.createElement("div");
      el.className = "thr-card";
      el.innerHTML = `
        <div class="thr-card__head">
          <span class="thr-card__name">${t.id} <small>${t.type}</small></span>
          <span class="badge" id="thr-st-${t.id}">No data</span>
        </div>
        <div class="thr-card__stats">
          <span>Current <b id="thr-c-${t.id}">—</b> A</span>
        </div>`;
      tWrap.appendChild(el);
    });

    const cWrap = root.querySelector("#teleCharts");
    CHANNELS.forEach((c) => {
      const card = document.createElement("div");
      card.className = "chart-card";
      card.innerHTML = `
        <div class="chart-card__head"><span class="chart-card__title">${c.title} (${c.unit})</span></div>
        <div class="chart-card__body"><canvas id="cv-${c.key}"></canvas></div>`;
      cWrap.appendChild(card);
      this.charts[c.key] = makeLineChart(card.querySelector(`#cv-${c.key}`), { unit: c.unit, window: WINDOW });
    });

    this.els.status = root.querySelector("#teleStatus");
    this.els.samplesBadge = root.querySelector("#teleSamples");
    root.querySelector("#teleStart").onclick = () => this._start();
    root.querySelector("#teleStop").onclick = () => this._stop();
    root.querySelector("#teleExcel").onclick = () => this._exportCsv();
    root.querySelector("#teleClear").onclick = () => this._clear();
  },

  onShow() { this.visible = true; if (!this.raf) this._loop(); },
  onHide() { this.visible = false; if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; } },

  onTelemetry(d) {
    const real = {
      yaw: Number.isFinite(d.heading) ? ((d.heading % 360) + 360) % 360 : 0,
      depth: d.depth || 0, pitch: d.pitch || 0, roll: d.roll || 0,
      // null (operator belum menekan SET) sengaja BUKAN 0: 0 m adalah setpoint
      // permukaan yang sah, dan depth_error yang dihitung darinya akan
      // menyesatkan saat tuning.
      depthSetpoint: Number.isFinite(d.depth_target) ? d.depth_target : null,
      depthHold: d.depth_hold === true,
      mode: d.mode || "unknown",
      thrusterVerticalPwm: d.thruster_vertical_pwm || 0,
      pidP: d.pid_p_out || 0, pidI: d.pid_i_out || 0, pidD: d.pid_d_out || 0,
      pidRollP: d.pid_roll_p_out || 0, pidRollI: d.pid_roll_i_out || 0, pidRollD: d.pid_roll_d_out || 0,
      pidPitchP: d.pid_pitch_p_out || 0, pidPitchI: d.pid_pitch_i_out || 0, pidPitchD: d.pid_pitch_d_out || 0,
      // POSHOLD: setpoint heading + status overlay. Tanpa dua kolom ini,
      // menyetel HEADING_P (rov_heading.py) sesudah trial jadi tebak-tebakan.
      // null (belum di-seed) sengaja diekspor sebagai kolom kosong, bukan 0 —
      // 0° adalah heading yang sah.
      headingSetpoint: Number.isFinite(d.heading_target) ? d.heading_target : null,
      poshold: d.poshold === true,
    };
    for (const c of CHANNELS) pushRing(this.buf[c.key], real[c.key], WINDOW);
    // arus thruster nyata bila ROV mengirim (array A: [T1..T6]); jika tidak, biarkan null
    if (Array.isArray(d.thrusters)) this.thrusters = d.thrusters;
    if (this.capturing) {
      this.samples++;
      // Tanpa setpoint tidak ada error yang bermakna -> kolom kosong, bukan 0.
      const depthError = real.depthSetpoint === null ? null : real.depthSetpoint - real.depth;
      this.csvRows.push([
        Date.now(), real.yaw.toFixed(2), real.depth.toFixed(3), real.pitch.toFixed(2), real.roll.toFixed(2),
        real.depthSetpoint === null ? "" : real.depthSetpoint.toFixed(3),
        real.mode, real.thrusterVerticalPwm,
        real.pidP.toFixed(3), real.pidI.toFixed(3), real.pidD.toFixed(3),
        real.pidRollP.toFixed(3), real.pidRollI.toFixed(3), real.pidRollD.toFixed(3),
        real.pidPitchP.toFixed(3), real.pidPitchI.toFixed(3), real.pidPitchD.toFixed(3),
        depthError === null ? "" : depthError.toFixed(3),
        real.headingSetpoint === null ? "" : real.headingSetpoint.toFixed(2),
        real.poshold ? 1 : 0,
        real.depthHold ? 1 : 0,
      ].join(","));
    }
  },

  _loop() {
    this.raf = requestAnimationFrame(() => this._loop());
    if (!this.visible) return;
    // throttle ~15 fps: 4 chart × 60 fps memberatkan laptop venue tanpa manfaat visual
    const now = performance.now();
    if (this._lastRender && now - this._lastRender < 66) return;
    this._lastRender = now;
    this._renderCharts();
    this._renderThrusters();
    if (this.els.samplesBadge) this.els.samplesBadge.textContent = `${this.samples} sampel`;
  },

  _renderCharts() {
    // Titik terbaru di kiri, makin lama makin ke kanan — lihat chart-line.js.
    for (const c of CHANNELS) renderSeries(this.charts[c.key], this.buf[c.key], WINDOW);
  },

  _renderThrusters() {
    for (let i = 0; i < THRUSTERS.length; i++) {
      const t = THRUSTERS[i];
      const amp = this.thrusters && Number.isFinite(this.thrusters[i]) ? this.thrusters[i] : null;
      const c = document.getElementById(`thr-c-${t.id}`);
      if (c) c.textContent = amp === null ? "—" : amp.toFixed(2);
      const st = document.getElementById(`thr-st-${t.id}`);
      if (st) {
        const high = amp !== null && amp >= OVERCURRENT;
        st.textContent = amp === null ? "No data" : (high ? "High" : "Normal");
        st.className = "badge " + (amp === null ? "" : (high ? "badge--fault" : "badge--ok"));
      }
    }
  },

  _start() {
    this.capturing = true;
    this.els.status.textContent = "Recording";
    this.els.status.classList.add("badge--active");
    log("Telemetry capture mulai", "ok");
  },
  _stop() {
    this.capturing = false;
    this.els.status.textContent = "Idle";
    this.els.status.classList.remove("badge--active");
    log(`Telemetry capture berhenti — ${this.samples} sampel`, "warn");
  },
  _clear() {
    this.capturing = false; this.samples = 0; this.csvRows = [];
    this.els.status.textContent = "Idle";
    this.els.status.classList.remove("badge--active");
    CHANNELS.forEach((c) => { this.buf[c.key] = []; });
    this._renderCharts();
    log("Telemetry dibersihkan", "");
  },
  _exportCsv() {
    if (!this.csvRows.length) { log("Tidak ada sampel untuk diekspor", "warn"); return; }
    const header = "timestamp,yaw_deg,depth_m,pitch_deg,roll_deg,depth_setpoint,mode,thruster_vertical_pwm,pid_p_out,pid_i_out,pid_d_out,pid_roll_p_out,pid_roll_i_out,pid_roll_d_out,pid_pitch_p_out,pid_pitch_i_out,pid_pitch_d_out,depth_error,heading_setpoint,poshold,depth_hold";
    const blob = new Blob([header + "\n" + this.csvRows.join("\n")], { type: "text/csv" });
    const trial = parseInt(document.getElementById("teleTrial")?.value, 10) || 1;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `hydroship_telemetry_trial${trial}_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    log(`Ekspor ${this.csvRows.length} sampel (trial ${trial}) ke CSV`, "ok");
  },
};
