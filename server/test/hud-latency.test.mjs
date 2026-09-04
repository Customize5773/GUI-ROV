/* Penjaga dua jalur panas HUD di public/js/app.js.

   app.js menyentuh DOM saat dimuat, jadi tidak bisa di-import. Fungsinya
   diambil sebagai teks lalu dijalankan dengan stub — pola yang sama dipakai
   cam-splitter.test.mjs.

   Jalankan:  node server/test/hud-latency.test.mjs
*/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, "..", "..", "public", "js", "app.js"), "utf8");

function extract(signature) {
  const start = src.indexOf(signature);
  assert.ok(start >= 0, `${signature} tidak ditemukan di app.js`);
  let depth = 0;
  for (let i = src.indexOf("{", start); i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`kurung ${signature} tidak seimbang`);
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

/* ---------------------------- depth tape ---------------------------- */

// Offset mark ditulis sekali di buildTape; ambil ekspresinya dari sumber
// supaya test ikut gagal kalau rumusnya diubah tanpa memperbarui updateTape.
const topExpr = /mark\.style\.top = \(([^;]+?)\) \+ "px";/.exec(src);
assert.ok(topExpr, "ekspresi mark.style.top tidak ditemukan di buildTape");
const markOffset = new Function("m", "TAPE", `return ${topExpr[1]};`);

const updateTape = (els, TAPE, tapeHeight) =>
  new Function("els", "TAPE", "num", "tapeHeight",
    `${extract("function updateTape(")}; return updateTape;`,
  )(els, TAPE, (v, d) => Number(v).toFixed(d), tapeHeight);

function tapeStub(height) {
  const scale = { style: {}, parentElement: { clientHeight: height } };
  return { tapeScale: scale, tapeVal: { textContent: "" } };
}

const shiftOf = (els) => {
  const m = /translateY\((-?[\d.]+)px\)/.exec(els.tapeScale.style.transform);
  assert.ok(m, `transform tidak terbaca: ${els.tapeScale.style.transform}`);
  return Number(m[1]);
};

test("posisi mark identik dengan rumus lama (top = h/2 + (m - depth) * px)", () => {
  const H = 300;
  for (const TAPE of [
    { min: -0.2, max: 1.2, minor: 0.1, major: 0.5, px: 200 },   // kolam KKI 0,9 m
    { min: -0.5, max: 3.5, minor: 0.5, major: 1, px: 90 },
    { min: -1, max: 7, minor: 1, major: 2, px: 48 },
  ]) {
    for (const depth of [0, 0.37, 0.9, 2.5, -0.1]) {
      const els = tapeStub(H);
      updateTape(els, TAPE, 0)(depth);
      const shift = shiftOf(els);
      for (let m = TAPE.min; m <= TAPE.max + 1e-9; m += TAPE.minor) {
        const baru = markOffset(m, TAPE) + shift;
        const lama = H / 2 + (m - depth) * TAPE.px;
        assert.ok(Math.abs(baru - lama) < 1e-6,
          `m=${m} depth=${depth}: ${baru} != ${lama}`);
      }
    }
  }
});

test("tinggi tape dibaca sekali, tidak tiap paket telemetri", () => {
  const els = tapeStub(300);
  let reads = 0;
  Object.defineProperty(els.tapeScale.parentElement, "clientHeight", {
    get() { reads++; return 300; },
  });
  // tapeHeight menjadi variabel lokal fungsi hasil ekstraksi, jadi cache-nya
  // hanya bisa diamati dalam satu instance.
  const tick = updateTape(els, { min: -0.2, px: 200 }, 0);
  for (let i = 0; i < 20; i++) tick(i * 0.05);
  assert.equal(reads, 1, `clientHeight dibaca ${reads}x — harusnya 1x (reflow paksa)`);
});

test("nilai kedalaman tetap tampil 2 desimal", () => {
  const els = tapeStub(300);
  updateTape(els, { min: -0.2, px: 200 }, 0)(0.375);
  assert.equal(els.tapeVal.textContent, "0.38 m");
});

/* ---------------------------- readout LATENCY ---------------------------- */

function latency() {
  const els = { lat: { textContent: "" } };
  const body = src.slice(src.indexOf("const LAT_WINDOW"),
    src.indexOf("// ping berkala untuk ukur latency"));
  const f = new Function("els", `${body}; return { setLatency, resetLatency, els };`)(els);
  return f;
}

test("menampilkan median, bukan sampel terakhir", () => {
  const { setLatency, els } = latency();
  for (const v of [0.4, 0.5, 0.4, 0.6, 0.5]) setLatency(v);
  assert.equal(els.lat.textContent, "0.5");
});

test("satu pencilan tidak menggeser pembacaan", () => {
  const { setLatency, els } = latency();
  for (const v of [0.4, 0.5, 0.4, 0.6, 0.5, 0.4, 0.5]) setLatency(v);
  const sebelum = els.lat.textContent;
  setLatency(48);            // satu frame berat di browser
  assert.equal(els.lat.textContent, sebelum, "pencilan tunggal ikut tampil");
});

test("latensi yang benar-benar naik tetap terbaca", () => {
  const { setLatency, els } = latency();
  for (let i = 0; i < 12; i++) setLatency(0.5);
  for (let i = 0; i < 12; i++) setLatency(35);
  assert.equal(els.lat.textContent, "35");
});

test("jendela dibatasi, sampel lama tidak menempel selamanya", () => {
  const { setLatency, els } = latency();
  for (let i = 0; i < 200; i++) setLatency(0.5);
  for (let i = 0; i < 10; i++) setLatency(7);
  assert.equal(els.lat.textContent, "7.0");
});

test("sub-milidetik tidak dibulatkan menjadi 0", () => {
  const { setLatency, els } = latency();
  for (let i = 0; i < 5; i++) setLatency(0.42);
  assert.equal(els.lat.textContent, "0.4");
});

test("reset mengosongkan pembacaan saat link putus", () => {
  const { setLatency, resetLatency, els } = latency();
  setLatency(3);
  resetLatency();
  assert.equal(els.lat.textContent, "—");
  setLatency(9);
  assert.equal(els.lat.textContent, "9.0", "median lama tidak boleh terbawa");
});

/* ------------------- badge LINK & penanda link Pixhawk ------------------- */

function linkHud() {
  const els = { link: { dataset: {} }, linkLabel: { textContent: "" } };
  const logs = [];
  const bodyClasses = new Set();
  const documentStub = {
    body: {
      classList: {
        toggle: (c, on) => (on ? bodyClasses.add(c) : bodyClasses.delete(c)),
      },
    },
  };
  let domWrites = 0;
  Object.defineProperty(els.link, "dataset", {
    value: new Proxy({}, { set(t, k, v) { domWrites++; t[k] = v; return true; } }),
  });
  const api = new Function("els", "log", "document",
    `let lastLinkMode = null; let fcLinkDown = false;
     ${extract("function setLink(")}
     ${extract("function applyFcLink(")}
     return { setLink, applyFcLink, get fcLinkDown() { return fcLinkDown; } };`,
  )(els, (m, lvl) => logs.push([m, lvl]), documentStub);
  return { ...api, els, logs, bodyClasses, writes: () => domWrites };
}

test("setLink idempoten — mode sama tidak menyentuh DOM berulang", () => {
  const h = linkHud();
  h.setLink("on"); h.setLink("on"); h.setLink("on");
  assert.equal(h.writes(), 1, `DOM ditulis ${h.writes()}x untuk mode yang sama`);
  h.setLink("fc-down");
  assert.equal(h.writes(), 2);
  assert.equal(h.els.linkLabel.textContent, "PIXHAWK PUTUS");
});

test("agent lama tanpa fc_link tidak dikarang statusnya", () => {
  const h = linkHud();
  h.applyFcLink({ heading: 10 });
  assert.equal(h.logs.length, 0, "agent lama tidak boleh memicu log link FC");
  assert.equal(h.bodyClasses.has("fc-down"), false);
});

test("fc_link down memberi peringatan sekali, bukan tiap paket", () => {
  const h = linkHud();
  for (let i = 0; i < 30; i++) h.applyFcLink({ fc_link: "down" });
  assert.equal(h.logs.length, 1, `dicatat ${h.logs.length}x — harus edge-triggered`);
  assert.equal(h.logs[0][1], "err");
  assert.match(h.logs[0][0], /Pixhawk/);
  assert.ok(h.bodyClasses.has("fc-down"), "bacaan FC tidak ditandai beku");
});

test("pemulihan link FC dilaporkan dan penanda beku dilepas", () => {
  const h = linkHud();
  h.applyFcLink({ fc_link: "down" });
  for (let i = 0; i < 5; i++) h.applyFcLink({ fc_link: "ok" });
  assert.equal(h.logs.length, 2, "harus tepat dua transisi");
  assert.equal(h.logs[1][1], "ok");
  assert.equal(h.bodyClasses.has("fc-down"), false);
});

test("badge diturunkan dari fc_link, bukan ditimpa pemulihan telemetry", () => {
  /* Regresi urutan: applyTelemetry dulu memanggil setLink("on") di dalam blok
     pemulihan `if (linkStale)`. Kalau telemetry pulih selagi Pixhawk MASIH
     putus, badge tersangkut di "ONLINE" — persis kebohongan yang ingin
     dihindari. Sekarang badge dihitung SESUDAH applyFcLink. */
  const apply = extract("function applyTelemetry(");
  const iFc = apply.indexOf("applyFcLink(d)");
  const iBadge = apply.indexOf('setLink(fcLinkDown ? "fc-down" : "on")');
  assert.ok(iFc >= 0, "applyTelemetry tidak memanggil applyFcLink");
  assert.ok(iBadge >= 0, "badge tidak lagi diturunkan dari fcLinkDown");
  assert.ok(iBadge > iFc, "badge dihitung sebelum fc_link diperbarui");
  assert.ok(!/if \(linkStale\)[\s\S]{0,200}setLink\("on"\)/.test(apply),
    'blok pemulihan telemetry kembali memanggil setLink("on") langsung');
});

let failed = 0;
for (const { name, fn } of tests) {
  try { fn(); console.log(`  ok  ${name}`); }
  catch (e) { failed++; console.error(`FAIL  ${name}\n      ${e.message}`); }
}
console.log(failed ? `\n${failed} gagal dari ${tests.length}` : `\n${tests.length} lulus`);
process.exit(failed ? 1 : 0);
