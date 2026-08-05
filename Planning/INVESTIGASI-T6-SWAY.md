# Investigasi: Kelemahan Thruster 6 dalam Kontrol Sway

Status: **selesai** (analisis + satu bug label diperbaiki) — sisanya rekomendasi.

## 1. Ringkasan eksekutif

**T6 adalah satu-satunya aktuator untuk axis lateral/sway** pada layout
BlueROV1 "3-2-1" yang dipakai HydroShip (T1,T2 = surge+yaw; T3,T4,T5 = heave;
T6 = sway). Berbeda dari axis lain yang punya cadangan (redundan), degradasi
atau kegagalan T6 berarti ROV **kehilangan seluruh kemampuan sway**, bukan
sekadar berkurang otoritasnya.

Investigasi ini menemukan bahwa risiko itu:
- tidak diberi perlakuan khusus di jalur input (deadzone/expo sway = generik,
  sama seperti axis lain),
- tidak dideteksi otomatis (tidak ada fault-detection per-motor kontinu;
  hanya uji manual sekali klik di panel Setup; tabel failsafe resmi hanya
  mencakup kegagalan link/komunikasi, bukan motor individual),
- tidak dimodelkan di simulasi autonomy (`sim_plant.py` memperlakukan sway
  sebagai axis penuh normal),
- dan disertai **satu bug label UI konkret**: halaman Telemetry melabeli T6
  sebagai "Vertical", padahal T6 seharusnya "Horizontal" (lateral) menurut
  tabel resmi — sudah diperbaiki (lihat §8).

## 2. Kronologi & koreksi hipotesis awal

Permintaan investigasi awal menyebut "kelemahan Thruster 6 dalam kontrol
**yaw**". Setelah ditelusuri ke tabel resmi frame & mixing
(`CONTROL-MAPPING.md:264-271`), ternyata:

- T6 punya **faktor Yaw = 0** — T6 tidak berkontribusi pada torque yaw sama
  sekali. Yaw sepenuhnya dibangkitkan oleh T1/T2 (faktor ±1.0), yang justru
  **berpasangan/redundan**.
- T6 punya **faktor Roll = −0.25** sebagai efek samping mekanik, karena T6
  adalah satu-satunya thruster lateral tanpa pasangan penyeimbang.

Artinya gejala "ROV terasa berputar/melenceng saat manuver samping" yang
memicu kecurigaan ke arah yaw kemungkinan besar adalah **roll parasit** dari
T6 saat sway dipakai — bukan masalah yaw. User mengoreksi fokus investigasi
ke **sway**, yang memang axis tempat T6 benar-benar krusial.

## 3. Bukti teknis: T6 sebagai single point of failure (SPOF) sway

Redundansi per axis pada layout 3-2-1 (`CONTROL-MAPPING.md:249-253,264-271`):

| Axis        | Thruster       | Jumlah aktuator | Redundan? |
|-------------|----------------|:---:|:---:|
| Surge + Yaw | T1, T2         | 2   | Ya  |
| Heave       | T3, T4, T5     | 3   | Ya  |
| **Lateral / Sway** | **T6**  | **1**   | **Tidak** |

Faktor kontribusi axis T6 (`CONTROL-MAPPING.md:271`):

| Motor | Roll  | Pitch | Yaw | Throttle | Forward | Lateral |
|-------|-------|-------|-----|----------|---------|---------|
| T6    | −0.25 | 0     | 0   | 0        | 0       | **1.0** |

Dokumen `CONTROL-MAPPING.md:249-253` sudah mengakui efek roll parasit sebagai
**konsekuensi desain yang disengaja** ("bukan bug, melainkan konsekuensi tata
letak 3-2-1"). Namun dokumen itu **tidak membahas implikasi operasionalnya**:
degradasi/kegagalan T6 tidak hanya menambah roll parasit, tapi menghilangkan
**100% kemampuan sway** — kelas risiko yang berbeda dari thruster redundan
lain (mis. kalau T3 lemah, heave masih dibantu T4/T5).

## 4. Celah di jalur input & kontrol sway

Jalur command sway dari joystick/UI **tidak diperlakukan berbeda** dari axis
lain, meski secara hardware jauh lebih rapuh:

- `shared/joystick-profile.js:70,132-133,177-179,203-211` — axis Y (sway)
  memakai `DEFAULT_DEADZONE = 0.12` dan `DEFAULT_EXPO = 1.6`, identik dengan
  surge/heave/yaw. Tidak ada kompensasi gain atau limiter khusus untuk fakta
  bahwa sway non-redundan.
- `rov_axes.py:20,26,32-33,48-59` — `AXIS_RANGE["sway"]` sama dengan axis
  lain; `clamp_axis()` generik; `axes_to_manual_control()` memetakan sway
  langsung ke field `y` MANUAL_CONTROL tanpa scaling berbeda.

Konsekuensi: operator tidak mendapat sinyal apa pun (visual, haptic, atau
software) bahwa axis sway lebih rentan terhadap kegagalan tunggal dibanding
axis lainnya.

## 5. Celah deteksi kegagalan T6

- `rov_agent.py:747-780` (`run_motor_test()`) — hanya menjalankan
  `MAV_CMD_DO_MOTOR_TEST` sebagai **uji manual sekali klik** dari panel Setup.
  Bukan monitoring berkelanjutan; tidak ada logic yang membandingkan hasil
  test T6 terhadap ekspektasi atau menandainya "gagal".
- `CONTROL-MAPPING.md` §6, baris 289-314 (tabel Safety & failsafe) — seluruh
  baris tabel hanya mencakup kegagalan **link/komunikasi**: stale link
  (>0.5s), WS terputus, gamepad tercabut, E-Stop. **Tidak ada satu baris pun**
  untuk overcurrent atau fault motor individual, apalagi penanganan khusus
  untuk T6 yang tanpa redundansi.
- `public/js/pages/telemetry.js:8` — `OVERCURRENT = 10` A berlaku identik
  untuk keenam thruster; `_renderThrusters()` (~baris 130-150) memberi status
  "High"/"Normal"/"No data" yang sama persis untuk semua thruster, tanpa
  severity lebih tinggi untuk T6 walau secara fungsional kegagalannya jauh
  lebih parah (SPOF vs redundan).

## 6. Dampak ke autonomy / misi docking

- `autonomy/control/visual_servo.py` dan `autonomy/fsm/mission5.py:706-810` —
  koreksi lateral (`sway`) dihitung via PID dan dikirim tiap siklus kontrol
  selama fase approach/centering docking, sama seperti axis lain.
- Jika T6 lemah/tidak responsif: `sway` tetap dihitung dan dikirim, tapi ROV
  tidak bergerak lateral sama sekali. Tidak ada guard yang mendeteksi
  "commanded sway tapi posisi/error lateral tidak berkurang" — secara logis
  misi bisa macet/timeout di state alignment tanpa pesan diagnosis spesifik
  "T6 gagal"; operator hanya melihat mission stuck/gagal generik.
- `autonomy/tests/sim_plant.py:136,146-199,302-303` — plant simulasi
  mengintegrasikan `sway` sebagai axis penuh normal (memengaruhi roll, rx,
  hkx via `K_SWAY_X`), **tanpa model degradasi/kegagalan T6**. Akibatnya
  skenario "T6 gagal → sway nol saat docking" tidak pernah diuji oleh
  test suite autonomy yang ada.

## 7. Keterbatasan bukti

File CSV trial yang sebelumnya dianalisis
(`hydroship_telemetry_trial1*.csv`) hanya berisi kolom
`timestamp, yaw_deg, depth_m, pitch_deg, roll_deg` — **tidak ada data
arus/current per-thruster**. Klaim "T6 lemah secara elektrikal/mekanikal"
karena itu **tidak bisa dibuktikan langsung dari data historis yang ada**.
Investigasi ini bersifat analisis arsitektur & desain sistem berdasarkan kode
dan dokumentasi, bukan forensik data telemetri. Lihat rekomendasi R6 untuk
menutup celah ini di trial berikutnya.

## 8. Yang sudah diperbaiki di iterasi ini

**Bug label** di `public/js/pages/telemetry.js` (array `THRUSTERS`, sekitar
baris 17-22): label `type` (Horizontal/Vertical) yang ditampilkan di kartu
thruster halaman Telemetry tidak cocok dengan tabel resmi.

| Thruster | Label lama | Label benar (CONTROL-MAPPING.md §5.1) |
|----------|:---:|:---:|
| T1 | Horizontal | Horizontal ✓ (tidak berubah) |
| T2 | Horizontal | Horizontal ✓ (tidak berubah) |
| T3 | Horizontal | **Vertical** (diperbaiki) |
| T4 | Horizontal | **Vertical** (diperbaiki) |
| T5 | Vertical | Vertical ✓ (tidak berubah) |
| T6 | Vertical | **Horizontal** (diperbaiki) |

Akibat sebelumnya: operator yang memantau kartu T6 di halaman Telemetry bisa
salah paham bahwa T6 adalah thruster vertikal (heave), padahal T6 justru
thruster horizontal/lateral satu-satunya yang paling kritis untuk sway.
Array sudah disinkronkan dengan `MOTOR_LAYOUT` di
`public/js/pages/setup.js:29-36` (yang sudah benar sebelum perbaikan ini) dan
diberi komentar rujukan ke `CONTROL-MAPPING.md §5.1` agar tidak drift lagi.
Perubahan murni tekstual — `type` hanya dirender sebagai `<small>` di kartu
thruster, tidak dipakai untuk logic/ikon/status.

## 9. Rekomendasi berprioritas

| # | Prioritas | Masalah | Usulan | File target |
|---|-----------|---------|--------|--------------|
| R1 | Tinggi | SPOF T6 tidak terdokumentasi sebagai risiko operasional | Tambah sub-bagian "Redundansi & single point of failure" di §5.1, dan baris baru di tabel failsafe §6 untuk overcurrent/fault motor individual | `CONTROL-MAPPING.md` |
| R2 | Sedang | Severity T6 di UI Telemetry sama dengan thruster redundan | Beri ambang/label khusus ("SPOF — sway hilang") saat T6 abnormal atau tidak ada data | `public/js/pages/telemetry.js` |
| R3 | Sedang | Simulasi autonomy tidak menguji kegagalan T6 | Tambah parameter degradasi (`sway_efficiency` 0.0–1.0) di plant + kasus uji "T6 gagal" | `autonomy/tests/sim_plant.py`, `autonomy/tests/test_mission5.py` |
| R4 | Sedang | Mission docking macet generik saat sway tidak responsif | Guard "commanded sway persisten tapi error lateral tidak mengecil" → abort dengan alasan spesifik | `autonomy/control/visual_servo.py`, `autonomy/fsm/mission5.py` |
| R5 | Rendah | Deadzone/expo sway belum dikompensasi untuk non-redundansi | Evaluasi ulang setelah data arus (R6) tersedia | `shared/joystick-profile.js` |
| R6 | Prasyarat R2/R5 | Tidak ada data arus per-thruster di log trial | Tambah kolom arus per-thruster ke logging CSV telemetri | logging/recording telemetri |
| R7 | Rendah, no-code | Tidak ada checklist operasional untuk risiko T6 | Checklist pre-dive: wajib uji T6 lewat panel Thruster Test sebelum misi | prosedur operasi (non-kode) |

## 10. Referensi file & baris

- `CONTROL-MAPPING.md:242-286` — §5.1 Calibration & Thruster Layout, tabel
  faktor axis per motor
- `CONTROL-MAPPING.md:289-314` — §6 Safety & failsafe
- `public/js/pages/telemetry.js:8,17-22,~74,~130-150` — ambang overcurrent,
  array `THRUSTERS` (diperbaiki), render kartu, `_renderThrusters()`
- `public/js/pages/setup.js:29-36` — `MOTOR_LAYOUT`, referensi label yang
  sudah benar
- `shared/joystick-profile.js:70,132-133,177-179,203-211` — deadzone/expo
  per-axis
- `rov_axes.py:20,26,32-33,48-59` — `AXIS_RANGE`, `clamp_axis()`,
  `axes_to_manual_control()`
- `rov_agent.py:747-780` — `run_motor_test()`
- `autonomy/control/visual_servo.py`; `autonomy/fsm/mission5.py:706-810` —
  perhitungan & pengiriman koreksi sway saat docking
- `autonomy/tests/sim_plant.py:136,146-199,302-303` — model plant sway di
  simulasi
