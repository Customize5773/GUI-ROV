import socket
import json
import time
import threading
import math
from pymavlink import mavutil

from rov_axes import AXIS_RANGE, IDLE_TIMEOUT, clamp_axis, resolve_manual_packet
from attitude_filter import AttitudeFilter
from rov_gripper import (
    GRIPPER_PWM_NEUTRAL,
    GRIPPER_SERVO_CH,
    gripper_value_to_pwm,
    slew_toward,
)

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
    # "ok" selama axis dari GUI masih mengalir, "stale" saat fail-safe idle
    # aktif dan Pi mengirim netral sendiri. Sengaja BUKAN "link": sisi browser
    # sudah punya penanda sendiri untuk arah sebaliknya (telemetry tidak sampai
    # ke GUI); yang ini menandai perintah tidak sampai ke Pi.
    "cmd_link": "ok",
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
# IDLE_TIMEOUT detik (GUI crash / joystick dicabut / link putus), berhenti
# memakai axis terakhir dan streaming NEUTRAL sebagai gantinya.
#
# Kenapa TETAP streaming (bukan berhenti mengirim)? ArduSub mengharapkan aliran
# MANUAL_CONTROL yang kontinu; kalau kita diam, failsafe pilot-input Pixhawk
# yang jalan dan perilakunya tergantung parameter. Mengirim netral terus jauh
# lebih bisa diprediksi: diam di tempat, dan di ALT_HOLD berarti tahan
# kedalaman.
JOYSTICK_SEND_INTERVAL = 0.05   # 20 Hz
last_joystick_update = 0.0
joystick_lock = threading.Lock()

# Log axis per-iterasi membanjiri console Pi (20 baris/detik) dan memakan CPU
# yang dibutuhkan link serial. Nyalakan hanya saat debugging.
VERBOSE_JOYSTICK = False
JOYSTICK_LOG_INTERVAL = 1.0

# Telemetry keluar 10 Hz; mencetaknya juga hanya menutupi log yang penting.
VERBOSE_TELEMETRY = False

# Perintah yang memang hanya mengubah tampilan/state di dashboard dan tidak
# punya padanan di wahana. Didaftarkan eksplisit supaya log "unknown command"
# benar-benar hanya berisi hal yang perlu diperiksa.
GUI_ONLY_COMMANDS = frozenset({
    "controller",     # tab Keyboard/Gamepad di dashboard
    "set_surface",    # reset acuan permukaan (dihitung sisi GUI)
    "snapshot",       # tangkapan frame di browser
    "record",         # perekaman di browser
})

# =========================
# Gripper
# =========================
# Target = posisi yang diminta operator (keyboard/tombol/axis gamepad).
# Filtered = posisi yang benar-benar dikirim ke servo, hasil rate-limit + EMA
# di rov_gripper.slew_toward() supaya gerakan halus dan tidak menyentak.
GRIPPER_SEND_INTERVAL = 0.1     # 10 Hz
GRIPPER_SEND_EPSILON = 1.0      # jangan spam MAVLink kalau beda < 1 PWM

gripper_target = float(GRIPPER_PWM_NEUTRAL)
gripper_filtered = float(GRIPPER_PWM_NEUTRAL)
gripper_lock = threading.Lock()

def send_telemetry():
    payload = json.dumps(state).encode("utf-8")
    telem_sock.sendto(payload, (LAPTOP_IP, UDP_TELEM_PORT))
    if VERBOSE_TELEMETRY:
        print(f"[SEND] -> {LAPTOP_IP}:{UDP_TELEM_PORT} | {state}")
def normalize_heading(deg):
    if deg < 0:
        deg += 360.0
    return deg % 360.0

# =========================
# Command handler dari laptop
# =========================
def command_listener():
    global master, last_joystick_update, gripper_target
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

        # axis datang ~15 Hz — jangan di-log supaya tidak membanjiri console.
        # Gripper analog (nilai angka dari axis gamepad) juga bisa datang cepat;
        # open/close diskrit dari tombol/keyboard tetap di-log.
        quiet = name in AXIS_RANGE or (
            name == "gripper" and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
        if not quiet:
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

            elif name == "gripper":
                # "open"/"close" dari tombol & keyboard H/G, atau angka
                # -1000..1000 dari axis analog gamepad. Yang disimpan hanya
                # TARGET; gripper_sender() yang menggerakkannya perlahan.
                with gripper_lock:
                    gripper_target = gripper_value_to_pwm(value)

            elif name in AXIS_RANGE:
                with joystick_lock:
                    joystick[name] = clamp_axis(name, value)
                    last_joystick_update = time.time()

            elif name in GUI_ONLY_COMMANDS:
                # Murni state dashboard (tab controller aktif, reset acuan
                # permukaan, snapshot/record lokal). Diterima tanpa aksi —
                # diakui di sini supaya tidak tercatat sebagai perintah asing.
                pass

            else:
                print(f"[CMD] unknown command: {name} = {value}")

        except Exception as e:
            print("[CMD] error executing command:", e)

def joystick_sender():
    """Streaming MANUAL_CONTROL ke Pixhawk 20 Hz.

    Konversi axis GUI -> field MANUAL_CONTROL dilakukan oleh
    rov_axes.resolve_manual_packet(), termasuk heave -> z (0..1000, netral 500).
    Ini PENTING: mengirim heave mentah membuat stik netral terbaca sebagai
    turun penuh oleh ArduSub.
    """
    global master

    was_stale = None        # None = belum pernah lapor, supaya status awal ikut ter-log
    last_log = 0.0

    while True:
        if master is None:
            time.sleep(JOYSTICK_SEND_INTERVAL)
            continue

        with joystick_lock:
            axes = dict(joystick)
            last_update = last_joystick_update

        packet, stale = resolve_manual_packet(axes, last_update, time.time())

        if stale != was_stale:
            was_stale = stale
            state["cmd_link"] = "stale" if stale else "ok"
            if stale:
                print(f"[FAILSAFE] Tidak ada axis > {IDLE_TIMEOUT}s — kirim NEUTRAL")
            else:
                print("[FAILSAFE] Axis mengalir lagi — kontrol manual pulih")

        try:
            master.mav.manual_control_send(
                master.target_system,
                packet["x"],
                packet["y"],
                packet["z"],
                packet["r"],
                packet["buttons"],
            )
        except Exception as e:
            print("[MANUAL] gagal kirim:", e)

        if VERBOSE_JOYSTICK:
            now = time.time()
            if now - last_log >= JOYSTICK_LOG_INTERVAL:
                last_log = now
                print(
                    f"[MANUAL] x={packet['x']} y={packet['y']} "
                    f"z={packet['z']} r={packet['r']}"
                    f"{' (STALE)' if stale else ''}"
                )

        time.sleep(JOYSTICK_SEND_INTERVAL)

def gripper_sender():
    """Gerakkan servo gripper menuju target 10 Hz dgn rate-limit + EMA.

    Sengaja dipisah dari command_listener: perintah dari GUI hanya mengubah
    TARGET, sedangkan thread ini yang menggeser posisi servo sedikit demi
    sedikit. Efeknya gripper tidak menyentak walau operator menekan
    open/close berulang cepat, dan posisi terakhir DITAHAN (tidak balik
    sendiri) saat tidak ada perintah baru.
    """
    global gripper_filtered

    last_sent_pwm = None
    last_ts = time.time()

    while True:
        if master is None:
            time.sleep(0.1)
            last_ts = time.time()
            continue

        now = time.time()
        dt = now - last_ts
        last_ts = now

        with gripper_lock:
            target = gripper_target

        gripper_filtered = slew_toward(gripper_filtered, target, dt)

        # Hanya kirim kalau posisi benar-benar berubah — hindari membanjiri
        # link serial 115200 yang dipakai bersama telemetry.
        if last_sent_pwm is None or abs(gripper_filtered - last_sent_pwm) >= GRIPPER_SEND_EPSILON:
            pwm = int(round(gripper_filtered))
            try:
                master.mav.command_long_send(
                    master.target_system,
                    master.target_component,
                    mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
                    0,
                    GRIPPER_SERVO_CH,
                    pwm,
                    0, 0, 0, 0, 0
                )
                last_sent_pwm = gripper_filtered
            except Exception as e:
                print("[GRIPPER] gagal kirim:", e)

        time.sleep(GRIPPER_SEND_INTERVAL)

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
    threading.Thread(target=gripper_sender, daemon=True).start()

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
        # AHRS2 : Depth dari ArduSub (meter)
        # --------------------------------
        elif mtype == "AHRS2":

            # altitude bernilai negatif saat ROV berada di bawah permukaan
            if hasattr(msg, "altitude"):
                state["depth"] = max(0.0, -float(msg.altitude))
                if VERBOSE_TELEMETRY:
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
