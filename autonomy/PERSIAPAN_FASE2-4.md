# Persiapan Fase 2–4 — Run-book Operasional (Misi 5, KKI 2026)

Panduan langkah-demi-langkah untuk **Fase 2 (bring-up bench)**, **Fase 3 (uji kolam &
tuning)**, dan **Fase 4 (latihan & lomba)**. Dokumen ini **operasional** (untuk dijalankan
saat hardware/kolam tersedia); logika & checklist verifikasi teknis ada di
`VERIFIKASI_ARDUSUB.md` — di sini kita **rujuk** ke sana, bukan menyalin.

Cetak / buka di tablet saat di lapangan. Centang `[ ]` dan isi kolom hasil.

**PRINSIP KESELAMATAN (berlaku semua fase):** selalu siap **STOP / Spasi** (netral +
disarm). Uji bertahap sederhana → kompleks. Jangan ARM di darat dekat orang tanpa
pelindung baling-baling.

---

## FASE 2 — Bring-up hardware kering (ROV di darat / ember, TANPA kolam penuh)

Tujuan: pastikan tiap axis & aktuator fisik **benar** sebelum ROV masuk air.
Detail cara cek + konstanta → `VERIFIKASI_ARDUSUB.md` tabel A (#1–7).

### 2.0 Prasyarat & boot
- [ ] Pixhawk/ArduSub menyala, ESC/thruster tersambung, baterai terisi.
- [ ] `rov_link.py` diarahkan ke hardware nyata (bukan SITL): sesuaikan `--mavlink`
      (mis. `/dev/ttyACM0`), jalankan; tunggu `[MAV] terhubung: system=…`.
- [ ] GUI LIVE (`RPI_ADDR=<ip> npm start`) → telemetri masuk (heading/depth/attitude).
- [ ] **Safety switch fisik di board Pixhawk** — LED merah berkedip = terkunci,
      output motor diblokir walau MAVLink sudah melaporkan armed. Tekan SEKALI
      sampai LED berhenti berkedip. Sekali per siklus nyala power, BUKAN tiap
      ARM/DISARM — kalau berkedip lagi tiap kali ARM, berarti board sempat
      reboot/power siklus di antaranya, dicek dulu sebelum lanjut. Terkonfirmasi
      2026-08-25: ini penyebab "perlu delay sebelum bisa dikendalikan joystick"
      — bukan bug kode (`rov_agent.py`/GUI tak punya gerbang delay apa pun
      setelah ARM). Bisa dinonaktifkan permanen via param `BRD_SAFETYENABLE=0`
      kalau tim memutuskan begitu — keputusan keselamatan, bukan default.
- [ ] Cek **STOP** dulu sebelum apa pun: tekan STOP → tak ada gerak, disarm.

### 2.1 Arah thruster (VERIFIKASI #1) — paling kritis
ARM, mode MANUAL. Uji satu per satu, amati arah gerak nyata:

| Command | Harusnya | Hasil (✓/✗) | Bila ✗ → ubah di `rov_link.py` |
|---------|----------|-------------|--------------------------------|
| surge + (W) | maju | | balik tanda `x` di `send_manual_control()` |
| sway + (D) | geser kanan | | balik tanda `y` |
| yaw + (E) | putar CW | | balik tanda `r` |

### 2.2 Vertikal & depth-hold (VERIFIKASI #2, #5, #6)
- [ ] Mode DEPTH HOLD ada (`mode_mapping()` memuat `ALT_HOLD`).
- [ ] `vert=0` → tahan kedalaman; `vert<0` (F) → turun; `vert>0` (R) → naik.
      (bila terbalik → `Z_NEUTRAL`/rumus `z` di `rov_link.py`).
- [ ] Depth telemetri wajar vs kedalaman nyata (ember/penggaris). Air tawar `WATER_RHO=997`.

### 2.3 Gripper & lampu (VERIFIKASI #3, #4)
- [ ] `gripper close` → menutup; `gripper open` → membuka (cek `SERVOn_FUNCTION` di QGC).
      Sesuaikan `GRIPPER_SERVO_CH`, `GRIPPER_PWM_OPEN/CLOSE`.
- [ ] Lampu on/off (`LIGHT_SERVO_CH`, PWM) — bila dipakai untuk bantu deteksi QR.

### 2.4 Failsafe & dual-cam (VERIFIKASI #7 + §4.7.3)
- [ ] ARM/DISARM via GUI benar; **STOP → netral + disarm seketika**.
- [ ] Feed **CAM 1 (BOTTOM)** & **CAM 2 (WALL)** tampil bersamaan di halaman Camera.

**DoD Fase 2:** semua ✓ di atas; catat nilai konstanta final yang diubah di
`rov_link.py`. Baru boleh masuk air.

---

## FASE 3 — Uji kolam & tuning (loop tertutup di air)

Tujuan: kalibrasi ulang & tuning parameter dgn kondisi air. Detail cek → 
`VERIFIKASI_ARDUSUB.md` tabel B (M1–M8). **Semua tuning masuk `config/mission5.local.yaml`**
(salin dari `config/mission5.example.yaml`) — jangan edit `fsm/mission5.py` di lapangan.

### 3.1 Kalibrasi kamera DI AIR (kritis — refraksi ubah focal length)
- [ ] `python tools/calibrate_camera.py --auto --save-dir calib_air --cols 9 --rows 6 --square <mm>`
      dgn papan catur **tahan air**, di balik housing/dome yang SAMA dgn misi. Target RMS < 1.0 px.
- [ ] Simpan `.npz` → dipakai `--calib` saat run PBVS.

### 3.2 Diagnosa deteksi QR (isu QR-01) — pakai CSV logging
- [ ] `python tools/servo_webcam_test.py --device 0 --csv kolam.csv --cam-width 1280 --cam-height 720`
      Gerakkan QR dari dekat → jauh; saat keluar dicetak **detection-rate %** + rentang jarak.
- [ ] Plot/lihat `kolam.csv`: pada jarak berapa deteksi mulai gagal? → tetapkan jarak kerja.
- [ ] Bila deteksi buruk: naikkan resolusi/lampu, cek exposure kamera (lihat `PR-AUTONOMY.md` QR-01).

### 3.3 Worksheet tuning parameter
Isi kolom "nilai tuned" hasil uji, lalu tulis ke `config/mission5.local.yaml`:

| Parameter (config key) | Default | Nilai tuned | Cara verifikasi (VERIFIKASI) |
|------------------------|---------|-------------|------------------------------|
| `invert.sway/vert/surge/yaw` | false | | **M1** — QR ke kanan → error MENGECIL; balik bila membesar |
| `docking.servo_target_dist` (PBVS) | 0.30 m | | **M2** — gripper tepat menjangkau payload saat ALIGNED |
| `docking.servo_target_area` (IBVS) | 3000 px² | | **M2** — sda, mode tanpa kalibrasi |
| `depth.hook_depth` | 0.45 m | | **M4** — REDIVE berhenti di depth payload |
| `depth.target_bottom` | 0.70 m | | sesuaikan kedalaman kolam venue |
| `m5_mechanics.unhook_vert/surge` | 30 / -20 | | **M5** — angkat lalu tarik melepas hook, tak nyangkut |
| `m5_mechanics.unhook_lift_t/pull_t` | 3.0 / 2.0 s | | **M5** — durasi cukup utk lepas |
| `m5_mechanics.engage_surge` | 15 | | **M6** — payload masuk gripper mulus |
| `wall_heading.A/B/C/D` | 270/90/0/180 | | orientasi kompas vs tata letak kolam venue |
| `docking.servo_kp_yaw` | 0.0 | | **M7** — biarkan 0 kecuali squaring terbukti stabil; validasi pasif pakai `python -m autonomy.tests.pool_yaw_validation --calib kalib.npz --qr-size 0.04 --device 0 --duration 30` |

### 3.4 Uji rantai misi 5 & handoff (M8)
- [ ] `python fsm/mission5.py --vision usb --device 0 --calib calib_air.npz --qr-size 0.04 \
      --config config/mission5.local.yaml --start-state M5_REDIVE`
- [ ] Operator kemudikan misi 1–4 MANUAL via GUI; setelah docking permukaan, toggle
      header → AUTONOMOUS → FSM jalankan misi 5. Toggle balik → FSM abort bersih.
- [ ] Uji **loss-of-lock**: halangi QR sesaat saat docking → ROV dead-reckon hold lalu
      sapu terarah (bukan langsung hilang) → pulih. (fitur `M5_DOCK`/`M5_ENGAGE`.)

**DoD Fase 3:** docking closed-loop ambil & lepas payload berulang (≥5×);
`config/mission5.local.yaml` terisi nilai tuned.

---

## FASE 4 — Latihan penuh & hari-H

### 4.1 Urutan boot (hafalkan)
1. Pixhawk/ROV power on → 2. `rov_link.py` (tunggu heartbeat) → 3. `server.js`
(`npm start`) → 4. GUI di laptop operator → 5. `fsm/mission5.py … --start-state M5_REDIVE`
(FSM menunggu mode autonomous). *(Alternatif uji: `tools/launch_sitl.py` untuk mock/SITL.)*

### 4.2 Checklist pra-dive (tiap run)
- [ ] Baterai penuh; **umbilical kabel** tersambung (KKI: wireless dilarang), satu subnet.
- [ ] STOP berfungsi (uji sekali). Gripper OPEN di posisi awal.
- [ ] Kamera BOTTOM & WALL tampil; QR payload terpasang & bersih.
- [ ] `--config` menunjuk `mission5.local.yaml` yang benar; `--calib` = kalibrasi air.
- [ ] Identitas tim & jam tampil di GUI (§4.7.3); auto screenshot/logging aktif.

### 4.3 Strategi run (rekomendasi: 1–4 manual, 5 autonomous)
- [ ] Misi 1–4 dikemudikan operator (MANUAL). Setelah docking permukaan (misi 4)…
- [ ] …toggle header GUI **MANUAL → AUTONOMOUS** → FSM otomatis jalankan misi 5.
- [ ] Pantau log FSM: `M5_REDIVE → M5_DOCK → M5_ENGAGE → M5_UNHOOK → M5_ASCEND → DONE`.

### 4.4 Drill fallback (latih sebelum lomba)
- [ ] Simulasikan QR gagal saat docking (tutup lensa) → FSM degradasi ke `M5_FALLBACK`
      (timed) → tetap dapat kredit parsial, bukan ABORT total. Pastikan tim paham ini normal.

### 4.5 Scoresheet per-run (rehearsal ≥3×)
| Run | m1 | m2 | m3 | m4 | m5 | Total | Waktu | Catatan |
|-----|----|----|----|----|----|-------|-------|---------|
| 1 | | | | | | /100 | | |
| 2 | | | | | | /100 | | |
| 3 | | | | | | /100 | | |

### 4.6 Teardown
- [ ] Disarm, matikan FSM (Ctrl+C) & rov_link, angkat ROV, bilas, keringkan konektor.
- [ ] Backup log/CSV/screenshot run.

**DoD Fase 4:** 3× run sukses berturut skor stabil; tim hafal boot order + prosedur
pemulihan fallback.

---

## Referensi
- `VERIFIKASI_ARDUSUB.md` — checklist teknis #1–7 (hardware) & M1–M8 (kolam).
- `ROADMAP_MISI5.md` — peta jalan Fase 0–4 + DoD.
- `config/mission5.example.yaml` — semua parameter tunable + penjelasan.
- `PR-AUTONOMY.md` — backlog & isu tertunda (mis. QR-01 detail).
- `SITL_SETUP.md`, `README_SETUP_C.md` — setup SITL/rov_link.
