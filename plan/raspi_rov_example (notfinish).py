#!/usr/bin/env python3
"""
raspi_rov_example.py — Contoh sisi RASPI (ROV).

Menunjukkan format UDP yang dipakai dashboard:
  - KIRIM telemetri (JSON) ke server Node.js  -> port 14551
  - TERIMA command (JSON) dari server Node.js <- port 14550

Ganti nilai dummy dengan data nyata dari sensor/Pixhawk Anda (mis. via pymavlink:
heading, roll, pitch dari ATTITUDE; depth dari pressure; voltage dari SYS_STATUS).

  python3 raspi_rov_example.py --server 192.168.2.1
"""

import argparse
import json
import socket
import threading
import time
import math

ap = argparse.ArgumentParser()
ap.add_argument("--server", default="192.168.2.1", help="IP komputer yang menjalankan Node.js")
ap.add_argument("--tx-port", type=int, default=14551, help="port telemetri di server")
ap.add_argument("--rx-port", type=int, default=14550, help="port command di Raspi")
args = ap.parse_args()

# penerima command 
def command_listener():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("0.0.0.0", args.rx_port))
    print(f"[RX] menunggu command di :{args.rx_port}")
    while True:
        data, _ = rx.recvfrom(1024)
        try:
            cmd = json.loads(data.decode())
        except ValueError:
            continue
        handle_command(cmd.get("name"), cmd.get("value"))

def handle_command(name, value):
    print(f"[CMD] {name} = {value}")
    if name == "stop":
        # WAJIB: netralkan semua thruster segera (failsafe)
        pass
    elif name == "arm":
        pass      # arm / disarm Pixhawk
    elif name == "light":
        pass      # nyalakan/matikan lampu
    elif name == "record":
        pass
    elif name == "snapshot":
        pass

threading.Thread(target=command_listener, daemon=True).start()

# ----------------------------- pengirim telemetri -----------------------------
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
print(f"[TX] mengirim telemetri ke {args.server}:{args.tx_port}")

t = 0.0
while True:
    t += 0.1
    telemetry = {
        "heading": round((90 + 30 * math.sin(t * 0.2)) % 360, 1),  # dari kompas/IMU
        "roll":    round(8 * math.sin(t * 0.6), 1),                # dari IMU
        "pitch":   round(5 * math.sin(t * 0.4), 1),                # dari IMU
        "depth":   round(2.0 + 1.0 * math.sin(t * 0.15), 2),       # dari pressure sensor
        "temp":    round(26.0 + math.sin(t * 0.05), 1),
        "voltage": round(15.6, 1),
        "armed":   False,
        "light":   False,
        "ts":      time.time(),
    }
    tx.sendto(json.dumps(telemetry).encode(), (args.server, args.tx_port))
    time.sleep(0.1)   # 10 Hz
