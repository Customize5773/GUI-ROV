/* Test profil joystick — skema (shared/joystick-profile.js) + I/O (joystick-config.js).
 *
 * Mengikuti runner tanpa-dependensi di recording.test.js: server hanya punya
 * `ws` sebagai dependensi dan itu sengaja dipertahankan.
 *
 * HYDROSHIP_CONFIG_DIR di-set SEBELUM require supaya test tidak pernah menyentuh
 * profil asli operator.
 */

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "hydroship-joycfg-"));
process.env.HYDROSHIP_CONFIG_DIR = TMP;

const joyConfig = require("../joystick-config");

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

(async () => {
  const S = await joyConfig.loadSchema();

  /* ===================== SKEMA ===================== */

  test("default profile lolos sanitize tanpa berubah (idempoten)", () => {
    const def = S.defaultProfile();
    const out = S.sanitizeProfile(def);
    assert.deepStrictEqual(out, def);
  });

  test("default profile memakai tata letak F310 mode X", () => {
    const def = S.defaultProfile();
    assert.strictEqual(def.version, S.SCHEMA_VERSION);
    assert.strictEqual(def.axisConfig.length, 4, "standard mapping = 4 axis");
    assert.strictEqual(def.device.buttons, 17, "standard mapping = 17 tombol");
    // LT/RT analog untuk gripper
    const grip = def.buttonConfig.regular.filter((r) => r.action.startsWith("grip_"));
    assert.deepStrictEqual(grip.map((r) => r.button).sort(), [6, 7]);
    assert.ok(grip.every((r) => r.mode === "hold"));
  });

  test("keempat DOF terpetakan tepat sekali", () => {
    const def = S.defaultProfile();
    const assigned = def.axisConfig.map((r) => r.assigned).sort();
    assert.deepStrictEqual(assigned, ["Axis R", "Axis X", "Axis Y", "Axis Z"]);
  });

  /* ===================== MIGRASI v1 -> v2 ===================== */

  test("profil v1 (tanpa version, actuator1_*) dimigrasikan", () => {
    const v1 = {
      enabled: true,
      shiftButton: 0,
      axisConfig: [
        { input: "axis 0", assigned: "Axis R", min: -1000, max: 1000, direction: "↔" },
      ],
      buttonConfig: {
        regular: [{ action: "actuator1_inc", button: 6, mode: "hold" }],
        shift: [],
      },
    };

    const warnings = [];
    const out = S.migrateProfile(v1, warnings);

    assert.strictEqual(out.version, S.SCHEMA_VERSION);
    assert.strictEqual(out.buttonConfig.regular[0].action, "grip_close",
      "actuator1_inc -> grip_close");
    assert.strictEqual(out.axisConfig[0].deadzone, S.DEFAULT_DEADZONE,
      "deadzone yang dulu hardcoded ikut terisi");
    assert.strictEqual(out.axisConfig[0].expo, S.DEFAULT_EXPO);
    assert.ok(warnings.some((w) => /v1/.test(w)));
  });

  test("profil v2 dimigrasikan ke tata letak D-pad depth (v3)", () => {
    const v2 = S.defaultProfile();
    v2.version = 2;
    v2.buttonConfig.regular = [
      // profil yang sudah disesuaikan operator: D-pad ↑/↓ dipakai aksi lain,
      // dan tidak ada binding kedalaman sama sekali
      { action: "input_hold_set", button: 12, mode: "toggle" },
      { action: "input_hold_set", button: 13, mode: "toggle" },
      { action: "no_function", button: 14, mode: "toggle" },
      { action: "no_function", button: 15, mode: "toggle" },
      { action: "arm", button: 0, mode: "toggle" },
    ];

    const warnings = [];
    const out = S.migrateProfile(v2, warnings);
    const rows = out.buttonConfig.regular;

    assert.deepStrictEqual(
      rows.slice(0, 4).map((r) => [r.action, r.button, r.mode]),
      [
        ["gain_dec", 12, "repeat"],        // ↑ = naik ke permukaan
        ["gain_inc", 13, "repeat"],        // ↓ = makin dalam
        ["mount_tilt_up", 14, "hold"],     // ←
        ["mount_tilt_down", 15, "hold"],   // →
      ],
      "kontrol kedalaman harus ADA, bukan hilang diam-diam"
    );
    assert.deepStrictEqual([rows[4].action, rows[4].button], ["arm", 0],
      "tombol di luar D-pad tidak disentuh");
    assert.ok(warnings.some((w) => /input_hold_set/.test(w)),
      "aksi yang tergusur harus dilaporkan, bukan hilang tanpa jejak");
  });

  /* ===================== VALIDASI ===================== */

  test("indeks tombol di luar jangkauan di-clamp", () => {
    const bad = S.defaultProfile();
    bad.buttonConfig.regular[0].button = 99;
    bad.buttonConfig.regular[1].button = -1;

    const out = S.sanitizeProfile(bad);
    assert.strictEqual(out.buttonConfig.regular[0].button, 16, "clamp ke tombol terakhir");
    assert.strictEqual(out.buttonConfig.regular[1].button, 0, "clamp ke 0");
  });

  test("axis dengan min === max ditolak (sumbu mati diam-diam)", () => {
    const bad = S.defaultProfile();
    bad.axisConfig[0].min = 500;
    bad.axisConfig[0].max = 500;

    const warnings = [];
    const out = S.sanitizeProfile(bad, warnings);
    assert.notStrictEqual(out.axisConfig[0].min, out.axisConfig[0].max);
    assert.ok(warnings.some((w) => /min = max/.test(w)));
  });

  test("assigned duplikat ditolak", () => {
    const bad = S.defaultProfile();
    bad.axisConfig[1].assigned = bad.axisConfig[0].assigned;

    const warnings = [];
    const out = S.sanitizeProfile(bad, warnings);
    assert.strictEqual(out.axisConfig[1].assigned, "No function");
    assert.ok(warnings.some((w) => /lebih dari sekali/.test(w)));
  });

  test("deadzone & expo di luar rentang di-clamp", () => {
    const bad = S.defaultProfile();
    bad.axisConfig[0].deadzone = 5;
    bad.axisConfig[0].expo = 99;

    const out = S.sanitizeProfile(bad);
    assert.strictEqual(out.axisConfig[0].deadzone, 0.9);
    assert.strictEqual(out.axisConfig[0].expo, 4);
  });

  test("tombol shift yang bentrok dengan aksi regular ditolak", () => {
    const bad = S.defaultProfile();
    bad.shiftButton = 0; // tombol A = arm

    const warnings = [];
    const out = S.sanitizeProfile(bad, warnings);
    assert.strictEqual(out.shiftButton, 4, "kembali ke LB");
    assert.ok(warnings.some((w) => /bentrok/.test(w)));
  });

  test("aksi tak dikenal dijadikan no_function", () => {
    const bad = S.defaultProfile();
    bad.buttonConfig.regular[0].action = "peluncur_rudal";
    const out = S.sanitizeProfile(bad);
    assert.strictEqual(out.buttonConfig.regular[0].action, "no_function");
  });

  test("input sampah tidak pernah melempar", () => {
    for (const junk of [null, undefined, 42, "halo", [], { axisConfig: "bukan array" }]) {
      const out = S.sanitizeProfile(junk);
      assert.strictEqual(out.version, S.SCHEMA_VERSION);
      assert.strictEqual(out.axisConfig.length, 4);
    }
  });

  /* ===================== PEMETAAN AXIS ===================== */

  test("mapAxisValue: netral 0, drift teredam, defleksi penuh ±1000", () => {
    const row = { min: -1000, max: 1000, deadzone: 0.12, expo: 1.6 };
    assert.strictEqual(S.mapAxisValue(0, row), 0);
    assert.strictEqual(S.mapAxisValue(0.08, row), 0, "drift di bawah deadzone");
    assert.strictEqual(S.mapAxisValue(-0.08, row), 0);
    assert.strictEqual(S.mapAxisValue(1, row), 1000);
    assert.strictEqual(S.mapAxisValue(-1, row), -1000);
  });

  test("mapAxisValue: min > max membalik arah", () => {
    const fwd = { min: -1000, max: 1000, deadzone: 0.12, expo: 1.6 };
    const rev = { min: 1000, max: -1000, deadzone: 0.12, expo: 1.6 };
    assert.strictEqual(S.mapAxisValue(1, rev), -S.mapAxisValue(1, fwd));
  });

  test("deadzone per-axis benar-benar dipakai", () => {
    const loose = { min: -1000, max: 1000, deadzone: 0.5, expo: 1 };
    const tight = { min: -1000, max: 1000, deadzone: 0.01, expo: 1 };
    assert.strictEqual(S.mapAxisValue(0.3, loose), 0, "0.3 masih di dalam deadzone 0.5");
    assert.ok(S.mapAxisValue(0.3, tight) > 0, "deadzone 0.01 meloloskannya");
  });

  /* ===================== PORTABILITAS (EXPORT/IMPORT) ===================== */

  test("round-trip export -> import identik", () => {
    const original = S.defaultProfile();
    const wire = JSON.parse(JSON.stringify(original));   // seperti lewat file
    const { profile } = S.loadProfile(wire);
    assert.deepStrictEqual(profile, original);
  });

  /* ===================== I/O ===================== */

  test("configPath mengikuti HYDROSHIP_CONFIG_DIR", () => {
    assert.strictEqual(joyConfig.configPath(), path.join(TMP, "joystick-profile.json"));
  });

  test("load membuat profil dan save menulis atomik", async () => {
    const cfg = await joyConfig.load();
    assert.strictEqual(cfg.version, S.SCHEMA_VERSION);
    assert.ok(fs.existsSync(joyConfig.configPath()));

    joyConfig.save(cfg);
    const files = fs.readdirSync(TMP);
    assert.ok(!files.some((f) => f.endsWith(".tmp")), "tidak ada .tmp tersisa");

    // file yang tertulis harus JSON valid dan setara
    const onDisk = JSON.parse(fs.readFileSync(joyConfig.configPath(), "utf8"));
    assert.deepStrictEqual(onDisk, cfg);
  });

  test("profil korup dikarantina, bukan dibiarkan gagal terus", async () => {
    fs.writeFileSync(joyConfig.configPath(), "{ ini bukan json", "utf8");

    const cfg = await joyConfig.load();
    assert.strictEqual(cfg.version, S.SCHEMA_VERSION, "tetap dapat profil yang bisa dipakai");

    const quarantined = fs.readdirSync(TMP).filter((f) => f.includes(".corrupt-"));
    assert.strictEqual(quarantined.length, 1, "file rusak dipindah, tidak hilang");
  });

  test("save menolak nilai jahat sebelum menyentuh disk", async () => {
    const evil = S.defaultProfile();
    evil.buttonConfig.regular[0].button = 9999;

    const { profile } = joyConfig.save(evil);
    assert.strictEqual(profile.buttonConfig.regular[0].button, 16);

    const onDisk = JSON.parse(fs.readFileSync(joyConfig.configPath(), "utf8"));
    assert.strictEqual(onDisk.buttonConfig.regular[0].button, 16);
  });

  /* ===================== RUNNER ===================== */

  let pass = 0;
  let fail = 0;

  for (const t of tests) {
    try {
      await t.fn();
      console.log(`  ok   ${t.name}`);
      pass++;
    } catch (err) {
      console.error(`  FAIL ${t.name}\n       ${err.message}`);
      fail++;
    }
  }

  fs.rmSync(TMP, { recursive: true, force: true });

  console.log(`\n  ${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})();
