// telemetry.js — Halaman Telemetry & Health.
// Grafik live Yaw/Depth/Pitch/Roll (Real / DT / Error) dari feed telemetri yang
// sama dengan halaman Control, plus metrik kesehatan dan grid kesehatan thruster.
import Chart from "chart.js/auto";
import { log, num } from "../core.js";

const WINDOW = 120;          // jumlah titik pada grafik bergulir
const EMA_ALPHA = 0.15;      // faktor smoothing untuk estimasi "Digital Twin"

// warna selaras tema
const C_REAL = "#14d8ff";
const C_DT = "#7bb8ff";
const C_ERR = "#ff5a5a";
const C_GRID = "rgba(160,186,209,.12)";
const C_TICK = "rgba(160,186,209,.7)";

/* selisih sudut ke rentang [-180,180] */
function angDiff(a, b) {
  let d = (a - b) % 360;
  if (d > 180) d -= 360;
  if (d < -180) d += 360;
  return d;
}

const CHANNELS = [
  { key: "yaw", title: "Yaw Tracking", unit: "°", angular: true },
  { key: "depth", title: "Depth Tracking", unit: "m", angular: false },
  { key: "pitch", title: "Pitch Tracking", unit: "°", angular: false },
  { key: "roll", title: "Roll Tracking", unit: "°", angular: false },
];

const METRICS = [
  { id: "hiRms", label: "HI_RMS" },
  { id: "thr", label: "Threshold" },
  { id: "resYaw", label: "Residual Yaw", unit: "deg" },
  { id: "resDepth", label: "Residual Depth", unit: "cm" },
  { id: "resRoll", label: "Residual Roll", unit: "deg" },
  { id: "resPitch", label: "Residual Pitch", unit: "deg" },
  { id: "fault", label: "Fault" },
  { id: "samples", label: "Samples" },
];

const THRUSTERS = [
  { id: "T1", type: "Horizontal" }, { id: "T2", type: "Horizontal" },
  { id: "T3", type: "Horizontal" }, { id: "T4", type: "Horizontal" },
  { id: "T5", type: "Vertical" }, { id: "T6", type: "Vertical" },
  { id: "T7", type: "Vertical" }, { id: "T8", type: "Vertical" },
];

export const telemetryPage = {
  charts: {},
  dt: { yaw: null, depth: null, pitch: null, roll: null },
  buf: {},                 // {key: {real:[], dt:[], err:[]}}
  errWindow: [],           // gabungan error ternormalisasi untuk HI_RMS
  capturing: false,
  samples: 0,
  csvRows: [],
  threshold: 0.15,
  efficiency: 1.0,
  faultThruster: "None",
  thrusterState: {},       // {id: {health, current, degr}}
  raf: null,
  visible: false,
  els: {},

  init(root) {
    CHANNELS.forEach((c) => (this.buf[c.key] = { real: [], dt: [], err: [] }));
    THRUSTERS.forEach((t) => (this.thrusterState[t.id] = { health: 100, current: 0.6, degr: 0 }));

    root.innerHTML = `
      <div class="tele">
        <div class="tele__head">
          <div>
            <span class="panel__eyebrow">LSTM HEALTH MODEL</span>
            <h2 class="tele__title">Pose Change Fault Detection</h2>
          </div>
          <span class="badge badge--active tele__conf" id="teleConf">96.2% Confidence</span>
        </div>

        <div class="metrics" id="teleMetrics"></div>

        <div class="tele__controls">
          <label class="field"><span>Scenario</span>
            <select id="teleScenario">
              <option>Hold static (no setpoint change)</option>
              <option>Step yaw +30°</option>
              <option>Depth change +1.0 m</option>
              <option>Lawnmower survey</option>
            </select>
          </label>
          <label class="field"><span>Fault thruster</span>
            <select id="teleFault">
              <option>None</option>
              <option>T1</option><option>T2</option><option>T3</option><option>T4</option>
              <option>T5</option><option>T6</option><option>T7</option><option>T8</option>
            </select>
          </label>
          <label class="field"><span>Efficiency</span>
            <select id="teleEff">
              <option>1.00</option><option>0.75</option><option>0.50</option><option>0.25</option>
            </select>
          </label>
          <label class="field field--sm"><span>Duration</span>
            <input id="teleDur" type="number" value="60" min="1" />
          </label>
          <label class="field field--sm"><span>Trial</span>
            <input id="teleTrial" type="number" value="1" min="1" />
          </label>
          <span class="badge tele__status" id="teleStatus">Normal</span>
          <div class="tele__btns">
            <button class="chip chip--go" id="teleStart">Start</button>
            <button class="chip" id="teleStop">Stop</button>
            <button class="chip" id="teleExcel">Excel</button>
            <button class="chip" id="teleClear">Clear</button>
          </div>
        </div>

        <div class="thrusters" id="teleThrusters"></div>

        <div class="charts" id="teleCharts"></div>
      </div>`;

    // metrik
    const mWrap = root.querySelector("#teleMetrics");
    METRICS.forEach((m) => {
      const el = document.createElement("div");
      el.className = "metric";
      el.innerHTML = `<span class="metric__k">${m.label}</span>
        <span class="metric__v" id="m-${m.id}">0.000</span>
        ${m.unit ? `<span class="metric__u">${m.unit}</span>` : ""}`;
      mWrap.appendChild(el);
      this.els[m.id] = el.querySelector(`#m-${m.id}`);
    });

    // thruster cards
    const tWrap = root.querySelector("#teleThrusters");
    THRUSTERS.forEach((t) => {
      const el = document.createElement("div");
      el.className = "thr-card";
      el.innerHTML = `
        <div class="thr-card__head">
          <span class="thr-card__name">${t.id} <small>${t.type}</small></span>
          <span class="badge badge--ok" id="thr-st-${t.id}">Normal</span>
        </div>
        <div class="bar"><div class="bar__fill" id="thr-bar-${t.id}" style="width:100%"></div></div>
        <div class="thr-card__stats">
          <span>Health <b id="thr-h-${t.id}">100%</b></span>
          <span>Current <b id="thr-c-${t.id}">0.6</b></span>
          <span>Degr <b id="thr-d-${t.id}">0.00</b></span>
        </div>`;
      tWrap.appendChild(el);
    });

    // charts
    const cWrap = root.querySelector("#teleCharts");
    CHANNELS.forEach((c) => {
      const card = document.createElement("div");
      card.className = "chart-card";
      card.innerHTML = `
        <div class="chart-card__head">
          <span class="chart-card__title">${c.title}</span>
          <span class="chart-card__legend">
            <i class="lg lg--dt"></i>DT <i class="lg lg--real"></i>Real <i class="lg lg--err"></i>Error
          </span>
        </div>
        <div class="chart-card__body"><canvas id="cv-${c.key}"></canvas></div>`;
      cWrap.appendChild(card);
      this.charts[c.key] = this._mkChart(card.querySelector(`#cv-${c.key}`), c.unit);
    });

    // kontrol
    this.els.status = root.querySelector("#teleStatus");
    this.els.scenario = root.querySelector("#teleScenario");
    root.querySelector("#teleFault").addEventListener("change", (e) => {
      this.faultThruster = e.target.value;
      log(`Fault thruster: ${this.faultThruster}`, this.faultThruster === "None" ? "" : "warn");
    });
    root.querySelector("#teleEff").addEventListener("change", (e) => {
      this.efficiency = parseFloat(e.target.value) || 1;
    });
    root.querySelector("#teleStart").onclick = () => this._start();
    root.querySelector("#teleStop").onclick = () => this._stop();
    root.querySelector("#teleExcel").onclick = () => this._exportCsv();
    root.querySelector("#teleClear").onclick = () => this._clear();
  },

  _mkChart(canvas, unit) {
    const empty = () => Array(WINDOW).fill(null);
    return new Chart(canvas, {
      type: "line",
      data: {
        labels: Array.from({ length: WINDOW }, (_, i) => i),
        datasets: [
          { label: "DT", data: empty(), borderColor: C_DT, borderDash: [5, 4], borderWidth: 1.4, pointRadius: 0, tension: 0.25 },
          { label: "Real", data: empty(), borderColor: C_REAL, borderWidth: 1.8, pointRadius: 0, tension: 0.25 },
          { label: "Error", data: empty(), borderColor: C_ERR, borderWidth: 1.2, pointRadius: 0, tension: 0.2 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { display: false, grid: { color: C_GRID } },
          y: {
            grid: { color: C_GRID }, ticks: { color: C_TICK, font: { size: 9 } },
            title: { display: true, text: unit, color: C_TICK, font: { size: 9 } },
          },
        },
      },
    });
  },

  onShow() {
    this.visible = true;
    if (!this.raf) this._loop();
  },
  onHide() {
    this.visible = false;
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  },

  onTelemetry(d) {
    // hitung Real per channel
    const real = {
      yaw: Number.isFinite(d.heading) ? ((d.heading % 360) + 360) % 360 : 0,
      depth: d.depth || 0,
      pitch: d.pitch || 0,
      roll: d.roll || 0,
    };
    // estimasi DT (EMA) + error
    const errNorm = [];
    const scales = { yaw: 180, depth: 5, pitch: 45, roll: 45 };
    for (const c of CHANNELS) {
      const k = c.key;
      let err;
      if (this.dt[k] === null) this.dt[k] = real[k];
      if (c.angular) {
        // smoothing sudut: gerak DT menuju Real lewat jalur terpendek
        this.dt[k] = ((this.dt[k] + EMA_ALPHA * angDiff(real[k], this.dt[k])) % 360 + 360) % 360;
        err = angDiff(real[k], this.dt[k]);
      } else {
        this.dt[k] = this.dt[k] * (1 - EMA_ALPHA) + real[k] * EMA_ALPHA;
        err = real[k] - this.dt[k];
      }
      const b = this.buf[k];
      b.real.push(real[k]); b.dt.push(this.dt[k]); b.err.push(err);
      if (b.real.length > WINDOW) { b.real.shift(); b.dt.shift(); b.err.shift(); }
      errNorm.push(err / scales[k]);
    }
    // HI_RMS atas jendela gabungan
    const hi = Math.sqrt(errNorm.reduce((s, e) => s + e * e, 0) / errNorm.length);
    this.errWindow.push(hi);
    if (this.errWindow.length > 40) this.errWindow.shift();

    if (this.capturing) {
      this.samples++;
      this.csvRows.push([
        Date.now(), real.yaw.toFixed(2), real.depth.toFixed(3),
        real.pitch.toFixed(2), real.roll.toFixed(2), hi.toFixed(4),
      ].join(","));
    }
    this._lastReal = real;
    this._lastHi = hi;
  },

  _loop() {
    this.raf = requestAnimationFrame(() => this._loop());
    if (!this.visible) return;
    this._renderCharts();
    this._renderMetrics();
    this._renderThrusters();
  },

  _renderCharts() {
    for (const c of CHANNELS) {
      const ch = this.charts[c.key];
      const b = this.buf[c.key];
      const pad = (arr) => {
        const out = Array(WINDOW - arr.length).fill(null).concat(arr);
        return out.slice(-WINDOW);
      };
      ch.data.datasets[0].data = pad(b.dt);
      ch.data.datasets[1].data = pad(b.real);
      ch.data.datasets[2].data = pad(b.err);
      ch.update("none");
    }
  },

  _renderMetrics() {
    const r = this._lastReal, hi = this._lastHi || 0;
    if (!r) return;
    const errOf = (k) => { const b = this.buf[k]; return b.err.length ? Math.abs(b.err[b.err.length - 1]) : 0; };
    this.els.hiRms.textContent = num(hi, 3);
    this.els.thr.textContent = num(this.threshold, 3);
    this.els.resYaw.textContent = num(errOf("yaw"), 2);
    this.els.resDepth.textContent = num(errOf("depth") * 100, 2);
    this.els.resRoll.textContent = num(errOf("roll"), 2);
    this.els.resPitch.textContent = num(errOf("pitch"), 2);
    this.els.samples.textContent = String(this.samples);
    const fault = hi > this.threshold;
    this.els.fault.textContent = fault ? "Detected" : "None";
    this.els.fault.classList.toggle("metric__v--alert", fault);
    this.els.status.textContent = fault ? "Fault" : "Normal";
    this.els.status.classList.toggle("badge--fault", fault);
  },

  _renderThrusters() {
    for (const t of THRUSTERS) {
      const s = this.thrusterState[t.id];
      const faulted = this.faultThruster === t.id;
      // drift halus
      const target = faulted ? 38 : 100 * this.efficiency;
      s.health += (target - s.health) * 0.02;
      s.current = (faulted ? 1.6 : 0.6) + Math.sin(Date.now() / 700 + t.id.charCodeAt(1)) * 0.08;
      s.degr = Math.max(0, (100 - s.health) / 100);
      const h = Math.round(s.health);
      const bar = document.getElementById(`thr-bar-${t.id}`);
      const st = document.getElementById(`thr-st-${t.id}`);
      document.getElementById(`thr-h-${t.id}`).textContent = `${h}%`;
      document.getElementById(`thr-c-${t.id}`).textContent = s.current.toFixed(2);
      document.getElementById(`thr-d-${t.id}`).textContent = s.degr.toFixed(2);
      if (bar) {
        bar.style.width = `${h}%`;
        bar.classList.toggle("bar__fill--warn", h < 70 && h >= 50);
        bar.classList.toggle("bar__fill--alert", h < 50);
      }
      if (st) {
        const label = h >= 70 ? "Normal" : h >= 50 ? "Warning" : "Fault";
        st.textContent = label;
        st.className = "badge " + (h >= 70 ? "badge--ok" : h >= 50 ? "badge--warn" : "badge--fault");
      }
    }
  },

  _start() {
    this.capturing = true;
    this.els.status.textContent = "Recording";
    log(`Telemetry capture mulai — ${this.els.scenario.value}`, "ok");
  },
  _stop() {
    this.capturing = false;
    log(`Telemetry capture berhenti — ${this.samples} sampel`, "warn");
  },
  _clear() {
    this.capturing = false;
    this.samples = 0;
    this.csvRows = [];
    this.errWindow = [];
    CHANNELS.forEach((c) => { this.buf[c.key] = { real: [], dt: [], err: [] }; this.dt[c.key] = null; });
    this._renderCharts();
    log("Telemetry dibersihkan", "");
  },
  _exportCsv() {
    if (!this.csvRows.length) { log("Tidak ada sampel untuk diekspor", "warn"); return; }
    const header = "timestamp,yaw_deg,depth_m,pitch_deg,roll_deg,hi_rms";
    const blob = new Blob([header + "\n" + this.csvRows.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `hydroship_telemetry_trial${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    log(`Ekspor ${this.csvRows.length} sampel ke CSV`, "ok");
  },
};
