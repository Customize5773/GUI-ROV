// server.js — Jembatan: Dashboard <--WebSocket--> Node.js <--UDP--> Raspi (ROV).
//
//   Dashboard  : ws://<host>:8080         (telemetry keluar, command masuk)
//   Raspi  ->  : UDP JSON ke port 14551    (telemetry dari ROV)
//   ->  Raspi  : UDP JSON ke RPI_ADDR:14550 (command ke ROV)
//
// Jalankan:
//   node server.js              koneksi nyata ke Raspi
//   node server.js --sim        tanpa Raspi, server membuat telemetri palsu
//
// NOTE : Konfigurasi via environment variable (opsional):
//   RPI_ADDR=192.168.2.2 WS_PORT=8080 UDP_IN=14551 UDP_OUT=14550 node server.js

const http = require("http");
const https = require("https");
const dgram = require("dgram");
const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");
const { WebSocketServer } = require("ws");
const recording = require("./recording");
const WS_PORT  = parseInt(process.env.WS_PORT  || "8080", 10);
const UDP_IN   = parseInt(process.env.UDP_IN   || "14551", 10); // telemetry dari ROV
const UDP_OUT  = parseInt(process.env.UDP_OUT  || "14550", 10); // command ke ROV
const RPI_ADDR = process.env.RPI_ADDR || "192.168.2.2";
// Dipakai /api/runs utk narik log trial JSONL dari Pi via rsync/ssh — lihat
// bagian "Ambil Log Trial" di connect_raspi.md utk kenapa ini perlu (log
// ditulis rov_mission5_bridge.py di FILESYSTEM PI, bukan di laptop).
const RPI_SSH_USER = process.env.RPI_SSH_USER || "hydroships";
const RPI_LOG_DIR  = process.env.RPI_LOG_DIR  || "rov-agent/logs";
const SIM = process.argv.includes("--sim");

const PUBLIC = path.join(__dirname, "..", "public");
const SHARED_ROOT = path.join(__dirname, "..", "shared");
const AUTONOMY = path.join(__dirname, "..", "autonomy");
const AUTONOMOUS_LOG_DIR = path.join(AUTONOMY, "logs");
fs.mkdirSync(AUTONOMOUS_LOG_DIR, { recursive: true });

let lastControlMode = null;
let autonomousLogTimer = null;
let autonomousLogSyncBusy = false;
let autonomousLogPath = null;

function newAutonomousLog() {
  const d = new Date();
  const stamp = [d.getFullYear(), String(d.getMonth() + 1).padStart(2, "0"),
    String(d.getDate()).padStart(2, "0")].join("") + "_" +
    [d.getHours(), d.getMinutes(), d.getSeconds()].map((v) => String(v).padStart(2, "0")).join("");
  autonomousLogPath = path.join(AUTONOMOUS_LOG_DIR, `autonomous_${stamp}.log`);
}

function latestAutonomousLog() {
  if (autonomousLogPath) return autonomousLogPath;
  try {
    const names = fs.readdirSync(AUTONOMOUS_LOG_DIR)
      .filter((name) => /^autonomous_\d{8}_\d{6}\.log$/.test(name)).sort();
    return names.length ? path.join(AUTONOMOUS_LOG_DIR, names[names.length - 1]) : null;
  } catch (_) { return null; }
}

function syncLatestAutonomousLog() {
  if (autonomousLogSyncBusy) return;
  autonomousLogSyncBusy = true;
  // Salin snapshot terbaru selama autonomous berjalan. RunLogger di Pi flush
  // setiap event, jadi file lokal bisa dipakai untuk monitoring sebelum trial
  // selesai; nama file dibuat sekali per sesi agar tanggal trial ikut tersimpan.
  execFile("rsync", [
    "-az",
    "-e", "ssh -o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=accept-new",
    `${RPI_SSH_USER}@${RPI_ADDR}:${RPI_LOG_DIR}/*.jsonl`,
    path.join(AUTONOMY, "logs"),
  ], { timeout: 5000 }, () => {
    fs.readdir(AUTONOMOUS_LOG_DIR, (err, names) => {
      const runs = err ? [] : names.filter((name) => /^run_.*\.jsonl$/.test(name)).sort();
      const latest = runs[runs.length - 1];
      if (!latest) {
        autonomousLogSyncBusy = false;
        return;
      }
      const destination = autonomousLogPath || path.join(AUTONOMOUS_LOG_DIR, `autonomous_${latest.slice(4, -5)}.log`);
      if (!autonomousLogPath) autonomousLogPath = destination;
      fs.copyFile(path.join(AUTONOMOUS_LOG_DIR, latest), destination, () => {
        autonomousLogSyncBusy = false;
      });
    });
  });
}

function trackAutonomousRun(data) {
  const mode = data && data.control_mode;
  if (mode === "autonomous" && lastControlMode !== "autonomous") {
    newAutonomousLog();
    syncLatestAutonomousLog();
    clearInterval(autonomousLogTimer);
    autonomousLogTimer = setInterval(syncLatestAutonomousLog, 2000);
  } else if (mode !== "autonomous" && lastControlMode === "autonomous") {
    clearInterval(autonomousLogTimer);
    autonomousLogTimer = null;
    syncLatestAutonomousLog();
  }
  if (typeof mode === "string") lastControlMode = mode;
}

const MOTION_AXES = new Set([
    "surge",
    "sway",
    "yaw",
    "heave"
]);

function clampAxis(name, value) {
    let v = Number(value);

    if (!Number.isFinite(v))
        return 0;

    switch (name) {

        // Keempat axis memakai konvensi GUI yang sama: -1000..1000, 0 = diam.
        // Konversi heave ke MANUAL_CONTROL.z ArduSub (0..1000, 500 = diam)
        // dilakukan di rov_agent.py (to_mavlink_z), bukan di sini.
        case "surge":
        case "sway":
        case "yaw":
        case "heave":
            return Math.max(-1000, Math.min(1000, Math.round(v)));

        default:
            return v;
    }
}

/* ======================= JOYSTICK CONFIG FILE ======================= */

/* Skema + I/O profil joystick sekarang tinggal di satu tempat masing-masing:
   shared/joystick-profile.js (skema, default, validasi, migrasi) dan
   server/joystick-config.js (lokasi file OS, tulis atomik, pemulihan korup).
   Sebelumnya default & tabel migrasi ditulis kembar di sini dan di
   public/js/joystick-state.js. */
const joyConfig = require("./joystick-config");

// Diisi oleh start() sebelum server mulai listen.
let joystickConfig = null;

/* ----------------------- HTTP static server ----------------------- */
const MIME = {
  ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
  ".json": "application/json", ".glb": "model/gltf-binary",
  ".fbx": "application/octet-stream", ".png": "image/png", ".svg": "image/svg+xml",
  ".jsonl": "application/x-ndjson",
};

// Host tujuan yang boleh di-proxy (umbilical LAN). Cegah open-proxy ke internet.
// Set CAM_ALLOW_ANY=1 untuk menonaktifkan pembatasan (mis. uji lab).
function isAllowedCamHost(host) {
  if (process.env.CAM_ALLOW_ANY === "1") return true;
  if (!host) return false;
  if (host === "localhost" || host.endsWith(".local")) return true;

  // IPv4 privat: 127/8, 10/8, 192.168/16, 172.16–31/12
  const m = host.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (!m) return false;
  const [a, b] = [Number(m[1]), Number(m[2])];
  return a === 127 || a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31);
}

// Satu koneksi upstream per URL kamera fisik, di-share ke semua klien yang
// memintanya (halaman Control, kedua cell Camera, dst) — mencegah N klien =
// N koneksi ke kamera (kamera murah/mjpg-streamer berat kalau harus
// re-encode per klien, itu penyebab lag saat Start Stream diklik).
const camStreams = new Map(); // key -> { clients: Set<res>, statusCode, headers, up }
// Client yang lambat baca (res.write() balik false) di-skip sampai 'drain',
// supaya buffer Node untuk dia tidak numpuk tak terbatas dan menyeret klien lain.
const pausedCamClients = new WeakSet();

function camStreamKey(target) {
  const t = new URL(target);
  t.searchParams.delete("_t"); // satu-satunya cache-bust param yang dipakai client
  return t.toString();
}

/* ----------------------- QR preview proxy ----------------------- */
const QR_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";
const QR_MAX_HTML = 4 * 1024 * 1024; // batas buang halaman HTML yang diparse
const QR_MAX_HOPS = 5;               // max redirect + hop ekstraksi gambar

function qrFetch(href, cb) {
  const u = new URL(href);
  const mod = u.protocol === "https:" ? https : http;
  const req = mod.get(u, {
    headers: {
      "User-Agent": QR_UA,
      "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
    },
    timeout: 8000,
  }, (res) => cb(null, res));
  req.on("error", cb);
  req.on("timeout", () => req.destroy(Object.assign(new Error("timeout"), { code: "ETIMEDOUT" })));
}

// Gambar "utama" sebuah halaman HTML: img http non-svg dengan area terbesar
// (ikon header/thumbnail kecil ber-width < 80 px dibuang), fallback og:image.
function extractQrPageImage(href, html) {
  const best = [];
  const re = /<img\b[^>]*>/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const tag = m[0];
    const srcM = tag.match(/\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
    if (!srcM) continue;
    const src = (srcM[1] || srcM[2] || srcM[3] || "").trim();
    if (!src) continue;
    if (/\.svg(\?|#|$)/i.test(src)) continue;
    let abs;
    try { abs = new URL(src.replace(/&amp;/g, "&"), href).href; }
    catch { continue; }
    if (!/^https?:\/\//i.test(abs)) continue;
    const w = Number((tag.match(/\bwidth\s*=\s*["']?(\d+)/i) || [0, "0"])[1]);
    const h = Number((tag.match(/\bheight\s*=\s*["']?(\d+)/i) || [0, "0"])[1]);
    if (w && w < 80) continue; // ikon/thumbnail kecil
    best.push({ url: abs, area: w * h });
  }
  if (best.length) return best.sort((a, b) => b.area - a.area)[0].url;

  const og = html.match(/<meta[^>]+property=["']og:image["'][^>]*>/i);
  const ogUrl = og && (og[0].match(/content\s*=\s*(?:"([^"]*)"|'([^']*)')/) || [])[1];
  if (ogUrl) {
    try { return new URL(ogUrl.replace(/&amp;/g, "&"), href).href; } catch { /* lanjut */ }
  }
  return null;
}

// Stream bytes gambar dari href (lewat redirect/HTML). hops = pelindung loop.
function qrStreamPreview(href, res, hops) {
  const bail = () => { if (!res.headersSent) res.writeHead(404); res.end(); };
  qrFetch(href, (err, upstream) => {
    if (err || !upstream) return bail();
    const status = upstream.statusCode || 0;
    const ct = String(upstream.headers["content-type"] || "").split(";")[0].trim().toLowerCase();

    if (status >= 300 && status < 400 && upstream.headers.location) {
      upstream.destroy();
      if (hops >= QR_MAX_HOPS) return bail();
      let next;
      try { next = new URL(upstream.headers.location, href).href; }
      catch { return bail(); }
      return qrStreamPreview(next, res, hops + 1);
    }

    if (status >= 400) { upstream.destroy(); return bail(); }

    if (ct.startsWith("image/")) {
      res.writeHead(200, {
        "Content-Type": upstream.headers["content-type"],
        "Cache-Control": "public, max-age=86400",
      });
      return upstream.pipe(res);
    }

    if (ct.startsWith("text/html")) {
      let size = 0;
      const chunks = [];
      upstream.on("data", (d) => { size += d.length; if (size <= QR_MAX_HTML) chunks.push(d); });
      upstream.on("end", () => {
        const img = extractQrPageImage(href, Buffer.concat(chunks).toString());
        if (img) return qrStreamPreview(img, res, hops + 1);
        bail();
      });
      return;
    }

    upstream.destroy();
    bail();
  });
}

const httpServer = http.createServer((req, res) => {
  let urlPath = decodeURIComponent(req.url.split("?")[0]);

  // Proxy stream kamera → dashboard mengambilnya SAME-ORIGIN, jadi tak perlu CORS
  // di server kamera dan getImageData (deteksi QR) tidak ter-taint.
  if (urlPath === "/cam") {
    const target = new URL(req.url, `http://localhost:${WS_PORT}`).searchParams.get("url");
    if (!target) { res.writeHead(400); return res.end("param 'url' wajib"); }

    let t;
    try { t = new URL(target); }
    catch { res.writeHead(400); return res.end("url tidak valid"); }

    if (t.protocol !== "http:" && t.protocol !== "https:") {
      res.writeHead(400);
      return res.end("protokol tidak didukung");
    }

    if (!isAllowedCamHost(t.hostname)) {
      res.writeHead(403);
      return res.end("host kamera tidak diizinkan");
    }

    const key = camStreamKey(target);
    let entry = camStreams.get(key);

    if (entry) {
      // Headers upstream sudah datang → langsung writeHead. Kalau belum
      // (masih connecting), biarkan callback upRes di bawah yang writeHead
      // untuk semua client di entry.clients sekaligus.
      if (entry.statusCode) res.writeHead(entry.statusCode, entry.headers);
      entry.clients.add(res);
    } else {
      entry = { clients: new Set(), statusCode: null, headers: null, up: null };
      camStreams.set(key, entry);

      const mod = t.protocol === "https:" ? https : http;
      entry.up = mod.get(target, (upRes) => {
        entry.statusCode = upRes.statusCode || 502;
        entry.headers = upRes.headers;
        for (const c of entry.clients) if (!c.writableEnded) c.writeHead(entry.statusCode, entry.headers);

        upRes.on("data", (chunk) => {
          for (const c of entry.clients) {
            if (c.writableEnded || pausedCamClients.has(c)) continue;
            if (!c.write(chunk)) {
              pausedCamClients.add(c);
              c.once("drain", () => pausedCamClients.delete(c));
            }
          }
        });
        upRes.on("end", () => {
          for (const c of entry.clients) if (!c.writableEnded) c.end();
          camStreams.delete(key);
        });
      });

      entry.up.on("error", (e) => {
        for (const c of entry.clients) {
          if (c.writableEnded) continue;
          if (!c.headersSent) c.writeHead(502);
          c.end("kamera upstream error: " + e.message);
        }
        camStreams.delete(key);
      });

      entry.clients.add(res);
    }

    req.on("close", () => {
      entry.clients.delete(res);
      if (entry.clients.size === 0 && camStreams.get(key) === entry) {
        entry.up.destroy();
        camStreams.delete(key);
      }
    });
    return;
  }

  // ================= QR PREVIEW PROXY =================
  // Payload QR berupa URL halaman web (mis. q.me-qr.com/xxx → halaman HTML yang
  // menampilkan gambar) tidak bisa di-fetch browser (CORS lintas-origin). Resolusi
  // "URL → gambar yang benar" dikerjakan di server: 1) ikuti redirect sampai habis,
  // bila langsung image → stream bytes-nya; 2) bila HTML → ekstrak gambar utama
  // halaman, lalu stream bytes gambar tsb; 3) gagal → 404 (client jatuh ke teks).
  // QR dari misi boleh menunjuk mana saja, jadi proxy ini sengaja tidak membatasi
  // host (berbeda dgn /cam yang memang hanya untuk kamera LAN).
  if (urlPath === "/qr/preview") {
    const target = new URL(req.url, `http://localhost:${WS_PORT}`).searchParams.get("url");
    if (!target) { res.writeHead(400); return res.end("param 'url' wajib"); }
    let t;
    try { t = new URL(target); }
    catch { res.writeHead(400); return res.end("url tidak valid"); }
    if (t.protocol !== "http:" && t.protocol !== "https:") {
      res.writeHead(400);
      return res.end("protokol tidak didukung");
    }
    return qrStreamPreview(t.href, res, 0);
  }

  // ================= PLAYBACK / REPLAY (HTTP) =================
  // Data replay bersifat historis & akses-acak (daftar sesi, log trajectory,
  // frame video per-timestamp) → cocok dengan pola HTTP static/endpoint yang
  // sudah ada (server ini memang meng-serve file & mem-proxy /cam via HTTP).
  // WebSocket dipakai untuk push live; replay TIDAK lewat WS agar tak bercampur.

  // Daftar sesi rekaman (id, tanggal, durasi, ukuran, kamera).
  if (urlPath === "/api/recordings") {
    const list = recording.listSessions();
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify(list));
  }

  // Ringkasan run misi autonomous (JSONL dari fsm/mission5.py --run-log).
  // Ringkasannya DIHITUNG oleh tools/analyze_run.py, bukan di-parse ulang di sini —
  // supaya angka di panel GUI persis sama dgn laporan CLI yang dibaca saat analisis
  // trial. Run jarang & filenya kecil, jadi spawn per-request cukup murah.
  if (urlPath === "/api/runs") {
    const runAnalyze = () => execFile("python3",
      [path.join(AUTONOMY, "tools", "analyze_run.py"),
       path.join(AUTONOMY, "logs", "*.jsonl"), "--json"],
      { maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => {
        res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
        if (err) return res.end("[]");   // belum ada run / python3 tak tersedia
        let list;
        try { list = JSON.parse(stdout); } catch { return res.end("[]"); }
        // analyze_run.py mengeluarkan object tunggal bila cuma ada 1 run.
        if (!Array.isArray(list)) list = [list];
        list.reverse();                  // terbaru dulu (nama file berurut waktu)
        res.end(JSON.stringify(list));
      });

    // Tarik dulu run_*.jsonl terbaru dari ~/rov-agent/logs/ di Pi — file itu
    // ditulis rov_mission5_bridge.py di DISK PI, bukan laptop, jadi tanpa
    // langkah ini panel selalu menampilkan trial basi/lama. Gagal-lunak:
    // Pi mati/kabel putus/SSH belum di-setup TIDAK BOLEH bikin /api/runs
    // hang atau error — timeout pendek, lanjut baca file lokal apa adanya.
    return execFile("rsync",
      ["-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=accept-new",
       `${RPI_SSH_USER}@${RPI_ADDR}:${RPI_LOG_DIR}/*.jsonl`,
       path.join(AUTONOMY, "logs")],
      { timeout: 5000 },
      () => runAnalyze());
  }

  if (urlPath === "/api/autonomous.log") {
    const current = latestAutonomousLog();
    return current ? fs.readFile(current, (err, data) => {
      if (err) { res.writeHead(404); return res.end("Belum ada log autonomous"); }
      res.writeHead(200, {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": `attachment; filename=${path.basename(current)}`,
        "Cache-Control": "no-store",
      });
      res.end(data);
    }) : (res.writeHead(404), res.end("Belum ada log autonomous"));
  }

  // Satu frame JPEG dari sesi: /replay/frame?session=<id>&cam=<bottom|wall>&i=<idx>
  if (urlPath === "/replay/frame") {
    const q = new URL(req.url, `http://localhost:${WS_PORT}`).searchParams;
    const id = q.get("session");
    const cam = (q.get("cam") || "").toLowerCase();
    const idx = parseInt(q.get("i") || "0", 10);
    recording.getFrame(id, cam, idx, (err, buf) => {
      if (err || !buf) { res.writeHead(404); return res.end("frame tidak ada"); }
      res.writeHead(200, { "Content-Type": "image/jpeg", "Cache-Control": "no-store" });
      res.end(buf);
    });
    return;
  }

  // File data sesi (meta.json / trajectory.jsonl / commands.jsonl / *.index.jsonl):
  //   /recordings/<id>/<file>
  if (urlPath.startsWith("/recordings/")) {
    const rest = urlPath.slice("/recordings/".length);
    const slash = rest.indexOf("/");
    const id = slash < 0 ? rest : rest.slice(0, slash);
    const name = slash < 0 ? "" : rest.slice(slash + 1);
    const filePath = recording.resolveSessionFile(id, name);
    if (!filePath) { res.writeHead(400); return res.end("permintaan tidak valid"); }
    return fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(404); return res.end("Not found"); }
      res.writeHead(200, {
        "Content-Type": MIME[path.extname(filePath)] || "application/octet-stream",
        "Cache-Control": "no-store",
      });
      res.end(data);
    });
  }

  if (urlPath === "/") urlPath = "/index.html";

  /* shared/ berisi modul skema yang dipakai bersama browser & server, jadi
     ikut disajikan (read-only) supaya halaman bisa meng-import-nya. */
  const root = urlPath.startsWith("/shared/") ? SHARED_ROOT : PUBLIC;
  const filePath = path.join(root, path.normalize(
    root === SHARED_ROOT ? urlPath.replace("/shared/", "/") : urlPath,
  ));
  if (!filePath.startsWith(root)) {
    res.writeHead(403);
    return res.end("Forbidden");
  }

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      return res.end("Not found");
    }

    res.writeHead(200, {
      "Content-Type": MIME[path.extname(filePath)] || "application/octet-stream"
    });
    res.end(data);
  });
});

/* ----------------------- WebSocket ----------------------- */
const wss = new WebSocketServer({ server: httpServer });
const clients = new Set();

function broadcast(obj) {
  // Tap telemetry untuk rekaman trajectory (bila sesi rekam aktif). Ini hanya
  // "menguping" — tidak mengubah/menghambat aliran telemetry ke dashboard.
  if (obj && obj.type === "telemetry") {
    recording.onTelemetry(obj.data);
    trackAutonomousRun(obj.data);
  }
  const s = JSON.stringify(obj);
  for (const c of clients) {
    if (c.readyState === 1) c.send(s);
  }
}

function broadcastRecordStatus() {
  broadcast({ type: "record_status", data: recording.status() });
}

wss.on("connection", (ws, req) => {
  clients.add(ws);
  const ip = req.socket.remoteAddress;

  console.log(`[WS] dashboard terhubung (${ip}). Total: ${clients.size}`);

  ws.send(JSON.stringify({
    type: "event",
    text: `Terhubung ke server (${SIM ? "SIM" : "LIVE"})`,
    level: "ok"
  }));

  // kirim config joystick saat dashboard baru connect
  ws.send(JSON.stringify({
    type: "joystick_config",
    data: joystickConfig,
  }));

  // kirim status rekaman saat ini agar dashboard baru langsung sinkron
  ws.send(JSON.stringify({ type: "record_status", data: recording.status() }));

  ws.on("message", (raw) => {


    console.log("WS RAW =", raw.toString());


    let msg;
    try {
        msg = JSON.parse(raw);

        console.log("TYPE =", msg.type);
        console.log("NAME =", msg.name);

    } catch (e) {
        console.error("JSON ERROR:", e);
        return;
    }

    // ================= PING =================
    if (msg.type === "ping") {
      ws.send(JSON.stringify({ type: "pong", t: msg.t }));
      return;
    }

    // ================= JOYSTICK CONFIG GET =================
    if (msg.type === "joystick_config_get") {
      ws.send(JSON.stringify({
        type: "joystick_config",
        data: joystickConfig,
      }));
      return;
    }

    // ================= JOYSTICK CONFIG SAVE =================
    if (msg.type === "joystick_config_save") {
      try {
        const { profile, warnings } = joyConfig.save(msg.data);
        joystickConfig = profile;

        ws.send(JSON.stringify({
          type: "event",
          text: warnings.length
            ? `Mapping disimpan dengan ${warnings.length} koreksi: ${warnings[0]}`
            : "Joystick mapping berhasil disimpan",
          level: warnings.length ? "warn" : "ok",
        }));

        /* Disiarkan ke SEMUA dashboard, bukan hanya yang menyimpan — kalau
           tidak, tab lain tetap memegang profil basi sampai reconnect. */
        broadcast({ type: "joystick_config", data: joystickConfig });

        console.log("[JOYCFG] mapping joystick disimpan oleh dashboard");
      } catch (err) {
        ws.send(JSON.stringify({
          type: "event",
          text: `Gagal menyimpan joystick mapping: ${err.message}`,
          level: "err",
        }));

        console.warn("[JOYCFG] save gagal:", err.message);
      }
      return;
    }

    // ================= RECORD START (fitur Replay) =================
    // Message type khusus — SENGAJA bukan "cmd" sehingga tidak pernah
    // diteruskan ke UDP/ROV. Rekaman murni sisi server (telemetry + video tap).
    if (msg.type === "record_start") {
      if (recording.isRecording()) {
        ws.send(JSON.stringify({ type: "event", text: "Rekaman sudah berjalan", level: "warn" }));
        broadcastRecordStatus();
        return;
      }
      try {
        const meta = recording.startSession(msg.cameras, {
          allowHost: isAllowedCamHost,
          label: msg.label,
          onWarn: (m) => { console.warn("[REC]", m); broadcast({ type: "event", text: `Rekam: ${m}`, level: "warn" }); },
          onStatus: () => { broadcastRecordStatus(); broadcast({ type: "event", text: "Rekaman auto-stop (batas durasi)", level: "warn" }); },
        });
        console.log(`[REC] sesi dimulai: ${meta.id} (kamera: ${meta.cameras.map((c) => c.role).join(",") || "—"})`);
        broadcast({ type: "event", text: `Rekaman sesi dimulai: ${meta.id}`, level: "ok" });
        broadcastRecordStatus();
      } catch (err) {
        console.warn("[REC] gagal mulai:", err.message);
        ws.send(JSON.stringify({ type: "event", text: `Gagal mulai rekam: ${err.message}`, level: "err" }));
      }
      return;
    }

    // ================= RECORD STOP =================
    if (msg.type === "record_stop") {
      if (!recording.isRecording()) {
        broadcastRecordStatus();
        return;
      }
      const meta = recording.stopSession("manual");
      if (meta) console.log(`[REC] sesi berhenti: ${meta.id} (${meta.duration_ms} ms, ${meta.trajectory_samples} sampel)`);
      broadcast({ type: "event", text: `Rekaman dihentikan: ${meta ? meta.id : ""}`, level: "ok" });
      broadcastRecordStatus();
      return;
    }

    // ================= COMMAND KE ROV =================
    console.log("MASUK IF CMD");
    if (msg.type === "cmd") {
      /* ================= MANIPULATOR ================= */

      if (msg.name === "manipulator") {

          const packet = Buffer.from(JSON.stringify(msg));

          console.log("[MANIPULATOR]", msg);
          console.log("KIRIM UDP =", msg);
          
          udp.send(packet, UDP_OUT, RPI_ADDR, (e) => {
              if (e) console.warn("[UDP] gagal kirim manipulator:", e.message);
          });

          return;
      }
      
      if (MOTION_AXES.has(msg.name)) {
        msg.value = clampAxis(msg.name, msg.value);
      }

      // Tap command relevan-trajectory untuk rekaman (surge/sway/dll). Hanya
      // menguping nilai yang lewat — tidak mengubah routing command ke ROV.
      recording.onCommand(msg.name, msg.value);

      // di mode SIM, pantulkan status perintah agar tombol header berefek nyata
      if (SIM) applySimCommand(msg.name, msg.value, msg);

      // teruskan command ke Raspi via UDP
      let command = {
          name: msg.name,
          value: msg.value,
          t: Date.now()
      };

      if (msg.name === "thruster_config") {
          const gain = Number(msg.gain);

          command.gain = Number.isFinite(gain)
              ? Math.max(0, Math.min(100, gain))
              : 100;

          command.motors = msg.motors;
      }

      if (msg.name === "motor_test") {
          command.motor = msg.motor;
          command.throttle = msg.throttle;
          command.duration = msg.duration;
          command.direction = msg.direction;
      }

      if (msg.name === "camera_resolution") {
          command.camera = msg.camera;
          command.resolution = msg.resolution;
      }

      const packet = Buffer.from(JSON.stringify(command));

      udp.send(packet, UDP_OUT, RPI_ADDR, (e) => {
        if (e) console.warn("[UDP] gagal kirim command:", e.message);
      });

      const prettyValue =
       typeof msg.value === "object"
       ? JSON.stringify(msg.value)
      : String(msg.value);

      console.log(`[CMD] ${msg.name} = ${prettyValue} -> ${RPI_ADDR}:${UDP_OUT}`);
    }
  });

  ws.on("close", () => {
    clients.delete(ws);
    console.log(`[WS] terputus. Total: ${clients.size}`);
  });
});

/* ----------------------- UDP (telemetry masuk) ----------------------- */
const udp = dgram.createSocket("udp4");

udp.on("message", (buf, rinfo) => {
  let data;
  try {
    data = JSON.parse(buf.toString());
  } catch {
    return;
  }

  /* Diskriminator envelope. rov_agent.py mengirim telemetry sebagai dict
     `state` TELANJANG (tanpa field "type"), sementara kanal baru
     (param_batch / param_ack / mavlink_msg / statustext) SELALU membawa "type".
     Tanpa cek ini semuanya akan terbungkus sebagai telemetry — dan karena
     broadcast() men-tap yang bertipe telemetry ke perekam Replay, tabel param
     ikut tertulis ke trajectory.jsonl. */
  if (data && typeof data.type === "string") {
    broadcast(data);
    return;
  }

  console.log(
    `[TELEM] from ${rinfo.address}:${rinfo.port} | ` +
    `heading=${data.heading} roll=${data.roll} pitch=${data.pitch} ` +
    `volt=${data.voltage} armed=${data.armed} mode=${data.mode}`
  );

  broadcast({ type: "telemetry", data, recv: Date.now() });
});

udp.on("error", (e) => console.error("[UDP] error:", e.message));

udp.bind(UDP_IN, "0.0.0.0", () => {
  console.log(`[UDP] mendengar telemetri di 0.0.0.0:${UDP_IN}`);
});

/* ----------------------- simulator (opsional) ----------------------- */
// status yang dikendalikan tombol header (di-echo balik di telemetri SIM)
// Padanan rov_modes.PILOT_MODE_MAP di sisi Python — dipakai agar telemetri SIM
// melaporkan nama mode ArduSub yang sama dengan yang dikirim Pixhawk sungguhan,
// sehingga tab mode bisa diuji tanpa hardware.
const PILOT_MODE_MAP = {
  manual: "MANUAL",
  stabilize: "STABILIZE",
  depth_hold: "ALT_HOLD",
  // Overlay heading-hold sisi Pi di atas ALT_HOLD, BUKAN mode POSHOLD firmware
  // — lihat docstring rov_modes.py. Karena mode ArduSub-nya sama dengan
  // depth_hold, yang membedakan di GUI adalah flag `poshold` di telemetri.
  poshold: "ALT_HOLD",
};

const DEPTH_HOLD_MODES = new Set(["STABILIZE"]);   // rov_modes.DEPTH_HOLD_MODES

const simState = {
  armed: false,
  light: false,
  controlMode: "manual", // gate otoritas GUI: manual | autonomous
  pilotMode: "MANUAL",   // mode ArduSub yang "dilaporkan" wahana palsu
  // Padanan poshold_active di rov_agent.py. Tidak bisa disimpulkan dari
  // pilotMode: POSHOLD dan Alt Hold sama-sama ALT_HOLD.
  poshold: false,
};

// Counter trial Misi 2/3 palsu (padanan mission_counter_fails di rov_agent.py)
// — supaya tim bisa latihan pakai Guidebook §4.7.4 tanpa Pixhawk/Pi sama sekali.
const simMissionCounterFails = { m2: 0, m3: 0 };
function simTierScore(fails) {
  const trial = fails + 1;
  return trial === 1 ? 15 : trial === 2 ? 10 : 5;
}

/* Tabel param palsu (halaman Vehicle) — diisi saat start() bila mode SIM.
   Sumbernya dump nyata dari Pixhawk, lihat server/sim-params.js. */
const { SimParams, resolvePidWrites } = require("./sim-params");
let simParams = null;
let simParamStreamCancel = null;

// Kedalaman kolam yang "diketahui" wahana palsu — dipantulkan di telemetri SIM
// supaya operator bisa memastikan nilainya benar-benar sampai (sama seperti
// state["pool_depth"] di rov_agent.py).
let simPoolDepth = null;

/* Depth-set palsu (padanan depth_target + depth_hold_enabled di rov_agent.py).
   null = operator belum menekan SET, dibedakan dari 0 yang setpoint sah. Ada di
   SIM supaya alur "SET -> ON -> OFF" dan janji bahwa masuk Alt Hold TIDAK
   memasang setpoint apa pun bisa diuji penuh dari browser tanpa Pixhawk. */
let simDepthTarget = null;
let simDepthHoldEnabled = false;

/* Kedalaman "sekarang" wahana palsu. Dipakai DUA tempat — paket telemetri dan
   tombol SET — jadi rumusnya harus satu: kalau SET memakai rumus lain, setpoint
   yang direkam tidak akan pernah cocok dengan angka yang dilihat operator.
   Kolam KKI 2026 dangkal (~0.9 m) → depth ~0.1-0.8 m agar ALT & alarm realistis. */
let simClock = 0;
// Heading "sungguhan" simulator pada waktu t — sama dengan rumus yang dipakai
// broadcast telemetri di bawah. Dipakai untuk mengunci heading_target saat
// POSHOLD baru dinyalakan, bukan angka tetap.
function simHeadingNow() {
  return (90 + 45 * Math.sin(simClock * 0.2) + 360) % 360;
}
// Heading yang dikunci saat POSHOLD diaktifkan — padanan auto-seed
// heading_target dari state["heading"] di apply_heading_hold() (rov_agent.py).
// null selama POSHOLD tidak aktif.
let simHeadingTarget = null;
function simDepthNow() {
  return 0.45 + 0.35 * Math.sin(simClock * 0.13);
}

// Padanan rov_pid.clamp_depth_target().
function clampSimDepthTarget(value) {
  let v = Number(value);
  if (!Number.isFinite(v)) return 0;
  v = Math.max(0, v);
  if (simPoolDepth != null) v = Math.min(v, simPoolDepth);
  return v;
}

/* Stream MAVLink palsu (halaman Analyze). Dimatikan saat GUI mengirim
   mavlink_stream:false — sama seperti rov_agent.py.
   Padanan rov_mavlink.STREAM_KEEPALIVE_TIMEOUT: stream juga mati sendiri kalau
   GUI berhenti memperbarui permintaan. Tanpa ini SIM akan terus menyiarkan
   selamanya setelah tab ditutup mendadak — perilaku yang BERBEDA dari wahana,
   padahal justru itu yang ingin diuji di SIM. */
const SIM_STREAM_KEEPALIVE_MS = 30_000;
let simMavStream = null;
let simMavStreamLastReq = 0;

function stopSimMavStream() {
  if (simMavStream) { clearInterval(simMavStream); simMavStream = null; }
}

function startSimMavStream() {
  simMavStreamLastReq = Date.now();
  if (simMavStream) return;   // sudah jalan: ini cuma keepalive
  let t = 0;
  // 10 Hz per type, sama dengan throttle rov_mavlink.RateLimiter di wahana.
  simMavStream = setInterval(() => {
    if (Date.now() - simMavStreamLastReq > SIM_STREAM_KEEPALIVE_MS) {
      console.log("[SIM] mavlink_stream mati (GUI berhenti memperbarui permintaan)");
      stopSimMavStream();
      return;
    }
    t += 0.1;
    const now = Date.now() / 1000;
    const emit = (msg, fields) => broadcast({ type: "mavlink_msg", msg, t: now, fields });

    emit("ATTITUDE", {
      time_boot_ms: Math.round(t * 1000),
      roll: (10 * Math.sin(t * 0.6) * Math.PI) / 180,
      pitch: (7 * Math.sin(t * 0.4 + 1) * Math.PI) / 180,
      yaw: (((90 + 45 * Math.sin(t * 0.2)) % 360) * Math.PI) / 180,
      rollspeed: 0.01 * Math.cos(t * 0.6),
      pitchspeed: 0.01 * Math.cos(t * 0.4),
      yawspeed: 0.01 * Math.cos(t * 0.2),
    });

    emit("VFR_HUD", {
      airspeed: 0, groundspeed: 0.2 + 0.1 * Math.sin(t),
      heading: Math.round((90 + 45 * Math.sin(t * 0.2) + 360) % 360),
      throttle: 50, alt: -(0.45 + 0.35 * Math.sin(t * 0.13)), climb: 0.05 * Math.cos(t * 0.13),
    });

    emit("SYS_STATUS", {
      voltage_battery: Math.round((15.7 + 0.2 * Math.sin(t)) * 1000),
      current_battery: Math.round(300 + 100 * Math.sin(t * 0.3)),
      battery_remaining: 78,
    });

    emit("HEARTBEAT", {
      type: 12, autopilot: 3,
      base_mode: simState.armed ? 209 : 81,
      custom_mode: 19, system_status: 4,
    });
  }, 100);
}

/* Perintah param di mode SIM. Mengembalikan true kalau sudah ditangani, supaya
   pemanggil tahu tidak ada yang perlu diteruskan lagi. */
function applySimParamCommand(name, value) {
  if (!simParams) return false;

  if (name === "param_list") {
    if (simParamStreamCancel) simParamStreamCancel();
    console.log(`[SIM] kirim ${simParams.size} param ke dashboard`);
    simParamStreamCancel = simParams.streamAll(broadcast);
    return true;
  }

  if (name === "param_get") {
    const entry = simParams.get(value);
    if (!entry) {
      broadcast({ type: "param_ack", name: String(value), ok: false, reason: "param tidak dikenal di FC" });
      return true;
    }
    broadcast({
      type: "param_batch",
      params: [{ ...entry, index: -1, count: simParams.size }],
      done: false,
    });
    return true;
  }

  if (name === "param_set") {
    if (!value || typeof value !== "object") {
      broadcast({ type: "param_ack", name: "?", ok: false, reason: "payload param_set tidak valid" });
      return true;
    }

    const res = simParams.set(value.name, value.value);
    if (!res.ok) {
      broadcast({ type: "param_ack", name: String(value.name), ok: false, reason: res.reason });
      console.warn(`[SIM] param_set ditolak: ${value.name} — ${res.reason}`);
      return true;
    }

    /* Urutannya sengaja sama seperti wahana nyata: FC meng-echo PARAM_VALUE,
       dan echo itulah yang jadi bukti berhasil. */
    broadcast({
      type: "param_batch",
      params: [{ ...res.entry, index: -1, count: simParams.size }],
      done: false,
    });
    broadcast({ type: "param_ack", name: res.entry.name, ok: true, value: res.entry.value });
    console.log(`[SIM] param_set ${res.entry.name} = ${res.entry.value}`);
    return true;
  }

  if (name === "mavlink_stream") {
    if (value) startSimMavStream();
    else stopSimMavStream();
    return true;
  }

  if (name === "pid") {
    /* Meniru rov_agent.py: gain di luar rentang aman DITOLAK (bukan dijepit),
       yang lolos ditulis lalu di-echo balik seperti PARAM_VALUE dari FC. */
    const { writes, rejects } = resolvePidWrites(value);

    for (const [param, reason] of rejects) {
      console.warn(`[SIM] pid DITOLAK ${param}: ${reason}`);
      broadcast({ type: "param_ack", name: param, ok: false, reason });
    }

    for (const [param, gain] of writes) {
      const res = simParams.set(param, gain);
      if (!res.ok) {
        broadcast({ type: "param_ack", name: param, ok: false, reason: res.reason });
        continue;
      }
      broadcast({
        type: "param_batch",
        params: [{ ...res.entry, index: -1, count: simParams.size }],
        done: false,
      });
      broadcast({ type: "param_ack", name: res.entry.name, ok: true, value: res.entry.value });
    }

    if (writes.length) console.log(`[SIM] pid: ${writes.length} gain ditulis`);
    return true;
  }

  if (name === "pool_depth") {
    const depth = Number(value);
    if (!Number.isFinite(depth) || depth <= 0) {
      console.warn(`[SIM] pool_depth tidak valid: ${value}`);
      return true;
    }
    simPoolDepth = depth;
    // Jepit ULANG target berjalan, sama seperti rov_agent.py. null dibiarkan
    // null: "belum di-set" tidak boleh berubah jadi setpoint 0 m.
    if (simDepthTarget != null) simDepthTarget = clampSimDepthTarget(simDepthTarget);
    console.log(`[SIM] kedalaman kolam = ${depth.toFixed(2)} m`);
    return true;
  }

  return false;
}

function applySimCommand(name, value, msg) {
  if (applySimParamCommand(name, value)) return;

  switch (name) {
    case "motor_test": {
      // Tidak ada wahana nyata di SIM — balas ack palsu setelah `duration`
      // supaya panel Thruster Test di Setup bisa diuji tanpa hardware.
      const motor = Number(msg && msg.motor);
      const direction = (msg && msg.direction) || "forward";
      const duration = Math.max(0.2, Math.min(2.0, Number(msg && msg.duration) || 1.0));
      setTimeout(() => {
        broadcast({ type: "motor_test_ack", motor, direction, ok: true });
      }, duration * 1000);
      break;
    }
    case "arm":
      simState.armed = !!value;
      // Disarm dari jalur mana pun mematikan depth-set (setpoint dipertahankan),
      // sama seperti cek transisi armed->disarmed di handler HEARTBEAT
      // rov_agent.py. Tanpa ini wahana berenang sendiri ke setpoint lama begitu
      // di-arm ulang.
      if (!simState.armed) simDepthHoldEnabled = false;
      break;
    case "light":
      simState.light = !!value;
      break;
    case "stop":
      simState.armed = false;
      // E-Stop mematikan depth-set tapi MEMPERTAHANKAN setpoint, sama seperti
      // handler stop di rov_agent.py.
      simDepthHoldEnabled = false;
      break; // failsafe: netralkan
    case "control_mode":
      simState.controlMode = value;
      break;
    case "pilot_mode": {
      // Tolak nama tak dikenal, persis seperti rov_agent.py — supaya bug
      // penamaan ketahuan di SIM, bukan baru di kolam.
      const mapped = PILOT_MODE_MAP[String(value).toLowerCase()];
      if (!mapped) {
        console.warn(`[SIM] pilot_mode tidak dikenal: ${value}`);
        break;
      }
      simState.pilotMode = mapped;
      // Setiap permintaan mode mematikan overlay lebih dulu, persis seperti
      // handler pilot_mode di rov_agent.py.
      const enteringPoshold =
        String(value).toLowerCase() === "poshold" && !simState.poshold;
      simState.poshold = String(value).toLowerCase() === "poshold";
      // Kunci heading SEKARANG, bukan angka tetap — padanan auto-seed di
      // apply_heading_hold() (rov_agent.py) saat POSHOLD baru dinyalakan.
      // Keluar dari POSHOLD melepas target, sama seperti heading_target=None
      // di sisi Pi ketika overlay berhenti.
      if (enteringPoshold) simHeadingTarget = simHeadingNow();
      else if (!simState.poshold) simHeadingTarget = null;
      // Sengaja TIDAK menyentuh depth-set: masuk Alt Hold berarti "tahan
      // kedalaman sekarang" (kerjaan cascade PID ArduSub), bukan "menyelam ke
      // setpoint". Sama seperti handler pilot_mode di rov_agent.py.
      //
      // TAPI: kalau depth-set sudah ON dari sesi sebelumnya dan errornya besar
      // (operator pindah mode sambil wahana jauh dari setpoint lama), matikan
      // saklarnya. Tanpa ini, pindah balik ke Alt Hold memicu bias throttle
      // penuh tanpa operator menekan apa pun — kejutan yang sama seperti
      // "masuk Alt Hold langsung menyelam" yang sedang diperbaiki.
      if (
        DEPTH_HOLD_MODES.has(mapped) &&
        simDepthHoldEnabled &&
        simDepthTarget != null &&
        Math.abs(simDepthTarget - simDepthNow()) > 0.3
      ) {
        simDepthHoldEnabled = false;
        console.log(
          `[SIM] depth-set OFF — error ${Math.abs(simDepthTarget - simDepthNow()).toFixed(2)} m terlalu besar saat pindah mode`
        );
      }
      break;
    }
    // Tombol SET: rekam kedalaman "sekarang". Tidak menuntut armed maupun mode
    // tertentu — merekam angka tidak menggerakkan apa pun (lihat rov_agent.py).
    case "depth_set": {
      simDepthTarget = clampSimDepthTarget(simDepthNow());
      console.log(`[SIM] depth set = ${simDepthTarget.toFixed(2)} m`);
      break;
    }

    // Saklar ON/OFF. value null (dari tombol gamepad) = toggle.
    case "depth_hold": {
      const want = value == null ? !simDepthHoldEnabled : !!value;
      if (want && simDepthTarget == null) {
        console.log("[SIM] depth_hold ON diabaikan — belum ada setpoint");
        break;
      }
      if (want && !simState.armed) {
        console.log("[SIM] depth_hold ON diabaikan — belum armed");
        break;
      }
      simDepthHoldEnabled = want;
      console.log(`[SIM] depth-set ${want ? "ON" : "OFF"}`);
      break;
    }

    // Counter trial Misi 2/3 — padanan handler mission_counter di rov_agent.py.
    case "mission_counter": {
      const mission = value && value.mission;
      const event = value && value.event;
      if (event === "reset") {
        simMissionCounterFails.m2 = 0;
        simMissionCounterFails.m3 = 0;
      } else if (event === "fail" && mission in simMissionCounterFails) {
        simMissionCounterFails[mission] += 1;
      } else if (event === "set" && mission in simMissionCounterFails) {
        const trial = Number(value.trial);
        if (Number.isFinite(trial) && trial >= 1) {
          simMissionCounterFails[mission] = Math.floor(trial) - 1;
        }
      }
      console.log(`[SIM] Counter ${mission || "ALL"}: ${event} ->`, simMissionCounterFails);
      break;
    }
  }
}

if (SIM) {
  console.log("[SIM] menghasilkan telemetri palsu (tanpa Raspi).");
  let t = 0;

  setInterval(() => {
    t += 0.1;
    simClock = t;

    broadcast({
      type: "telemetry",
      data: {
        heading: (90 + 45 * Math.sin(t * 0.2) + 360) % 360,
        depth: simDepthNow(),
        roll: 10 * Math.sin(t * 0.6),
        pitch: 7 * Math.sin(t * 0.4 + 1),
        temp: 26.5 + Math.sin(t * 0.05),
        voltage: 15.7 + 0.2 * Math.sin(t),
        armed: simState.armed,
        light: simState.light,
        // Sama seperti ROV sungguhan: field `mode` adalah mode ArduSub dari
        // HEARTBEAT, bukan control_mode.
        mode: simState.pilotMode,
        // Overlay heading-hold: tidak terlihat di `mode` (ia berjalan di
        // ALT_HOLD), jadi tab POSHOLD di GUI menyala dari flag ini.
        poshold: simState.poshold,
        heading_target: simState.poshold ? simHeadingTarget : null,
        control_mode: simState.controlMode,
        pool_depth: simPoolDepth,
        depth_target: simDepthTarget,
        depth_hold: simDepthHoldEnabled,
        mission_counter: {
          m2_fails: simMissionCounterFails.m2,
          m2_score: simTierScore(simMissionCounterFails.m2),
          m3_fails: simMissionCounterFails.m3,
          m3_score: simTierScore(simMissionCounterFails.m3),
        },
      },
      recv: Date.now(),
    });
  }, 100);
}

/* Bootstrap async: skema joystick adalah ES module, dimuat lewat await import()
   (jalan di Node 14+, tidak bergantung require(ESM) yang baru ada di Node
   >=22.12). Server baru listen setelah profil siap, sehingga handler WS tidak
   pernah melihat joystickConfig === null. */
async function start() {
  try {
    await joyConfig.loadSchema();
    joystickConfig = await joyConfig.load();
  } catch (err) {
    console.error("[JOYCFG] FATAL: gagal memuat profil joystick:", err.message);
    process.exit(1);
  }

  if (SIM) {
    /* Bukan fatal: kalau dump param hilang, sisa mode SIM tetap berguna —
       hanya halaman Vehicle yang kosong. */
    try {
      simParams = SimParams.load();
      console.log(`[SIM] tabel param dimuat: ${simParams.size} param dari parameters_ardusub.params`);
    } catch (err) {
      console.warn("[SIM] gagal memuat parameters_ardusub.params:", err.message);
      console.warn("[SIM] halaman Vehicle akan kosong di mode simulasi.");
    }
  }

  httpServer.listen(WS_PORT, () => {
    console.log(`\n  HYDROSHIP server aktif`);
    console.log(`  Dashboard : http://localhost:${WS_PORT}`);
    console.log(`  WebSocket : ws://localhost:${WS_PORT}`);
    console.log(`  Raspi cmd : ${RPI_ADDR}:${UDP_OUT}   telemetry in: :${UDP_IN}`);
    console.log(`  Mode      : ${SIM ? "SIMULASI" : "LIVE"}`);
    console.log(`  Joy cfg   : ${joyConfig.configPath()}\n`);
  });
}

start();
