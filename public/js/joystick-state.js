export const joystickState = {
  enabled: true,
  connected: false,
  controllerName: "Unknown controller",
  rawAxes: [],

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
};

export function getJoystickConfigPayload() {
  return {
    enabled: joystickState.enabled,
    axisConfig: joystickState.axisConfig.map((row) => ({
      input: row.input,
      assigned: row.assigned,
      min: row.min,
      max: row.max,
      direction: row.direction,
    })),
  };
}

export function applyJoystickConfig(config) {
  if (!config || typeof config !== "object") return;

  joystickState.enabled = config.enabled !== false;

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
}

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function parseAxisIndex(inputName) {
  const m = String(inputName).match(/axis\\s+(\\d+)/i);
  return m ? Number(m[1]) : -1;
}

function normalizeAxis(raw) {
  return clamp(Number(raw) || 0, -1, 1);
}

function findConfigByAssigned(label) {
  return joystickState.axisConfig.find(row => row.assigned === label);
}

function readAssignedAxis(label) {
  const row = findConfigByAssigned(label);
  if (!row) return 0;

  const idx = parseAxisIndex(row.input);
  if (idx < 0) return 0;

  let v = normalizeAxis(joystickState.rawAxes[idx] ?? 0);

  if (Number(row.min) > Number(row.max)) {
    v *= -1;
  }

  return v;
}

export function updateJoystickStateFromGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const gp = Array.from(pads).find(Boolean);

  if (!gp) {
    joystickState.connected = false;
    joystickState.controllerName = "Unknown controller";
    joystickState.rawAxes = [];
    joystickState.mapped = { surge: 0, sway: 0, yaw: 0, heave: 0 };
    return;
  }

  joystickState.connected = true;
  joystickState.controllerName = gp.id || "Unknown controller";
  joystickState.rawAxes = Array.from(gp.axes || []);

  joystickState.mapped.surge = readAssignedAxis("Axis Y");
  joystickState.mapped.sway  = readAssignedAxis("Axis X");
  joystickState.mapped.yaw   = readAssignedAxis("Axis R");
  joystickState.mapped.heave = readAssignedAxis("Axis Z");
}