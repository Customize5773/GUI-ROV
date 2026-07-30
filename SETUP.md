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
- [ ] **Cabut USB F310** saat stik terdefleksi → axis langsung dinetralkan.
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
