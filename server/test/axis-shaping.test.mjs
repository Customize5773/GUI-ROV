/* Unit test pembentukan respons stik. Pakai test runner bawaan Node
 * (`node --test server/test/`) supaya tidak menambah dependensi apa pun.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  applyDeadzone,
  applyExpo,
  shapeAxis,
  slewToward,
} from "../../public/js/axis-shaping.js";

const DZ = 0.12;

test("deadzone: di dalam zona keluarannya tepat nol", () => {
  for (const v of [0, 0.05, -0.05, DZ, -DZ]) {
    assert.equal(applyDeadzone(v, DZ), 0);
  }
});

test("deadzone: kontinu di tepi — tidak melompat", () => {
  // Tepat di luar ambang harus mendekati 0, bukan melompat ke DZ.
  assert.ok(Math.abs(applyDeadzone(DZ + 1e-6, DZ)) < 1e-5);
  assert.ok(Math.abs(applyDeadzone(-DZ - 1e-6, DZ)) < 1e-5);
});

test("deadzone: ujung tetap penuh ±1 (thrust maksimum tidak berkurang)", () => {
  assert.equal(applyDeadzone(1, DZ), 1);
  assert.equal(applyDeadzone(-1, DZ), -1);
});

test("deadzone: monoton naik", () => {
  let prev = -Infinity;
  for (let v = -1; v <= 1.0001; v += 0.01) {
    const out = applyDeadzone(v, DZ);
    assert.ok(out >= prev - 1e-9, `turun di v=${v}`);
    prev = out;
  }
});

test("deadzone: input di luar rentang & sampah di-clamp", () => {
  assert.equal(applyDeadzone(5, DZ), 1);
  assert.equal(applyDeadzone(-5, DZ), -1);
  assert.equal(applyDeadzone(NaN, DZ), 0);
  assert.equal(applyDeadzone(undefined, DZ), 0);
});

test("expo: 0 = linier, ujung selalu dipertahankan", () => {
  assert.equal(applyExpo(0.5, 0), 0.5);
  for (const e of [0, 0.35, 1]) {
    assert.equal(applyExpo(1, e), 1);
    assert.equal(applyExpo(-1, e), -1);
    assert.equal(applyExpo(0, e), 0);
  }
});

test("expo: melandaikan bagian tengah (itu gunanya untuk presisi)", () => {
  assert.ok(applyExpo(0.5, 0.6) < 0.5);
  assert.ok(applyExpo(-0.5, 0.6) > -0.5);
});

test("expo: monoton naik dan tidak pernah keluar ±1", () => {
  let prev = -Infinity;
  for (let v = -1; v <= 1.0001; v += 0.01) {
    const out = applyExpo(v, 0.35);
    assert.ok(out >= prev - 1e-9, `turun di v=${v}`);
    assert.ok(Math.abs(out) <= 1 + 1e-9);
    prev = out;
  }
});

test("shapeAxis: stik diam benar-benar nol (bukti anti-drift)", () => {
  assert.equal(shapeAxis(0.0, { deadzone: DZ, expo: 0.35 }), 0);
  assert.equal(shapeAxis(0.09, { deadzone: DZ, expo: 0.35 }), 0);
});

test("shapeAxis: defleksi penuh tetap penuh", () => {
  assert.equal(shapeAxis(1, { deadzone: DZ, expo: 0.35 }), 1);
  assert.equal(shapeAxis(-1, { deadzone: DZ, expo: 0.35 }), -1);
});

test("shapeAxis: tanpa opsi = passthrough", () => {
  assert.equal(shapeAxis(0.42), 0.42);
});

test("slew: tidak pernah melompat lebih dari maxPerSec * dt", () => {
  const out = slewToward(0, 1000, 4000, 0.033);
  assert.ok(out <= 4000 * 0.033 + 1e-9);
  assert.ok(out > 0);
});

test("slew: sampai persis di target kalau jaraknya muat", () => {
  assert.equal(slewToward(0, 100, 4000, 0.033), 100);
  assert.equal(slewToward(500, 500, 4000, 0.033), 500);
});

test("slew: bekerja dua arah", () => {
  assert.ok(slewToward(1000, -1000, 4000, 0.033) < 1000);
  assert.ok(slewToward(-1000, 1000, 4000, 0.033) > -1000);
});

test("slew: dt/rate nol = langsung ke target, jangan sampai macet", () => {
  // Kalau rate atau dt tidak masuk akal, menahan nilai lama justru berbahaya
  // (perintah netral bisa tidak pernah sampai). Lebih aman lompat ke target.
  assert.equal(slewToward(500, 0, 4000, 0), 0);
  assert.equal(slewToward(500, 0, 0, 0.033), 0);
});

test("slew: netralisasi darurat selalu bisa tercapai bertahap", () => {
  let v = 1000;
  for (let i = 0; i < 100; i++) v = slewToward(v, 0, 4000, 0.033);
  assert.equal(v, 0);
});
