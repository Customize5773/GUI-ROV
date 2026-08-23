// vehicle.js — Halaman Vehicle Configuration (adaptasi QGroundControl "Parameters").
//
// Isi: tabel SELURUH parameter ArduSub di FC — cari, lihat, dan ubah satu per
// satu. Melengkapi halaman Setup (thruster/PID/pool depth) yang hanya menyentuh
// segelintir param lewat form tetap; di sini semuanya bisa dijangkau tanpa
// mencabut kabel umbilical dari dashboard untuk dicolok ke QGroundControl.
//
// Yang SENGAJA tidak ada di sini (sudah ada tempatnya, jangan diduplikasi):
//   - arah thruster  -> Setup → Thruster (command `thruster_config`)
//   - PID            -> Setup → PID
//   - mapping gamepad-> halaman Joystick
//   - firmware flashing, geofence, mission planner (lihat Planning/PLAN-QgroundControl.md §4)
//
// Sumber kebenaran nilai param adalah FC, BUKAN halaman ini: tabel diisi dari
// PARAM_VALUE yang dikirim wahana, dan nilai baru baru dianggap tertulis
// setelah FC meng-echo-nya balik (badge "synced"). Pola yang sama dipakai
// tombol ARM dan tab mode di app.js — jangan pernah percaya klik operator
// sebagai bukti wahana sudah berubah.

import { log, sendCmd, wsSend, makeFullscreen } from "../core.js";

/* Tipe param (padanan rov_params.PARAM_TYPES di Python & sim-params.js di server). */
const PARAM_TYPE_NAMES = {
  1: "UINT8", 2: "INT8", 3: "UINT16", 4: "INT16", 5: "UINT32",
  6: "INT32", 7: "UINT64", 8: "INT64", 9: "REAL32", 10: "REAL64",
};
const INTEGER_TYPES = new Set([1, 2, 3, 4, 5, 6, 7, 8]);

/* Tanpa filter, 975 baris DOM dibangun ulang tiap render — laptop venue
   langsung tersendat. Batasi tampilan dan minta operator menyaring. */
const RENDER_CAP = 200;

/* Batch param datang beruntun; menggambar ulang tabel tiap batch mubazir. */
const RENDER_INTERVAL_MS = 250;

/* Param yang salah tulis di sini paling berpotensi membuat wahana tidak bisa
   arm, thruster berputar terbalik, atau failsafe mati. Teks konfirmasinya
   diberi peringatan tambahan — gerbangnya sendiri sama untuk semua param. */
const RISKY_PREFIXES = [
  ["MOT_", "arah/keluaran thruster bisa berubah"],
  ["FRAME", "geometri frame — mixer bisa salah total"],
  ["SERVO", "pemetaan keluaran servo/ESC bisa berubah"],
  ["FS_", "ini failsafe — mematikannya menghilangkan pengaman"],
  ["ARMING", "cek pra-arm — wahana bisa jadi tidak bisa/terlalu mudah di-arm"],
  ["BATT_", "pemantauan baterai & failsafe tegangan"],
  ["ATC_", "gain kendali sikap — wahana bisa berosilasi"],
  ["PSC_", "gain kendali posisi/kedalaman"],
];

function typeName(ptype) {
  return PARAM_TYPE_NAMES[ptype] || `?${ptype}`;
}

function formatValue(value, ptype) {
  if (!Number.isFinite(value)) return "—";
  if (INTEGER_TYPES.has(ptype)) return String(Math.round(value));
  // Cukup untuk membedakan gain, tanpa memamerkan galat float32.
  return String(Number(value.toFixed(6)));
}

function riskyNote(name) {
  const hit = RISKY_PREFIXES.find(([p]) => name.startsWith(p));
  return hit ? hit[1] : null;
}

export const vehiclePage = {
  /* name -> {value, ptype, index, count, status} ; status: synced|pending|fail */
  params: new Map(),
  filter: "",   // kotak cari — cocok di MANA SAJA dalam nama
  group: "",    // dropdown grup — cocok hanya di AWAL nama
  expectedCount: 0,
  loading: false,
  requestedOnce: false,
  els: {},
  _renderTimer: null,
  _dirty: false,

  init(root) {
    root.innerHTML = `
      <div class="veh">
        <div class="veh__design">
          <div class="veh__eyebrow-row">
            <span class="panel__eyebrow">SPESIFIKASI TEKNIS</span>
            <h2 class="veh__title veh__title--design">Desain ROV</h2>
          </div>
          <div class="veh__plates">
            <figure class="veh__plate">
              <img src="models/rov2d_1.jpeg" alt="Desain ROV — tampak 1" />
              <span class="veh__plate__label">PLAT 01 &middot; TAMPAK 1</span>
              <button class="chip chip--ghost veh__plate__zoom" data-plate-fs="0" title="Perbesar">⛶</button>
            </figure>
            <figure class="veh__plate">
              <img src="models/rov2d_2.jpeg" alt="Desain ROV — tampak 2" />
              <span class="veh__plate__label">PLAT 02 &middot; TAMPAK 2</span>
              <button class="chip chip--ghost veh__plate__zoom" data-plate-fs="1" title="Perbesar">⛶</button>
            </figure>
          </div>
        </div>

        <div class="veh__head">
          <div>
            <span class="panel__eyebrow">VEHICLE CONFIGURATION</span>
            <h2 class="veh__title">Parameter ArduSub</h2>
          </div>
          <span class="badge" id="vehStatus">Belum dimuat</span>
        </div>

        <p class="card__desc veh__note">
          Nilai di tabel ini datang langsung dari flight controller. Perubahan baru
          dianggap berhasil setelah FC mengembalikannya (badge <b>synced</b>).
          Untuk arah thruster, PID, dan mapping joystick pakai halaman khusus —
          tautan di bawah.
        </p>

        <div class="veh__controls">
          <label class="field field--sm field--grow"><span>Cari param</span>
            <input id="vehSearch" type="text" placeholder="mis. MOT, FS_, BATT" autocomplete="off" />
          </label>
          <label class="field field--sm"><span>Grup</span>
            <select id="vehGroup"><option value="">(semua)</option></select>
          </label>
          <div class="veh__btns">
            <button class="chip chip--go" id="vehRefresh">Muat Ulang Semua</button>
            <button class="chip" id="vehGoSetup">Thruster &amp; PID →</button>
            <button class="chip" id="vehGoJoy">Joystick →</button>
          </div>
        </div>

        <div class="veh__progress" id="vehProgress" hidden>
          <div class="veh__progress-bar"><span id="vehProgressFill"></span></div>
          <span class="veh__progress-text" id="vehProgressText"></span>
        </div>

        <div class="veh__tablewrap">
          <table class="veh__table">
            <thead>
              <tr><th>Parameter</th><th class="veh__num">Nilai</th><th>Tipe</th><th>Status</th></tr>
            </thead>
            <tbody id="vehRows"></tbody>
          </table>
        </div>

        <p class="veh__hint" id="vehHint"></p>
      </div>`;

    this.els.status = root.querySelector("#vehStatus");
    this.els.rows = root.querySelector("#vehRows");
    this.els.hint = root.querySelector("#vehHint");
    this.els.group = root.querySelector("#vehGroup");
    this.els.progress = root.querySelector("#vehProgress");
    this.els.progressFill = root.querySelector("#vehProgressFill");
    this.els.progressText = root.querySelector("#vehProgressText");

    const search = root.querySelector("#vehSearch");
    search.oninput = () => { this.filter = search.value.trim().toUpperCase(); this._render(); };

    // Grup DAN pencarian berlaku bersamaan, dengan aturan yang berbeda dan
    // sengaja: grup mencocokkan AWAL nama (memilih "MOT_" tidak boleh ikut
    // membawa COMPASS_MOT_X), pencarian mencocokkan di mana saja (mengetik
    // "GCS" harus menemukan FS_GCS_ENABLE).
    this.els.group.onchange = () => { this.group = this.els.group.value; this._render(); };

    root.querySelector("#vehRefresh").onclick = () => this.requestAll();
    root.querySelector("#vehGoSetup").onclick = () => this._goto("setup");
    root.querySelector("#vehGoJoy").onclick = () => this._goto("joystick");

    for (const btn of root.querySelectorAll("[data-plate-fs]")) {
      const plate = btn.closest(".veh__plate");
      const fs = makeFullscreen(plate);
      btn.onclick = () => fs.toggle();
    }

    this._render();
  },

  onShow() {
    // Sekali saja: menarik ~975 param tiap kali tab dibuka membebani link
    // serial tanpa perlu. Operator bisa menekan "Muat Ulang Semua".
    if (!this.requestedOnce) this.requestAll();
  },

  onHide() {
    if (this._renderTimer) { clearTimeout(this._renderTimer); this._renderTimer = null; }
  },

  _goto(page) {
    // Pola event lintas-halaman yang sudah dipakai "hydroship:pool-depth" dan
    // "hydroship:camera-url" di setup.js.
    window.dispatchEvent(new CustomEvent("hydroship:goto-page", { detail: page }));
  },

  requestAll() {
    this.requestedOnce = true;
    this.loading = true;
    this.expectedCount = 0;
    this._setStatus("Memuat…", "badge--active");
    sendCmd("param_list", true);
    log("Minta seluruh parameter dari FC", "");
    this._render();
  },

  /* ---------- pesan dari server ---------- */

  onParamBatch(msg) {
    if (!msg || !Array.isArray(msg.params)) return;

    for (const p of msg.params) {
      if (!p || typeof p.name !== "string") continue;
      const prev = this.params.get(p.name);
      this.params.set(p.name, {
        value: Number(p.value),
        ptype: Number(p.ptype),
        // index -1 dipakai untuk pembaruan satuan (param_get / echo param_set),
        // yang tidak boleh merusak urutan hasil param_list.
        index: Number.isFinite(p.index) && p.index >= 0 ? p.index : (prev ? prev.index : -1),
        status: prev && prev.status === "pending" ? prev.status : "synced",
      });
      if (Number.isFinite(p.count) && p.count > 0) this.expectedCount = p.count;
    }

    if (msg.done) {
      this.loading = false;
      log(`Parameter FC dimuat: ${this.params.size}`, "ok");
    }

    this._dirty = true;
    this._scheduleRender();
  },

  onParamAck(msg) {
    if (!msg || typeof msg.name !== "string") return;

    const entry = this.params.get(msg.name);
    if (entry) {
      entry.status = msg.ok ? "synced" : "fail";
      if (msg.ok && Number.isFinite(msg.value)) entry.value = Number(msg.value);
    }

    // Hasilnya sudah di-log terpusat di app.js (supaya tetap terlihat walau
    // halaman ini belum pernah dibuka); di sini cukup perbarui badge baris.
    this._dirty = true;
    this._scheduleRender();
  },

  /* ---------- render ---------- */

  _setStatus(text, cls = "") {
    if (!this.els.status) return;
    this.els.status.textContent = text;
    this.els.status.className = "badge " + cls;
  },

  _scheduleRender() {
    if (this._renderTimer) return;
    this._renderTimer = setTimeout(() => {
      this._renderTimer = null;
      if (this._dirty) { this._dirty = false; this._render(); }
    }, RENDER_INTERVAL_MS);
  },

  _matching() {
    const f = this.filter, g = this.group;
    const names = [...this.params.keys()]
      .filter((n) => (!g || n.startsWith(g)) && (!f || n.includes(f)));
    names.sort();
    return names;
  },

  _render() {
    if (!this.els.rows) return;

    this._renderProgress();
    this._renderGroups();

    const names = this._matching();
    const shown = names.slice(0, RENDER_CAP);

    if (!this.params.size) {
      this.els.rows.innerHTML = `<tr><td colspan="4" class="veh__empty">${
        this.loading ? "Menunggu parameter dari FC…"
                     : "Belum ada parameter. Tekan “Muat Ulang Semua”."
      }</td></tr>`;
      this.els.hint.textContent = "";
      if (!this.loading) this._setStatus("Belum dimuat");
      return;
    }

    this.els.rows.innerHTML = shown.map((name) => {
      const p = this.params.get(name);
      const badge = p.status === "pending" ? '<span class="badge badge--active">pending</span>'
                  : p.status === "fail"    ? '<span class="badge badge--fault">gagal</span>'
                  :                          '<span class="badge badge--ok">synced</span>';
      const risky = riskyNote(name) ? ' <span class="veh__risky" title="parameter sensitif">!</span>' : "";
      return `<tr data-name="${name}">
        <td class="veh__name">${name}${risky}</td>
        <td class="veh__num"><button class="veh__val" data-edit="${name}">${formatValue(p.value, p.ptype)}</button></td>
        <td class="veh__type">${typeName(p.ptype)}</td>
        <td>${badge}</td>
      </tr>`;
    }).join("");

    for (const btn of this.els.rows.querySelectorAll("[data-edit]")) {
      btn.onclick = () => this._beginEdit(btn.getAttribute("data-edit"));
    }

    const hidden = names.length - shown.length;
    this.els.hint.textContent = hidden > 0
      ? `Menampilkan ${shown.length} dari ${names.length} param yang cocok (total ${this.params.size}). Persempit pencarian untuk melihat sisanya.`
      : `${names.length} param cocok dari total ${this.params.size}.`;

    if (!this.loading) this._setStatus(`${this.params.size} param`, "badge--ok");
  },

  _renderProgress() {
    if (!this.els.progress) return;

    if (!this.loading || !this.expectedCount) {
      this.els.progress.hidden = true;
      return;
    }

    const pct = Math.min(100, Math.round((this.params.size / this.expectedCount) * 100));
    this.els.progress.hidden = false;
    this.els.progressFill.style.width = `${pct}%`;
    this.els.progressText.textContent = `${this.params.size} / ${this.expectedCount}`;
  },

  _renderGroups() {
    if (!this.els.group) return;

    // Prefiks diturunkan dari nama yang BENAR-BENAR diterima, bukan daftar
    // hardcoded — firmware berbeda punya kelompok param berbeda.
    const groups = new Set();
    for (const name of this.params.keys()) {
      const us = name.indexOf("_");
      groups.add(us > 0 ? name.slice(0, us + 1) : name);
    }

    if (groups.size === this._groupCount) return;   // tidak berubah, jangan gambar ulang
    this._groupCount = groups.size;

    const current = this.els.group.value;
    this.els.group.innerHTML = '<option value="">(semua)</option>' +
      [...groups].sort().map((g) => `<option value="${g}">${g}</option>`).join("");
    this.els.group.value = current;
  },

  /* ---------- edit + gerbang konfirmasi ---------- */

  _beginEdit(name) {
    const p = this.params.get(name);
    if (!p) return;

    const row = this.els.rows.querySelector(`tr[data-name="${name}"]`);
    if (!row) return;

    const cell = row.querySelector(".veh__num");
    const old = formatValue(p.value, p.ptype);

    cell.innerHTML = `<input class="veh__input" type="number" step="${INTEGER_TYPES.has(p.ptype) ? "1" : "any"}" value="${old}" />`;
    const input = cell.querySelector("input");
    input.focus();
    input.select();

    const cancel = () => this._render();

    input.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); this._commitEdit(name, input.value); }
      else if (e.key === "Escape") { e.preventDefault(); cancel(); }
    };
    // Klik ke luar = batal. Menyimpan diam-diam saat blur akan menulis ke FC
    // tanpa operator sadar.
    input.onblur = () => setTimeout(cancel, 120);
  },

  _commitEdit(name, raw) {
    const p = this.params.get(name);
    if (!p) return;

    const next = Number(raw);
    if (!Number.isFinite(next)) { log(`Nilai ${name} tidak valid`, "warn"); this._render(); return; }

    const rounded = INTEGER_TYPES.has(p.ptype) ? Math.round(next) : next;
    if (rounded === p.value) { this._render(); return; }

    // Gerbang konfirmasi. Sengaja confirm() bawaan browser — repo ini tidak
    // punya utilitas modal, dan menambah satu di sini berarti dua mekanisme
    // konfirmasi yang berbeda untuk dua hal yang sama-sama berisiko.
    const note = riskyNote(name);
    const pesan =
      `Ubah parameter ${name}\n\n` +
      `  ${formatValue(p.value, p.ptype)}  →  ${formatValue(rounded, p.ptype)}   (${typeName(p.ptype)})\n\n` +
      (note ? `PERINGATAN: ${note}.\n\n` : "") +
      "Nilai ditulis langsung ke flight controller. Lanjutkan?";

    if (!confirm(pesan)) {
      log(`Perubahan ${name} dibatalkan`, "warn");
      this._render();
      return;
    }

    p.status = "pending";
    wsSend({ type: "cmd", name: "param_set", value: { name, value: rounded, type: p.ptype } });
    log(`Tulis ${name} = ${formatValue(rounded, p.ptype)} …`, "");
    this._render();
  },
};
