import socket
import json
import time
import threading
import math
from pymavlink import mavutil

from rov_axes import AXIS_NEUTRAL, AXIS_RANGE, clamp_axis, to_mavlink_z

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
# Manipulator Servo
# =========================
SERVO_GRIP = 7
SERVO_ROTATE = 8

PWM_STOP  = 1500

PWM_OPEN  = 1900
PWM_CLOSE = 1100

PWM_LEFT  = 1100
PWM_RIGHT = 1900

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
<<<<<<< HEAD
    print(f"[SEND] -> {LAPTOP_IP}:{UDP_TELEM_PORT} | {state}")
=======
>>>>>>> b988616c341010f902e9fc3a38ab3899740bd725

def normalize_heading(deg):
    if deg < 0:
        deg += 360.0
    return deg % 360.0

# =========================
# Servo Output
# =========================
def set_servo(channel, pwm):
    global master

    if master is None:
        return

    try:
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
            0,
            channel,    # Servo channel
            pwm,        # PWM
            0, 0, 0, 0, 0
        )

        print(f"[SERVO] CH={channel} PWM={pwm}")

    except Exception as e:
        print(f"[SERVO] ERROR: {e}")    

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
<<<<<<< HEAD
        device = msg.get("device")
        action = msg.get("action")
        direction = msg.get("direction")
=======

        # axis datang ~15 Hz — jangan di-log supaya tidak membanjiri console
        if name not in AXIS_RANGE:
            print(f"[CMD] {name} = {value} from {addr}")
>>>>>>> b988616c341010f902e9fc3a38ab3899740bd725

        print(f"[CMD] {name} = {value} from {addr}")
        
        if master is None:
            print("[CMD] Pixhawk not connected yet")
            continue

        try:
            if name == "manipulator":

                # Validasi
                if device not in ("grip", "rotate"):
                    print(f"[MANIPULATOR] Unknown device: {device}")
                    continue

                if action not in ("start", "stop"):
                    print(f"[MANIPULATOR] Unknown action: {action}")
                    continue

                if direction not in ("open", "close", "left", "right", None):
                    print(f"[MANIPULATOR] Unknown direction: {direction}")
                    continue

                print(
                    f"[MANIPULATOR] "
                    f"device={device} "
                    f"action={action} "
                    f"direction={direction}"
                )

                # =========================
                # GRIP
                # =========================
                if device == "grip":

                    if action == "start":

                        if direction == "open":
                            set_servo(SERVO_GRIP, PWM_OPEN)

                        elif direction == "close":
                            set_servo(SERVO_GRIP, PWM_CLOSE)

                    elif action == "stop":
                        set_servo(SERVO_GRIP, PWM_STOP)

                # =========================
                # ROTATE
                # =========================
                elif device == "rotate":

                    if action == "start":

                        if direction == "left":
                            set_servo(SERVO_ROTATE, PWM_LEFT)

                        elif direction == "right":
                            set_servo(SERVO_ROTATE, PWM_RIGHT)

                    elif action == "stop":
                        set_servo(SERVO_ROTATE, PWM_STOP)

                continue
            
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
<<<<<<< HEAD
                print("[DEBUG] Motors received:", motors)

                for motor, motor_direction in motors.items():
=======

                for motor, direction in motors.items():
>>>>>>> b988616c341010f902e9fc3a38ab3899740bd725

                    motor = int(motor)
                    motor_direction = int(motor_direction)

                    param = f"MOT_{motor}_DIRECTION"

                    print(f"[PARAM] {param} -> {motor_direction}")

                    master.mav.param_set_send(
                        master.target_system,
                        master.target_component,
                        param.encode("utf-8"),
                        float(motor_direction),
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

        if msg is None:
            continue

        # =========================
        # DEBUG MAVLINK
        # =========================
        if msg.get_type() in [
            "AHRS2",
            "ALTITUDE",
            "SCALED_PRESSURE",
            "SCALED_PRESSURE2",
            "SCALED_PRESSURE3",
            "VFR_HUD"
        ]:
            print(msg)

        if msg.get_type() == "STATUSTEXT":
            print("[PIXHAWK]", msg.text)

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
        # --------------------------------
        # COMMAND_ACK: hasil ARM/DISARM dsb.
        # Ditangani di sini (bukan di command_listener) supaya thread command
        # tidak pernah terblokir menunggu ACK.
        # --------------------------------
        elif mtype == "COMMAND_ACK":
            print(f"[MAV] ACK cmd={msg.command} result={msg.result}")

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
            # altitude pada ArduSub bernilai negatif saat berada di bawah permukaan.
            # Depth = -altitude
            if hasattr(msg, "altitude"):
                state["depth"] = max(0.0, -float(msg.altitude))

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
