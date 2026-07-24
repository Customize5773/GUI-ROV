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
  "lights_brighter",
  "lights_dimmer",
  "gain_inc",
  "gain_dec",
];

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
    { action: "actuator1_inc", button: 8, mode: "hold" },
    { action: "actuator1_dec", button: 9, mode: "hold" },
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
  },

  axisConfig: [
    { input: "axis 0", assigned: "Axis X", min: -1000, max: 1000, direction: "↔" },
    { input: "axis 1", assigned: "Axis Y", min: 1000, max: -1000, direction: "↕" },
    { input: "axis 2", assigned: "Axis R", min: -1000, max: 1000, direction: "↔" },
    { input: "axis 3", assigned: "Axis Z", min: 1000, max: -1000, direction: "↕" },
    { input: "axis 4", assigned: "No function", min: -1, max: 1, direction: "↕" },
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

function readAssignedAxis(label) {
  const row = findConfigByAssigned(label);
  if (!row) return 0;

  const idx = parseAxisIndex(row.input);
  if (idx < 0) return 0;

  let v = normalizeAxis(joystickState.rawAxes[idx] ?? 0);

  // Reverse jika Min > Max
  if (Number(row.min) > Number(row.max)) {
    v *= -1;
  }

  const outMin = Number(row.min);
  const outMax = Number(row.max);

  const low = Math.min(outMin, outMax);
  const high = Math.max(outMin, outMax);

  const mapped =
    low + ((v + 1) / 2) * (high - low);

  return Math.round(mapped);
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

        row.action = typeof src.action === "string" ? src.action : row.action;
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
    joystickState.mapped = { surge: 0, sway: 0, yaw: 0, heave: 0 };
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

  console.log("[MAPPED]", joystickState.mapped);
}