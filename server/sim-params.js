/* sim-params.js — tabel parameter palsu untuk `node server.js --sim`.
 *
 * Kenapa perlu: di mode SIM tidak ada Pixhawk maupun rov_agent.py yang
 * menjawab, sementara command TETAP dikirim ke UDP (lihat server.js). Tanpa
 * modul ini halaman Vehicle selalu kosong dan fitur param hanya bisa
 * dikembangkan sambil menyalakan SITL atau menyolok hardware.
 *
 * Sumber datanya BUKAN karangan: parameters_ardusub.params di root repo adalah
 * dump QGroundControl yang diambil langsung dari Pixhawk wahana (ArduSub 4.5.7),
 * jadi nama, nilai, dan tipe yang tampil di SIM sama persis dengan yang nanti
 * dilihat operator di kolam.
 *
 * Nol dependency baru — sama seperti keputusan frame-store di fitur Replay.
 */

const fs = require("fs");
const path = require("path");

// 22 Agu 2026: dump dipindah ke parameter-pixhawk/. Pemuatan ini gagal LUNAK
// (server.js hanya mencetak warning), jadi path yang salah membuat halaman
// Vehicle mode simulasi kosong tanpa error yang kelihatan.
// Dikunci test_rov_params.py::test_dump_asli_di_repo.
const DUMP_PATH = path.join(__dirname, "..", "parameter-pixhawk", "parameters_ardusub.params");

// Padanan rov_params.PARAM_TYPES di sisi Python. Nilai enum MAV_PARAM_TYPE
// bagian dari wire format MAVLink, jadi aman disalin.
const INTEGER_TYPES = new Set([1, 2, 3, 4, 5, 6, 7, 8]);
const KNOWN_TYPES = new Set([...INTEGER_TYPES, 9, 10]);

const PARAM_NAME_MAX = 16;

/* Padanan PID_PARAM_MAP di rov_pid.py — nama, rentang aman, dan urutan tulis
   harus sama persis dengan sisi Python, kalau tidak mode SIM akan meloloskan
   gain yang ditolak wahana (atau sebaliknya) dan justru menyembunyikan bug
   yang seharusnya ketahuan sebelum turun ke kolam. */
const PID_PARAM_MAP = [
  ["yaw", "p", "ATC_RAT_YAW_P", 0.0, 1.0],
  ["yaw", "i", "ATC_RAT_YAW_I", 0.0, 1.0],
  ["yaw", "d", "ATC_RAT_YAW_D", 0.0, 0.05],
  ["roll", "p", "ATC_RAT_RLL_P", 0.0, 1.0],
  ["roll", "i", "ATC_RAT_RLL_I", 0.0, 1.0],
  ["roll", "d", "ATC_RAT_RLL_D", 0.0, 0.05],
  ["pitch", "p", "ATC_RAT_PIT_P", 0.0, 1.0],
  ["pitch", "i", "ATC_RAT_PIT_I", 0.0, 1.0],
  ["pitch", "d", "ATC_RAT_PIT_D", 0.0, 0.05],
  ["depth", "p", "PSC_ACCZ_P", 0.2, 1.5],
  ["depth", "i", "PSC_ACCZ_I", 0.0, 3.0],
  ["depth", "d", "PSC_ACCZ_D", 0.0, 0.4],
];

/**
 * Terjemahkan payload command `pid` jadi daftar tulis + penolakan.
 * Padanan rov_pid.resolve_pid_writes(). Mengembalikan {writes, rejects}.
 */
function resolvePidWrites(payload) {
  const writes = [];
  const rejects = [];

  if (!payload || typeof payload !== "object") {
    return { writes, rejects: [["pid", "payload bukan objek"]] };
  }

  const axisRejected = new Set();

  for (const [axis, gain, name, lo, hi] of PID_PARAM_MAP) {
    const section = payload[axis];
    if (section === undefined || section === null) continue;
    if (typeof section !== "object") {
      if (!axisRejected.has(axis)) { axisRejected.add(axis); rejects.push([axis, "bagian bukan objek"]); }
      continue;
    }
    if (!(gain in section)) continue;

    const raw = section[gain];
    /* Number(null), Number("") dan Number([]) semuanya 0 di JavaScript —
       tanpa saringan ini gain kosong akan diam-diam ditulis sebagai 0,
       sementara float(None) di rov_pid.py menolaknya. Dua sisi harus sepakat. */
    if (raw === null || raw === "" || typeof raw === "boolean" || typeof raw === "object") {
      rejects.push([name, `nilai bukan angka: ${raw}`]);
      continue;
    }

    const value = Number(raw);
    if (!Number.isFinite(value)) { rejects.push([name, `nilai bukan angka: ${raw}`]); continue; }

    if (value < lo || value > hi) {
      rejects.push([name,
        `${value} di luar rentang aman ${lo}..${hi} — ` +
        "periksa satuan (nilai ArduSub, bukan skala GUI lama)"]);
      continue;
    }

    writes.push([name, value]);
  }

  if (!writes.length && !rejects.length) {
    rejects.push(["pid", "tidak ada gain yang dikenali di payload"]);
  }

  return { writes, rejects };
}

/* Batasi ukuran satu batch persis seperti rov_agent.PARAM_BATCH_SIZE, supaya
   perilaku progresif yang dilihat GUI di SIM sama dengan di wahana nyata. */
const BATCH_SIZE = 50;
const BATCH_INTERVAL_MS = 60;

/** Normalisasi nama param — padanan rov_params.normalize_param_name. */
function normalizeName(name) {
  if (typeof name !== "string") return null;
  const c = name.trim().toUpperCase();
  if (!c || c.length > PARAM_NAME_MAX) return null;
  if (!/^[A-Z_][A-Z0-9_]*$/.test(c)) return null;
  return c;
}

/** Baca dump format QGroundControl -> Map(nama -> {value, ptype}). */
function parseDump(text) {
  const out = new Map();
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;

    const parts = t.split(/\s+/);
    if (parts.length < 5) continue;

    const name = normalizeName(parts[2]);
    if (name === null) continue;

    const value = Number(parts[3]);
    const ptype = Number(parts[4]);
    if (!Number.isFinite(value) || !KNOWN_TYPES.has(ptype)) continue;

    out.set(name, { value, ptype });
  }
  return out;
}

class SimParams {
  constructor(params) {
    this.params = params;
    // Urutan indeks dibekukan sekali: param_index/param_count yang dikirim ke
    // GUI harus konsisten antar permintaan, kalau tidak progress bar melompat.
    this.order = [...params.keys()];
    this._timers = new Set();
  }

  static load(dumpPath = DUMP_PATH) {
    return new SimParams(parseDump(fs.readFileSync(dumpPath, "utf8")));
  }

  get size() {
    return this.params.size;
  }

  get(name) {
    const key = normalizeName(name);
    if (key === null) return null;
    const entry = this.params.get(key);
    return entry ? { name: key, ...entry } : null;
  }

  /** Satu entri siap kirim, lengkap dengan index/count. */
  entryAt(i) {
    const name = this.order[i];
    const { value, ptype } = this.params.get(name);
    return { name, value, ptype, index: i, count: this.order.length };
  }

  /**
   * Tulis satu param. Mengembalikan {ok, reason, entry}.
   *
   * Menirukan perilaku ArduPilot yang penting untuk diuji di GUI: nama tak
   * dikenal TIDAK diterima diam-diam, dan tipe integer dibulatkan sehingga
   * nilai yang di-echo balik belum tentu sama dengan yang diketik operator.
   */
  set(name, value) {
    const key = normalizeName(name);
    if (key === null) return { ok: false, reason: "nama param tidak valid" };

    const entry = this.params.get(key);
    if (!entry) return { ok: false, reason: "param tidak dikenal di FC" };

    let v = Number(value);
    if (!Number.isFinite(v)) return { ok: false, reason: "nilai bukan angka" };

    if (INTEGER_TYPES.has(entry.ptype)) v = Math.round(v);

    entry.value = v;
    return { ok: true, entry: { name: key, value: v, ptype: entry.ptype } };
  }

  /**
   * Kirim seluruh tabel sebagai rentetan batch, meniru param_request_list yang
   * datang bertahap dari FC (bukan sekaligus) supaya progress bar di GUI
   * benar-benar teruji.
   *
   * `emit(msg)` dipanggil per batch. Mengembalikan fungsi pembatal.
   */
  streamAll(emit) {
    let i = 0;
    const total = this.order.length;

    const timer = setInterval(() => {
      if (i >= total) {
        clearInterval(timer);
        this._timers.delete(timer);
        return;
      }

      const params = [];
      for (let n = 0; n < BATCH_SIZE && i < total; n++, i++) {
        params.push(this.entryAt(i));
      }

      emit({ type: "param_batch", params, done: i >= total });
    }, BATCH_INTERVAL_MS);

    this._timers.add(timer);
    return () => { clearInterval(timer); this._timers.delete(timer); };
  }

  /** Hentikan semua stream yang sedang jalan (dipakai saat test selesai). */
  stopAll() {
    for (const t of this._timers) clearInterval(t);
    this._timers.clear();
  }
}

module.exports = {
  SimParams, parseDump, normalizeName, resolvePidWrites,
  DUMP_PATH, BATCH_SIZE, PID_PARAM_MAP,
};
