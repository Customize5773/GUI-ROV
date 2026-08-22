"""Ambil (dump) seluruh parameter ArduSub dari Pixhawk ke file .param lokal.

Param ArduSub hidup di Pixhawk, tak ter-version-control di repo ini. Skrip
ini yang mengisi gap itu: kirim PARAM_REQUEST_LIST, kumpulkan semua
PARAM_VALUE yang balik, tulis ke file format QGC/Mission Planner
(`NAMA,nilai` per baris) supaya bisa didiff lintas waktu.

Pakai: python3 rov_param_dump.py [output.param]
"""
import os
import sys
import time

from pymavlink import mavutil

PIXHAWK_PORT = os.environ.get("PIXHAWK_PORT", "/dev/ttyACM0")
PIXHAWK_BAUD = int(os.environ.get("PIXHAWK_BAUD", "115200"))
TIMEOUT_S = 30


def dump_params(output_path):
    master = mavutil.mavlink_connection(PIXHAWK_PORT, baud=PIXHAWK_BAUD)
    master.wait_heartbeat(timeout=30)
    master.mav.param_request_list_send(master.target_system, master.target_component)

    params = {}
    expected = None
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        msg = master.recv_match(type="PARAM_VALUE", blocking=True, timeout=2)
        if msg is None:
            continue
        expected = msg.param_count
        params[msg.param_id] = msg.param_value
        if len(params) % 50 == 0:
            print(f"  {len(params)}/{expected} param diterima...")
        if expected and len(params) >= expected:
            break

    with open(output_path, "w") as f:
        for name in sorted(params):
            f.write(f"{name},{params[name]}\n")

    print(f"{len(params)} param ditulis ke {output_path}")
    return params


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else f"pixhawk_params_{int(time.time())}.param"
    dump_params(out)
