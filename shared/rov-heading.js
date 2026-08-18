/* Padanan JS dari rov_heading.py — HANYA bagian yang dibutuhkan GUI untuk
 * menggambar heading bug: error ter-wrap dan lebar deadband-nya.
 *
 * Hukum kendalinya sendiri (heading_bias) sengaja TIDAK diduplikasi ke sini.
 * Yang mengoreksi heading adalah Pi; GUI cuma menampilkan. Menyalin gain ke
 * browser berarti dua sumber kebenaran untuk satu hukum kendali.
 *
 * ES module tanpa build step, dipakai browser (public/js/app.js) dan node
 * (server/test/heading-error.test.mjs) — sama polanya dengan shared/rov-modes.js.
 */

/* Error di bawah ini dianggap nol oleh wahana, jadi GUI menandainya ON TARGET.
 * WAJIB sama dengan HEADING_DEADBAND_DEG di rov_heading.py:39 — kalau di sana
 * diubah, ubah di sini juga, kalau tidak indikator ON TARGET berbohong soal
 * kapan thruster yaw benar-benar berhenti mengoreksi. */
export const HEADING_DEADBAND_DEG = 2.0;

/** Error heading ter-wrap ke rentang [-180, 180) derajat.
 *
 * Positif = wahana perlu berputar SEARAH JARUM JAM untuk mencapai target.
 * null kalau salah satu argumen bukan angka — pemanggil memakai ini untuk
 * menyembunyikan bug, bukan menggambarnya di 0°.
 *
 * Catatan: `%` di JS mempertahankan tanda operand kiri (beda dengan Python),
 * jadi butuh normalisasi ganda. Tanpa itu target 10° / heading 350° menghasilkan
 * -340 bukan +20, dan bug akan melompat ke sisi yang salah tiap lewat utara.
 */
export function headingError(target, actual) {
  if (!Number.isFinite(target) || !Number.isFinite(actual)) return null;
  const err = target - actual;
  return (((err + 180) % 360) + 360) % 360 - 180;
}
