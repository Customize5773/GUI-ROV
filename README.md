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
├─ autonomy/                   # otonomi ROV: visual servo, ArduSub SITL, deteksi QR
│  ├─ control/visual_servo.py   # PBVS (position-based visual servo)
│  ├─ fsm/mission5.py            # finite-state machine misi
│  ├─ vision/qr_detect.py        # deteksi QR payload
│  ├─ tools/                    # kalibrasi kamera, generator marker/checkerboard, tes SITL
│  ├─ rov_link.py               # link komunikasi ke ROV
│  ├─ sitl_mock.py              # mock SITL untuk pengujian tanpa hardware
│  ├─ SITL_SETUP.md              # panduan setup ArduSub SITL (WSL2)
│  ├─ README_SETUP_C.md
│  ├─ VERIFIKASI_ARDUSUB.md
│  └─ ROADMAP_MISI5.md           # peta jalan Fase 0-4: meja -> SITL -> hardware -> kolam -> lomba
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
  menampilkan data + sisi dinding **A/B/C/D**. Isi QR payload = **JSON terstruktur**
  `{"mission":5,"team":"HYDROSHIP","type":"payload","id":"A"}` (sisi diambil dari `id`);
  QR string biasa (mis. `SIDE_B`) tetap didukung sebagai fallback.
- **Identitas** (nama tim, perguruan tinggi) + hari/tanggal/waktu di header (atur di
  Setup → Team Identity, atau `config.js` `TEAM_NAME`/`UNIVERSITY`).
- **Altitude** ROV terhadap dasar kolam = `POOL_DEPTH − depth` (readout `ALT`).
- **Gambar disain ROV** (model 3D) + **trajectory** (halaman Mission).
- **Emergency Stop** (tombol STOP) menetralkan seluruh thruster.
- Fitur tambahan: toggle **Manual/Autonomous**, **alarm audio** kedalaman berbahaya
  (ambang `DANGER_DEPTH`), **auto screenshot & logging** (aktif saat autonomous + armed).

### Deteksi QR & CORS
- `jsQR` di-vendor di `public/vendor/jsqr.min.js` agar jalan **offline** di venue.
- Decode QR memakai `getImageData` pada canvas — ini butuh feed **same-origin**,
  jika tidak canvas ter-*taint* dan decode gagal.
- **Feed kamera diambil lewat proxy `server.js`**: dashboard memuat
  `/cam?url=<url-kamera>` (bukan langsung ke IP kamera), sehingga selalu same-origin.
  Hasilnya **video tampil tanpa perlu CORS di server kamera** dan QR bisa di-decode.
  Tidak perlu lagi `crossOrigin="anonymous"` maupun konfigurasi CORS di mjpg-streamer.
- Proxy membatasi tujuan ke host LAN privat (127/10/192.168/172.16–31, `*.local`,
  `localhost`) untuk mencegah open-proxy. Override dengan env `CAM_ALLOW_ANY=1` bila perlu.
- Untuk decode QR canvas diperkecil ke maks 800 px agar ringan pada feed 1080p.
- Fallback tetap ada: tombol **"Scan dari gambar"** men-decode QR dari berkas gambar.
- Catatan: proxy hanya aktif saat dijalankan via `server.js` (bukan penyaji statis lain).

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
├─ vision/qr_detect.py      # deteksi QR payload, estimasi pose solvePnP (PBVS)
├─ control/visual_servo.py  # VisualServo (IBVS, piksel) & PoseServo (PBVS, meter)
├─ fsm/mission5.py          # state machine docking QR (PBVS bila --calib, else IBVS)
├─ tools/
│  ├─ calibrate_camera.py     # kalibrasi kamera via checkerboard -> intrinsics .npz
│  ├─ make_checkerboard.py    # cetak papan kalibrasi
│  ├─ pose_webcam_test.py     # tes solvePnP + PoseServo pd QR payload dgn webcam (+--csv)
│  ├─ servo_webcam_test.py    # tes docking QR (IBVS/PBVS) dgn webcam (+--csv/--cam-width)
│  ├─ detection_log.py        # logger CSV deteksi QR (detection-rate + pose) utk tuning
│  ├─ run_sitl.sh             # launch ArduSub SITL (WSL2) -> host Windows:14555
│  └─ launch_sitl.py          # peluncur SATU-PERINTAH: vehicle(mock/SITL) -> rov_link -> GUI(-> FSM)
├─ tests/                    # tes-nilai-evaluasi misi 5 (closed-loop, tanpa hardware)
│  ├─ sim_plant.py            # simulator ROV+payload in-process (fisika + geometri QR)
│  ├─ evaluate_mission5.py    # harness skor rubrik + evaluasi mutu docking (CLI/JSON)
│  ├─ test_mission5.py        # pytest: unit (PID/servo) + integrasi misi 1→5
│  └─ test_qr_detect.py       # pytest: decode_qr preprocessing (CLAHE/upscale/rescale)
├─ config/                   # tuning misi 5 TANPA edit kode (--config di mission5.py)
│  ├─ loader.py                # baca .yaml/.yml/.json -> override konstanta modul
│  └─ mission5.example.yaml    # contoh lengkap (salin -> mission5.local.yaml, gitignored)
├─ requirements.txt
├─ README_SETUP_C.md        # panduan integrasi GUI <-> rov_link.py <-> mock/SITL
├─ SITL_SETUP.md            # instalasi ArduSub SITL di WSL2 + routing MAVLink
├─ VERIFIKASI_ARDUSUB.md    # checklist teknis #1-7 (hardware) & M1-M8 (kolam)
├─ ROADMAP_MISI5.md         # peta jalan lengkap: meja -> SITL -> hardware -> kolam -> lomba
├─ PERSIAPAN_FASE2-4.md     # run-book operasional Fase 2 (bench) / 3 (kolam) / 4 (lomba)
└─ PR-AUTONOMY.md           # backlog & analisis gap Fase 0-4 + isu tertunda (mis. QR jarak/cahaya)
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

### Peluncur satu-perintah (`tools/launch_sitl.py`)

Alih-alih 3-4 terminal manual di atas, jalankan sekaligus vehicle (mock/SITL) →
`rov_link.py` → GUI (→ FSM opsional) dengan **satu perintah**:

```bash
cd autonomy
python tools/launch_sitl.py                          # mock + rov_link + GUI (default, tanpa WSL)
python tools/launch_sitl.py --vehicle sitl            # ArduSub SITL WSL2 sudah jalan terpisah
python tools/launch_sitl.py --fsm --start-state M5_REDIVE --vision mock   # + FSM sekalian
python tools/launch_sitl.py --no-gui                  # tanpa server.js (mis. GUI sudah jalan)
```

Keluaran tiap proses diberi label warna `[VEHICLE]`/`[ROV_LINK]`/`[GUI]`/`[FSM]` di satu
terminal. Ctrl+C sekali menghentikan semua; bila satu proses crash, semua proses lain
ikut dihentikan otomatis (tak ada yang "nyangkut" separuh jalan).

### Tes-Nilai-Evaluasi Misi 5 (`autonomy/tests/`)

Untuk **memvalidasi rantai docking payload autonomous misi 5 secara terukur tanpa
kolam/hardware**, `autonomy/tests/` menutup loop kontrol Mission5FSM melawan
`sim_plant.py` — simulator ROV+payload in-process yang memodelkan **umpan balik
nyata** (perintah thruster mengubah depth/heading & geometri QR relatif), sehingga
PoseServo/VisualServo benar-benar diuji, bukan sekadar timer. Jam virtual membuat
misi penuh selesai dalam milidetik & deterministik.

```bash
cd autonomy
pip install -r requirements.txt pytest      # numpy + pytest (cv2/pyzbar opsional)

pytest tests/ -v                            # unit (PID/servo/heading) + integrasi 1→5

python tests/evaluate_mission5.py           # laporan skor rubrik + evaluasi mutu docking
python tests/evaluate_mission5.py --start M5_REDIVE   # hanya misi 5 autonomous (1-4 manual)
python tests/evaluate_mission5.py --ibvs    # tanpa kalibrasi (IBVS piksel)
python tests/evaluate_mission5.py --json    # keluaran JSON (untuk CI)
```

Harness memberi **NILAI** (skor rubrik m1..m5, total /100) dan **EVALUASI** (checklist
mutu: jalur docking visual vs fallback timed, akurasi align lateral & jarak saat engage,
payload lepas-hook & sampai permukaan, waktu tempuh). Cakupan uji termasuk mode PBVS &
IBVS, keempat sisi kolam A/B/C/D, dan **ketahanan loss-of-lock** (dropout deteksi QR
sesaat) yang menguji dead-reckon hold + sapu reacquire terarah pada `M5_DOCK`/`M5_ENGAGE`.

### Tuning tanpa edit kode (`autonomy/config/`)

Parameter yang biasa di-tuning di kolam (gain PID docking, target jarak/luas engage,
kedalaman, timeout tiap state, `WALL_HEADING`, arah `invert_*`, gerak lepas-hook, syarat
validasi payload QR) bisa diubah lewat **file config** — tim tak perlu sentuh
`fsm/mission5.py` sama sekali:

```bash
cd autonomy
cp config/mission5.example.yaml config/mission5.local.yaml   # gitignored, aman diubah bebas
# ...edit mission5.local.yaml sesuai hasil tuning...
python fsm/mission5.py --config config/mission5.local.yaml --vision usb
```

Format `.yaml`/`.yml` (butuh `pip install pyyaml`, sudah ada di `requirements.txt`) atau
`.json` (setara, tanpa instalasi tambahan). Semua kunci **opsional** — yang tak diisi tetap
memakai default di kode. Lihat `config/mission5.example.yaml` untuk daftar lengkap +
penjelasan tiap parameter, dan `autonomy/ROADMAP_MISI5.md` Fase 3 untuk urutan tuning
di kolam yang disarankan.

### Deteksi QR robust + diagnosa (`decode_qr` & `--csv`)

Deteksi QR (`vision/qr_detect.py`) memakai **`decode_qr()`** — preprocessing berjenjang
yang hanya mengeskalasi saat perlu: frame mentah → grayscale+**CLAHE** (cahaya tak
rata/glare) → **upscale 2×** (QR kecil/jauh) → fallback `cv2.QRCodeDetector`. Ini menjawab
gejala umum "QR terbaca hanya jarak dekat / sensitif cahaya".

Untuk **mengukur** batas deteksi (mis. saat tuning kolam), tool webcam bisa merekam CSV
per-frame + resolusi kamera lebih tinggi:
```bash
python tools/servo_webcam_test.py --device 0 --csv run.csv --cam-width 1280 --cam-height 720
```
Saat keluar dicetak **detection-rate %** + rentang jarak/area terdeteksi. Analisis lengkap
isu deteksi ada di `autonomy/PR-AUTONOMY.md` (QR-01), prosedur tuning di
`autonomy/PERSIAPAN_FASE2-4.md` Fase 3.

### Persiapan hardware & lomba (Fase 2–4)

Karena Fase 2–4 butuh hardware/kolam, langkah operasionalnya disiapkan sebagai run-book
yang bisa dicentang di lapangan: **`autonomy/PERSIAPAN_FASE2-4.md`** (Fase 2 bring-up
bench · Fase 3 tuning kolam + worksheet parameter · Fase 4 run-book hari-H + scoresheet),
merujuk checklist teknis `VERIFIKASI_ARDUSUB.md`. Backlog & analisis apa yang masih kurang
tiap fase ada di **`autonomy/PR-AUTONOMY.md`**.
