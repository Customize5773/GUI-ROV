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
  ACTUAL_MODE_WARNINGS,
  ACRO_CONFIRM,
} from "../../shared/rov-modes.js";

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

test("PILOT_MODE_MAP memetakan nama GUI ke nama ArduSub yang benar", () => {
  assert.deepStrictEqual(PILOT_MODE_MAP, {
    // alias: tab STABILIZE sudah dihapus, tapi profil joystick lama masih
    // mengirim nama ini — lihat docstring rov_modes.py.
    stabilize: "ALT_HOLD",
    depth_hold: "ALT_HOLD",
    // Overlay heading-hold sisi Pi di atas ALT_HOLD, bukan mode POSHOLD
    // firmware (butuh EKF POSXY yang tidak ada di bawah air).
    poshold: "ALT_HOLD",
    acro: "ACRO",
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

test("setiap tab punya mode ArduSub, dan tidak ada tab untuk STABILIZE", () => {
  for (const tab of Object.values(ARDUSUB_MODE_TO_TAB)) {
    assert.ok(tab in PILOT_MODE_MAP, `tab ${tab} tidak ada di PILOT_MODE_MAP`);
    assert.strictEqual(ARDUSUB_MODE_TO_TAB[PILOT_MODE_MAP[tab]], tab);
  }
  // STABILIZE tidak boleh menyorot tab mana pun: ia menstabilkan attitude tapi
  // tidak menahan kedalaman, jadi menyamakannya dengan tab Alt Hold berbohong.
  assert.strictEqual(ARDUSUB_MODE_TO_TAB.STABILIZE, undefined);
});

test("STABILIZE punya peringatan meski bukan mode risky", () => {
  // Tidak bisa diminta dari GUI (jadi tidak perlu gerbang konfirmasi), tapi
  // wahana masih bisa berada di sana lewat saklar RC / GCS lain.
  assert.strictEqual(RISKY_ARDUSUB_MODES.has("STABILIZE"), false);
  assert.match(ACTUAL_MODE_WARNINGS.STABILIZE, /depth hold/i);
  assert.match(ACTUAL_MODE_WARNINGS.ACRO, /STABILISASI/i);
});

test("setiap mode risky punya pesan peringatan untuk ditampilkan", () => {
  for (const mode of RISKY_ARDUSUB_MODES) {
    assert.ok(ACTUAL_MODE_WARNINGS[mode], `${mode} tanpa pesan peringatan`);
  }
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

test("ACRO_CONFIRM menyebutkan tidak ada stabilisasi dan depth-set tidak menahan", () => {
  assert.match(ACRO_CONFIRM, /ACRO/);
  assert.match(ACRO_CONFIRM, /stabilisasi/i);
  assert.match(ACRO_CONFIRM, /[Dd]epth[- ](hold|set)/);
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
