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
# Bobot YOLO *.pt tetap di laptop; Pi tidak memuat Ultralytics/model.
rsync -av --delete --exclude='*.pt' autonomy/vision/ hydroships@192.168.2.2:~/rov-agent/vision/
rsync -av --delete --exclude='hook_vision_worker.py' autonomy/tools/ hydroships@192.168.2.2:~/rov-agent/tools/
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

- **Laptop:** `npm start` otomatis menyalakan `hook_vision_worker.py`, membuka
  CAM WALL `http://192.168.2.2:8080/stream`, memuat `best_pose.pt`, lalu
  menjalankan YOLOv8-Pose/keypoint dan lokalisasi hook.
- **Raspberry Pi:** `rov_agent.py` hanya menerima JSON `hook_vision`, memvalidasi
  bbox/confidence, dan membuang hasil yang berumur lebih dari 1 detik. Detektor
  hook OpenCV lokal serta fallback wall-CNN juga dimatikan; Pi tidak memuat
  model `.pt` atau paket Ultralytics. Decoder QR asli masih berjalan di Pi saat
  Mission 5 aktif karena hasilnya dipakai langsung oleh FSM.
- **Pixhawk/ArduSub:** tetap menangani mixing thruster, stabilisasi, PWM, dan
  failsafe. Pemindahan vision tidak memindahkan kontrol hardware ke laptop.

Di laptop instal `autonomy/requirements-laptop.txt`. Di Pi instal
`autonomy/requirements.txt`; jangan instal Ultralytics/Torch hanya untuk hook.

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
