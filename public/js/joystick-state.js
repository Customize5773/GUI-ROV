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

  joystickState.mapped.surge = readAssignedAxis("Axis X");
  joystickState.mapped.sway  = readAssignedAxis("Axis Y");
  joystickState.mapped.yaw   = readAssignedAxis("Axis R");
  joystickState.mapped.heave = readAssignedAxis("Axis Z");

  /* Grip analog: di standard mapping LT/RT adalah TOMBOL dengan nilai analog
     0..1, bukan axis — jadi baris "axis 4 -> Grip" di profil lama tidak pernah
     bisa terpicu di F310 mode X. RT membuka, LT menutup, hasilnya -1000..1000. */
  const open = getButtonValue(F310_TRIGGER_RIGHT);
  const close = getButtonValue(F310_TRIGGER_LEFT);
  joystickState.mapped.grip = Math.round((open - close) * 1000);
}