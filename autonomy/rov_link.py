#!/usr/bin/env python3
"""
rov_link.py — Jembatan sisi-ROV: protokol JSON/UDP GUI HYDROSHIP  <->  MAVLink (ArduSub).

Ini adalah upgrade nyata dari `raspi_rov_example.py`. Di produksi ia jalan di
Raspberry Pi (terhubung ke Pixhawk/ArduSub). Saat pengembangan, ia jalan di laptop
dan menyambung ke MOCK atau ArduSub SITL.

Topologi (tiga port UDP, TIDAK boleh bentrok):

    GUI browser ──WS:8080── server.js ──cmd JSON :14550──►  rov_link  ──MANUAL_CONTROL──►  ArduSub
                                       ◄──telem JSON :14551──         ◄──ATTITUDE/PRESSURE──   (SITL/mock/HW)
                                                                MAVLink di port terpisah :14555

  - server.js mengirim command JSON ke  :14550   → rov_link DENGARKAN di sini.
  - rov_link mengirim telemetri JSON ke  server:14551.
  - MAVLink ke vehicle lewat port lain (default udpin :14555) agar tidak bentrok 14550/14551.

Jalankan (uji dengan mock):
    python rov_link.py --server 127.0.0.1 --mavlink udpin:0.0.0.0:14555
Jalankan (ArduSub SITL):
    # sim_vehicle.py -v ArduSub --out=udpout:127.0.0.1:14555
    python rov_link.py --server 127.0.0.1 --mavlink udpin:0.0.0.0:14555
Jalankan (di Raspberry Pi, Pixhawk via USB):
    python rov_link.py --server 192.168.2.1 --mavlink /dev/ttyACM0 --baud 115200

Kontrak JSON (sesuai server.js + README-WORK §3):
  Command masuk  : {"name": "...", "value": ..., "t": ...}
  Telemetri keluar: {heading, roll, pitch, depth, temp, voltage, armed, light, mode, ts}
"""

import argparse
import json
import math
import socket
import threading
import time

from pymavlink import mavutil

# ───────────────────────── Konfigurasi yang perlu DIVERIFIKASI ke setup ArduSub kalian ──
WATER_RHO = 997.0          # kg/m³ air tawar (kolam). Air laut ≈ 1025.
G = 9.80665
SURFACE_HPA_DEFAULT = 1013.25

# Channel servo (SERVOn_FUNCTION di ArduSub). VERIFIKASI dgn QGroundControl.
LIGHT_SERVO_CH = 9         # contoh — sesuaikan
GRIPPER_SERVO_CH = 10      # contoh — sesuaikan
GRIPPER_PWM_OPEN = 1900
GRIPPER_PWM_CLOSE = 1100
LIGHT_PWM_ON = 1900
LIGHT_PWM_OFF = 1100

# MANUAL_CONTROL: sumbu vertikal (z). Di ArduSub umumnya 0..1000 dgn 500 = netral.
# Surge/sway/yaw: -1000..1000 dgn 0 = netral. VERIFIKASI arah/tanda saat uji SITL.
Z_NEUTRAL = 500


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class RovLink:
    def __init__(self, args):
        self.args = args
        # setpoint manual dari GUI (-100..100)
        self.sp = {"surge": 0.0, "sway": 0.0, "yaw": 0.0, "vert": 0.0}
        self.light_on = False
        self.control_mode = "manual"
        self.surface_hpa = SURFACE_HPA_DEFAULT
        self.lock = threading.Lock()

        # telemetri terbaru hasil parsing MAVLink
        self.telem = {
            "heading": None, "roll": None, "pitch": None, "depth": None,
            "temp": None, "voltage": None, "armed": False,
            "light": False, "mode": "manual",
        }

        # MAVLink
        print(f"[MAV] connecting: {args.mavlink}")
        if args.mavlink.startswith(("udp", "tcp")):
            self.master = mavutil.mavlink_connection(args.mavlink, source_system=255, source_component=190)
        else:
            self.master = mavutil.mavlink_connection(args.mavlink, baud=args.baud, source_system=255, source_component=190)
        print("[MAV] menunggu heartbeat dari vehicle…")
        self.master.wait_heartbeat()
        print(f"[MAV] terhubung: system={self.master.target_system} component={self.master.target_component}")
        self._request_streams()

        # UDP sockets ke server.js
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # telemetri keluar
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # command masuk
        self.rx.bind(("0.0.0.0", args.json_rx_port))

    # ───────────────────────── MAVLink helpers ─────────────────────────
    def _request_streams(self):
        self.master.mav.request_data_stream_send(
            self.master.target_system, self.master.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)  # 10 Hz

    def arm(self, on):
        self.master.mav.command_long_send(
            self.master.target_system, self.master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
            1 if on else 0, 0, 0, 0, 0, 0, 0)
        print(f"[CMD] {'ARM' if on else 'DISARM'}")

    def set_servo(self, ch, pwm):
        self.master.mav.command_long_send(
            self.master.target_system, self.master.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0,
            ch, pwm, 0, 0, 0, 0, 0)

    def set_mode(self, ardusub_mode):
        """ardusub_mode mis. 'MANUAL', 'STABILIZE', 'ALT_HOLD' (=Depth Hold)."""
        mapping = self.master.mode_mapping() or {}
        if ardusub_mode not in mapping:
            print(f"[MODE] '{ardusub_mode}' tidak ada di mode_mapping {list(mapping)} — dilewati")
            return
        self.master.set_mode(mapping[ardusub_mode])
        print(f"[MODE] -> {ardusub_mode}")

    def send_manual_control(self):
        with self.lock:
            s = dict(self.sp)
        x = int(clamp(s["surge"] * 10, -1000, 1000))   # maju/mundur
        y = int(clamp(s["sway"] * 10, -1000, 1000))    # samping
        r = int(clamp(s["yaw"] * 10, -1000, 1000))     # putar (yaw)
        z = int(clamp(Z_NEUTRAL + s["vert"] * 5, 0, 1000))  # vertikal, 500 netral
        self.master.mav.manual_control_send(self.master.target_system, x, y, z, r, 0)

    def send_gcs_heartbeat(self):
        self.master.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)

    # ───────────────────────── Command dari GUI ─────────────────────────
    def handle_command(self, name, value):
        if name in self.sp:                      # surge/sway/yaw/vert
            with self.lock:
                self.sp[name] = float(value)
            return
        if name == "stop":                       # FAILSAFE
            with self.lock:
                for k in self.sp:
                    self.sp[k] = 0.0
            self.send_manual_control()
            self.arm(False)
            print("[CMD] STOP — netral + disarm")
        elif name == "arm":
            self.arm(bool(value))
        elif name == "light":
            self.light_on = bool(value)
            self.set_servo(LIGHT_SERVO_CH, LIGHT_PWM_ON if self.light_on else LIGHT_PWM_OFF)
        elif name == "gripper":                  # "open"/"close" atau true(=close)/false(=open)
            close = (value == "close") or (value is True)
            self.set_servo(GRIPPER_SERVO_CH, GRIPPER_PWM_CLOSE if close else GRIPPER_PWM_OPEN)
            print(f"[CMD] gripper {'CLOSE' if close else 'OPEN'}")
        elif name == "control_mode":
            self.control_mode = str(value)
            self.set_mode("ALT_HOLD" if self.control_mode == "autonomous" else "MANUAL")
            if self.control_mode == "autonomous":
                with self.lock:
                    for k in self.sp:
                        self.sp[k] = 0.0         # hold; FSM autonomy akan ambil alih nanti
        elif name == "set_surface":
            # tangkap tekanan saat ini sebagai depth=0 (butuh telemetri pressure terbaru)
            print("[CMD] set_surface (lihat catatan: kalibrasi surface dari pressure terbaru)")
        else:
            # mode/controller/thruster_config/pid/pool_depth/viewer_access → urusan GUI
            print(f"[CMD] (diabaikan di link) {name} = {value}")

    # ───────────────────────── Loop-loop ─────────────────────────
    def loop_rx_json(self):
        print(f"[JSON] dengar command di :{self.args.json_rx_port}")
        while True:
            data, _ = self.rx.recvfrom(2048)
            try:
                msg = json.loads(data.decode())
            except ValueError:
                continue
            self.handle_command(msg.get("name"), msg.get("value"))

    def loop_mavlink_rx(self):
        while True:
            msg = self.master.recv_match(blocking=True, timeout=1)
            if msg is None:
                continue
            t = msg.get_type()
            if t == "ATTITUDE":
                self.telem["roll"] = round(math.degrees(msg.roll), 1)
                self.telem["pitch"] = round(math.degrees(msg.pitch), 1)
                self.telem["heading"] = round((math.degrees(msg.yaw) + 360) % 360, 1)
            elif t == "SCALED_PRESSURE2":
                depth = (msg.press_abs - self.surface_hpa) * 100.0 / (WATER_RHO * G)
                self.telem["depth"] = round(max(0.0, depth), 2)
                self.telem["temp"] = round(msg.temperature / 100.0, 1)
            elif t == "GLOBAL_POSITION_INT" and self.telem["depth"] is None:
                self.telem["depth"] = round(max(0.0, -msg.relative_alt / 1000.0), 2)
            elif t == "SYS_STATUS":
                if msg.voltage_battery not in (0, 65535):
                    self.telem["voltage"] = round(msg.voltage_battery / 1000.0, 1)
            elif t == "HEARTBEAT":
                self.telem["armed"] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

    def loop_manual_tx(self):
        while True:
            self.send_manual_control()
            time.sleep(0.05)   # 20 Hz

    def loop_telem_tx(self):
        while True:
            with self.lock:
                self.telem["light"] = self.light_on
                self.telem["mode"] = self.control_mode
            out = dict(self.telem)
            out["ts"] = time.time()
            self.tx.sendto(json.dumps(out).encode(), (self.args.server, self.args.telem_port))
            time.sleep(0.1)    # 10 Hz

    def loop_gcs_hb(self):
        while True:
            self.send_gcs_heartbeat()
            time.sleep(1.0)

    def run(self):
        for fn in (self.loop_rx_json, self.loop_mavlink_rx, self.loop_manual_tx,
                   self.loop_telem_tx, self.loop_gcs_hb):
            threading.Thread(target=fn, daemon=True).start()
        print("[OK] rov_link berjalan. Ctrl+C untuk berhenti.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[EXIT] berhenti.")


def main():
    ap = argparse.ArgumentParser(description="Jembatan JSON/UDP GUI <-> MAVLink ArduSub")
    ap.add_argument("--server", default="127.0.0.1", help="IP komputer yang menjalankan server.js (telemetri dikirim ke sini)")
    ap.add_argument("--telem-port", type=int, default=14551, help="port telemetri di server.js")
    ap.add_argument("--json-rx-port", type=int, default=14550, help="port command JSON dari server.js")
    ap.add_argument("--mavlink", default="udpin:0.0.0.0:14555", help="endpoint MAVLink ke vehicle/SITL/mock")
    ap.add_argument("--baud", type=int, default=115200, help="baud (jika serial, mis. /dev/ttyACM0)")
    args = ap.parse_args()
    RovLink(args).run()


if __name__ == "__main__":
    main()
