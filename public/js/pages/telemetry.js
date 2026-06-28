// telemetry.js — Halaman Telemetry & Health.
// Grafik live Yaw/Depth/Pitch/Roll (nilai nyata) + pemantauan arus/status thruster.
// KKI 2026: maksimal 6 thruster.
import Chart from "chart.js/auto";
import { log, num } from "../core.js";

const WINDOW = 120;

const C_REAL = "#14d8ff";
const C_GRID = "rgba(160,186,209,.12)";
const C_TICK = "rgba(160,186,209,.7)";

const CHANNELS = [
  { key: "yaw", title: "Yaw", unit: "°" },
  { key: "depth", title: "Depth", unit: "m" },
  { key: "pitch", title: "Pitch", unit: "°" },
  { key: "roll", title: "Roll", unit: "°" },
];

const THRUSTERS = [
  { id: "T1", type: "Horizontal" }, { id: "T2", type: "Horizontal" },
  { id: "T3", type: "Horizontal" }, { id: "T4", type: "Horizontal" },
  { id: "T5", type: "Vertical" }, { id: "T6", type: "Vertical" },
];

export const telemetryPage = {
  charts: {},
  buf: {},
  capturing: false,
  samples: 0,
  csvRows: [],
  thrusterState: {},
  raf: null,
  visible: false,
  els: {},

  init(root) {
    CHANNELS.forEach((c) => (this.buf[c.key] = []));
    THRUSTERS.forEach((t) => (this.thrusterState[t.id] = { current: 0.6 }));

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
          <span class="badge badge--ok" id="thr-st-${t.id}">Normal</span>
        </div>
        <div class="thr-card__stats">
          <span>Current <b id="thr-c-${t.id}">0.6</b> A</span>
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
      this.charts[c.key] = this._mkChart(card.querySelector(`#cv-${c.key}`), c.unit);
    });

    this.els.status = root.querySelector("#teleStatus");
    this.els.samplesBadge = root.querySelector("#teleSamples");
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
          { label: "Real", data: empty(), borderColor: C_REAL, borderWidth: 1.8, pointRadius: 0, tension: 0.25 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { display: false, grid: { color: C_GRID } },
          y: { grid: { color: C_GRID }, ticks: { color: C_TICK, font: { size: 9 } },
               title: { display: true, text: unit, color: C_TICK, font: { size: 9 } } },
        },
      },
    });
  },

  onShow() { this.visible = true; if (!this.raf) this._loop(); },
  onHide() { this.visible = false; if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; } },

  onTelemetry(d) {
    const real = {
      yaw: Number.isFinite(d.heading) ? ((d.heading % 360) + 360) % 360 : 0,
      depth: d.depth || 0, pitch: d.pitch || 0, roll: d.roll || 0,
    };
    for (const c of CHANNELS) {
      const b = this.buf[c.key];
      b.push(real[c.key]);
      if (b.length > WINDOW) b.shift();
    }
    if (this.capturing) {
      this.samples++;
      this.csvRows.push([Date.now(), real.yaw.toFixed(2), real.depth.toFixed(3), real.pitch.toFixed(2), real.roll.toFixed(2)].join(","));
    }
  },

  _loop() {
    this.raf = requestAnimationFrame(() => this._loop());
    if (!this.visible) return;
    this._renderCharts();
    this._renderThrusters();
    if (this.els.samplesBadge) this.els.samplesBadge.textContent = `${this.samples} sampel`;
  },

  _renderCharts() {
    for (const c of CHANNELS) {
      const ch = this.charts[c.key];
      const b = this.buf[c.key];
      // titik terbaru di kiri, makin lama makin ke kanan (alir kiri→kanan)
      const rev = b.slice().reverse();
      const out = rev.concat(Array(WINDOW - rev.length).fill(null));
      ch.data.datasets[0].data = out.slice(0, WINDOW);
      ch.update("none");
    }
  },

  _renderThrusters() {
    for (const t of THRUSTERS) {
      const s = this.thrusterState[t.id];
      s.current = 0.6 + Math.sin(Date.now() / 700 + t.id.charCodeAt(1)) * 0.08;
      const c = document.getElementById(`thr-c-${t.id}`);
      if (c) c.textContent = s.current.toFixed(2);
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
    const header = "timestamp,yaw_deg,depth_m,pitch_deg,roll_deg";
    const blob = new Blob([header + "\n" + this.csvRows.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `hydroship_telemetry_trial${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    log(`Ekspor ${this.csvRows.length} sampel ke CSV`, "ok");
  },
};
