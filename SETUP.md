# SETUP — GUI-ROV Trial

> Panduan copy-paste untuk setup cepat tiap trial. Baca dari atas ke bawah sesuai mode yang dipakai.

---

## Prasyarat

- Node.js ≥ 18 (server.js)
- Python 3 (rov_agent.py di RPI)
- Ethernet laptop ↔ RPI dalam satu subnet (contoh: laptop `192.168.2.1`, RPI `192.168.2.2`)

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
