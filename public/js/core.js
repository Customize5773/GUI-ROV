export const pilotAxes = { surge: 0, sway: 0, yaw: 0, heave: 0 };

/* layanan yang disuntik app.js */
let _log = (m) => console.log("[log]", m);
let _sendCmd = (n, v) => console.log("[cmd]", n, v);
let _send = (obj) => console.log("[ws]", obj);
export function setServices({ log, sendCmd, send }) {
  if (log) _log = log;
  if (sendCmd) _sendCmd = sendCmd;
  if (send) _send = send;
}
export function log(msg, level = "") { _log(msg, level); }

export function sendCmd(name, value) { _sendCmd(name, value); }
/* kirim pesan WebSocket mentah (mis. {type:"record_start"}). Berbeda dari sendCmd:
   TIDAK dibungkus type:"cmd", jadi tidak pernah diteruskan ke UDP/ROV. */
export function wsSend(obj) { _send(obj); }

/* unduh frame <img> saat ini sebagai PNG. return false jika tak ada frame. */
export function snapshotImage(img, prefix = "hydroship_snapshot") {
  if (!img || !img.naturalWidth) return false;
  try {
    const c = document.createElement("canvas");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    // toBlob melempar SecurityError bila canvas ter-taint (stream tanpa CORS)
    c.toBlob((b) => {
      if (!b) { _log("Snapshot gagal — feed lintas-asal tanpa CORS", "warn"); return; }
      const a = document.createElement("a");
      a.href = URL.createObjectURL(b);
      a.download = `${prefix}_${Date.now()}.png`;
      a.click();
      URL.revokeObjectURL(a.href);
    }, "image/png");
    return true;
  } catch (e) {
    _log("Snapshot gagal — feed lintas-asal tanpa CORS (aktifkan Access-Control-Allow-Origin)", "warn");
    return false;
  }
}

/* perekam: salin frame <img> ke canvas lalu rekam ke WebM via MediaRecorder. */
export function createRecorder(img, prefix = "hydroship_record") {
  let mediaRecorder = null, chunks = [], canvas = null, ctx = null, raf = null;

  function start() {
    if (!img || !img.naturalWidth) return false;
    canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    ctx = canvas.getContext("2d");
    (function loop() {
      if (img.naturalWidth) ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      raf = requestAnimationFrame(loop);
    })();
    let stream;
    try { stream = canvas.captureStream(25); }
    catch (e) {
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      canvas = null; ctx = null;
      _log("Rekam gagal — feed lintas-asal tanpa CORS", "warn");
      return false;
    }
    chunks = [];
    try { mediaRecorder = new MediaRecorder(stream, { mimeType: "video/webm;codecs=vp8" }); }
    catch (e) { try { mediaRecorder = new MediaRecorder(stream); } catch (e2) {
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      canvas = null; ctx = null;
      _log("Rekam gagal — MediaRecorder tidak tersedia", "warn");
      return false;
    } }
    mediaRecorder.ondataavailable = (ev) => { if (ev.data && ev.data.size) chunks.push(ev.data); };
    mediaRecorder.onstop = () => {
      const blob = new Blob(chunks, { type: "video/webm" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${prefix}_${Date.now()}.webm`;
      a.click();
      URL.revokeObjectURL(url);
      chunks = [];
    };
    mediaRecorder.start();
    return true;
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
    canvas = null;
    ctx = null;
  }

  return { start, stop };
}

/* util kecil untuk format angka aman */
export function num(v, d = 1) {
  return (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toFixed(d);
}

/* URL feed kamera lewat proxy same-origin server.js (/cam?url=...), supaya video
   tampil tanpa CORS di server kamera dan canvas (QR/snapshot/record) tidak ter-taint.
   String kosong → kembalikan "" (pemanggil sebaiknya removeAttribute src). */
export function camProxy(url) {
  return url ? "/cam?url=" + encodeURIComponent(url) : "";
}

/* Fullscreen yang tahan banting:
   coba Fullscreen API (lintas-browser); jika tidak tersedia atau ditolak
   (mis. di dalam iframe/webview yang memblokirnya), jatuh ke "pseudo-fullscreen"
   berbasis CSS (.pseudo-fs) sehingga tombol selalu berfungsi.
   onToggle(isFull) dipanggil setiap kali status berubah. */
export function makeFullscreen(el, { onToggle } = {}) {
  const req = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
  const exitFn = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
  const fsEl = () => document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement;

  function pseudoOn() {
    el.classList.add("pseudo-fs");
    document.body.classList.add("pseudo-fs-lock");
    el._pseudoFs = true;
    if (onToggle) onToggle(true);
  }
  function pseudoOff() {
    el.classList.remove("pseudo-fs");
    document.body.classList.remove("pseudo-fs-lock");
    el._pseudoFs = false;
    if (onToggle) onToggle(false);
  }

  function isFull() { return fsEl() === el || !!el._pseudoFs; }

  function toggle() {
    if (isFull()) {
      if (el._pseudoFs) pseudoOff();
      else if (exitFn) exitFn.call(document);
      return;
    }
    if (req) {
      let p;
      try { p = req.call(el); } catch (e) { p = null; }
      if (p && typeof p.then === "function") {
        p.catch(() => pseudoOn());
      }
      // fallback: jika 150ms kemudian native tak aktif, paksa pseudo
      setTimeout(() => { if (!el._pseudoFs && fsEl() !== el) pseudoOn(); }, 150);
    } else {
      pseudoOn();
    }
  }

  // sinkronkan label saat keluar via Esc / tombol browser (mode native)
  const onChange = () => { if (!el._pseudoFs && onToggle) onToggle(fsEl() === el); };
  document.addEventListener("fullscreenchange", onChange);
  document.addEventListener("webkitfullscreenchange", onChange);
  // Esc menutup pseudo-fullscreen
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && el._pseudoFs) pseudoOff(); });

  return { toggle, isFull };
}

/* Sumber data QR bersama antara halaman Camera dan readout Control. Python
   (mission5.py, via applyMission5 di app.js) diprioritaskan selama masih
   segar (misi autonomous jalan); scan jsQR lokal tiap halaman jadi fallback
   saat FSM Python belum/tidak aktif. */
const PY_QR_FRESH_MS = 3000;
let _pyQrData = null, _pyQrWall = null, _pyQrAt = 0;
let _clientQrData = null;
let _lastStableQr = null, _lastStableSource = null;
let _lastDisplayedQr = null;

export function setPyQr(data, wall) {
  _pyQrData = data || null;
  _pyQrWall = wall || null;
  _pyQrAt = Date.now();
  if (data && data !== _lastStableQr) {
    _lastStableQr = data;
    _lastStableSource = "python";
  }
}

export function setClientQr(data) {
  _clientQrData = data || null;
  if (data && data !== _lastStableQr) {
    _lastStableQr = data;
    _lastStableSource = "client";
  }
}

export function clearQr() {
  _clientQrData = null;
  _pyQrData = null;
  _pyQrWall = null;
  _lastStableQr = null;
  _lastStableSource = null;
  _lastDisplayedQr = null;
}

/* pisahkan sisi A/B/C/D dari payload QR (JSON KKI 2026 mis.
   {"mission":5,"team":"HYDROSHIP","type":"payload","id":"A"}, atau huruf
   sisi terisolasi dalam string biasa). Regex tanpa lookbehind agar
   kompatibel Safari lama. */
export function deriveQrSide(raw) {
  let parsed = null;
  try { parsed = JSON.parse(raw); } catch (_) { parsed = null; }
  if (parsed && typeof parsed === "object" && typeof parsed.id === "string" &&
      /^[ABCD]$/.test(parsed.id.trim().toUpperCase())) {
    const side = parsed.id.trim().toUpperCase();
    const team = parsed.team ? ` · ${parsed.team}` : "";
    const miss = parsed.mission != null ? ` · M${parsed.mission}` : "";
    return { side, shown: `id=${side}${miss}${team}` };
  }
  const m = String(raw).toUpperCase().match(/(?:^|[^A-Z])([ABCD])(?![A-Z])/);
  return { side: m ? m[1] : null, shown: raw };
}

export function getQrState() {
  const pyFresh = !!(_pyQrData && Date.now() - _pyQrAt < PY_QR_FRESH_MS);
  let raw = pyFresh ? _pyQrData : _clientQrData;
  let source = pyFresh ? "python" : (raw ? "client" : null);
  if (!raw && _lastStableQr) {
    raw = _lastStableQr;
    source = _lastStableSource;
  }
  const derived = raw ? deriveQrSide(raw) : null;
  const side = (pyFresh && _pyQrWall) ? _pyQrWall : (derived ? derived.side : null);
  let changeType = null;
  if (raw) {
    changeType = raw !== _lastDisplayedQr ? "new" : "same";
    _lastDisplayedQr = raw;
  } else {
    _lastDisplayedQr = null;
  }
  return {
    raw,
    side,
    shown: derived ? derived.shown : null,
    source,
    changeType,
  };
}

/* Decoder QR lokal bersama. Pertahankan resolusi kamera agar QR 4×4 cm tidak
   jatuh menjadi 2–4 piksel per modul, lalu coba threshold lokal hanya bila
   decode mentah gagal. Ini tetap ringan di frame normal dan menangani glare/
   caustic yang terlihat pada rekaman kolam. */
function clientQrAdaptive(imageData, constant) {
  const w = imageData.width, h = imageData.height;
  const gray = new Uint8Array(w * h);
  const stride = w + 1;
  const integral = new Int32Array((w + 1) * (h + 1));
  const src = imageData.data;
  for (let y = 0; y < h; y++) {
    let row = 0;
    for (let x = 0; x < w; x++) {
      const si = (y * w + x) * 4;
      const value = Math.round(0.299 * src[si] + 0.587 * src[si + 1] + 0.114 * src[si + 2]);
      const gi = y * w + x;
      gray[gi] = value;
      row += value;
      integral[(y + 1) * stride + x + 1] = integral[y * stride + x + 1] + row;
    }
  }

  const out = new Uint8ClampedArray(src.length);
  const radius = 15;
  for (let y = 0; y < h; y++) {
    const y0 = Math.max(0, y - radius), y1 = Math.min(h - 1, y + radius);
    for (let x = 0; x < w; x++) {
      const x0 = Math.max(0, x - radius), x1 = Math.min(w - 1, x + radius);
      const area = (x1 - x0 + 1) * (y1 - y0 + 1);
      const sum = integral[(y1 + 1) * stride + x1 + 1]
        - integral[y0 * stride + x1 + 1]
        - integral[(y1 + 1) * stride + x0]
        + integral[y0 * stride + x0];
      const black = gray[y * w + x] < sum / area - constant;
      const value = black ? 0 : 255;
      const oi = (y * w + x) * 4;
      out[oi] = value; out[oi + 1] = value; out[oi + 2] = value; out[oi + 3] = 255;
    }
  }
  return out;
}

/* Jalur lama, di main thread. Dipertahankan HANYA sebagai fallback untuk browser
   tanpa OffscreenCanvas/createImageBitmap. Logikanya tidak diubah. */
function decodeClientQrSync(source, canvas, maxSide, wantSharpness) {
  const empty = { qr: null, sharpness: null };
  if (!source || !canvas || !window.jsQR) return empty;
  const sw = source.naturalWidth || source.width;
  const sh = source.naturalHeight || source.height;
  if (!sw || !sh) return empty;
  const scale = Math.min(1, maxSide / Math.max(sw, sh));
  const w = Math.max(1, Math.round(sw * scale));
  const h = Math.max(1, Math.round(sh * scale));
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  try {
    canvas.width = w; canvas.height = h;
    ctx.drawImage(source, 0, 0, w, h);
    const image = ctx.getImageData(0, 0, w, h);
    const sharpness = wantSharpness ? sharpnessScoreLocal(image, w, h) : null;
    const run = (data) => window.jsQR(data, w, h, { inversionAttempts: "attemptBoth" });
    let code = run(image.data);
    if (!code) {
      for (const constant of [3, 7]) {
        code = run(clientQrAdaptive(image, constant));
        if (code) break;
      }
    }
    return { qr: code || null, sharpness };
  } catch (_) {
    return empty;
  }
}

/* salinan sharpnessScore untuk jalur fallback saja (versi utama ada di qr-worker.js) */
function sharpnessScoreLocal(imgData, w, h) {
  const gray = new Float32Array(w * h);
  const d = imgData.data;
  for (let i = 0, p = 0; i < d.length; i += 4, p++) {
    gray[p] = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
  }
  let sum = 0, sumSq = 0, n = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const idx = y * w + x;
      const lap = 4 * gray[idx] - gray[idx - 1] - gray[idx + 1] - gray[idx - w] - gray[idx + w];
      sum += lap; sumSq += lap * lap; n++;
    }
  }
  const mean = sum / n;
  return sumSq / n - mean * mean;
}

/* Satu worker dipakai bersama halaman Control & Camera — hanya satu halaman yang
   terlihat pada satu waktu, jadi tidak ada kontensi nyata. */
const qrWorkerSupported =
  typeof Worker !== "undefined" &&
  typeof OffscreenCanvas !== "undefined" &&
  typeof createImageBitmap === "function";

let qrWorker = null;
let qrBusy = false;
let qrPending = null;    // newest-wins: hanya frame TERBARU yang menunggu
const qrQueue = [];      // pekerjaan noDrop (dipicu operator), dilayani lebih dulu
let qrSeq = 0;
const qrWaiting = new Map();   // id -> resolve

function getQrWorker() {
  if (qrWorker) return qrWorker;
  qrWorker = new Worker("js/qr-worker.js");
  qrWorker.onmessage = (e) => {
    const { id, qr, sharpness } = e.data;
    const resolve = qrWaiting.get(id);
    qrWaiting.delete(id);
    qrBusy = false;
    if (resolve) resolve({ qr, sharpness });
    const next = qrQueue.shift() || qrPending;
    if (next) {
      if (next === qrPending) qrPending = null;
      postQrJob(next);
    }
  };
  qrWorker.onerror = () => {
    /* worker mati (mis. jsqr.min.js gagal dimuat): jangan diamkan pemanggil.
       Lepas semua yang menunggu, matikan worker — pemanggil berikutnya jatuh
       ke jalur sync di main thread. */
    for (const resolve of qrWaiting.values()) resolve({ qr: null, sharpness: null });
    qrWaiting.clear();
    for (const job of qrQueue.splice(0)) { job.bitmap.close(); job.resolve({ qr: null, sharpness: null }); }
    if (qrPending) { qrPending.bitmap.close(); qrPending.resolve({ qr: null, sharpness: null }); qrPending = null; }
    qrBusy = false;
    qrWorker.terminate();
    qrWorker = null;
  };
  return qrWorker;
}

function postQrJob(job) {
  qrBusy = true;
  const id = ++qrSeq;
  qrWaiting.set(id, job.resolve);
  const w = getQrWorker();
  if (!w) { qrWaiting.delete(id); qrBusy = false; job.bitmap.close(); return job.resolve({ qr: null, sharpness: null }); }
  w.postMessage(
    { id, bitmap: job.bitmap, maxSide: job.maxSide, wantSharpness: job.wantSharpness },
    [job.bitmap],
  );
}

/* Satu decoder lokal untuk halaman Camera dan Control. Python tetap menjadi
   sumber utama saat FSM aktif; fungsi ini hanya fallback operator/manual.
   Kembalikan Promise<{qr, sharpness}> — seluruh kerja piksel ada di worker,
   main thread hanya membayar createImageBitmap yang sendirinya off-thread. */
export async function decodeClientQr(source, canvas, maxSide = 1280, opts = {}) {
  const wantSharpness = !!opts.sharpness;
  /* noDrop: pekerjaan yang dipicu operator (scan berkas) tidak boleh ikut dibuang
     oleh newest-wins — kalau dibuang, hasilnya tampil sebagai "Tidak ada QR" palsu. */
  const noDrop = !!opts.noDrop;
  if (!qrWorkerSupported) return decodeClientQrSync(source, canvas, maxSide, wantSharpness);
  if (!source) return { qr: null, sharpness: null };
  const sw = source.naturalWidth || source.width;
  const sh = source.naturalHeight || source.height;
  if (!sw || !sh) return { qr: null, sharpness: null };

  let bitmap;
  try {
    bitmap = await createImageBitmap(source);
  } catch (_) {
    return { qr: null, sharpness: null };   // frame belum siap / ter-taint
  }

  return new Promise((resolve) => {
    const job = { bitmap, maxSide, wantSharpness, resolve };
    if (qrBusy) {
      if (noDrop) { qrQueue.push(job); return; }
      /* Worker masih sibuk. Buang frame yang ANTRE (bukan yang baru) — hasil QR
         dari frame lama tidak berguna, dan antrean yang menua justru menambah
         latensi. Bitmap yang tergeser wajib di-close supaya tidak bocor. */
      if (qrPending) {
        qrPending.bitmap.close();
        qrPending.resolve({ qr: null, sharpness: null });
      }
      qrPending = job;
      return;
    }
    postQrJob(job);
  });
}
