/* State joystick runtime di browser.
 *
 * Skema (default, validasi, migrasi, pemetaan axis) TIDAK lagi didefinisikan di
 * sini — semuanya diimpor dari shared/joystick-profile.js yang juga dipakai
 * server. Dulu default & tabel migrasi ditulis kembar di dua file.
 */

import {
  AXIS_OPTIONS,
  BUTTON_ACTIONS,
  DEFAULT_DEADZONE,
  DEFAULT_EXPO,
  F310_TRIGGER_LEFT,
  F310_TRIGGER_RIGHT,
  STANDARD_AXIS_COUNT,
  STANDARD_BUTTON_COUNT,
  defaultProfile,
  isStandardMapping,
  loadProfile,
  mapAxisValue,
  migrateButtonAction,
  parseAxisIndex,
} from "/shared/joystick-profile.js";

import { slewToward } from "./axis-shaping.js";

export {
  AXIS_OPTIONS,
  BUTTON_ACTIONS,
  DEFAULT_DEADZONE,
  DEFAULT_EXPO,
  STANDARD_AXIS_COUNT,
  STANDARD_BUTTON_COUNT,
  mapAxisValue,
  migrateButtonAction,
  parseAxisIndex,
};

const base = defaultProfile();

export const joystickState = {
  enabled: base.enabled,
  connected: false,
  controllerName: "Unknown controller",

  /* Gate mode X: F310 di posisi D melaporkan mapping kosong dan indeksnya
     berbeda per OS, sehingga profil jadi salah tombol tanpa peringatan.
     pollGamepad() menolak mengirim axis selama nonStandard && !overrideNonStandard. */
  mapping: "",
  nonStandard: false,
  overrideNonStandard: false,

  rawAxes: [],
  rawButtons: [],

  mapped: {
    surge: 0,
    sway: 0,
    yaw: 0,
    heave: 0,
    grip: 0,
  },

  axisConfig: base.axisConfig,
  shiftButton: base.shiftButton,
  buttonConfig: base.buttonConfig,

  device: base.device,
};

/* Rate-limit axis: batas seberapa cepat perintah boleh berubah, dalam unit
   axis (skala ±1000) per detik.

   Sebelum ini TIDAK ADA rate-limit sama sekali di jalur kirim. axis-shaping.js
   punya slewToward() dan joystick-defaults.json punya "slewPerSec": 4000, tapi
   keduanya yatim — tidak ada satu pun pemanggil, jadi hentakan stik penuh
   sampai ke thruster dalam satu frame (16 ms).

   4000 = sekali slam dari netral ke penuh butuh 0,25 detik. HEAVE dibatasi
   lebih ketat karena dorongan vertikal adalah pengganggu attitude terbesar
   yang terukur (trial 21 Agu 2026: saat heave+, pitch -4,88 ± 5,13 — sd
   terburuk dari semua axis, di atas manuver rata-rata 4,69).

   1200 (bukan lagi 2000): trial 22 Agu 2026 mode ALT_HOLD, heave saturasi
   penuh (±1000) di 12,6% sampel [CMD] dan pilot minta gerak lebih pelan
   tanpa kehilangan respons. Slam neutral->penuh naik dari 0,5 s ke ~0,83 s;
   input kecil di dekat tengah tidak kena delay tambahan, cuma hentakan
   penuh yang diperlambat.

   INI KNOB KALIBRASI, bukan konstanta suci: naikkan kalau pilot merasa
   wahana jadi lamban merespons, turunkan kalau hentakan masih menggoyang
   attitude. Ukur hasilnya di tools/analyze_trial.py kolom sd per axis. */
const AXIS_SLEW_PER_SEC = 4000;
const HEAVE_SLEW_PER_SEC = 2000;

let slewLast = { surge: 0, sway: 0, yaw: 0, heave: 0 };
let slewStamp = 0;

/* Batasi laju perubahan satu axis. Matematika step-nya dipinjam dari
   slewToward() di axis-shaping.js (sudah ada + sudah diuji di
   server/test/axis-shaping.test.mjs) — sampai sekarang fungsi itu yatim tanpa
   pemanggil; di sinilah ia akhirnya dipakai.

   dt dihitung dari jam nyata, bukan diasumsikan 16 ms: pollGamepad ikut
   requestAnimationFrame, yang melambat saat tab tidak aktif atau GUI sibuk. */
function slew(name, target, dt) {
  const rate = name === "heave" ? HEAVE_SLEW_PER_SEC : AXIS_SLEW_PER_SEC;

  // Jeda panjang (tab baru bangun, frame pertama) -> lompat ke target. Menahan
  // ramp di sini justru mengirim perintah basi berdetik-detik.
  const next = dt > 0.5 ? target : slewToward(slewLast[name], target, rate, dt);

  slewLast[name] = next;
  return Math.round(next);
}

/* Buang riwayat slew. WAJIB dipanggil setiap kali axis dinetralkan di luar
   jalur joystick (E-Stop, pindah ke autonomous, koneksi putus): tanpa ini
   slewLast masih menyimpan defleksi penuh, dan begitu kendali kembali langkah
   pertama meramp TURUN dari nilai basi itu — E-Stop yang baru dilepas akan
   mengirim ~980 alih-alih 0. */
export function resetAxisSlew() {
  slewLast = { surge: 0, sway: 0, yaw: 0, heave: 0 };
  slewStamp = 0;
}

function findConfigByAssigned(label) {
  return joystickState.axisConfig.find((row) => row.assigned === label);
}

function readAssignedAxis(label) {
  const row = findConfigByAssigned(label);
  if (!row) return 0;

  const idx = parseAxisIndex(row.input);
  if (idx < 0) return 0;

  return mapAxisValue(joystickState.rawAxes[idx] ?? 0, row);
}

/* ========================= BUTTON HELPERS ========================= */

export function getAvailableButtonActions() {
  return BUTTON_ACTIONS.slice();
}

export function getButtonPressed(index) {
  const b = joystickState.rawButtons[index];
  if (!b) return false;
  return !!(b.pressed || Number(b.value) > 0.5);
}

export function getButtonValue(index) {
  const b = joystickState.rawButtons[index];
  if (!b) return 0;
  return Number(b.value || 0);
}

export function getActiveButtonLayerName() {
  return getButtonPressed(joystickState.shiftButton) ? "shift" : "regular";
}

/* Joystick boleh menggerakkan ROV hanya kalau mapping-nya standard (F310 mode X)
   atau operator sudah menekan "Pakai apa adanya". */
export function isJoystickUsable() {
  if (!joystickState.connected || !joystickState.enabled) return false;
  return !joystickState.nonStandard || joystickState.overrideNonStandard;
}

/* ========================= SAVE / LOAD CONFIG ========================= */

export function getJoystickConfigPayload() {
  return {
    version: 2,
    device: { ...joystickState.device },
    enabled: joystickState.enabled,
    shiftButton: joystickState.shiftButton,

    axisConfig: joystickState.axisConfig.map((row) => ({
      input: row.input,
      assigned: row.assigned,
      min: row.min,
      max: row.max,
      direction: row.direction,
      deadzone: row.deadzone,
      expo: row.expo,
    })),

    buttonConfig: {
      regular: joystickState.buttonConfig.regular.map((row) => ({ ...row })),
      shift: joystickState.buttonConfig.shift.map((row) => ({ ...row })),
    },
  };
}

/* Satu-satunya jalur masuk konfigurasi ke state — dari server, dari file import,
   maupun dari reset. Sebelumnya halaman joystick mengganti array secara langsung
   tanpa validasi dan tanpa migrasi, jadi profil lama/rusak bisa masuk mentah. */
export function applyJoystickConfig(config) {
  const { profile, warnings } = loadProfile(config);

  joystickState.enabled = profile.enabled;
  joystickState.shiftButton = profile.shiftButton;
  joystickState.axisConfig = profile.axisConfig;
  joystickState.buttonConfig = profile.buttonConfig;
  joystickState.device = profile.device;

  return warnings;
}

/* ========================= GAMEPAD POLLING ========================= */

export function updateJoystickStateFromGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const gp = Array.from(pads).find(Boolean);

  if (!gp) {
    joystickState.connected = false;
    joystickState.controllerName = "Unknown controller";
    joystickState.mapping = "";
    joystickState.nonStandard = false;
    joystickState.rawAxes = [];
    joystickState.rawButtons = [];
    joystickState.mapped = { surge: 0, sway: 0, yaw: 0, heave: 0, grip: 0 };
    slewLast = { surge: 0, sway: 0, yaw: 0, heave: 0 };
    slewStamp = 0;
    return;
  }

  joystickState.connected = true;
  joystickState.controllerName = gp.id || "Unknown controller";
  joystickState.mapping = gp.mapping || "";
  joystickState.nonStandard = !isStandardMapping(gp.mapping);

  joystickState.rawAxes = Array.from(gp.axes || []);
  joystickState.rawButtons = Array.from(gp.buttons || []).map((b) => ({
    pressed: !!b.pressed,
    value: Number(b.value || 0),
  }));

  const now = (typeof performance !== "undefined" ? performance.now() : Date.now());
  const dt = slewStamp ? (now - slewStamp) / 1000 : 0;
  slewStamp = now;

  joystickState.mapped.surge = slew("surge", readAssignedAxis("Axis X"), dt);
  joystickState.mapped.sway  = slew("sway",  readAssignedAxis("Axis Y"), dt);
  joystickState.mapped.yaw   = slew("yaw",   readAssignedAxis("Axis R"), dt);
  joystickState.mapped.heave = slew("heave", readAssignedAxis("Axis Z"), dt);

  /* Grip analog: di standard mapping LT/RT adalah TOMBOL dengan nilai analog
     0..1, bukan axis — jadi baris "axis 4 -> Grip" di profil lama tidak pernah
     bisa terpicu di F310 mode X. RT membuka, LT menutup, hasilnya -1000..1000. */
  const open = getButtonValue(F310_TRIGGER_RIGHT);
  const close = getButtonValue(F310_TRIGGER_LEFT);
  joystickState.mapped.grip = Math.round((open - close) * 1000);
}
