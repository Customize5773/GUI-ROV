# Cara Kerja GUI HYDROSHIP terhadap Misi ROV KKI 2026

Dokumen ini menjelaskan **bagaimana operator menggunakan GUI** untuk menjalankan
rangkaian misi ROV KKI 2026 (5 tahapan, §4.7.3 Panduan), tahap demi tahap, lengkap
dengan halaman, kontrol, data yang dipantau, dan indikator keberhasilan.

---

## 1. Arsitektur singkat

```
   Browser (GUI)  <— WebSocket :8080 —>  server.js  <— UDP —>  Raspberry Pi (ROV)
   render + kontrol                      jembatan          :14551 telemetri masuk
                                                           :14550 command keluar
```

- **Telemetri** (heading, depth, roll, pitch, temp, voltage, armed, light, mode) mengalir
  dari ROV → server → semua tab GUI tiap ~100 ms.
- **Command** dari GUI → server → ROV via UDP. Daftar command di §3.
- Tanpa hardware, jalankan `npm run sim` untuk telemetri tiruan (lihat `README.md`).
- Larangan KKI: **tanpa wireless** selama misi — semua lewat kabel umbilical.

## 2. Peta antarmuka

**Header (selalu tampil):**
| Elemen | Fungsi |
|---|---|
| Link pill | Status koneksi: ONLINE / SIMULASI / OFFLINE |
| Identitas | Nama tim + perguruan tinggi (atur di Setup → Team Identity) |
| Jam | Hari, tanggal, waktu live |
| **MANUAL / AUTONOMOUS** | Toggle mode kontrol (kirim `control_mode`) |
| **ALARM** | Bisukan alarm kedalaman |
| **LIGHT** | Lampu ROV on/off |
| **ARM / DISARMED** | Mengaktifkan/menonaktifkan thruster |
| **STOP** | Failsafe: netralkan SELURUH thruster seketika (juga tombol Spasi) |

**Strip telemetri:** HEADING · DEPTH · **ALT** (ketinggian dari dasar = `POOL_DEPTH − depth`) ·
ROLL · PITCH · TEMP · VOLT · LATENCY. Readout DEPTH **berkedip merah + alarm** saat
`depth ≥ DANGER_DEPTH`.

**5 halaman (sidebar):**
| Halaman | Peran utama dalam misi |
|---|---|
| **Control** | Mengemudikan ROV (digital twin 3D, sumbu Surge/Sway/Yaw/Vertical, keyboard), kamera utama, depth tape, console |
| **Camera** | 2 kamera (BOTTOM + WALL) + **deteksi QR** → sisi A/B/C/D |
| **Mission** | Peta **trajectory** posisi ROV titik awal → akhir |
| **Telemetry** | Grafik Yaw/Depth/Pitch/Roll + status 6 thruster + rekam CSV |
| **Setup** | Identitas tim, URL kamera, thruster, PID, kedalaman kolam, akses mobile |

## 3. Command yang dikirim GUI ke ROV

| Command | Nilai | Dari |
|---|---|---|
| `arm` | true/false | tombol ARM |
| `light` | true/false | tombol LIGHT |
| `stop` | true | tombol STOP / Spasi |
| `control_mode` | "manual" / "autonomous" | toggle header |
| `surge`,`sway`,`yaw`,`vert` | −100..100 | input axis / keyboard (W/S, A/D, Q/E, R/F) |
| `set_surface` | true | "Set Surface Level" (Depth = 0) |
| `mode` | standby/drycal/manual/hold | tab mode pilot |
| `controller` | Keyboard/Gamepad/Meta Quest | tab controller |
| `thruster_config` | objek mixer/PWM/gain/reverse | Setup → Thruster |
| `pid` | gain yaw/depth | Setup → PID |
| `pool_depth` | meter | Setup → Test Pool |
| `viewer_access` | true/false | Setup → Mobile Companion |

> **Kontrol gripper** belum punya tombol khusus di GUI. Rekomendasi: tambahkan tombol
> Open/Close gripper yang mengirim command `gripper` (mis. nilai "open"/"close") —
> dipakai pada Tahap 2 & 5. Lihat §6 Checklist.

---

## 4. Alur Misi (5 tahapan) dan peran GUI

Durasi run: maks 20 menit (5 menit persiapan, 10 menit misi, 5 menit evakuasi).
Total bobot misi 100% (autonomous Tahap 5 bernilai paling besar).

### Tahap 1 — Diving & Scan QR Code (15%)
**Tujuan:** ROV menyelam ke dasar kolam dan memindai QR code.

**Cara di GUI:**
1. **ARM** ROV (header) → mode **MANUAL**.
2. Halaman **Control**: turunkan ROV dengan sumbu **Vertical** (tahan `F` untuk turun,
   `R` untuk naik) sambil pantau **DEPTH** dan **ALT** di strip serta **depth tape**.
   Digital twin 3D menampilkan attitude (heading/roll/pitch) real-time.
3. Pindah ke halaman **Camera**: lihat **CAM 1 — BOTTOM** mengarah ke lantai.
4. Arahkan ROV hingga QR masuk frame. Panel **QR CODE DETECTION** otomatis membaca
   dan menampilkan **data QR + sisi (A/B/C/D)**. Sisi inilah target dinding payload.

**Indikator sukses:** kotak sisi (A/B/C/D) menyala hijau, data QR tercatat di console.
**Dinilai:** diving (5), steady positioning di QR (5), scanning QR (5).

### Tahap 2 — Grapping Payload (15%)
**Tujuan:** ROV mengambil payload dari dasar dengan gripper.

**Cara di GUI:**
1. Tetap **MANUAL**. Gunakan **CAM 1 — BOTTOM** sebagai panduan visual menuju payload.
2. Atur posisi presisi dengan Surge/Sway/Yaw (W/S, A/D, Q/E) — pantau heading di HUD.
3. **Tutup gripper** untuk mencengkeram payload (lihat catatan command `gripper` di §3).
4. Naikkan sedikit (`R`) untuk memastikan payload terangkat (cek lewat kamera).

**Indikator sukses:** payload tampak tercengkeram di kamera.
**Dinilai:** 15 jika 1× percobaan, 10 jika 2×, 5 jika >2×.

### Tahap 3 — Payload Placement ke Dinding (15%)
**Tujuan:** memindahkan payload ke gantungan dinding **sesuai sisi QR (A/B/C/D)**.

**Cara di GUI:**
1. Lihat kembali sisi target di panel **QR** (Camera). Navigasikan ROV ke dinding tsb.
2. Gunakan **CAM 2 — WALL** untuk membidik gantungan di dinding.
3. Manuver dengan Surge/Sway/Yaw; **ALT** membantu menjaga ketinggian terhadap dasar.
4. Gantungkan payload, lalu **buka gripper** untuk melepas ke gantungan.
5. Halaman **Mission** merekam **trajectory** dari titik awal hingga lokasi ini.

**Indikator sukses:** payload tergantung di sisi yang benar (terlihat di CAM 2 — WALL).
**Dinilai:** 15 jika 1× percobaan, 10 jika 2×, 5 jika >2×.

### Tahap 4 — Surface Docking (15%)
**Tujuan:** ROV mengapung ke permukaan dan bersandar (docking) di sisi dinding payload.

**Cara di GUI:**
1. Naikkan ROV (`R`) sambil pantau **DEPTH menuju 0** dan **ALT membesar** di strip.
   **Alarm kedalaman** berhenti begitu keluar dari zona berbahaya.
2. Gunakan **CAM 2 — WALL** untuk menyandarkan ROV di sisi dinding yang sesuai QR.
3. Halaman **Mission** menandai titik akhir (E) lintasan.

**Indikator sukses:** ROV mengapung dan docking di sisi yang benar.
**Dinilai:** 15 jika docking di sisi seharusnya, 5 jika sisi salah, 0 jika gagal mengapung.

### Tahap 5 — Autonomous Payload Release (40% / 10%) ⭐
**Tujuan:** ROV menjalankan **program autonomous** untuk melepas payload lalu naik ke
permukaan. Bernilai **40% jika full-autonomous**, hanya **10% jika dilakukan remotely**.

**Cara di GUI (mode autonomous):**
1. Pastikan ROV **ARMED**.
2. Tekan toggle header menjadi **AUTONOMOUS** → GUI mengirim `control_mode = autonomous`.
   ROV menjalankan rutin onboard (navigasi + lepas payload + naik) **tanpa kemudi manual**.
3. **Pemantauan, bukan pengemudian:** operator mengawasi via —
   - **Strip**: DEPTH/ALT/HEADING untuk memastikan ROV bergerak sesuai rencana.
   - **Mission**: trajectory autonomous tergambar realtime (titik awal → akhir).
   - **Camera**: CAM BOTTOM/WALL untuk konfirmasi pelepasan payload.
   - **Telemetry**: grafik Yaw/Depth/Pitch/Roll + status 6 thruster.
4. **Auto screenshot & data logging** menyala otomatis saat **AUTONOMOUS + ARMED**:
   - Logging CSV (timestamp, heading, depth, altitude, roll, pitch) — diunduh saat mode
     keluar (disarm/manual), berguna sebagai bukti & analisis.
   - Snapshot kamera berkala tiap 15 detik.
5. **Failsafe:** jika menyimpang/bahaya, tekan **STOP** (atau Spasi) — seluruh thruster
   netral seketika dan ROV disarm; mode kembali bisa dipindah ke MANUAL untuk recovery.

**Indikator sukses:** payload terlepas & ROV naik **tanpa intervensi manual**.
**Dinilai:** 40 jika full-autonomous, 10 jika remotely/partly-autonomous.

> Catatan: GUI **memerintahkan** ROV masuk mode autonomous dan **memantau** hasilnya;
> logika autonomous (path-planning, pelepasan) berjalan di sisi ROV/Raspberry Pi.

---

## 5. Fitur GUI pendukung lintas-tahap

- **Emergency Stop (wajib KKI):** STOP / Spasi menetralkan semua thruster kapan saja.
- **Alarm audio kedalaman:** mencegah ROV melewati `DANGER_DEPTH` (Setup → Test Pool).
- **Identitas & jam:** nama tim, perguruan tinggi, tanggal/waktu di header (syarat tampilan KKI).
- **Mobile Companion:** buka dashboard dari perangkat lain di jaringan umbilical (read-along).
- **Setup persisten:** URL kamera, thruster, PID, kedalaman tersimpan di browser (localStorage).

## 6. Checklist pra-run & catatan

**Sebelum run:**
1. Setup → **Team Identity**: isi nama tim & perguruan tinggi.
2. Setup → **Camera Stream**: isi URL CAM 1 (BOTTOM) & CAM 2 (WALL), klik Apply.
   Cek kedua feed tampil di halaman Camera.
3. Setup → **Test Pool**: set `Pool depth` (mis. 3.0 m) & `Danger depth` (mis. 2.8 m).
4. Setup → **Thruster/PID**: sesuaikan mixer & gain (maks **6 thruster** sesuai KKI).
5. Uji **ARM → STOP** memastikan failsafe bekerja.

**Catatan teknis:**
- **QR & CORS:** decode QR memakai `getImageData`. Untuk stream MJPEG lintas-asal,
  server kamera harus mengirim header CORS; bila tidak, pakai tombol **"Scan dari gambar"**
  di panel QR. `jsQR` sudah di-vendor (`public/vendor/jsqr.min.js`) agar jalan offline.
- **Gripper:** belum ada tombol khusus — tambahkan command `gripper` (Open/Close) untuk
  Tahap 2 & 5 agar alur misi lengkap sepenuhnya dari GUI.
- **Replay camera & trajectory** (fitur opsional KKI) belum diimplementasikan.
