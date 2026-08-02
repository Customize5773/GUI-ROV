// Unit test tabel param simulasi (halaman Vehicle tanpa hardware).
// Fokus: parsing dump QGroundControl, validasi tulis, dan streaming batch.
//
// Jalankan:  cd server && npm test        (atau: node test/sim-params.test.js)
// Tanpa dependency/framework tambahan — runner mini seperti recording.test.js.

const assert = require("assert");

const {
  SimParams,
  parseDump,
  normalizeName,
  BATCH_SIZE,
} = require("../sim-params");

/* ---------------- runner mini ---------------- */
const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

const DUMP = [
  "# Onboard parameters for Vehicle 1",
  "#",
  "# Vehicle-Id Component-Id Name Value Type",
  "1\t1\tACRO_EXPO\t0.300000011920928955\t9",
  "1\t1\tMOT_1_DIRECTION\t-1\t2",
  "1\t1\tBATT_CAPACITY\t3300\t6",
  "",
  "1\t1\tBARIS_RUSAK",                   // kolom kurang
  "1\t1\tPARAM_X\tbukan_angka\t9",       // nilai bukan angka
  "1\t1\tPARAM_Y\t1.0\t123",             // tipe tak dikenal
].join("\n");

/* ---------------- kasus uji ---------------- */

test("normalizeName menerima nama valid dan menormalkannya", () => {
  assert.strictEqual(normalizeName("MOT_1_DIRECTION"), "MOT_1_DIRECTION");
  assert.strictEqual(normalizeName("  mot_1_direction "), "MOT_1_DIRECTION");
  assert.strictEqual(normalizeName("FS_GCS_ENABLE"), "FS_GCS_ENABLE");
});

test("normalizeName menolak nama tidak valid", () => {
  // Padanan test_rov_params.TestNormalizeParamName di sisi Python — dua sisi
  // harus menolak hal yang sama, kalau tidak SIM meloloskan yang ditolak wahana.
  for (const bad of ["", "   ", null, undefined, 42, "MOT-1-DIR", "MOT 1", "1MOT",
                     "A".repeat(17), "MÖT_1"]) {
    assert.strictEqual(normalizeName(bad), null, `harusnya null: ${String(bad)}`);
  }
  assert.strictEqual(normalizeName("A".repeat(16)), "A".repeat(16), "16 karakter masih boleh");
});

test("parseDump membaca nama/nilai/tipe dan melewati baris rusak", () => {
  const m = parseDump(DUMP);
  assert.deepStrictEqual([...m.keys()], ["ACRO_EXPO", "MOT_1_DIRECTION", "BATT_CAPACITY"]);
  assert.ok(Math.abs(m.get("ACRO_EXPO").value - 0.3) < 1e-6);
  assert.strictEqual(m.get("ACRO_EXPO").ptype, 9);
  assert.strictEqual(m.get("MOT_1_DIRECTION").value, -1);
  assert.strictEqual(m.get("BATT_CAPACITY").ptype, 6);
});

test("dump asli dari Pixhawk bisa dibaca", () => {
  // Menjaga fixture nyata tetap terbaca — ini yang dipakai `npm run sim`.
  const p = SimParams.load();
  assert.ok(p.size > 900, `harusnya >900 param, dapat ${p.size}`);
  assert.ok(p.get("FRAME_CONFIG"), "FRAME_CONFIG ada");
  assert.ok(p.get("MOT_1_DIRECTION"), "MOT_1_DIRECTION ada");
  assert.ok(p.get("FS_GCS_ENABLE"), "FS_GCS_ENABLE ada");
});

test("get tidak peduli huruf besar/kecil, dan null untuk yang tak dikenal", () => {
  const p = new SimParams(parseDump(DUMP));
  assert.strictEqual(p.get("mot_1_direction").value, -1);
  assert.strictEqual(p.get("TIDAK_ADA"), null);
  assert.strictEqual(p.get("MOT-1-DIR"), null);
});

test("set menulis nilai dan mengembalikan entri barunya", () => {
  const p = new SimParams(parseDump(DUMP));
  const res = p.set("MOT_1_DIRECTION", 1);
  assert.strictEqual(res.ok, true);
  assert.strictEqual(res.entry.value, 1);
  assert.strictEqual(p.get("MOT_1_DIRECTION").value, 1);
});

test("set membulatkan tipe integer, tidak membulatkan REAL32", () => {
  // Perilaku ArduPilot yang penting diuji di GUI: nilai yang di-echo balik
  // belum tentu sama dengan yang diketik operator.
  const p = new SimParams(parseDump(DUMP));
  assert.strictEqual(p.set("MOT_1_DIRECTION", 2.7).entry.value, 3);
  assert.strictEqual(p.set("BATT_CAPACITY", 3300.4).entry.value, 3300);
  assert.strictEqual(p.set("ACRO_EXPO", 0.35).entry.value, 0.35);
});

test("set menolak param tak dikenal, bukan menambahkannya diam-diam", () => {
  const p = new SimParams(parseDump(DUMP));
  const res = p.set("NOPE_XYZ", 1);
  assert.strictEqual(res.ok, false);
  assert.match(res.reason, /tidak dikenal/);
  assert.strictEqual(p.get("NOPE_XYZ"), null);
  assert.strictEqual(p.size, 3, "jumlah param tidak berubah");
});

test("set menolak nama & nilai tidak valid", () => {
  const p = new SimParams(parseDump(DUMP));
  assert.strictEqual(p.set("MOT-1-DIR", 1).ok, false);
  assert.strictEqual(p.set("MOT_1_DIRECTION", "abc").ok, false);
  assert.strictEqual(p.set("MOT_1_DIRECTION", NaN).ok, false);
  assert.strictEqual(p.set("MOT_1_DIRECTION", Infinity).ok, false);
  assert.strictEqual(p.get("MOT_1_DIRECTION").value, -1, "nilai lama tidak tersentuh");
});

test("entryAt memberi index/count yang konsisten", () => {
  const p = new SimParams(parseDump(DUMP));
  assert.deepStrictEqual(p.entryAt(0), { name: "ACRO_EXPO", value: 0.30000001192092896, ptype: 9, index: 0, count: 3 });
  assert.strictEqual(p.entryAt(2).index, 2);
  assert.strictEqual(p.entryAt(2).count, 3);
});

test("streamAll mengirim seluruh param dalam batch dan menutup dengan done", (done) => {
  const p = SimParams.load();
  const batches = [];

  p.streamAll((msg) => {
    batches.push(msg);
    if (!msg.done) return;

    try {
      const all = batches.flatMap((b) => b.params);
      assert.strictEqual(all.length, p.size, "semua param terkirim");
      assert.strictEqual(new Set(all.map((e) => e.name)).size, p.size, "tanpa duplikat");
      assert.strictEqual(batches.filter((b) => b.done).length, 1, "tepat satu batch done");
      assert.strictEqual(batches[batches.length - 1], msg, "done adalah batch terakhir");
      assert.ok(batches.length > 1, "dikirim bertahap, bukan sekaligus");
      assert.ok(batches.every((b) => b.params.length <= BATCH_SIZE), `batch <= ${BATCH_SIZE}`);

      // index harus berurutan 0..n-1 supaya progress bar GUI tidak melompat.
      assert.deepStrictEqual(all.map((e) => e.index), all.map((_, i) => i));
      assert.ok(all.every((e) => e.count === p.size), "count sama di semua entri");
      done();
    } catch (e) {
      done(e);
    }
  });
});

test("streamAll kedua membatalkan yang pertama lewat nilai kembaliannya", (done) => {
  const p = new SimParams(parseDump(DUMP));
  let n = 0;
  const cancel = p.streamAll(() => { n++; });
  cancel();
  setTimeout(() => {
    try {
      assert.strictEqual(n, 0, "batch tidak jadi dikirim setelah dibatalkan");
      done();
    } catch (e) { done(e); }
  }, 150);
});

/* ---------------- jalankan ---------------- */
(async () => {
  let passed = 0, failed = 0;
  for (const { name, fn } of tests) {
    try {
      if (fn.length >= 1) {
        await new Promise((resolve, reject) => {
          let settled = false;
          const done = (e) => { if (settled) return; settled = true; e ? reject(e) : resolve(); };
          const timer = setTimeout(() => done(new Error("timeout")), 5000);
          const wrapped = (e) => { clearTimeout(timer); done(e); };
          try { fn(wrapped); } catch (e) { wrapped(e); }
        });
      } else {
        fn();
      }
      console.log(`  ✓ ${name}`);
      passed++;
    } catch (e) {
      console.error(`  ✗ ${name}\n      ${e && e.message}`);
      failed++;
    }
  }
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
