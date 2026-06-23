// core.js — layanan bersama antara app.js dan modul-modul halaman.
// app.js menyuntik log/sendCmd lewat setServices(); modul halaman meng-import
// helper ini sehingga tidak ada ketergantungan melingkar dengan app.js.

/* nilai sumbu pilot terkini (Surge/Sway/Yaw/Vertical), dibaca halaman Mission
   untuk dead-reckoning posisi. Diperbarui oleh handler kontrol di app.js. */
export const pilotAxes = { surge: 0, sway: 0, yaw: 0, vert: 0 };

/* layanan yang disuntik app.js */
let _log = (m) => console.log("[log]", m);
let _sendCmd = (n, v) => console.log("[cmd]", n, v);
export function setServices({ log, sendCmd }) {
  if (log) _log = log;
  if (sendCmd) _sendCmd = sendCmd;
}
export function log(msg, level = "") { _log(msg, level); }
export function sendCmd(name, value) { _sendCmd(name, value); }

/* unduh frame <img> saat ini sebagai PNG. return false jika tak ada frame. */
export function snapshotImage(img, prefix = "hydroship_snapshot") {
  if (!img || !img.naturalWidth) return false;
  const c = document.createElement("canvas");
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  c.toBlob((b) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = `${prefix}_${Date.now()}.png`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, "image/png");
  return true;
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
    const stream = canvas.captureStream(25);
    chunks = [];
    try { mediaRecorder = new MediaRecorder(stream, { mimeType: "video/webm;codecs=vp8" }); }
    catch (e) { mediaRecorder = new MediaRecorder(stream); }
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
