#!/usr/bin/env python3
"""
sitl_mock.py — MOCK vehicle MAVLink ringan untuk menguji rov_link.py TANPA WSL/SITL.

Berperan seolah-olah ArduSub: mengirim HEARTBEAT/ATTITUDE/SCALED_PRESSURE2/SYS_STATUS,
dan MENG-INTEGRASIKAN MANUAL_CONTROL yang diterima menjadi gerak palsu (heading & depth
berubah), sehingga 3D di GUI ikut bergerak saat kamu menekan kontrol. Cukup `pip install pymavlink`.

Jalankan:
    python sitl_mock.py --mavlink udpout:127.0.0.1:14555
(rov_link.py harus `--mavlink udpin:0.0.0.0:14555`)
"""

import argparse
import math
import time

from pymavlink import mavutil

WATER_RHO = 997.0
G = 9.80665
SURFACE_HPA = 1013.25


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mavlink", default="udpout:127.0.0.1:14555")
    args = ap.parse_args()

    m = mavutil.mavlink_connection(args.mavlink, source_system=1, source_component=1)
    print(f"[MOCK] mengirim sebagai vehicle ke {args.mavlink}")

    armed = False
    custom_mode = 0          # 0 = MANUAL (ArduSub)
    heading = 90.0           # deg
    depth = 0.0              # m
    roll = pitch = 0.0       # deg
    t0 = time.time()
    last = {"hb": 0, "att": 0, "press": 0, "sys": 0}

    while True:
        now = time.time()

        # ── terima perintah dari rov_link ──
        while True:
            msg = m.recv_match(blocking=False)
            if msg is None:
                break
            t = msg.get_type()
            if t == "MANUAL_CONTROL":
                # r: yaw -1000..1000 → laju putar; z: 500 netral, >500 naik
                heading = (heading + (msg.r / 1000.0) * 90.0 * 0.05 + 360) % 360
                depth = max(0.0, depth - ((msg.z - 500) / 500.0) * 0.6 * 0.05)
                roll = (msg.y / 1000.0) * 12.0
                pitch = (msg.x / 1000.0) * 8.0
            elif t == "COMMAND_LONG":
                if msg.command == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
                    armed = (msg.param1 == 1)
                    print(f"[MOCK] {'ARMED' if armed else 'DISARMED'}")
                elif msg.command == mavutil.mavlink.MAV_CMD_DO_SET_SERVO:
                    print(f"[MOCK] DO_SET_SERVO ch={int(msg.param1)} pwm={int(msg.param2)}")
            elif t in ("SET_MODE", "COMMAND_INT"):
                pass

        # ── kirim telemetri ──
        if now - last["hb"] >= 1.0:
            base = mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
            if armed:
                base |= mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
            m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_SUBMARINE,
                                 mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                                 base, custom_mode, mavutil.mavlink.MAV_STATE_ACTIVE)
            last["hb"] = now
        if now - last["att"] >= 0.1:
            ms = int((now - t0) * 1000)
            m.mav.attitude_send(ms, math.radians(roll), math.radians(pitch),
                                math.radians(heading), 0, 0, 0)
            last["att"] = now
        if now - last["press"] >= 0.2:
            press = SURFACE_HPA + depth * WATER_RHO * G / 100.0
            m.mav.scaled_pressure2_send(int((now - t0) * 1000), press, 0.0, 2650)  # temp 26.5°C
            last["press"] = now
        if now - last["sys"] >= 0.5:
            m.mav.sys_status_send(0, 0, 0, 0, 15600, -1, -1, 0, 0, 0, 0, 0, 0)  # 15.6 V
            last["sys"] = now

        time.sleep(0.02)


if __name__ == "__main__":
    main()
