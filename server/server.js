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
const { WebSocketServer } = require("ws");
const recording = require("./recording");

const WS_PORT  = parseInt(process.env.WS_PORT  || "8080", 10);
const UDP_IN   = parseInt(process.env.UDP_IN   || "14551", 10); // telemetry dari ROV
const UDP_OUT  = parseInt(process.env.UDP_OUT  || "14550", 10); // command ke ROV
const RPI_ADDR = process.env.RPI_ADDR || "192.168.2.2";
const SIM = process.argv.includes("--sim");

const PUBLIC = path.join(__dirname, "..", "public");

/* Axis kontrol manual (persen -100..100). Divalidasi ulang sebelum diteruskan
   ke Pi supaya input klien tidak bisa mengirim nilai di luar rentang. */
const MOTION_AXES = new Set(["surge", "sway", "yaw", "heave"]);
function clampPercent(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 0;
  return Math.max(-100, Math.min(100, Math.round(n)));
}

/* ======================= JOYSTICK CONFIG FILE ======================= */
const CONFIG_DIR = path.join(__dirname, "config");
const JOY_CFG_FILE = path.join(CONFIG_DIR, "joystick-profile.json");

function defaultButtonLayer() {
  return [
    { action: "arm", button: 0, mode: "toggle" },
    { action: "disarm", button: 1, mode: "toggle" },
    { action: "mode_manual", button: 2, mode: "toggle" },
    { action: "mode_stabilize", button: 3, mode: "toggle" },
    { action: "mode_depth_hold", button: 4, mode: "toggle" },
    { action: "mount_tilt_up", button: 5, mode: "hold" },
    { action: "mount_tilt_down", button: 6, mode: "hold" },
    { action: "mount_center", button: 7, mode: "toggle" },
    { action: "actuator1_inc", button: 8, mode: "hold" },
    { action: "actuator1_dec", button: 9, mode: "hold" },
    { action: "lights_brighter", button: 10, mode: "hold" },
    { action: "lights_dimmer", button: 11, mode: "hold" },
    { action: "gain_inc", button: 12, mode: "hold" },
    { action: "gain_dec", button: 13, mode: "hold" },
    { action: "input_hold_set", button: 14, mode: "toggle" },
    { action: "no_function", button: 15, mode: "toggle" },
  ];
}

function defaultJoystickConfig() {
  return {
    enabled: true,
    shiftButton: 5,

    axisConfig: [
      { input: "axis 0", assigned: "Axis X", min: -1000, max: 1000, direction: "↔" },
      { input: "axis 1", assigned: "Axis Y", min: 1000, max: -1000, direction: "↕" },
      { input: "axis 2", assigned: "Axis R", min: -1000, max: 1000, direction: "↔" },
      { input: "axis 3", assigned: "Axis Z", min: 1000, max: -1000, direction: "↕" },
      { input: "axis 4", assigned: "No function", min: -1, max: 1, direction: "↕" },
    ],

    buttonConfig: {
      regular: defaultButtonLayer(),
      shift: defaultButtonLayer(),
    },
  };
}

function ensureConfigDir() {
  if (!fs.existsSync(CONFIG_DIR)) {
    fs.mkdirSync(CONFIG_DIR, { recursive: true });
  }
}

function sanitizeJoystickConfig(data) {
  const fallback = defaultJoystickConfig();
  if (!data || typeof data !== "object") return fallback;

  const enabled = data.enabled !== false;
  const shiftButton = Number.isInteger(Number(data.shiftButton))
    ? Number(data.shiftButton)
    : fallback.shiftButton;

  /* ================= AXIS ================= */
  const srcAxis = Array.isArray(data.axisConfig)
    ? data.axisConfig
    : fallback.axisConfig;

  const axisConfig = fallback.axisConfig.map((def, i) => {
    const row = srcAxis[i] || {};
    return {
      input: typeof row.input === "string" ? row.input : def.input,
      assigned: typeof row.assigned === "string" ? row.assigned : def.assigned,
      min: Number.isFinite(Number(row.min)) ? Number(row.min) : def.min,
      max: Number.isFinite(Number(row.max)) ? Number(row.max) : def.max,
      direction: row.direction === "↕" ? "↕" : "↔",
    };
  });

  /* ================= BUTTON ================= */
  const srcBtnCfg = (data.buttonConfig && typeof data.buttonConfig === "object")
    ? data.buttonConfig
    : {};

  const buttonConfig = {
    regular: fallback.buttonConfig.regular.map((def, i) => {
      const row = Array.isArray(srcBtnCfg.regular) ? (srcBtnCfg.regular[i] || {}) : {};
      return {
        action: typeof row.action === "string" ? row.action : def.action,
        button: Number.isFinite(Number(row.button)) ? Number(row.button) : def.button,
        mode: row.mode === "hold" ? "hold" : "toggle",
      };
    }),

    shift: fallback.buttonConfig.shift.map((def, i) => {
      const row = Array.isArray(srcBtnCfg.shift) ? (srcBtnCfg.shift[i] || {}) : {};
      return {
        action: typeof row.action === "string" ? row.action : def.action,
        button: Number.isFinite(Number(row.button)) ? Number(row.button) : def.button,
        mode: row.mode === "hold" ? "hold" : "toggle",
      };
    }),
  };

  return {
    enabled,
    shiftButton,
    axisConfig,
    buttonConfig,
  };
}

function loadJoystickConfig() {
  try {
    if (!fs.existsSync(JOY_CFG_FILE)) {
      const cfg = defaultJoystickConfig();
      ensureConfigDir();
      fs.writeFileSync(JOY_CFG_FILE, JSON.stringify(cfg, null, 2), "utf8");
      console.log("[JOYCFG] file config belum ada, dibuat default");
      return cfg;
    }

    const raw = fs.readFileSync(JOY_CFG_FILE, "utf8");
    const parsed = JSON.parse(raw);
    const cfg = sanitizeJoystickConfig(parsed);
    console.log("[JOYCFG] config joystick dimuat");
    return cfg;
  } catch (err) {
    console.warn("[JOYCFG] gagal load config, pakai default:", err.message);
    return defaultJoystickConfig();
  }
}

function saveJoystickConfig(data) {
  const cfg = sanitizeJoystickConfig(data);
  ensureConfigDir();
  fs.writeFileSync(JOY_CFG_FILE, JSON.stringify(cfg, null, 2), "utf8");
  console.log("[JOYCFG] config joystick disimpan ke", JOY_CFG_FILE);
  return cfg;
}

let joystickConfig = loadJoystickConfig();

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

    const mod = t.protocol === "https:" ? https : http;
    const up = mod.get(target, (upRes) => {
      res.writeHead(upRes.statusCode || 502, upRes.headers);
      upRes.pipe(res);
    });

    up.on("error", (e) => {
      if (!res.headersSent) res.writeHead(502);
      res.end("kamera upstream error: " + e.message);
    });

    req.on("close", () => up.destroy());
    return;
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

  const filePath = path.join(PUBLIC, path.normalize(urlPath));
  if (!filePath.startsWith(PUBLIC)) {
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
  if (obj && obj.type === "telemetry") recording.onTelemetry(obj.data);
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
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
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
        joystickConfig = saveJoystickConfig(msg.data);

        ws.send(JSON.stringify({
          type: "event",
          text: "Joystick mapping berhasil disimpan",
          level: "ok",
        }));

        ws.send(JSON.stringify({
          type: "joystick_config",
          data: joystickConfig,
        }));

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
    if (msg.type === "cmd") {
      // Jangan percaya input klien mentah-mentah: clamp axis kontrol manual
      // (surge/sway/yaw/heave) ke rentang persen valid -100..100 sebelum
      // diteruskan. Pi yang mengubahnya ke MANUAL_CONTROL (-1000..1000 / 0..1000).
      if (MOTION_AXES.has(msg.name)) {
        msg.value = clampPercent(msg.value);
      }

      // Tap command relevan-trajectory untuk rekaman (surge/sway/dll). Hanya
      // menguping nilai yang lewat — tidak mengubah routing command ke ROV.
      recording.onCommand(msg.name, msg.value);

      // di mode SIM, pantulkan status perintah agar tombol header berefek nyata
      if (SIM) applySimCommand(msg.name, msg.value);

      // teruskan command ke Raspi via UDP
      const packet = Buffer.from(JSON.stringify({
        name: msg.name,
        value: msg.value,
        t: Date.now()
      }));

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
const simState = { armed: false, light: false, mode: "manual" };

function applySimCommand(name, value) {
  switch (name) {
    case "arm":
      simState.armed = !!value;
      break;
    case "light":
      simState.light = !!value;
      break;
    case "stop":
      simState.armed = false;
      break; // failsafe: netralkan
    case "control_mode":
      simState.mode = value;
      break;
  }
}

if (SIM) {
  console.log("[SIM] menghasilkan telemetri palsu (tanpa Raspi).");
  let t = 0;

  setInterval(() => {
    t += 0.1;

    broadcast({
      type: "telemetry",
      data: {
        heading: (90 + 45 * Math.sin(t * 0.2) + 360) % 360,
        // Kolam KKI 2026 dangkal (~0.9 m) → depth ~0.1–0.8 m agar ALT & alarm realistis.
        depth: 0.45 + 0.35 * Math.sin(t * 0.13),
        roll: 10 * Math.sin(t * 0.6),
        pitch: 7 * Math.sin(t * 0.4 + 1),
        temp: 26.5 + Math.sin(t * 0.05),
        voltage: 15.7 + 0.2 * Math.sin(t),
        armed: simState.armed,
        light: simState.light,
        mode: simState.mode,
      },
      recv: Date.now(),
    });
  }, 100);
}

httpServer.listen(WS_PORT, () => {
  console.log(`\n  HYDROSHIP server aktif`);
  console.log(`  Dashboard : http://localhost:${WS_PORT}`);
  console.log(`  WebSocket : ws://localhost:${WS_PORT}`);
  console.log(`  Raspi cmd : ${RPI_ADDR}:${UDP_OUT}   telemetry in: :${UDP_IN}`);
  console.log(`  Mode      : ${SIM ? "SIMULASI" : "LIVE"}`);
  console.log(`  Joy cfg   : ${JOY_CFG_FILE}\n`);
});