# SETUP — GUI-ROV Koneksi & Trial

## Prasyarat

- Node.js ≥ 18 (server.js)
- Python 3 (rov_agent.py di RPI)
- Ethernet umbilical: laptop ↔ RPI, satu subnet (contoh: laptop `192.168.2.1`, RPI `192.168.2.2`)

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

---

## 2. Sisi Laptop (Server Node.js)

```bash
# Masuk ke repo
cd /home/rasya/GUI-ROV

# Install dependency server
cd server && npm install

# Jalankan server (mode LIVE — butuh RPI nyata)
npm start

# ATAU mode simulasi (tanpa RPI, telemetri palsu)
npm run sim
```

> Buka browser ke `http://localhost:8080` — dashboard muncul.

---

## 3. Uji Koneksi UDP (Laptop → RPI)

```bash
# Dari laptop, kirim test command ke RPI port 14550
echo '{"name":"light","value":true,"t":'$(date +%s)'}' | nc -u -w1 192.168.2.2 14550
```

Di RPI, `journalctl -u rov-agent -f` harus menampilkan command masuk.

---

## 4. Trial (Full End-to-End)

```bash
# Terminal 1 — Start server (LIVE)
cd /home/rasya/GUI-ROV/server
npm start

# Terminal 2 — Buka dashboard
# Browser: http://localhost:8080

# Terminal 3 (opsional) — Cek log RPI via SSH
ssh hydroships@192.168.2.2
journalctl -u rov-agent -f
```

1. Pastikan dashboard menunjukkan status **ONLINE** (link pill hijau).
2. Klik **ARM** di header → ROV armed.
3. Gerakkan sumbu keyboard (W/S, A/D, Q/E, R/F) → ROV bergerak.
4. Tekan **STOP** atau **Spasi** → failsafe, semua thruster netral.

---

## Tanpa Hardware (Simulasi Saja)

```bash
cd /home/rasya/GUI-ROV/server
npm run sim
```

Buka `http://localhost:8080` — ROV 3D bergerak mengikuti telemetri palsu.
Tidak ada UDP nyata ke RPI.

---

## Urutan Cepat (Copy-Paste)

```bash
# RPI (via SSH)
ssh hydroships@192.168.2.2
sudo systemctl restart rov-agent

# Laptop
cd /home/rasya/GUI-ROV/server && npm start
# Buka http://localhost:8080
```
