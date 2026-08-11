# KKI 2026 ROV — SITL Testing Quick Start

Panduan cepat testing misi autonomous ROV tanpa hardware fisik menggunakan SITL (Software-In-The-Loop) mock.

## Prasyarat

```bash
pip install pymavlink opencv-python pyzbar numpy
```

## Setup Cepat (3 komponen dalam 1 perintah)

```bash
cd /home/rasya/GUI-ROV
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state DIVE --no-wait-autonomous
```

**Apa yang terjadi:**
1. **VEHICLE (sitl_mock.py)** — Simulasi MAVLink ROV tanpa hardware
2. **ROV_LINK (rov_link.py)** — Bridge GUI ↔ vehicle + Mission5FSM
3. **FSM (mission5.py)** — Eksekusi misi 5 tahap autonomous
4. **GUI (server.js)** — Browser interface (opsional, tekan Ctrl+B di localhost:3000)

## Testing Skenario

### A. Full Misi 1-5 (Dari menyelam sampai release payload)

```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state DIVE --no-wait-autonomous
```

**Timeline:**
- DIVE (15s) → menyelam ke dasar (0.70m)
- SCAN_QR (20s) → cari QR code di dasar
- GRAB (10s) → ambil payload dengan gripper
- NAV_WALL (30s) → navigasi ke dinding target
- HANG (15s) → gantung payload ke hook (visual servo)
- SURFACE (15s) → naik ke permukaan
- DOCK (15s) → docking visual ke hook (surface docking)
- M5_REDIVE (15s) → selam ulang akuisisi QR payload
- M5_DOCK (25s) → docking closed-loop ke QR (nembak x/y)
- M5_ENGAGE (12s) → grab payload (steady positioning)
- M5_UNHOOK (10s) → angkat & tarik lepas dari hook
- M5_ASCEND (20s) → naik ke permukaan bawa payload
- **Total: ≈3.5 menit** (masuk 10 menit running limit)

### B. Test Misi 5 Autonomous Saja (Setelah manual docking selesai)

```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state M5_REDIVE --no-wait-autonomous
```

Simulasi: operator sudah menyelam & dock di permukaan, FSM tinggal execute lepas payload.

### C. Test Docking Visual Saja

```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state M5_DOCK --no-wait-autonomous
```

Test closed-loop servo ke QR payload (PBVS/IBVS).

## Integrasi GUI (Autonomous Toggle)

Kalau ingin uji toggle Manual↔Autonomous dari GUI:

```bash
# Terminal 1: Jalankan SITL tanpa FSM (menunggu GUI toggle)
python autonomy/tools/launch_sitl.py --vision mock

# Terminal 2 (atau browser localhost:3000): Toggle tombol "Manual/Autonomous" header
# FSM otomatis mulai saat toggle → Autonomous
```

## Telemetri & Logging

Semua komponen menulis ke stdout dengan label warna:
- `[VEHICLE]` — sitl_mock.py state (heading, depth, arm status)
- `[ROV_LINK]` — command & telemetry routing
- `[FSM]` — mission state, visual servo output, skor

Contoh output FSM:
```
[FSM] [scan_qr] QR payload terdeteksi: data={"mission":5,...} → target wall=C
[FSM] ✓ Misi 1 selesai (+15 poin)
[FSM] [docking] servo(PBVS) x=-0.02 y=0.05 z=0.31 → su=50 sw=15 vt=-10
[FSM] ✓ Misi 5 AUTONOMOUS selesai (+40 poin)
[FSM] ===== SKOR AKHIR =====
[FSM]  Misi 1 (Scan QR)     : 15/15
[FSM]  Misi 2 (Grab Payload): 15/15
[FSM]  Misi 3 (Hang Payload): 15/15
[FSM]  Misi 4 (Surface Dock): 15/15
[FSM]  Misi 5 (Auto Release): 40/40
[FSM]  TOTAL               : 100/100
```

## Vision Pipeline (dwe.npz Model)

QR detection menggunakan model terlatih di `autonomy/vision/calibration/dwe.npz` (atau fallback cv2.QRCodeDetector).

Mode mock (`--vision mock`): QR dipancarkan sintetis, convergen otomatis ke center frame seiring waktu.

## Tuning Parameter (untuk testing nyata di kolam)

Setelah SITL berjalan, gunakan config file untuk override tuning:

```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --config autonomy/config/mission5.example.yaml
```

File config bisa override:
- Depth targets (DEPTH_TARGET_BOTTOM, HOOK_DEPTH)
- Servo gain (IBVS_KP_*, PBVS_KP_*)
- Mechanical timing (HANG_SEAT_T, UNHOOK_LIFT_T, dll)
- Heading per wall (WALL_HEADING)
- Arah servo (SERVO_INVERT)

Lihat `autonomy/config/mission5.example.yaml` untuk template.

## Troubleshooting

### "dwe.npz tidak ditemukan"
Model kalibrasi tidak ada. Pakai `--vision mock` atau letakkan `.npz` di path yang benar.

### "opencv tidak tersedia" / "pyzbar import error"
Install: `pip install opencv-python pyzbar`

Pada Linux: `apt install libzbar0`

### "Heartbeat timeout dari vehicle"
- Pastikan sitl_mock.py jalan (pesan `[MOCK]` terlihat)
- Pastikan --mavlink endpoint cocok (default `udpin:0.0.0.0:14555`)

### FSM stuck di satu state
- Cek log FSM (catat timeout values di mission5.py, lihat `TIMEOUT_*`)
- Mock mode vision sengaja convergen lambat (MOCK_FAR_SEC=3, MOCK_CONVERGE_SEC=3) agar bisa verify servo logic

## Next: Testing di Hardware

Setelah SITL lolos, test berturut-turut:

1. **Bench / dry run** — Gripper, thruster PWM, depth sensor
2. **Pool shallow** — Full misi 1-4 (manual drive 1-4, autonomous 5 optional)
3. **Pool depth penuh** — Full misi 1-5, verifikasi depth targets & servo tuning
4. **Kompetisi** — Run tanpa debug, score 100 poin ideal (40 autonomous + 60 manual/partial)

---

**Created:** 2026-08-11  
**Status:** Siap SITL, await pool testing untuk hardware verification
