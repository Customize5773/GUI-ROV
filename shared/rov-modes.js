/* Nama & gating pilot mode ArduSub — SATU sumber kebenaran sisi JS, dipakai
 * baik oleh browser (public/js/app.js) maupun test Node (server/test/*).
 *
 * Padanan sisi Python: rov_modes.py. Jaga keduanya tetap sinkron kalau ada
 * mode baru — tidak ada mekanisme otomatis yang mengecek ini lintas bahasa.
 */

// data-mode tab GUI / value command pilot_mode -> nama mode ArduSub (HEARTBEAT).
export const PILOT_MODE_MAP = {
  manual: "MANUAL",
  stabilize: "STABILIZE",
  depth_hold: "ALT_HOLD",
  acro: "ACRO",
};

// Nama mode ArduSub (dari HEARTBEAT) -> data-mode tab GUI.
export const ARDUSUB_MODE_TO_TAB = {
  MANUAL: "manual",
  STABILIZE: "stabilize",
  ALT_HOLD: "depth_hold",
  ACRO: "acro",
};

// Mode yang perlu peringatan menonjol (padanan rov_modes.RISKY_MODES).
export const RISKY_ARDUSUB_MODES = new Set(["ACRO"]);

export const ACRO_CONFIRM =
  "Masuk mode ACRO?\n\n" +
  "• Tidak ada stabilisasi attitude — stik memerintahkan RATE, bukan sudut.\n" +
  "• Throttle netral TIDAK menahan kedalaman.\n" +
  "• Depth hold (gain +/-) dinonaktifkan selama ACRO.";
