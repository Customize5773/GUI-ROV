/* Nama & gating pilot mode ArduSub — SATU sumber kebenaran sisi JS, dipakai
 * baik oleh browser (public/js/app.js) maupun test Node (server/test/*).
 *
 * Padanan sisi Python: rov_modes.py. Jaga keduanya tetap sinkron kalau ada
 * mode baru — tidak ada mekanisme otomatis yang mengecek ini lintas bahasa.
 */

/* data-mode tab GUI / value command pilot_mode -> nama mode ArduSub (HEARTBEAT).
 *
 * "stabilize" sengaja menunjuk ALT_HOLD juga: STABILIZE ArduSub menstabilkan
 * attitude tapi TIDAK menahan kedalaman, sementara ALT_HOLD memberi keduanya.
 * Tab STABILIZE sudah dihapus dari dashboard; aliasnya dipertahankan supaya
 * profil joystick tersimpan dengan aksi `mode_stabilize` tetap bekerja.
 * Alasan lengkapnya ada di docstring rov_modes.py. */
export const PILOT_MODE_MAP = {
  manual: "MANUAL",
  stabilize: "ALT_HOLD",
  depth_hold: "ALT_HOLD",
  /* "poshold" adalah OVERLAY sisi Pi di atas ALT_HOLD (tahan kedalaman + tahan
   * heading), BUKAN mode POSHOLD firmware ArduSub — itu butuh estimasi posisi
   * horizontal dari EKF yang tidak ada di bawah air. Ia TIDAK menahan posisi
   * x/y. Alasan lengkapnya di docstring rov_modes.py. */
  poshold: "ALT_HOLD",
  acro: "ACRO",
};

/* Nama mode ArduSub (dari HEARTBEAT) -> data-mode tab GUI.
 *
 * STABILIZE TIDAK ada di sini: tidak ada lagi tab untuknya, jadi kalau wahana
 * berada di sana (saklar RC / GCS lain) tidak ada tab yang menyala dan
 * #modeActual menampilkan "STABILIZE" apa adanya — lihat syncModeTabs().
 *
 * "poshold" juga TIDAK ada di sini, dan itu disengaja: peta ini satu-arah,
 * sedangkan ALT_HOLD bisa berarti dua tab (Alt Hold atau Pos Hold). Yang
 * memisahkan keduanya adalah flag `poshold` di telemetri — lihat syncModeTabs()
 * di app.js. */
export const ARDUSUB_MODE_TO_TAB = {
  MANUAL: "manual",
  ALT_HOLD: "depth_hold",
  ACRO: "acro",
};

// Mode yang perlu konfirmasi sebelum diminta (padanan rov_modes.RISKY_MODES).
export const RISKY_ARDUSUB_MODES = new Set(["ACRO"]);

/* Peringatan untuk mode yang tidak bisa diminta dari GUI tapi masih mungkin
 * dilaporkan HEARTBEAT. Padanan rov_modes.MODE_WARNINGS. */
export const ACTUAL_MODE_WARNINGS = {
  ACRO: "TANPA STABILISASI",
  STABILIZE: "TANPA DEPTH HOLD",
};

export const ACRO_CONFIRM =
  "Masuk mode ACRO?\n\n" +
  "• Tidak ada stabilisasi attitude — stik memerintahkan RATE, bukan sudut.\n" +
  "• Throttle netral TIDAK menahan kedalaman.\n" +
  "• Depth hold (gain +/-) dinonaktifkan selama ACRO.";
