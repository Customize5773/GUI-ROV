# SETUP — GUI-ROV Trial

> Panduan copy-paste untuk setup cepat tiap trial. Baca dari atas ke bawah sesuai mode yang dipakai.

---

## Prasyarat

- Node.js ≥ 18 (server.js)
- Python 3 (rov_agent.py di RPI)
- Ethernet laptop ↔ RPI dalam satu subnet (contoh: laptop `192.168.2.1`, RPI `192.168.2.2`)

### Dependency Python

```bash
pip install -r requirements.txt          # rov_agent.py + unit test di root
pip install -r autonomy/requirements.txt # opsional: stack autonomy/visi
```

### Konfigurasi environment

Semua port, alamat, dan port serial punya default yang sudah benar untuk
topologi tether standar. Kalau perlu mengubahnya, salin contohnya:

```bash
cp .env.example .env
```

`.env` tidak di-commit. Cara memuatnya:

```bash
# Laptop — start-gui.sh memuatnya otomatis kalau ada.
./start-gui.sh sim

# Manual, shell POSIX:
set -a; . ./.env; set +a

# RPI — lewat systemd:
#   [Service]
#   EnvironmentFile=/home/hydroships/GUI-ROV/.env
```

---

## 1. Sisi RPI (ROV)

```bash
# SSH ke RPI
ssh hydroships@192.168.2.2
# password: (spasi 1 kali)

# Pastikan rov-agent aktif
sudo systemctl start rov-agent
sudo systemctl status rov-agent

# Kalau ada perubahan di rov_agent.py, reload:
sudo systemctl restart rov-agent

# Lihat log real-time:
journalctl -u rov-agent -f
```

> Jika `status` menunjukkan `active (running)`, RPI siap.

---

## 2. Sisi Laptop (Server Node.js)

```bash
# Masuk ke repo
cd /home/rasya/GUI-ROV

# LIVE (default) — butuh RPI nyala & terhubung
./start-gui.sh

# ATAU simulasi — tanpa RPI
./start-gui.sh sim
```

> Buka browser ke `http://localhost:8080`. Dashboard harus muncul.

---

## 3. Uji Koneksi UDP (Laptop → RPI) — LIVE mode saja

```bash
# Kirim test command ke RPI port 14550
echo '{"name":"light","value":true,"t":'$(date +%s)'}' | nc -u -w1 192.168.2.2 14550
```

Di RPI (`journalctl -u rov-agent -f`), harus muncul log command masuk.

---

## 4. Trial (Full End-to-End) — LIVE mode

```bash
# Terminal 1 — Start server (script otomatis cek dependency + buka browser)
cd /home/rasya/GUI-ROV
./start-gui.sh

# Terminal 2 — Cek log RPI via SSH (opsional)
ssh hydroships@192.168.2.2
journalctl -u rov-agent -f
```

### Checklist Trial

- [ ] Dashboard menunjukkan status **ONLINE** (link pill hijau)
- [ ] Klik **ARM** di header → ROV armed
- [ ] Gerakkan sumbu keyboard (W/S, A/D, Q/E, R/F) → ROV bergerak
- [ ] Tekan **STOP** atau **Spasi** → failsafe, semua thruster netral

---

## 5. Simulasi (Tanpa Hardware)

```bash
cd /home/rasya/GUI-ROV
./start-gui.sh sim
```

Buka `http://localhost:8080` — ROV 3D bergerak mengikuti telemetri palsu. Tidak ada UDP nyata ke RPI.

**Pakai mode ini untuk semua sesi coding.** Kalau laptop Anda tidak sesubnet
dengan RPI (mis. laptop di `192.168.67.x` sedangkan RPI di `192.168.2.2`), mode
LIVE hanya akan menghasilkan log "gagal kirim command" — bukan bug.

Yang bisa diuji di mode sim tanpa hardware sama sekali:

- Tab **Manual / Stabilize / Depth Hold / Acro** — server SIM sekarang menerima
  `pilot_mode` dan memantulkannya sebagai field `mode` pada telemetri, persis
  seperti HEARTBEAT dari Pixhawk. Jadi sorotan tab, badge mode aktual, dialog
  konfirmasi ACRO, dan badge peringatan ACRO semuanya berperilaku nyata.
- Arm/disarm, lampu, E-Stop, rekaman, halaman replay.

Yang **tidak** bisa diuji di sini: apa pun yang butuh MAVLink sungguhan (mode
ditolak firmware, respons thruster). Untuk itu pakai mock MAVLink:

```bash
python autonomy/sitl_mock.py --mavlink udpout:127.0.0.1:14555
python autonomy/rov_link.py --server 127.0.0.1 --mavlink udpin:0.0.0.0:14555
```

Detailnya di [`autonomy/SITL_SETUP.md`](autonomy/SITL_SETUP.md).

---

## 6. Troubleshooting

| Gejala | Solusi |
|---|---|
| Dashboard **OFFLINE** | Cek kabel Ethernet, pastikan laptop & RPI di subnet yang sama (`ip a` di kedua sisi) |
| RPI `active (failed)` | Cek log: `journalctl -u rov-agent -e` — biasanya konfigurasi atau library Python yang error |
| Port `14550` bentrok | Pastikan hanya satu `rov_agent.py` yang berjalan (`sudo systemctl restart rov-agent`) |
| Thruster tidak respons | Cek nilai `x`, `y`, `r` di dashboard — harus ada saat joystick digerakkan |
| Server crash `EADDRINUSE` | Port 8080 dipakai proses lain — kill: `lsof -ti:8080 \| xargs kill -9` |

---

## 7. Urutan Cepat (Copy-Paste)

### LIVE (dengan RPI)

```bash
# RPI (via SSH)
ssh hydroships@192.168.2.2
sudo systemctl restart rov-agent

# Laptop
cd /home/rasya/GUI-ROV && ./start-gui.sh
# Browser otomatis buka http://localhost:8080
```

### SIM (tanpa RPI)

```bash
cd /home/rasya/GUI-ROV && ./start-gui.sh sim
# Browser otomatis buka http://localhost:8080
```

---

## 8. Pre-Trial Health Check

Cek cepat sebelum arm. Jalankan di laptop (dan SSH ke RPI untuk yang bertanda `*`):

```bash
# Laptop: ping RPI
ping -c 3 192.168.2.2

# Laptop: pastikan port 8080 tidak bentrok
lsof -ti:8080 || echo "port 8080 free"

# Laptop: pastikan npm deps terpasang
[ -d server/node_modules ] && echo "deps OK" || (cd server && npm install)

# * RPI: pastikan rov-agent aktif
ssh hydroships@192.168.2.2 "sudo systemctl is-active rov-agent && echo 'ROV agent OK'"

# * RPI: pastikan Pixhawk terdeteksi
ssh hydroships@192.168.2.2 "ls /dev/ttyACM* && echo 'Pixhawk detected'"
```

---

## 9. Trial Start Commands

### One-liner: Start Everything (LIVE)

```bash
# Laptop: restart RPI agent + start GUI server sekaligus
ssh hydroships@192.168.2.2 'sudo systemctl restart rov-agent' && cd /home/rasya/GUI-ROV && ./start-gui.sh
```

### One-liner: Start Everything (SIM)

```bash
cd /home/rasya/GUI-ROV && ./start-gui.sh sim
```

### Restart hanya ROV agent (setelah edit kode di Pi)

```bash
ssh hydroships@192.168.2.2 'sudo systemctl restart rov-agent && journalctl -u rov-agent -n 5 -f'
```

---

## 10. Direct UDP Commands to ROV

Kirim perintah langsung via UDP (port `14550`) bila GUI tidak responsif atau untuk scripting:

```bash
# Arm ROV
echo '{"name":"arm","value":true}' | nc -u -w1 192.168.2.2 14550

# Disarm ROV
echo '{"name":"arm","value":false}' | nc -u -w1 192.168.2.2 14550

# E-Stop (stop + disarm)
echo '{"name":"stop","value":true}' | nc -u -w1 192.168.2.2 14550

# Toggle lampu
echo '{"name":"light","value":true}' | nc -u -w1 192.168.2.2 14550

# Set pilot mode (MANUAL / STABILIZE / ALT_HOLD / ACRO)
echo '{"name":"pilot_mode","value":"ALT_HOLD"}' | nc -u -w1 192.168.2.2 14550

# Set pool depth (meter) — batas atas setpoint depth-set
echo '{"name":"pool_depth","value":0.9}' | nc -u -w1 192.168.2.2 14550

# Depth-set: rekam kedalaman SEKARANG jadi setpoint (setara tombol SET / D-pad ↑)
echo '{"name":"depth_set","value":true}' | nc -u -w1 192.168.2.2 14550

# Depth-set ON/OFF (value null = toggle). Butuh setpoint + armed;
# bias baru benar-benar dikirim saat mode ALT_HOLD.
echo '{"name":"depth_hold","value":true}'  | nc -u -w1 192.168.2.2 14550
echo '{"name":"depth_hold","value":false}' | nc -u -w1 192.168.2.2 14550

# Kontrol gripper
echo '{"name":"gripper","value":"open"}' | nc -u -w1 192.168.2.2 14550
echo '{"name":"gripper","value":"close"}' | nc -u -w1 192.168.2.2 14550

# Motor test: motor 1 maju 15% selama 1s
echo '{"name":"motor_test","value":{"motor":1,"throttle":15,"duration":1,"direction":"forward"}}' | nc -u -w1 192.168.2.2 14550
```

> Semaphore `timestamp`/`t` otomatis ditambah server.js. Perintah ini hanya untuk LIVE mode ke RPI.

---

## 11. Motor Test Quick Reference

Uji semua thruster secara berurutan:

```bash
# Test semua motor 1-6
for m in 1 2 3 4 5 6; do
  echo "{\"name\":\"motor_test\",\"value\":{\"motor\":$m,\"throttle\":15,\"duration\":1,\"direction\":\"forward\"}}" | nc -u -w1 192.168.2.2 14550
  sleep 1.5
done

# Test motor 3 mundur
echo '{"name":"motor_test","value":{"motor":3,"throttle":15,"duration":1,"direction":"reverse"}}' | nc -u -w1 192.168.2.2 14550
```

> Throttle maksimal motor_test = 20% (di-hardcode di `rov_motor_test.py`).

---

## 12. Recording Management

```bash
# Laptop: list rekaman (via HTTP API)
curl -s http://localhost:8080/api/recordings | python3 -m json.tool

# Laptop: bersihkan rekaman lama (>24h)
find /home/rasya/GUI-ROV/server/recordings -maxdepth 1 -type d -mtime +1 -exec rm -rf {} +

# Laptop: list direktori rekaman dengan ukuran
du -sh /home/rasya/GUI-ROV/server/recordings/* 2>/dev/null
```

---

## 13. Post-Trial Cleanup

```bash
# Laptop: disarm ROV
echo '{"name":"arm","value":false}' | nc -u -w1 192.168.2.2 14550

# Laptop: kill server (Ctrl+C juga jika terminal masih terbuka)
pkill -f "node server.js" || lsof -ti:8080 | xargs kill -9

# * RPI: stop rov-agent
ssh hydroships@192.168.2.2 'sudo systemctl stop rov-agent && echo "ROV agent stopped"'
```

> **Shutdown RPI hanya bila perlu secara fisik** (mis. baterai habis). Untuk trial
> berikutnya cukup `restart` agar lebih cepat:
> ```bash
> ssh hydroships@192.168.2.2 'sudo systemctl restart rov-agent'
> ```

---

## 14. Test Commands

Verifikasi cepat setelah perubahan kode:

```bash
# Python unit tests (dari repo root)
python3 -m unittest test_rov_axes -v
python3 -m unittest test_rov_modes -v
python3 -m unittest test_rov_mavlink -v
python3 -m unittest test_rov_pid -v
python3 -m unittest test_rov_motor_test -v
python3 -m unittest test_rov_params -v
python3 -m unittest test_rov_gripper -v

# JS server tests
cd server && npm test

# JS mode-gating test (ESM)
node test/mode-gating.test.mjs
```

---

## 15. Environment Variables Quick Reference

Override via env var sebelum `./start-gui.sh`:

```bash
# Override alamat RPI (subnet berbeda)
RPI_ADDR=192.168.2.2 ./start-gui.sh

# Override port server
WS_PORT=8081 ./start-gui.sh

# Izinkan proxy /cam ke host kamera mana saja (lab/testing only)
CAM_ALLOW_ANY=1 ./start-gui.sh

# SIM dengan RPI_ADDR loop back
RPI_ADDR=127.0.0.1 WS_PORT=8080 ./start-gui.sh sim
```

--

## 16. Shutdown (Sisi RPI (Aman & Tanpa Risk Corrupt SD Card))

```bash
# 1. Hentikan service agen
sudo systemctl stop rov-agent

# 2. Shutdown OS RPI
sudo poweroff
# ssh hydroships@192.168.2.2 "sudo systemctl stop rov-agent && sudo poweroff"
```
