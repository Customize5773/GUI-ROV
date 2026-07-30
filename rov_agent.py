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


# ==========================
# Manipulator Configuration
# ==========================

GRIP_CHANNEL = 7
ROTATE_CHANNEL = 8

SERVO_NEUTRAL = 1500

GRIP_OPEN_PWM = 1900
GRIP_CLOSE_PWM = 1100

ROTATE_LEFT_PWM = 1100
ROTATE_RIGHT_PWM = 1900

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

def set_servo_pwm(channel, pwm):
    """
    Mengirim PWM ke output servo Pixhawk.
    Channel menggunakan nomor SERVO (1-14).
    """

    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
        0,
        channel,
        pwm,
        0, 0, 0, 0, 0
    )

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
            print("RAW UDP:", msg)
        except Exception:
            print("[UDP] invalid JSON command")
            continue

        name = msg.get("name")
        value = msg.get("value")
        print(f"[CMD] {name} = {value} from {addr}")
        print("FULL CMD =", msg)

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

                    ack = master.recv_match(type="COMMAND_ACK", blocking=True, timeout=3)

                    print("ACK =", ack)
                else:
                    print("[MAV] DISARM")
                    master.arducopter_disarm()

            elif name == "control_mode":

                global current_control_mode

                current_control_mode = str(value).lower()

                if current_control_mode not in ("manual", "autonomous"):
                    print(f"[CONTROL] Unknown mode: {current_control_mode}")
                    continue

                print(f"[CONTROL] {current_control_mode}")

            elif name == "pilot_mode":

                mode = str(value).lower()

                pilot_mode_map = {
                    "manual": "MANUAL",
                    "stabilize": "STABILIZE",
                    "depth_hold": "ALT_HOLD",
                }

                if mode not in pilot_mode_map:
                    print(f"[PILOT] Unknown mode: {mode}")
                    continue

                pixhawk_mode = pilot_mode_map[mode]

                mode_mapping = master.mode_mapping() or {}

                if pixhawk_mode not in mode_mapping:
                    print(f"[PILOT] {pixhawk_mode} not supported")
                    continue

                master.set_mode(mode_mapping[pixhawk_mode])

                print("====================================")
                print(f" PILOT MODE : {pixhawk_mode}")
                print("====================================")

            elif name == "stop":
                # Failsafe sederhana: disarm
                print("[MAV] STOP -> DISARM")
                master.arducopter_disarm()

            elif name == "light":
                # Belum dihubungkan ke hardware lampu, simpan status saja
                state["light"] = bool(value)
                print(f"[LIGHT] set to {state['light']}")

            elif name == "thruster_config":

                motors = msg.get("motors", {})
                print("[DEBUG] Motors received:", motors)
                
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

            elif name == "manipulator":

                handle_manipulator(
                    msg.get("device"),
                    msg.get("action"),
                    msg.get("direction")
                )

            elif name in ["surge", "sway", "yaw", "heave"]:
                joystick[name] = int(value)

            else:
                print(f"[CMD] unknown command: {name}")

        except Exception as e:
            print("[CMD] error executing command:", e)

def handle_manipulator(device, action, direction):

    print(f"[MANIP] {device} | {action} | {direction}")

    if device == "grip":

        if action == "start":

            if direction == "open":
                set_servo_pwm(GRIP_CHANNEL, GRIP_OPEN_PWM)

            elif direction == "close":
                set_servo_pwm(GRIP_CHANNEL, GRIP_CLOSE_PWM)

        elif action == "stop":
            set_servo_pwm(GRIP_CHANNEL, SERVO_NEUTRAL)

    elif device == "rotate":

        if action == "start":

            if direction == "left":
                set_servo_pwm(ROTATE_CHANNEL, ROTATE_LEFT_PWM)

            elif direction == "right":
                set_servo_pwm(ROTATE_CHANNEL, ROTATE_RIGHT_PWM)

        elif action == "stop":
            set_servo_pwm(ROTATE_CHANNEL, SERVO_NEUTRAL)

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
                joystick["surge"],
                joystick["sway"],
                joystick["heave"],
                joystick["yaw"],
                0
            )
            
            print("[MANUAL] sent")

        time.sleep(0.05)
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

    # =========================
    # Request AHRS2 (Depth)
    # =========================
    try:
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            mavutil.mavlink.MAVLINK_MSG_ID_AHRS2,
            100000,      # 10 Hz (100000 µs)
            0, 0, 0, 0, 0
        )
    except Exception as e:
        print("[MAV] AHRS2 request warning:", e)

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
        # AHRS2 : Depth dari ArduSub (meter)
        # --------------------------------
        elif mtype == "AHRS2":

            # altitude bernilai negatif saat ROV berada di bawah permukaan
            if hasattr(msg, "altitude"):
                state["depth"] = max(0.0, -float(msg.altitude))
                print(f"[DEPTH] {state['depth']:.2f} m")
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
