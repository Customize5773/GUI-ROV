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

---

## 5. Checklist Trial Gamepad F310

Kerjakan berurutan. **Jangan lanjut ke tahap berikutnya sebelum tahap sebelumnya
lulus.** Mapping lengkap ada di [CONTROL-MAPPING.md](CONTROL-MAPPING.md).

### A. Meja kerja — tanpa wahana

```bash
cd /home/rasya/GUI-ROV
.venv/bin/python -m pytest -q test_rov_axes.py test_rov_gripper.py test_attitude_filter.py
node --test server/test/*.test.mjs
node server/server.js --sim
```

Colok F310, **pastikan switch belakang di posisi `X`**, lalu di `http://localhost:8080`:

- [ ] Halaman **Joystick** → nama controller terdeteksi, panel tester **bergerak**
      (kalau beku, ada error di console).
- [ ] Baris axis menampilkan `axis 0..3` + `triggers`.
- [ ] **Lepas semua stik → keempat nilai mapped tepat `0`.** Ini bukti deadzone jalan.
- [ ] Defleksi penuh tiap stik → `±1000`, dan arahnya benar
      (dorong stik kanan ke atas = surge positif).
- [ ] RT / LT → nilai `Grip` bergerak proporsional.
- [ ] Tab controller = **Gamepad**, lalu D-Pad ↓/←/↑ → tab mode berpindah dan
      badge mode mengikuti.
- [ ] **LB/RB** → angka `GAIN` di HUD berubah.
- [ ] **Back** → semua axis nol dan terkunci; **Start** → terbuka lagi.

### B. Bench — wahana menyala, **PROPELLER DILEPAS**

> Lakukan dengan propeller dilepas. Beberapa langkah sengaja memicu thrust.

- [ ] `journalctl -u rov-agent -f` bersih (tidak ada banjir log axis).
- [ ] **ARM, semua stik netral → TIDAK ADA thrust vertikal sama sekali.**
      Ini pengujian paling penting: sebelum perbaikan konversi `z`, kondisi ini
      justru memberi perintah turun penuh.
- [ ] Uji tiap sumbu satu per satu, cocokkan dengan arah putaran motor yang
      diharapkan untuk frame BlueROV1. Perbaiki lewat **Setup → Reverse arah thruster**
      bila ada yang terbalik.
- [ ] **Tutup tab browser saat ter-ARM** → dalam ≤0,5 detik log Pi menampilkan
      `[FAILSAFE] ... kirim NEUTRAL` dan thruster netral. Buka lagi → `kontrol manual pulih`.
- [ ] **Cabut USB F310 saat stik terdefleksi** → axis langsung dinetralkan.
- [ ] Gripper: A menutup, B membuka, X netral, RT/LT proporsional — gerakannya
      halus (bukan menyentak).
- [ ] Pindah MANUAL → STABILIZE → DEPTH HOLD dari D-Pad **dan** dari tab GUI;
      sorotan tab harus mengikuti HEARTBEAT asli. Kalau tab tetap putus-putus lalu
      muncul peringatan, berarti Pixhawk **menolak** mode itu — periksa sensor
      kedalaman sebelum lanjut.

### C. Dalam air

- [ ] Mulai di **MANUAL**, gain **25–40%**. Naikkan hanya setelah pilot hafal responsnya.
- [ ] Cek daya apung & trim netral sebelum menguji DEPTH HOLD.
- [ ] **DEPTH HOLD dengan stik vertikal netral → wahana MENAHAN kedalaman**, tidak
      perlahan tenggelam. Kalau tenggelam, kembali ke MANUAL dan periksa
      kalibrasi sensor tekanan.
- [ ] Uji E-Stop (**Back**) sungguhan pada kedalaman aman, minimal sekali,
      sebelum manuver dekat struktur.

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
