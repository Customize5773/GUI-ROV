/* Test gating mode ACRO sisi JS (shared/rov-modes.js) — padanan test_rov_modes.py
 * sisi Python. Mengetes konstanta murni yang dipakai public/js/app.js untuk
 * menyorot tab, menampilkan badge peringatan, dan memicu dialog konfirmasi;
 * tidak menyentuh DOM/confirm() nyata karena app.js bukan module yang bisa
 * di-import berdiri sendiri di luar browser.
 *
 * ESM (.mjs) karena shared/rov-modes.js adalah ES module tanpa build step.
 */

import assert from "assert";
import {
  PILOT_MODE_MAP,
  ARDUSUB_MODE_TO_TAB,
  RISKY_ARDUSUB_MODES,
  ACRO_CONFIRM,
} from "../../shared/rov-modes.js";

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

test("PILOT_MODE_MAP memetakan keempat mode ke nama ArduSub yang benar", () => {
  assert.deepStrictEqual(PILOT_MODE_MAP, {
    manual: "MANUAL",
    stabilize: "STABILIZE",
    depth_hold: "ALT_HOLD",
    acro: "ACRO",
  });
});

test("ARDUSUB_MODE_TO_TAB adalah kebalikan PILOT_MODE_MAP", () => {
  for (const [tab, ardusub] of Object.entries(PILOT_MODE_MAP)) {
    assert.strictEqual(ARDUSUB_MODE_TO_TAB[ardusub], tab);
  }
  assert.strictEqual(Object.keys(ARDUSUB_MODE_TO_TAB).length, Object.keys(PILOT_MODE_MAP).length);
});

test("hanya ACRO yang ditandai risky", () => {
  assert.strictEqual(RISKY_ARDUSUB_MODES.has("ACRO"), true);
  for (const ardusub of Object.values(PILOT_MODE_MAP)) {
    if (ardusub === "ACRO") continue;
    assert.strictEqual(
      RISKY_ARDUSUB_MODES.has(ardusub),
      false,
      `${ardusub} seharusnya tidak risky`
    );
  }
});

test("ACRO_CONFIRM menyebutkan tidak ada stabilisasi dan depth hold nonaktif", () => {
  assert.match(ACRO_CONFIRM, /ACRO/);
  assert.match(ACRO_CONFIRM, /stabilisasi/i);
  assert.match(ACRO_CONFIRM, /[Dd]epth hold/);
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
console.log(`\n${tests.length - failed}/${tests.length} test lolos (mode-gating)`);
if (failed > 0) process.exit(1);
