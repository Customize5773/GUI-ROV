# PR-AUTONOMY.md — Backlog & Analisis Gap Autonomy (Misi 5)

Catatan pekerjaan yang **ditunda / belum tuntas** untuk otonomi ROV KKI 2026,
plus analisis apa yang masih kurang di tiap fase (Fase 0–4, lihat `ROADMAP_MISI5.md`).
Dokumen hidup: perbarui status saat item dikerjakan.

> Ringkas status: kode logika (FSM, servo, deteksi QR & hook, docking closed-loop misi
> 3b/4/5, simulator, evaluasi) **matang & teruji** (83 pytest hijau). Yang tersisa
> mayoritas **butuh hardware/kolam** — tak bisa
> diselesaikan tanpa perangkat. Item yang bisa dikerjakan tanpa hardware sudah/akan dibuat
> (config tunable, launch_sitl, CSV logging, preprocessing QR, run-book).

---

## 1. Known issues

### QR-01 — Deteksi QR hanya jarak dekat / sensitif cahaya (Fase 0)
**Gejala** (uji meja tim): QR payload terbaca oleh `servo_webcam_test.py` /
`pose_webcam_test.py`, tapi **hanya bila dekat** atau **tergantung pencahayaan**.

**Root cause**: `vision/qr_detect.py._run_camera` (dan kedua tool webcam) dulu mengirim
frame kamera **mentah** langsung ke `pyzbar.decode()` — tanpa perbaikan kontras/skala.

**Sudah ditangani di PR ini** (level algoritma & resolusi):
- `vision/qr_detect.py` → fungsi `decode_qr()` dgn preprocessing **berjenjang**:
  mentah → grayscale+CLAHE (cahaya tak rata/glare) → upscale 2× (QR kecil/jauh) →
  fallback `cv2.QRCodeDetector`. Dipakai `_run_camera` & kedua tool webcam.
- Tool webcam: opsi `--cam-width/--cam-height` (default 1280×720) — resolusi lebih tinggi
  bantu QR jauh (kamera sering default 640×480).
- Tool webcam: `--csv` merekam detection-rate + jarak/area per frame → **kuantifikasi**
  batas jarak deteksi untuk keputusan tuning.

**Sisa backlog (butuh venue/hardware, tak bisa tanpa perangkat):**
- [ ] Ukur kurva **detection-rate vs jarak** di air (pakai `--csv`) → tetapkan jarak
      kerja aman; sesuaikan `SERVO_TARGET_DIST`/`HOOK_DEPTH` agar akuisisi terjadi di
      dalam rentang terbaca.
- [ ] Tuning kamera fisik: **exposure/gain/fokus/white-balance** (mis. `v4l2-ctl` /
      properti `cv2.CAP_PROP_*`) di pencahayaan venue; air keruh menurunkan kontras.
- [ ] Keputusan **ukuran fiducial**: QR 4×4 cm KKI mungkin terlalu kecil utk jarak
      akuisisi di air keruh → evaluasi apakah aturan mengizinkan cetak QR lebih besar,
      atau tambah lampu (`LIGHT_SERVO_CH`).
- [ ] Benchmark **`cv2.QRCodeDetector` vs pyzbar** pada rekaman air nyata (mana lebih
      tahan blur/keruh) → mungkin ubah urutan jenjang di `decode_qr()`.
- [ ] Pertimbangkan **rig pencahayaan** / anti-glare (polarizer) bila refleksi jadi masalah.

Referensi: `vision/qr_detect.py` (`decode_qr`, `_run_camera`), `VERIFIKASI_ARDUSUB.md` M3.

### QR-02 — Yaw squaring PBVS dinonaktifkan (`SERVO_KP_YAW=0`)
Yaw dari 1 QR planar ambigu (dua solusi IPPE). Squaring tegak-lurus dinding saat ini
mengandalkan heading-hold ArduSub. **Backlog**: bila perlu squaring aktif, verifikasi
stabilitas tanda yaw di kolam dulu (`VERIFIKASI_ARDUSUB.md` M7), baru naikkan `SERVO_KP_YAW`.

### OPEN-FASE1 — Rantai SITL (MAVLink → rov_link.py → FSM) tak pernah keluar dari DIVE (2026-08-12)
**Status: Fase 1 BELUM VERIFIED — jangan dicentang, jangan lanjut ke Fase 2.**

**Gejala**: `python tools/launch_sitl.py --fsm --vision mock --no-wait-autonomous` start
bersih (VEHICLE/ROV_LINK/FSM semua up, heartbeat & telemetri UDP mengalir), tapi FSM tak
pernah turun dari `DIVE` — timeout 15 dtk tiap kali, `DIVE → ABORT`, skor **0/100**.
Direproduksi **3/3 run** dengan hasil identik (lihat `/tmp/phase1_fsm_run{1,2,3}.log`).

**Environment**: repo lokal, `--vehicle mock` (`sitl_mock.py`, bukan ArduSub SITL WSL2),
server GUI dijalankan via `npm run sim` (server/), 121 pytest hijau (1 skip) sebelum run.

**Repro**:
```
cd server && npm run sim &
cd autonomy && python3 tools/launch_sitl.py --fsm --vision mock --no-wait-autonomous --no-gui
```

**Expected**: FSM `DIVE` selesai begitu depth mendekati `DEPTH_TARGET_BOTTOM` (0.70 m),
lanjut ke `SCAN_QR` dst. sampai `DONE`, 4 siklus hook, skor rubrik > 0.

**Actual**: log `ROV_LINK` menampilkan `[CMD] (diabaikan di link) vert = -30` berulang
sepanjang state `DIVE` — setpoint vertikal FSM **tidak pernah diterapkan**, sehingga depth
diam dan timer timeout 15 dtk selalu tercapai.

**Root cause (layer: protokol command FSM↔rov_link, BUKAN MAVLink/vehicle)**: field-name
mismatch. `fsm/mission5.py` mengirim command JSON dengan key **`"vert"`**
(`Mission5FSM.send()`, sekitar baris 207–210 & docstring header). `rov_link.py`
(`handle_command`, sekitar baris 190–191, 98) hanya mengenali
`self.sp = {"surge","sway","yaw","heave"}` — `"vert"` tidak ada di set itu, jatuh ke
cabang `else` generik (`print(f"[CMD] (diabaikan di link) {name} = {value}")`) yang
dimaksudkan utk command GUI-only (`mode`/`controller`/`pid`/dst), bukan utk axis gerak.
Akibatnya thruster vertikal tak pernah bergerak lewat jalur SITL nyata — meski
`sim_plant.py` in-process (yang dipakai 121 pytest) tidak mengalami ini karena tak lewat
`rov_link.py` sama sekali.

**Lapisan MAVLink/telemetri TIDAK bermasalah**: `rov_link.py` → `server.js` UDP telemetry
terverifikasi jalan (log `server.js`: 199 baris `[TELEM] from 127.0.0.1:... heading=...
armed=true mode=STABILIZE` selama window pengujian) — heartbeat & pembacaan sensor sampai
ke GUI. Bug murni di pemetaan nama field axis command.

**Kandidat perbaikan (list saja, TIDAK diimplementasikan sesuai instruksi tugas ini)**:
- Samakan nama key: ubah `mission5.py` mengirim `"heave"` alih-alih `"vert"` (paling
  minimal, ikut konvensi `rov_link.py`/`self.sp` yang sudah dipakai jalur manual joystick).
- ATAU tambah alias `"vert"` di `rov_link.py.handle_command` (map ke `self.sp["heave"]`)
  agar dua konvensi penamaan (`vert` dokumentasi FSM vs `heave` internal rov_link) sama-sama
  didukung tanpa mengubah `mission5.py`.
- Tambah test integrasi kecil (mis. di `tests/test_launch_sitl.py` atau baru) yang menegaskan
  tiap key command yang dikirim `Mission5FSM.send()`/`_emit()` ada di dalam
  `RovLink.sp.keys()` — supaya regresi field-name seperti ini tertangkap oleh CI, bukan cuma
  ketahuan saat SITL run manual.

**Catatan tambahan (di luar cakupan verifikasi, tak dieksekusi)**:
- Task brief menyebut `autonomy/README_SETUP_C.md` — file itu **tidak ada** di repo (dirujuk
  dari `ROADMAP_MISI5.md` & docstring `launch_sitl.py`, tapi belum pernah dibuat).
- `npm run sim` (server `--sim`) menghasilkan telemetri **palsu miliknya sendiri** tiap 100 ms
  (lihat `server.js` baris ~772) yang di-broadcast bersamaan dgn telemetri UDP nyata dari
  `rov_link.py` — GUI akan menampilkan campuran/flicker data asli & sintetis. Untuk verifikasi
  Fase 1 yang bersih, GUI semestinya dijalankan via `npm start` (bukan `npm run sim`) dengan
  `RPI_ADDR=127.0.0.1` sesuai `ROADMAP_MISI5.md`, bukan `--sim`. Listener UDP `:14551` sendiri
  tetap aktif & mencatat `[TELEM] from ...` di kedua mode, jadi verifikasi command-layer di
  atas tidak terpengaruh, tapi verifikasi visual GUI (3D movement) akan salah kalau memakai
  `--sim`.
- Verifikasi sisi GUI (toggle Manual↔Autonomous di dashboard nyata, tombol STOP, F12 console,
  ROV 3D bergerak sinkron) **tidak tercakup** — memerlukan browser interaktif, di luar
  kapasitas headless observasi ini. Juga tak relevan dieksekusi selama DIVE tak pernah lulus.

### HOOK-01 — Deteksi hook PVC untuk docking misi 3b (HANG) & misi 4 (DOCK)
**Konteks**: hook = pipa PVC ¾" (25 mm) ujung-U di dinding, **tanpa QR/marker sendiri**;
posisi sisi (A/B/C/D) diacak tiap run. `_state_hang` & `_state_dock` dulu **murni timed**
(gerak buta berbasis detik), tanpa umpan balik visual.

**Sudah ditangani di PR ini** (level algoritma & closed-loop, teruji di simulator):
- `vision/hook_detect.py` → `detect_hook()` **berjenjang** (pola `decode_qr`): color mask
  (opsional) → grayscale+CLAHE→Canny contour (jalur utama non-warna) → HoughCircles
  (lengkung-U/lubang) fallback. Kembalikan center/area/bbox/confidence + estimasi jarak
  (proxy diameter pipa 25 mm bila `focal_px` ada). Hasil kompatibel `_hook_servo_step()`.
- `_state_hang`/`_state_dock` jadi **closed-loop**: reuse `VisualServo`/`PoseServo`
  (instans `hook_servo`/`hook_pose_servo`), dead-reckon hold saat dropout
  (`HOOK_LOCK_GRACE_T`), dan **degradasi eksplisit** ke urutan timed lama bila hook tak
  ter-lock (`HOOK_ACQUIRE_T`/`TIMEOUT_*`) — pola sama `M5_DOCK → M5_FALLBACK`.
- Config tunable (`hook_docking:`/`hook_detect:`/`hang:` di `mission5.example.yaml`,
  `config/loader.py`) + model hook di `tests/sim_plant.py` + test unit/integrasi
  (`test_hook_detect.py`, `test_mission5.py` termasuk loss-of-lock).

**Sisa backlog (butuh venue/hardware, tak bisa tanpa perangkat):**
- [ ] **Warna PVC asli** vs latar dinding kolam belum pasti (Panduan tak menyebut). Ukur
      di venue; isi `hook_detect.color_hsv_range` HANYA bila kontras warna terbukti andal —
      jangan jadikan warna satu-satunya jalur (default tetap contour/edge).
- [ ] **Exposure/gain/fokus CAM WALL** di pencahayaan venue + air keruh/glare → tuning ulang
      `HOOK_MIN_AREA` & ambang Canny; ukur detection-rate vs jarak (analog QR-01).
- [ ] **Estimasi jarak hook** (`width_px`→z) masih proxy KASAR: pada bentuk-U `minAreaRect`
      cenderung menangkap lebar-U, bukan diameter pipa 25 mm → kalibrasi/koreksi di air, atau
      andalkan `HOOK_TARGET_AREA` (IBVS) bila pose tak stabil.
- [ ] Verifikasi kamera mana yang dipakai HANG vs DOCK di hardware (WALL cam tunggal vs
      pipeline terpisah) & arah sumbu servo hook (`invert_*`) di kolam.
- [ ] Uji kokoh HoughCircles terhadap **lubang payload Ø3 cm** vs lengkung-U (bisa saling
      keliru) — putuskan urutan jenjang setelah rekaman air nyata.

Referensi: `vision/hook_detect.py`, `fsm/mission5.py` (`_state_hang`/`_state_dock`,
`_hook_servo_step`), `tests/sim_plant.py` (model hook), `VERIFIKASI_ARDUSUB.md` M3/M7.

---

## 2. Analisis gap per fase (Fase 0–4)

| Fase | Status | Sudah ada | Yang kurang / TODO | Blocker |
|------|--------|-----------|--------------------|---------|
| **0** Visi di meja | ✅ hampir | servo+pose webcam test jalan; kalibrasi papan catur OK; deteksi QR JSON | Rekam nilai `invert_*` hasil uji ke config; formalkan pass/fail; **QR-01** (ditangani PR ini) | — |
| **1** SITL | ❌ dijalankan, GAGAL (lihat **OPEN-FASE1**) | `launch_sitl.py` (1 perintah), `sitl_mock.py`, `rov_link.py`, GUI LIVE | Fix bug field-name `vert`/`heave` (OPEN-FASE1), ulangi 3× run sampai `DONE` konsisten, baru verifikasi handoff manual↔autonomous & STOP | Bug software (bukan hardware) — **blocker utk lanjut Fase 2** |
| **2** Bring-up bench | 🔒 blocked | `VERIFIKASI_ARDUSUB.md` #1–7; `PERSIAPAN_FASE2-4.md` (run-book, PR ini) | Eksekusi cek arah 6–8 thruster, servo gripper/lampu (channel/PWM), depth, arming | **Hardware** (Pixhawk/ROV) |
| **3** Uji kolam & tuning | 🔒 blocked | `VERIFIKASI_ARDUSUB.md` M1–M8; config tunable (`--config`); CSV logging (PR ini) | Kalibrasi kamera DI AIR; verif ulang `invert_*`; tuning jarak/depth/timing/`WALL_HEADING`; uji unhook | **Kolam + hardware** |
| **4** Latihan & lomba | 🔒 blocked | `PERSIAPAN_FASE2-4.md` run-book hari-H + scoresheet (PR ini) | Rehearsal 3× run; drill fallback; checklist boot & pra-dive dieksekusi | **Setup penuh + kolam** |

Legenda: ✅ selesai · ⏳ bisa dikerjakan sekarang (tak butuh hardware) · 🔒 menunggu hardware/kolam.

---

## 3. Backlog tugas (dikerjakan setelah hardware/kolam tersedia)

**Fase 1 (bisa segera, tak butuh hardware):**
- [ ] Jalankan `python tools/launch_sitl.py --fsm --vision mock --no-wait-autonomous`
      → konfirmasi rantai misi 5 sampai `DONE` via jalur MAVLink nyata.
- [ ] Uji toggle GUI Manual↔Autonomous + STOP saat FSM berjalan (centang M8 di VERIFIKASI).

**Fase 2 (butuh hardware kering):**
- [ ] Kerjakan `VERIFIKASI_ARDUSUB.md` #1–7; catat konstanta `rov_link.py` yang perlu dibalik/disesuaikan
      (`Z_NEUTRAL`, tanda sumbu, `GRIPPER_SERVO_CH`/PWM, `LIGHT_SERVO_CH`, `WATER_RHO`, mode `ALT_HOLD`).

**Fase 3 (butuh kolam):**
- [ ] Kerjakan `VERIFIKASI_ARDUSUB.md` M1–M8; tuang hasil tuning ke `config/mission5.local.yaml`.
- [ ] Selesaikan sisa **QR-01** (kurva jarak, exposure, fiducial, benchmark detektor).
- [ ] Kalibrasi kamera DI AIR (`calibrate_camera.py`, papan tahan air) → `.npz` PBVS venue.

**Fase 4 (butuh setup penuh):**
- [ ] Rehearsal 3× run + drill fallback; isi scoresheet di `PERSIAPAN_FASE2-4.md`.

**Joystick manual control (GUI, baru diimplementasikan — 2026-07-14):**
- [x] Capture Gamepad API + deadzone + mapping axis (browser, sudah ada sebelumnya).
- [x] Encoding **MANUAL_CONTROL** di Pi (`rov_agent.py` + `rov_axes.py`, `pymavlink`);
      Node server hanya forward JSON + clamp axis (−100..100).
- [x] Gating otoritas: joystick nonaktif saat mode Autonomous & terkunci saat E-Stop.
- [x] Fail-safe: netral saat disconnect / idle > 0.5 s. Unit test `test_rov_axes.py`.
- [ ] **Butuh keputusan Rasya / uji hardware:** verifikasi tanda & skala sumbu (surge/sway/yaw/heave)
      cocok dengan orientasi thruster di kolam; bitmask tombol masih placeholder.

**Peningkatan opsional (nice-to-have):**
- [ ] Resolusi/preprocessing `VisionPipeline` (bukan hanya tool webcam) bila deteksi di Pi kurang.
- [ ] Squaring yaw PBVS (QR-02) bila terbukti stabil.
- [ ] Replay/telemetry logging terpadu untuk analisis pasca-run.
