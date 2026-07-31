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

| `gripper` | "open" / "close" | tombol **Gripper OPEN/CLOSE** (Control) / keyboard **H** (buka) & **G** (tutup) |

> **Kontrol gripper** sudah tersedia di halaman **Control**: tombol **OPEN**/**CLOSE**
> (`public/index.html` `#btnGripOpen`/`#btnGripClose`, handler di `public/js/app.js`)
> mengirim command `gripper` ("open"/"close") — dipakai pada Tahap 2, 3 & 5.

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

> **Docking autonomous ke hook (baru).** State `HANG` di `fsm/mission5.py` kini
> **closed-loop**: mendeteksi hook PVC ujung-U di dinding via **CAM WALL**
> (`vision/hook_detect.py` — contour/edge, tak bergantung warna PVC yang belum pasti),
> lalu men-servo ROV (`VisualServo`/`PoseServo`, sama seperti docking misi 5) hingga
> payload sejajar hook baru **melepas gripper**. Deteksi dropout sesaat ditutup
> *dead-reckon hold*; bila hook tak pernah ter-lock, sistem **degradasi ke urutan
> timed lama** (jaring pengaman, bukan jalur utama). Operator cukup **memantau** —
> gerak halus ini berjalan onboard saat mode autonomous.

### Tahap 4 — Surface Docking (15%)
**Tujuan:** ROV mengapung ke permukaan dan bersandar (docking) di sisi dinding payload.

**Cara di GUI:**
1. Naikkan ROV (`R`) sambil pantau **DEPTH menuju 0** dan **ALT membesar** di strip.
   **Alarm kedalaman** berhenti begitu keluar dari zona berbahaya.
2. Gunakan **CAM 2 — WALL** untuk menyandarkan ROV di sisi dinding yang sesuai QR.
3. Halaman **Mission** menandai titik akhir (E) lintasan.

**Indikator sukses:** ROV mengapung dan docking di sisi yang benar.
**Dinilai:** 15 jika docking di sisi seharusnya, 5 jika sisi salah, 0 jika gagal mengapung.

> **Surface docking autonomous (baru).** State `DOCK` di `fsm/mission5.py` juga
> **closed-loop**: setelah mengapung, ROV men-servo ke hook sisi target (CAM WALL)
> sampai berada dalam jarak/pose docking wajar, baru berhenti — menggantikan "maju
> `surge=20` selama 8 detik" yang buta. Guard `TIMEOUT_DOCK` + fallback timed tetap
> ada sebagai degradasi terakhir. Parameter deteksi & docking dapat di-tuning lewat
> `config/mission5.example.yaml` (grup `hook_docking:` & `hook_detect:`).

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
- **Gripper:** tombol **OPEN/CLOSE** sudah ada di halaman Control (juga keyboard **H**/**G**)
  dan mengirim command `gripper` — dipakai Tahap 2, 3 & 5.
- **Replay camera & trajectory** (fitur **nilai tambah**, di luar komponen wajib
  §4.7.3) **sudah diimplementasikan** — lihat §8.

## 7. Joystick manual control (Gamepad → MANUAL_CONTROL)

Kontrol manual ROV memakai joystick fisik yang dicolok ke komputer operator (browser),
melalui jalur existing: **Browser → WebSocket → Node.js server → UDP → Raspberry Pi → Pixhawk**.

**Cara pakai:**
1. Colok joystick ke laptop, buka dashboard, tekan satu tombol joystick agar terdeteksi
   (`gamepadconnected`).
2. Di panel **Controller**, pilih tab **Gamepad** (default Keyboard). Badge menampilkan status.
3. Pastikan mode kontrol = **MANUAL** (toggle Manual/Autonomous di header) — joystick **tidak**
   berefek saat Autonomous.
4. Halaman **Joystick** menyediakan mapping axis/tombol (disimpan ke `server/config/joystick-profile.json`).

**Mapping axis (GUI −1000..1000, 0 = diam → MANUAL_CONTROL):**

Keempat axis memakai **satu** konvensi di seluruh GUI, server, dan link UDP: `−1000..1000`
dengan `0` = diam. Konversi ke rentang `z` khas ArduSub dilakukan **hanya** di sisi Pi
(`rov_axes.to_mavlink_z`), tepat sebelum `manual_control_send`.

| Axis GUI | Gerak            | Rentang GUI/UDP | Field MANUAL_CONTROL | Rentang wire |
|----------|------------------|-----------------|----------------------|--------------|
| surge    | maju/mundur      | −1000..1000 (0) | `x`                  | −1000..1000  |
| sway     | lateral kiri/kanan | −1000..1000 (0) | `y`                | −1000..1000  |
| yaw      | rotasi           | −1000..1000 (0) | `r`                  | −1000..1000  |
| heave    | throttle naik/turun | −1000..1000 (0) | `z`               | 0..1000 (netral **500**) |

- **Pembentukan respons stik** di browser (`public/js/axis-shaping.js`, disisipkan di
  `readAssignedAxis`): `deadzone → expo → skala min/max → × gain pilot → rate limit`.
  Default `deadzone 0.12`, `expo 0.35`, `rate 4000/s`, gain mulai 40% (6 langkah 25–100%,
  diubah saat operasi lewat LB/RB). Semuanya bisa disetel di halaman Joystick dan ikut
  tersimpan di profil. Preview di halaman itu memanggil fungsi yang **sama persis**
  dengan jalur kirim, jadi angka di layar tidak bisa berbeda dari yang diterima ROV.
- **Heartbeat axis ~15 Hz**: nilai axis di-resend berkala walau ditahan konstan, supaya Pi
  menerima MANUAL_CONTROL berkelanjutan. Heartbeat ini **tidak** bergantung pada jenis
  controller — kalau hanya jalur gamepad yang mengirim, mode Keyboard akan terus terbaca
  "stale" oleh fail-safe Pi padahal normal. Saat E-Stop aktif, yang dikirim adalah **nol
  eksplisit**, bukan diam.
- **Validasi ulang di server** (`server/server.js`): axis di-clamp ke −1000..1000 sebelum
  diteruskan (tidak percaya input klien).
- **Validasi ulang di Pi** (`rov_axes.clamp_axis`): Pi tidak mempercayai paket UDP mentah —
  nilai di luar rentang atau bukan angka di-clamp / jadi `0` sebelum dikirim ke Pixhawk.
- **Encoding MANUAL_CONTROL** di sisi Pi (`rov_agent.py` + `rov_axes.py`, via `pymavlink`
  `manual_control_send`). Node server **tidak** meng-encode MAVLink — ia hanya meneruskan JSON,
  konsisten dengan pola command lain (arm/light/stop).

**Kenapa MANUAL_CONTROL, bukan RC_CHANNELS_OVERRIDE:** MANUAL_CONTROL adalah cara standar ArduSub
menerima kontrol manual dari ground station (4 sumbu + bitmask tombol). Tidak menimpa channel RC
fisik, jadi aman berdampingan dengan konfigurasi channel/servo di Pixhawk (scope Devanka).

**Safety / fallback:**
- **Joystick disconnect** (`gamepaddisconnected`) → axis dinetralkan (x=y=r=0, z=500).
- **WebSocket putus** → E-Stop dikunci & axis dinetralkan; operator harus ARM ulang setelah
  koneksi pulih sebelum joystick boleh menggerakkan ROV.
- **E-Stop / Spasi** → joystick **terkunci** sampai operator ARM ulang; tidak bisa override E-Stop.
- **Mode Autonomous** → joystick otomatis nonaktif (otoritas GUI vs FSM, mirip prinsip gripper).
  Keyboard kini tunduk pada gerbang yang sama — sebelumnya W/A/S/D bisa melewati E-Stop.
- **Fail-safe Pi** (`rov_axes.resolve_manual_packet`): jika tak ada axis baru > 0.5 s, Pi
  **terus** mengirim NEUTRAL (`x=y=r=0`, `z=500`) 20 Hz dan menandai `cmd_link: "stale"` di
  telemetry; dashboard memunculkan banner merah. Sengaja tetap mengirim, bukan berhenti:
  ArduSub mengharapkan aliran MANUAL_CONTROL kontinu, dan kalau ground station diam maka
  failsafe pilot-input Pixhawk yang jalan dengan perilaku tergantung parameter. Streaming
  netral lebih bisa diprediksi — diam di tempat, dan di ALT_HOLD berarti tahan kedalaman.

**Testing:**
- Unit test mapping + fail-safe (pure function, tanpa hardware):
  `python3 -m unittest test_rov_axes -v`.
- Unit test pembentukan respons stik (tanpa dependensi tambahan):
  `node --test server/test/*.test.mjs`.
- Manual test verifikasi command sampai UDP:
  1. `cd server && node server.js --sim` (atau `hydroship` di launch.json).
  2. Buka dashboard, pilih Gamepad + mode Manual, gerakkan stick.
  3. Amati log server `[CMD] surge = ... -> <RPI>:14550` (nilai sudah ter-clamp −1000..1000).
  4. Di Pi, jalankan `rov_agent.py`; amati log `[MANUAL]` dan MANUAL_CONTROL terkirim ke Pixhawk.

**Tombol joystick:** `buttons` di MANUAL_CONTROL tetap **0** — aksi tombol ditangani lewat
command GUI tersendiri (`arm`, `pilot_mode`, `gripper`, `stop`), bukan lewat bitmask MAVLink.
Profil default F310 lengkap (termasuk D-Pad 12–15 dan trigger analog sebagai button 6/7)
ada di `public/js/joystick-defaults.json` dan didokumentasikan di
[CONTROL-MAPPING.md](CONTROL-MAPPING.md). Aksi yang tidak punya hardware di wahana
(`mount_tilt_*`, `actuator1_*`, `lights_brighter/dimmer`, `input_hold_set`) sudah dihapus
supaya tidak ada tombol yang diam-diam mati; profil lama dimigrasikan otomatis.

## 8. Replay Camera & Trajectory (nilai tambah, bukan §4.7.3 wajib)

Fitur untuk **merekam** satu run misi lalu **memutar ulang** video 2 kamera + posisi
ROV di scene 3D **secara tersinkron**. Berguna sebagai bukti & bahan analisis pasca-run.
Ini **nilai tambah** (opsional), terpisah penuh dari jalur kontrol live — tidak pernah
bisa mengirim perintah ke ROV.

### Cara pakai
1. Buka halaman **Replay** (sidebar). Pastikan URL kamera BOTTOM/WALL sudah diisi di
   **Setup → Camera Stream** bila ingin ikut merekam video (opsional; trajectory tetap
   terekam walau tanpa kamera).
2. Tekan **● Start Recording** tepat sebelum/di awal run misi. Badge berubah **REC ●**.
   Rekaman berjalan **sepenuhnya di server** — tak masalah halaman mana yang dibuka.
3. Jalankan misi seperti biasa (Manual/Autonomous). Server mencatat tiap sampel
   telemetry + command gerak (surge/sway) dan men-tap frame kedua kamera.
4. Tekan **■ Stop Recording** di akhir run. Sesi baru muncul di daftar **SESI TERSIMPAN**.
5. Klik sesi → video + lintasan termuat. Pakai **scrubber/timeline** (play/pause/seek):
   video kedua kamera **dan** posisi ROV di scene 3D bergerak bersama sesuai timestamp.

### Command / message baru (tidak mengubah command existing §3)
| Message WS | Arah | Fungsi |
|---|---|---|
| `record_start` | GUI→server | mulai rekam (kirim daftar `cameras:[{role,url}]`) |
| `record_stop` | GUI→server | hentikan rekam |
| `record_status` | server→GUI | status rekam (broadcast + saat connect) |

> Ini **message type tersendiri**, sengaja **bukan** `type:"cmd"`, sehingga **tidak
> pernah** diteruskan ke UDP/ROV. Mode Replay tak punya jalur ke kontrol ROV.

### Playback API (HTTP — konsisten dgn static server & proxy `/cam` existing)
| Endpoint | Fungsi |
|---|---|
| `GET /api/recordings` | daftar sesi (id, tanggal, durasi, ukuran, kamera) |
| `GET /recordings/<id>/meta.json` | metadata sesi (termasuk `session_start_time`) |
| `GET /recordings/<id>/trajectory.jsonl` | log telemetry berstempel waktu |
| `GET /recordings/<id>/commands.jsonl` | log command gerak berstempel waktu |
| `GET /replay/frame?session=<id>&cam=<bottom\|wall>&i=<idx>` | 1 frame JPEG dari mjpeg |

Data replay bersifat historis/akses-acak → HTTP (bukan WebSocket) dipilih agar sejalan
dengan pola server yang sudah meng-serve file & mem-proxy kamera lewat HTTP; WS tetap
khusus push live sehingga live & replay tak bercampur.

### Penyimpanan (`server/recordings/<session_id>/`)
```
meta.json            session_start_time (acuan sync), durasi, ukuran, jumlah sampel
trajectory.jsonl     {t, heading, depth, roll, pitch}   (append sinkron → durable)
commands.jsonl       {t, name, value}   (hanya surge/sway/yaw/heave/control_mode/set_surface)
bottom.mjpeg / wall.mjpeg          frame JPEG mentah disambung
bottom.index.jsonl / wall.index.jsonl   {t, off, len} per frame → seek per timestamp
```
Folder `server/recordings/` **tidak di-commit** (ada `.gitignore` di dalamnya).

### Keputusan teknis
- **Video = frame store (JPEG + index), bukan .webm.** Stream kamera aktual = **MJPEG**
  (mjpg-streamer `?action=stream`). ffmpeg tidak tersedia di server, jadi encoding webm
  butuh dependency berat + kurang presisi sinkron. Frame store: **nol dependency baru**,
  sinkron **frame-accurate** (browser tinggal tukar `<img>.src` per waktu scrubber).
- **Posisi x,y direkonstruksi di browser saat replay.** Server hanya punya heading/depth
  (x,y adalah dead-reckoning di `mission.js`). Maka server merekam telemetry **+** command
  surge/sway; halaman Replay merekonstruksi lintasan dengan integrator **identik**
  `mission.js` (`VEL_SCALE`, `DEPTH_SCALE`, konvensi heading) → lintasan replay sepadan live.
- **Sinkronisasi** memakai satu clock server: `session_start_time` (meta.json) + timestamp
  tiap sampel/frame. Scrubber menghitung `tc = session_start_time + posisi_slider`, lalu
  memilih pose 3D & frame video dengan `t <= tc` (binary search).

### Batas durasi/ukuran
Auto-stop di **`MAX_RECORD_MIN` menit (default 15)**, configurable via env:
`MAX_RECORD_MIN=10 node server.js`. Run KKI ~maks 20 menit (5 siap+10 misi+5 evakuasi),
15 menit cukup untuk fase misi+evakuasi. Saat auto-stop, server memberi warning ke log &
event dashboard.

### Testing
- **Unit test server** (start/stop, tulis trajectory, tap command, listing, guard path,
  baca frame): `cd server && npm test` (tanpa framework/dependency tambahan).
- **Manual test sinkron video+trajectory** (sinkronisasi visual sulit di-assert otomatis):
  1. `cd server && node server.js --sim` (atau `hydroship` di launch.json).
  2. Buka dashboard → halaman **Replay** → **Start Recording**.
  3. Di halaman **Control**, gerakkan ROV (W/S/A/D) beberapa detik agar trajectory bergerak.
     Untuk video nyata, isi URL kamera dan pastikan feed tampil di halaman Camera.
  4. **Stop Recording** → klik sesi di daftar → tekan **play**.
  5. **Verifikasi:** garis lintasan & marker ROV bergerak; DENGAN kamera nyata, frame kedua
     video maju seiring posisi ROV pada timestamp yang sama (geser scrubber untuk cek titik
     tertentu). Bandingkan momen kunci (mis. saat gripper menutup) di video vs posisi 3D.
