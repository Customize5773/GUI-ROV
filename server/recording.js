// recording.js — Manajer rekaman sesi misi untuk fitur REPLAY (nilai tambah KKI 2026).
//
// Merekam, PER SESI, dua hal yang tersinkron pada satu clock server:
//   1. Trajectory  : sampel telemetry (heading, depth, roll, pitch, pos_n, pos_e)
//                    + command gerak (surge/sway/yaw/heave/control_mode/set_surface)
//                    — keduanya berstempel waktu. pos_n/pos_e adalah posisi EKF
//                    ArduSub (LOCAL_POSITION_NED, meter) bila FC mengirimnya; kalau
//                    null (EKF belum publish saat sesi direkam), replay.js jatuh ke
//                    fallback dead-reckoning dari surge/sway + heading yang SAMA
//                    seperti mission.js.
//   2. Video       : stream MJPEG kamera BOTTOM & WALL ditap frame-per-frame. Tiap
//                    frame JPEG mentah ditulis apa adanya ke `<role>.mjpeg`, dengan
//                    index `<role>.index.jsonl` ({t, off, len}) agar bisa di-seek per
//                    timestamp saat replay. TANPA ffmpeg / dependency encoding berat.
//
// Semua tersimpan di server (laptop operator), bukan di Raspberry Pi:
//   server/recordings/<session_id>/
//     meta.json            metadata sesi (session_start_time, durasi, ukuran, dst)
//     trajectory.jsonl     {t, heading, depth, roll, pitch, pos_n, pos_e}
//     commands.jsonl       {t, name, value}   (hanya command relevan trajectory)
//     bottom.mjpeg         JPEG BOTTOM disambung
//     bottom.index.jsonl   {t, off, len} per frame
//     wall.mjpeg / wall.index.jsonl
//
// Modul ini SENGAJA terpisah dari jalur command→UDP→ROV: ia hanya "menguping"
// telemetry & command, tidak pernah mengirim apa pun ke ROV.

const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");

// Folder penyimpanan (override via env berguna untuk unit test).
const RECORDINGS_DIR = process.env.HYDROSHIP_RECORDINGS_DIR || path.join(__dirname, "recordings");

// Batas durasi rekam (menit). Run misi KKI ~ maks 20 menit (5 siap + 10 misi +
// 5 evakuasi, lihat README-WORK §4). Default aman 15 menit; configurable via env.
const MAX_RECORD_MIN = parseFloat(process.env.MAX_RECORD_MIN || "15");
const MAX_RECORD_MS = Math.max(1, MAX_RECORD_MIN) * 60 * 1000;

// Peran kamera yang valid untuk direkam (selaras CONFIG.CAMERAS di browser).
const VALID_ROLES = new Set(["bottom", "wall"]);

// Command yang relevan untuk rekonstruksi trajectory saat replay. Hanya ini yang
// ikut dicatat — sisanya (arm/light/gripper/dll) tak memengaruhi posisi.
const TRAJ_COMMANDS = new Set(["surge", "sway", "yaw", "heave", "control_mode", "set_surface"]);

// Penanda JPEG: SOI (Start Of Image) & EOI (End Of Image).
const JPEG_SOI = Buffer.from([0xff, 0xd8]);
const JPEG_EOI = Buffer.from([0xff, 0xd9]);
// Jaring pengaman: buang buffer parse bila membengkak tanpa menemукan frame utuh
// (mis. upstream bukan MJPEG). ~8 MB cukup longgar untuk 1 frame 1080p.
const MAX_PARSE_BUFFER = 8 * 1024 * 1024;

let active = null; // sesi yang sedang direkam (satu pada satu waktu)

/* ------------------------------------------------------------------ */
/* util                                                                */
/* ------------------------------------------------------------------ */

function ensureRecordingsDir() {
  if (!fs.existsSync(RECORDINGS_DIR)) fs.mkdirSync(RECORDINGS_DIR, { recursive: true });
}

// id sesi berbasis waktu lokal: 2026-07-14_09-30-05-123 → mudah diurut & dibaca.
// Milidetik disertakan agar dua run dalam detik yang sama tidak bertabrakan id.
function makeSessionId(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}` +
    `_${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}` +
    `-${String(d.getMilliseconds()).padStart(3, "0")}`
  );
}

// Cegah path traversal: hanya izinkan id sesi "polos" (tanpa / .. dsb).
function isSafeId(id) {
  return typeof id === "string" && /^[A-Za-z0-9_\-]+$/.test(id) && id.length <= 64;
}

function isRecording() {
  return !!active;
}

/* ------------------------------------------------------------------ */
/* MJPEG frame tap                                                     */
/* ------------------------------------------------------------------ */

// Buka stream MJPEG satu kamera & tulis tiap frame JPEG ke file + index.
// `cam` adalah objek state kamera pada sesi aktif.
function openCameraTap(cam, allowHost, onWarn) {
  let target;
  try {
    target = new URL(cam.url);
  } catch {
    onWarn(`URL kamera ${cam.role} tidak valid: ${cam.url}`);
    return;
  }
  if (target.protocol !== "http:" && target.protocol !== "https:") {
    onWarn(`Protokol kamera ${cam.role} tidak didukung: ${target.protocol}`);
    return;
  }
  if (typeof allowHost === "function" && !allowHost(target.hostname)) {
    onWarn(`Host kamera ${cam.role} tidak diizinkan: ${target.hostname}`);
    return;
  }

  const mod = target.protocol === "https:" ? https : http;
  let parseBuf = Buffer.alloc(0);

  const req = mod.get(cam.url, (res) => {
    if (!res.statusCode || res.statusCode >= 400) {
      onWarn(`Kamera ${cam.role} status ${res.statusCode}`);
      res.resume();
      return;
    }
    res.on("data", (chunk) => {
      parseBuf = parseBuf.length ? Buffer.concat([parseBuf, chunk]) : chunk;

      // Ekstrak setiap JPEG utuh (SOI..EOI) dari buffer berjalan.
      while (true) {
        const soi = parseBuf.indexOf(JPEG_SOI);
        if (soi < 0) {
          // belum ada awal frame; jaga buffer tidak membengkak tak terbatas
          if (parseBuf.length > MAX_PARSE_BUFFER) parseBuf = Buffer.alloc(0);
          break;
        }
        const eoi = parseBuf.indexOf(JPEG_EOI, soi + 2);
        if (eoi < 0) {
          // frame belum lengkap; buang byte sebelum SOI, tunggu sisanya
          if (soi > 0) parseBuf = parseBuf.slice(soi);
          if (parseBuf.length > MAX_PARSE_BUFFER) parseBuf = Buffer.alloc(0);
          break;
        }
        const frame = parseBuf.slice(soi, eoi + 2);
        parseBuf = parseBuf.slice(eoi + 2);
        writeFrame(cam, frame);
      }
    });
    res.on("end", () => onWarn(`Stream kamera ${cam.role} berakhir (upstream end)`));
    res.on("error", (e) => onWarn(`Stream kamera ${cam.role} error: ${e.message}`));
  });

  req.on("error", (e) => onWarn(`Koneksi kamera ${cam.role} gagal: ${e.message}`));
  cam.req = req;
}

function writeFrame(cam, frame) {
  if (!cam.mjpegStream || !cam.indexStream) return;
  const t = Date.now();
  const off = cam.bytes;
  const len = frame.length;
  cam.mjpegStream.write(frame);
  cam.indexStream.write(JSON.stringify({ t, off, len }) + "\n");
  cam.bytes += len;
  cam.frames += 1;
}

/* ------------------------------------------------------------------ */
/* start / stop                                                        */
/* ------------------------------------------------------------------ */

// cameras : [{ role: "bottom"|"wall", url: "http://..." }, ...]  (boleh kosong)
// opts    : { allowHost?, label?, onStatus?, onWarn? }
// return  : ringkasan meta sesi.
function startSession(cameras, opts = {}) {
  if (active) throw new Error("sesi rekaman sudah berjalan");
  ensureRecordingsDir();

  const allowHost = opts.allowHost;
  const onWarn = typeof opts.onWarn === "function" ? opts.onWarn : () => {};
  const startTime = Date.now();
  const id = makeSessionId(new Date(startTime));
  const dir = path.join(RECORDINGS_DIR, id);
  fs.mkdirSync(dir, { recursive: true });

  const cams = {};
  const camList = Array.isArray(cameras) ? cameras : [];
  for (const c of camList) {
    const role = String(c && c.role || "").toLowerCase();
    if (!VALID_ROLES.has(role) || !c.url) continue;
    if (cams[role]) continue; // hindari duplikat peran
    const cam = {
      role,
      url: c.url,
      frames: 0,
      bytes: 0,
      mjpegStream: fs.createWriteStream(path.join(dir, `${role}.mjpeg`)),
      indexStream: fs.createWriteStream(path.join(dir, `${role}.index.jsonl`)),
      req: null,
    };
    cams[role] = cam;
  }

  active = {
    id,
    dir,
    label: typeof opts.label === "string" ? opts.label.slice(0, 120) : "",
    startTime,
    /* Tulis sinkron: durable bila server crash di tengah run, dan langsung
       terbaca (tanpa menunggu buffer stream ter-flush).

       Deskriptornya dibuka SEKALI, bukan appendFileSync per baris. Baris
       trajectory memang cuma 10/detik, tapi commands.jsonl menerima keempat
       axis joystick sampai ~240 baris/detik — dan appendFileSync membuka +
       menutup file untuk SETIAP baris, di event loop yang sama yang
       meneruskan command ke ROV. writeSync ke fd yang sudah terbuka
       menghilangkan open/close itu tanpa mengurangi durabilitas (keduanya
       sama-sama tidak fsync). */
    trajPath: path.join(dir, "trajectory.jsonl"),
    cmdPath: path.join(dir, "commands.jsonl"),
    trajFd: openAppendFd(path.join(dir, "trajectory.jsonl")),
    cmdFd: openAppendFd(path.join(dir, "commands.jsonl")),
    cams,
    telemCount: 0,
    cmdCount: 0,
    timer: null,
    onStatus: typeof opts.onStatus === "function" ? opts.onStatus : () => {},
  };

  // tulis meta awal (status recording)
  writeMeta("recording");

  // buka tap tiap kamera
  for (const role of Object.keys(cams)) {
    openCameraTap(cams[role], allowHost, onWarn);
  }

  // auto-stop di batas durasi (cegah file membengkak tak terkendali)
  const onStatus = active.onStatus;
  active.timer = setTimeout(() => {
    onWarn(`Rekaman mencapai batas ${MAX_RECORD_MIN} menit — auto-stop.`);
    const meta = stopSession("max_duration");
    if (meta) { try { onStatus(meta); } catch {} }
  }, MAX_RECORD_MS);

  return summary();
}

function openAppendFd(file) {
  try { return fs.openSync(file, "a"); } catch { return null; }
}

function appendLine(fd, text) {
  if (fd === null || fd === undefined) return;
  try { fs.writeSync(fd, text); } catch {}
}

function stopSession(reason = "manual") {
  if (!active) return null;
  const s = active;

  if (s.timer) clearTimeout(s.timer);

  for (const fd of [s.trajFd, s.cmdFd]) {
    if (fd !== null && fd !== undefined) { try { fs.closeSync(fd); } catch {} }
  }
  s.trajFd = null;
  s.cmdFd = null;

  // tutup semua stream kamera & file
  for (const role of Object.keys(s.cams)) {
    const cam = s.cams[role];
    try { if (cam.req) cam.req.destroy(); } catch {}
    try { cam.mjpegStream.end(); } catch {}
    try { cam.indexStream.end(); } catch {}
  }

  const stopTime = Date.now();
  s.stopTime = stopTime;
  s.stopReason = reason;
  const meta = writeMeta("stopped");

  active = null;
  return meta;
}

/* ------------------------------------------------------------------ */
/* tap telemetry & command (dipanggil dari server.js)                  */
/* ------------------------------------------------------------------ */

function onTelemetry(data) {
  if (!active || !data) return;
  const hook = data.hook_xy || {};
  const line = {
    t: Date.now(),
    heading: numOrNull(data.heading),
    depth: numOrNull(data.depth),
    roll: numOrNull(data.roll),
    pitch: numOrNull(data.pitch),
    pos_n: numOrNull(data.pos_n),
    pos_e: numOrNull(data.pos_e),
    hook_xy_status: hook.status ?? null,
    hook_id: hook.hook_id ?? null,
    hook_x: numOrNull(hook.x),
    hook_y: numOrNull(hook.y),
    hook_z: numOrNull(hook.z),
    hook_sigma_xy_m: numOrNull(hook.sigma_xy_m),
    hook_reproj_px: numOrNull(hook.reproj_px),
    hook_confidence: numOrNull(hook.confidence),
    hook_offset_x: numOrNull(hook.offset_x),
    hook_offset_y: numOrNull(hook.offset_y),
    hook_reason: hook.reason ?? null,
  };
  appendLine(active.trajFd, JSON.stringify(line) + "\n");
  active.telemCount += 1;
}

function onCommand(name, value) {
  if (!active || !TRAJ_COMMANDS.has(name)) return;
  appendLine(active.cmdFd, JSON.stringify({ t: Date.now(), name, value }) + "\n");
  active.cmdCount += 1;
}

function numOrNull(v) {
  // null/undefined/"" → null (field telemetry hilang), bukan 0 (Number(null)===0).
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/* ------------------------------------------------------------------ */
/* metadata & listing                                                  */
/* ------------------------------------------------------------------ */

function buildMeta(status) {
  const s = active;
  const now = Date.now();
  const stopTime = s.stopTime || (status === "stopped" ? now : null);
  return {
    id: s.id,
    label: s.label,
    status,
    session_start_time: s.startTime,          // epoch ms — acuan sync video+trajectory
    started_at: new Date(s.startTime).toISOString(),
    stopped_at: stopTime ? new Date(stopTime).toISOString() : null,
    duration_ms: stopTime ? stopTime - s.startTime : now - s.startTime,
    max_record_ms: MAX_RECORD_MS,
    stop_reason: s.stopReason || null,
    trajectory_samples: s.telemCount,
    command_samples: s.cmdCount,
    cameras: Object.keys(s.cams).map((role) => ({
      role,
      url: s.cams[role].url,
      frames: s.cams[role].frames,
      bytes: s.cams[role].bytes,
    })),
  };
}

function writeMeta(status) {
  const meta = buildMeta(status);
  try {
    fs.writeFileSync(path.join(active.dir, "meta.json"), JSON.stringify(meta, null, 2));
  } catch {}
  return meta;
}

function summary() {
  return active ? buildMeta("recording") : null;
}

// Status ringkas untuk dikirim ke dashboard.
function status() {
  if (!active) return { recording: false };
  return {
    recording: true,
    id: active.id,
    session_start_time: active.startTime,
    duration_ms: Date.now() - active.startTime,
    max_record_ms: MAX_RECORD_MS,
    cameras: Object.keys(active.cams),
  };
}

// Daftar seluruh sesi rekaman (baca meta.json tiap folder).
function listSessions() {
  ensureRecordingsDir();
  let entries;
  try {
    entries = fs.readdirSync(RECORDINGS_DIR, { withFileTypes: true });
  } catch {
    return [];
  }
  const out = [];
  for (const e of entries) {
    if (!e.isDirectory() || !isSafeId(e.name)) continue;
    const metaPath = path.join(RECORDINGS_DIR, e.name, "meta.json");
    try {
      const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
      const totalBytes = (meta.cameras || []).reduce((a, c) => a + (c.bytes || 0), 0);
      out.push({
        id: meta.id || e.name,
        label: meta.label || "",
        status: meta.status || "unknown",
        session_start_time: meta.session_start_time || null,
        started_at: meta.started_at || null,
        duration_ms: meta.duration_ms || 0,
        trajectory_samples: meta.trajectory_samples || 0,
        cameras: (meta.cameras || []).map((c) => c.role),
        bytes: totalBytes,
      });
    } catch {
      // folder tanpa meta valid → lewati (mis. rekaman yang crash di tengah)
    }
  }
  // terbaru dulu
  out.sort((a, b) => (b.session_start_time || 0) - (a.session_start_time || 0));
  return out;
}

/* ------------------------------------------------------------------ */
/* akses file untuk HTTP playback                                      */
/* ------------------------------------------------------------------ */

// Path file dalam folder sesi, dengan guard traversal. null bila tidak aman/ada.
function resolveSessionFile(id, name) {
  if (!isSafeId(id)) return null;
  if (typeof name !== "string" || name.includes("/") || name.includes("\\") || name.includes("..")) {
    return null;
  }
  const p = path.join(RECORDINGS_DIR, id, name);
  if (!p.startsWith(RECORDINGS_DIR + path.sep)) return null;
  return p;
}

// Cache index frame per (id/role) agar seek saat scrubbing tidak baca-ulang file.
const _indexCache = new Map();
function loadFrameIndex(id, role) {
  if (!isSafeId(id) || !VALID_ROLES.has(role)) return null;
  const key = `${id}/${role}`;
  const file = path.join(RECORDINGS_DIR, id, `${role}.index.jsonl`);
  let stat;
  try { stat = fs.statSync(file); } catch { return null; }
  const cached = _indexCache.get(key);
  if (cached && cached.size === stat.size && cached.mtimeMs === stat.mtimeMs) {
    return cached.frames;
  }
  const frames = [];
  try {
    const raw = fs.readFileSync(file, "utf8");
    for (const line of raw.split("\n")) {
      if (!line.trim()) continue;
      const o = JSON.parse(line);
      if (Number.isFinite(o.off) && Number.isFinite(o.len)) frames.push(o);
    }
  } catch {
    return null;
  }
  _indexCache.set(key, { size: stat.size, mtimeMs: stat.mtimeMs, frames });
  return frames;
}

// Ambil satu frame JPEG (Buffer) dari sesi. cb(err, buf).
function getFrame(id, role, index, cb) {
  const frames = loadFrameIndex(id, role);
  if (!frames || !frames.length) return cb(new Error("index frame tidak ada"));
  let i = Number(index);
  if (!Number.isInteger(i)) i = 0;
  i = Math.max(0, Math.min(frames.length - 1, i));
  const { off, len } = frames[i];
  const file = path.join(RECORDINGS_DIR, id, `${role}.mjpeg`);
  fs.open(file, "r", (err, fd) => {
    if (err) return cb(err);
    const buf = Buffer.alloc(len);
    fs.read(fd, buf, 0, len, off, (e2, read) => {
      fs.close(fd, () => {});
      if (e2) return cb(e2);
      cb(null, read === len ? buf : buf.slice(0, read));
    });
  });
}

module.exports = {
  RECORDINGS_DIR,
  MAX_RECORD_MS,
  MAX_RECORD_MIN,
  isRecording,
  startSession,
  stopSession,
  onTelemetry,
  onCommand,
  status,
  listSessions,
  resolveSessionFile,
  getFrame,
  // diekspos untuk unit test
  _makeSessionId: makeSessionId,
  _isSafeId: isSafeId,
};
