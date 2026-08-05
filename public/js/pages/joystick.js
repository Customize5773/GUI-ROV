// joystick.js — halaman konfigurasi joystick/gamepad

import {
  joystickState,
  updateJoystickStateFromGamepad,
  getJoystickConfigPayload,
  getAvailableButtonActions,
  getActiveButtonLayerName,
  getButtonPressed,
  getButtonValue,
  applyJoystickConfig,
  mapAxisValue,
  AXIS_OPTIONS,
  STANDARD_AXIS_COUNT,
  STANDARD_BUTTON_COUNT,
} from "../joystick-state.js";

import { BUTTON_MODES, axisLabel, buttonLabel } from "/shared/joystick-profile.js";

let root = null;
let rafId = null;

let visualMode = "visual"; // visual | table
let mappingLayer = "regular"; // regular | shift

/* F310 mode X (standard mapping) mengekspos 4 axis dan 17 tombol. Daftar lama
   menawarkan 5 axis dan 16 tombol: "axis 4" tidak pernah ada di perangkatnya,
   dan tombol Logitech (16) tidak bisa dipilih sama sekali. */
const BUTTON_OPTIONS = Array.from({ length: STANDARD_BUTTON_COUNT }, (_, i) => i);
const AXIS_INPUT_OPTIONS = Array.from({ length: STANDARD_AXIS_COUNT }, (_, i) => `axis ${i}`);
// BUTTON_MODES ada di shared/joystick-profile.js karena normalizeProfile() ikut
// memvalidasinya — diimpor di atas.

/* ===================== MODE BELAJAR ===================== */
/* Menebak nomor axis di dropdown adalah sumber kesalahan terbesar. Dengan mode
   belajar operator cukup menggerakkan input yang dimaksud. */
const LEARN_TIMEOUT_MS = 5000;
let learn = null; // { kind: "axis"|"button", index, baseline, until }

function startLearn(kind, index) {
  learn = {
    kind,
    index,
    until: performance.now() + LEARN_TIMEOUT_MS,
    baseline: {
      axes: joystickState.rawAxes.slice(),
      buttons: joystickState.rawButtons.map((b) => !!b.pressed),
    },
  };
  rerender();
}

function cancelLearn() {
  learn = null;
  rerender();
}

/* Dipanggil tiap frame dari tick(). Mengembalikan true kalau sesuatu berubah. */
function pollLearn() {
  if (!learn) return false;

  if (performance.now() > learn.until) {
    learn = null;
    return true;
  }

  if (learn.kind === "axis") {
    // Ambil axis dengan perubahan terbesar di atas ambang.
    let best = -1;
    let bestDelta = 0.5;
    joystickState.rawAxes.forEach((v, i) => {
      const d = Math.abs(Number(v) - Number(learn.baseline.axes[i] ?? 0));
      if (d > bestDelta) { bestDelta = d; best = i; }
    });
    if (best < 0) return false;

    const row = joystickState.axisConfig[learn.index];
    if (!row) { learn = null; return true; }

    row.input = `axis ${best}`;

    /* Arah ikut ditentukan dari tanda perubahan: kalau operator mendorong stik
       ke arah yang dia anggap "positif", reversal (min > max) menyesuaikan
       sendiri — tidak perlu menebak tombol ↔/↕ lagi. */
    const delta = Number(joystickState.rawAxes[best]) - Number(learn.baseline.axes[best] ?? 0);
    const lo = Math.min(row.min, row.max);
    const hi = Math.max(row.min, row.max);
    if (delta < 0) { row.min = hi; row.max = lo; } else { row.min = lo; row.max = hi; }

    learn = null;
    return true;
  }

  // kind === "button"
  const hit = joystickState.rawButtons.findIndex(
    (b, i) => b && b.pressed && !learn.baseline.buttons[i],
  );
  if (hit < 0) return false;

  const layer = joystickState.buttonConfig[mappingLayer];
  const row = layer && layer[learn.index];
  if (row) row.button = hit;

  learn = null;
  return true;
}

const ACTION_LABELS = {
  no_function: "No function",
  arm: "Arm",
  disarm: "Disarm",
  mode_manual: "Emergency stop",
  // Keduanya berujung di ALT_HOLD — lihat PILOT_MODE_MAP di shared/rov-modes.js.
  mode_stabilize: "Mode alt hold (stabilize+depth)",
  mode_depth_hold: "Mode alt hold (stabilize+depth)",
  mode_poshold: "Mode pos hold (alt hold + tahan heading)",
  mode_acro: "Mode acro (berisiko)",
  input_hold_set: "Input hold set",
  mount_tilt_up: "Mount tilt up",
  mount_tilt_down: "Mount tilt down",
  mount_center: "Mount center",
  actuator1_inc: "Actuator 1 inc",
  actuator1_dec: "Actuator 1 dec",
  grip_open: "Gripper open",
  grip_close: "Gripper close",
  lights_brighter: "Lights brighter",
  lights_dimmer: "Lights dimmer",
  // Nama wire-nya tetap gain_inc/gain_dec (kompatibilitas agent), tapi yang
  // digeser adalah setpoint kedalaman. `depth` positif ke bawah, jadi
  // gain_dec = naik ke permukaan.
  gain_inc: "Depth +0.05 m (turun)",
  gain_dec: "Depth −0.05 m (naik)",
};

const COCKPIT_LAYOUT = {
  regular: {
    left: [
      { action: "disarm", anchor: "l1" },
      { action: "mount_tilt_down", anchor: "l2" },
      { action: "gain_dec", anchor: "dpad_up" },
      { action: "mount_tilt_up", anchor: "dpad_left" },
      { action: "mount_tilt_down", anchor: "dpad_right" },
      { action: "gain_inc", anchor: "dpad_down" },
      { action: "lights_dimmer", anchor: "left_stick" },
      { action: "mount_center", anchor: "left_bottom" },
    ],
    center: { action: "no_function", anchor: "touchpad" },
    right: [
      { action: "arm", anchor: "r1" },
      { action: "mount_tilt_up", anchor: "r2" },
      { action: "actuator1_dec", anchor: "face_up" },
      { action: "mode_stabilize", anchor: "face_left" },
      { action: "mode_manual", anchor: "face_right" },
      { action: "mode_depth_hold", anchor: "face_down" },
      { action: "shift", anchor: "right_stick" },
      { action: "input_hold_set", anchor: "right_bottom" },
    ],
  },

  shift: {
    left: [
      { action: "disarm", anchor: "l1" },
      { action: "mount_tilt_down", anchor: "l2" },
      { action: "gain_dec", anchor: "dpad_up" },
      { action: "mount_tilt_up", anchor: "dpad_left" },
      { action: "mount_tilt_down", anchor: "dpad_right" },
      { action: "gain_inc", anchor: "dpad_down" },
      { action: "lights_dimmer", anchor: "left_stick" },
      { action: "mount_center", anchor: "left_bottom" },
    ],
    center: { action: "no_function", anchor: "touchpad" },
    right: [
      { action: "arm", anchor: "r1" },
      { action: "mount_tilt_up", anchor: "r2" },
      { action: "actuator1_dec", anchor: "face_up" },
      { action: "mode_stabilize", anchor: "face_left" },
      { action: "mode_manual", anchor: "face_right" },
      { action: "mode_depth_hold", anchor: "face_down" },
      { action: "shift", anchor: "right_stick" },
      { action: "input_hold_set", anchor: "right_bottom" },
    ],
  },
};

function esc(str) {
  return String(str ?? "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[m]));
}

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function normalizeAxis(v) {
  return clamp(Number(v) || 0, -1, 1);
}

function axisPercent(v) {
  return Math.round(normalizeAxis(v) * 100);
}

function axisBarStyle(v) {
  const p = axisPercent(v); // -100..100
  const width = Math.abs(p) / 2; // 0..50 (% dari track penuh)
  if (p >= 0) {
    return `left:50%;width:${width}%;`;
  }
  return `left:${50 - width}%;width:${width}%;`;
}

/* Preview memakai mapping yang sama persis dengan runtime (joystick-state.js),
   supaya angka di layar tidak pernah berbeda dari yang dikirim ke ROV. */
function axisPreviewValue(raw, row) {
  return mapAxisValue(raw, row);
}

/* Baris ke-N tidak selalu membaca axis fisik ke-N — indeksnya ada di row.input.
   Preview lama memakai posisi baris, jadi bar bergerak salah kolom begitu
   operator memindahkan sumbu. */
function parseAxisIdx(input) {
  const m = String(input).match(/axis\s+(\d+)/i);
  return m ? Number(m[1]) : -1;
}

function getAxisAssignedOptions(current) {
  return AXIS_OPTIONS.map((opt) => {
    const selected = opt === current ? "selected" : "";
    return `<option value="${esc(opt)}" ${selected}>${esc(opt)}</option>`;
  }).join("");
}

function getActionLabel(action) {
  return ACTION_LABELS[action] || action || "No function";
}

function getCurrentLayerConfig() {
  const layer = mappingLayer === "shift" ? "shift" : "regular";
  return joystickState.buttonConfig?.[layer] || [];
}

function getActionRow(layer, actionKey) {
  const rows = joystickState.buttonConfig?.[layer] || [];
  return rows.find((r) => r.action === actionKey) || null;
}

function getActionButtonText(layer, actionKey) {
  if (actionKey === "shift") {
    return `Button ${joystickState.shiftButton}`;
  }
  const row = getActionRow(layer, actionKey);
  if (!row || !Number.isInteger(row.button)) return "Unassigned";
  return `Button ${row.button}`;
}

function getActionPressed(layer, actionKey) {
  if (actionKey === "shift") {
    return getButtonPressed(joystickState.shiftButton);
  }
  const row = getActionRow(layer, actionKey);
  if (!row || !Number.isInteger(row.button)) return false;
  return getButtonPressed(row.button);
}

function getActionTesterValue(layer, actionKey) {
  if (actionKey === "shift") {
    return getButtonValue(joystickState.shiftButton);
  }
  const row = getActionRow(layer, actionKey);
  if (!row || !Number.isInteger(row.button)) return 0;
  return getButtonValue(row.button);
}

function buildButtonOptions(current) {
  return BUTTON_OPTIONS.map((n) => {
    const selected = n === current ? "selected" : "";
    // Label F310 (A/B/X/Y/LB/…) jauh lebih mudah dicocokkan operator
    // dibanding nomor mentah.
    return `<option value="${n}" ${selected}>${n} — ${esc(buttonLabel(n))}</option>`;
  }).join("");
}

function buildActionOptions(current) {
  const actions = getAvailableButtonActions();
  return actions.map((a) => {
    const selected = a === current ? "selected" : "";
    return `<option value="${esc(a)}" ${selected}>${esc(getActionLabel(a))}</option>`;
  }).join("");
}

function buildModeOptions(current) {
  return BUTTON_MODES.map((m) => {
    const selected = m === current ? "selected" : "";
    const label = m.charAt(0).toUpperCase() + m.slice(1);
    return `<option value="${m}" ${selected}>${label}</option>`;
  }).join("");
}

/* ========================= AXIS PANEL ========================= */

function renderAxisMappingPanel() {
  const rows = joystickState.axisConfig || [];

  return `
    <section class="joy-card joy-card--axis">
      <div class="joy-card__head">
        <div>
          <div class="joy-kicker">AXIS MAPPING</div>
          <h3 class="joy-card__title">Axis Input</h3>
        </div>
      </div>

      <div class="joy-axis-table-wrap">
        <table class="joy-axis-table">
          <thead>
            <tr>
              <th class="col-name">Input</th>
              <th class="col-preview">Preview</th>
              <th class="col-dir">Direction</th>
              <th class="col-min">Min</th>
              <th class="col-axis">Axis</th>
              <th class="col-max">Max</th>
              <th class="col-dz">Deadzone</th>
              <th class="col-expo">Expo</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((row, idx) => {
              const rawIdx = parseAxisIdx(row.input);
              const raw = joystickState.rawAxes[rawIdx] ?? 0;
              const val = axisPreviewValue(raw, row);
              const barStyle = axisBarStyle(val);
              const learning = learn && learn.kind === "axis" && learn.index === idx;

              return `
                <tr class="${learning ? "is-learning" : ""}">
                  <td class="joy-axis-name">
                    <select class="joy-select" data-axis-index="${idx}" data-field="input">
                      ${AXIS_INPUT_OPTIONS.map((opt) => `
                        <option value="${esc(opt)}" ${opt === row.input ? "selected" : ""}>
                          ${esc(opt)} — ${esc(axisLabel(parseAxisIdx(opt)))}
                        </option>`).join("")}
                    </select>
                    <button
                      class="joy-mini-btn joy-learn-btn ${learning ? "is-learning" : ""}"
                      data-action="learn-axis"
                      data-axis-index="${idx}"
                      type="button"
                    >${learning ? "Gerakkan…" : "Deteksi"}</button>
                  </td>

                  <td class="joy-axis-preview">
                    <div class="joy-axis-value">${val.toFixed(2)}</div>
                    <div class="joy-axis-track">
                      <div class="joy-axis-track__zero"></div>
                      <div class="joy-axis-bar" style="${barStyle}"></div>
                    </div>
                    <div class="joy-axis-sub">${(val * 1000).toFixed(2)}</div>
                  </td>

                  <td class="joy-axis-dir">
                    <button
                      class="joy-mini-btn"
                      data-action="toggle-axis-dir"
                      data-axis-index="${idx}"
                      type="button"
                    >${esc(row.direction)}</button>
                  </td>

                  <td>
                    <input
                      class="joy-input joy-input--num"
                      type="number"
                      value="${esc(row.min)}"
                      data-axis-index="${idx}"
                      data-field="min"
                    />
                  </td>

                  <td>
                    <select
                      class="joy-select"
                      data-axis-index="${idx}"
                      data-field="assigned"
                    >
                      ${getAxisAssignedOptions(row.assigned)}
                    </select>
                  </td>

                  <td>
                    <input
                      class="joy-input joy-input--num"
                      type="number"
                      value="${esc(row.max)}"
                      data-axis-index="${idx}"
                      data-field="max"
                    />
                  </td>

                  <td>
                    <input
                      class="joy-input joy-input--num"
                      type="number" step="0.01" min="0" max="0.9"
                      value="${esc(row.deadzone)}"
                      data-axis-index="${idx}"
                      data-field="deadzone"
                    />
                  </td>

                  <td>
                    <input
                      class="joy-input joy-input--num"
                      type="number" step="0.1" min="1" max="4"
                      value="${esc(row.expo)}"
                      data-axis-index="${idx}"
                      data-field="expo"
                    />
                  </td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

/* ========================= COCKPIT VISUAL ========================= */

function renderCockpitLabel(layer, item, side) {
  const actionKey = item.action;
  const label = actionKey === "shift" ? "Shift" : getActionLabel(actionKey);
  const btnText = getActionButtonText(layer, actionKey);
  const active = getActionPressed(layer, actionKey) ? "is-active" : "";
  const testerVal = getActionTesterValue(layer, actionKey);

  return `
    <div class="joy-vlabel joy-vlabel--${side} ${active}" data-action-key="${esc(actionKey)}">
      <div class="joy-vlabel__main">${esc(label)}</div>
      <div class="joy-vlabel__sub">
        ${esc(btnText)}
        ${testerVal > 0 ? `<span class="joy-vlabel__live">${testerVal.toFixed(2)}</span>` : ""}
      </div>
    </div>
  `;
}

function renderCockpitVisualPanel() {
  const layer = mappingLayer === "shift" ? "shift" : "regular";
  const layout = COCKPIT_LAYOUT[layer];
  const activeLayerRuntime = getActiveButtonLayerName();

  return `
    <section class="joy-card joy-card--visual">
      <div class="joy-visual-head">
        <div>
          <div class="joy-visual-title">${esc(joystickState.controllerName || "Unknown controller")}</div>
          <div class="joy-visual-sub">Layer aktif runtime: ${esc(activeLayerRuntime === "shift" ? "Shift" : "Regular")}</div>
        </div>

        <div class="joy-shift-box">
          <div class="joy-kicker">SHIFT BUTTON</div>
          <select id="joyShiftButtonSelect" class="joy-select joy-select--shift">
            ${BUTTON_OPTIONS.map((n) => `
              <option value="${n}" ${joystickState.shiftButton === n ? "selected" : ""}>Button ${n}</option>
            `).join("")}
          </select>
        </div>
      </div>

      <div class="joy-cockpit">
        <div class="joy-cockpit__col joy-cockpit__col--left">
          ${layout.left.map((item) => renderCockpitLabel(layer, item, "left")).join("")}
        </div>

        <div class="joy-cockpit__center">
          <div class="joy-center-label ${getActionPressed(layer, layout.center.action) ? "is-active" : ""}">
            ${esc(getActionLabel(layout.center.action))}
          </div>

          ${renderControllerSvg()}
        </div>

        <div class="joy-cockpit__col joy-cockpit__col--right">
          ${layout.right.map((item) => renderCockpitLabel(layer, item, "right")).join("")}
        </div>
      </div>
    </section>
  `;
}

function renderControllerSvg() {
  return `
    <div class="joy-controller">
      <svg class="joy-controller__svg" viewBox="0 0 820 420" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <!-- outline -->
        <path class="jc-stroke" d="
          M285 105
          C250 105 215 122 194 151
          C171 184 160 224 160 265
          C160 319 176 364 204 388
          C223 405 248 406 267 388
          C279 377 287 354 295 330
          C304 302 320 286 345 286
          H475
          C500 286 516 302 525 330
          C533 354 541 377 553 388
          C572 406 597 405 616 388
          C644 364 660 319 660 265
          C660 224 649 184 626 151
          C605 122 570 105 535 105
          H285 Z
        "/>

        <!-- touchpad -->
        <rect class="jc-stroke" x="330" y="128" rx="16" ry="16" width="160" height="82"/>

        <!-- left stick -->
        <circle class="jc-stroke" cx="310" cy="275" r="44"/>
        <circle class="jc-stroke" cx="310" cy="275" r="20"/>

        <!-- right stick -->
        <circle class="jc-stroke" cx="510" cy="275" r="44"/>
        <circle class="jc-stroke" cx="510" cy="275" r="20"/>

        <!-- dpad -->
        <rect class="jc-stroke" x="206" y="190" width="28" height="86" rx="10"/>
        <rect class="jc-stroke" x="177" y="219" width="86" height="28" rx="10"/>

        <!-- face buttons -->
        <circle class="jc-stroke" cx="603" cy="221" r="18"/>
        <circle class="jc-stroke" cx="563" cy="261" r="18"/>
        <circle class="jc-stroke" cx="643" cy="261" r="18"/>
        <circle class="jc-stroke" cx="603" cy="301" r="18"/>

        <!-- center / ps -->
        <circle class="jc-stroke" cx="410" cy="294" r="18"/>

        <!-- shoulder small -->
        <rect class="jc-stroke" x="236" y="140" width="18" height="34" rx="8"/>
        <rect class="jc-stroke" x="566" y="140" width="18" height="34" rx="8"/>

        <!-- helper center line -->
        <path class="jc-center" d="M410 230 L410 350"/>

        <!-- LEFT connectors -->
        <path class="jc-line" d="M252 145 L145 145" />
        <path class="jc-line" d="M236 170 L130 170" />
        <path class="jc-line" d="M206 205 L112 205" />
        <path class="jc-line" d="M177 236 L96 236" />
        <path class="jc-line" d="M263 236 L120 236" opacity=".0"/>
        <path class="jc-line" d="M206 265 L96 265" />
        <path class="jc-line" d="M267 292 L126 292" />
        <path class="jc-line" d="M285 350 L140 350" />

        <!-- RIGHT connectors -->
        <path class="jc-line" d="M568 145 L675 145" />
        <path class="jc-line" d="M584 170 L690 170" />
        <path class="jc-line" d="M603 205 L697 205" />
        <path class="jc-line" d="M563 236 L704 236" />
        <path class="jc-line" d="M643 265 L724 265" />
        <path class="jc-line" d="M603 301 L697 301" />
        <path class="jc-line" d="M554 314 L692 314" />
        <path class="jc-line" d="M535 350 L680 350" />

        <!-- center label line -->
        <path class="jc-line" d="M410 128 L410 70" />
      </svg>
    </div>
  `;
}

/* ========================= BUTTON TABLE ========================= */

function renderButtonMappingPanel() {
  const activeLayer = getActiveButtonLayerName();

  return `
    <section class="joy-card joy-card--buttons">
      <div class="joy-card__head joy-card__head--buttonmap">
        <div>
          <div class="joy-kicker">BUTTON MAPPING</div>
          <h3 class="joy-card__title">Regular / Shift</h3>
        </div>

        <div class="joy-shift-box joy-shift-box--inline">
          <div class="joy-kicker">SHIFT BUTTON</div>
          <select id="joyShiftButtonSelect" class="joy-select joy-select--shift">
            ${BUTTON_OPTIONS.map((n) => `
              <option value="${n}" ${joystickState.shiftButton === n ? "selected" : ""}>Button ${n}</option>
            `).join("")}
          </select>
        </div>
      </div>

      <div class="joy-button-content">
        <div class="joy-dual-grid">
          ${renderButtonTableForLayer("regular", activeLayer === "regular")}
          ${renderButtonTableForLayer("shift", activeLayer === "shift")}
        </div>
      </div>
    </section>
  `;
}

function renderButtonTableForLayer(layer, isRuntimeActive) {
  const rows = joystickState.buttonConfig?.[layer] || [];

  return `
    <div class="joy-layer-card ${isRuntimeActive ? "is-runtime-active" : "is-runtime-inactive"}">
      <div class="joy-layer-card__head">
        <div class="joy-layer-card__title">${layer === "shift" ? "SHIFT" : "REGULAR"}</div>
        <div class="joy-layer-card__state">
          ${isRuntimeActive ? "ACTIVE" : "STANDBY"}
        </div>
      </div>

      <div class="joy-table-card joy-table-card--layer">
        <table class="joy-button-table joy-button-table--compact">
          <thead>
            <tr>
              <th>Function</th>
              <th>Button</th>
              <th>Mode</th>
              <th>Tester</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((row, idx) => {
              const pressed = isRuntimeActive ? getButtonPressed(row.button) : false;
              const val = isRuntimeActive ? getButtonValue(row.button) : 0;

              return `
                <tr class="${pressed ? "is-active" : ""}">
                  <td>
                    <select
                      class="joy-select joy-select--compact"
                      data-btn-layer="${layer}"
                      data-btn-index="${idx}"
                      data-field="action"
                    >
                      ${buildActionOptions(row.action)}
                    </select>
                  </td>

                  <td>
                    <select
                      class="joy-select joy-select--compact joy-select--btn"
                      data-btn-layer="${layer}"
                      data-btn-index="${idx}"
                      data-field="button"
                    >
                      ${buildButtonOptions(row.button)}
                    </select>
                    ${isRuntimeActive ? `
                      <button
                        class="joy-mini-btn joy-learn-btn ${learn && learn.kind === "button" && learn.index === idx ? "is-learning" : ""}"
                        data-action="learn-button"
                        data-btn-index="${idx}"
                        type="button"
                      >${learn && learn.kind === "button" && learn.index === idx ? "Tekan…" : "Deteksi"}</button>` : ""}
                  </td>

                  <td>
                    <select
                      class="joy-select joy-select--compact joy-select--mode"
                      data-btn-layer="${layer}"
                      data-btn-index="${idx}"
                      data-field="mode"
                    >
                      ${buildModeOptions(row.mode)}
                    </select>
                  </td>

                  <td>
                    <div class="joy-tester ${pressed ? "is-active" : ""} ${isRuntimeActive ? "" : "is-dim"}">
                      <span class="joy-tester__lamp"></span>
                      <span class="joy-tester__text">
                        ${isRuntimeActive ? (pressed ? "Pressed" : "Idle") : "Standby"}
                        <small>${isRuntimeActive ? val.toFixed(2) : "0.00"}</small>
                      </span>
                    </div>
                  </td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

/* ========================= HEADER / LAYOUT ========================= */

function renderTopSummary() {
  const connected = !!joystickState.connected;
  const statusClass = connected ? "is-connected" : "is-disconnected";
  const statusText = connected ? "Connected" : "Disconnected";

  return `
    <div class="joy-page__head">
      <div>
        <span class="joy-kicker">INPUT DEVICE</span>
        <h2 class="joy-page__title">Joystick Configuration</h2>
        <p class="joy-page__desc">Mapping axis dan tombol gamepad ke kontrol ROV.</p>
      </div>

      <div class="joy-head-actions">
        <div class="joy-device-card ${statusClass}">
          <span class="joy-device-card__dot"></span>
          <div>
            <div class="joy-device-card__name">${esc(joystickState.controllerName || "Unknown controller")}</div>
            <div class="joy-device-card__sub">${statusText}</div>
          </div>
        </div>

        <button id="joySaveBtn" class="joy-save-btn" type="button">Save Mapping</button>
        <button id="joyExportBtn" class="joy-mini-btn" type="button">Export</button>
        <label class="joy-mini-btn joy-import-label">
          Import
          <input id="joyImportInput" type="file" accept="application/json,.json" hidden />
        </label>

        <label class="joy-enable-toggle">
          <input id="joyEnabledToggle" type="checkbox" ${joystickState.enabled ? "checked" : ""}/>
          <span>Enabled</span>
        </label>
      </div>
    </div>
  `;
}

/* Gate mode X. F310 di posisi D melaporkan mapping kosong dan indeks axis/tombol
   berbeda per OS — profil yang sama jadi salah tombol tanpa gejala yang jelas.
   Tombol override disediakan supaya operator tidak pernah terkunci total di
   tengah lomba kalau memakai gamepad lain. */
/* Isi banner dihitung ulang tiap frame lewat patchLiveJoystickUI, BUKAN sekali
   saat render: halaman ini hanya render sekali di init() (onShow tidak
   rerender), sedangkan status gamepad berubah kapan saja. */
function mappingBannerHtml() {
  if (!joystickState.connected || !joystickState.nonStandard) return "";

  if (joystickState.overrideNonStandard) {
    return `
      <div class="joy-banner joy-banner--warn">
        <strong>Mode non-standard dipakai paksa.</strong>
        Pemetaan tombol mungkin tidak sesuai profil. Indeks axis/tombol pada mode
        DirectInput berbeda antar sistem operasi.
      </div>`;
  }

  return `
    <div class="joy-banner joy-banner--err">
      <strong>Gamepad tidak dalam mode standard (XInput).</strong>
      Geser saklar di belakang Logitech F310 ke posisi <strong>X</strong>,
      lalu cabut &amp; pasang kembali kabel USB.
      Kontrol joystick dinonaktifkan sampai itu dilakukan.
      <button id="joyOverrideBtn" class="joy-mini-btn" type="button">Pakai apa adanya</button>
    </div>`;
}

function renderLayout() {
  return `
    <section class="panel joy-page page--joystick">
      ${renderTopSummary()}
      <div id="joyBannerSlot"></div>
      ${renderAxisMappingPanel()}
      ${renderButtonMappingPanel()}
    </section>
  `;
}

/* ========================= EVENTS ========================= */

function bindEvents() {
  if (!root) return;

  root.querySelector("#joySaveBtn")?.addEventListener("click", async () => {
    await saveMapping();
  });

  root.querySelector("#joyEnabledToggle")?.addEventListener("change", (e) => {
    joystickState.enabled = !!e.target.checked;
  });

  root.querySelectorAll("[data-action='toggle-axis-dir']").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.axisIndex);
      const row = joystickState.axisConfig[idx];
      if (!row) return;

      row.direction = row.direction === "↔" ? "↕" : "↔";

      const oldMin = row.min;
      row.min = row.max;
      row.max = oldMin;

      rerender();
    });
  });

  root.querySelectorAll("[data-axis-index][data-field]").forEach((el) => {
    el.addEventListener("change", (e) => {
      const idx = Number(el.dataset.axisIndex);
      const field = el.dataset.field;
      const row = joystickState.axisConfig[idx];
      if (!row) return;

      if (field === "assigned") {
        row.assigned = e.target.value;

        /* Satu fungsi hanya boleh dipegang satu axis: readAssignedAxis memakai
           kecocokan pertama, jadi duplikat akan mematikan axis kedua tanpa
           pesan apa pun. Bebaskan pemilik lama secara eksplisit. */
        if (row.assigned !== "No function") {
          joystickState.axisConfig.forEach((other, i) => {
            if (i !== idx && other.assigned === row.assigned) {
              other.assigned = "No function";
            }
          });
        }
        rerender();
        return;
      } else if (field === "input") {
        row.input = e.target.value;
        rerender();
        return;
      } else if (field === "min" || field === "max") {
        row[field] = Number(e.target.value);
      } else if (field === "deadzone" || field === "expo") {
        row[field] = Number(e.target.value);
      }
    });
  });

  root.querySelectorAll("[data-action='learn-axis']").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (learn) return cancelLearn();
      startLearn("axis", Number(btn.dataset.axisIndex));
    });
  });

  root.querySelectorAll("[data-action='learn-button']").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (learn) return cancelLearn();
      startLearn("button", Number(btn.dataset.btnIndex));
    });
  });

  root.querySelector("#joyExportBtn")?.addEventListener("click", exportProfile);
  root.querySelector("#joyImportInput")?.addEventListener("change", importProfile);

  /* Delegasi: tombol override hidup di dalam banner yang di-patch tiap frame,
     jadi tidak bisa di-bind langsung saat render. */
  root.addEventListener("click", (e) => {
    if (e.target && e.target.id === "joyOverrideBtn") {
      joystickState.overrideNonStandard = true;
      patchLiveJoystickUI();
    }
  });

  root.querySelectorAll("[data-joy-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      visualMode = btn.dataset.joyView === "table" ? "table" : "visual";
      rerender();
    });
  });

  root.querySelectorAll("[data-joy-layer]").forEach((btn) => {
    btn.addEventListener("click", () => {
      mappingLayer = btn.dataset.joyLayer === "shift" ? "shift" : "regular";
      rerender();
    });
  });

  root.querySelector("#joyShiftButtonSelect")?.addEventListener("change", (e) => {
    joystickState.shiftButton = Number(e.target.value);
    rerender();
  });

  root.querySelectorAll("[data-btn-layer][data-btn-index][data-field]").forEach((el) => {
    el.addEventListener("change", (e) => {
      const layer = el.dataset.btnLayer;
      const idx = Number(el.dataset.btnIndex);
      const field = el.dataset.field;

      const rows = joystickState.buttonConfig?.[layer];
      if (!rows || !rows[idx]) return;

      if (field === "action") {
        rows[idx].action = e.target.value;
      } else if (field === "button") {
        rows[idx].button = Number(e.target.value);
      } else if (field === "mode") {
        rows[idx].mode = BUTTON_MODES.includes(e.target.value) ? e.target.value : "toggle";
      }
    });
  });
}

/* ========================= SAVE ========================= */

/* Menunggu echo joystick_config dari server, bukan sekadar menampilkan
   "Saving...". Sebelumnya kalau WS putus, editan hilang tanpa operator sadar. */
let savePending = null;

function saveMapping() {
  try {
    const payload = getJoystickConfigPayload();

    if (!window.ws || window.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket tidak tersambung");
    }

    window.ws.send(JSON.stringify({
      type: "joystick_config_save",
      data: payload,
    }));

    flashSaveState("Menyimpan…");

    clearTimeout(savePending);
    savePending = setTimeout(() => {
      savePending = null;
      flashSaveState("Gagal: server tidak menjawab", true);
    }, 3000);
  } catch (err) {
    console.error("[joystick] save failed:", err);
    flashSaveState(`Gagal: ${err.message}`, true);
  }
}

/* ========================= EXPORT / IMPORT ========================= */

/* Inti dari "konsisten di berbagai perangkat": profil bisa dibawa sebagai file
   biasa antar laptop/OS tanpa konfigurasi ulang. */
function exportProfile() {
  const payload = getJoystickConfigPayload();
  const stamp = new Date().toISOString().slice(0, 10);
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);

  const a = document.createElement("a");
  a.href = url;
  a.download = `hydroship-joystick-${stamp}.json`;
  a.click();

  URL.revokeObjectURL(url);
  flashSaveState("Profil diekspor");
}

async function importProfile(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;

  try {
    const text = await file.text();
    // applyJoystickConfig menjalankan migrateProfile + sanitizeProfile, jadi
    // profil versi lama atau rusak tidak pernah masuk mentah ke state.
    const warnings = applyJoystickConfig(JSON.parse(text));

    rerender();

    const summary = warnings.length
      ? `Diimpor dengan ${warnings.length} koreksi:\n\n${warnings.join("\n")}\n\nSimpan sekarang?`
      : "Profil valid dan sudah dimuat.\n\nSimpan sekarang?";

    if (window.confirm(summary)) saveMapping();
  } catch (err) {
    console.error("[joystick] import failed:", err);
    flashSaveState(`Import gagal: ${err.message}`, true);
  } finally {
    e.target.value = "";
  }
}

function flashSaveState(text, isError = false) {
  const btn = root?.querySelector("#joySaveBtn");
  if (!btn) return;

  const old = btn.textContent;
  btn.textContent = text;
  btn.classList.toggle("is-error", !!isError);

  setTimeout(() => {
    btn.textContent = old || "Save Mapping";
    btn.classList.remove("is-error");
  }, 1400);
}

/* ========================= LIVE PATCH ========================= */

function patchLiveJoystickUI() {
  if (!root) return;

  // banner gate mode X — status gamepad bisa berubah kapan saja
  const bannerSlot = root.querySelector("#joyBannerSlot");
  if (bannerSlot) {
    const html = mappingBannerHtml();
    if (bannerSlot.innerHTML !== html) bannerSlot.innerHTML = html;
  }

  // device card
  const nameEl = root.querySelector(".joy-device-card__name");
  const subEl = root.querySelector(".joy-device-card__sub");
  const cardEl = root.querySelector(".joy-device-card");

  if (nameEl) {
    nameEl.textContent = joystickState.controllerName || "Unknown controller";
  }
  if (subEl) {
    subEl.textContent = joystickState.connected ? "Connected" : "Disconnected";
  }
  if (cardEl) {
    cardEl.classList.toggle("is-connected", !!joystickState.connected);
    cardEl.classList.toggle("is-disconnected", !joystickState.connected);
  }

  // axis preview live
  const axisRows = root.querySelectorAll(".joy-axis-table tbody tr");
  axisRows.forEach((tr, idx) => {
    const row = joystickState.axisConfig[idx];
    if (!row) return;

    // Indeks axis fisik ada di row.input, bukan posisi baris.
    const raw = joystickState.rawAxes[parseAxisIdx(row.input)] ?? 0;
    const mapped = axisPreviewValue(raw, row);
    const barStyle = axisBarStyle(raw);

    const valueEl = tr.querySelector(".joy-axis-value");
    const subValueEl = tr.querySelector(".joy-axis-sub");
    const barEl = tr.querySelector(".joy-axis-bar");

    if (valueEl) valueEl.textContent = Number(raw).toFixed(2);
    // `mappedValue` dulu tidak pernah dideklarasikan — ReferenceError tiap frame.
    if (subValueEl) subValueEl.textContent = mapped.toFixed(0);
    if (barEl) barEl.style.cssText = barStyle;
  });

  // layer state + tester live
  const activeLayer = getActiveButtonLayerName();
  const layerCards = root.querySelectorAll(".joy-layer-card");

  layerCards.forEach((card) => {
    const titleEl = card.querySelector(".joy-layer-card__title");
    const stateEl = card.querySelector(".joy-layer-card__state");
    if (!titleEl) return;

    const title = titleEl.textContent.trim().toUpperCase();
    const layer = title === "SHIFT" ? "shift" : "regular";
    const isRuntimeActive = activeLayer === layer;
    const rows = joystickState.buttonConfig?.[layer] || [];

    card.classList.toggle("is-runtime-active", isRuntimeActive);
    card.classList.toggle("is-runtime-inactive", !isRuntimeActive);

    if (stateEl) {
      stateEl.textContent = isRuntimeActive ? "ACTIVE" : "STANDBY";
    }

    const trs = card.querySelectorAll("tbody tr");
    trs.forEach((tr, idx) => {
      const row = rows[idx];
      if (!row) return;

      const pressed = isRuntimeActive ? getButtonPressed(row.button) : false;
      const val = isRuntimeActive ? getButtonValue(row.button) : 0;

      tr.classList.toggle("is-active", pressed);

      const tester = tr.querySelector(".joy-tester");
      if (tester) {
        tester.classList.toggle("is-active", pressed);
        tester.classList.toggle("is-dim", !isRuntimeActive);
      }

      const textEl = tr.querySelector(".joy-tester__text");
      if (textEl) {
        textEl.innerHTML = `
          ${isRuntimeActive ? (pressed ? "Pressed" : "Idle") : "Standby"}
          <small>${isRuntimeActive ? val.toFixed(2) : "0.00"}</small>
        `;
      }
    });
  });
}

/* ========================= RENDER LOOP ========================= */

function rerender() {
  if (!root) return;
  root.innerHTML = renderLayout();
  bindEvents();
}

function tick() {
  updateJoystickStateFromGamepad();

  // Mode belajar butuh render ulang begitu input terdeteksi.
  if (pollLearn()) rerender();
  else patchLiveJoystickUI();

  rafId = requestAnimationFrame(tick);
}

/* ========================= STYLES ========================= */

function injectJoystickStyles() {
  if (document.getElementById("joystick-page-styles")) return;

  const style = document.createElement("style");
  style.id = "joystick-page-styles";
  style.textContent = `
        .page--joystick,
    .page[data-page="joystick"]{
      height:100%;
      min-height:0;
      overflow:auto !important;
    }

    .page--joystick .grid,
    .page[data-page="joystick"] .grid{
      display:block !important;
      min-height:max-content;
    }

    .page--joystick .panel,
    .page[data-page="joystick"] .panel{
      min-height:max-content;
      overflow:visible !important;
    }

    .joy-page{
      display:flex;
      flex-direction:column;
      gap:14px;
      min-height:max-content;
      overflow:visible !important;
      padding-bottom:22px;
    }

    .joy-page__head{
      display:flex;
      justify-content:space-between;
      align-items:flex-start;
      gap:14px;
      flex-wrap:wrap;
      padding:0 2px;
    }

    .joy-page__title{
      margin:4px 0 4px;
      font-size:21px;
      line-height:1.08;
      color:#eaf3ff;
      font-weight:800;
      letter-spacing:-.02em;
    }

    .joy-page__desc{
      margin:0;
      color:#98b8cf;
      font-size:12px;
    }

    .joy-kicker{
      display:inline-block;
      font-size:10px;
      letter-spacing:.24em;
      text-transform:uppercase;
      color:#42d8ff;
      font-weight:800;
    }

    .joy-head-actions{
      display:flex;
      align-items:center;
      gap:10px;
      flex-wrap:wrap;
    }

    .joy-device-card{
      min-width:240px;
      display:flex;
      align-items:center;
      gap:12px;
      padding:12px 14px;
      border-radius:14px;
      border:1px solid rgba(66,216,255,.18);
      background:linear-gradient(180deg, rgba(18,37,63,.82), rgba(16,31,53,.86));
      box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
    }

    .joy-device-card__dot{
      width:12px;
      height:12px;
      border-radius:50%;
      background:#ff6f7b;
      box-shadow:0 0 12px rgba(255,111,123,.45);
      flex:0 0 auto;
    }

    .joy-device-card.is-connected .joy-device-card__dot{
      background:#65f18a;
      box-shadow:0 0 12px rgba(101,241,138,.45);
    }

    .joy-device-card__name{
      color:#eef5ff;
      font-weight:800;
      font-size:14px;
      line-height:1.2;
    }

    .joy-device-card__sub{
      color:#a9bfd4;
      font-size:12px;
      margin-top:2px;
    }

    .joy-save-btn{
      height:38px;
      padding:0 14px;
      border:none;
      border-radius:12px;
      cursor:pointer;
      background:linear-gradient(180deg, #1f8bd0, #186ca6);
      color:#f7fbff;
      font-weight:800;
      font-size:12px;
      box-shadow:0 8px 18px rgba(16,92,148,.22), inset 0 1px 0 rgba(255,255,255,.12);
    }

    .joy-save-btn:hover{filter:brightness(1.06)}
    .joy-save-btn.is-error{background:linear-gradient(180deg, #d24d63, #9f3245)}

    .joy-enable-toggle{
      display:flex;
      align-items:center;
      gap:8px;
      color:#e5efff;
      font-weight:700;
      font-size:12px;
      user-select:none;
    }

    .joy-enable-toggle input{
      width:16px;
      height:16px;
      accent-color:#22b0ff;
    }

    .joy-card{
  background:linear-gradient(180deg, rgba(24,43,69,.92), rgba(20,36,59,.95));
  border:1px solid rgba(80,142,198,.16);
  border-radius:16px;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
  overflow:visible;
}

    .joy-card__head{
      display:flex;
      justify-content:space-between;
      align-items:flex-start;
      gap:14px;
      padding:14px 16px 0;
      flex-wrap:wrap;
    }

    .joy-card__title{
      margin:4px 0 0;
      font-size:16px;
      line-height:1.1;
      color:#eef5ff;
      font-weight:800;
    }

    /* ===== axis table ===== */
    .joy-axis-table-wrap{
      padding:12px 0 0;
      overflow:auto;
    }

    .joy-axis-table{
      width:100%;
      border-collapse:collapse;
      min-width:820px;
    }

    .joy-axis-table thead th{
      text-align:left;
      color:#6fd8ff;
      font-size:11px;
      letter-spacing:.18em;
      text-transform:uppercase;
      font-weight:800;
      padding:10px 12px;
      border-top:1px solid rgba(93,141,184,.16);
      border-bottom:1px solid rgba(93,141,184,.16);
      background:rgba(8,18,31,.16);
    }

    .joy-axis-table tbody td{
      padding:10px 12px;
      border-bottom:1px solid rgba(93,141,184,.12);
      vertical-align:middle;
      color:#edf5ff;
    }

    .joy-axis-name{
      font-size:14px;
      font-weight:800;
      color:#eef5ff;
      white-space:nowrap;
    }

    .joy-axis-preview{
      min-width:200px;
    }

    .joy-axis-value{
      font-size:14px;
      font-weight:800;
      color:#eef5ff;
      margin-bottom:6px;
      text-align:center;
    }

    .joy-axis-sub{
      font-size:11px;
      color:#8fb1ca;
      margin-top:5px;
      text-align:center;
    }

    .joy-axis-track{
      position:relative;
      height:10px;
      border-radius:999px;
      background:rgba(7,18,31,.55);
      border:1px solid rgba(63,108,149,.24);
      overflow:hidden;
    }

    .joy-axis-track__zero{
      position:absolute;
      left:50%;
      top:0;
      bottom:0;
      width:2px;
      transform:translateX(-50%);
      background:rgba(210,235,255,.20);
    }

    .joy-axis-bar{
      position:absolute;
      top:1px;
      bottom:1px;
      border-radius:999px;
      background:linear-gradient(90deg, #2a9fe1, #3dd8ff);
      box-shadow:0 0 12px rgba(54,188,255,.18);
    }

    .joy-mini-btn{
      min-width:48px;
      height:36px;
      border-radius:12px;
      border:1px solid rgba(63,121,171,.30);
      background:rgba(17,34,56,.78);
      color:#8ad8ff;
      font-size:18px;
      cursor:pointer;
    }

    .joy-mini-btn:hover{filter:brightness(1.08)}

    .joy-input,
    .joy-select{
      width:100%;
      height:36px;
      border-radius:12px;
      border:1px solid rgba(63,121,171,.30);
      background:rgba(12,26,45,.86);
      color:#eef5ff;
      padding:0 12px;
      font-size:12px;
      outline:none;
    }

    .joy-input:focus,
    .joy-select:focus{
      border-color:rgba(74,188,255,.6);
      box-shadow:0 0 0 3px rgba(43,166,255,.14);
    }

    .joy-input--num{
      text-align:center;
      min-width:88px;
    }

    /* ===== button mapping ===== */
    .joy-card__head--buttonmap{
      padding-bottom:12px;
      border-bottom:1px solid rgba(93,141,184,.12);
    }

    .joy-tabs-wrap{
      display:flex;
      gap:10px;
      flex-wrap:wrap;
      align-items:center;
    }

    .joy-tabs{
      display:inline-flex;
      gap:6px;
      padding:4px;
      border-radius:12px;
      background:rgba(8,18,31,.22);
      border:1px solid rgba(70,117,157,.18);
    }

    .joy-tab{
      height:34px;
      padding:0 14px;
      border:none;
      border-radius:10px;
      background:transparent;
      color:#9eb7cb;
      font-size:11px;
      letter-spacing:.14em;
      font-weight:800;
      cursor:pointer;
      text-transform:uppercase;
    }

    .joy-tab.is-active{
      background:linear-gradient(180deg, rgba(40,157,224,.28), rgba(20,94,154,.28));
      color:#f2f8ff;
      box-shadow:inset 0 0 0 1px rgba(78,197,255,.22);
    }

    .joy-button-content{
      padding:12px 14px 14px;
      display:flex;
      flex-direction:column;
      gap:10px;
    }

    .joy-shift-box{
      min-width:150px;
      display:flex;
      flex-direction:column;
      gap:6px;
      align-items:flex-end;
    }

    .joy-shift-box--inline{
      min-width:150px;
      display:flex;
      flex-direction:column;
      gap:6px;
      align-items:flex-end;
    }

    .joy-select--shift{
      width:132px;
    }

    /* ===== dual regular / shift mapping ===== */
    .joy-dual-grid{
      display:grid;
      grid-template-columns:1fr 1fr;
      gap:12px;
      align-items:start;
    }

    .joy-layer-card{
  border:1px solid rgba(80,142,198,.14);
  border-radius:14px;
  background:rgba(8,18,31,.14);
  overflow:visible;
  position:relative;
  z-index:1;
}

    .joy-layer-card.is-runtime-active{
      border-color:rgba(72,186,255,.28);
      box-shadow:0 0 0 1px rgba(72,186,255,.08) inset;
    }

    .joy-layer-card.is-runtime-inactive{
      opacity:.92;
    }

    .joy-layer-card__head{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:10px;
      padding:10px 12px;
      border-bottom:1px solid rgba(93,141,184,.12);
      background:rgba(8,18,31,.16);
    }

    .joy-layer-card__title{
      font-size:12px;
      font-weight:800;
      letter-spacing:.18em;
      color:#eaf3ff;
    }

    .joy-layer-card__state{
      font-size:10px;
      font-weight:800;
      letter-spacing:.18em;
      color:#8fb1ca;
    }

    .joy-layer-card.is-runtime-active .joy-layer-card__state{
      color:#55dfff;
    }

    /* ===== button table ===== */
    .joy-table-card{
  border-radius:14px;
  border:1px solid rgba(80,142,198,.14);
  background:rgba(10,21,36,.18);
  overflow:visible;
  position:relative;
  z-index:2;
}

    .joy-table-card--compact{
      margin-top:0;
    }

    .joy-table-card--layer{
      border:none;
      border-radius:0;
      background:transparent;
    }

    .joy-button-table{
      width:100%;
      min-width:620px;
      border-collapse:collapse;
    }

    .joy-button-table thead th{
      text-align:left;
      color:#6fd8ff;
      font-size:10px;
      letter-spacing:.14em;
      text-transform:uppercase;
      font-weight:800;
      padding:8px 10px;
      border-bottom:1px solid rgba(93,141,184,.14);
      background:rgba(8,18,31,.18);
    }

    .joy-button-table tbody td{
      padding:6px 8px;
      border-bottom:1px solid rgba(93,141,184,.10);
      vertical-align:middle;
    }

    .joy-button-table tbody tr.is-active{
      background:rgba(36,115,176,.08);
    }

    .joy-button-table--compact{
      min-width:100%;
    }

    .joy-button-table--compact thead th{
      font-size:10px;
      letter-spacing:.14em;
      padding:8px 10px;
    }

    .joy-button-table--compact tbody td{
      padding:6px 8px;
    }

    .joy-select--compact{
      height:34px;
      font-size:12px;
      padding:0 10px;
      border-radius:10px;
    }

    .joy-select--btn{
      min-width:96px;
    }

    .joy-select--mode{
      min-width:88px;
    }

    .joy-button-table--compact .joy-select--btn{
      min-width:96px;
    }

    .joy-button-table--compact .joy-select--mode{
      min-width:88px;
    }

    .joy-tester{
      display:inline-flex;
      align-items:center;
      gap:8px;
      min-width:88px;
      padding:5px 8px;
      border-radius:10px;
      border:1px solid rgba(69,112,150,.18);
      background:rgba(8,18,31,.22);
      color:#a7bfd2;
      font-size:11px;
      font-weight:700;
    }

    .joy-tester__lamp{
      width:8px;
      height:8px;
      border-radius:50%;
      background:#5d7082;
      box-shadow:none;
      flex:0 0 auto;
    }

    .joy-tester small{
      display:block;
      font-size:10px;
      color:#84a3bd;
      margin-top:1px;
    }

    .joy-tester.is-active{
      color:#eff8ff;
      border-color:rgba(72,186,255,.34);
      background:rgba(26,94,145,.16);
    }

    .joy-tester.is-active .joy-tester__lamp{
      background:#4be0ff;
      box-shadow:0 0 12px rgba(75,224,255,.40);
    }

    .joy-tester.is-dim{
      opacity:.62;
    }

    /* ===== responsive ===== */
    @media (max-width: 1100px){
      .joy-dual-grid{
        grid-template-columns:1fr;
      }
    }

    @media (max-width: 780px){
      .joy-page__title{font-size:19px}
      .joy-device-card{min-width:unset;width:100%}
      .joy-head-actions{width:100%}
      .joy-save-btn{width:100%}
      .joy-tabs-wrap{width:100%}
      .joy-tabs{width:100%;justify-content:flex-start;overflow:auto}
    }

    .joy-button-table td,
.joy-button-table th{
  overflow:visible;
}

.joy-select{
  position:relative;
  z-index:5;
}
  `;

  document.head.appendChild(style);
}

/* ========================= PUBLIC API ========================= */

export const joystickPage = {
  init(container) {
    root = container;
    injectJoystickStyles();
    updateJoystickStateFromGamepad();
    rerender();
  },

  onShow() {
    if (!rafId) {
      tick();
    }
  },

  onHide() {
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  },

  onTelemetry(_data) {
    // tidak dipakai sekarang
  },
};

/* Satu-satunya jalur masuk config dari server. Sebelumnya fungsi ini mengganti
   array secara langsung TANPA validasi dan TANPA migrasi, sementara
   applyJoystickConfig() yang punya keduanya justru tidak pernah dipanggil. */
export function handleJoystickConfigMessage(msg) {
  if (!msg || typeof msg !== "object") return;

  const warnings = applyJoystickConfig(msg);
  warnings.forEach((w) => console.warn("[joystick]", w));

  // Save berhasil dikonfirmasi server.
  if (savePending) {
    clearTimeout(savePending);
    savePending = null;
    flashSaveState(warnings.length ? `Disimpan (${warnings.length} koreksi)` : "Tersimpan");
  }

  if (root) rerender();
}
