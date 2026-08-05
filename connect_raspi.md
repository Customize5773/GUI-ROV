LANGKAH AWAL :
```bash
ssh hydroships@192.168.2.2
password : (spasi 1 kali)
```

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