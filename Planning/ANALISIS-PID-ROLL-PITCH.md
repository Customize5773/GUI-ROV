# TASK: Analisis & Rekomendasi Tuning PID Roll/Pitch Flight Controller

Agent yang menjalankan task ini bertugas melakukan analisis mendalam terhadap parameter PID (Proportional, Integral, Derivative) untuk sumbu **roll** dan **pitch** pada flight controller ArduSub, berdasarkan parameter aktual yang ada di repository dan gejala-gejala tuning yang telah diidentifikasi. Berikut adalah konteks lengkap dan scope yang harus dicakup.

---

## Konteks Proyek

Repositori ini adalah **GUI-ROV**, antarmuka web untuk mengontrol ROV (Remotely Operated Vehicle) 6-thruster berbasis **Pixhawk + ArduSub 4.5.7**. Sistem komunikasi menggunakan MAVLink (UDP 14550), dan GUI memiliki halaman Setup yang memungkinkan pengiriman perintah PID ke flight controller.

File-file terkait yang sudah ada:
- `rov_pid.py` — pemetaan gain PID GUI → parameter ArduSub + validasi batas
- `test_rov_pid.py` — unit test untuk pemetaan dan logika PID
- `parameters_ardusub.params` — dump parameter aktual dari Pixhawk wahana
- `attitude_filter.py` — complementary filter + EMA untuk roll/pitch/yaw
- `rov_modes.py` — dokumentasi mode penerbangan ArduSub

---

## Data Parameter Aktual dari Flight Controller

Berdasarkan `parameters_ardusub.params` (ArduSub 4.5.7), berikut adalah nilai **rate PID** yang sedang berjalan di wahana:

| Parameter | Nilai Aktual | Keterangan |
|-----------|-------------|------------|
| `ATC_RAT_RLL_P` | 0.135 | Gain Proportional roll |
| `ATC_RAT_RLL_I` | 0.090 | Gain Integral roll |
| `ATC_RAT_RLL_D` | 0.0036 | Gain Derivative roll |
| `ATC_RAT_RLL_IMAX` | 0.444 | Batas output integral roll |
| `ATC_RAT_PIT_P` | 0.135 | Gain Proportional pitch |
| `ATC_RAT_PIT_I` | 0.090 | Gain Integral pitch |
| `ATC_RAT_PIT_D` | 0.0036 | Gain Derivative pitch |
| `ATC_RAT_PIT_IMAX` | 0.444 | Batas output integral pitch |
| `ATC_RAT_YAW_P` | 0.180 | Gain Proportional yaw (sudah diekspos di GUI) |
| `ATC_RAT_YAW_I` | 0.018 | Gain Integral yaw (sudah diekspos di GUI) |
| `ATC_RAT_YAW_D` | 0.000 | Gain Derivative yaw (sudah diekspos di GUI) |

Parameter pendukung lain yang relevan:
- `ATC_RATE_R_MAX` = 0 (unlimited) — batas laju sudut roll
- `ATC_RATE_P_MAX` = 0 (unlimited) — batas laju sudut pitch
- `ATC_RATE_Y_MAX` = 180.0 — batas laju sudut yaw
- `ATC_RAT_RLL_FLTD` = 30.0 Hz — low-pass filter D roll
- `ATC_RAT_PIT_FLTD` = 30.0 Hz — low-pass filter D pitch
- `ATC_ANG_RLL_P` = 6.0 — angle controller P roll
- `ATC_ANG_PIT_P` = 6.0 — angle controller P pitch
- `ATC_INPUT_TC` = 0.150 s — smoothing input stik
- `AHRS_RP_P` = 0.2 — estimator roll/pitch

---

## Status Eksposur di GUI

Saat ini, `rov_pid.py` hanya mengekspos tuning untuk **yaw** dan **depth** melalui halaman Setup:

```python
PID_PARAM_MAP = {
    ("yaw", "p"): ("ATC_RAT_YAW_P", REAL32, 0.0, 1.0),
    ("yaw", "i"): ("ATC_RAT_YAW_I", REAL32, 0.0, 1.0),
    ("yaw", "d"): ("ATC_RAT_YAW_D", REAL32, 0.0, 0.05),
    ("depth", "p"): ("PSC_ACCZ_P", REAL32, 0.2, 1.5),
    ("depth", "i"): ("PSC_ACCZ_I", REAL32, 0.0, 3.0),
    ("depth", "d"): ("PSC_ACCZ_D", REAL32, 0.0, 0.4),
}
```

**Roll dan pitch BELUM diimplementasikan** di GUI. Tuning saat ini harus dilakukan via QGroundControl atau Mission Planner.

---

## Tugas yang Harus Dilakukan

Agent harus menyelesaikan pekerjaan berikut secara berurutan:

### 1. Analisis Kondisi Saat Ini

Lakukan analisis terhadap nilai PID roll/pitch yang ada di `parameters_ardusub.params`:

- **Proportional (P = 0.135):** Apakah sudah cukup untuk respons attitude ROV 6-thruster?identifikasi risiko respon lambat atau osilasi.
- **Integral (I = 0.090 dengan IMAX = 0.444):** Apakah IMAX terlalu besar/b kecil?identifikasi risiko overshoot atau windup.
- **Derivative (D = 0.0036):** Apakah D sudah memberikan efek damping yang memadai?identifikasi risiko overshoot atau osilasi tinggi.
- **Perbandingan roll vs pitch:** Apakah sinkronisasi nilai keduanya sudah tepat untuk ROV simetris?

### 2. Matrix Diagnosa Berdasarkan Gejala

Buat matriks yang menghubungkan gejala-gejala terbang/kolam dengan kemungkinan penyebab PID dan rekomendasinya:

| Gejala yang Diamati | Kemungkinan Penyebab | Aksi |
|---------------------|----------------------|------|
| Respon attitude lambat — ROV sulit diputar | P terlalu rendah | Naikkan P bertahap |
| Osilasi berfrekuensi tinggi — badan bergetar saat stabil | P terlalu tinggi atau D terlalu rendah | Turunkan P, naikkan D |
| Overshoot besar — lewat setpoint sebelum kembali | D terlalu rendah, atau IMAX terlalu besar | Naikkan D, kecilkan IMAX |
| Rising time lambat tapi stabil | P terlalu rendah, I terlalu tinggi | Naikkan P, turunkan I |
| Hunting / limit cycle — osilasi kecil menetap | D terlalu tinggi atau IMAX terlalu kecil | Turunkan D, naikkan IMAX |
| Satu sisi lebih berat — roll/pitch condong | I terlalu rendah atau trim salah | Naikkan I, centang AHRS_TRIM |
| Motor mendengung tapi ROV tidak bergerak | Rate max terlalu kecil atau P terlalu tinggi | Naikkan ATC_RATE_R/P_MAX |

### 3. Rekomendasi Nilai Target

Berdasarkan analisis, berikan rekomendasi nilai PID yang diharapkan untuk ROV 6-thruster standar:

**Target awal untuk manual tuning:**
```
ATC_RAT_RLL_P = 0.25 – 0.50
ATC_RAT_RLL_I = 0.05 – 0.12
ATC_RAT_RLL_D = 0.006 – 0.012
ATC_RAT_RLL_IMAX = 0.444 (tetap atau sesuaikan)

ATC_RAT_PIT_P = 0.25 – 0.50
ATC_RAT_PIT_I = 0.05 – 0.12
ATC_RAT_PIT_D = 0.006 – 0.012
ATC_RAT_PIT_IMAX = 0.444 (tetap atau sesuaikan)
```

### 4. Rekomendasi Parameter Pendukung

Identifikasi parameter-parameter pendukung yang perlu disesuaikan bersamaan dengan PID:

- `ATC_RATE_R_MAX` dan `ATC_RATE_P_MAX` — batas laju sudut
- `ATC_RATE_FF_ENAB` — feedforward rate
- `ATC_RAT_RLL_FLTD` dan `ATC_RAT_PIT_FLTD` — low-pass filter D
- `ATC_INPUT_TC` — smoothing input stik
- `AHRS_RP_P` — estimator attitude

### 5. Prosedur Tuning yang Disarankan

Jelaskan prosedur tuning yang harus diikuti oleh tim:

#### Opsi A: Autotune (Cara Paling Aman)
1. ROV di kolam danggal, aman, dengan payload penuh
2. Set mode ke AUTOTUNE via QGroundControl
3. Jalankan autotune satu per satu: Roll → Pitch → Yaw
4. Catat nilai hasil autotune dan validasi di kolam

#### Opsi B: Manual Tuning Step-by-Step
1. **Tahap 1 — Damping (D):** Mulai P=0, I=0, naikkan D sampai osilasi tepat sebelum muncul, lalu kurangi 20-30%
2. **Tahap 2 — Respons (P):** Naikkan P bertahap (+25% per iterasi), uji step input 15°, stop saat ada osilasi ringan, kurangi 15-20%
3. **Tahap 3 — Koreksi Statik (I):** Naikkan I sampai ROV bisa menahan sudut tanpa drift
4. **Tahap 4 — Validasi IMAX:** Sesuaikan IMAX jika ada windup yang jelas

### 6. Catatan Kendala dan Risiko

- Roll dan pitch adalah **rate controller** yang sensitif terhadap massa, distribusi berat, dan hidrodinamika ROV. Nilai yang cocok untuk satu ROV tidak menjamin cocok untuk ROV lain.
- Tuning via GUI tanpa instrumentasi (log CSV, scope, PID_TUNING message) sangat berisiko. Autotune di FC jauh lebih aman.
- Perbedaan nilai antara roll dan pitch mungkin diperlukan jika distribusi berat tidak simetris.
- `ATC_RATE_R_MAX = 0` (unlimited) berisiko jika P dinaikkan terlalu tinggi — pertimbangkan set ke 150-300 deg/s.

### 7. Output yang Diharapkan

Agent harus menghasilkan:

1. **Laporan analisis kondisi saat ini** — status P, I, D, IMAX, dan parameter pendukung
2. **Matrix diagnosa** — tabel gejala, penyebab, dan aksi
3. **Rekomendasi nilai target** — rentang nilai yang diharapkan untuk roll/pitch
4. **Prosedur tuning** — langkah-langkah autotune dan manual tuning
5. **Catatan implementasi GUI (opsional)** — jika roll/pitch ingin ditambahkan ke `rov_pid.py`, berikan petunjuk modifikasi kode

---

## Format Output

Simpan hasil analisis ini dalam file markdown di folder `Planning/` dengan nama:

```
Planning/ANALISIS-PID-ROLL-PITCH.md
```

Pastikan file mencakup:
- Ringkasan eksekutif
- Tabel data parameter aktual
- Analisis setiap komponen PID
- Matrix diagnosa gejala
- Rekomendasi nilai
- Prosedur tuning
- Referensi ke file-file terkait di repository

---

## Referensi File di Repository

- `rov_pid.py` — pemetaan PID dan validasi
- `test_rov_pid.py` — test untuk logika PID
- `parameters_ardusub.params` — dump parameter aktual dari Pixhawk
- `attitude_filter.py` — filter attitude roll/pitch/yaw
- `rov_modes.py` — dokumentasi mode ArduSub
- `CONTROL-MAPPING.md` — pemetaan kontrol ROV

---

## Catatan untuk Agent

- Analisis ini bersifat **read-only** terhadap parameter yang ada. Jangan memodifikasi `rov_pid.py`, `parameters_ardusub.params`, atau file apapun di repository kecuali ditentukan lain.
- Fokus pada analisis teknis dan rekomendasi yang dapat diimplementasikan oleh tim pengembang.
- Jika ada ketidaksesuaian antara teori PID dan implementasi ArduSub, jelaskan perbedaannya dengan jelas.
- Gunakan bahasa Indonesia sesuai konteks proyek.
