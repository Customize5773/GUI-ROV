# Rencana: Menyesuaikan GUI HYDROSHIP dengan Panduan KKI 2026

## Context

User meminta pengecekan apakah GUI ROV (`R:\GUI ROV`) sudah sesuai panduan lomba **KKI 2026** (`R:\AUTONOMUS ROV\Panduan-KKI-2026.pdf`).

Ketentuan GUI resmi (§4.7.3, hal. 54): *"GUI untuk mengoperasikan ROV lengkap dengan: **Deteksi QR Code**; **Display camera 1 (bottom) dan camera 2 (wall)**."* Misi berputar di sekitar QR code yang menentukan sisi dinding (A/B/C/D) tempat payload digantung. Spesifikasi terkait: min 2 kamera real-time (§4.7.2), Emergency Stop wajib (ada ✓), thruster **maksimal 6** (§4.7.2 — GUI saat ini meng-hardcode 8). "Disain GUI" juga dinilai di proposal & video kemajuan.

Konsep tim (gambar yang dikirim user) menambah: altitude ROV terhadap dasar kolam, hari/tanggal/waktu + nama tim + perguruan tinggi, gambar disain ROV (3D — sudah ada ✓), trajectory (sudah ada ✓), dan fitur opsional (toggle manual/autonomous, alarm audio kedalaman, auto screenshot/logging, replay).

**Penting — kondisi repo:** `git status` bersih dan `git diff` kosong; working tree persis di commit `1dbe20c`. **Seluruh pekerjaan sesi ini sudah ter-revert** (Telemetry & Setup kembali ke versi LSTM lama, dll.). Rencana ini dibangun di atas baseline tersebut. User setuju ("Ya, rapikan sekalian") untuk sekalian menerapkan ulang pembersihan LSTM & Setup fungsional.

**Keputusan user:** QR di-decode di browser (jsQR); prioritaskan fitur yang missing; rapikan juga kondisi ter-revert.

## Gap audit (vs baseline commit 1dbe20c)

| Kebutuhan | Status | Aksi |
|---|---|---|
| 2 kamera (bottom+wall) | ⚠️ satu feed bergantian, label CAM1/2 | Tampilkan 2 feed bersamaan + label BOTTOM/WALL |
| **Deteksi QR + sisi A/B/C/D** | ❌ tidak ada | Tambah jsQR + panel hasil QR |
| Altitude vs dasar kolam | ⚠️ hanya depth | Readout ALT = POOL_DEPTH − depth |
| Hari/tanggal/waktu, tim, PT | ❌ tidak ada | Blok identitas + jam di header |
| Gambar disain ROV (3D) | ✅ ada | — |
| Trajectory awal→akhir | ✅ ada (Mission) | — |
| Emergency Stop | ✅ STOP | — |
| Thruster ≤6 | ❌ hardcode 8 | Turunkan ke 6 |
| Toggle manual/autonomous | ❌ | Tambah toggle |
| Alarm audio kedalaman | ❌ | Tambah WebAudio alarm |
| Auto screenshot/logging | ⚠️ manual | Auto saat misi/autonomous |
| Replay camera & trajectory | ❌ | Stretch (fase akhir) |

## Pendekatan

Pakai pola yang sudah ada: modul halaman `public/js/pages/*.js` (`init/onShow/onHide/onTelemetry`) terdaftar di [app.js](public/js/app.js) `pageModules`; util bersama di [core.js](public/js/core.js) (`log, sendCmd, num, snapshotImage, makeFullscreen`); fan-out telemetri lewat `applyTelemetry(d)` di app.js; konstanta di [config.js](public/js/config.js).

### Fase 0 — Fondasi config ([config.js](public/js/config.js))
- Tambah `TEAM_NAME`, `UNIVERSITY` (placeholder, bisa diedit di Setup).
- Tambah peran kamera: `CAMERAS: [{id:"CAM 1", role:"BOTTOM", url}, {id:"CAM 2", role:"WALL", url}]`.
- Tambah `DANGER_DEPTH` (mis. 2.8 m) untuk alarm.
- Tambah `THRUSTER`/`PID` (untuk Setup fungsional, Fase 4).

### Fase 1 — Fitur wajib KKI (prioritas)
1. **Identitas + jam** — [index.html](public/index.html) header `.bar`: blok berisi `TEAM_NAME · UNIVERSITY` dan jam hidup (hari, tanggal, waktu). Update via `setInterval` di app.js (format `id-ID`).
2. **Altitude** — tambah readout `ALT` di `.strip` ([index.html](public/index.html)); hitung di `applyTelemetry()` [app.js](public/js/app.js): `alt = max(0, POOL_DEPTH − depth)`.
3. **Dual camera** — rework [camera.js](public/js/pages/camera.js): tampilkan 2 viewport berdampingan, label `CAM 1 — BOTTOM` & `CAM 2 — WALL`; fullscreen per viewport tetap pakai `makeFullscreen`.
4. **Deteksi QR (jsQR)** — vendor `public/vendor/jsqr.min.js` (perlu diunduh sekali; wajib di-vendor agar jalan offline di venue), load via `<script>` sebelum `app.js`. Di camera.js: loop `onShow`-gated menggambar frame kamera BOTTOM ke `<canvas>` lalu `jsQR(...)`; tampilkan **panel QR RESULT** (data mentah + sisi A/B/C/D + waktu). Indikator ringkas QR juga di header/Control.
   - Catatan: `getImageData` butuh stream same-origin / `crossOrigin="anonymous"` + CORS dari Pi; dokumentasikan di README. Sediakan fallback "scan dari gambar terunggah" bila CORS memblokir.

### Fase 2 — Bersihkan kondisi ter-revert (disetujui user)
1. **Telemetry** ([telemetry.js](public/js/pages/telemetry.js)): hapus kerangka LSTM/pose-gradient (HI_RMS, Threshold, Residual, Fault, Scenario/Efficiency, seri DT+Error). Sisakan grafik nyata Yaw/Depth/Pitch/Roll (alir **kiri→kanan**) + kartu thruster. Turunkan thruster ke **6** (T1–T6) sesuai §4.7.2.
2. **Setup** ([setup.js](public/js/pages/setup.js)): hapus kartu **Digital Twin / LSTM Model Manager**; jadikan kartu Camera/Thruster/PID/Pool/Mobile fungsional (persist `localStorage` + `sendCmd`).

### Fase 3 — Fitur opsional bernilai tinggi (missing → ditambah)
1. **Toggle Manual/Autonomous** — di header/Control; `sendCmd("control_mode", "manual"|"autonomous")`. Relevan untuk misi autonomous (bobot 40%).
2. **Alarm audio kedalaman** — WebAudio oscillator + kedip visual saat `depth ≥ DANGER_DEPTH`; tombol mute. Logika di app.js `applyTelemetry`.
3. **Auto screenshot & logging** — saat misi/autonomous aktif: auto-mulai CSV (reuse buffer telemetry) + snapshot berkala (reuse `snapshotImage`). Toggle on/off.

### Fase 4 — Dukungan server & dokumentasi
- [server.js](server/server.js): di mode `--sim`, lacak & pantulkan `control_mode` (dan reflect arm/light/stop bila sekalian dirapikan); terima cmd baru.
- [README.md](README.md): cara QR/CORS, vendor jsQR untuk offline, perintah `npm run sim`.

### Fase 5 (Stretch, opsional terakhir) — Replay
- Rekam titik trajectory + frame kamera berkala bertimestamp; kontrol play/scrub untuk memutar ulang lintasan (animasi Mission) + frame kamera.

## File yang disentuh
- `public/js/config.js` — konstanta baru.
- `public/index.html` — header identitas+jam, readout ALT, `<script>` jsQR.
- `public/js/app.js` — jam, altitude, alarm, manual/autonomous, auto-logging, fan-out.
- `public/js/pages/camera.js` — dual camera + QR.
- `public/js/pages/telemetry.js` — buang LSTM, 6 thruster, grafik kiri→kanan.
- `public/js/pages/setup.js` — kartu fungsional, hapus LSTM Manager.
- `public/vendor/jsqr.min.js` — library baru (vendored).
- `server/server.js`, `README.md` — dukungan & docs.

## Verifikasi
- Jalankan `preview_start` (server `node server.js --sim`, sudah ada di `.claude/launch.json`).
- Header: jam berjalan, nama tim & PT tampil; strip ALT update mengikuti depth.
- Camera: dua feed BOTTOM/WALL tampil; muat QR uji (gambar) → panel QR RESULT menampilkan data + sisi A/B/C/D.
- Telemetry: tidak ada teks LSTM (`grep`), 6 kartu thruster, grafik mengalir kiri→kanan.
- Setup: tak ada LSTM Manager; Apply menyimpan ke `localStorage` + log `CMD`.
- Alarm: paksa `depth ≥ DANGER_DEPTH` via sim → bunyi + kedip; mute bekerja.
- Manual/Autonomous: klik → `CMD control_mode` di log.
- `preview_console_logs` level error = kosong.

## Catatan
- Nilai `TEAM_NAME`/`UNIVERSITY` saat ini placeholder — user perlu mengisi (atau edit lewat Setup).
- Fitur lain dari sesi yang ikut ter-revert (camera PiP drag/resize/swap, PiP pilot saat kamera Control fullscreen, mission single-load FBX + orbit bebas, refleksi arm/light/stop di sim) **tidak** termasuk rencana inti ini; bisa di-re-apply atas permintaan.
- Replay (Fase 5) paling berat; dikerjakan terakhir bila waktu memungkinkan.