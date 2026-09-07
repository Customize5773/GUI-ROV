LANGKAH AWAL :
```bash
ssh hydroships@192.168.2.2
password : (spasi 1 kali)
```

## Sync Perubahan (Laptop → Pi)

`~/rov-agent` di Pi BUKAN clone git dari repo ini — layout-nya juga sudah
**diratakan**: `fsm/`, `vision/`, `tools/`, `config/`, `control/` langsung di
bawah `~/rov-agent`, bukan di dalam folder `autonomy/` seperti di repo laptop.
Jangan `rsync` seluruh repo mentah-mentah (bakal bikin folder `autonomy/`
ganda) — mapping tiap folder satu per satu:

```bash
# Dari root repo laptop (/home/rasya/GUI-ROV)

# 1) File Python di root repo (rov_agent.py, rov_mission5_bridge.py, dll)
rsync -av --include='*.py' --exclude='*' ./ hydroships@192.168.2.2:~/rov-agent/

# 2) Folder autonomy/* -> diratakan (tanpa prefix "autonomy/")
rsync -av --delete autonomy/fsm/    hydroships@192.168.2.2:~/rov-agent/fsm/
# Bobot *.onnx WAJIB ikut (Pi menjalankan YOLO sejak 7 Sep 2026); *.pt tetap
# ditahan di laptop karena Pi tidak punya (dan tidak butuh) torch/Ultralytics.
rsync -av --delete --exclude='*.pt' autonomy/vision/ hydroships@192.168.2.2:~/rov-agent/vision/
# hook_vision_worker.py TIDAK LAGI dikecualikan — ia sekarang berjalan DI PI.
rsync -av --delete autonomy/tools/ hydroships@192.168.2.2:~/rov-agent/tools/
rsync -av --delete autonomy/config/ hydroships@192.168.2.2:~/rov-agent/config/
rsync -av --delete autonomy/control/ hydroships@192.168.2.2:~/rov-agent/control/

# 3) Reload service supaya kode baru kepakai
ssh hydroships@192.168.2.2 "sudo systemctl restart rov-agent"
```

> Cek cepat tanpa nulis apa pun: tambahkan `--dry-run` di tiap `rsync`.
> Verifikasi hasil sync: `md5sum <file_lokal>` vs
> `ssh hydroships@192.168.2.2 md5sum ~/rov-agent/<file>` harus sama persis.
>
> `--delete` pada folder autonomy/* aman krn folder itu murni kode (bukan
> tempat pilot simpan sesuatu) — TAPI JANGAN pakai `--delete` pada langkah 1,
> karena banyak file `*.py.bak-*`/`rov_agent_frendy.py`/dll milik tim lain
> yang sengaja disimpan langsung di Pi (lihat [[pi-agent-deploy-drift]]).

## Pembagian Beban Vision

> **Berubah total 7 Sep 2026.** Sebelumnya YOLO berjalan di laptop dan hasilnya
> dikirim ke Pi lewat tether. Sekarang **seluruh sistem YOLO ada di Pi**.

- **Raspberry Pi:** menjalankan SEMUA inferensi. Dua worker systemd
  (`rov-vision-hook`, `rov-vision-qr`) membaca kamera dari **localhost**
  (uStreamer 8080/8081), menjalankan YOLOv8 lewat **cv2.dnn atas bobot `.onnx`**,
  lalu mengirim hasil ke `rov_agent.py` via UDP `127.0.0.1:14550` memakai amplop
  yang sama persis dengan yang dulu dikirim laptop. `rov_agent.py` tetap
  memvalidasi bbox/confidence/umur seperti sebelumnya — tidak ada baris
  validator yang berubah.
- **Laptop:** TIDAK menjalankan YOLO sama sekali. Hanya menampilkan video
  (proxy `/cam`) dan overlay bbox yang datang lewat telemetry (`hook_xy`).
  Ultralytics/torch di laptop kini murni untuk **training, export `.onnx`, dan
  test paritas** — bukan jalur runtime.
- **Pixhawk/ArduSub:** tetap menangani mixing thruster, stabilisasi, PWM, dan
  failsafe.

**Pi TIDAK memerlukan torch maupun Ultralytics.** `opencv-contrib-python` yang
sudah ada di `autonomy/requirements.txt` sudah cukup — `cv2.dnn` yang
menjalankan `.onnx`. Jangan instal Ultralytics di Pi.

### Kenapa dipindah

Loop kontrol vision tidak lagi melintasi tether: `age_ms` tak terkena antrean
jaringan, dan **QR docking tetap jalan meski kabel laptop tercabut**.

### Anggaran CPU — ini yang menjaga gerak ROV tetap waras

Pi 4 hanya punya 4 core, dan repo ini sudah pernah kena batunya: satu thread
optical-flow saja memakan ~85% CPU dan mengganggu ALT_HOLD (lihat komentar di
`autonomy/vision/optical_flow.py`). Karena itu isolasi core **bukan opsional**:

| Unit | CPUAffinity | Alasan |
|---|---|---|
| `rov-agent` | `0 1` | MAVLink + FSM. Tidak boleh direbut YOLO, apa pun yang terjadi. |
| `rov-vision-hook` | `2 3` | YOLO. Spike/thrashing di sini tak bisa menyentuh core 0-1. |
| `rov-vision-qr` | `2 3` | idem |

Lapis kedua: FSM menerbitkan `vision_want` (lihat `VISION_WANT` di
`autonomy/fsm/mission5.py`) — daftar kamera yang benar-benar dibaca state saat
ini. Worker yang tidak disebut **melewatkan inferensi**. Mis. di `M5_QR_DOCK`
hanya worker QR yang bekerja. Fail-open: tanpa telemetri, kedua worker jalan.

### Ekspor bobot (di LAPTOP, sekali tiap model baru)

`imgsz` graf ONNX **bersifat tetap dan dipilih saat export** — bukan knob
runtime. Tiap ukuran = satu berkas:

```bash
yolo export model=autonomy/vision/best_pose.pt format=onnx imgsz=640 simplify=True
yolo export model=autonomy/vision/best_new.pt  format=onnx imgsz=640 simplify=True
# Varian kecil (lebih cepat, akurasi turun) — rename setelah tiap export karena
# Ultralytics selalu menulis ke nama yang sama:
yolo export model=autonomy/vision/best_pose.pt format=onnx imgsz=320 simplify=True
mv autonomy/vision/best_pose.onnx autonomy/vision/best_pose_320.onnx
```

Verifikasi WAJIB sebelum deploy — decode ONNX ditulis tangan, dan keypoint 2..5
membidik servo docking:

```bash
pytest autonomy/tests/test_onnx_parity.py -v
```

### Hasil pengukuran di Pi ASLI (7 Sep 2026)

Perangkat: **Raspberry Pi 4 Model B Rev 1.5, aarch64, 4 core, RAM 8 GB**,
venv `~/rov-agent/.venv` (Python 3.12.3, cv2 5.0.0, numpy 2.5.2, **tanpa torch**).
Diukur dengan `rov-agent` + kedua uStreamer AKTIF.

| Model | imgsz | Bebas (4 core) | **Dipin core 2-3, 2 thread** | Ambang | Putusan |
|---|---|---|---|---|---|
| `best_pose.onnx` | 640 | 1,43 Hz | — | ≥2 Hz | **GAGAL** |
| `best_pose_416.onnx` | 416 | 3,36 Hz | **2,67 Hz** | ≥2 Hz | lolos (tipis) |
| `best_pose_320.onnx` | 320 | 5,87 Hz | **4,61 Hz** | ≥2 Hz | **LULUS** |
| `best_new.onnx` | 640 | 1,42 Hz | — | ≥3 Hz | **GAGAL** |
| `best_new_416.onnx` | 416 | 3,45 Hz | **~2,7 Hz** | ≥3 Hz | **GAGAL saat dipin** |
| `best_new_320.onnx` | 320 | 5,86 Hz | **4,61 Hz** | ≥3 Hz | **LULUS** |

**Pakai varian 320 untuk KEDUANYA.** 640 tidak pernah masuk akal (0,7 detik per
frame). 416 terlihat lolos pada bench bebas tetapi **jatuh ke ~2,7 Hz begitu
dipin ke 2 core** — di bawah gate QR 3 Hz. Hanya 320 yang lolos dengan margin
pada konfigurasi produksi sebenarnya.

> Soal akurasi 320: test paritas mencatat `best_new_320` tidak mendeteksi region
> QR pada korpus `fixtures/real_hard_cases/`. Perlu diletakkan pada konteks —
> keempat foto itu memang **gagal decode di SEMUA jenjang, termasuk zxing-cpp**
> (lihat `tests/test_qr_real_hard_cases.py`), dan worker hanya melapor bila QR
> benar-benar ter-decode. Jadi yang "hilang" di 320 adalah frame yang toh tidak
> menghasilkan apa pun. **Tetap wajib divalidasi ulang di kolam dgn QR nyata.**

Threading: `--cv-threads 2` (default) terukur sedikit LEBIH CEPAT daripada 4
thread saat dipin ke 2 core (216,9 ms vs 224,8 ms) — 4 thread hanya berebut core
yang sama, dan menambah panas percuma.

### Termal — diukur pada sistem TERPASANG (7 Sep 2026)

| Kondisi | Suhu | `get_throttled` |
|---|---|---|
| Idle (rov-agent + 2 uStreamer) | 71,1 °C | `0x0` |
| Stress sintetis 100% duty, 45 dtk | **81,3 °C** | `0xe0008` — soft temp limit AKTIF |
| **Konfigurasi TERPASANG, 90 dtk** (2 worker `--fps 4` + rov-agent) | **69,6–72,0 °C** | **`0x0` — tidak throttle** |

**Konfigurasi terpasang tidak menimbulkan throttling.** Angka 81,3 °C itu uji
beban sintetis tanpa jeda (inferensi berturut-turut tanpa henti) dan TIDAK
mewakili worker sesungguhnya: `--fps 4` plus throttle emit membuat duty jauh
lebih rendah. Suhu berjalan praktis sama dengan idle.

Tetap perlu diawasi, karena dua alasan:

1. Pengukuran ini di **meja terbuka**. Di enclosure tertutup pendinginan lebih
   buruk — ukur ulang di kondisi lomba sebenarnya.
2. Throttling termal bersifat **SoC-wide**: bila suatu saat tersentuh, penurunan
   clock ikut mengenai core 0-1. `CPUAffinity` melindungi loop kontrol dari
   perebutan penjadwalan, TIDAK dari panas.

`pi_temp` sudah dikirim `rov_agent.py` ke telemetry GUI — jadikan angka yang
diawasi pilot selama trial.

### Laju inferensi nyata setelah terpasang

CPU per worker ~1 core penuh (core 2-3 dipakai habis oleh keduanya). Karena itu:

| Kondisi | Worker aktif | Laju per worker |
|---|---|---|
| FSM idle / manual (gate fail-open) | keduanya | ~2,3 Hz |
| `M5_QR_DOCK`, `M5_HOOK_ALIGN` | **satu** | **~4,6 Hz** |
| `M5_YOLO_SEARCH` | keduanya | ~2,3 Hz |

Inilah alasan gate `vision_want` berarti: pada fase docking yang menentukan,
worker yang bekerja mendapat KEDUA core dan berjalan ~4,6 Hz — di atas gate QR
3 Hz. Di `M5_YOLO_SEARCH` keduanya berbagi, jadi pengintipan QR di state itu
lebih jarang berhasil (jalan pintas ke M5_QR_DOCK jadi kurang sering terpakai —
bukan kegagalan, alur hook tetap jalan).

### Pilih ukuran model (jalankan ulang bila model berubah)

```bash
# di Pi, dengan rov-agent AKTIF (angka di Pi menganggur menipu)
python3 ~/rov-agent/tools/bench_pi_yolo.py
```

Ambang berasal dari gate kesegaran `rov_agent.py`: QR >= 3 Hz, hook >= 2 Hz.
Bench berjalan TANPA pin core, jadi angkanya optimistis — verifikasi kandidat
dengan `taskset -c 2,3` sebelum memutuskan.

### Unit systemd worker vision

> **Pakai interpreter venv, BUKAN `/usr/bin/python3`.** Audit Pi 7 Sep 2026:
> python3 sistem TIDAK punya cv2/numpy/pymavlink sama sekali — semuanya ada di
> `~/rov-agent/.venv` (Python 3.12.3, cv2 5.0.0), dan `rov-agent.service` yang
> berjalan sekarang memang sudah memakai venv itu. Menunjuk `/usr/bin/python3`
> membuat worker mati saat start dengan `ModuleNotFoundError: cv2`.


```ini
# /etc/systemd/system/rov-vision-hook.service
[Unit]
Description=ROV Vision Worker — CAM WALL (hook, YOLOv8-Pose)
After=rov-agent.service ustreamer-cam1.service

[Service]
User=hydroships
WorkingDirectory=/home/hydroships/rov-agent
Environment=PYTHONPATH=/home/hydroships/rov-agent
ExecStart=/home/hydroships/rov-agent/.venv/bin/python tools/hook_vision_worker.py --camera http://127.0.0.1:8080/stream --model vision/best_pose_320.onnx --map config/hook_map.pool.yaml --calib vision/calibration/wall.npz --emit-udp 127.0.0.1:14550 --telemetry-port 14556 --cv-threads 2 --fps 4
CPUAffinity=2 3
Nice=10
CPUWeight=20
MemoryMax=700M
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/rov-vision-qr.service
[Unit]
Description=ROV Vision Worker — CAM BOTTOM (region QR + decode dalam crop)
After=rov-agent.service ustreamer-cam2.service

[Service]
User=hydroships
WorkingDirectory=/home/hydroships/rov-agent
Environment=PYTHONPATH=/home/hydroships/rov-agent
ExecStart=/home/hydroships/rov-agent/.venv/bin/python tools/qr_vision_worker.py --camera http://127.0.0.1:8081/stream --model vision/best_new_320.onnx --calib vision/calibration/bottom.npz --conf 0.6 --emit-udp 127.0.0.1:14550 --telemetry-port 14557 --cv-threads 2 --fps 4
CPUAffinity=2 3
Nice=10
CPUWeight=20
MemoryMax=700M
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Tambahkan ke `rov-agent.service` (bagian `[Service]`) supaya loop kontrol punya
core sendiri:

```ini
CPUAffinity=0 1
Nice=-5
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rov-vision-hook rov-vision-qr
sudo systemctl restart rov-agent
journalctl -u rov-vision-qr -f          # hasil deteksi per baris JSON
```

Cek isolasi core benar-benar berlaku:

```bash
for u in rov-agent rov-vision-hook rov-vision-qr; do
  echo -n "$u: "; taskset -cp $(systemctl show -p MainPID --value $u) 2>/dev/null
done
```

## Ambil Log Trial (Pi → Laptop)

Tiap kali toggle Autonomous dijalankan, `rov_mission5_bridge.py` menulis satu
file `.jsonl` per trial ke `~/rov-agent/logs/` di Pi — **bukan** ke folder
`autonomy/logs/` di laptop (dua mesin, dua filesystem).

**Otomatis (sejak server.js diupdate):** tiap kali panel "Run terakhir" minta
`/api/runs`, server laptop lebih dulu `rsync` diam-diam menarik `*.jsonl`
terbaru dari `~/rov-agent/logs/` di Pi ke `autonomy/logs/` laptop lewat SSH
key yang sama dipakai `ssh hydroships@192.168.2.2` — tidak perlu scp manual
lagi. Gagal-lunak: kalau Pi mati/kabel putus, panel tetap tampil (pakai file
lokal yang ada), timeout 5 detik, tidak nge-hang.

Syarat: SSH key laptop → Pi harus sudah passwordless (`ssh-copy-id` sekali di
awal). Kalau user/path Pi beda dari default, override via env saat start
server: `RPI_SSH_USER=<user> RPI_LOG_DIR=<path/ke/rov-agent/logs> npm start`.

```bash
# Manual (debug/fallback kalau rsync otomatis gagal, mis. SSH key belum ada):
scp hydroships@192.168.2.2:~/rov-agent/logs/run_*.jsonl autonomy/logs/

# Cek isi run terbaru dari CLI (skor, state akhir, durasi):
python3 autonomy/tools/analyze_run.py autonomy/logs/run_<timestamp>.jsonl
```

> Aman dijalankan berulang — nama file berbasis timestamp, `scp`/`rsync`
> cuma menimpa file yang sama persis kalau diulang, tidak ada file baru dobel.

cara cek
```bash
sudo systemctl start rov-agent (start service)
sudo systemctl restart rov-agent (reload service kalau ada perubahan)
sudo systemctl stop rov-agent (stop rov-agent)
sudo systemctl status rov-agent (cek status rov-agent)
journalctl -u rov-agent -f (cara melihat log)
nano ~/rov-agent/rov_agent.py (edit file.py)
```

cara shutdown 
```bash
sudo power off 
```
```bash
sudo systemctl restart rov-agent
journalctl -u rov-agent -f
```

---

## Topologi Jaringan

```bash
# Cek IP Pi
ip a

# Pastikan bisa ping dari laptop
# (laptop)  ping -c 3 192.168.2.2
```

> Topologi standar: laptop di `192.168.2.1`, Pi di `192.168.2.2`. Telemetry
> (14551) dan command (14550) lewat UDP. Jika IP berubah, update di `.env`
> (`LAPTOP_IP`).

## Variabel Environment (`.env`)

```bash
# File konfig lokal (jangan di-commit — lihat .gitignore)
nano ~/rov-agent/.env

# Reload systemd agar EnvironmentFile terbaca setelah edit
sudo systemctl restart rov-agent
```

Variabel penting di sisi Pi:

| Variabel | Default | Deskripsi |
|---|---|---|
| `LAPTOP_IP` | `192.168.2.1` | IP tujuan telemetry stream |
| `UDP_IN` | `14551` | Port telemetry DARI Pi KE laptop |
| `UDP_OUT` | `14550` | Port command DARI laptop KE Pi |
| `PIXHAWK_PORT` | `/dev/ttyACM0` | Port serial Pixhawk |
| `PIXHAWK_BAUD` | `115200` | Baud rate serial |

> Di systemd unit, `.env` dimuat via `EnvironmentFile=`.

## Unit Systemd

```bash
# Lihat unit file
sudo systemctl cat rov-agent

# Edit unit (jika perlu ganti port/path/EnvironmentFile)
sudo systemctl edit --full rov-agent

# Auto-start saat boot
sudo systemctl enable rov-agent

# Non-aktifkan auto-start
sudo systemctl disable rov-agent
```

Unit standar (referensi):

```ini
[Unit]
Description=ROV Agent (rov_agent.py)
After=network.target

[Service]
User=hydroships
WorkingDirectory=/home/hydroships/rov-agent
EnvironmentFile=/home/hydroships/rov-agent/.env
ExecStart=/usr/bin/python3 /home/hydroships/rov-agent/rov_agent.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

## Jalankan Agent Manual (Debug)

```bash
# Matikan service otomatis
sudo systemctl stop rov-agent

# Jalankan langsung — lihat output, tidak lewat journalctl
python3 ~/rov-agent/rov_agent.py

# Pakai tmux agar tetap berjalan setelah SSH putus
tmux new -s rov
python3 ~/rov-agent/rov_agent.py
# Ctrl+B, D  -> detach;  tmux attach  -> kembali
```

> Setelah debug, jalankan `sudo systemctl start rov-agent` agar agent kembali
> berjalan otomatis di systemd.

## Cek Hardware

```bash
# Pastikan Pixhawk terdeteksi di port serial
ls -l /dev/ttyACM*

# Cek device USB yang terhubung
dmesg | grep tty
lsusb

# Cek dependency Python
python3 -c "import pymavlink; print(pymavlink.__version__)"
pip3 show pymavlink
```

## Test Koneksi UDP (dari Laptop)

```bash
# Kirim perintah langsung ke Pi port 14550
echo '{"name":"light","value":true}' | nc -u -w1 192.168.2.2 14550

# Lihat di Pi apakah command diterima:
# journalctl -u rov-agent -f
```

## Perilaku Link & Reconnect

- **Link timeout**: jika tidak ada pesan MAVLink selama `LINK_TIMEOUT` (3 detar)
  dari Pixhawk, agent anggap link terputus dan mencoba sambung ulang otomatis
  (lihat `connect_pixhawk()` / `drop_link()` di `rov_agent.py`).
- **Heartbeat GCS**: agent mengirim heartbeat GCS 1 Hz ke Pixhawk. Tanpa ini
  ArduSub akan **disarm otomatis** (FS_GCS_ENABLE).
- **Auto-restart**: jika process crash, systemd restart otomatis
  (`Restart=always`, `RestartSec=3`).

## Troubleshooting

| Gejala | Solusi |
|---|---|
| `ssh: connect to host 192.168.2.2` | Cek kabel ethernet, `ip a` di laptop & Pi |
| `status` → `failed` | `journalctl -u rov-agent -e --no-pager` — cek error Python / port bentrok |
| Pixhawk tidak terdeteksi | `ls /dev/ttyACM*` kosong → kabel/USB rusak; cek `dmesg` |
| `Permission denied` pada `/dev/ttyACM0` | `sudo usermod -a -G dialout $USER` lalu logout/login |
| Port 14550 bentrok | `sudo lsof -i:14550` — hanya boleh satu `rov_agent.py` |
| Telemetry tidak sampai ke GUI | Cek `LAPTOP_IP` di `.env` sesuai IP laptop |
| Agent restart berulang | `journalctl -u rov-agent --no-pager -n 50`
