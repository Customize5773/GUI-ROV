import socket
import json
import time
import threading
import math
from pymavlink import mavutil

from manual_control import axes_to_manual_control, NEUTRAL

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

joystick = {
    "surge": 0,
    "sway": 0,
    "heave": 0,
    "yaw": 0,
}
# =========================
# Kontrol manual (joystick GUI -> MANUAL_CONTROL)
# =========================
# Nilai axis dalam persen -100..100 seperti dikirim dashboard (surge/sway/yaw/heave).
# Sebuah thread terpisah mengubahnya jadi MANUAL_CONTROL dan mengirim ~15 Hz.
manual_axes = {"surge": 0.0, "sway": 0.0, "yaw": 0.0, "heave": 0.0}
manual_lock = threading.Lock()
# last_ts: kapan terakhir menerima perintah axis manual (untuk fail-safe timeout).
# neutral_sent: sudah mengirim satu perintah netral setelah idle? (hindari spam).
manual_ctx = {"last_ts": 0.0, "neutral_sent": True}

MANUAL_SEND_HZ = 15          # laju kirim MANUAL_CONTROL ke Pixhawk
MANUAL_TIMEOUT = 0.5         # detik; kalau tak ada axis baru -> netralkan (fail-safe)

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

            elif name in ("surge", "sway", "yaw", "heave"):
                # Axis kontrol manual dari dashboard (persen -100..100).
                # Server sudah clamp, tapi tetap defensif di sisi Pi.
                try:
                    v = float(value)
                except (TypeError, ValueError):
                    v = 0.0
                v = max(-100.0, min(100.0, v))
                with manual_lock:
                    manual_axes[name] = v
                    manual_ctx["last_ts"] = time.time()
                    manual_ctx["neutral_sent"] = False

            elif name == "stop":
                # Failsafe sederhana: disarm + netralkan axis manual.
                print("[MAV] STOP -> DISARM")
                master.arducopter_disarm()
                with manual_lock:
                    for k in manual_axes:
                        manual_axes[k] = 0.0
                    # picu satu kali kirim netral (x=y=r=0, z=500)
                    manual_ctx["last_ts"] = time.time()
                    manual_ctx["neutral_sent"] = False

            elif name == "light":
                # Belum dihubungkan ke hardware lampu, simpan status saja
                state["light"] = bool(value)
                print(f"[LIGHT] set to {state['light']}")

            elif name in ["surge", "sway", "yaw", "heave"]:
                joystick[name] = int(value)

            else:
                print(f"[CMD] unknown command: {name}")

        except Exception as e:
            print("[CMD] error executing command:", e)

def joystick_sender():
    global master
    while True:
        if master is not None:
            print(
                f"[MANUAL] "
                f"X={joystick['surge']} "
                f"Y={joystick['sway']} "
                f"Z={joystick['heave']} "
                f"R={joystick['yaw']}"
            )   
            print("RAW =", joystick)

            master.mav.manual_control_send(
                master.target_system,
                joystick["surge"]*10,
                joystick["sway"]*10,
                joystick["heave"]*10,
                joystick["yaw"]*10,
                0
            )
            print("[MANUAL] sent")

        time.sleep(0.05)
# =========================
# Pengirim MANUAL_CONTROL periodik ke Pixhawk
# =========================
def manual_control_sender():
    """Kirim MANUAL_CONTROL ~15 Hz selama ada aktivitas axis manual.

    Fail-safe: kalau tak ada axis baru selama MANUAL_TIMEOUT (mis. joystick
    dicabut, E-Stop, atau mode Autonomous mematikan pengiriman di GUI), kirim
    SATU perintah netral (x=y=r=0, z=500) lalu berhenti mengirim sampai ada
    perintah manual lagi — sehingga command terakhir tidak "nyangkut" dan kita
    tidak mengganggu mode autonomous dengan MANUAL_CONTROL terus-menerus.
    """
    period = 1.0 / MANUAL_SEND_HZ
    print(f"[MANUAL] sender aktif @ {MANUAL_SEND_HZ} Hz (timeout {MANUAL_TIMEOUT}s)")
    while True:
        time.sleep(period)
        if master is None:
            continue

        now = time.time()
        with manual_lock:
            active = (now - manual_ctx["last_ts"]) <= MANUAL_TIMEOUT
            axes = dict(manual_axes)
            neutral_sent = manual_ctx["neutral_sent"]

        try:
            if active:
                mc = axes_to_manual_control(
                    axes["surge"], axes["sway"], axes["yaw"], axes["heave"]
                )
                master.mav.manual_control_send(
                    master.target_system,
                    mc["x"], mc["y"], mc["z"], mc["r"], mc["buttons"],
                )
            elif not neutral_sent:
                mc = NEUTRAL
                master.mav.manual_control_send(
                    master.target_system,
                    mc["x"], mc["y"], mc["z"], mc["r"], mc["buttons"],
                )
                with manual_lock:
                    manual_ctx["neutral_sent"] = True
                print("[MANUAL] idle -> kirim netral (fail-safe)")
        except Exception as e:
            print("[MANUAL] send error:", e)


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
    threading.Thread(target=joystick_sender, daemon=True).start()

    # Thread pengirim MANUAL_CONTROL periodik (kontrol manual joystick/keyboard)
    threading.Thread(target=manual_control_sender, daemon=True).start()

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
