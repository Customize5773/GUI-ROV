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
| **1** SITL | ⏳ belum jalan | `launch_sitl.py` (1 perintah), `sitl_mock.py`, `rov_link.py`, GUI LIVE | Jalankan rantai penuh; verifikasi handoff manual↔autonomous & STOP saat FSM jalan | Perlu jalankan (bukan hardware) — bisa segera |
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
