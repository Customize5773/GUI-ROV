#!/usr/bin/env python3
"""
sitl_mock.py — MOCK vehicle MAVLink ringan untuk menguji rov_link.py TANPA WSL/SITL.

Berperan seolah-olah ArduSub: mengirim HEARTBEAT/ATTITUDE/SCALED_PRESSURE2/SYS_STATUS,
dan MENG-INTEGRASIKAN MANUAL_CONTROL yang diterima menjadi gerak palsu (heading & depth
berubah), sehingga 3D di GUI ikut bergerak saat kamu menekan kontrol.

Mode mission5: bila --telem-extra diberikan, kirim state ke mission5 FSM di port terpisah.

Jalankan (standalone):
    python sitl_mock.py --mavlink udpout:127.0.0.1:14555

Jalankan (dengan mission5 FSM):
    python sitl_mock.py --mavlink udpout:127.0.0.1:14555 --telem-extra 127.0.0.1:14552
"""

import argparse
import json
import math
import socket
import threading
import time

from pymavlink import mavutil

WATER_RHO = 997.0
G = 9.80665
SURFACE_HPA = 1013.25


class TelemetryFanout:
    """Kirim telemetri ke multiple destinations (GUI + FSM autonomous)."""
    def __init__(self, extra_dests=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.dests = extra_dests or []

    def send(self, telem_dict):
        if not self.dests:
            return
        payload = json.dumps(telem_dict).encode()
        for host, port in self.dests:
            try:
                self.sock.sendto(payload, (host, port))
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mavlink", default="udpout:127.0.0.1:14555")
    ap.add_argument("--telem-extra", default="",
                    help="host:port tambahan untuk telemetri mission5 FSM (mis. 127.0.0.1:14552)")
    args = ap.parse_args()

    m = mavutil.mavlink_connection(args.mavlink, source_system=1, source_component=1)
    print(f"[MOCK] mengirim sebagai vehicle ke {args.mavlink}")

    # Parse extra telemetry destination
    extra_dests = []
    if args.telem_extra:
        for item in args.telem_extra.split(","):
            item = item.strip()
            if item:
                host, port = item.rsplit(":", 1)
                extra_dests.append((host, int(port)))
                print(f"[MOCK] telemetri extra ke {host}:{port}")
    fanout = TelemetryFanout(extra_dests)

    # Windows: socket udpout baru bisa recv setelah paket pertama dikirim.
    # Kirim heartbeat awal agar socket tersambung (hindari WinError 10022).
    m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_SUBMARINE,
                         mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                         mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, 0,
                         mavutil.mavlink.MAV_STATE_ACTIVE)

    armed = False
    custom_mode = 0          # 0 = MANUAL (ArduSub)
    heading = 90.0           # deg
    depth = 0.0              # m
    roll = pitch = 0.0       # deg
    t0 = time.time()
    last = {"hb": 0, "att": 0, "press": 0, "sys": 0, "telem": 0}

    while True:
        now = time.time()

        # ── terima perintah dari rov_link ──
        while True:
            try:
                msg = m.recv_match(blocking=False)
            except OSError:
                # Windows: belum ada pengirim di sisi lain (10022/10054) — abaikan
                msg = None
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

        # ── kirim telemetri ke mission5 FSM (dari rov_link.py via fan-out) ──
        if now - last["telem"] >= 0.1:
            telem = {
                'heading': heading,
                'roll': roll,
                'pitch': pitch,
                'depth': depth,
                'temp': 26.5,
                'voltage': 15.6,
                'armed': armed,
                'light': False,
                'mode': 'manual',  # akan di-override oleh rov_link saat toggle autonomous
            }
            fanout.send(telem)
            last["telem"] = now

        # ── kirim telemetri MAVLink ──
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
