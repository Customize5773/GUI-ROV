# HYDROSHIP — Dashboard ROV

Dashboard operator ROV: telemetri real-time, visualisasi attitude 3D (three.js),
dan umpan kamera. Dibangun dengan HTML/CSS/JS murni di sisi tampilan, jembatan
Node.js di tengah, dan UDP ke Raspberry Pi (ROV).

```
  Browser (dashboard)  <--- WebSocket --->  Node.js server  <--- UDP --->  Raspi (ROV)
   three.js + UI            ws://:8080         server.js        :14551 telemetri
                                                                :14550 command
```

## Struktur

```
GUI-ROV/
├─ public/                     # dashboard (dibuka di browser)
│  ├─ index.html
│  ├─ css/style.css
│  ├─ images/                  # Logo1.png, Logo2.png
│  ├─ models/                  # rov.fbx (model 3D ROV)
│  ├─ vendor/                  # jsqr.min.js
│  └─ js/
│     ├─ config.js             # << atur IP kamera / model 3D di sini
│     ├─ core.js                # state bersama, WebSocket, util inti
│     ├─ app.js                 # bootstrap, routing antar halaman
│     ├─ model.js                # loader & kontrol model 3D (three.js)
│     ├─ scene.js               # three.js: ROV 3D + cincin kompas
│     └─ pages/                 # satu modul per halaman dashboard
│        ├─ telemetry.js         # telemetri real-time, attitude 3D
│        ├─ camera.js            # umpan kamera + deteksi QR (jsQR)
│        ├─ mission.js           # perencanaan/kontrol misi
│        └─ setup.js             # konfigurasi & pengaturan koneksi
├─ server/
│  ├─ server.js                # jembatan WebSocket <-> UDP + static server
│  ├─ package.json
│  └─ package-lock.json
├─ autonomy/                   # otonomi ROV: visual servo, ArduSub SITL, ArUco
│  ├─ control/visual_servo.py   # PBVS (position-based visual servo)
│  ├─ fsm/mission5.py            # finite-state machine misi
│  ├─ vision/aruco_qr.py         # deteksi ArUco/QR
│  ├─ tools/                    # kalibrasi kamera, generator marker/checkerboard, tes SITL
│  ├─ rov_link.py               # link komunikasi ke ROV
│  ├─ sitl_mock.py              # mock SITL untuk pengujian tanpa hardware
│  ├─ SITL_SETUP.md              # panduan setup ArduSub SITL (WSL2)
│  ├─ README_SETUP_C.md
│  └─ VERIFIKASI_ARDUSUB.md
├─ image logo/                 # aset logo sumber (Logo1.png, Logo2.png)
├─ raspi_rov_example (notfinish).py   # contoh format UDP di sisi ROV
├─ Rencana.md
└─ README-WORK.md
```

## Menjalankan (uji cepat, tanpa hardware)

```bash
cd server
npm install
npm run sim          # server + telemetri palsu
```
Buka `http://localhost:8080`. ROV 3D akan bergerak mengikuti data simulasi.

> **Gunakan `npm run sim`, bukan `npm start sim`.** Simulator hanya aktif jika
> server menerima flag persis `--sim`. `npm start sim` mengirim kata `sim`
> (tanpa strip) sebagai argumen, sehingga server tetap jalan mode LIVE dan
> dashboard akan mencatat *"Telemetri terputus (timeout)"*. Alternatif setara:
> `npm start -- --sim`.

## Kesesuaian KKI 2026

GUI memenuhi ketentuan Panduan KKI 2026 §4.7.3:
- **2 kamera**: halaman Camera menampilkan CAM 1 (BOTTOM) & CAM 2 (WALL) bersamaan.
- **Deteksi QR Code**: dibaca di browser dengan **jsQR** dari feed BOTTOM; panel QR
  menampilkan data + sisi dinding **A/B/C/D**.
- **Identitas** (nama tim, perguruan tinggi) + hari/tanggal/waktu di header (atur di
  Setup → Team Identity, atau `config.js` `TEAM_NAME`/`UNIVERSITY`).
- **Altitude** ROV terhadap dasar kolam = `POOL_DEPTH − depth` (readout `ALT`).
- **Gambar disain ROV** (model 3D) + **trajectory** (halaman Mission).
- **Emergency Stop** (tombol STOP) menetralkan seluruh thruster.
- Fitur tambahan: toggle **Manual/Autonomous**, **alarm audio** kedalaman berbahaya
  (ambang `DANGER_DEPTH`), **auto screenshot & logging** (aktif saat autonomous + armed).

### Deteksi QR & CORS
- `jsQR` di-vendor di `public/vendor/jsqr.min.js` agar jalan **offline** di venue.
- Decode QR memakai `getImageData` pada canvas. Untuk stream MJPEG lintas-asal,
  server kamera (mjpg-streamer/Pi) **harus mengirim header CORS**
  (`Access-Control-Allow-Origin: *`) dan `<img>` memakai `crossOrigin="anonymous"`
  (sudah diset). Jika CORS tidak tersedia, pakai tombol **"Scan dari gambar"** di
  panel QR untuk men-decode dari berkas gambar.

### Telemetri terputus (timeout)?

Pesan *"Telemetri terputus (timeout)"* berarti dashboard tersambung ke server
(status **ONLINE**) tetapi **tidak ada telemetri masuk** selama >2.5 detik.
Penyebab umum:

| Perintah | Mode | Akibat |
|---|---|---|
| `npm start` | LIVE, tanpa simulator | Tidak ada data kecuali ROV nyata mengirim UDP ke port `14551` → timeout |
| `npm start sim` | LIVE (flag salah) | `sim` ≠ `--sim`, simulator mati → timeout |
| `npm run sim` | SIMULASI | Telemetri palsu tiap 100 ms → **tidak ada timeout** ✅ |

Untuk uji tanpa hardware selalu pakai **`npm run sim`**. Untuk ROV nyata
(`npm start`), pastikan Raspi benar-benar mengirim telemetri UDP ke port `14551`.

## Menjalankan (dengan ROV nyata)

1. Atur IP di `server/server.js` (atau via env): `RPI_ADDR` = IP Raspberry Pi.
2. Jalankan server:
   ```bash
   cd server && npm install && npm start
   ```
3. Di Raspi, kirim telemetri UDP ke IP komputer server, port **14551**, dan
   dengarkan command di port **14550**. Lihat `raspi_rov_example.py`:
   ```bash
   python3 raspi_rov_example.py --server <IP_KOMPUTER_SERVER>
   ```
4. Buka `http://<IP_KOMPUTER_SERVER>:8080` di laptop operator.

> Wireless dilarang aturan KKI — pastikan semua lewat kabel Ethernet umbilical,
> satu subnet (mis. laptop 192.168.2.1, Raspi 192.168.2.2).

## Format data

**Telemetri (Raspi → server, UDP JSON):**
```json
{ "heading": 112.0, "roll": 2.6, "pitch": 4.0, "depth": 0.0,
  "temp": 26.5, "voltage": 15.6, "armed": false, "light": false, "ts": 1718...}
```
Field yang kosong/absen tampil sebagai "—". Sudut dalam derajat, depth meter.

**Command (server → Raspi, UDP JSON):**
```json
{ "name": "light", "value": true,  "t": 1718... }   // light/arm/record/snapshot
{ "name": "stop",  "value": true }                  // failsafe: netralkan thruster
```
Di Raspi, `stop` HARUS langsung menetralkan semua thruster.

## Model 3D ROV

Default memakai model open-frame bawaan (dibuat dari primitif). Untuk pakai model
asli ROV Anda:
1. Ekspor dari **Fusion** (`.fbx`) atau **Blender** (`.glb`).
2. Taruh di `public/models/`, mis. `public/models/rov.glb`.
3. Di `public/js/config.js`, set `MODEL_URL: "models/rov.glb"`.

Model di-skala & dipusatkan otomatis. Pastikan **haluan (depan) menghadap +Z**
agar orientasi heading benar; kalau terbalik, rotasikan model di Blender/Fusion
sebelum ekspor, atau tambahkan offset rotasi di `scene.js`.

## Kamera

Browser tidak bisa memutar RTSP langsung. Paling mudah: ubah ke **MJPEG**.
- Di Raspi: jalankan `mjpg-streamer` (output `http://<raspi>:8080/?action=stream`),
  atau transcode RTSP→MJPEG dengan ffmpeg.
- Set URL itu di `config.js` → `CAMERA_URL`.
- Upgrade kualitas/latensi rendah: pakai WebRTC (mis. `mediamtx`/`go2rtc`) dan
  ganti `<img>` jadi `<video>` di `index.html`.

## Pakai tanpa internet (venue lomba)

three.js dimuat dari CDN (unpkg) lewat import map. Agar jalan offline:
1. Unduh `three@0.169.0` (`build/three.module.js` + folder `examples/jsm/`).
2. Taruh di `public/vendor/three/`.
3. Ubah import map di `index.html` agar menunjuk ke `vendor/three/...` (path lokal).
Font Google juga sebaiknya di-self-host; jika gagal dimuat, fallback monospace/sans
tetap terbaca.

## Pintasan

- **Spasi** = STOP (failsafe) kapan saja.
- Klik & drag pada panel ATTITUDE untuk memutar pandangan 3D.

## Autonomy (Python, opsional)

Folder `autonomy/` berisi jalur MAVLink + visi komputer untuk misi otonom
(mis. Misi 5: APPROACH_HOOK), terpisah dari dashboard di atas.

```
Browser ──WS:8080── server.js ──cmd JSON :14550──► rov_link.py ──MANUAL_CONTROL──► mock / SITL / Pixhawk
  (3D)             (LIVE)      ◄─telem JSON :14551─             ◄─ATTITUDE/PRESSURE─   (MAVLink :14555)
```

```
autonomy/
├─ rov_link.py              # jembatan server.js (UDP JSON) <-> vehicle (MAVLink)
├─ sitl_mock.py             # vehicle MAVLink palsu, buat uji tanpa ArduSub
├─ vision/aruco_qr.py       # deteksi ArUco + QR, estimasi pose solvePnP (PBVS)
├─ control/visual_servo.py  # VisualServo (IBVS, piksel) & PoseServo (PBVS, meter)
├─ fsm/mission5.py          # state machine APPROACH_HOOK (PBVS bila --calib, else IBVS)
├─ tools/
│  ├─ calibrate_camera.py     # kalibrasi kamera via checkerboard -> intrinsics .npz
│  ├─ make_checkerboard.py    # cetak papan kalibrasi
│  ├─ make_marker.py          # generator marker ArUco (hook_marker_id7.png)
│  ├─ pose_webcam_test.py     # tes solvePnP + PoseServo dgn webcam
│  ├─ servo_webcam_test.py    # tes APPROACH_HOOK (IBVS/PBVS) dgn webcam
│  └─ run_sitl.sh             # launch ArduSub SITL (WSL2) -> host Windows:14555
├─ hook_marker_id7.png      # marker ArUco target hook
├─ requirements.txt
├─ README_SETUP_C.md        # panduan integrasi GUI <-> rov_link.py <-> mock/SITL
├─ SITL_SETUP.md            # instalasi ArduSub SITL di WSL2 + routing MAVLink
└─ VERIFIKASI_ARDUSUB.md    # checklist yang wajib dicek saat naik ke ArduSub asli
```

Setup singkat (Python 3.12 + venv):
```powershell
cd autonomy
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Uji end-to-end tanpa hardware: jalankan `sitl_mock.py`, lalu `rov_link.py`, lalu
GUI mode LIVE (`RPI_ADDR=127.0.0.1 npm start`) — lihat langkah lengkap & kriteria
sukses di `autonomy/README_SETUP_C.md`. Untuk naik ke fisika nyata (ArduSub SITL
di WSL2), ikuti `autonomy/SITL_SETUP.md`.
```
