/* Test gating pilot mode sisi JS (shared/rov-modes.js) — padanan test_rov_modes.py
 * sisi Python. Mengetes konstanta murni yang dipakai public/js/app.js untuk
 * menyorot tab.
 *
 * ESM (.mjs) karena shared/rov-modes.js adalah ES module tanpa build step.
 */

import assert from "assert";
import {
  PILOT_MODE_MAP,
  ARDUSUB_MODE_TO_TAB,
  DEPTH_HOLD_MODES,
} from "../../shared/rov-modes.js";

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

test("PILOT_MODE_MAP memetakan nama GUI ke nama ArduSub yang benar", () => {
  assert.deepStrictEqual(PILOT_MODE_MAP, {
    manual: "MANUAL",
    stabilize: "STABILIZE",
    depth_hold: "ALT_HOLD",
    // Overlay heading-hold sisi Pi di atas ALT_HOLD, bukan mode POSHOLD
    // firmware (butuh EKF POSXY yang tidak ada di bawah air).
    poshold: "ALT_HOLD",
  });
});

test("poshold sengaja tidak punya entri balik di ARDUSUB_MODE_TO_TAB", () => {
  // ALT_HOLD bisa berarti dua tab (Alt Hold / Pos Hold) dan peta ini satu-arah.
  // Kalau ada yang menambahkan ARDUSUB_MODE_TO_TAB.ALT_HOLD = "poshold", tab
  // Alt Hold biasa tidak akan pernah menyala lagi — jadi entri baliknya HARUS
  // tetap "depth_hold", dan pemisahnya adalah flag `poshold` di telemetri
  // (lihat syncModeTabs di public/js/app.js).
  assert.strictEqual(ARDUSUB_MODE_TO_TAB.ALT_HOLD, "depth_hold");
  assert.strictEqual(Object.values(ARDUSUB_MODE_TO_TAB).includes("poshold"), false);
});

test("setiap tab punya mode ArduSub, termasuk STABILIZE", () => {
  for (const tab of Object.values(ARDUSUB_MODE_TO_TAB)) {
    assert.ok(tab in PILOT_MODE_MAP, `tab ${tab} tidak ada di PILOT_MODE_MAP`);
    assert.strictEqual(ARDUSUB_MODE_TO_TAB[PILOT_MODE_MAP[tab]], tab);
  }
  assert.strictEqual(ARDUSUB_MODE_TO_TAB.STABILIZE, "stabilize");
});

test("depth-set kini dipasangkan ke STABILIZE, bukan ALT_HOLD", () => {
  // STABILIZE tidak punya cascade PID kedalaman ArduSub, jadi bias depth-set
  // di sini jadi satu-satunya yang mendorong wahana ke setpoint.
  assert.strictEqual(DEPTH_HOLD_MODES.has("STABILIZE"), true);
  assert.strictEqual(DEPTH_HOLD_MODES.has("ALT_HOLD"), false);
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
