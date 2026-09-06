/* cam_visual_fps.mjs — ukur KAPAN GAMBAR KAMERA BENAR-BENAR BERGANTI di layar.
 *
 * Kenapa terpisah dari gui_page_latency.mjs: yang itu mengukur requestAnimationFrame,
 * dan rAF tetap 60 fps MESKI gambar kamera macet — jadi ia pernah melaporkan
 * "control 59,9 fps, mulus" padahal pilot mengeluh berat. Alat ini menggambar
 * <img> kamera ke kanvas 24x24 tiap rAF lalu menghitung checksum; stempel waktu
 * dicatat hanya saat piksel BERUBAH. Itulah kemulusan yang benar-benar dilihat mata.
 *
 * Acuan terukur 6 Sep 2026 (halaman Control, 720p):
 *   kamera 15 fps -> jeda antar-gambar p50 67 ms
 *   kamera 25 fps -> jeda antar-gambar p50 34 ms, visual 24,9 fps
 *
 * Halaman Camera akan menunjukkan 0 kecuali tombol "Start Stream" sudah ditekan.
 *
 *   node server/cam_visual_fps.mjs [--seconds 20]
 */
import { spawn } from "child_process";
import { mkdtempSync, existsSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import WebSocket from "ws";
const EDGE = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
              "C:/Program Files/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const SECS = Number(process.argv[process.argv.indexOf("--seconds") + 1] || 12);

const dir = mkdtempSync(join(tmpdir(), "camf-"));
const proc = spawn(EDGE, ["--remote-debugging-port=9344", `--user-data-dir=${dir}`,
  "--no-first-run", "--no-default-browser-check",
  /* Chromium menghentikan requestAnimationFrame saat jendelanya dianggap
     tertutup jendela lain -- tanpa flag ini hasil ukur jadi 0 fps palsu. */
  "--disable-features=CalculateNativeWinOcclusion",
  "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
  "--window-position=0,0", "--window-size=1600,950",
  "http://localhost:8080"], { stdio: "ignore" });
let t = null;
for (let i = 0; i < 40 && !t; i++) { await sleep(500);
  try { t = (await (await fetch("http://127.0.0.1:9344/json/list")).json()).find(x => x.type === "page" && x.webSocketDebuggerUrl); } catch {} }
const ws = new WebSocket(t.webSocketDebuggerUrl, { maxPayload: 64 << 20 });
await new Promise(r => ws.once("open", r));
let id = 0; const w = new Map();
ws.on("message", m => { const o = JSON.parse(m.toString()); if (o.id && w.has(o.id)) { w.get(o.id)(o); w.delete(o.id); } });
const send = (m, p = {}) => new Promise((res, rej) => { const n = ++id; w.set(n, o => o.error ? rej(new Error(JSON.stringify(o.error))) : res(o.result)); ws.send(JSON.stringify({ id: n, method: m, params: p })); });
const val = async e => (await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true })).result.value;
await send("Runtime.enable");
for (let i = 0; i < 40; i++) { if (await val("!!document.getElementById('vLat')")) break; await sleep(500); }

const PROBE = `
window.__cf = (() => {
  const c = document.createElement("canvas"); c.width = 24; c.height = 24;
  const x = c.getContext("2d", { willReadFrequently: true });
  let prev = null, stamps = [], rafs = [], lastRaf = performance.now();
  const loop = () => {
    const now = performance.now(); rafs.push(now - lastRaf); lastRaf = now;
    const img = document.getElementById("camImg");
    if (img && img.naturalWidth) {
      try {
        x.drawImage(img, 0, 0, 24, 24);
        const d = x.getImageData(0, 0, 24, 24).data;
        let h = 0; for (let i = 0; i < d.length; i += 16) h = (h * 31 + d[i]) | 0;
        if (prev !== null && h !== prev) stamps.push(now);
        prev = h;
      } catch (e) {}
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
  const st = a => { const s = [...a].sort((p, q) => p - q); const g = f => s.length ? s[Math.min(s.length - 1, Math.floor(f * s.length))] : 0;
    return { n: s.length, p50: g(.5), p95: g(.95), max: s[s.length - 1] || 0 }; };
  return {
    reset() { stamps = []; rafs = []; lastRaf = performance.now(); },
    stats() {
      const gaps = []; for (let i = 1; i < stamps.length; i++) gaps.push(stamps[i] - stamps[i - 1]);
      const dur = stamps.length > 1 ? (stamps[stamps.length - 1] - stamps[0]) / 1000 : 0;
      return { newFrames: stamps.length, visFps: dur ? (stamps.length - 1) / dur : 0,
               gap: st(gaps), raf: st(rafs) };
    },
  };
})(); "ok"`;

await val(PROBE);
for (const page of ["control", "camera"]) {
  await val(`window.dispatchEvent(new CustomEvent("hydroship:goto-page",{detail:"${page}"})); "ok"`);
  await sleep(2500);
  await val("window.__cf.reset(); 'ok'");
  await sleep(SECS * 1000);
  const s = JSON.parse(await val("JSON.stringify(window.__cf.stats())"));
  console.log(`${page.padEnd(8)} gambar-baru ${String(s.newFrames).padStart(4)} | VISUAL ${s.visFps.toFixed(1).padStart(5)} fps | ` +
    `jeda antar-gambar p50 ${s.gap.p50.toFixed(0)}ms p95 ${s.gap.p95.toFixed(0)}ms max ${s.gap.max.toFixed(0)}ms | ` +
    `rAF p95 ${s.raf.p95.toFixed(0)}ms max ${s.raf.max.toFixed(0)}ms`);
}
ws.close(); proc.kill();
