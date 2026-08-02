// analyze.js — Halaman Analyze (adaptasi QGroundControl "MAVLink Inspector").
//
// Isi: SEMUA message MAVLink yang masuk dari FC — bukan hanya yang sudah
// dipetakan ke telemetri — beserta nilai field terakhirnya, plus grafik
// time-series untuk field numerik apa pun yang dipilih operator.
//
// Kenapa berguna: telemetri dashboard sengaja sempit (heading/depth/roll/
// pitch/volt). Saat ada yang ganjil di kolam — EKF tidak konvergen, baterai
// drop, pre-arm menolak — jawabannya ada di message yang tidak ditampilkan
// di mana pun. Ini jendela ke sana, tanpa mencabut umbilical untuk QGC.
//
// Yang SENGAJA tidak ada (lihat Planning/PLAN-QgroundControl.md §2.1):
//   - unduh log dataflash  -> tidak ada SD card di alur ini
//   - GeoTag / plot peta   -> ROV bawah air tanpa GPS
//   - ekspor CSV           -> sudah ada di halaman Telemetry
//
// Stream ini MAHAL (semua message, semua stream), jadi dinyalakan hanya selama
// halaman ini terbuka: onShow menyalakan + memperbarui tiap 10 detik, onHide
// mematikan. Wahana juga mematikannya sendiri kalau pembaruan berhenti
// (rov_mavlink.stream_still_wanted) supaya tab yang ditutup mendadak atau WS
// yang putus tidak meninggalkan firehose.

import { log, sendCmd } from "../core.js";
import { DEFAULT_WINDOW, makeLineChart, pushRing, renderSeries } from "../chart-line.js";

/* Harus lebih rapat dari STREAM_KEEPALIVE_TIMEOUT (30 s) di rov_mavlink.py,
   dengan ruang untuk beberapa kali gagal. */
const KEEPALIVE_MS = 10_000;

/* Grafik: 120 sampel @ ~10 Hz per type = ~12 detik. Cukup untuk melihat
   osilasi/drift tanpa menyimpan riwayat panjang — ini diagnostik live, bukan
   playback (untuk playback ada fitur Replay). */
const WINDOW = DEFAULT_WINDOW;

/* Lebih dari ini dan laptop venue mulai tersendat; juga sudah cukup untuk
   membandingkan beberapa besaran sekaligus. */
const MAX_PLOTS = 4;

const RENDER_INTERVAL_MS = 100;   // ~10 fps, sama semangatnya dengan telemetry.js

function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return String(v);
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4);
  if (Array.isArray(v)) return `[${v.length}]`;
  return String(v);
}

export const analyzePage = {
  /* type -> {fields, t, count, hz, lastHzT, lastHzCount} */
  msgs: new Map(),
  expanded: new Set(),
  /* "TYPE.field" -> {chart, buf, canvasId} */
  plots: new Map(),
  paused: false,
  visible: false,
  streaming: false,
  els: {},
  _keepalive: null,
  _renderTimer: null,
  _treeDirty: true,

  init(root) {
    root.innerHTML = `
      <div class="anz">
        <div class="anz__head">
          <div>
            <span class="panel__eyebrow">ANALYZE</span>
            <h2 class="anz__title">MAVLink Inspector</h2>
          </div>
          <div class="anz__headbtns">
            <span class="badge" id="anzStatus">Idle</span>
            <button class="chip" id="anzPause">Pause</button>
            <button class="chip" id="anzClear">Clear</button>
          </div>
        </div>

        <p class="card__desc anz__note">
          Semua message MAVLink dari FC, ter-throttle 10&nbsp;Hz per jenis. Klik satu
          jenis untuk melihat field-nya; klik field numerik untuk menambahkannya ke
          grafik (maks ${MAX_PLOTS}). Untuk merekam ke CSV pakai halaman <b>Telemetry</b>.
        </p>

        <div class="anz__body">
          <div class="anz__tree" id="anzTree"></div>
          <div class="anz__plots" id="anzPlots"></div>
        </div>
      </div>`;

    this.els.tree = root.querySelector("#anzTree");
    this.els.plots = root.querySelector("#anzPlots");
    this.els.status = root.querySelector("#anzStatus");
    this.els.pause = root.querySelector("#anzPause");

    this.els.pause.onclick = () => this._togglePause();
    root.querySelector("#anzClear").onclick = () => this._clear();

    this._renderTree();
    this._renderPlotsShell();
  },

  onShow() {
    this.visible = true;
    this._startStream();
    if (!this._renderTimer) this._loop();
  },

  onHide() {
    this.visible = false;
    this._stopStream();
    if (this._renderTimer) { clearTimeout(this._renderTimer); this._renderTimer = null; }
  },

  /* ---------- kendali stream ---------- */

  _startStream() {
    sendCmd("mavlink_stream", true);
    this.streaming = true;
    // Halaman ini sempat ditinggalkan -> stream mati -> acuan Hz sudah basi.
    this._rebaseHz();
    clearInterval(this._keepalive);
    // Keepalive, bukan sekali kirim: kalau WS sempat putus & tersambung lagi,
    // permintaan pertama hilang dan halaman ini akan diam selamanya.
    this._keepalive = setInterval(() => {
      if (this.visible) sendCmd("mavlink_stream", true);
    }, KEEPALIVE_MS);
    this._setStatus("Streaming", "badge--active");
    log("MAVLink Inspector aktif", "");
  },

  _stopStream() {
    clearInterval(this._keepalive);
    this._keepalive = null;
    if (this.streaming) {
      sendCmd("mavlink_stream", false);
      this.streaming = false;
      log("MAVLink Inspector dimatikan", "");
    }
    this._setStatus("Idle");
  },

  /* ---------- data masuk ---------- */

  onMavlinkMsg(m) {
    if (this.paused) return;
    if (!m || typeof m.msg !== "string") return;

    const now = performance.now();
    let entry = this.msgs.get(m.msg);
    if (!entry) {
      entry = { fields: {}, t: now, count: 0, hz: 0, lastHzT: now, lastHzCount: 0 };
      this.msgs.set(m.msg, entry);
      this._treeDirty = true;   // jenis baru muncul -> struktur berubah
    }

    entry.fields = m.fields || {};
    entry.t = now;
    entry.count++;

    // Hz dihitung dari jumlah pesan per jendela ~1 detik, bukan dari selisih
    // dua pesan terakhir — jitter jaringan membuat cara kedua melompat-lompat.
    if (now - entry.lastHzT >= 1000) {
      entry.hz = ((entry.count - entry.lastHzCount) * 1000) / (now - entry.lastHzT);
      entry.lastHzT = now;
      entry.lastHzCount = entry.count;
    }

    for (const [key, plot] of this.plots) {
      const [type, field] = key.split(".");
      if (type !== m.msg) continue;
      const v = entry.fields[field];
      if (typeof v === "number" && Number.isFinite(v)) pushRing(plot.buf, v, WINDOW);
    }
  },

  /* ---------- render ---------- */

  _setStatus(text, cls = "") {
    if (!this.els.status) return;
    this.els.status.textContent = text;
    this.els.status.className = "badge " + cls;
  },

  _loop() {
    this._renderTimer = setTimeout(() => this._loop(), RENDER_INTERVAL_MS);
    if (!this.visible) return;
    this._renderTree();
    for (const plot of this.plots.values()) renderSeries(plot.chart, plot.buf, WINDOW);
  },

  _renderTree() {
    if (!this.els.tree) return;

    if (!this.msgs.size) {
      this.els.tree.innerHTML = `<p class="anz__empty">${
        this.streaming ? "Menunggu message MAVLink dari FC…"
                       : "Stream mati. Buka kembali halaman ini untuk menyalakan."
      }</p>`;
      return;
    }

    const types = [...this.msgs.keys()].sort();

    // Struktur (daftar jenis + field yang terbuka) hanya digambar ulang saat
    // berubah; sisanya cukup menimpa TEKS nilai. Membangun ulang innerHTML
    // 10x/detik membuat teks tidak bisa diseleksi dan boros.
    if (this._treeDirty) {
      this.els.tree.innerHTML = types.map((type) => {
        const open = this.expanded.has(type);
        const entry = this.msgs.get(type);
        const rows = open ? Object.keys(entry.fields).sort().map((f) => {
          const v = entry.fields[f];
          const plottable = typeof v === "number" && Number.isFinite(v);
          const key = `${type}.${f}`;
          const on = this.plots.has(key);
          return `<div class="anz__field">
            <span class="anz__fname">${f}</span>
            <span class="anz__fval" data-val="${key}">${fmt(v)}</span>
            ${plottable
              ? `<button class="anz__plotbtn${on ? " is-on" : ""}" data-plot="${key}">${on ? "− plot" : "+ plot"}</button>`
              : `<span class="anz__plotbtn anz__plotbtn--na">—</span>`}
          </div>`;
        }).join("") : "";

        return `<div class="anz__msg${open ? " is-open" : ""}">
          <button class="anz__msghead" data-type="${type}">
            <span class="anz__mname">${type}</span>
            <span class="anz__mhz" data-hz="${type}">${entry.hz.toFixed(1)} Hz</span>
          </button>
          <div class="anz__fields">${rows}</div>
        </div>`;
      }).join("");

      for (const b of this.els.tree.querySelectorAll("[data-type]")) {
        b.onclick = () => {
          const t = b.getAttribute("data-type");
          if (this.expanded.has(t)) this.expanded.delete(t); else this.expanded.add(t);
          this._treeDirty = true;
          this._renderTree();
        };
      }
      for (const b of this.els.tree.querySelectorAll("[data-plot]")) {
        b.onclick = () => this._togglePlot(b.getAttribute("data-plot"));
      }

      this._treeDirty = false;
      return;
    }

    // Jalur cepat: hanya perbarui angka.
    for (const type of types) {
      const entry = this.msgs.get(type);
      const hz = this.els.tree.querySelector(`[data-hz="${type}"]`);
      if (hz) hz.textContent = `${entry.hz.toFixed(1)} Hz`;
      if (!this.expanded.has(type)) continue;
      for (const [f, v] of Object.entries(entry.fields)) {
        const cell = this.els.tree.querySelector(`[data-val="${type}.${f}"]`);
        if (cell) cell.textContent = fmt(v);
      }
    }
  },

  _renderPlotsShell() {
    if (!this.els.plots) return;
    if (!this.plots.size) {
      this.els.plots.innerHTML = `<p class="anz__empty">Belum ada field yang diplot. Buka satu jenis message di kiri lalu tekan “+ plot”.</p>`;
    }
  },

  /* ---------- plot ---------- */

  _togglePlot(key) {
    if (this.plots.has(key)) { this._removePlot(key); return; }

    if (this.plots.size >= MAX_PLOTS) {
      log(`Maksimal ${MAX_PLOTS} grafik — lepas salah satu dulu`, "warn");
      return;
    }

    const [type, field] = key.split(".");
    if (!this.msgs.has(type)) return;

    if (!this.plots.size) this.els.plots.innerHTML = "";

    const canvasId = `anz-cv-${key.replace(/[^A-Za-z0-9]/g, "-")}`;
    const card = document.createElement("div");
    card.className = "chart-card";
    card.dataset.plot = key;
    card.innerHTML = `
      <div class="chart-card__head">
        <span class="chart-card__title">${type}.${field}</span>
        <button class="chip chip--x" data-close="${key}">×</button>
      </div>
      <div class="chart-card__body"><canvas id="${canvasId}"></canvas></div>`;
    this.els.plots.appendChild(card);
    card.querySelector("[data-close]").onclick = () => this._removePlot(key);

    const chart = makeLineChart(card.querySelector(`#${canvasId}`), { unit: field, window: WINDOW });
    this.plots.set(key, { chart, buf: [], card });

    this._treeDirty = true;
    this._renderTree();
  },

  _removePlot(key) {
    const plot = this.plots.get(key);
    if (!plot) return;
    // destroy() wajib: Chart.js memasang listener resize per instance, dan
    // membuang <canvas>-nya saja meninggalkan instance itu hidup selamanya.
    try { plot.chart.destroy(); } catch (e) {}
    plot.card.remove();
    this.plots.delete(key);
    this._renderPlotsShell();
    this._treeDirty = true;
    this._renderTree();
  },

  /* ---------- tombol ---------- */

  /* Setel ulang acuan perhitungan Hz.
   *
   * Wajib dipanggil setiap kali aliran pesan sempat TERPUTUS sementara
   * (pause, atau halaman ditinggalkan lalu dibuka lagi). Tanpa ini, Hz
   * berikutnya dihitung dari selisih waktu yang mencakup seluruh jeda tadi —
   * stream 10 Hz yang di-pause 10 detik akan terbaca 0,1 Hz dan membuat
   * operator mengira link-nya sekarat. */
  _rebaseHz() {
    const now = performance.now();
    for (const entry of this.msgs.values()) {
      entry.lastHzT = now;
      entry.lastHzCount = entry.count;
    }
  },

  _togglePause() {
    this.paused = !this.paused;
    if (!this.paused) this._rebaseHz();
    this.els.pause.textContent = this.paused ? "Resume" : "Pause";
    // Stream TETAP jalan saat pause — yang dibekukan hanya tampilan, supaya
    // melepas pause langsung menunjukkan nilai terkini, bukan yang basi.
    this._setStatus(this.paused ? "Paused" : (this.streaming ? "Streaming" : "Idle"),
                    this.paused ? "badge--warn" : (this.streaming ? "badge--active" : ""));
  },

  _clear() {
    this.msgs.clear();
    this.expanded.clear();
    for (const key of [...this.plots.keys()]) this._removePlot(key);
    this._treeDirty = true;
    this._renderTree();
    this._renderPlotsShell();
    log("MAVLink Inspector dibersihkan", "");
  },
};
