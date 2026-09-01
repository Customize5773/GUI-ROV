/* Skema profil joystick — SATU sumber kebenaran untuk browser dan server.
 *
 * Sebelumnya default + tabel migrasi ditulis kembar di public/js/joystick-state.js
 * dan server/server.js, lengkap dengan komentar "harus sinkron" — yang artinya
 * memang pernah tidak sinkron. Semua definisi skema sekarang tinggal di sini.
 *
 * ES module, NOL dependensi. Browser meng-import langsung; server memuatnya
 * lewat await import() (lihat server/joystick-config.js).
 *
 * ================== KENAPA PROFIL INI PORTABEL ==================
 * Profil menyimpan indeks mentah ("axis 2", button 7). Indeks itu hanya bermakna
 * kalau lapisan HID host identik. Logitech F310 punya saklar X/D di belakang:
 *
 *   Posisi X (XInput)      -> gamepad.mapping === "standard"
 *                             indeks axis/tombol SAMA di Windows & Ubuntu
 *   Posisi D (DirectInput) -> gamepad.mapping === ""
 *                             indeks berbeda per OS
 *
 * Karena itu sistem MEWAJIBKAN mode X (lihat isStandardMapping). Dengan syarat
 * itu dipenuhi, indeks menjadi kanonik dan profil bisa dipindah antar mesin
 * maupun antar OS tanpa kalibrasi ulang.
 */

/* v3: D-pad ↑/↓ pindah dari mount tilt ke geser setpoint kedalaman (mode
   "repeat"), mount tilt turun ke D-pad ←/→. Versi dinaikkan supaya profil
   tersimpan operator ikut dimigrasikan, bukan diam-diam menahan binding lama.

   v4: aksi "mode_poshold" (Alt Hold + tahan heading) masuk BUTTON_ACTIONS.
   Versi dinaikkan supaya profil tersimpan divalidasi ulang terhadap daftar aksi
   yang baru.

   v5: aksi baru "emergency_stop" masuk BUTTON_ACTIONS, dan default binding
   digeser: disarm pindah dari B ke LB (button 4), mode_manual pindah dari X
   ke B (button 1), emergency_stop menempati X (button 2, slot lama
   mode_manual). Versi dinaikkan supaya default binding baru diterapkan ke
   profil tersimpan operator, bukan diam-diam menahan binding lama.

   v6: trim setpoint kedalaman ±0.05 m (gain_inc/gain_dec, mode "repeat")
   dihapus. Penggantinya "depth_set" (rekam kedalaman saat ini) dan
   "depth_hold_toggle" (nyalakan/matikan depth-set), keduanya sekali-pencet di
   posisi D-pad yang sama. Versi dinaikkan supaya profil tersimpan ikut
   dibetulkan mode-nya, bukan hanya nama aksinya.

   v7: mode ACRO dihapus dari dashboard (tanpa cascade PID kedalaman ArduSub
   dan tanpa handler ACRO di app.js, action ini praktis mati). Profil
   tersimpan yang punya tombol terikat ke "mode_acro" dimigrasikan ke
   "no_function" supaya tidak diam-diam macet.

   v10: 4 aksi baru yang men-trigger tombol GUI yang sudah ada (light,
   snapshot, record, toggle mode manual/autonomous) supaya bisa di-bind ke
   tombol joystick lewat halaman Setup > Joystick. */
export const SCHEMA_VERSION = 10;

export const BUTTON_ACTIONS = [
  "no_function",
  "arm",
  "disarm",
  "mode_manual",
  // Menghentikan seluruh thruster seketika (netralkan axis + disarm). Aksi
  // terpisah dari mode_manual supaya keduanya tidak menumpang satu action id.
  "emergency_stop",
  "mode_stabilize",
  "mode_depth_hold",
  // Alt Hold + tahan heading (overlay sisi Pi). Tidak ter-bind di
  // defaultButtonLayer(): ke-16 tombol pad sudah terpakai. Bind manual lewat
  // halaman Setup > Joystick.
  "mode_poshold",
  "input_hold_set",
  "mount_tilt_up",
  "mount_tilt_down",
  "cam_prev",
  "cam_next",
  "camera_stream",
  "mount_center",
  "actuator1_inc",
  "actuator1_dec",
  "grip_open",
  "grip_close",
  "lights_brighter",
  "lights_dimmer",
  "depth_masuk_hook",
  "depth_dasar",
  "depth_ambil_hook",
  "thruster_gain_inc",
  "thruster_gain_dec",
  "toggle_light",
  "camera_snapshot",
  "toggle_record",
  "toggle_control_mode",
];

/* Cara sebuah tombol memicu aksinya:
     toggle — sekali saat ditekan.
     hold   — sekali saat ditekan + sekali saat dilepas (aksi punya "lawan",
              mis. gripper yang harus berhenti).
     repeat — sekali saat ditekan, lalu berulang selama ditahan. Tidak dipakai
              aksi bawaan mana pun sejak trim kedalaman dihapus, tapi tetap
              tersedia untuk binding kustom operator.
   Ditaruh di sini (bukan di halaman Joystick) karena normalizeProfile() ikut
   memvalidasinya. */
export const BUTTON_MODES = ["toggle", "hold", "repeat"];

/* Label axis yang bisa dipilih operator. "Axis S"/"Axis T" sengaja TIDAK ada:
   keduanya dulu muncul di dropdown tapi tidak pernah dibaca runtime, jadi
   memilihnya sama saja mematikan sumbu tanpa pesan apa pun. */
export const AXIS_OPTIONS = [
  "Axis X",       // surge
  "Axis Y",       // sway
  "Axis Z",       // heave
  "Axis R",       // yaw
  "No function",
];

/* Profil lama memetakan tombol ke actuator1_inc/dec, yang tidak punya handler
   apa pun di sisi ROV (rov_agent.py) — praktis mati. Gripper memakai posisi
   tombol yang sama, jadi profil tersimpan dimigrasikan otomatis supaya
   operator tidak perlu menyunting ulang halaman Joystick. */
const ACTION_MIGRATION = {
  actuator1_inc: "grip_close",
  actuator1_dec: "grip_open",

  /* Trim kedalaman ±0.05 m per penekanan dihapus: depth-set sekarang bekerja
     dengan merekam kedalaman saat ini (SET) lalu menyalakannya (ON/OFF), jadi
     tidak ada lagi setpoint yang perlu dihitung selangkah demi selangkah.
     Dipetakan ke pasangan penggantinya di posisi yang sama (D-pad ↑ = SET,
     D-pad ↓ = ON/OFF) supaya profil tersimpan tidak kehilangan kontrol
     kedalaman diam-diam. `mode` ikut dibetulkan di migrasi v6 di bawah —
     "repeat" tidak masuk akal untuk tombol sekali-pencet. */
  gain_dec: "depth_set",
  gain_inc: "depth_hold_toggle",

  // ACRO dihapus dari dashboard (v7): tombol yang masih terikat ke aksi ini
  // dimigrasikan ke no_function alih-alih diam-diam macet.
  mode_acro: "no_function",
};

export function migrateButtonAction(action) {
  return ACTION_MIGRATION[action] || action;
}

/* ===================== LOGITECH F310 / STANDARD GAMEPAD =====================
 * Tata letak W3C "standard gamepad" — persis yang dilaporkan F310 di posisi X.
 * Dipakai untuk label UI dan validasi jumlah input.
 *
 * PENTING: standard mapping mengekspos 4 AXIS dan 17 TOMBOL. UI lama menawarkan
 * 5 axis dan 16 tombol, jadi "axis 4" tidak pernah ada di perangkatnya dan
 * tombol Logitech (16) tidak bisa dipilih sama sekali.
 */
export const STANDARD_AXIS_COUNT = 4;
export const STANDARD_BUTTON_COUNT = 17;

export const STANDARD_LAYOUT = {
  axes: ["Stik kiri X", "Stik kiri Y", "Stik kanan X", "Stik kanan Y"],
  buttons: [
    "A", "B", "X", "Y",
    "LB", "RB",
    "LT", "RT",
    "Back", "Start",
    "L3", "R3",
    "D-pad ↑", "D-pad ↓", "D-pad ←", "D-pad →",
    "Logitech",
  ],
};

/* LT/RT di standard mapping adalah TOMBOL dengan nilai analog 0..1, bukan axis.
   Ini sebabnya grip analog lewat axis mustahil di F310 mode X. */
export const F310_TRIGGER_LEFT = 6;
export const F310_TRIGGER_RIGHT = 7;

export function isStandardMapping(mapping) {
  return mapping === "standard";
}

export function axisLabel(index) {
  return STANDARD_LAYOUT.axes[index] || `Axis ${index}`;
}

export function buttonLabel(index) {
  return STANDARD_LAYOUT.buttons[index] || `Tombol ${index}`;
}

/* ===================== DEFAULT ===================== */

export const DEFAULT_DEADZONE = 0.12;
export const DEFAULT_EXPO = 1.6;

function defaultButtonLayer() {
  return [
    { action: "arm", button: 0, mode: "toggle" },              // A
    { action: "mode_manual", button: 1, mode: "toggle" },      // B
    { action: "emergency_stop", button: 2, mode: "toggle" },   // X
    { action: "mode_stabilize", button: 3, mode: "toggle" },   // Y
    { action: "disarm", button: 4, mode: "toggle" },           // LB
    { action: "mode_depth_hold", button: 5, mode: "toggle" },  // RB
    { action: "grip_close", button: 6, mode: "hold" },         // LT (analog)
    { action: "grip_open", button: 7, mode: "hold" },          // RT (analog)
    { action: "input_hold_set", button: 8, mode: "toggle" },   // Back
    { action: "mount_center", button: 9, mode: "toggle" },     // Start
    { action: "no_function", button: 12, mode: "toggle" },
    { action: "no_function", button: 13, mode: "toggle" },
    { action: "cam_prev", button: 14, mode: "toggle" },        // D-pad ← : CAM sebelumnya
    { action: "no_function", button: 15, mode: "toggle" },     // D-pad → : shift (lihat shiftButton)
    { action: "thruster_gain_dec", button: 10, mode: "toggle" }, // L3
    { action: "thruster_gain_inc", button: 11, mode: "toggle" }, // R3
    { action: "camera_stream", button: 16, mode: "toggle" },     // Logitech
  ];
}

function emptyButtonLayer() {
  return defaultButtonLayer().map((row) => ({
    action: "no_function",
    button: row.button,
    mode: "toggle",
  }));
}

export function defaultProfile() {
  return {
    version: SCHEMA_VERSION,
    device: { id: "", mapping: "standard", axes: STANDARD_AXIS_COUNT, buttons: STANDARD_BUTTON_COUNT },
    enabled: true,

    /* D-pad kanan (15) dipakai sebagai shift: tidak bentrok dengan aksi
       layer regular (button 15 di-nolkan jadi no_function di bawah). */
    shiftButton: 15,

    axisConfig: [
      { input: "axis 0", assigned: "Axis R", min: -1000, max: 1000, direction: "↔", deadzone: DEFAULT_DEADZONE, expo: DEFAULT_EXPO },
      { input: "axis 1", assigned: "Axis Z", min: 1000, max: -1000, direction: "↕", deadzone: DEFAULT_DEADZONE, expo: DEFAULT_EXPO },
      { input: "axis 2", assigned: "Axis Y", min: -1000, max: 1000, direction: "↔", deadzone: DEFAULT_DEADZONE, expo: DEFAULT_EXPO },
      { input: "axis 3", assigned: "Axis X", min: 1000, max: -1000, direction: "↕", deadzone: DEFAULT_DEADZONE, expo: DEFAULT_EXPO },
    ],

    buttonConfig: {
      regular: defaultButtonLayer(),
      shift: emptyButtonLayer(),
    },
  };
}

/* ===================== PEMETAAN AXIS ===================== */

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function normalizeAxis(raw) {
  return clamp(Number(raw) || 0, -1, 1);
}

/* Deadzone membuang drift stik; expo (>1) melembutkan gerakan di sekitar tengah
   tanpa mengurangi keluaran maksimum. Nilai di atas deadzone di-rescale ke 0..1
   supaya tidak ada lompatan di tepi deadzone. */
export function applyDeadzoneExpo(v, row = {}) {
  const dz = Number.isFinite(Number(row.deadzone)) ? Number(row.deadzone) : DEFAULT_DEADZONE;
  const expo = Number.isFinite(Number(row.expo)) ? Number(row.expo) : DEFAULT_EXPO;

  const mag = Math.abs(v);
  if (mag <= dz) return 0;

  const scaled = (mag - dz) / (1 - dz);
  return Math.sign(v) * Math.pow(scaled, expo);
}

/* Satu-satunya sumber kebenaran untuk mapping axis mentah -> nilai keluaran.
   Dipakai runtime maupun preview di halaman joystick, supaya angka yang dilihat
   operator persis sama dengan yang dikirim. */
export function mapAxisValue(raw, row) {
  let v = applyDeadzoneExpo(normalizeAxis(raw), row);

  // Reverse jika Min > Max
  if (Number(row.min) > Number(row.max)) v *= -1;

  const low = Math.min(Number(row.min), Number(row.max));
  const high = Math.max(Number(row.min), Number(row.max));

  return Math.round(low + ((v + 1) / 2) * (high - low));
}

/* ===================== VALIDASI ===================== */

function intInRange(value, lo, hi, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return clamp(Math.round(n), lo, hi);
}

function floatInRange(value, lo, hi, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return clamp(n, lo, hi);
}

/* Mengubah data sembarang menjadi profil yang PASTI valid.
 *
 * Berbeda dari versi lama yang hanya mengecek Number.isFinite (sehingga
 * button: 99 dan button: -1 ikut tersimpan), fungsi ini meng-clamp ke rentang
 * perangkat dan menolak konfigurasi yang mematikan sumbu secara diam-diam.
 *
 * Peringatan dikumpulkan di array `warnings` supaya UI bisa memberi tahu
 * operator apa yang diubah, bukan mengubahnya tanpa jejak.
 */
export function sanitizeProfile(data, warnings = []) {
  const fallback = defaultProfile();
  if (!data || typeof data !== "object") return fallback;

  const buttonCount = intInRange(
    data.device && data.device.buttons, 1, 64, STANDARD_BUTTON_COUNT,
  );
  const axisCount = intInRange(
    data.device && data.device.axes, 1, 16, STANDARD_AXIS_COUNT,
  );
  const maxButton = buttonCount - 1;

  /* ================= AXIS ================= */
  const srcAxis = Array.isArray(data.axisConfig) ? data.axisConfig : fallback.axisConfig;
  const seenAssigned = new Set();

  const axisConfig = fallback.axisConfig.map((def, i) => {
    const row = srcAxis[i] || {};

    let input = typeof row.input === "string" ? row.input : def.input;
    const idx = parseAxisIndex(input);
    if (idx < 0 || idx >= axisCount) {
      warnings.push(`Axis "${input}" tidak ada di perangkat ini — dipakai ${def.input}`);
      input = def.input;
    }

    let assigned = typeof row.assigned === "string" ? row.assigned : def.assigned;
    if (!AXIS_OPTIONS.includes(assigned)) {
      warnings.push(`Fungsi axis "${assigned}" tidak dikenal — dijadikan "No function"`);
      assigned = "No function";
    }
    // Duplikat: hanya yang pertama menang di readAssignedAxis, jadi sumbu kedua
    // akan mati diam-diam. Lebih baik ditolak dengan pesan.
    if (assigned !== "No function") {
      if (seenAssigned.has(assigned)) {
        warnings.push(`"${assigned}" dipetakan lebih dari sekali — ${input} dimatikan`);
        assigned = "No function";
      } else {
        seenAssigned.add(assigned);
      }
    }

    let min = intInRange(row.min, -1000, 1000, def.min);
    let max = intInRange(row.max, -1000, 1000, def.max);
    if (min === max) {
      warnings.push(`Rentang ${input} kosong (min = max) — dikembalikan ke default`);
      min = def.min;
      max = def.max;
    }

    return {
      input,
      assigned,
      min,
      max,
      direction: row.direction === "↕" ? "↕" : "↔",
      deadzone: floatInRange(row.deadzone, 0, 0.9, DEFAULT_DEADZONE),
      expo: floatInRange(row.expo, 1, 4, DEFAULT_EXPO),
    };
  });

  /* ================= BUTTON ================= */
  const srcBtnCfg = (data.buttonConfig && typeof data.buttonConfig === "object")
    ? data.buttonConfig
    : {};

  const sanitizeLayer = (layerName) => fallback.buttonConfig[layerName].map((def, i) => {
    const src = Array.isArray(srcBtnCfg[layerName]) ? srcBtnCfg[layerName] : [];
    const row = src[i] || {};

    let action = typeof row.action === "string" ? migrateButtonAction(row.action) : def.action;
    if (!BUTTON_ACTIONS.includes(action)) {
      warnings.push(`Aksi tombol "${action}" tidak dikenal — dijadikan "no_function"`);
      action = "no_function";
    }

    return {
      action,
      button: intInRange(row.button, 0, maxButton, def.button),
      mode: BUTTON_MODES.includes(row.mode) ? row.mode : "toggle",
    };
  });

  const buttonConfig = {
    regular: sanitizeLayer("regular"),
    shift: sanitizeLayer("shift"),
  };

  /* Shift tidak boleh menempel pada tombol yang sudah punya aksi di layer
     regular — kalau bentrok, menekan shift ikut menjalankan aksi itu. */
  let shiftButton = intInRange(data.shiftButton, 0, maxButton, fallback.shiftButton);
  const conflict = buttonConfig.regular.find(
    (r) => r.button === shiftButton && r.action !== "no_function",
  );
  if (conflict) {
    warnings.push(
      `Tombol shift (${buttonLabel(shiftButton)}) bentrok dengan aksi "${conflict.action}" — ` +
      `dikembalikan ke ${buttonLabel(fallback.shiftButton)}`,
    );
    shiftButton = fallback.shiftButton;
  }

  return {
    version: SCHEMA_VERSION,
    device: {
      id: typeof (data.device && data.device.id) === "string" ? data.device.id : "",
      mapping: (data.device && data.device.mapping) === "standard" ? "standard" : "",
      axes: axisCount,
      buttons: buttonCount,
    },
    enabled: data.enabled !== false,
    shiftButton,
    axisConfig,
    buttonConfig,
  };
}

export function parseAxisIndex(inputName) {
  const m = String(inputName).match(/axis\s+(\d+)/i);
  return m ? Number(m[1]) : -1;
}

/* ===================== MIGRASI ===================== */

/* Profil tanpa `version` dianggap v1. Rantai migrasi sengaja dibuat bertahap
   supaya penambahan v3 nanti tinggal menyambung, bukan menulis ulang. */
export function migrateProfile(data, warnings = []) {
  if (!data || typeof data !== "object") return defaultProfile();

  let cfg = { ...data };
  const from = Number.isFinite(Number(cfg.version)) ? Number(cfg.version) : 1;

  if (from < 2) {
    warnings.push("Profil versi lama (v1) dimigrasikan ke v2");

    // v1 tidak punya deadzone/expo per-axis: isi dengan nilai yang dulu hardcoded.
    if (Array.isArray(cfg.axisConfig)) {
      cfg.axisConfig = cfg.axisConfig.map((row) => ({
        deadzone: DEFAULT_DEADZONE,
        expo: DEFAULT_EXPO,
        ...row,
      }));
    }

    // v1 tidak punya blok device; asumsikan standard (satu-satunya yang didukung).
    if (!cfg.device) {
      cfg.device = {
        id: "",
        mapping: "standard",
        axes: STANDARD_AXIS_COUNT,
        buttons: STANDARD_BUTTON_COUNT,
      };
    }

    cfg.version = 2;
  }

  if (from < 3) {
    /* Seluruh blok D-pad (tombol 12-15) ditulis ulang, bukan hanya baris yang
       kebetulan sudah memegang aksi yang tepat.

       Alasannya: mengatur kedalaman lewat D-pad ↑/↓ adalah inti dari mode Alt
       Hold yang baru. Kalau migrasi hanya memindahkan binding lama, profil yang
       sudah disesuaikan operator (mis. D-pad dipakai input_hold_set) akan
       kehilangan kontrol kedalaman sepenuhnya tanpa pesan apa pun — kegagalan
       diam yang justru paling berbahaya. Aksi yang tergusur dilaporkan di
       `warnings` supaya operator bisa memasang ulang di halaman Joystick. */
    const DPAD_V3 = {
      12: { action: "depth_set", mode: "toggle" },          // ↑ SET kedalaman
      13: { action: "depth_hold_toggle", mode: "toggle" },  // ↓ depth-set ON/OFF
      14: { action: "mount_tilt_up", mode: "hold" },     // ←
      15: { action: "mount_tilt_down", mode: "hold" },   // →
    };

    const displaced = new Set();

    for (const layer of ["regular", "shift"]) {
      const rows = cfg.buttonConfig?.[layer];
      if (!Array.isArray(rows)) continue;
      cfg.buttonConfig[layer] = rows.map((row) => {
        const next = row && DPAD_V3[Number(row.button)];
        if (!next) return row;
        if (row.action !== next.action && row.action !== "no_function") {
          displaced.add(row.action);
        }
        return { ...row, ...next };
      });
    }

    warnings.push("D-pad ditata ulang: ↑/↓ untuk depth-set, ←/→ mount tilt");
    if (displaced.size) {
      warnings.push(`Aksi berikut lepas dari D-pad, pasang ulang bila perlu: ${[...displaced].join(", ")}`);
    }

    cfg.version = 3;
  }

  if (from < 6) {
    /* ACTION_MIGRATION sudah memetakan gain_dec/gain_inc ke depth_set /
       depth_hold_toggle di posisi mana pun, tapi TIDAK menyentuh `mode`:
       keduanya dulu "repeat" (tahan = geser terus), sedangkan tombol SET dan
       ON/OFF sekali-pencet. Kalau mode lama dibiarkan, menahan tombol akan
       mengirim puluhan perintah — backend membatasinya ke 2 Hz, tapi toggle
       ON/OFF yang berulang tetap berarti saklar berkedip-kedip. */
    for (const layer of ["regular", "shift"]) {
      const rows = cfg.buttonConfig?.[layer];
      if (!Array.isArray(rows)) continue;
      cfg.buttonConfig[layer] = rows.map((row) => {
        const action = migrateButtonAction(row?.action);
        if (action !== "depth_set" && action !== "depth_hold_toggle") return row;
        return { ...row, action, mode: "toggle" };
      });
    }

    warnings.push("Depth: trim ±0.05 m diganti tombol SET (D-pad ↑) dan ON/OFF (D-pad ↓)");
    cfg.version = 6;
  }

  if (from < 7) {
    for (const layer of ["regular", "shift"]) {
      const rows = cfg.buttonConfig?.[layer];

      if (!Array.isArray(rows)) continue;

      cfg.buttonConfig[layer] = rows.map((row) => {
        if (
          Number(row?.button) === 16 &&
          (!row.action || row.action === "no_function")
        ) {
          return {
            ...row,
            action: "camera_stream",
            mode: "toggle",
          };
        }

        return row;
      });
    }

    warnings.push("Button 16 ditambahkan sebagai Camera Stream");
    cfg.version = 7;
  }

if (from < 9) {
  for (const layer of ["regular", "shift"]) {
    const rows = cfg.buttonConfig?.[layer];

    if (!Array.isArray(rows)) continue;

    cfg.buttonConfig[layer] = rows.map((row) => {
      if (!row) return row;

      if (row.action === "depth_set") {
        return {
          ...row,
          action: "depth_up",
          mode: "toggle",
        };
      }

      if (row.action === "depth_hold_toggle") {
        return {
          ...row,
          action: "depth_down",
          mode: "toggle",
        };
      }

      return row;
    });
  }

  warnings.push("Depth: depth_set/depth_hold_toggle diganti menjadi depth_up/depth_down");
  cfg.version = 9;
}

  // sanitizeProfile menjalankan migrateButtonAction dan seluruh clamping.
  return sanitizeProfile(cfg, warnings);
}

/* Muat data sembarang (file import, isi disk, pesan WS) menjadi profil valid,
   sekaligus mengembalikan daftar perubahan yang dilakukan. */
export function loadProfile(data) {
  const warnings = [];
  const profile = migrateProfile(data, warnings);
  return { profile, warnings };
}
