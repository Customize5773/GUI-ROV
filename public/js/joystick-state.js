import { shapeAxis } from "./axis-shaping.js";

/* Default mapping dimuat dari joystick-defaults.json — SATU sumber kebenaran
   yang dipakai bersama server.js (lihat require() di server/server.js), supaya
   tabel default tidak lagi disalin di dua tempat dan bisa hanyut.

   Top-level await: modul ini kecil dan same-origin, dan app.js memang harus
   punya mapping sebelum polling gamepad dimulai. Kalau fetch gagal (file
   hilang / dibuka lewat file://), kita jatuh ke konfigurasi KOSONG yang aman —
   tidak ada axis dan tidak ada tombol yang aktif — bukan ke tebakan mapping. */

const EMPTY_BUTTON_LAYER = () =>
  Array.from({ length: 17 }, (_, i) => ({
    action: "no_function",
    button: i,
    mode: "toggle",
  }));

const SAFE_FALLBACK = {
  actions: ["no_function"],
  actionMigration: {},
  profile: {
    enabled: true,
    shiftButton: 10,
    tuning: { deadzone: 0.12, expo: 0.35, slewPerSec: 4000, gainIndex: 1 },
    axisConfig: [],
    buttonConfig: { regular: EMPTY_BUTTON_LAYER(), shift: EMPTY_BUTTON_LAYER() },
  },
};

let DEFAULTS = SAFE_FALLBACK;
try {
  const res = await fetch(new URL("./joystick-defaults.json", import.meta.url));
  if (res.ok) DEFAULTS = await res.json();
  else console.warn("[joystick] joystick-defaults.json tidak terbaca:", res.status);
} catch (err) {
  console.warn("[joystick] gagal memuat joystick-defaults.json:", err.message);
}

export const BUTTON_ACTIONS = DEFAULTS.actions.slice();

/* Profil tersimpan milik operator bisa memuat aksi dari versi lama (mis.
   actuator1_inc, mount_tilt_up) yang sudah tidak punya handler atau hardware.
   Dipetakan ulang saat dimuat supaya operator tidak perlu menyunting ulang
   halaman Joystick, dan supaya tidak ada tombol yang diam-diam mati. */
export function migrateButtonAction(action) {
  const mapped = DEFAULTS.actionMigration[action] || action;
  return BUTTON_ACTIONS.includes(mapped) ? mapped : "no_function";
}

function cloneDefaultProfile() {
  return structuredClone(DEFAULTS.profile);
}

export const joystickState = {
  enabled: true,
  connected: false,
  controllerName: "Unknown controller",

  /* Mapping baru dianggap sah hanya setelah server mengirimkannya (atau
     setelah default dimuat). Dipakai app.js untuk menahan kendali thruster
     sampai mapping benar-benar diketahui. */
  configLoaded: false,

  rawAxes: [],
  rawButtons: [],

  mapped: {
    surge: 0,
    sway: 0,
    yaw: 0,
    heave: 0,
    grip: 0,
  },

  ...cloneDefaultProfile(),
};

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

/* Sumber input bisa berupa axis fisik ("axis 2") atau sumber virtual.
   "triggers" = LT/RT pada F310. Pada X-Input, LT/RT BUKAN axis melainkan
   button analog 6 dan 7, jadi tidak akan pernah muncul di gp.axes. Nilainya
   disintesis di sini: RT positif (menutup gripper), LT negatif (membuka). */
export function readRawInput(inputName) {
  const name = String(inputName).trim().toLowerCase();

  if (name === "triggers") {
    const lt = Number(joystickState.rawButtons[6]?.value) || 0;
    const rt = Number(joystickState.rawButtons[7]?.value) || 0;
    return clamp(rt - lt, -1, 1);
  }

  const m = name.match(/axis\s+(\d+)/);
  if (!m) return 0;

  return clamp(Number(joystickState.rawAxes[Number(m[1])]) || 0, -1, 1);
}

function findConfigByAssigned(label) {
  return joystickState.axisConfig.find((row) => row.assigned === label);
}

/* Satu-satunya sumber kebenaran untuk mapping axis mentah -> nilai keluaran.
   Dipakai runtime (readAssignedAxis) maupun preview di halaman joystick,
   supaya angka yang dilihat operator persis sama dengan yang dikirim. */
/* Deadzone stik: di bawah ambang ini dianggap diam. Nilai di atas ambang
   di-rescale ke 0..1 supaya tidak ada lompatan di tepi deadzone. */
export const AXIS_DEADZONE = 0.12;

/* Expo: >1 membuat gerakan di sekitar tengah lebih halus tanpa mengurangi
   keluaran maksimum. Penting untuk sway, yang cuma punya satu thruster
   lateral sehingga input kasar terasa menyentak. */
const AXIS_EXPO = 1.6;

function applyDeadzoneExpo(v) {
  const mag = Math.abs(v);
  if (mag <= AXIS_DEADZONE) return 0;

  const scaled = (mag - AXIS_DEADZONE) / (1 - AXIS_DEADZONE);
  return Math.sign(v) * Math.pow(scaled, AXIS_EXPO);
}

export function mapAxisValue(raw, row) {
  let v = applyDeadzoneExpo(normalizeAxis(raw));

  // Reverse jika Min > Max
  if (Number(row.min) > Number(row.max)) {
    v *= -1;
  }

  const low = Math.min(Number(row.min), Number(row.max));
  const high = Math.max(Number(row.min), Number(row.max));

  return Math.round(low + ((v + 1) / 2) * (high - low));
}

/* Deadzone + expo diterapkan DI SINI, sebelum penskalaan min/max — satu titik
   untuk semua konsumen. Halaman Joystick memanggil fungsi yang SAMA untuk
   preview-nya, jadi angka yang dilihat operator dijamin persis sama dengan
   yang dikirim ke ROV. */
export function axisOutputFor(row) {
  if (!row) return 0;
  return mapAxisValue(shapeAxis(readRawInput(row.input), joystickState.tuning), row);
}

export function readAssignedAxis(label) {
  return axisOutputFor(findConfigByAssigned(label));
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

/* ========================= SAVE / LOAD CONFIG ========================= */

export function getJoystickConfigPayload() {
  return {
    enabled: joystickState.enabled,
    shiftButton: joystickState.shiftButton,
    tuning: { ...joystickState.tuning },

    axisConfig: joystickState.axisConfig.map((row) => ({
      input: row.input,
      assigned: row.assigned,
      min: row.min,
      max: row.max,
      direction: row.direction,
    })),

    buttonConfig: {
      regular: joystickState.buttonConfig.regular.map((row) => ({
        action: row.action,
        button: row.button,
        mode: row.mode,
      })),
      shift: joystickState.buttonConfig.shift.map((row) => ({
        action: row.action,
        button: row.button,
        mode: row.mode,
      })),
    },
  };
}

export function applyJoystickConfig(config) {
  if (!config || typeof config !== "object") return;

  const def = DEFAULTS.profile;

  joystickState.enabled = config.enabled !== false;

  if (Number.isInteger(Number(config.shiftButton))) {
    joystickState.shiftButton = Number(config.shiftButton);
  }

  /* ================= TUNING ================= */
  const t = (config.tuning && typeof config.tuning === "object") ? config.tuning : {};
  joystickState.tuning = {
    deadzone: clampNum(t.deadzone, 0, 0.9, def.tuning.deadzone),
    expo: clampNum(t.expo, 0, 1, def.tuning.expo),
    slewPerSec: clampNum(t.slewPerSec, 100, 20000, def.tuning.slewPerSec),
    gainIndex: Math.round(clampNum(t.gainIndex, 0, 20, def.tuning.gainIndex)),
  };

  /* ================= AXIS ================= */
  if (Array.isArray(config.axisConfig)) {
    joystickState.axisConfig = config.axisConfig.map((src, i) => {
      const fallback = def.axisConfig[i] || def.axisConfig[0] || {
        input: `axis ${i}`, assigned: "No function", min: -1000, max: 1000, direction: "↕",
      };
      return {
        input: typeof src?.input === "string" ? src.input : fallback.input,
        assigned: typeof src?.assigned === "string" ? src.assigned : fallback.assigned,
        min: Number.isFinite(Number(src?.min)) ? Number(src.min) : fallback.min,
        max: Number.isFinite(Number(src?.max)) ? Number(src.max) : fallback.max,
        direction: src?.direction === "↕" ? "↕" : "↔",
      };
    });
  }

  /* ================= BUTTON ================= */
  if (config.buttonConfig && typeof config.buttonConfig === "object") {
    for (const layerName of ["regular", "shift"]) {
      const srcLayer = config.buttonConfig[layerName];
      if (!Array.isArray(srcLayer)) continue;

      joystickState.buttonConfig[layerName] = srcLayer.map((src, i) => ({
        action: migrateButtonAction(typeof src?.action === "string" ? src.action : "no_function"),
        button: Number.isFinite(Number(src?.button)) ? Number(src.button) : i,
        mode: src?.mode === "hold" ? "hold" : "toggle",
      }));
    }
  }

  joystickState.configLoaded = true;
}

function clampNum(v, lo, hi, fallback) {
  const n = Number(v);
  return Number.isFinite(n) ? clamp(n, lo, hi) : fallback;
}

/* Pakai default sebagai titik awal supaya dashboard tetap bisa dikemudikan
   walau server belum sempat mengirim profil tersimpan. */
applyJoystickConfig(cloneDefaultProfile());

/* ========================= GAMEPAD POLLING ========================= */

export function updateJoystickStateFromGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const gp = Array.from(pads).find(Boolean);

  if (!gp) {
    joystickState.connected = false;
    joystickState.controllerName = "Unknown controller";
    joystickState.rawAxes = [];
    joystickState.rawButtons = [];
    joystickState.mapped = { surge: 0, sway: 0, yaw: 0, heave: 0, grip: 0 };
    return;
  }

  joystickState.connected = true;
  joystickState.controllerName = gp.id || "Unknown controller";
  joystickState.rawAxes = Array.from(gp.axes || []);
  joystickState.rawButtons = Array.from(gp.buttons || []).map((b) => ({
    pressed: !!b.pressed,
    value: Number(b.value || 0),
  }));

  joystickState.mapped.surge = readAssignedAxis("Axis X");
  joystickState.mapped.sway  = readAssignedAxis("Axis Y");
  joystickState.mapped.yaw   = readAssignedAxis("Axis R");
  joystickState.mapped.heave = readAssignedAxis("Axis Z");
  // "Grip" opsional: 0 bila tidak ada axis yang di-assign ke Grip.
  joystickState.mapped.grip  = readAssignedAxis("Grip");
}
