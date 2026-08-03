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
const SHARED_ROOT = path.join(__dirname, "..", "shared");

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
      if (SIM) applySimCommand(msg.name, msg.value);

      // teruskan command ke Raspi via UDP
      let command = {
          name: msg.name,
          value: msg.value,
          t: Date.now()
      };

      if (msg.name === "thruster_config") {
          command.motors = msg.motors;
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
// sehingga tab mode & badge peringatan ACRO bisa diuji tanpa hardware.
const PILOT_MODE_MAP = {
  manual: "MANUAL",
  stabilize: "STABILIZE",
  depth_hold: "ALT_HOLD",
  acro: "ACRO",
};

const simState = {
  armed: false,
  light: false,
  controlMode: "manual", // gate otoritas GUI: manual | autonomous
  pilotMode: "MANUAL",   // mode ArduSub yang "dilaporkan" wahana palsu
};

/* Tabel param palsu (halaman Vehicle) — diisi saat start() bila mode SIM.
   Sumbernya dump nyata dari Pixhawk, lihat server/sim-params.js. */
const { SimParams, resolvePidWrites } = require("./sim-params");
let simParams = null;
let simParamStreamCancel = null;

// Kedalaman kolam yang "diketahui" wahana palsu — dipantulkan di telemetri SIM
// supaya operator bisa memastikan nilainya benar-benar sampai (sama seperti
// state["pool_depth"] di rov_agent.py).
let simPoolDepth = null;

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
    console.log(`[SIM] kedalaman kolam = ${depth.toFixed(2)} m`);
    return true;
  }

  return false;
}

function applySimCommand(name, value) {
  if (applySimParamCommand(name, value)) return;

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
      break;
    }
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
        // Sama seperti ROV sungguhan: field `mode` adalah mode ArduSub dari
        // HEARTBEAT, bukan control_mode.
        mode: simState.pilotMode,
        control_mode: simState.controlMode,
        pool_depth: simPoolDepth,
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