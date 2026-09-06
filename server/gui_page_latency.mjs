/* gui_page_latency.mjs — ukur latensi & kemulusan GUI di SETIAP halaman.
 *
 * Menjawab: "kalau pilot pindah halaman, apakah latensi naik?"
 * Tiga metrik per halaman:
 *   vLat_ms   latensi kontrol WS yang DITAMPILKAN GUI (#vLat) — yang dirasa pilot
 *   fps/gap   interval requestAnimationFrame = kemulusan gambar di layar
 *   longtask  tugas main-thread >50 ms = penyebab tersendat/jank
 *
 * Pakai Edge/Chrome yang sudah ada + modul `ws` yang sudah terpasang; tidak
 * menambah dependensi. Dijalankan HEADED (berjendela) dengan sengaja: mode
 * headless memakai WebGL software sehingga scene 3D halaman Control terukur
 * jauh lebih lambat dari kenyataan.
 *
 *   node server/gui_page_latency.mjs [--seconds 8] [--url http://localhost:8080]
 */
import { spawn } from "child_process";
import { mkdtempSync, existsSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import WebSocket from "ws";

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > -1 ? process.argv[i + 1] : d; };
const SECONDS = Number(arg("--seconds", 8));
const URL_GUI = arg("--url", "http://localhost:8080");
const PORT = 9222;
const PAGES = ["control", "camera", "mission", "telemetry", "setup",
               "vehicle", "analyze", "joystick", "replay"];

const EDGE = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
].find((p) => existsSync(p));
if (!EDGE) { console.error("Edge/Chrome tidak ditemukan"); process.exit(1); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const COLLECTOR = `
window.__probe = (() => {
  let frames = [], long = [], last = performance.now();
  const tick = () => { const t = performance.now(); frames.push(t - last); last = t; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  try {
    new PerformanceObserver((l) => { for (const e of l.getEntries()) long.push(e.duration); })
      .observe({ entryTypes: ["longtask"] });
  } catch (e) {}
  return {
    reset() { frames = []; long = []; last = performance.now(); },
    stats() {
      const s = [...frames].sort((a, b) => a - b);
      const q = (f) => s.length ? s[Math.min(s.length - 1, Math.floor(f * s.length))] : null;
      const mean = frames.length ? frames.reduce((a, b) => a + b, 0) / frames.length : null;
      const el = document.getElementById("vLat");
      return {
        fps: mean ? 1000 / mean : null,
        gap_p50: q(0.5), gap_p95: q(0.95), gap_max: s.length ? s[s.length - 1] : null,
        longtasks: long.length,
        long_max: long.length ? Math.max.apply(null, long) : 0,
        long_total: long.reduce((a, b) => a + b, 0),
        vLat: el ? el.textContent.trim() : "?",
      };
    },
  };
})(); "ok"`;

async function cdp() {
  const dir = mkdtempSync(join(tmpdir(), "guiprobe-"));
  const proc = spawn(EDGE, [
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${dir}`,
    "--no-first-run", "--no-default-browser-check",
  /* Chromium menghentikan requestAnimationFrame saat jendelanya dianggap
     tertutup jendela lain -- tanpa flag ini hasil ukur jadi 0 fps palsu. */
  "--disable-features=CalculateNativeWinOcclusion",
  "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
  "--window-position=0,0", "--window-size=1600,950",
    URL_GUI,
  ], { detached: false, stdio: "ignore" });

  let target = null;
  for (let i = 0; i < 40 && !target; i++) {
    await sleep(500);
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      target = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
    } catch { /* browser belum siap */ }
  }
  if (!target) { proc.kill(); throw new Error("target CDP tidak muncul"); }
  return { proc, ws: new WebSocket(target.webSocketDebuggerUrl, { maxPayload: 64 << 20 }) };
}

function rpc(ws) {
  let id = 0; const waiting = new Map();
  ws.on("message", (m) => {
    const msg = JSON.parse(m.toString());
    if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); }
  });
  return (method, params = {}) => new Promise((res, rej) => {
    const n = ++id;
    waiting.set(n, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
    ws.send(JSON.stringify({ id: n, method, params }));
  });
}

const val = async (send, expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  return r.result && r.result.value;
};

(async () => {
  const { proc, ws } = await cdp();
  await new Promise((r) => ws.once("open", r));
  const send = rpc(ws);
  await send("Runtime.enable");

  process.stdout.write(`menunggu GUI siap di ${URL_GUI} ...\n`);
  for (let i = 0; i < 40; i++) {
    if (await val(send, "!!document.getElementById('vLat')")) break;
    await sleep(500);
  }
  await val(send, COLLECTOR);
  await sleep(2500);   // biarkan WS connect & telemetri mengalir

  const rows = [];
  for (const page of PAGES) {
    await val(send, `window.dispatchEvent(new CustomEvent("hydroship:goto-page",{detail:${JSON.stringify(page)}})); "ok"`);
    await sleep(1200);                       // transisi halaman, jangan ikut diukur
    await val(send, "window.__probe.reset(); 'ok'");
    await sleep(SECONDS * 1000);
    const s = await val(send, "JSON.stringify(window.__probe.stats())");
    rows.push({ page, ...JSON.parse(s) });
    const r = rows[rows.length - 1];
    console.log(
      `${page.padEnd(10)} vLat ${String(r.vLat).padStart(5)} ms | UI ${r.fps.toFixed(1).padStart(5)} fps ` +
      `(gap p50 ${r.gap_p50.toFixed(0)}ms p95 ${r.gap_p95.toFixed(0)}ms max ${r.gap_max.toFixed(0)}ms) | ` +
      `longtask ${String(r.longtasks).padStart(3)} (max ${r.long_max.toFixed(0)}ms, total ${r.long_total.toFixed(0)}ms)`);
  }

  const lat = rows.map((r) => parseFloat(r.vLat)).filter(Number.isFinite);
  if (lat.length) {
    const lo = Math.min(...lat), hi = Math.max(...lat);
    console.log(`\nlatensi kontrol antar-halaman: ${lo.toFixed(1)}-${hi.toFixed(1)} ms (rentang ${(hi - lo).toFixed(1)} ms)`);
  }
  const worst = [...rows].sort((a, b) => b.long_total - a.long_total)[0];
  console.log(`halaman paling membebani main-thread: ${worst.page} (longtask total ${worst.long_total.toFixed(0)} ms dlm ${SECONDS}s)`);

  ws.close(); proc.kill();
})().catch((e) => { console.error("GAGAL:", e.message); process.exit(1); });
