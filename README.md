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
hydroship/
├─ public/                 # dashboard (dibuka di browser)
│  ├─ index.html
│  ├─ css/style.css
│  └─ js/
│     ├─ config.js         # << atur IP kamera / model 3D di sini
│     ├─ app.js            # WebSocket, telemetri, kontrol, simulator
│     └─ scene.js          # three.js: ROV 3D + cincin kompas
│  └─ models/              # taruh rov.glb / rov.fbx di sini (opsional)
├─ server/
│  ├─ server.js            # jembatan WebSocket <-> UDP + static server
│  └─ package.json
└─ raspi_rov_example.py    # contoh format UDP di sisi ROV
```

## Menjalankan (uji cepat, tanpa hardware)

```bash
cd server
npm install
npm run sim          # server + telemetri palsu
```
Buka `http://localhost:8080`. ROV 3D akan bergerak mengikuti data simulasi.

> Tanpa server pun dashboard tetap hidup: buka `public/index.html` langsung,
> ia otomatis masuk **mode DEMO** (simulator di browser).

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
```
