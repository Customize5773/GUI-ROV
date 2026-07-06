import { joystickState } from "../joystick-state.js";

let root = null;
let axisLoopStarted = false;

const AXIS_OPTIONS = [
  "No function",
  "Axis X",
  "Axis Y",
  "Axis Z",
  "Axis R",
  "Axis S",
  "Axis T",
];

const axisConfig = joystickState.axisConfig;

const axisRows = [
  { name: "axis 0" },
  { name: "axis 1" },
  { name: "axis 2" },
  { name: "axis 3" },
  { name: "axis 4" },
];

export const joystickPage = {
  init(container) {
    root = container;

    root.innerHTML = `
      <main class="joypage">
        <section class="joypanel panel">
          <div class="joypanel__head">
            <div>
              <div class="panel__eyebrow">INPUT DEVICE</div>
              <h2 class="joypanel__title">Joystick Configuration</h2>
              <p class="joypanel__subtitle">
                Mapping axis gamepad ke kontrol ROV.
              </p>
            </div>

            <div class="joycontroller">
              <span class="joycontroller__dot"></span>
              <span class="joycontroller__name" id="joyControllerNameTable">
                Unknown controller
              </span>
            </div>
          </div>

          <div class="joytable__wrap">
            <table class="joytable__table">
              <thead>
                <tr>
                  <th class="col-name">Name</th>
                  <th class="col-preview">Preview</th>
                  <th class="col-dir">Direction</th>
                  <th class="col-min">Min</th>
                  <th class="col-axis">Axis</th>
                  <th class="col-max">Max</th>
                </tr>
              </thead>

              <tbody>
                ${axisRows
                  .map(
                    (row, index) => `
                  <tr data-axis-row="${index}">
                    <td class="col-name">
                      <span class="joyrow__name">${row.name}</span>
                    </td>

                    <td class="col-preview">
                      <div class="joypreview">
                        <div class="joypreview__value" id="joyPreviewMain-${index}">
                          0.00
                        </div>

                        <div class="joypreview__bar">
                          <span class="joypreview__zero"></span>
                          <span
                            class="joypreview__fill joypreview__fill--neg"
                            id="joyPreviewFillNeg-${index}"
                          ></span>
                          <span
                            class="joypreview__fill joypreview__fill--pos"
                            id="joyPreviewFillPos-${index}"
                          ></span>
                        </div>

                        <div class="joypreview__sub" id="joyPreviewSub-${index}">
                          0.00
                        </div>
                      </div>
                    </td>

                    <td class="col-dir">
                      <button
                        class="joydirBtn"
                        type="button"
                        data-dir-btn="${index}"
                        title="Ubah arah"
                      >
                        ${axisConfig[index]?.direction ?? "↔"}
                      </button>
                    </td>

                    <td class="col-min">
                      <input
                        class="joynum"
                        type="number"
                        value="${axisConfig[index]?.min ?? 0}"
                        data-min-input="${index}"
                      />
                    </td>

                    <td class="col-axis">
                      <div class="joyselectWrap">
                        <select class="joyselect" data-axis-select="${index}">
                          ${AXIS_OPTIONS.map(
                            (opt) => `
                              <option
                                value="${opt}"
                                ${opt === axisConfig[index]?.assigned ? "selected" : ""}
                              >
                                ${opt}
                              </option>
                            `
                          ).join("")}
                        </select>
                        <span class="joyselectArrow">▾</span>
                      </div>
                    </td>

                    <td class="col-max">
                      <input
                        class="joynum"
                        type="number"
                        value="${axisConfig[index]?.max ?? 0}"
                        data-max-input="${index}"
                      />
                    </td>
                  </tr>
                `
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    `;

    injectJoystickStyles();
    bindJoystickUI(root);
    updateControllerName(root);

    if (!axisLoopStarted) {
      axisLoopStarted = true;
      startAxisPreviewLoop();
    }
  },

  onShow() {
    updateControllerName(root);
  },

  onHide() {},

  onTelemetry(_data) {},
};

function bindJoystickUI(root) {
  const dirButtons = root.querySelectorAll("[data-dir-btn]");
  dirButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const index = Number(btn.dataset.dirBtn);
      if (Number.isNaN(index) || !axisConfig[index]) return;

      axisConfig[index].direction =
        axisConfig[index].direction === "↔" ? "↕" : "↔";

      btn.textContent = axisConfig[index].direction;
    });
  });

  const axisSelects = root.querySelectorAll("[data-axis-select]");
  axisSelects.forEach((select) => {
    select.addEventListener("change", (e) => {
      const index = Number(select.dataset.axisSelect);
      const value = e.target.value;

      if (!Number.isNaN(index) && axisConfig[index]) {
        axisConfig[index].assigned = value;
        console.log(`[Joystick] ${axisConfig[index].input} -> ${value}`);
      }
    });
  });

  const minInputs = root.querySelectorAll("[data-min-input]");
  minInputs.forEach((input) => {
    input.addEventListener("change", (e) => {
      const index = Number(input.dataset.minInput);
      const value = Number(e.target.value);

      if (!Number.isNaN(index) && axisConfig[index]) {
        axisConfig[index].min = value;
      }
    });
  });

  const maxInputs = root.querySelectorAll("[data-max-input]");
  maxInputs.forEach((input) => {
    input.addEventListener("change", (e) => {
      const index = Number(input.dataset.maxInput);
      const value = Number(e.target.value);

      if (!Number.isNaN(index) && axisConfig[index]) {
        axisConfig[index].max = value;
      }
    });
  });

  window.addEventListener("gamepadconnected", () => updateControllerName(root));
  window.addEventListener("gamepaddisconnected", () => updateControllerName(root));
}

function updateControllerName(root) {
  if (!root) return;

  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const gp = Array.from(pads).find(Boolean);
  const name = gp ? gp.id : "Unknown controller";

  const el = root.querySelector("#joyControllerNameTable");
  if (el) el.textContent = name;
}

function startAxisPreviewLoop() {
  const loop = () => {
    if (root) {
      const pads = navigator.getGamepads ? navigator.getGamepads() : [];
      const gp = Array.from(pads).find(Boolean);

      axisRows.forEach((_, index) => {
        const main = root.querySelector(`#joyPreviewMain-${index}`);
        const sub = root.querySelector(`#joyPreviewSub-${index}`);
        const fillNeg = root.querySelector(`#joyPreviewFillNeg-${index}`);
        const fillPos = root.querySelector(`#joyPreviewFillPos-${index}`);

        if (!main || !sub || !fillNeg || !fillPos) return;

        let val = 0;
        if (gp && gp.axes && typeof gp.axes[index] === "number") {
          val = gp.axes[index];
        }

        val = Math.max(-1, Math.min(1, Number(val) || 0));

        const fixed = val.toFixed(2);
        const scaled = (val * 1000).toFixed(2);

        main.textContent = fixed;
        sub.textContent = scaled;

        const percent = Math.min(50, Math.abs(val) * 50);

        if (val > 0) {
          fillPos.style.width = `${percent}%`;
          fillNeg.style.width = "0%";
        } else if (val < 0) {
          fillNeg.style.width = `${percent}%`;
          fillPos.style.width = "0%";
        } else {
          fillNeg.style.width = "0%";
          fillPos.style.width = "0%";
        }
      });
    }

    requestAnimationFrame(loop);
  };

  requestAnimationFrame(loop);
}

function injectJoystickStyles() {
  if (document.getElementById("joystick-page-styles")) return;

  const style = document.createElement("style");
  style.id = "joystick-page-styles";
  style.textContent = `
    .joypage {
      width: 100%;
      min-height: 100%;
      padding: 0;
      box-sizing: border-box;
    }

    .joypanel {
      width: 100%;
      min-height: calc(100vh - 255px);
      box-sizing: border-box;
      padding: 18px 18px 22px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    .joypanel__head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 20px;
      flex-wrap: wrap;
      margin-bottom: 4px;
    }

    .joypanel__title {
      margin: 6px 0 6px;
      font-size: 24px;
      font-weight: 800;
      color: #f4f8ff;
      letter-spacing: 0.2px;
      line-height: 1.15;
    }

    .joypanel__subtitle {
      margin: 0;
      color: #9cb6cc;
      font-size: 14px;
      line-height: 1.45;
    }

    .joycontroller {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px 16px;
      min-width: 320px;
      max-width: 100%;
      border-radius: 14px;
      background: rgba(14, 34, 56, 0.55);
      border: 1px solid rgba(53, 111, 164, 0.35);
      color: #d7e7f5;
      font-size: 14px;
      font-weight: 600;
      box-sizing: border-box;
    }

    .joycontroller__dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #29d3ff;
      box-shadow: 0 0 12px rgba(41, 211, 255, 0.6);
      flex: 0 0 auto;
    }

    .joytable__wrap {
      width: 100%;
      flex: 1;
      overflow: auto;
      border: 1px solid rgba(39, 84, 126, 0.45);
      border-radius: 18px;
      background: rgba(9, 25, 42, 0.22);
    }

    .joytable__table {
      width: 100%;
      min-width: 1120px;
      border-collapse: collapse;
      table-layout: fixed;
      color: #eaf4ff;
      font-size: 13px;
    }

    .joytable__table thead th {
      padding: 15px 12px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: #73cfff;
      border-bottom: 1px solid rgba(39, 84, 126, 0.45);
      text-align: center;
      white-space: nowrap;
    }

    .joytable__table tbody td {
      padding: 14px 12px;
      border-bottom: 1px solid rgba(39, 84, 126, 0.28);
      vertical-align: middle;
      text-align: center;
    }

    .joytable__table tbody tr:last-child td {
      border-bottom: none;
    }

    .col-name {
      width: 12%;
      text-align: left !important;
    }

    .col-preview {
      width: 24%;
    }

    .col-dir {
      width: 10%;
    }

    .col-min {
      width: 14%;
    }

    .col-axis {
      width: 24%;
    }

    .col-max {
      width: 14%;
    }

    .joyrow__name {
      color: #f4f8ff;
      font-size: 14px;
      text-transform: lowercase;
      font-weight: 600;
      white-space: nowrap;
    }

    .joypreview {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
    }

    .joypreview__value {
      font-size: 15px;
      font-weight: 700;
      color: #f4f8ff;
      line-height: 1;
    }

    .joypreview__bar {
      position: relative;
      width: 100%;
      max-width: 180px;
      height: 10px;
      border-radius: 999px;
      background: rgba(7, 19, 33, 0.75);
      border: 1px solid rgba(45, 100, 155, 0.35);
      overflow: hidden;
    }

    .joypreview__zero {
      position: absolute;
      left: 50%;
      top: 0;
      transform: translateX(-50%);
      width: 2px;
      height: 100%;
      background: rgba(180, 220, 255, 0.7);
      box-shadow: 0 0 8px rgba(120, 200, 255, 0.35);
      z-index: 2;
    }

    .joypreview__fill {
      position: absolute;
      top: 0;
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #1aa8ff 0%, #3bd3ff 100%);
      box-shadow: 0 0 12px rgba(36, 186, 255, 0.45);
      transition: width 0.05s linear;
    }

    .joypreview__fill--neg {
      right: 50%;
      border-radius: 999px 0 0 999px;
    }

    .joypreview__fill--pos {
      left: 50%;
      border-radius: 0 999px 999px 0;
    }

    .joypreview__sub {
      font-size: 11px;
      color: #9cb6cc;
      line-height: 1;
    }

    .joydirBtn {
      width: 50px;
      height: 40px;
      border-radius: 12px;
      border: 1px solid rgba(53, 111, 164, 0.35);
      background: rgba(14, 34, 56, 0.55);
      color: #73cfff;
      font-size: 18px;
      cursor: pointer;
      transition: 0.18s ease;
    }

    .joydirBtn:hover {
      border-color: rgba(77, 169, 255, 0.5);
      color: #b9ebff;
    }

    .joynum {
      width: 100%;
      height: 40px;
      border-radius: 12px;
      border: 1px solid rgba(53, 111, 164, 0.35);
      background: rgba(14, 34, 56, 0.55);
      color: #f4f8ff;
      text-align: center;
      font-size: 14px;
      outline: none;
      padding: 0 10px;
      box-sizing: border-box;
    }

    .joynum:focus {
      border-color: rgba(77, 169, 255, 0.55);
      box-shadow: 0 0 0 2px rgba(24, 163, 255, 0.12);
    }

    .joynum::-webkit-outer-spin-button,
    .joynum::-webkit-inner-spin-button {
      -webkit-appearance: none;
      margin: 0;
    }

    .joynum[type="number"] {
      -moz-appearance: textfield;
    }

    .joyselectWrap {
      position: relative;
      width: 100%;
    }

    .joyselect {
      width: 100%;
      height: 40px;
      border-radius: 12px;
      border: 1px solid rgba(53, 111, 164, 0.35);
      background: rgba(14, 34, 56, 0.55);
      color: #f4f8ff;
      font-size: 14px;
      padding: 0 38px 0 14px;
      appearance: none;
      outline: none;
      cursor: pointer;
    }

    .joyselect:focus {
      border-color: rgba(77, 169, 255, 0.55);
      box-shadow: 0 0 0 2px rgba(24, 163, 255, 0.12);
    }

    .joyselect option {
      background: #122338;
      color: #f4f8ff;
    }

    .joyselectArrow {
      position: absolute;
      right: 14px;
      top: 50%;
      transform: translateY(-50%);
      color: #a7d8f4;
      pointer-events: none;
      font-size: 13px;
    }

    @media (max-width: 1100px) {
      .joypanel__head {
        flex-direction: column;
        align-items: flex-start;
      }

      .joycontroller {
        min-width: unset;
        width: 100%;
      }
    }
  `;

  document.head.appendChild(style);
}