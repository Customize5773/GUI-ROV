# KKI 2026 Autonomous System — Testing Checklist

**Baseline:** Mission5FSM fully implemented (1065 lines) ✅  
**Session:** Integration layer (GUI toggle ↔ FSM) ✅  
**Status:** Ready for SITL validation  

---

## Pre-Test Verification

### Environment Setup
```bash
cd /home/rasya/GUI-ROV
pip install pymavlink opencv-python pyzbar numpy
```

**Field laptop pre-flight (pool day):** pastikan `opencv-python`, `pyzbar`, dan
system lib `libzbar0` (`sudo apt install libzbar0` di Linux) sudah terpasang —
tanpa `libzbar0`, `pyzbar` gagal import saat startup, bukan saat decode QR.

### Quick Integration Check
```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state DIVE --no-wait-autonomous
```

**Expected first output (~5 seconds):**
```
[VEHICLE] [MOCK] mengirim sebagai vehicle...
[ROV_LINK] [MAV] terhubung: system=1 component=1
[FSM] ===== MISI ROV KKI 2026 DIMULAI (start=DIVE) =====
[FSM] DIVE depth=0.00 target=0.70
```

**Success:** All 3 processes start without error, FSM begins DIVE state.

---

## SITL Test Scenarios (Each ~3.5 min)

### Scenario A: Full Mission 1-5 (Baseline)

**Command:**
```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state DIVE --no-wait-autonomous
```

**Checklist** — dijalankan 2026-08-21, LULUS (log: 100/100, 54 dtk, 0 WARNING/ERROR):
- [x] FSM reaches DIVE → SCAN_QR → GRAB → NAV_WALL → HANG → SURFACE → DOCK
- [x] FSM transitions to M5_REDIVE → M5_DOCK → M5_ENGAGE → M5_UNHOOK → M5_ASCEND → DONE
- [x] Final output shows:
  ```
  [FSM] ===== SKOR AKHIR =====
  [FSM]  Misi 1 (Scan QR)     : 15/15
  [FSM]  Misi 2 (Grab Payload): 15/15
  [FSM]  Misi 3 (Hang Payload): 15/15
  [FSM]  Misi 4 (Surface Dock): 15/15
  [FSM]  Misi 5 (Auto Release): 40/40
  [FSM]  TOTAL               : 100/100
  ```
- [x] Total runtime < 5 minutes — **aktual 54 detik**

**Pass Criteria:** Skor total = 100, no crashes

---

### Scenario B: Mission 5 Autonomous Only

**Command:**
```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state M5_REDIVE --no-wait-autonomous
```

**Checklist** — dijalankan 2026-08-21, LULUS (40/40, 15 dtk, 0 WARNING/ERROR):
- [x] FSM starts at M5_REDIVE (simulates already docked at surface)
- [x] M5_DOCK → M5_ENGAGE → M5_UNHOOK → M5_ASCEND → DONE
- [x] Misi 5 skor = 40 (no partial credit in mock)
- [x] Runtime < 2 minutes — **aktual 15 detik**

**Pass Criteria:** Misi 5 complete, skor 40

---

### Scenario C: Docking Visual Servo Test

**Command:**
```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state M5_DOCK --no-wait-autonomous
```

**Checklist** — dijalankan 2026-08-21, LULUS:
- [x] FSM starts at M5_DOCK (already near QR payload)
- [x] Log shows servo steps (butuh `--loglevel DEBUG`, lihat catatan di bawah):
  ```
  [FSM] servo(PBVS) x=-0.02 y=0.05 z=0.31 → su=50 sw=15 vt=-10
  [FSM] servo(PBVS) x=-0.01 y=0.02 z=0.30 → su=45 sw=8 vt=-5
  ...
  [FSM] QR payload ALIGNED (PBVS) — engage gripper
  ```
- [x] Alignment converges smoothly (error decreases each iteration)
- [x] Transition to M5_ENGAGE occurs after alignment

Terverifikasi 2026-08-21. Baris `servo(...)` adalah `log.debug`, jadi TIDAK
muncul di level default — `launch_sitl.py` belum meneruskan `--loglevel`. Untuk
melihatnya, jalankan FSM langsung di atas stack manual (`README_SETUP_C.md`):

```bash
PYTHONPATH= python3 fsm/mission5.py --server 127.0.0.1 --telem-port 14552 \
    --vision mock --start-state M5_DOCK --no-wait-autonomous --loglevel DEBUG
```

Konvergensi terukur: x 0.15→0.00, y 0.10→0.00, z 0.72→0.30 (= `SERVO_TARGET_DIST`),
monoton turun, `ALIGNED` muncul tepat 1×.

**Pass Criteria:** Smooth servo convergence, alignment message appears

---

## Integration Layer Validation

### GUI Toggle (Manual ↔ Autonomous) + STOP

Terverifikasi otomatis 2026-08-21 — jalankan ulang kapan pun:

```bash
node autonomy/tools/verify_handoff.mjs      # VERBOSE=1 untuk log stack
```

Skrip menyalakan stack-nya sendiri (server.js RPI_ADDR=127.0.0.1 di WS :8090,
vehicle mock + rov_link, TANPA --fsm) lalu menguji tiga skenario lewat
WebSocket dengan envelope yang sama persis dengan dashboard:

- **A** `control_mode=autonomous` → FSM hidup dan MAJU (`IDLE → M5_REDIVE → M5_DOCK`)
- **B** `control_mode=manual` saat FSM jalan → FSM abort bersih
- **C** `stop` saat FSM jalan → disarm **dan** FSM ikut berhenti

**Jangan jalankan skrip ini sambil `node server.js` polos aktif dan berharap
port 8080.** RPI_ADDR default adalah `192.168.2.2` (ROV asli), jadi toggle akan
dikirim ke wahana di kolam sementara telemetry tetap terlihat normal — server
BIND :14551 dan menerima dari siapa pun. Perintah hilang tanpa jejak, gejalanya
menyesatkan. Skrip memakai port terpisah justru untuk menghindari ini.

**CATATAN, jangan tulis ulang ke bentuk lama:** telemetry TIDAK pernah berisi
`"mode": "autonomous"`. Ada dua field berbeda (lihat `rov_link.py loop_telem_tx`):

| field | isi | sumber |
|---|---|---|
| `mode` | `MANUAL` / `ALT_HOLD` / `STABILIZE` | HEARTBEAT ArduSub |
| `control_mode` | `manual` / `autonomous` | gate otoritas GUI |

Checklist versi lama menyuruh memeriksa `"mode": "autonomous"` — nilai yang tak
pernah ada. `mission5.py` juga membaca field yang salah sampai 2026-08-21.

#### Yang masih butuh mata (browser, tidak bisa headless)

Buka `http://localhost:8080` dengan stack Fase 1 jalan
(`RPI_ADDR=127.0.0.1 node server.js` + `python3 autonomy/tools/launch_sitl.py --no-gui --vision mock`):

- [ ] Toggle header Manual→Autonomous benar-benar mengirim `control_mode`
      (F12 → Network → WS, atau log server `[CMD] control_mode = autonomous`)
- [ ] Badge mode di header berubah, dan kembali saat di-toggle balik
- [ ] Tombol **STOP** merah: sekali klik → thruster netral, badge disarm,
      dan blok mission5 hilang dari panel telemetry
- [ ] ROV 3D bergerak sinkron dengan depth/heading/attitude — bukan diam,
      bukan melompat-lompat
- [ ] F12 console bersih (tak ada exception saat toggle bolak-balik 3×)
- [ ] Joystick fisik: dorong stik saat Autonomous → KILL-SWITCH menyala,
      FSM abort, mode balik ke Manual. Ambangnya `KILL_SWITCH_DEADZONE=15` pada
      skala axis **-1000..1000** (bukan -100..100) → menyala di ~20% defleksi
      stik fisik. Logikanya sudah dikunci `tests/test_rov_link.py`; yang butuh
      mata tinggal rangkaian stik→browser→server→rov_link.
      **Jangan setel deadzone joystick ke 0 di hari lomba** — yang menyaring
      drift stik adalah deadzone sisi-GUI (0.12), bukan ambang ini.

**Pass Criteria:** skrip otomatis 3/3 LULUS **dan** keenam butir browser tercentang.

---

## Mock Convergence Verification

### Visual Servo Behavior (PBVS Mode)

Mock payload QR converges: far & off-center → center & close

**Expected log pattern:**
```
[FSM] servo(PBVS) x=+0.18 y=+0.12 z=0.80 → su=+50 sw=+40 vt=+25  [1 sec: far]
[FSM] servo(PBVS) x=+0.12 y=+0.08 z=0.60 → su=+35 sw=+25 vt=+15  [2 sec]
[FSM] servo(PBVS) x=+0.05 y=+0.03 z=0.40 → su=+15 sw=+10 vt=+5   [3 sec]
[FSM] servo(PBVS) x=+0.01 y=+0.01 z=0.31 → su=+3  sw=+2  vt=+0   [converge]
[FSM] QR payload ALIGNED (PBVS) → engage gripper
```

**Checklist** — terverifikasi 2026-08-21 (lihat perintah `--loglevel DEBUG` di Scenario C):
- [x] Error magnitude decreases consistently (x/y/z → smaller)
- [x] Convergence takes 3-5 seconds — **aktual ~9 dtk** dari start M5_DOCK
      (lebih jauh dari asumsi tabel; bukan kegagalan, catat ulang saat tuning kolam)
- [x] Alignment message appears exactly once per docking attempt

---

## Failure Mode Testing

### Timeout Handling (Fallback to Degraded Mode)

~~**Setup:** Patch vision pipeline to simulate detection loss~~ — **USANG**,
tak perlu lagi mengedit `qr_detect.py`; dua pytest di bawah menapaki jalur ini
otomatis lewat `dropout=` milik `SimVision`.

**Expected Behavior:**
- M5_DOCK waits M5_LOCK_GRACE_T (0.6s) → sweeps for QR
- After TIMEOUT_M5_DOCK (25s) → transitions M5_FALLBACK (degraded timed mode)
- M5_FALLBACK executes dive/grab/unhook/ascend without visual feedback
- Skor reduced: misi 5 still scores (40 full or partial depending on phase)

**Checklist** — terverifikasi 2026-08-21 di SIMULATOR (bukan SITL), dikunci pytest:
- [x] FSM does NOT crash when vision fails
- [x] Log shows "degradasi ke fallback timed"
- [x] FSM completes DONE state (bukan ABORT), misi 5 tetap dapat nilai

Tak perlu lagi mem-*patch* `qr_detect.py` seperti resep di atas — dua test
menapaki jalur ini otomatis (`tests/test_mission5.py`):

| test | skenario |
|---|---|
| `test_m5_fallback_when_qr_lost_during_dock` | QR ter-lock lalu HILANG di tengah docking → `M5_DOCK` timeout → `M5_FALLBACK` → `DONE` |
| `test_m5_fallback_when_qr_never_acquired` | QR tak pernah terdeteksi → `M5_REDIVE` timeout → `M5_FALLBACK`, payload tetap lepas dari hook |

Keduanya sudah diuji-mutasi: mengganti transisi fallback jadi `ABORT` membuat
test gagal. **Drill fisik "tutup lensa kamera" tetap wajib di kolam** (Fase 4) —
yang terbukti di sini logikanya, bukan perilaku ROV di air.

---

## Performance Metrics

### Target Timings (Mock Mode)

| Stage | Target | Typical | Notes |
|-------|--------|---------|-------|
| DIVE | 15s | 8s | mock: no drag, instant descent |
| SCAN_QR | 20s | 2s | mock: QR always present |
| GRAB | 10s | 10s | timed phases: deterministic |
| NAV_WALL | 30s | 18s | 5s rotate + 18s timed nav |
| HANG | 15s | 12s | visual servo + mekanis |
| SURFACE | 15s | 8s | ascent (instant in mock) |
| DOCK | 15s | 12s | surface docking visual servo |
| M5_REDIVE | 15s | 8s | redive + reacquire QR |
| M5_DOCK | 25s | 12s | docking closed-loop |
| M5_ENGAGE | 12s | 12s | grab while holding x/y |
| M5_UNHOOK | 10s | 10s | mekanis: deterministic |
| M5_ASCEND | 20s | 8s | ascent with payload |
| **TOTAL** | **227s** | **130s** | ~2x speedup vs real water (no drag) |

**Pass Criteria:** SITL total < 4 minutes (mock compresses real physics)

---

## Memory & Stability

### Resource Check (During SITL)

```bash
# While SITL running, in another terminal:
ps aux | grep -E "sitl_mock|rov_link|mission5|launch_sitl"
```

**Checklist** — terukur 2026-08-21 saat SITL berjalan:
- [x] No process > 500 MB RAM — aktual: mock 42 MB, rov_link 72 MB, FSM 77 MB, launcher 17 MB
- [x] No 100% CPU sustained — aktual puncak 10,5% (rov_link)
- [x] No zombie processes after shutdown — tak ada proses stack tersisa

**Pass Criteria:** Clean resource usage, graceful shutdown

---

## Next: Hardware Bringup

Once SITL validates full mission logic:

### Bench Testing (Dry)
1. **Pixhawk + gripper:** Manual servo control, arm/disarm
2. **Depth sensor:** Read mock depth, tare surface
3. **Thruster:** Listen MANUAL_CONTROL from mission5 FSM, verify axis response

### Shallow Water (0.5m)
1. **Vision:** QR detected from kamera (switch --vision usb), mock hook detection
2. **Servo:** Visual servo converges to QR under water
3. **Gripper:** Seating & release mechanics

### Full Depth (0.9m)
1. **Depth targets:** DEPTH_TARGET_BOTTOM=0.70m actual depth
2. **Timing:** Real hydro delays, re-tune IBVS/PBVS gains
3. **Full mission:** Misi 1-5 with real physics

---

## Rollback Plan

If SITL reveals critical logic bugs:

1. **Revert rov_agent.py** (state["mode"] wiring) — single line, no impact
2. **Revert sitl_mock.py** — use prior version without fanout
3. **mission5.py logic** — fix directly (unchanged from pre-existing)

No data loss; git history preserved.

---

## Pool Trial Launch

Satu perintah menggantikan 3-4 terminal manual (vehicle/rov_link/GUI/FSM):

```bash
cp autonomy/config/mission5.example.yaml autonomy/config/mission5.local.yaml
# isi gain per fase kedalaman (bench/shallow/deep) di mission5.local.yaml
# (sudah gitignored, aman diisi nilai hasil tuning lokal)

python autonomy/tools/launch_sitl.py --vehicle none --mavlink /dev/ttyACM0 \
    --fsm --vision usb --calib vision/calibration/dwe.npz \
    --config autonomy/config/mission5.local.yaml \
    --log-file /tmp/pool_run1.log --no-gui
```

Setup pool saat ini **single-cam (legacy)** — cukup `--calib dwe.npz`, TIDAK
perlu kalibrasi dual-cam (`bottom.npz`/`wall.npz`). `--log-file` mengarsipkan
log run ke disk untuk perbandingan gain pasca-trial (run 1 vs run 2).

---

## Sign-Off Checklist

**SITL Validation** — LULUS 2026-08-21
- [x] Scenario A (Full 1-5) passes with skor=100
- [x] Scenario B (M5 only) passes with skor=40
- [x] Scenario C (Docking servo) converges smoothly
- [x] GUI toggle test: FSM waits, starts on "autonomous" (`verify_handoff.mjs` 3/3)
- [x] No memory leaks, clean shutdown
- [x] Jalur degradasi `M5_FALLBACK` tidak ABORT (pytest, simulator)
- [x] Unit test: 135 passed, 2 skipped (`PYTHONPATH= pytest tests/ -q`)

**Hardware Readiness**
- [ ] Pixhawk connected via USB/tether
- [ ] Gripper servo responds to PWM commands
- [ ] Depth sensor reads non-zero value underwater
- [ ] Thruster responds to MAVLink MANUAL_CONTROL

**Pool Readiness**
- [ ] QR payload printed & calibrated
- [ ] Hook PVC (¾" candy-cane) installed at 0.45m depth
- [ ] Camera calibration file (dwe.npz or bottom.npz) ready
- [ ] Team consensus on dive targets: 0.70m bottom, 0.05m surface

---

**Status:** Ready to execute SITL → Bench → Pool progression  
**Owner:** Rasya (autonomy integration)  
**Last Updated:** 2026-08-21  
**Next Review:** After first pool test
