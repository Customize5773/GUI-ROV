// camera.js — Halaman Camera (KKI 2026): tampilkan 2 kamera bersamaan
// CAM 1 = BOTTOM, CAM 2 = WALL + deteksi QR Code dari kedua kamera.
// QR menentukan sisi dinding (A/B/C/D) tempat payload digantung.

import { CONFIG } from "../config.js";
import {
  log,
  num,
  snapshotImage,
  makeFullscreen,
  camProxy
} from "../core.js";

export const cameraPage = {
  streaming: false,
  scanRaf: null,
  scanCanvas: null,
  lastQR: "",
  qrResults: [],
  visible: false,
  els: {},

  init(root) {
    const cams = CONFIG.CAMERAS || [];

    root.innerHTML = `
      <div class="campage">

        <div class="campage__head">
          <div>
            <span class="panel__eyebrow">VISION</span>
            <h2 class="tele__title">Live Cameras &amp; QR</h2>
          </div>

          <div class="campage__head-actions">
            <button class="chip chip--go" id="camStart">
              Start Stream
            </button>

            <span class="badge" id="camState">
              IDLE
            </span>
          </div>
        </div>

        <!-- =========================
             LIVE CAMERA GRID
             ========================= -->
        <div class="camgrid" id="camGrid"></div>


        <!-- =========================
             QR DETECTION
             ========================= -->
        <div class="qrpanel" id="qrPanel">

          <div class="qrpanel__main" style="width:100%;">

            <span class="panel__eyebrow">
              QR CODE DETECTION
            </span>

            <div
              id="qrCamGrid"
              style="
                display:grid;
                grid-template-columns:repeat(
                  2,
                  minmax(0,1fr)
                );
                gap:12px;
                margin-top:10px;
              "
            ></div>

          </div>

          <div class="qrpanel__actions">

            <label class="chip" for="qrFile">
              Scan dari gambar
            </label>

            <input
              id="qrFile"
              type="file"
              accept="image/*"
              hidden
            />

            <button class="chip" id="qrClear">
              Clear
            </button>

          </div>

        </div>


        <!-- =========================
             CAMERA CONFIGURATION
             ========================= -->
        <div
          class="campage__cfg"
          id="camCfg"
        ></div>

      </div>
    `;


    // =========================================================
    // DUA SEL KAMERA
    // =========================================================

    const grid = root.querySelector("#camGrid");

    this.els.cells = cams.map((c, i) => {

      const cell = document.createElement("div");

      cell.className = "camcell";

      cell.innerHTML = `
        <div class="camcell__bar">

          <span class="camcell__name">
            ${c.id}
            <b>${c.role || ""}</b>
          </span>

          <div class="camcell__actions">

            <button
              class="chip chip--ghost"
              data-snap="${i}"
              title="Snapshot frame ini"
            >
              ⤓
            </button>

            <button
              class="chip chip--ghost"
              data-full="${i}"
            >
              ⛶
            </button>

          </div>

        </div>


        <div
          class="camcell__view"
          id="camView${i}"
        >

          <img
            id="camImg${i}"
            alt="${c.id}"
          />

          <div class="hud">

            <div
              class="hud__compass"
              id="camHdg${i}"
            >
              HDG —°
            </div>

            <div class="hud__rp">

              <span id="camDepth${i}">
                D —m
              </span>

              <span id="camAlt${i}">
                ALT —m
              </span>

            </div>

          </div>


          <div
            class="cam__nosignal"
            id="camNo${i}"
          >
            <span>
              ${(c.role || c.id)} NOT CONNECTED
            </span>
          </div>


          <!-- PiP kamera lain -->
          <div
            class="pip campip"
            id="camPip${i}"
            aria-hidden="true"
          >

            <div
              class="pip__resize"
              title="Tarik untuk ubah ukuran"
            ></div>

            <canvas
              class="campip__img"
            ></canvas>

            <div class="pip__nosignal">
              NO CAM
            </div>

            <span class="pip__label"></span>

          </div>

        </div>
      `;

      grid.appendChild(cell);


      const img =
        cell.querySelector(`#camImg${i}`);

      const no =
        cell.querySelector(`#camNo${i}`);


      img.onload = () => {
        no.style.display = "none";
      };


      img.onerror = () => {
        no.style.display = "flex";
        img.removeAttribute("src");
      };


      const view =
        cell.querySelector(`#camView${i}`);

      const pip =
        cell.querySelector(`#camPip${i}`);

      const pipImg =
        pip.querySelector(".campip__img");

      const pipNo =
        pip.querySelector(".pip__nosignal");

      const pipLabel =
        pip.querySelector(".pip__label");


      // fullscreen
      const fs = makeFullscreen(
        view,
        {
          onToggle: (on) =>
            this._onCellFs(i, on)
        }
      );


      cell
        .querySelector(`[data-full="${i}"]`)
        .onclick = () => fs.toggle();


      // snapshot
      cell
        .querySelector(`[data-snap="${i}"]`)
        .onclick = () => {

          const tag =
            `hydroship_${(c.role || c.id).toLowerCase()}`;

          if (snapshotImage(img, tag)) {

            log(
              `Snapshot ${c.id} (${c.role || ""})`,
              "ok"
            );

          } else {

            log(
              `Tidak ada frame ${c.id} untuk snapshot`,
              "warn"
            );

          }

        };


      return {
        cell,
        view,
        img,
        no,
        pip,
        pipImg,
        pipNo,
        pipLabel,
        fs,
        fsOn: false
      };

    });


    // =========================================================
    // SETUP PiP
    // =========================================================

    this.els.cells.forEach(
      (cellObj, i) =>
        this._setupCellPip(cellObj, i)
    );


    // =========================================================
    // CONFIG URL KAMERA
    // =========================================================

    const cfg =
      root.querySelector("#camCfg");

    cams.forEach((c, i) => {

      const wrap =
        document.createElement("div");

      wrap.className =
        "camcfg__row";

      wrap.innerHTML = `
        <label class="field field--grow">

          <span>
            ${c.id} — ${c.role || ""} URL
          </span>

          <input
            id="camUrl${i}"
            type="text"
            placeholder="http://192.168.2.2:8080/stream"
            value="${c.url || ""}"
          />

        </label>

        <button
          class="btn-wide btn-wide--inline"
          data-apply="${i}"
        >
          Apply
        </button>
      `;

      cfg.appendChild(wrap);


      wrap
        .querySelector(`[data-apply="${i}"]`)
        .onclick = () => {

          const url =
            wrap
              .querySelector(`#camUrl${i}`)
              .value
              .trim();

          c.url = url;

        };

    });


    // =========================================================
    // QR PANEL PER KAMERA
    // =========================================================

    const qrCamGrid =
      root.querySelector("#qrCamGrid");


    this.els.qrCards = cams.map((c, i) => {

      const card =
        document.createElement("div");


      card.style.cssText = `
        min-width:0;
        padding:12px;
        border:1px solid rgba(100,180,220,.16);
        border-radius:10px;
        background:rgba(7,24,42,.72);
      `;


      card.innerHTML = `
        <div
          style="
            font-size:11px;
            font-weight:700;
            letter-spacing:.08em;
            color:#8fdcff;
            margin-bottom:8px;
          "
        >
          ${c.id}
          ${c.role ? `<b>${c.role}</b>` : ""}
        </div>


        <div
          style="
            display:flex;
            align-items:center;
            gap:10px;
          "
        >

          <div
            class="qr__side"
            id="qrSide${i}"
          >
            —
          </div>


          <div
            class="qr__info"
            style="min-width:0;"
          >

            <span
              class="qr__status"
              id="qrStatus${i}"
            >
              Menunggu feed…
            </span>


            <span
              class="qr__data"
              id="qrData${i}"
            >
              No QR detected
            </span>


            <span
              class="qr__time"
              id="qrTime${i}"
            ></span>

          </div>

        </div>
      `;


      qrCamGrid.appendChild(card);


      return {

        card,

        side:
          card.querySelector(
            `#qrSide${i}`
          ),

        status:
          card.querySelector(
            `#qrStatus${i}`
          ),

        data:
          card.querySelector(
            `#qrData${i}`
          ),

        time:
          card.querySelector(
            `#qrTime${i}`
          )

      };

    });


    // hasil QR masing-masing kamera
    this.qrResults =
      cams.map(() => "");


    // CLEAR semua hasil QR
    root
      .querySelector("#qrClear")
      .onclick = () =>
        this._clearQR();


    // scan QR dari file
    root
      .querySelector("#qrFile")
      .onchange = (e) =>
        this._scanFile(
          e.target.files[0]
        );


    // tombol stream
    root
      .querySelector("#camStart")
      .onclick = () =>
        this._toggleStream();


    this.els.state =
      root.querySelector("#camState");


    // canvas untuk jsQR
    this.scanCanvas =
      document.createElement("canvas");

  },


  // =========================================================
  // SHOW / HIDE
  // =========================================================

  onShow() {

    this.visible = true;

    if (!this.scanRaf) {
      this._scanLoop();
    }

  },


  onHide() {

    this.visible = false;

    if (this.scanRaf) {

      cancelAnimationFrame(
        this.scanRaf
      );

      this.scanRaf = null;

    }

  },


  // =========================================================
  // TELEMETRY
  // =========================================================

  onTelemetry(d) {

    const alt =
      Number.isFinite(d.depth)
        ? Math.max(
            0,
            CONFIG.POOL_DEPTH - d.depth
          )
        : null;


    (this.els.cells || [])
      .forEach((_, i) => {

        const set =
          (id, v) => {

            const el =
              document.getElementById(id);

            if (el) {
              el.textContent = v;
            }

          };


        set(
          `camHdg${i}`,
          "HDG " +
          num(d.heading, 0) +
          "°"
        );


        set(
          `camDepth${i}`,
          "D " +
          num(d.depth, 2) +
          "m"
        );


        set(
          `camAlt${i}`,
          "ALT " +
          num(alt, 2) +
          "m"
        );

      });

  },


  // =========================================================
  // FULLSCREEN + PiP
  // =========================================================

  _onCellFs(i, on) {

    const cams =
      CONFIG.CAMERAS || [];

    const cell =
      this.els.cells[i];

    cell.fsOn = on;

    const other =
      (i + 1) % cams.length;


    if (
      on &&
      cams.length >= 2
    ) {

      cell.pipLabel.textContent =
        `${cams[other].id} ${
          cams[other].role || ""
        }`.trim();


      if (
        !this.streaming ||
        !cams[other].url
      ) {

        cell.pipImg.style.display =
          "none";

        cell.pipNo.style.display =
          "flex";

      }

    }

  },

  _setupCellPip(cell, i) {

    const cams =
      CONFIG.CAMERAS || [];

    const pip =
      cell.pip;

    const vp =
      cell.view;

    let dragging = false;
    let moved = false;
    let sx = 0;
    let sy = 0;
    let ox = 0;
    let oy = 0;

    pip.style.cursor = "grab";
    pip.style.touchAction = "none";
    pip.addEventListener(
      "pointerdown",
      (e) => {
        if (
          e.target.classList.contains(
            "pip__resize"
          )
        ) {
          return;
        }
        dragging = true;
        moved = false;
        sx = e.clientX;
        sy = e.clientY;
        const r =
          pip.getBoundingClientRect();
        const vr =
          vp.getBoundingClientRect();
        ox =
          r.left - vr.left;
        oy =
          r.top - vr.top;
        pip.style.left =
          ox + "px";
        pip.style.top =
          oy + "px";
        pip.style.right =
          "auto";
        pip.style.bottom =
          "auto";
        pip.style.cursor =
          "grabbing";

        try {
          pip.setPointerCapture(
            e.pointerId
          );
        } catch (_) {}
        e.preventDefault();
      }
    );

    pip.addEventListener(
      "pointermove",
      (e) => {
        if (!dragging) {
          return;
        }

        if (
          Math.abs(
            e.clientX - sx
          ) > 3 ||
          Math.abs(
            e.clientY - sy
          ) > 3
        ) {
          moved = true;
        }
        const vr =
          vp.getBoundingClientRect();
        pip.style.left =
          Math.max(
            0,
            Math.min(
              ox +
                (e.clientX - sx),
              vr.width -
                pip.offsetWidth
            )
          ) + "px";

        pip.style.top =
          Math.max(
            0,
            Math.min(
              oy +
                (e.clientY - sy),
              vr.height -
                pip.offsetHeight
            )
          ) + "px";
      }
    );
    const end = (e) => {
      if (!dragging) {
        return;
      }
      dragging = false;
      pip.style.cursor =
        "grab";
      try {
        pip.releasePointerCapture(
          e.pointerId
        );
      } catch (_) {}
      // klik → tukar fullscreen
      if (
        !moved &&
        cams.length >= 2
      ) {
        const other =
          (i + 1) % cams.length;
        cell.fs.toggle();
        this.els
          .cells[other]
          .fs.toggle();
      }
    };

    pip.addEventListener(
      "pointerup",
      end
    );

    pip.addEventListener(
      "pointercancel",
      end
    );
    // resize PiP
    const handle =
      pip.querySelector(
        ".pip__resize"
      );

    let rz = false;
    let rsx = 0;
    let rw = 0;

    handle.addEventListener(
      "pointerdown",
      (e) => {
        rz = true;
        rsx =
          e.clientX;
        rw =
          pip.offsetWidth;
        try {
          handle.setPointerCapture(
            e.pointerId
          );
        } catch (_) {}
        e.preventDefault();
        e.stopPropagation();
      }
    );

    handle.addEventListener(
      "pointermove",
      (e) => {
        if (!rz) {
          return;
        }
        const vr =
          vp.getBoundingClientRect();
        pip.style.width =
          Math.max(
            140,
            Math.min(
              rw +
                (rsx -
                  e.clientX),
              vr.width *
                0.85
            )
          ) + "px";
        e.stopPropagation();
      }
    );

    const rend = (e) => {
      if (!rz) {
        return;
      }
      rz = false;
      try {
        handle.releasePointerCapture(
          e.pointerId
        );
      } catch (_) {}
    };
    handle.addEventListener(
      "pointerup",
      rend
    );
    handle.addEventListener(
      "pointercancel",
      rend
    );
  },
  // =========================================================
  // GANTI KAMERA DENGAN D-PAD
  // =========================================================
  cycleCamera(dir) {
    const cams =
      CONFIG.CAMERAS || [];
    const cells =
      this.els.cells || [];
    if (
      cams.length < 2 ||
      !cells.length
    ) {
      return;
    }
    const current =
      cells.findIndex(
        (c) => c.fsOn
      );
    const from =
      current >= 0
        ? current
        : 0;
    const next =
      (from + dir + cams.length) %
      cams.length;
    if (
      next === current
    ) {
      return;
    }
    if (current >= 0) {
      cells[current].fs.toggle();
    }
    cells[next].fs.toggle();
  },
  // =========================================================
  // START / STOP STREAM
  // =========================================================
  _toggleStream() {
    this.streaming =
      !this.streaming;
    this.els.state.textContent =
      this.streaming
        ? "LIVE"
        : "IDLE";

    const cams =
      CONFIG.CAMERAS || [];
    cams.forEach((c, i) => {
      const cell =
        this.els.cells[i];

      if (
        this.streaming &&
        c.url
      ) {
        cell.img.src =
          camProxy(c.url);

      } else {
        cell.img.removeAttribute(
          "src"
        );
      }

      // PiP — digambar dari <img> sel sebelah lewat canvas (lihat _scanLoop),
      // bukan koneksi MJPEG baru, supaya klik Start Stream tidak menggandakan
      // jumlah koneksi ke kamera fisik.
      const other =
        cams[
          (i + 1) %
          cams.length
        ];

      cell.pipActive =
        this.streaming &&
        !!(other && other.url);

      if (!cell.pipActive) {
        const ctx = cell.pipImg.getContext("2d");
        ctx.clearRect(0, 0, cell.pipImg.width, cell.pipImg.height);
        cell.pipNo.style.display = "flex";
      }
    });
  },

  // =========================================================
  // GAMBAR PiP — mirror <img> sel sebelah ke canvas, tanpa
  // koneksi MJPEG baru (throttle ±20x/detik, cukup untuk thumbnail kecil)
  // =========================================================
  _drawPips() {
    const now = performance.now();
    if (this._lastPip && now - this._lastPip < 50) {
      return;
    }
    this._lastPip = now;

    const cells = this.els.cells || [];
    cells.forEach((cell, i) => {
      if (!cell.pipActive) {
        return;
      }
      const src = cells[(i + 1) % cells.length]?.img;
      if (!src || !src.naturalWidth) {
        return;
      }

      const cv = cell.pipImg;
      if (
        cv.width !== src.naturalWidth ||
        cv.height !== src.naturalHeight
      ) {
        cv.width = src.naturalWidth;
        cv.height = src.naturalHeight;
      }
      cv.getContext("2d").drawImage(src, 0, 0);

      cv.style.display = "";
      cell.pipNo.style.display = "none";
    });
  },

  // =========================================================
  // QR SCAN — SEMUA KAMERA
  // =========================================================
  _scanLoop() {

    this.scanRaf =
      requestAnimationFrame(
        () =>
          this._scanLoop()
      );

    if (!this.visible) {
      return;
    }

    // PiP digambar terus (termasuk saat fullscreen), lepas dari status jsQR
    this._drawPips();

    if (!window.jsQR) {
      return;
    }
    // jangan scan ketika fullscreen
    if (
      (this.els.cells || [])
        .some(
          (c) => c.fsOn
        )
    ) {
      return;
    }
    // throttle ±6x/detik
    const now =
      performance.now();

    if (
      this._lastScan &&
      now -
        this._lastScan <
        160
    ) {
      return;
    }
    this._lastScan =
      now;
    
      const cells =
      this.els.cells || [];
    const cams =
      CONFIG.CAMERAS || [];
    // =======================================================
    // SCAN CAM 1 DAN CAM 2
    // =======================================================
    for (
      let i = 0;
      i < cells.length;
      i++
    ) {

      const img =
        cells[i]?.img;
      const qr =
        this.els.qrCards?.[i];

      if (!qr) {
        continue;
      }

      if (
        !img ||
        !img.naturalWidth
      ) {
        qr.status.textContent =
          "Menunggu feed…";
        continue;
      }

      qr.status.textContent =
        "Memindai…";
      const code =
        this._decode(img);

      if (code) {
        const cam =
          cams[i];
        const camName =
          cam
            ? `${cam.id} ${
                cam.role || ""
              }`.trim()
            : `CAM ${i + 1}`;
        this._setQR(
          code.data,
          `QR terdeteksi dari ${camName}`,
          i
        );
      }
    }
  },
  // =========================================================
  // DECODE QR DENGAN jsQR
  // =========================================================
  _decode(source, w, h) {
    const sw =
      w ||
      source.naturalWidth ||
      source.width;
    const sh =
      h ||
      source.naturalHeight ||
      source.height;
    if (
      !sw ||
      !sh
    ) {
      return null;
    }
    // maksimum 800 px
    const scale =
      Math.min(
        1,
        800 /
          Math.max(
            sw,
            sh
          )
      );
    const cw =
      Math.max(
        1,
        Math.round(
          sw * scale
        )
      );
    const ch =
      Math.max(
        1,
        Math.round(
          sh * scale
        )
      );
    const cv =
      this.scanCanvas;
    cv.width =
      cw;
    cv.height =
      ch;

    const ctx =
      cv.getContext(
        "2d",
        {
          willReadFrequently:
            true
        }
      );

    try {
      ctx.drawImage(
        source,
        0,
        0,
        cw,
        ch
      );

      const data =
        ctx.getImageData(
          0,
          0,
          cw,
          ch
        );

      return window.jsQR(
        data.data,
        cw,
        ch
      );

    } catch (e) {
      // canvas ter-taint
      log(
        "Feed kamera tidak same-origin. Pastikan menggunakan proxy /cam.",
        "warn"
      );
      return null;
    }
  },
  // =========================================================
  // SCAN QR DARI FILE GAMBAR
  // =========================================================
  _scanFile(file) {

    if (
      !file ||
      !window.jsQR
    ) {
      return;
    }

    const im =
      new Image();

    const url =
      URL.createObjectURL(
        file
      );

    im.onload = () => {
      const code =
        this._decode(
          im,
          im.naturalWidth,
          im.naturalHeight
        );

      if (code) {
        log(
          `QR dari gambar: "${code.data}"`,
          "ok"
        );
      } else {

        log(
          "Tidak ada QR pada gambar",
          "warn"
        );
      }

      URL.revokeObjectURL(
        url
      );
    };

    im.onerror = () => {
      URL.revokeObjectURL(
        url
      );

      log(
        "Gagal memuat gambar QR",
        "warn"
      );
    };

    im.src =
      url;
  },
  // =========================================================
  // CLEAR QR CAM 1 + CAM 2
  // =========================================================
  _clearQR() {
    this.qrResults =
      (
        CONFIG.CAMERAS || []
      ).map(
        () => ""
      );

    (
      this.els.qrCards || []
    ).forEach(
      (qr) => {
        qr.side.textContent =
          "—";
        qr.side.className =
          "qr__side";
        qr.data.textContent =
          "No QR detected";
        qr.status.textContent =
          "Menunggu feed…";
        qr.time.textContent =
          "";
      }
    );

    this.lastQR =
      "";
  },
  // =========================================================
  // TAMPILKAN HASIL QR PER KAMERA
  // =========================================================
  _setQR(
    data,
    status,
    camIndex = 0
  ) {

    const qr =
      this.els.qrCards?.[
        camIndex
      ];

    if (!qr) {
      return;
    }

    qr.status.textContent =
      status || "";

    if (!data) {

      qr.side.textContent =
        "—";
      qr.side.className =
        "qr__side";
      qr.data.textContent =
        "No QR detected";
      qr.time.textContent =
        "";
      this.qrResults[
        camIndex
      ] = "";
      return;
    }

    // =======================================================
    // FORMAT QR KKI
    //
    // {"mission":5,
    //  "team":"HYDROSHIP",
    //  "type":"payload",
    //  "id":"A"}
    // =======================================================

    let side = "?";
    let shown = data;
    let parsed = null;

    try {
      parsed =
        JSON.parse(
          data
        );
    } catch (_) {
      parsed =
        null;
    }

    if (
      parsed &&
      typeof parsed ===
        "object" &&
      typeof parsed.id ===
        "string" &&
      /^[ABCD]$/.test(
        parsed.id
          .trim()
          .toUpperCase()
      )
    ) {

      side =
        parsed.id
          .trim()
          .toUpperCase();
      const team =
        parsed.team
          ? ` · ${parsed.team}`
          : "";
      const miss =
        parsed.mission != null
          ? ` · M${parsed.mission}`
          : "";
      shown =
        `id=${side}${miss}${team}`;
    } else {

      const m =
        String(data)
          .toUpperCase()
          .match(
            /(?:^|[^A-Z])([ABCD])(?![A-Z])/
          );

      side =
        m
          ? m[1]
          : "?";
    }
    // =======================================================
    // UPDATE PANEL KAMERA
    // =======================================================
    qr.data.textContent =
      shown;
    qr.side.textContent =
      side;
    qr.side.className =
      "qr__side qr__side--" +
      (
        side === "?"
          ? "unknown"
          : "ok"
      );
    qr.time.textContent =
      new Date()
        .toLocaleTimeString(
          "id-ID",
          {
            hour12: false
          }
        );
    // =======================================================
    // LOG HANYA JIKA QR BERUBAH
    // =======================================================
    const previous =
      this.qrResults[
        camIndex
      ];
    if (
      data !== previous
    ) {
      const cam =
        CONFIG.CAMERAS?.[
          camIndex
        ];
      const camName =
        cam
          ? `${cam.id} ${
              cam.role || ""
            }`.trim()
          : `CAM ${
              camIndex + 1
            }`;
      log(
        `QR ${camName}: "${data}" → sisi ${side}`,
        "ok"
      );
    }
    this.qrResults[
      camIndex
    ] = data;
    this.lastQR =
      data;
  }
};