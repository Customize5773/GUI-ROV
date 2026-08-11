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

**Checklist:**
- [ ] FSM reaches DIVE → SCAN_QR → GRAB → NAV_WALL → HANG → SURFACE → DOCK
- [ ] FSM transitions to M5_REDIVE → M5_DOCK → M5_ENGAGE → M5_UNHOOK → M5_ASCEND → DONE
- [ ] Final output shows:
  ```
  [FSM] ===== SKOR AKHIR =====
  [FSM]  Misi 1 (Scan QR)     : 15/15
  [FSM]  Misi 2 (Grab Payload): 15/15
  [FSM]  Misi 3 (Hang Payload): 15/15
  [FSM]  Misi 4 (Surface Dock): 15/15
  [FSM]  Misi 5 (Auto Release): 40/40
  [FSM]  TOTAL               : 100/100
  ```
- [ ] Total runtime < 5 minutes (mock converge time + FSM overhead)

**Pass Criteria:** Skor total = 100, no crashes

---

### Scenario B: Mission 5 Autonomous Only

**Command:**
```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state M5_REDIVE --no-wait-autonomous
```

**Checklist:**
- [ ] FSM starts at M5_REDIVE (simulates already docked at surface)
- [ ] M5_DOCK → M5_ENGAGE → M5_UNHOOK → M5_ASCEND → DONE
- [ ] Misi 5 skor = 40 (no partial credit in mock)
- [ ] Runtime < 2 minutes

**Pass Criteria:** Misi 5 complete, skor 40

---

### Scenario C: Docking Visual Servo Test

**Command:**
```bash
python autonomy/tools/launch_sitl.py --fsm --vision mock --start-state M5_DOCK --no-wait-autonomous
```

**Checklist:**
- [ ] FSM starts at M5_DOCK (already near QR payload)
- [ ] Log shows servo steps:
  ```
  [FSM] servo(PBVS) x=-0.02 y=0.05 z=0.31 → su=50 sw=15 vt=-10
  [FSM] servo(PBVS) x=-0.01 y=0.02 z=0.30 → su=45 sw=8 vt=-5
  ...
  [FSM] QR payload ALIGNED (PBVS) — engage gripper
  ```
- [ ] Alignment converges smoothly (error decreases each iteration)
- [ ] Transition to M5_ENGAGE occurs after alignment

**Pass Criteria:** Smooth servo convergence, alignment message appears

---

## Integration Layer Validation

### GUI Toggle (Manual ↔ Autonomous)

**Setup:** 2 terminals

```bash
# Terminal 1: SITL without FSM (wait for GUI)
python autonomy/tools/launch_sitl.py --vision mock

# Terminal 2: Browser or curl test
# Navigate to localhost:3000, or:
# Send command: {"name":"control_mode","value":"autonomous"}
```

**Checklist:**
- [ ] FSM starts ONLY after toggle → "autonomous" (not before)
- [ ] Telemetry includes `"mode": "autonomous"` (in Terminal 1 logs)
- [ ] Operator can toggle back to "manual" → FSM stops gracefully

**Pass Criteria:** FSM waits for toggle, starts on "autonomous" signal

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

**Checklist:**
- [ ] Error magnitude decreases consistently (x/y/z → smaller)
- [ ] Convergence takes 3-5 seconds (mock design)
- [ ] Alignment message appears exactly once per docking attempt

---

## Failure Mode Testing

### Timeout Handling (Fallback to Degraded Mode)

**Setup:** Patch vision pipeline to simulate detection loss

```python
# In qr_detect.py _run_mock(), after line 410:
# return None  # Force QR loss
```

**Expected Behavior:**
- M5_DOCK waits M5_LOCK_GRACE_T (0.6s) → sweeps for QR
- After TIMEOUT_M5_DOCK (25s) → transitions M5_FALLBACK (degraded timed mode)
- M5_FALLBACK executes dive/grab/unhook/ascend without visual feedback
- Skor reduced: misi 5 still scores (40 full or partial depending on phase)

**Checklist:**
- [ ] FSM does NOT crash when vision fails
- [ ] Log shows "degradasi ke fallback timed"
- [ ] FSM completes DONE state

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

**Checklist:**
- [ ] No process > 500 MB RAM (normal: 50-200 MB each)
- [ ] No 100% CPU sustained (spikes OK, not hung)
- [ ] No zombie processes after Ctrl+C shutdown

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

## Sign-Off Checklist

**SITL Validation (This Session)**
- [ ] Scenario A (Full 1-5) passes with skor=100
- [ ] Scenario B (M5 only) passes with skor=40
- [ ] Scenario C (Docking servo) converges smoothly
- [ ] GUI toggle test: FSM waits, starts on "autonomous"
- [ ] No memory leaks, clean shutdown

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
**Last Updated:** 2026-08-11  
**Next Review:** After first pool test
