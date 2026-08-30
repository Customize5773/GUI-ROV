/* Worker decode QR + skor fokus.
 *
 * Seluruh pipeline yang dulu jalan di main thread dipindahkan ke sini APA ADANYA:
 * penskalaan ke maxSide, drawImage, getImageData, jsQR(attemptBoth), dua pass
 * adaptive-threshold (konstanta 3 dan 7), dan sharpnessScore. Tidak ada tier yang
 * dibuang dan tidak ada konstanta yang diubah — yang berubah hanya thread-nya.
 *
 * Main thread cukup mengirim ImageBitmap (transferable, tanpa salinan); seluruh
 * sentuhan piksel terjadi di sini. Lihat decodeClientQr() di core.js.
 */

importScripts("../vendor/jsqr.min.js");

let canvas = null;   // OffscreenCanvas, dipakai ulang antar frame
let ctx = null;

/* --- dipindahkan dari core.js:250-286, tanpa perubahan --- */
function clientQrAdaptive(imageData, constant) {
  const w = imageData.width, h = imageData.height;
  const gray = new Uint8Array(w * h);
  const stride = w + 1;
  const integral = new Int32Array((w + 1) * (h + 1));
  const src = imageData.data;
  for (let y = 0; y < h; y++) {
    let row = 0;
    for (let x = 0; x < w; x++) {
      const si = (y * w + x) * 4;
      const value = Math.round(0.299 * src[si] + 0.587 * src[si + 1] + 0.114 * src[si + 2]);
      const gi = y * w + x;
      gray[gi] = value;
      row += value;
      integral[(y + 1) * stride + x + 1] = integral[y * stride + x + 1] + row;
    }
  }

  const out = new Uint8ClampedArray(src.length);
  const radius = 15;
  for (let y = 0; y < h; y++) {
    const y0 = Math.max(0, y - radius), y1 = Math.min(h - 1, y + radius);
    for (let x = 0; x < w; x++) {
      const x0 = Math.max(0, x - radius), x1 = Math.min(w - 1, x + radius);
      const area = (x1 - x0 + 1) * (y1 - y0 + 1);
      const sum = integral[(y1 + 1) * stride + x1 + 1]
        - integral[y0 * stride + x1 + 1]
        - integral[(y1 + 1) * stride + x0]
        + integral[y0 * stride + x0];
      const black = gray[y * w + x] < sum / area - constant;
      const value = black ? 0 : 255;
      const oi = (y * w + x) * 4;
      out[oi] = value; out[oi + 1] = value; out[oi + 2] = value; out[oi + 3] = 255;
    }
  }
  return out;
}

/* --- dipindahkan dari app.js:761-777, tanpa perubahan --- */
function sharpnessScore(imgData, w, h) {
  const gray = new Float32Array(w * h);
  const d = imgData.data;
  for (let i = 0, p = 0; i < d.length; i += 4, p++) {
    gray[p] = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
  }
  let sum = 0, sumSq = 0, n = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const idx = y * w + x;
      const lap = 4 * gray[idx] - gray[idx - 1] - gray[idx + 1] - gray[idx - w] - gray[idx + w];
      sum += lap; sumSq += lap * lap; n++;
    }
  }
  const mean = sum / n;
  return sumSq / n - mean * mean;
}

self.onmessage = (e) => {
  const { id, bitmap, maxSide, wantSharpness } = e.data;
  let qr = null, sharpness = null;

  try {
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));

    if (!canvas || canvas.width !== w || canvas.height !== h) {
      canvas = new OffscreenCanvas(w, h);
      ctx = canvas.getContext("2d", { willReadFrequently: true });
    }
    ctx.drawImage(bitmap, 0, 0, w, h);
    const image = ctx.getImageData(0, 0, w, h);

    /* satu pembacaan piksel dipakai untuk KEDUA hasil — dulu app.js memanggil
       getImageData dua kali atas kanvas yang sama. Angkanya identik. */
    if (wantSharpness) sharpness = sharpnessScore(image, w, h);

    const run = (data) => self.jsQR(data, w, h, { inversionAttempts: "attemptBoth" });
    let code = run(image.data);
    if (!code) {
      for (const constant of [3, 7]) {
        code = run(clientQrAdaptive(image, constant));
        if (code) break;
      }
    }
    // jsQR mengembalikan objek dengan referensi tak bisa di-clone; kirim yang dipakai saja
    if (code) qr = { data: code.data, location: code.location };
  } catch (_) {
    // frame belum siap / bitmap rusak — perlakukan seperti "tidak ada QR"
  } finally {
    bitmap.close();
  }

  self.postMessage({ id, qr, sharpness });
};
