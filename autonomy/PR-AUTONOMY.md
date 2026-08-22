# PR-AUTONOMY.md — Backlog & Analisis Gap Autonomy (Misi 5)

Catatan pekerjaan yang **ditunda / belum tuntas** untuk otonomi ROV KKI 2026,
plus analisis apa yang masih kurang di tiap fase (Fase 0–4, lihat `ROADMAP_MISI5.md`).
Dokumen hidup: perbarui status saat item dikerjakan.

> Ringkas status: kode logika (FSM, servo, deteksi QR & hook, docking closed-loop misi
> 3b/4/5, simulator, evaluasi) **matang & teruji** (146 pytest hijau, 2 skip). **Trial
> kolam pertama sudah berlangsung 22 Agu 2026** (log `log-m5/journal-22agu.txt`) — Fase
> 2/3 bukan lagi "menunggu hardware/kolam", tapi **sedang jalan**: empat bug ditemukan &
> ditutup di hari yang sama (`BRIDGE-01`, `CALIB-01`, `HOOK-02`, `GRIPPER-01` di bawah),
> plus dua fitur baru (MARK/M5_REDIVE, depth-pulse ALT_HOLD) yang belum divalidasi ulang
> di kolam. Sisa backlog mayoritas **butuh trial kolam lagi** — tak bisa diselesaikan dari
> meja. Item yang bisa dikerjakan tanpa hardware sudah/akan dibuat (config tunable,
> launch_sitl, CSV logging, preprocessing QR, run-book).

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

### ~~OPEN-FASE1~~ — CLOSED 2026-08-21: rantai SITL tuntas + handoff/STOP terverifikasi

**Status: Fase 1 VERIFIED.** Boleh lanjut ke Fase 2 (bring-up hardware kering).

Bug `vert`/`heave` yang dilaporkan 2026-08-12 (FSM tak pernah keluar dari `DIVE`,
0/100, 3/3 run) sudah diperbaiki: `CommandSender` mengirim `heave` dalam skala
-1000..1000, dikunci `test_command_sender_emits_heave_scaled_to_1000`.

**Hasil verifikasi ulang:**
- `launch_sitl.py --fsm --vision mock --no-wait-autonomous --no-gui` 3× berturut →
  **3/3 `DONE`, 100/100**, 13 transisi state identik antar run, nol WARNING/ERROR.
- `node tools/verify_handoff.mjs` → **3/3 skenario LULUS** (handoff masuk, handoff
  keluar, STOP saat FSM jalan).
- 124 passed, 2 skipped.

**Dua bug kembaran ditemukan saat menutup ini** — keduanya kelas yang sama persis
(nama field yang dibaca tak pernah berisi nilai yang diharapkan, tanpa test):

1. **`mission5.py` membaca `telem['mode']` untuk gate autonomous.** `mode` berisi
   mode ArduSub dari HEARTBEAT (`MANUAL`/`ALT_HOLD`); gate GUI ada di
   `control_mode`. Akibat: `_wait_for_autonomous()` menunggu selamanya, dan cek
   abort-saat-kembali-ke-Manual tak pernah menyala. Tak bergejala karena kedua
   jalur yang dipakai (`start_mission5` dan `--no-wait-autonomous`) memakai
   `wait_mode=False`, yang juga men-set `_require_auto=False`.
   → Diperbaiki: baca `control_mode`. Dikunci `test_handoff_kembali_ke_manual_memicu_abort`
   (terbukti gagal saat dimutasi balik).

2. **Handler `stop` di `rov_link.py` tidak memanggil `stop_mission5()`.** STOP
   menetralkan setpoint + disarm, tapi thread FSM terus jalan dan terus menulis
   `self.sp`. Dibuktikan: dengan perbaikan dilepas, FSM tetap melaju ke
   `M5_ASCEND` setelah STOP ditekan. Sekali operator menekan ARM — refleks wajar
   setelah STOP tak sengaja — gerakan lanjut dari state terakhir tanpa peringatan.
   → Diperbaiki: `stop_mission5()` dipanggil lebih dulu. Dikunci skenario C
   `verify_handoff.mjs` (terbukti gagal saat dimutasi).

**Satu footgun ikut ditutup:** `launch_sitl.py` tak pernah meneruskan `--vision`
ke `rov_link.py`, jadi FSM yang distart lewat toggle GUI (`start_mission5`) selalu
jatuh ke default `usb` — `--vision mock` diam-diam berarti dua hal berbeda, dan
yang kedua menggantung di mesin tanpa kamera. Sekarang diteruskan sebagai
`--fsm-vision-source`.

**Catatan lingkungan yang masih berlaku:**
- `RPI_ADDR` **wajib** `127.0.0.1` untuk Fase 1. Default `192.168.2.2` mengirim
  command ke ROV asli sementara telemetry tetap terlihat normal (server BIND
  :14551 dan menerima dari siapa pun) — perintah hilang tanpa jejak.
- `pytest` butuh `PYTHONPATH=` di mesin ini; PYTHONPATH ROS Humble membuat
  collect gagal (`ModuleNotFoundError: lark`).
- `npm run sim` menghasilkan telemetri palsu sendiri tiap 100 ms yang di-broadcast
  bersamaan dgn telemetri UDP nyata. Pakai `npm start`/`node server.js`.
- ~~`autonomy/README_SETUP_C.md` masih tidak ada~~ — **dibuat 2026-08-21**:
  jalur manual per-terminal (mock → rov_link → GUI → FSM) dengan kriteria sukses
  tiap langkah, dipakai saat `launch_sitl.py` gagal dan perlu tahu komponen mana
  yang putus. Empat jebakan lingkungan di bawah ini dipindahkan ke sana supaya
  ada di tempat orang mencarinya.
- Verifikasi visual GUI (gerak 3D, tombol fisik, F12) tetap **belum** tercakup —
  checklist browser di `TEST_CHECKLIST.md`.

### KS-01 — `KILL_SWITCH_DEADZONE` berkomentar skala salah (diperbaiki 2026-08-21)

**Gejala**: tak ada gejala runtime — justru itu masalahnya.

**Temuan**: komentar di `rov_link.py` menyebut ambang kill-switch memakai "skala
sama dgn axis GUI -100..100". Skala axis GUI sebenarnya **-1000..1000**
(`clampAxis` di `server.js`, `AXIS_RANGE` di `rov_axes.py`), jadi `15` yang
terbaca seolah "15% defleksi" sesungguhnya 1,5% skala penuh. Sekelas dengan
`vert`/`heave` dan `mode`/`control_mode`: angka yang benar, keterangan yang
salah, tanpa satu pun test.

**Kenapa tetap aman**: yang menyaring drift stik bukan ambang ini, melainkan
deadzone sisi-GUI (`DEFAULT_DEADZONE=0.12` + expo 1.6 di
`shared/joystick-profile.js`) — nilai di bawah deadzone dikirim sebagai 0.
Efek gabungannya kill-switch menyala di ~20% defleksi stik fisik, yang memang
diinginkan.

**Tindakan**: komentar diperbaiki + peringatan eksplisit jangan "membetulkan"
15→150 (takeover jadi lamban). **Ambangnya sengaja TIDAK diubah** — perilaku
sekarang benar. Dikunci `tests/test_rov_link.py` (6 test: kill-switch menyala di
atas ambang, diam di bawah, tak menyala untuk perintah FSM sendiri, diam saat
sudah manual, plus penjaga rentang ambang & skala clamp).

**Sisa backlog (butuh hardware):**
- [ ] Profil joystick mengizinkan `deadzone: 0`. Dengan setelan itu drift stik
      MEMANG bisa memicu abort palsu di tengah misi. Verifikasi setelan deadzone
      tim ≠ 0 sebelum lomba, atau paksakan batas bawah di `joystick-profile.js`.
- [ ] Rangkaian fisik stik→browser→server→`rov_link` hanya bisa dibuktikan
      dengan joystick nyata (butir terakhir checklist browser).

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

### BRIDGE-01 — `rov_agent.py` (program yang jalan di Pi) tak pernah menjalankan FSM (CLOSED 2026-08-22)

**Gejala** (trial kolam 22 Agu, run 1 & 2): tombol Autonomous ditekan di GUI, ROV
diam total — log menunjukkan depth rata 0,08–0,14 m selama 57 detik.

**Root cause**: ada dua program sisi-ROV di repo ini. `rov_agent.py` — yang
BENAR-BENAR jalan di Pi via `rov-agent.service` — tidak pernah mengimpor
`Mission5FSM` sama sekali; handler `control_mode` cuma mengganti string dan
mencetaknya. FSM lengkap & tervalidasi SITL ada, tapi di `autonomy/rov_link.py`,
program terpisah yang tak pernah dijalankan di Pi (dependensi `fsm/`, `vision/`
belum ter-deploy ke sana).

**Ditutup**: `rov_mission5_bridge.py` — menjalankan `Mission5FSM` **in-process**
di dalam `rov_agent.py` (bukan proses kedua yang akan rebutan `/dev/ttyACM0`
dengan `rov_agent.py`). `Mission5CommandAdapter` menumpang `command_listener`
yang sudah ada, jadi kill-switch operator tetap otomatis (tak ada tag `src`
yang bisa lupa dipasang, beda dari jalur loopback UDP `rov_link.py`).

**Susulan ditemukan & ditutup di hari yang sama** (run 3 & 4, jenis bug sama:
dua bagian tak sepakat, tanpa test yang menyeberang):
1. `joystick` dict menyimpan nilai TERAKHIR dari GUI dan tak pernah pulang ke
   nol sendiri — sisa >deadzone dari sesi manual sebelumnya dibaca sebagai
   "operator nyetir" pada iterasi pertama masuk autonomous, kill-switch abort
   sebelum FSM sempat gerak. → axis operator & axis FSM dinolkan sekaligus
   saat masuk autonomous.
2. `current_control_mode` diset SEBELUM `runner.start()` — selama ~1 detik
   `VisionPipeline.start()` membuka kamera, STOP dari kill-switch di jendela
   itu menemukan `_fsm` masih `None` dan diam, meninggalkan thread FSM yatim.
   → mode dipindah SESUDAH `start()` berhasil.

Dikunci `test_rov_agent_autonomous.py` (uji tingkat AST, karena `rov_agent.py`
butuh `pymavlink`/socket untuk diimpor).

Referensi: `rov_mission5_bridge.py`, `rov_agent.py` (handler `control_mode`),
`test_rov_agent_autonomous.py`, `log-m5/journal-22agu.txt`.

### CALIB-01 — Kalibrasi kamera dibuat pada resolusi salah (MITIGATED 2026-08-22, belum solusi asli)

**Gejala**: potensi tabrakan payload — PBVS `SERVO_TARGET_DIST=0.30 m` berhenti
jauh lebih dekat dari yang diperintahkan.

**Root cause**: `dwe_underwater.npz` dikalibrasi pada 4080×3072 (resolusi FOTO),
sedangkan stream kamera 1280×720 → `fx` ~3,2× terlalu besar → PBVS mengira QR
3,2× lebih jauh dari jarak asli. Pada `SERVO_TARGET_DIST=0.30 m`, ROV baru
berhenti di jarak asli ~9 cm.

**Sudah ditangani**: dipilih `dwe_trial2.npz` — dgn dua syarat (bukan rms saja,
yang cuma mengukur kecocokan model thd gambar kalibrasinya sendiri): resolusi
harus cocok stream, dan geometri (fx/cy) harus masuk akal dibanding kandidat
lain. Ditambah guard `_verify_calib_size()` di `vision/qr_detect.py`: mismatch
resolusi → PBVS dimatikan + ERROR di log, bukan gagal diam-diam.

**Bukan solusi asli** — `dwe_trial2.npz` "tidak berbahaya", belum akurat: `cy`
masih meleset +29,5% dari tengah frame.

**Sisa backlog (butuh kolam)**:
- [ ] Kalibrasi ulang DI AIR pada 1280×720 (`calibrate_camera.py`, papan
      tahan air) — lihat juga QR-01.

Referensi: `autonomy/rov_link.py` (`CALIB_*_DEFAULT`), `vision/qr_detect.py`
(`_verify_calib_size`).

### HOOK-02 — Deteksi hook mengembalikan seluruh frame di air keruh (CLOSED 2026-08-22)

**Gejala** (uji kolam 22 Agu): `detect_hook()` mengembalikan center persis di
tengah frame, area 917.542/921.600 px² (99,6%), confidence 1,00 — di TIAP
frame. Fatal utk FSM (bukan cuma berisik): `_hook_servo_step` membaca "hook
tepat di tengah, sangat dekat", `_state_hang`/`_state_dock` mengira sudah
sejajar sempurna sejak frame pertama dan mendudukkan payload di tempat salah.

**Root cause**: air keruh + kontras rendah membuat Canny menghasilkan satu
contour raksasa yang membungkus seluruh frame; solidity-nya ≈1 (bounding box =
frame) dan suku ukuran di `_score_contour` sudah jenuh, jadi skornya justru
"sempurna".

**Ditutup**: `HOOK_MAX_AREA_FRAC=0.25` — batas atas luas contour sbg fraksi
luas frame, diterapkan di ketiga jenjang deteksi (`_detect_by_color`,
`_detect_by_contour`, `_detect_by_hough`). Longgar dgn sengaja: hook pada jarak
docking hanya ~0,33% frame (`HOOK_TARGET_AREA`), bahkan di separuh jarak itu
~1,3% — 25% tak memotong deteksi sah, tapi membunuh kasus patologis di atas.

**Sisa backlog (butuh kolam)**:
- [ ] Verifikasi di docking sungguhan bahwa batas 25% tak ikut menolak
      deteksi sah jarak dekat (ukur area terukur vs batas, pola sama QR-01).

Referensi: `autonomy/vision/hook_detect.py`, `autonomy/tests/test_hook_detect.py`.

### GRIPPER-01 — Mismatch PWM gripper antar dua program (CLOSED 2026-08-22)

**Gejala**: tak ada gejala runtime langsung — potensi servo didorong melebihi
batas fisik.

**Root cause**: `autonomy/rov_link.py` mengirim PWM 1900/1100 ke gripper,
sedangkan `gripper_controller.py` (dipakai di Pi) sudah lama memakai 1580/1350
hasil kalibrasi nyata di tepi kolam (22 Agu). Travel aman gripper cuma sampai
1580/1350 — `rov_link.py` mendorong servo ~2× lebih jauh dari itu.

**Ditutup**: `GRIPPER_PWM_OPEN`/`GRIPPER_PWM_CLOSE` di `autonomy/rov_link.py`
disamakan ke 1580/1350.

Referensi: `autonomy/rov_link.py`, `gripper_controller.py`.

### DEPTH-PULSE-01 — Bias depth-set kontinu berebut channel-z dgn ArduSub di ALT_HOLD (fitur baru, belum divalidasi kolam)

**Gejala** (laporan pilot, 22 Agu): osilasi kedalaman saat `depth_up`/`depth_down`
ditekan di mode `ALT_HOLD`.

**Root cause**: bias depth-set kontinu (`DEPTH_BIAS_*` di `rov_pid.py`) cocok
utk `STABILIZE` (tak ada cascade PID kedalaman ArduSub di mode itu), tapi di
`ALT_HOLD`/`poshold` ArduSub SUDAH punya cascade PID sendiri yang menahan
kedalaman — bias kontinu jadi berebut channel `z` dengannya.

**Ditangani**: mode pulsa sekali-tembak khusus non-`STABILIZE`
(`depth_bias_is_continuous()` di `rov_modes.py` memilih jalur) — meniru
operator menyentuh-lalu-lepas stik heave: dorong `z` sesaat
(`DEPTH_PULSE_MAGNITUDE`/`DEPTH_PULSE_DURATION_S` di `rov_pid.py`), lalu
netral, biarkan cascade ArduSub menahan kedalaman baru sendiri.

**Sisa backlog (butuh kolam)**:
- [ ] `DEPTH_PULSE_MAGNITUDE`/`DEPTH_PULSE_DURATION_S` ditandai `ponytail:` di
      kode — tebakan awal, belum divalidasi data kolam. Kalibrasi
      `DEPTH_PULSE_DURATION_S` dulu (bukan magnitude, supaya rasa dorongan
      `STABILIZE` tak ikut berubah).

Referensi: `rov_pid.py` (`DEPTH_PULSE_*`), `rov_modes.py`
(`depth_bias_is_continuous`), `rov_agent.py` (`apply_depth_hold_bias`).

---

## 2. Analisis gap per fase (Fase 0–4)

| Fase | Status | Sudah ada | Yang kurang / TODO | Blocker |
|------|--------|-----------|--------------------|---------|
| **0** Visi di meja | ✅ hampir | servo+pose webcam test jalan; kalibrasi papan catur OK; deteksi QR JSON | Rekam nilai `invert_*` hasil uji ke config; formalkan pass/fail; **QR-01** (ditangani PR ini) | — |
| **1** SITL | ✅ VERIFIED 2026-08-21 | `launch_sitl.py` (1 perintah), `sitl_mock.py`, `rov_link.py`, GUI LIVE, `verify_handoff.mjs` | 3/3 run `DONE` 100/100; handoff & STOP 3/3 LULUS; 146 test hijau. Sisa: **checklist browser** (gerak 3D, tombol fisik, F12) — butuh mata | — (tak lagi blocker) |
| **2** Bring-up bench | 🟡 SEDANG JALAN | `VERIFIKASI_ARDUSUB.md` #1–7; `PERSIAPAN_FASE2-4.md` (run-book); Pixhawk tersambung, thruster/gripper direspons MAVLink; PWM gripper terkalibrasi tepi kolam 1580/1350 (**GRIPPER-01**); toggle Autonomous di Pi benar-benar menjalankan FSM (**BRIDGE-01**) | Cek arah 6–8 thruster belum lengkap tercatat; depth plateau 0,37 m belum ter-root-cause (lihat Fase 3); servo lampu (channel/PWM) belum diverifikasi | **Sisa hardware/waktu di kolam** |
| **3** Uji kolam & tuning | 🟡 SEDANG JALAN (trial 1: 22 Agu 2026) | `VERIFIKASI_ARDUSUB.md` M1–M8; config tunable (`--config`, `pool_kki_running.yaml`); CSV logging; **trial kolam 1** (`log-m5/journal-22agu.txt`) — 4 bug ditemukan & ditutup hari yang sama (**BRIDGE-01, CALIB-01, HOOK-02, GRIPPER-01**); fitur baru MARK/M5_REDIVE + depth-pulse ALT_HOLD | 7 item terbuka: root-cause depth plateau 0,37 m; kalibrasi kamera DI AIR (bukan tambalan `dwe_trial2.npz`); validasi `DEPTH_PULSE_*`; uji MARK→M5_REDIVE di kolam; verifikasi `HOOK_MAX_AREA_FRAC` tak menolak deteksi sah; verif ulang `invert_*`; sisa QR-01 (kurva jarak/exposure/fiducial) | **Trial kolam lanjutan** |
| **4** Latihan & lomba | 🔒 blocked (sebagian siap) | run-book hari-H + scoresheet; **logika M5_FALLBACK terverifikasi & dikunci 2 pytest**; checklist boot/pra-dive/umbilical sudah tertulis | Rehearsal 3× run; **drill fisik** tutup-lensa di kolam; eksekusi & hafalkan checklist | **Setup penuh + kolam** |

Legenda: ✅ selesai · 🟡 sedang jalan (kolam/hardware sudah mulai dipakai) · ⏳ bisa dikerjakan sekarang (tak butuh hardware) · 🔒 menunggu hardware/kolam.

---

## 3. Backlog tugas (dikerjakan setelah hardware/kolam tersedia)

**Fase 1 — SELESAI (2026-08-21), disisakan sbg rujukan cara mengulang:**
- [x] `tools/launch_sitl.py --fsm --vision mock --no-wait-autonomous --no-gui`
      → rantai misi 5 tuntas `DONE` via jalur MAVLink nyata (A 100/100, B & C 40/40).
- [x] Toggle GUI Manual↔Autonomous + STOP saat FSM berjalan → `node tools/verify_handoff.mjs` 3/3.
- [ ] **Sisa, butuh mata:** enam butir checklist browser di `TEST_CHECKLIST.md`
      (badge mode, gerak ROV 3D, F12 bersih, joystick fisik).

**Fase 2 (butuh hardware kering):**
- [x] `GRIPPER_SERVO_CH`/PWM — dikalibrasi nyata di tepi kolam (1580/1350),
      disamakan di kedua program (**GRIPPER-01**, 2026-08-22).
- [ ] Kerjakan sisa `VERIFIKASI_ARDUSUB.md` #1–7; catat konstanta `rov_link.py` yang perlu dibalik/disesuaikan
      (`Z_NEUTRAL`, tanda sumbu, `LIGHT_SERVO_CH`, `WATER_RHO`, mode `ALT_HOLD`).

**Fase 3 — trial 1 (22 Agu 2026), sisa terbuka:**
- [ ] **Root cause depth plateau 0,37 m.** `dive: 30→45` di
      `autonomy/config/pool_kki_running.yaml` sudah terpasang sbg diagnostik —
      jalankan run berikutnya: plateau bergeser lebih dalam → keterbatasan daya
      (3 thruster heave); plateau tetap 0,37 m → curigai tare/ballast (offset
      sensor depth saat armed di permukaan, trim berat ROV).
- [ ] Kalibrasi kamera DI AIR pada 1280×720 (`calibrate_camera.py`, papan
      tahan air) → ganti tambalan `dwe_trial2.npz` (**CALIB-01**), verifikasi
      `cy` tak lagi meleset 29,5% dari tengah.
- [ ] Validasi `DEPTH_PULSE_MAGNITUDE`/`DEPTH_PULSE_DURATION_S` (`rov_pid.py`,
      **DEPTH-PULSE-01**) dgn data kolam — kalibrasi durasi dulu, bukan
      magnitude.
- [ ] Uji **MARK→M5_REDIVE** di kolam sungguhan: tekan MARK saat payload
      tergantung di hook (misi 3 manual), jalankan misi 5 autonomous, cek log
      `[FSM] MARK gantungan dipakai` dan ROV kembali ke arah gantungan (bukan
      sapu buta timeout).
- [ ] Verifikasi `HOOK_MAX_AREA_FRAC=0.25` (**HOOK-02**) tak menolak deteksi
      sah jarak dekat — ukur area terukur vs batas saat docking sungguhan
      (`--csv`, pola sama QR-01).
- [ ] Kerjakan sisa `VERIFIKASI_ARDUSUB.md` M1–M8; verif ulang `invert_*`;
      tuang hasil tuning ke `config/mission5.local.yaml`.
- [ ] Selesaikan sisa **QR-01** (kurva jarak, exposure, fiducial, benchmark
      detektor).

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

**Perlu 1 trial kolam lagi utk validasi (bukan lagi opsional murni):**
- [ ] MARK/M5_REDIVE — ditulis 22 Agu 2026, belum pernah dicoba di kolam
      sungguhan (lihat checklist Fase 3 di atas).
- [ ] Depth-pulse ALT_HOLD (**DEPTH-PULSE-01**) — magnitude/durasi masih
      tebakan awal (lihat checklist Fase 3 di atas).

**Peningkatan opsional (nice-to-have):**
- [ ] Resolusi/preprocessing `VisionPipeline` (bukan hanya tool webcam) bila deteksi di Pi kurang.
- [ ] Squaring yaw PBVS (QR-02) bila terbukti stabil.
- [ ] Replay/telemetry logging terpadu untuk analisis pasca-run.
