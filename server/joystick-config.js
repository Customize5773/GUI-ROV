/* Penyimpanan profil joystick — I/O saja, skema ada di shared/joystick-profile.js.
 *
 * Kenapa tidak lagi di server/config/:
 *   - direktori source ikut ter-commit, jadi profil operator bentrok saat git pull
 *   - folder instalasi bisa read-only
 *   - satu mesin dengan banyak user berbagi satu file
 *
 * Profil sekarang tinggal di direktori config milik OS. File di server/config/
 * tetap dipertahankan sebagai PROFIL PABRIK: dipakai sekali untuk seeding kalau
 * operator belum punya profil sendiri.
 *
 * Pola env override mengikuti recording.js (HYDROSHIP_RECORDINGS_DIR).
 */

const fs = require("fs");
const os = require("os");
const path = require("path");

const FILE_NAME = "joystick-profile.json";
const FACTORY_FILE = path.join(__dirname, "config", FILE_NAME);

function resolveConfigDir() {
  if (process.env.HYDROSHIP_CONFIG_DIR) return process.env.HYDROSHIP_CONFIG_DIR;

  if (process.platform === "win32") {
    return path.join(process.env.APPDATA || os.homedir(), "hydroship");
  }

  return path.join(
    process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"),
    "hydroship",
  );
}

function configPath() {
  return path.join(resolveConfigDir(), FILE_NAME);
}

function ensureConfigDir() {
  const dir = resolveConfigDir();
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  return dir;
}

/* shared/ adalah ES module dan server ini CommonJS. Node >=22.12 sebenarnya
   bisa require() ESM langsung, tapi modul ini justru harus tahan pindah mesin —
   await import() jalan di Node 14+ tanpa syarat. Di-cache supaya sekali muat. */
let schema = null;

async function loadSchema() {
  if (!schema) {
    schema = await import("../shared/joystick-profile.js");
  }
  return schema;
}

/* File korup dipindahkan, bukan dibiarkan. Perilaku lama membuat file rusak
   gagal parse setiap boot lalu ditimpa diam-diam saat save berikutnya —
   operator tidak pernah tahu setelannya hilang. */
function quarantineCorrupt(file, err) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const dest = file.replace(/\.json$/, `.corrupt-${stamp}.json`);
  try {
    fs.renameSync(file, dest);
    console.warn(`[JOYCFG] profil rusak (${err.message}) dipindah ke ${dest}`);
  } catch (e) {
    console.warn("[JOYCFG] profil rusak dan gagal dipindah:", e.message);
  }
}

function readJson(file) {
  const raw = fs.readFileSync(file, "utf8");
  return JSON.parse(raw);
}

/* Tulis atomik: tulis ke .tmp lalu rename. Rename dalam satu filesystem bersifat
   atomik di Windows maupun Linux, jadi crash saat menyimpan tidak pernah
   meninggalkan profil terpotong. */
function writeAtomic(file, obj) {
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), "utf8");
  fs.renameSync(tmp, file);
}

async function load() {
  const { defaultProfile, loadProfile } = await loadSchema();
  const file = configPath();

  // 1. Profil milik operator.
  if (fs.existsSync(file)) {
    try {
      const { profile, warnings } = loadProfile(readJson(file));
      warnings.forEach((w) => console.warn("[JOYCFG]", w));
      console.log("[JOYCFG] profil dimuat dari", file);
      return profile;
    } catch (err) {
      quarantineCorrupt(file, err);
    }
  }

  // 2. Seed dari profil pabrik yang ikut repo.
  if (fs.existsSync(FACTORY_FILE)) {
    try {
      const { profile, warnings } = loadProfile(readJson(FACTORY_FILE));
      warnings.forEach((w) => console.warn("[JOYCFG]", w));
      save(profile);
      console.log("[JOYCFG] profil pabrik disalin ke", file);
      return profile;
    } catch (err) {
      console.warn("[JOYCFG] profil pabrik tidak terbaca:", err.message);
    }
  }

  // 3. Default bawaan kode.
  const cfg = defaultProfile();
  save(cfg);
  console.log("[JOYCFG] profil belum ada, dibuat default di", file);
  return cfg;
}

/* Sinkron supaya handler WS bisa langsung membalas hasilnya. sanitize dipanggil
   lewat schema yang sudah ter-cache oleh load() saat boot. */
function save(data) {
  if (!schema) throw new Error("skema joystick belum dimuat");

  const warnings = [];
  const cfg = schema.sanitizeProfile(data, warnings);
  warnings.forEach((w) => console.warn("[JOYCFG]", w));

  ensureConfigDir();
  writeAtomic(configPath(), cfg);
  console.log("[JOYCFG] profil disimpan ke", configPath());

  return { profile: cfg, warnings };
}

module.exports = { configPath, resolveConfigDir, load, save, loadSchema };
