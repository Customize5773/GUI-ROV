// chart-line.js — satu tempat untuk grafik garis live di dashboard.
//
// Diekstrak dari pages/telemetry.js (dulu `_mkChart` + logika ring buffer yang
// inline di `_renderCharts`) supaya halaman Analyze memakai grafik yang SAMA,
// bukan menyalinnya. Nol dependency baru: Chart.js sudah ada di importmap
// public/index.html.
//
// Konvensi arah waktu: titik TERBARU di kiri, makin lama makin ke kanan.
// Ini sengaja dipertahankan persis seperti halaman Telemetry — dua halaman
// yang menggambar sumbu waktu ke arah berbeda akan salah dibaca operator saat
// membandingkan grafik berdampingan.

import Chart from "chart.js/auto";

export const CHART_COLORS = {
  line: "#14d8ff",
  grid: "rgba(160,186,209,.12)",
  tick: "rgba(160,186,209,.7)",
};

// Jumlah sampel yang ditahan/ditampilkan per grafik.
export const DEFAULT_WINDOW = 120;

/** Buat satu grafik garis live pada <canvas>. */
export function makeLineChart(canvas, { unit = "", window = DEFAULT_WINDOW, color = CHART_COLORS.line } = {}) {
  return new Chart(canvas, {
    type: "line",
    data: {
      labels: Array.from({ length: window }, (_, i) => i),
      datasets: [
        {
          label: "Real",
          data: Array(window).fill(null),
          borderColor: color,
          borderWidth: 1.8,
          pointRadius: 0,
          tension: 0.25,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false, grid: { color: CHART_COLORS.grid } },
        y: {
          grid: { color: CHART_COLORS.grid },
          ticks: { color: CHART_COLORS.tick, font: { size: 9 } },
          title: { display: true, text: unit, color: CHART_COLORS.tick, font: { size: 9 } },
        },
      },
    },
  });
}

/** Tambah satu sampel ke ring buffer, buang yang tertua bila penuh. */
export function pushRing(buf, value, window = DEFAULT_WINDOW) {
  buf.push(value);
  while (buf.length > window) buf.shift();
  return buf;
}

/** Ubah ring buffer jadi deret siap gambar (terbaru di kiri, sisanya kosong). */
export function toChartSeries(buf, window = DEFAULT_WINDOW) {
  const rev = buf.slice().reverse();
  return rev.concat(Array(Math.max(0, window - rev.length)).fill(null)).slice(0, window);
}

/** Gambar ulang satu grafik dari ring buffer-nya. */
export function renderSeries(chart, buf, window = DEFAULT_WINDOW) {
  chart.data.datasets[0].data = toChartSeries(buf, window);
  chart.update("none");
}
