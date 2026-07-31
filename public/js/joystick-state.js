export const BUTTON_ACTIONS = [
  "no_function",
  "arm",
  "disarm",
  "mode_manual",
  "mode_stabilize",
  "mode_depth_hold",
  "input_hold_set",
  "mount_tilt_up",
  "mount_tilt_down",
  "mount_center",
  "actuator1_inc",
  "actuator1_dec",
  "grip_open",
  "grip_close",
  "lights_brighter",
  "lights_dimmer",
  "gain_inc",
  "gain_dec",
];

/* Profil lama memetakan tombol ke actuator1_inc/dec, yang tidak punya handler
   apa pun di sisi ROV (rov_agent.py) — praktis mati. Gripper memakai posisi
   tombol yang sama, jadi profil tersimpan dimigrasikan otomatis supaya
   operator tidak perlu menyunting ulang halaman Joystick. */
const ACTION_MIGRATION = {
  actuator1_inc: "grip_close",
  actuator1_dec: "grip_open",
};

export function migrateButtonAction(action) {
  return ACTION_MIGRATION[action] || action;
}

function defaultButtonLayer() {
  return [
    { action: "arm", button: 0, mode: "toggle" },
    { action: "disarm", button: 1, mode: "toggle" },
    { action: "mode_manual", button: 2, mode: "toggle" },
    { action: "mode_stabilize", button: 3, mode: "toggle" },
    { action: "mode_depth_hold", button: 4, mode: "toggle" },
    { action: "mount_tilt_up", button: 5, mode: "hold" },
    { action: "mount_tilt_down", button: 6, mode: "hold" },
    { action: "mount_center", button: 7, mode: "toggle" },
    { action: "grip_close", button: 8, mode: "toggle" },
    { action: "grip_open", button: 9, mode: "toggle" },
    { action: "lights_brighter", button: 10, mode: "hold" },
    { action: "lights_dimmer", button: 11, mode: "hold" },
    { action: "gain_inc", button: 12, mode: "hold" },
    { action: "gain_dec", button: 13, mode: "hold" },
    { action: "input_hold_set", button: 14, mode: "toggle" },
    { action: "no_function", button: 15, mode: "toggle" },
  ];
}

export const joystickState = {
  enabled: true,
  connected: false,
  controllerName: "Unknown controller",

  rawAxes: [],
  rawButtons: [],

  mapped: {
    surge: 0,
    sway: 0,
    yaw: 0,
    heave: 0,
    grip: 0,
  },

  axisConfig: [
    { input: "axis 0", assigned: "Axis X", min: -1000, max: 1000, direction: "↔" },
    { input: "axis 1", assigned: "Axis Y", min: 1000, max: -1000, direction: "↕" },
    { input: "axis 2", assigned: "Axis R", min: -1000, max: 1000, direction: "↔" },
    { input: "axis 3", assigned: "Axis Z", min: 1000, max: -1000, direction: "↕" },
    /* Axis 4 dibiarkan "No function": pada Logitech F310 axis ini sering
       tidak diekspos browser, dan axis 0-3 sudah dipakai thruster. Operator
       yang ingin grip analog cukup mengubah dropdown ini ke "Grip" —
       min/max sudah disiapkan pada skala -1000..1000. */
    { input: "axis 4", assigned: "No function", min: -1000, max: 1000, direction: "↕" },
  ],

  shiftButton: 5,

  buttonConfig: {
    regular: defaultButtonLayer(),
    shift: defaultButtonLayer(),
  },
};

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function parseAxisIndex(inputName) {
  const m = String(inputName).match(/axis\s+(\d+)/i);
  return m ? Number(m[1]) : -1;
}

function normalizeAxis(raw) {
  return clamp(Number(raw) || 0, -1, 1);
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

/* ========================= SAVE / LOAD CONFIG ========================= */

export function getJoystickConfigPayload() {
  return {
    enabled: joystickState.enabled,
    shiftButton: joystickState.shiftButton,

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

  joystickState.enabled = config.enabled !== false;

  if (Number.isInteger(config.shiftButton)) {
    joystickState.shiftButton = config.shiftButton;
  }

  if (Array.isArray(config.axisConfig)) {
    joystickState.axisConfig.forEach((row, i) => {
      const src = config.axisConfig[i];
      if (!src) return;

      row.input = typeof src.input === "string" ? src.input : row.input;
      row.assigned = typeof src.assigned === "string" ? src.assigned : row.assigned;
      row.min = Number.isFinite(Number(src.min)) ? Number(src.min) : row.min;
      row.max = Number.isFinite(Number(src.max)) ? Number(src.max) : row.max;
      row.direction = src.direction === "↕" ? "↕" : "↔";
    });
  }

  if (config.buttonConfig && typeof config.buttonConfig === "object") {
    for (const layerName of ["regular", "shift"]) {
      const srcLayer = config.buttonConfig[layerName];
      const dstLayer = joystickState.buttonConfig[layerName];

      if (!Array.isArray(srcLayer) || !Array.isArray(dstLayer)) continue;

      dstLayer.forEach((row, i) => {
        const src = srcLayer[i];
        if (!src) return;

        row.action = typeof src.action === "string"
          ? migrateButtonAction(src.action)
          : row.action;
        row.button = Number.isFinite(Number(src.button)) ? Number(src.button) : row.button;
        row.mode = src.mode === "hold" ? "hold" : "toggle";
      });
    }
  }
}

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