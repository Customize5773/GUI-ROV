/* Nama & gating pilot mode ArduSub — SATU sumber kebenaran sisi JS, dipakai
 * baik oleh browser (public/js/app.js) maupun test Node (server/test/*).
 *
 * Padanan sisi Python: rov_modes.py. Jaga keduanya tetap sinkron kalau ada
 * mode baru — tidak ada mekanisme otomatis yang mengecek ini lintas bahasa.
 */

/* data-mode tab GUI / value command pilot_mode -> nama mode ArduSub (HEARTBEAT).
 *
 * Alasan lengkap tiap pemetaan ada di docstring rov_modes.py. */
export const PILOT_MODE_MAP = {
  manual: "MANUAL",
  stabilize: "STABILIZE",
  depth_hold: "ALT_HOLD",
  /* "poshold" adalah OVERLAY sisi Pi di atas ALT_HOLD (tahan kedalaman + tahan
   * heading), BUKAN mode POSHOLD firmware ArduSub — itu butuh estimasi posisi
   * horizontal dari EKF yang tidak ada di bawah air. Ia TIDAK menahan posisi
   * x/y. Alasan lengkapnya di docstring rov_modes.py. */
  poshold: "ALT_HOLD",
};

/* Nama mode ArduSub (dari HEARTBEAT) -> data-mode tab GUI.
 *
 * "poshold" TIDAK ada di sini, dan itu disengaja: peta ini satu-arah,
 * sedangkan ALT_HOLD bisa berarti dua tab (Alt Hold atau Pos Hold). Yang
 * memisahkan keduanya adalah flag `poshold` di telemetri — lihat syncModeTabs()
 * di app.js. */
export const ARDUSUB_MODE_TO_TAB = {
  MANUAL: "manual",
  STABILIZE: "stabilize",
  ALT_HOLD: "depth_hold",
};

/* Mode yang jadi syarat bias depth-set, sehingga bias throttle punya sesuatu
 * untuk dikoreksi (atau, di STABILIZE, jadi satu-satunya yang mendorong).
 * Padanan rov_modes.DEPTH_HOLD_MODES.
 *
 * BUKAN saklar depth-set: berada di STABILIZE tidak menyalakan depth-set. GUI
 * memakainya hanya untuk membedakan label ARMED (saklar hidup, mode belum
 * benar) dari HOLDING (bias benar-benar mengalir). */
export const DEPTH_HOLD_MODES = new Set(["STABILIZE"]);
