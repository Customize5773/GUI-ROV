import socket
import json
import time
import threading
import math
from pymavlink import mavutil

# =========================
# Konfigurasi jaringan
# =========================
LAPTOP_IP = "192.168.2.1"   # IP laptop / ground station
UDP_TELEM_PORT = 14551      # telemetry ke laptop (sesuai server.js)
UDP_CMD_PORT = 14550        # command dari laptop ke Pi

# =========================
# Konfigurasi Pixhawk
# =========================
PIXHAWK_PORT = "/dev/ttyACM0"
PIXHAWK_BAUD = 115200

# =========================
# Socket UDP
# =========================
telem_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
cmd_sock.bind(("0.0.0.0", UDP_CMD_PORT))
cmd_sock.settimeout(0.2)

# =========================
# Status telemetry lokal
# =========================
state = {
    "heading": 0.0,
    "depth": 0.0,       # sementara 0 dulu, nanti kita isi dari sensor depth
    "roll": 0.0,
    "pitch": 0.0,
    "temp": 0.0,        # sementara 0 dulu, nanti bisa dari sensor suhu
    "voltage": 0.0,
    "armed": False,
    "light": False,
    "mode": "unknown",
}

master = None

# =========================
# Utility
# ========================
def send_telemetry():
    payload = json.dumps(state).encode("utf-8")
    telem_sock.sendto(payload, (LAPTOP_IP, UDP_TELEM_PORT))
    print(f"[SEND] -> {LAPTOP_IP}:{UDP_TELEM_PORT} | {state}")
def normalize_heading(deg):
    if deg < 0:
        deg += 360.0
    return deg % 360.0

# =========================
# Command handler dari laptop
# =========================
def command_listener():
    global master
    print(f"[UDP] Listening command on 0.0.0.0:{UDP_CMD_PORT}")
    while True:
        try:
            data, addr = cmd_sock.recvfrom(4096)
        except socket.timeout:
            continue
        except Exception as e:
            print("[UDP] command socket error:", e)
            continue

        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            print("[UDP] invalid JSON command")
            continue

        name = msg.get("name")
        value = msg.get("value")
        print(f"[CMD] {name} = {value} from {addr}")

        if master is None:
            print("[CMD] Pixhawk not connected yet")
            continue

        try:
            if name == "arm":
                if value:
                    print("[MAV] ARM")
                    master.arducopter_arm()
                else:
                    print("[MAV] DISARM")
                    master.arducopter_disarm()

            elif name == "control_mode":
                # contoh mode umum ArduSub / ArduPilot:
                # MANUAL, STABILIZE, ALT_HOLD, POSHOLD, GUIDED, dsb
                mode = str(value).upper()
                if mode in master.mode_mapping():
                    mode_id = master.mode_mapping()[mode]
                    master.set_mode(mode_id)
                    print(f"[MAV] set mode {mode}")
                else:
                    print(f"[MAV] mode '{mode}' tidak ada di mode_mapping()")

            elif name == "stop":
                # Failsafe sederhana: disarm
                print("[MAV] STOP -> DISARM")
                master.arducopter_disarm()

            elif name == "light":
                # Belum dihubungkan ke hardware lampu, simpan status saja
                state["light"] = bool(value)
                print(f"[LIGHT] set to {state['light']}")

            else:
                print(f"[CMD] unknown command: {name}")

        except Exception as e:
            print("[CMD] error executing command:", e)

# =========================
# Main koneksi Pixhawk
# =========================
def main():
    global master

    print(f"[MAV] Connecting to Pixhawk on {PIXHAWK_PORT} @ {PIXHAWK_BAUD} ...")
    master = mavutil.mavlink_connection(PIXHAWK_PORT, baud=PIXHAWK_BAUD)

    print("[MAV] Waiting heartbeat...")
    master.wait_heartbeat(timeout=30)
    print("[MAV] Heartbeat received!")
    print(f"[MAV] System {master.target_system}, Component {master.target_component}")

    # Minta stream data secara periodik
    try:
        master.mav.request_data_stream_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_ALL,
            10,   # 10 Hz
            1
        )
    except Exception as e:
        print("[MAV] request_data_stream_send warning:", e)

    # Thread listener command
    threading.Thread(target=command_listener, daemon=True).start()

    last_send = 0

    while True:
        msg = master.recv_match(blocking=True, timeout=1)
        if msg is None:
            continue

        mtype = msg.get_type()

        # --------------------------------
        # ATTITUDE: roll, pitch, yaw
        # --------------------------------
        if mtype == "ATTITUDE":
            state["roll"] = math.degrees(msg.roll)
            state["pitch"] = math.degrees(msg.pitch)
            yaw_deg = math.degrees(msg.yaw)
            state["heading"] = normalize_heading(yaw_deg)

        # --------------------------------
        # VFR_HUD: heading kadang tersedia di sini juga
        # --------------------------------
        elif mtype == "VFR_HUD":
            if hasattr(msg, "heading"):
                state["heading"] = float(msg.heading)

        # --------------------------------
        # SYS_STATUS: tegangan baterai
        # voltage_battery dalam mV
        # --------------------------------
        elif mtype == "SYS_STATUS":
            if msg.voltage_battery != 65535:
                state["voltage"] = msg.voltage_battery / 1000.0

        # --------------------------------
        # HEARTBEAT: mode dan armed
        # --------------------------------
        elif mtype == "HEARTBEAT":
            try:
                state["mode"] = mavutil.mode_string_v10(msg)
            except Exception:
                pass

            base_mode = msg.base_mode
            state["armed"] = bool(base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

        # Kirim telemetry periodik ke laptop
        now = time.time()
        if now - last_send >= 0.1:  # 10 Hz
            send_telemetry()
            last_send = now

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[EXIT] rov_agent stopped by user")
    except Exception as e:
        print("[FATAL]", e)
