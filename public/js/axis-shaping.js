/* Pembentukan respons stik: deadzone, expo, dan rate-limit.
 *
 * Modul ini sengaja MURNI (tanpa DOM, tanpa Gamepad API, tanpa state) supaya:
 *   - bisa di-import Node untuk unit test (`node --test`), dan
 *   - dipakai satu kali di jalur baca axis, sehingga preview di halaman
 *     Joystick dan nilai yang benar-benar dikirim ke ROV tidak bisa berbeda.
 */

export function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

/* Deadzone dengan RESCALE, bukan sekadar pemotongan.
 *
 * Pemotongan biasa membuat output melompat dari 0 ke `dz` begitu stik lewat
 * ambang. Dengan rescale, keluaran mulai dari 0 tepat di tepi deadzone lalu
 * naik mulus sampai ±1 — jadi gerakan halus tetap halus di sekitar ambang. */
export function applyDeadzone(v, dz) {
  const x = clamp(Number(v) || 0, -1, 1);
  const d = clamp(Number(dz) || 0, 0, 0.95);

  const mag = Math.abs(x);
  if (mag <= d) return 0;

  return Math.sign(x) * ((mag - d) / (1 - d));
}

/* Kurva expo: campuran linier dan pangkat tiga.
 *
 * expo = 0 -> linier. Makin mendekati 1, makin landai di sekitar tengah
 * sehingga koreksi kecil jadi presisi, tanpa mengurangi thrust maksimum
 * (ujung ±1 selalu tetap ±1). */
export function applyExpo(v, expo) {
  const x = clamp(Number(v) || 0, -1, 1);
  const e = clamp(Number(expo) || 0, 0, 1);

  return (1 - e) * x + e * x * x * x;
}

/* raw gamepad (-1..1) -> nilai terbentuk (-1..1). */
export function shapeAxis(raw, { deadzone = 0, expo = 0 } = {}) {
  return applyExpo(applyDeadzone(raw, deadzone), expo);
}

/* Batasi laju perubahan supaya thrust tidak melompat (dan arus baterai tidak
 * menyentak) saat stik dihentak. dt dalam detik. */
export function slewToward(current, target, maxPerSec, dt) {
  const cur = Number(current) || 0;
  const tgt = Number(target) || 0;
  const rate = Number(maxPerSec) || 0;
  const step = rate * (Number(dt) || 0);

  if (!(step > 0)) return tgt;   // rate/dt tidak masuk akal -> jangan hambat

  const delta = tgt - cur;
  if (Math.abs(delta) <= step) return tgt;

  return cur + Math.sign(delta) * step;
}
