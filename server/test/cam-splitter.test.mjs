// Penjaga pemotong multipart di proxy /cam (server.js).
//
// Sebelumnya proxy membuang chunk TCP mentah saat klien lambat, artinya byte bisa
// hilang di tengah JPEG dan browser menerima frame cacat. Sekarang stream dipotong
// di batas multipart supaya yang dibuang selalu frame utuh. Test ini mengunci
// perilaku pemotongnya, termasuk kasus delimiter yang terbelah antar chunk TCP.
//
// Jalankan:  cd server && npm test   (atau: node test/cam-splitter.test.mjs)

import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, "..", "server.js"), "utf8");

// server.js menyalakan server saat di-require, jadi ambil fungsinya dari teks.
const start = src.indexOf("function camSplitter(");
assert.ok(start >= 0, "camSplitter tidak ditemukan di server.js");
let depth = 0, end = -1;
for (let i = src.indexOf("{", start); i < src.length; i++) {
  if (src[i] === "{") depth++;
  else if (src[i] === "}" && --depth === 0) { end = i + 1; break; }
}
const camSplitter = new Function("Buffer", `${src.slice(start, end)}; return camSplitter;`)(Buffer);

const CT = "multipart/x-mixed-replace; boundary=myboundary";
const part = (n) => Buffer.from(`--myboundary\r\nContent-Type: image/jpeg\r\n\r\nJPEG${n}\r\n`);

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

test("memotong tepat di batas part, bukan di tengah JPEG", () => {
  const out = [];
  const feed = camSplitter(CT, (b) => out.push(b.toString()));
  feed(Buffer.concat([part(1), part(2), part(3)]));
  // part terakhir masih ditahan (delimiter berikutnya belum terlihat) — itu benar
  assert.deepStrictEqual(out.map((s) => s.match(/JPEG\d/)[0]), ["JPEG1", "JPEG2"]);
  assert.ok(out.every((s) => s.startsWith("--myboundary")), "tiap part harus utuh dari delimiter");
});

test("delimiter yang terbelah antar chunk TCP tetap dikenali", () => {
  const out = [];
  const feed = camSplitter(CT, (b) => out.push(b.toString()));
  const all = Buffer.concat([part(1), part(2)]);
  // belah tepat di tengah delimiter part kedua
  const cut = all.indexOf("--myboundary", 1) + 5;
  feed(all.subarray(0, cut));
  assert.strictEqual(out.length, 0, "belum boleh keluar: part 1 belum ditutup delimiter utuh");
  feed(all.subarray(cut));
  assert.strictEqual(out.length, 1);
  assert.ok(out[0].includes("JPEG1") && !out[0].includes("JPEG2"));
});

test("byte per byte memberi hasil sama dengan satu chunk besar", () => {
  const whole = [], drip = [];
  const a = camSplitter(CT, (b) => whole.push(b.toString()));
  const b = camSplitter(CT, (x) => drip.push(x.toString()));
  const all = Buffer.concat([part(1), part(2), part(3)]);
  a(all);
  for (const byte of all) b(Buffer.from([byte]));
  assert.deepStrictEqual(drip, whole);
});

test("boundary ber-tanda-kutip di content-type ikut terbaca", () => {
  assert.ok(camSplitter('multipart/x-mixed-replace; boundary="myboundary"', () => {}));
});

test("content-type non-multipart -> null (pemanggil pakai jalur byte biasa)", () => {
  assert.strictEqual(camSplitter("image/jpeg", () => {}), null);
  assert.strictEqual(camSplitter(undefined, () => {}), null);
});

test("stream tanpa delimiter tidak menumpuk buffer tanpa batas", () => {
  const out = [];
  const feed = camSplitter(CT, (b) => out.push(b.length));
  const junk = Buffer.alloc(1 << 20, 0x41);   // 1 MB, tanpa delimiter
  for (let i = 0; i < 9; i++) feed(junk);     // 9 MB > ambang 8 MB
  assert.ok(out.length > 0, "buffer harus di-flush, bukan tumbuh selamanya");
});

let failed = 0;
for (const { name, fn } of tests) {
  try { fn(); console.log(`  ok  ${name}`); }
  catch (e) { failed++; console.error(`FAIL  ${name}\n      ${e.message}`); }
}
console.log(failed ? `\n${failed} gagal dari ${tests.length}` : `\n${tests.length} lulus`);
process.exit(failed ? 1 : 0);
