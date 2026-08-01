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

export const SCHEMA_VERSION = 2;

export const BUTTON_ACTIONS = [
  "no_function",
  "arm",
  "disarm",
  "mode_manual",
  "mode_stabilize",
  "mode_depth_hold",
  // ACRO tidak ter-bind di defaultButtonLayer(): ke-16 tombol pad sudah
  // terpakai, dan mode ini terlalu berisiko untuk menggeser binding yang sudah
  // dihafal operator. Bind manual lewat halaman Setup > Joystick.
  "mode_acro",
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
    { action: "disarm", button: 1, mode: "toggle" },           // B
    { action: "mode_manual", button: 2, mode: "toggle" },      // X
    { action: "mode_stabilize", button: 3, mode: "toggle" },   // Y
    { action: "mode_depth_hold", button: 5, mode: "toggle" },  // RB
    { action: "grip_close", button: 6, mode: "hold" },         // LT (analog)
    { action: "grip_open", button: 7, mode: "hold" },          // RT (analog)
    { action: "input_hold_set", button: 8, mode: "toggle" },   // Back
    { action: "mount_center", button: 9, mode: "toggle" },     // Start
    { action: "mount_tilt_up", button: 12, mode: "hold" },     // D-pad ↑
    { action: "mount_tilt_down", button: 13, mode: "hold" },   // D-pad ↓
    { action: "gain_dec", button: 14, mode: "toggle" },        // D-pad ←
    { action: "gain_inc", button: 15, mode: "toggle" },        // D-pad →
    { action: "lights_dimmer", button: 10, mode: "hold" },     // L3
    { action: "lights_brighter", button: 11, mode: "hold" },   // R3
    { action: "no_function", button: 16, mode: "toggle" },     // Logitech
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

    /* LB dipakai sebagai shift: mudah ditahan jempol kiri sambil tetap
       memegang kedua stik, dan tidak bentrok dengan aksi layer regular. */
    shiftButton: 4,

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
      mode: row.mode === "hold" ? "hold" : "toggle",
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
