import socket
import json
import time
import threading
import math
from pymavlink import mavutil

from rov_axes import AXIS_NEUTRAL, AXIS_RANGE, clamp_axis, to_mavlink_z
from attitude_filter import AttitudeFilter

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

# Complementary filter + EMA untuk roll/pitch/yaw dari ATTITUDE (lihat
# attitude_filter.py). Meredam jitter sensor tanpa menambah lag berarti.
attitude_filter = AttitudeFilter()
prev_attitude_ts = None

joystick = {
    "surge": 0,
    "sway": 0,
    "heave": 0,
    "yaw": 0,
}

# Fail-safe: kalau tidak ada perintah axis baru dari GUI selama
# JOYSTICK_IDLE_TIMEOUT detik (GUI crash / joystick dicabut / link putus),
# kirim SATU perintah netral lalu berhenti sampai ada perintah manual lagi.
JOYSTICK_IDLE_TIMEOUT = 0.5
last_joystick_update = 0.0
joystick_lock = threading.Lock()

def send_telemetry():
    payload = json.dumps(state).encode("utf-8")
    telem_sock.sendto(payload, (LAPTOP_IP, UDP_TELEM_PORT))

def normalize_heading(deg):
    if deg < 0:
        deg += 360.0
    return deg % 360.0

# =========================
# Command handler dari laptop
# =========================
def command_listener():
    global master, last_joystick_update
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

        # axis datang ~15 Hz — jangan di-log supaya tidak membanjiri console
        if name not in AXIS_RANGE:
            print(f"[CMD] {name} = {value} from {addr}")

        if master is None:
            print("[CMD] Pixhawk not connected yet")
            continue

        try:
            if name == "arm":
                if value:
                    print("[MAV] ARM")
                    master.mav.command_long_send(
                        master.target_system,
                        master.target_component,
                        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                        0,
                        1,      # arm
                        0,0,0,0,0,0
                    )
                    # COMMAND_ACK ditangani oleh loop utama (main()) supaya
                    # thread ini tidak terblokir dan STOP/E-Stop tetap responsif.
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
                # Failsafe sederhana: netralkan axis lalu disarm
                print("[MAV] STOP -> DISARM")
                with joystick_lock:
                    joystick.update(AXIS_NEUTRAL)
                    last_joystick_update = 0.0
                master.arducopter_disarm()

            elif name == "light":
                # Belum dihubungkan ke hardware lampu, simpan status saja
                state["light"] = bool(value)
                print(f"[LIGHT] set to {state['light']}")

            elif name == "thruster_config":

                motors = msg.get("motors", {})

                for motor, direction in motors.items():

                    motor = int(motor)
                    direction = int(direction)
                    param = f"MOT_{motor}_DIRECTION"
                    print(f"[PARAM] {param} -> {direction}")

                    master.mav.param_set_send(
                        master.target_system,
                        master.target_component,
                        param.encode("utf-8"),
                        float(direction),
                        mavutil.mavlink.MAV_PARAM_TYPE_INT8
                    )
                    time.sleep(0.1)
                print("[PARAM] Thruster configuration updated.")

            elif name in AXIS_RANGE:
                with joystick_lock:
                    joystick[name] = clamp_axis(name, value)
                    last_joystick_update = time.time()

            else:
                print(f"[CMD] unknown command: {name}")

        except Exception as e:
            print("[CMD] error executing command:", e)

def joystick_sender():
    """Kirim MANUAL_CONTROL 20 Hz selama GUI masih aktif mengirim axis.

    Fail-safe: bila tidak ada perintah axis baru selama JOYSTICK_IDLE_TIMEOUT,
    kirim satu perintah netral (semua axis 0) lalu berhenti mengirim sampai
    ada perintah manual berikutnya. Ini mencegah Pi terus mengulang nilai
    terakhir ke Pixhawk saat GUI crash atau link putus.
    """
    global master

    sent_neutral = True   # sudah netral saat start, belum ada input

    while True:
        if master is None:
            time.sleep(0.05)
            continue

        with joystick_lock:
            axes = dict(joystick)
            idle = (time.time() - last_joystick_update) > JOYSTICK_IDLE_TIMEOUT

        if idle:
            if sent_neutral:
                time.sleep(0.05)
                continue

            axes = dict(AXIS_NEUTRAL)
            sent_neutral = True
            print("[MANUAL] fail-safe: idle, kirim netral")
        else:
            sent_neutral = False

        try:
            master.mav.manual_control_send(
                master.target_system,
                axes["surge"],
                axes["sway"],
                to_mavlink_z(axes["heave"]),
                axes["yaw"],
                0
            )
        except Exception as e:
            print("[MANUAL] gagal kirim:", e)

        time.sleep(0.05)
# =========================
# Main koneksi Pixhawk
# =========================
def main():
    global master, prev_attitude_ts

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

    last_send = 0

    while True:
        msg = master.recv_match(blocking=True, timeout=1)
        if msg is not None and msg.get_type() == "STATUSTEXT":
           print("[PIXHAWK]", msg.text)

        if msg is None:
            continue

        mtype = msg.get_type()

        # --------------------------------
        # ATTITUDE: roll, pitch, yaw
        # --------------------------------
        if mtype == "ATTITUDE":
            now_ts = msg._timestamp
            dt = (now_ts - prev_attitude_ts) if prev_attitude_ts is not None else 0.1
            roll_f, pitch_f, yaw_f = attitude_filter.update(
                math.degrees(msg.roll),
                math.degrees(msg.pitch),
                normalize_heading(math.degrees(msg.yaw)),
                math.degrees(msg.rollspeed),
                math.degrees(msg.pitchspeed),
                math.degrees(msg.yawspeed),
                dt,
            )
            state["roll"] = roll_f
            state["pitch"] = pitch_f
            state["heading"] = yaw_f
            prev_attitude_ts = now_ts

        # --------------------------------
        # COMMAND_ACK: hasil ARM/DISARM dsb.
        # Ditangani di sini (bukan di command_listener) supaya thread command
        # tidak pernah terblokir menunggu ACK.
        # --------------------------------
        elif mtype == "COMMAND_ACK":
            print(f"[MAV] ACK cmd={msg.command} result={msg.result}")

        # VFR_HUD.heading sengaja tidak dipakai: ATTITUDE (via attitude_filter)
        # adalah satu-satunya sumber heading, supaya heading yang sudah
        # difilter tidak ditimpa nilai mentah.

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
