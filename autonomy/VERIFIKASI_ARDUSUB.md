# Checklist VERIFIKASI nilai-fisik (ArduSub SITL / hardware)

Mock **hanya** menguji jalur data. Item berikut wajib dicek saat tersambung ke
**ArduSub SITL** atau **Pixhawk asli**. Tiap item: cara cek + konstanta di `rov_link.py`
yang diubah bila salah. (`set_surface` sudah selesai & terverifikasi — depth re-zero.)

| # | Item | Cara verifikasi | Bila salah → ubah di `rov_link.py` |
|---|------|-----------------|-------------------------------------|
| 1 | **Arah surge/sway/yaw** | ARM, mode MANUAL. Tekan W (surge+) → SITL/ROV maju; D → kanan; E → yaw CW. Bandingkan dgn ATTITUDE/console. | Balik tanda di `send_manual_control()` (mis. `x = -…`). |
| 2 | **z-neutral & arah vertikal** | Mode DEPTH HOLD. `vert=0` → diam menahan kedalaman; `vert<0` (F) → turun; `vert>0` (R) → naik. | `Z_NEUTRAL` (500) dan rumus `z = Z_NEUTRAL + vert*5`; balik tanda bila terbalik. |
| 3 | **Channel servo gripper** | Di QGroundControl → Servo Output, lihat `SERVOn_FUNCTION` utk gripper. Kirim `gripper close/open`, pastikan output bergerak. | `GRIPPER_SERVO_CH` (skrg 10), `GRIPPER_PWM_OPEN/CLOSE` (1900/1100). |
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
