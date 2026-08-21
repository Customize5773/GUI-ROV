/**
 * verify_handoff.mjs — verifikasi DoD Fase 1: handoff GUI Manual<->Autonomous
 * dan tombol STOP saat FSM sedang jalan.
 *
 *     node autonomy/tools/verify_handoff.mjs
 *
 * KENAPA NODE, BUKAN PYTHON
 *   Skrip ini harus bicara WebSocket ke server.js supaya yang diuji adalah
 *   envelope yang BENAR-BENAR dipakai dashboard ({"type":"cmd",...} — lihat
 *   sendCmd di public/js/app.js). Python di mesin ini tidak punya klien
 *   WebSocket, sedangkan `ws` sudah jadi dependensi server/. Menambah paket
 *   Python cuma untuk ini tidak sepadan.
 *
 * APA YANG DIBUKTIKAN, DAN APA YANG TIDAK
 *   Dibuktikan: protokolnya benar — perintah dari dashboard menempuh
 *   WS -> server.js -> UDP :14550 -> rov_link -> FSM, dan efeknya kembali
 *   terlihat di telemetry. Itu lapisan tempat dua bug Fase 1 bersembunyi.
 *   TIDAK dibuktikan: bahwa tombol di layar benar-benar terhubung ke perintah
 *   itu, dan bahwa ROV 3D bergerak. Keduanya butuh mata — lihat checklist
 *   browser di TEST_CHECKLIST.md.
 *
 * PRASYARAT: tidak ada. Skrip menyalakan seluruh stack-nya sendiri di port
 * terpisah, lalu mematikannya lagi.
 *
 * KENAPA SERVER.JS SENDIRI, BUKAN YANG SUDAH JALAN
 *   server.js me-relay command ke RPI_ADDR:14550, dan RPI_ADDR default
 *   192.168.2.2 — alamat ROV asli. Server dev yang dijalankan dengan
 *   `node server.js` polos akan mengirim toggle kita ke wahana di kolam, bukan
 *   ke rov_link lokal, sementara telemetry tetap terlihat normal (server BIND
 *   :14551 dan menerima dari siapa pun). Gejalanya menyesatkan: telemetry
 *   mengalir, perintah hilang tanpa jejak. ROADMAP_MISI5.md memang menyuruh
 *   `RPI_ADDR=127.0.0.1` untuk Fase 1; skrip ini memastikannya alih-alih
 *   berharap operator ingat.
 *
 *   Port sengaja digeser (WS 8090, telemetry 14561) supaya server dev yang
 *   sedang jalan di :8080/:14551 tidak perlu dimatikan.
 */

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");
const require = createRequire(path.join(REPO, "server", "package.json"));
const WebSocket = require("ws");

const WS_PORT = process.env.WS_PORT || "8090";      // bukan 8080: hindari server dev
const UDP_IN = process.env.UDP_IN || "14561";       // bukan 14551: idem
const CMD_PORT = process.env.CMD_PORT || "14550";   // json-rx rov_link (lokal)
const WS_URL = process.env.WS_URL || `ws://localhost:${WS_PORT}`;

// Telemetry rov_link mengalir 5-10 Hz; 20 detik memberi ruang untuk state FSM
// yang butuh beberapa detik (M5_REDIVE menyelam dulu sebelum pindah state).
const TIMEOUT_MS = 20_000;

let telem = {};        // data telemetry terakhir dari server.js
const seenStates = []; // urutan state FSM yang pernah terlihat

function connect() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(WS_URL);
    const gagal = setTimeout(
      () => reject(new Error(`tak bisa connect ke ${WS_URL} dalam 5 dtk — server.js jalan?`)),
      5000,
    );
    ws.on("open", () => { clearTimeout(gagal); resolve(ws); });
    ws.on("error", (e) => { clearTimeout(gagal); reject(e); });
    ws.on("message", (raw) => {
      let msg;
      try { msg = JSON.parse(raw.toString()); } catch { return; }
      if (msg.type !== "telemetry" || !msg.data) return;
      telem = msg.data;
      const st = msg.data.mission5?.state;
      if (st && seenStates.at(-1) !== st) seenStates.push(st);
    });
  });
}

const kirim = (ws, name, value) =>
  ws.send(JSON.stringify({ type: "cmd", name, value }));

/** Tunggu sampai `cek(telem)` true, atau menyerah. Mengembalikan telemetry saat cocok. */
function tunggu(cek, label) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const iv = setInterval(() => {
      if (cek(telem)) { clearInterval(iv); resolve({ ...telem }); return; }
      if (Date.now() - t0 > TIMEOUT_MS) {
        clearInterval(iv);
        reject(new Error(
          `timeout ${TIMEOUT_MS / 1000}s menunggu: ${label}\n` +
          `       telemetry terakhir: control_mode=${telem.control_mode} ` +
          `armed=${telem.armed} mission5.state=${telem.mission5?.state ?? "(tidak ada)"}`,
        ));
      }
    }, 100);
  });
}

const jeda = (ms) => new Promise((r) => setTimeout(r, ms));

const hasil = [];
async function skenario(nama, fn) {
  process.stdout.write(`\n▶ ${nama}\n`);
  try {
    await fn();
    hasil.push([true, nama]);
    console.log(`  ✓ LULUS`);
  } catch (e) {
    hasil.push([false, nama]);
    console.log(`  ✗ GAGAL: ${e.message}`);
  }
}

async function main() {
  const logStack = [];
  const anak = [];
  const rekam = (proc, tag) => {
    anak.push(proc);
    for (const s of [proc.stdout, proc.stderr]) {
      s.on("data", (b) => {
        const t = b.toString();
        logStack.push(t);
        if (process.env.VERBOSE) process.stdout.write(`[${tag}] ${t}`);
      });
    }
  };

  console.log(`[verify] menyalakan server.js (WS :${WS_PORT}, RPI_ADDR=127.0.0.1)...`);
  rekam(spawn("node", ["server.js"], {
    cwd: path.join(REPO, "server"),
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, RPI_ADDR: "127.0.0.1", WS_PORT, UDP_IN, UDP_OUT: CMD_PORT },
  }), "GUI");
  await jeda(1500);

  console.log("[verify] menyalakan vehicle mock + rov_link (TANPA --fsm — justru");
  console.log("         toggle GUI yang harus menyalakannya; itu yang diuji)...");
  rekam(spawn("python3", [
    "autonomy/tools/launch_sitl.py", "--no-gui", "--vision", "mock",
    "--server-ip", "127.0.0.1", "--telem-port", UDP_IN, "--cmd-port", CMD_PORT,
  ], { cwd: REPO, stdio: ["ignore", "pipe", "pipe"] }), "STACK");

  let ws;
  try {
    ws = await connect();
    console.log(`[verify] tersambung ke ${WS_URL}`);
    await tunggu((t) => t.depth !== undefined && t.depth !== null,
                 "telemetry pertama dari rov_link");
    console.log("[verify] telemetry mengalir — mulai skenario\n");

    // Titik awal yang diketahui: Manual.
    kirim(ws, "control_mode", "manual");
    await jeda(500);

    await skenario("A. Handoff Manual -> Autonomous menyalakan FSM", async () => {
      kirim(ws, "control_mode", "autonomous");
      await tunggu((t) => t.control_mode === "autonomous",
                   "control_mode jadi 'autonomous'");
      await tunggu((t) => t.mission5?.state,
                   "blok mission5 muncul di telemetry (FSM hidup)");

      // Bukan cuma hidup — harus benar-benar MAJU. FSM yang hidup tapi macet
      // persis gejala bug vert/heave dulu (DIVE timeout 15 dtk, skor 0/100).
      //
      // start_mission5() masuk di M5_REDIVE, tapi state PERTAMA yang terbit di
      // telemetry adalah IDLE (nilai awal _state, sebelum _transition). Assert
      // "state != M5_REDIVE" karena itu dipenuhi IDLE secara trivial dan lulus
      // tanpa membuktikan apa pun — sempat terjadi. Yang benar: tunggu FSM
      // MASUK ke M5_REDIVE, lalu tunggu ia KELUAR lagi ke state sesudahnya.
      await tunggu(() => seenStates.includes("M5_REDIVE"),
                   "FSM masuk M5_REDIVE (mulai rantai misi 5)");
      const sesudahRedive = new Set([
        "M5_DOCK", "M5_ENGAGE", "M5_UNHOOK", "M5_ASCEND", "M5_FALLBACK", "DONE",
      ]);
      await tunggu(() => seenStates.some((s) => sesudahRedive.has(s)),
                   "FSM maju dari M5_REDIVE ke state berikutnya");
      console.log(`  state terlihat: ${seenStates.join(" -> ")}`);
    });

    await skenario("B. Handoff Autonomous -> Manual mengabort FSM", async () => {
      kirim(ws, "control_mode", "manual");
      await tunggu((t) => t.control_mode === "manual",
                   "control_mode kembali 'manual'");
      await tunggu((t) => !t.mission5 || t.mission5.state === "ABORT",
                   "FSM berhenti / masuk ABORT");
    });

    await skenario("C. STOP saat FSM jalan: netral, disarm, DAN FSM berhenti", async () => {
      // Nyalakan lagi supaya benar-benar ada FSM yang harus dihentikan STOP.
      kirim(ws, "control_mode", "autonomous");
      await tunggu((t) => t.mission5?.state && t.control_mode === "autonomous",
                   "FSM hidup lagi sebelum diuji STOP");
      await tunggu((t) => t.armed === true, "wahana armed oleh FSM");

      kirim(ws, "stop", true);

      await tunggu((t) => t.armed === false, "STOP -> disarm");
      // Bagian yang dulu TIDAK terjadi: handler 'stop' di rov_link tidak
      // memanggil stop_mission5(), jadi thread FSM terus hidup dan menulis
      // setpoint. Tanpa assert ini, skenario C lolos setengah.
      await tunggu((t) => !t.mission5 || t.mission5.state === "ABORT",
                   "STOP -> FSM ikut berhenti (bukan cuma thruster netral)");
    });
  } catch (e) {
    console.error(`\n[verify] gagal sebelum skenario selesai: ${e.message}`);
    if (!process.env.VERBOSE) {
      console.error("\n--- 30 baris terakhir stack ---");
      console.error(logStack.join("").split("\n").slice(-30).join("\n"));
    }
    hasil.push([false, "(setup)"]);
  } finally {
    ws?.close();
    for (const p of anak) p.kill("SIGINT");
    await jeda(1500);
    for (const p of anak) p.kill("SIGKILL");
  }

  console.log("\n" + "=".repeat(60));
  for (const [ok, nama] of hasil) console.log(`${ok ? "✓" : "✗"} ${nama}`);
  const gagal = hasil.filter(([ok]) => !ok).length;
  console.log("=".repeat(60));
  console.log(gagal === 0
    ? "SEMUA LULUS — sisa DoD Fase 1 tinggal checklist browser (TEST_CHECKLIST.md)"
    : `${gagal} skenario GAGAL`);
  process.exit(gagal === 0 ? 0 : 1);
}

main();
