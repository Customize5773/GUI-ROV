// Unit test manajer rekaman (fitur Replay). Fokus: logika start/stop, penulisan
// trajectory log, tap command, dan listing/akses sesi. Video stream di-mock
// (kami tidak membuka kamera nyata — cukup verifikasi manajemen file/sesi).
//
// Jalankan:  cd server && npm test        (atau: node test/recording.test.js)
// Tanpa dependency/framework tambahan — runner mini di bawah agar jalan di Node
// lama (server produksi memakai Node yang ada di laptop operator).

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

// Arahkan penyimpanan ke folder sementara SEBELUM require modul (dibaca saat load).
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "hydro-rec-"));
process.env.HYDROSHIP_RECORDINGS_DIR = TMP;

const recording = require("../recording");

/* ---------------- runner mini ---------------- */
const tests = [];
function test(name, fn) { tests.push({ name, fn }); }
function readJSONL(file) {
  return fs.readFileSync(file, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}
function cleanup() { if (recording.isRecording()) recording.stopSession("cleanup"); }

/* ---------------- kasus uji ---------------- */

test("startSession membuat folder + meta.json dengan session_start_time", () => {
  const meta = recording.startSession([], { label: "TIM-X" });
  assert.ok(meta.id, "punya id");
  assert.strictEqual(meta.status, "recording");
  assert.strictEqual(meta.label, "TIM-X");
  assert.ok(Number.isFinite(meta.session_start_time), "session_start_time epoch ms");

  const dir = path.join(TMP, meta.id);
  assert.ok(fs.existsSync(dir), "folder sesi dibuat");
  const onDisk = JSON.parse(fs.readFileSync(path.join(dir, "meta.json"), "utf8"));
  assert.strictEqual(onDisk.status, "recording");
  assert.strictEqual(onDisk.session_start_time, meta.session_start_time);
  recording.stopSession();
});

test("tidak bisa mulai dua sesi sekaligus", () => {
  recording.startSession([]);
  assert.throws(() => recording.startSession([]), /sudah berjalan/);
  recording.stopSession();
  assert.strictEqual(recording.isRecording(), false);
});

test("onTelemetry menulis trajectory.jsonl; field ternormalisasi", () => {
  const meta = recording.startSession([]);
  recording.onTelemetry({ heading: 90, depth: 0.5, roll: 2, pitch: -1 });
  recording.onTelemetry({ heading: "bad", depth: 0.6, roll: null });
  recording.stopSession();

  const rows = readJSONL(path.join(TMP, meta.id, "trajectory.jsonl"));
  assert.strictEqual(rows.length, 2);
  assert.strictEqual(rows[0].heading, 90);
  assert.strictEqual(rows[0].depth, 0.5);
  assert.strictEqual(rows[1].heading, null, "heading non-numerik → null");
  assert.strictEqual(rows[1].roll, null);
  assert.ok(rows.every((r) => Number.isFinite(r.t)), "tiap baris berstempel waktu");
});

test("onCommand hanya mencatat command relevan-trajectory", () => {
  const meta = recording.startSession([]);
  recording.onCommand("surge", 50);
  recording.onCommand("sway", -20);
  recording.onCommand("arm", true);       // TIDAK relevan → diabaikan
  recording.onCommand("light", true);     // TIDAK relevan → diabaikan
  recording.onCommand("control_mode", "manual");
  recording.stopSession();

  const rows = readJSONL(path.join(TMP, meta.id, "commands.jsonl"));
  assert.deepStrictEqual(rows.map((r) => r.name), ["surge", "sway", "control_mode"]);
  assert.strictEqual(rows[0].value, 50);
});

test("tap diabaikan saat tidak merekam (tanpa error)", () => {
  assert.strictEqual(recording.isRecording(), false);
  assert.doesNotThrow(() => {
    recording.onTelemetry({ heading: 10, depth: 1 });
    recording.onCommand("surge", 30);
  });
});

test("stopSession memfinalisasi meta (status stopped, durasi, jumlah sampel)", () => {
  const meta = recording.startSession([]);
  recording.onTelemetry({ heading: 1, depth: 0.1 });
  recording.onTelemetry({ heading: 2, depth: 0.2 });
  recording.onCommand("surge", 10);
  const finalMeta = recording.stopSession("manual");

  assert.strictEqual(finalMeta.status, "stopped");
  assert.strictEqual(finalMeta.stop_reason, "manual");
  assert.strictEqual(finalMeta.trajectory_samples, 2);
  assert.strictEqual(finalMeta.command_samples, 1);
  assert.ok(finalMeta.duration_ms >= 0);

  const onDisk = JSON.parse(fs.readFileSync(path.join(TMP, meta.id, "meta.json"), "utf8"));
  assert.strictEqual(onDisk.status, "stopped");
});

test("listSessions mengembalikan sesi (terbaru-dulu)", () => {
  const a = recording.startSession([]); recording.stopSession();
  const list = recording.listSessions();
  assert.ok(Array.isArray(list));
  assert.ok(list.find((s) => s.id === a.id), "sesi baru ada di daftar");
  for (let i = 1; i < list.length; i++) {
    assert.ok((list[i - 1].session_start_time || 0) >= (list[i].session_start_time || 0));
  }
});

test("resolveSessionFile menolak path traversal & id tak valid", () => {
  assert.strictEqual(recording.resolveSessionFile("../etc", "meta.json"), null);
  assert.strictEqual(recording.resolveSessionFile("abc", "../secret"), null);
  assert.strictEqual(recording.resolveSessionFile("abc", "sub/file"), null);
  const ok = recording.resolveSessionFile("2026-01-01_00-00-00", "meta.json");
  assert.ok(ok && ok.endsWith(path.join("2026-01-01_00-00-00", "meta.json")));
});

test("getFrame membaca slice JPEG dari mjpeg via index", (done) => {
  const id = "mockframes";
  const dir = path.join(TMP, id);
  fs.mkdirSync(dir, { recursive: true });
  const f0 = Buffer.from([0xff, 0xd8, 0x11, 0x22, 0xff, 0xd9]); // 6 byte
  const f1 = Buffer.from([0xff, 0xd8, 0x33, 0xff, 0xd9]);        // 5 byte
  fs.writeFileSync(path.join(dir, "bottom.mjpeg"), Buffer.concat([f0, f1]));
  fs.writeFileSync(
    path.join(dir, "bottom.index.jsonl"),
    JSON.stringify({ t: 1000, off: 0, len: f0.length }) + "\n" +
    JSON.stringify({ t: 1100, off: f0.length, len: f1.length }) + "\n"
  );

  recording.getFrame(id, "bottom", 1, (err, buf) => {
    assert.ifError(err);
    assert.deepStrictEqual([...buf], [...f1], "frame index 1 cocok");
    recording.getFrame(id, "bottom", 99, (e2, b2) => {   // di luar rentang → clamp
      assert.ifError(e2);
      assert.deepStrictEqual([...b2], [...f1]);
      done();
    });
  });
});

/* ---------------- eksekusi berurutan ---------------- */
(async function run() {
  let passed = 0, failed = 0;
  for (const { name, fn } of tests) {
    try {
      if (fn.length >= 1) {
        await new Promise((resolve, reject) => {
          let settled = false;
          const done = (e) => { if (settled) return; settled = true; e ? reject(e) : resolve(); };
          try { fn(done); } catch (e) { done(e); }
        });
      } else {
        fn();
      }
      cleanup();
      console.log(`  ✓ ${name}`);
      passed++;
    } catch (e) {
      cleanup();
      console.error(`  ✗ ${name}\n      ${e && e.message}`);
      failed++;
    }
  }
  try { fs.rmSync(TMP, { recursive: true, force: true }); } catch (e) {}
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
