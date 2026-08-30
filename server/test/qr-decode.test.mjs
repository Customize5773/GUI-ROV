// Penjaga pemindahan pipeline QR dari main thread ke Web Worker.
//
// clientQrAdaptive + sharpnessScore kini ada DUA salinan:
//   public/js/qr-worker.js  -> jalur utama (worker)
//   public/js/core.js       -> fallback main-thread (browser tanpa OffscreenCanvas)
// Test ini memastikan keduanya (a) masih ada dan (b) memberi hasil yang IDENTIK
// pada gambar sintetis — kalau seseorang menyetel salah satu saja, ini merah.
//
// Jalankan:  cd server && npm test    (atau: node test/qr-decode.test.mjs)

import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const pub = path.join(here, "..", "..", "public", "js");

/* Ambil satu deklarasi `function nama(...) { ... }` dari sebuah file sumber dan
   ubah jadi fungsi hidup. Menghitung kurung kurawal, bukan regex serakah. */
function extractFn(file, name) {
  const src = readFileSync(file, "utf8");
  const start = src.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} tidak ditemukan di ${path.basename(file)}`);
  let i = src.indexOf("{", start), depth = 0, end = -1;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) { end = i + 1; break; }
  }
  assert.ok(end > 0, `kurung ${name} tidak seimbang di ${path.basename(file)}`);
  return new Function(`${src.slice(start, end)}; return ${name};`)();
}

/* Gambar uji tetap: DUA kotak gelap identik (kiri & kanan) di atas latar dengan
   gradien iluminasi landai — persis glare/caustic kolam. Dipilih supaya tidak ada
   satu pun ambang GLOBAL yang bisa benar: kotak kiri (~82) lebih gelap daripada
   latar kiri (~100), tapi kotak kanan (~126) justru lebih TERANG dari latar kiri.
   Hanya threshold lokal yang bisa mengklasifikasi keempatnya dengan benar.
   Kotak sengaja lebih sempit dari jendela 31 px (radius 15) supaya jendela di
   tengah kotak masih menangkap latar sekitarnya. */
const W = 160, H = 120;
const BOX_Y0 = 52, BOX_Y1 = 68;
const BOX_L = [27, 43], BOX_R = [117, 133];

function makeImage(w, h) {
  const data = new Uint8ClampedArray(w * h * 4);
  const inX = ([a, b], x) => x >= a && x <= b;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const inBox = y >= BOX_Y0 && y <= BOX_Y1 && (inX(BOX_L, x) || inX(BOX_R, x));
      const illum = 90 + 90 * (x / w);           // gradien landai: terang ke kanan
      const v = Math.round(inBox ? illum * 0.75 : illum);
      const i = (y * w + x) * 4;
      data[i] = data[i + 1] = data[i + 2] = v;
      data[i + 3] = 255;
    }
  }
  return { data, width: w, height: h };
}

const img = makeImage(W, H);
const px = (out, x, y) => out[(y * W + x) * 4];

const adaptiveWorker = extractFn(path.join(pub, "qr-worker.js"), "clientQrAdaptive");
const adaptiveCore   = extractFn(path.join(pub, "core.js"), "clientQrAdaptive");
const sharpWorker    = extractFn(path.join(pub, "qr-worker.js"), "sharpnessScore");
const sharpCore      = extractFn(path.join(pub, "core.js"), "sharpnessScoreLocal");

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

test("clientQrAdaptive: worker dan fallback identik piksel per piksel", () => {
  const a = adaptiveWorker(img, 3);
  const b = adaptiveCore(img, 3);
  assert.strictEqual(a.length, b.length);
  for (let i = 0; i < a.length; i++) {
    assert.strictEqual(a[i], b[i], `beda di indeks ${i}`);
  }
});

test("clientQrAdaptive: konstanta 3 dan 7 memberi hasil berbeda", () => {
  // kalau sama, argumen `constant` diam-diam tidak terpakai lagi
  const a = adaptiveWorker(img, 3);
  const b = adaptiveWorker(img, 7);
  assert.ok(a.some((v, i) => v !== b[i]), "constant tidak berpengaruh");
});

test("clientQrAdaptive: threshold LOKAL, bukan global", () => {
  // Tidak ada ambang global yang bisa benar di gambar ini (lihat makeImage):
  // kotak kanan lebih terang daripada latar kiri. Adaptive harus benar keempatnya.
  const out = adaptiveWorker(img, 3);
  const midY = (BOX_Y0 + BOX_Y1) >> 1;
  assert.strictEqual(px(out, 35, midY), 0, "kotak kiri seharusnya hitam");
  assert.strictEqual(px(out, 125, midY), 0, "kotak kanan (lebih terang dari latar kiri) seharusnya hitam");
  assert.strictEqual(px(out, 80, 15), 255, "latar seharusnya putih");
  assert.strictEqual(px(out, 12, 15), 255, "latar sisi gelap seharusnya tetap putih");
});

test("sharpnessScore: worker dan fallback memberi angka sama persis", () => {
  assert.strictEqual(sharpWorker(img, W, H), sharpCore(img, W, H));
});

test("sharpnessScore: tepi tajam skornya jauh di atas bidang rata", () => {
  const flat = { data: new Uint8ClampedArray(W * H * 4).fill(128), width: W, height: H };
  assert.ok(sharpWorker(img, W, H) > sharpWorker(flat, W, H) + 1);
});

let failed = 0;
for (const { name, fn } of tests) {
  try { fn(); console.log(`  ok  ${name}`); }
  catch (e) { failed++; console.error(`FAIL  ${name}\n      ${e.message}`); }
}
console.log(failed ? `\n${failed} gagal dari ${tests.length}` : `\n${tests.length} lulus`);
process.exit(failed ? 1 : 0);
