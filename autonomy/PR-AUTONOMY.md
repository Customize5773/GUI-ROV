# PR-AUTONOMY.md — Backlog & Analisis Gap Autonomy (Misi 5)

Catatan pekerjaan yang **ditunda / belum tuntas** untuk otonomi ROV KKI 2026,
plus analisis apa yang masih kurang di tiap fase (Fase 0–4, lihat `ROADMAP_MISI5.md`).
Dokumen hidup: perbarui status saat item dikerjakan.

> Ringkas status: kode logika (FSM, servo, deteksi QR, simulator, evaluasi) **matang &
> teruji** (63 pytest hijau). Yang tersisa mayoritas **butuh hardware/kolam** — tak bisa
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

**Peningkatan opsional (nice-to-have):**
- [ ] Resolusi/preprocessing `VisionPipeline` (bukan hanya tool webcam) bila deteksi di Pi kurang.
- [ ] Squaring yaw PBVS (QR-02) bila terbukti stabil.
- [ ] Replay/telemetry logging terpadu untuk analisis pasca-run.
