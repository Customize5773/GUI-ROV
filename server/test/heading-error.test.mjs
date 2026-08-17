/* Test wrap error heading sisi JS (shared/rov-heading.js) — padanan
 * TestHeadingError di test_rov_heading.py sisi Python.
 *
 * Yang dijaga di sini cuma satu hal, tapi hal itu gampang salah: `%` di JS
 * mempertahankan tanda operand kiri, jadi rumus Python `(err + 180) % 360 - 180`
 * TIDAK bisa disalin apa adanya. Kalau salah, heading bug melompat ke sisi
 * berlawanan tiap kali wahana melewati utara — persis saat pilot paling
 * butuh bug itu benar.
 *
 * ESM (.mjs) karena shared/rov-heading.js adalah ES module tanpa build step.
 */

import assert from "assert";
import { HEADING_DEADBAND_DEG, headingError } from "../../shared/rov-heading.js";

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

test("kasus sederhana tanpa lewat utara", () => {
  assert.strictEqual(headingError(100, 90), 10);
  assert.strictEqual(headingError(90, 100), -10);
  assert.strictEqual(headingError(90, 90), 0);
});

test("wrap melewati utara mengambil jalan terpendek", () => {
  // Inti modul ini, dan justru kasus yang gagal kalau `%` JS dipakai polos:
  // target 350°, heading 10° -> 20° berlawanan jarum jam (-20), bukan +340.
  assert.strictEqual(headingError(350, 10), -20);
  assert.strictEqual(headingError(10, 350), 20);
  assert.strictEqual(headingError(0, 359), 1);
  assert.strictEqual(headingError(359, 0), -1);
});

test("hasil selalu di dalam [-180, 180), sepadan dengan rov_heading.py", () => {
  // Python: (err + 180) % 360 - 180 -> 180 jatuh ke -180. Versi JS harus
  // sepakat, bukan mengembalikan +180, supaya tanda error tidak beda antara
  // yang digambar GUI dan yang dipakai Pi untuk mengoreksi.
  assert.strictEqual(headingError(180, 0), -180);
  assert.strictEqual(headingError(0, 180), -180);
  for (let t = 0; t < 360; t += 7) {
    for (let a = 0; a < 360; a += 11) {
      const err = headingError(t, a);
      assert.ok(err >= -180 && err < 180, `di luar rentang: ${t},${a} -> ${err}`);
    }
  }
});

test("argumen bukan angka -> null, bukan 0", () => {
  // Bedanya penting: 0 berarti "tepat di target" dan akan menggambar bug di
  // heading sekarang. null berarti "tidak ada setpoint" -> bug disembunyikan.
  assert.strictEqual(headingError(null, 90), null);
  assert.strictEqual(headingError(90, null), null);
  assert.strictEqual(headingError(undefined, 90), null);
  assert.strictEqual(headingError(NaN, 90), null);
});

test("deadband sepadan dengan rov_heading.py", () => {
  assert.strictEqual(HEADING_DEADBAND_DEG, 2.0);
});

let failed = 0;
for (const { name, fn } of tests) {
  try {
    fn();
    console.log(`  ok - ${name}`);
  } catch (err) {
    failed++;
    console.error(`  FAIL - ${name}`);
    console.error(`    ${err.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} test lolos (heading-error)`);
if (failed > 0) process.exit(1);
