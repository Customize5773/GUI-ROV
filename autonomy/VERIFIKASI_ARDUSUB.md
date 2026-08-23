# Checklist VERIFIKASI nilai-fisik (ArduSub SITL / hardware)

Mock **hanya** menguji jalur data. Item berikut wajib dicek saat tersambung ke
**ArduSub SITL** atau **Pixhawk asli**. Tiap item: cara cek + konstanta di `rov_link.py`
yang diubah bila salah. (`set_surface` sudah selesai & terverifikasi — depth re-zero.)

| # | Item | Cara verifikasi | Bila salah → ubah di `rov_link.py` |
|---|------|-----------------|-------------------------------------|
| 1 | **Arah surge/sway/yaw** | ARM, mode MANUAL. Tekan W (surge+) → SITL/ROV maju; D → kanan; E → yaw CW. Bandingkan dgn ATTITUDE/console. | Balik tanda di `send_manual_control()` (mis. `x = -…`). |
| 2 | **z-neutral & arah vertikal** | Mode DEPTH HOLD. `vert=0` → diam menahan kedalaman; `vert<0` (F) → turun; `vert>0` (R) → naik. | `Z_NEUTRAL` (500) dan rumus `z = Z_NEUTRAL + vert*5`; balik tanda bila terbalik. |
| 3 | **Channel servo gripper** | Di QGroundControl → Servo Output, lihat `SERVOn_FUNCTION` utk gripper. Kirim `gripper close/open`, pastikan output bergerak. | `GRIPPER_SERVO_CH` (skrg 10), `GRIPPER_PWM_OPEN/CLOSE` (1580/1350, sudah sinkron dgn `gripper_controller.py`). |
| 4 | **Channel servo lampu** | Sama, untuk channel lampu. | `LIGHT_SERVO_CH` (9), `LIGHT_PWM_ON/OFF`. |
| 5 | **Nama mode depth-hold** | `print(master.mode_mapping())` — pastikan ada `'ALT_HOLD'` (= Depth Hold di ArduSub). | string di `set_mode("ALT_HOLD")` pada handler `control_mode`. |
| 6 | **Sumber & skala depth** | Bandingkan `depth` telemetri dgn kedalaman nyata/SITL. Cek `SCALED_PRESSURE2` ada; bila depth 0 terus, fallback `GLOBAL_POSITION_INT`. | `WATER_RHO` (997 tawar / 1025 laut); handler `SCALED_PRESSURE2`/`GLOBAL_POSITION_INT`. |
| 7 | **Arming & failsafe** | ARM via GUI → SITL armed. STOP → disarm + thruster netral seketika. | handler `arm` / `stop`. |

## Cara cepat dump mode_mapping (tanpa SITL penuh)
```python
from pymavlink import mavutil
m = mavutil.mavlink_connection("udpin:0.0.0.0:14555"); m.wait_heartbeat()
print(m.mode_mapping())
```

## Status item 3
- [x] `set_surface` (selesai, terverifikasi di mock: 0.6 m → 0.0 m)
- [ ] #1–#7 di atas — **butuh SITL/hardware** (WSL belum terpasang di mesin ini).

Saat ArduSub SITL siap, jalankan checklist ini lalu sesuaikan konstanta. Setelah
semua ✓, point (c) lengkap "nilai-fisik".

---

# Checklist VERIFIKASI Misi 5 — Docking QR & Lepas Payload (di KOLAM)

Konstanta di `fsm/mission5.py` (blok "Misi 5"). Servo di `control/visual_servo.py`.
Uji BERTAHAP, dari sederhana → kompleks. **Selalu siapkan STOP/Spasi** (failsafe) tiap uji.

| # | Item | Cara verifikasi | Bila salah → ubah |
|---|------|-----------------|-------------------|
| M1 | **Arah sumbu servo** (paling kritis) | Mode M5_DOCK, taruh QR agak KANAN frame → ROV harus geser mendekat & error MENGECIL. Ulangi utk atas/bawah (vert) & jauh/dekat (surge). | `SERVO_INVERT` di `mission5.py` — balik flag axis yang errornya MEMBESAR. |
| M2 | **Jarak engage** | Amati jarak ROV↔payload saat status ALIGNED. Gripper harus tepat menjangkau lubang/badan payload. | `SERVO_TARGET_DIST` (PBVS, m) / `SERVO_TARGET_AREA` (IBVS, px²). |
| M3 | **Ukuran QR & PBVS** | `--calib <npz>` + `--qr-size 0.04`. Cek pose x,y,z log wajar (z≈jarak nyata). QR 4 cm terbaca sampai jarak engage? | Kalau QR 4 cm tak terbaca di air keruh → pakai IBVS (tanpa `--calib`) atau fiducial lebih besar. |
| M4 | **Kedalaman hook** | M5_REDIVE berhenti selam di depth payload (tip hook 0.45 m dari dasar; kolam 0.9 m ⇒ ~0.45 m dari permukaan). | `HOOK_DEPTH` (m dari permukaan). |
| M5 | **Gerak lepas-hook (UNHOOK)** | Setelah grab: fase ANGKAT lalu TARIK harus melepas lubang payload dari candy-cane, bukan menyangkut. | `M5_UNHOOK_VERT` (angkat), `M5_UNHOOK_SURGE` (tarik, negatif=mundur), `UNHOOK_LIFT_T`, `UNHOOK_PULL_T`. |
| M6 | **Merayap grab (ENGAGE)** | Payload masuk gripper mulus tanpa mendorong lepas dari hook sebelum tercengkeram. | `M5_ENGAGE_SURGE` + timing fase di `_state_m5_engage`. |
| M7 | **Yaw squaring (opsional)** | Default `SERVO_KP_YAW=0.0` (NONAKTIF). Yaw dari 1 QR planar AMBIGU (dua solusi IPPE) → JANGAN aktifkan sebelum diverifikasi solid; bila perlu tegak-lurus, andalkan heading-hold ArduSub. Validasi pasif (tanpa kirim command) tersedia: `python -m autonomy.tests.pool_yaw_validation --calib kalib.npz --qr-size 0.04 --device 0 --duration 30`. | `SERVO_KP_YAW` — biarkan 0 kecuali sudah terbukti stabil. |
| M8 | **Handoff GUI → autonomous** | 1-4 manual, lalu toggle header → AUTONOMOUS. FSM (sudah jalan, `--start-state M5_REDIVE`) harus mulai. Toggle balik ke MANUAL → FSM abort. | pastikan telem `mode` mengalir (rov_link `loop_telem_tx`). |
| M9 | **Pencarian lateral M5_SEARCH** (kembali ke gantungan) | **(a)** Kalibrasi `SEARCH_SPEED`→m/s: surge 20% selama 10 s, ukur jarak — SEMUA konstanta `search.*` lain diskalakan dari angka ini. **(b)** Deviasi kompas: MARK di gantungan → naik → turun lagi → bandingkan heading; geser >15° = ladder mencari ke arah salah. **(c)** Rasio jarak "quad QR terlihat" vs "QR terbaca" — menentukan `SEARCH_BACKOFF_T`. **(d)** Lebar kolam: back-off ~1,2 m jangan sampai menabrak dinding seberang. | blok `search:` di config (`SEARCH_SPEED`, `SEARCH_BACKOFF_T`, `SEARCH_LEG_*`, `SEARCH_SPAN_MAX_T`, `SEARCH_CREEP_MAX_T`). |

## Alur uji lomba (rekomendasi)
```
# di Raspberry Pi, SEBELUM run:
python fsm/mission5.py --server 127.0.0.1 --vision usb --device 0 \
    --calib kalib.npz --qr-size 0.04 --start-state M5_REDIVE
# → FSM menunggu. Operator kemudikan misi 1-4 via GUI (MANUAL).
# → Setelah docking permukaan, tekan toggle header GUI: MANUAL → AUTONOMOUS.
# → FSM otomatis jalankan misi 5 (selam ulang → dock QR → grab → lepas → naik).
```

## Status
- [x] Jalur data + rantai state M5 (mock+SITL: M5_REDIVE→…→DONE, m5=40, PBVS & IBVS)
- [x] Handoff mode=autonomous (uji: FSM menunggu lalu jalan saat toggle)
- [ ] M1–M9 di atas — **butuh kolam/hardware** (arah sumbu, jarak, geometri unhook,
      kalibrasi kecepatan & kompas untuk pencarian lateral)
