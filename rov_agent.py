import socket
import json
import time
import threading
import math
from pymavlink import mavutil

from rov_axes import AXIS_NEUTRAL, AXIS_RANGE, NEUTRAL, axes_to_manual_control, clamp_axis
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

# Tidak ada satu pun pesan MAVLink selama ini -> link dianggap mati dan
# disambungkan ulang (USB lepas / Pixhawk re-enumerate).
LINK_TIMEOUT = 3.0

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

depth_target = 0.0
DEPTH_STEP = 0.10
depth_lock = threading.Lock()

# Mode yang TERAKHIR DIMINTA lewat set_mode. state["mode"] hanya ter-update saat
# HEARTBEAT datang, jadi tanpa ini gain_inc/gain_dec yang ditekan tepat setelah
# ganti mode akan diabaikan diam-diam.
requested_mode = None

# Depth hold didelegasikan ke ALT_HOLD ArduSub; depth_target hanya menggeser
# setpoint lewat bias kecil pada throttle. Dibatasi supaya tidak pernah bisa
# melawan operator atau menyelam tak terkendali di kolam dangkal.
DEPTH_BIAS_GAIN = 200.0   # unit z per meter error
DEPTH_BIAS_LIMIT = 80     # |bias| maksimum terhadap Z_NEUTRAL (500)
HEAVE_MANUAL_EPSILON = 20 # |heave| di atas ini dianggap operator sedang memegang stik


def depth_hold_active():
    return (requested_mode or state["mode"]) in ("STABILIZE", "ALT_HOLD")


def apply_depth_hold_bias(mc, axes):
    """Geser MANUAL_CONTROL.z sedikit ke arah depth_target saat ALT_HOLD.

    Hanya berlaku kalau stik heave benar-benar netral — begitu operator
    menyentuh stik, input manual menang mutlak.
    """
    if not depth_hold_active():
        return mc
    if abs(axes.get("heave", 0)) > HEAVE_MANUAL_EPSILON:
        return mc

    with depth_lock:
        target = depth_target

    # depth & target dalam meter, positif ke bawah. Error positif = perlu turun.
    error = target - state["depth"]
    bias = max(-DEPTH_BIAS_LIMIT, min(DEPTH_BIAS_LIMIT, error * DEPTH_BIAS_GAIN))

    out = dict(mc)
    out["z"] = max(0, min(1000, int(round(mc["z"] - bias))))
    return out

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

_last_telem_log = 0.0

def send_telemetry():
    """Kirim state ke laptop 10 Hz, tapi LOG hanya 1 Hz.

    Mencetak tiap paket (10 Hz) plus log joystick 20 Hz membuat stdout yang
    ter-pipe jadi blocking dan menimbulkan jitter pada loop kontrol di Pi.
    """
    global _last_telem_log

    with depth_lock:
        state["depth_target"] = depth_target

    payload = json.dumps(state).encode("utf-8")
    telem_sock.sendto(payload, (LAPTOP_IP, UDP_TELEM_PORT))

    now = time.time()
    if now - _last_telem_log >= 1.0:
        _last_telem_log = now
        print(f"[SEND] -> {LAPTOP_IP}:{UDP_TELEM_PORT} | {state}")

def normalize_heading(deg):
    if deg < 0:
        deg += 360.0
    return deg % 360.0

def send_arm_disarm(arm):
    """Arm/disarm lewat MAV_CMD_COMPONENT_ARM_DISARM (satu jalur untuk keduanya).

    Tidak menunggu ACK — lihat handler COMMAND_ACK di main().
    """
    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        1 if arm else 0,
        0, 0, 0, 0, 0, 0,
    )


def send_gcs_heartbeat():
    """Heartbeat GCS 1 Hz.

    Tanpa ini ArduSub menganggap ground station hilang dan DISARM SENDIRI
    beberapa detik setelah arm (FS_GCS_ENABLE) — gejalanya arm terlihat
    "putus-putus" saat mulai trial.
    """
    master.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
        0, 0, 0,
    )


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
    global last_joystick_update
    global gripper_target
    global depth_target
    global requested_mode

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
                # ACK TIDAK ditunggu di sini. recv_match(blocking=True) akan
                # memblokir seluruh thread ini sampai 3 detik DAN mencuri
                # COMMAND_ACK dari loop RX utama, sehingga status armed telat
                # sampai ke GUI. ACK ditangani non-blocking di main().
                print("[MAV] ARM" if value else "[MAV] DISARM")
                send_arm_disarm(bool(value))

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
                requested_mode = pixhawk_mode

                if mode in ("stabilize", "depth_hold"):
                    with depth_lock:
                        depth_target = state["depth"]
                    print(f"[DEPTH] Target initialized = {depth_target:.2f} m")

                print("====================================")
                print(f" PILOT MODE : {pixhawk_mode}")
                print("====================================")

            elif name == "stop":
                # Failsafe sederhana: netralkan axis lalu disarm
                print("[MAV] STOP -> DISARM")
                with joystick_lock:
                    joystick.update(AXIS_NEUTRAL)
                send_arm_disarm(False)

            elif name == "light":
                # Belum dihubungkan ke hardware lampu, simpan status saja
                state["light"] = bool(value)
                print(f"[LIGHT] set to {state['light']}")

            elif name in ("gain_inc", "gain_dec"):

                # Pakai requested_mode juga: state["mode"] baru ter-update saat
                # HEARTBEAT berikutnya, jadi kalau hanya mengandalkan itu maka
                # penekanan tepat setelah ganti mode hilang tanpa jejak.
                if not depth_hold_active():
                    print(f"[DEPTH] {name} diabaikan — mode bukan depth hold")
                    continue

                step = DEPTH_STEP if name == "gain_inc" else -DEPTH_STEP
                with depth_lock:
                    depth_target = max(0.0, depth_target + step)
                    shown = depth_target
                print(f"[DEPTH] Target = {shown:.2f} m")

            elif name == "thruster_config":

                # Dijalankan di thread terpisah: loop param_set_send + sleep
                # menahan listener ini ~0.6 s dan membuat axis/mode ikut tertunda.
                motors = msg.get("motors", {})
                print("[DEBUG] Motors received:", motors)
                threading.Thread(
                    target=apply_thruster_config, args=(motors,), daemon=True
                ).start()

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

            else:
                print(f"[CMD] unknown command: {name} = {value}")

        except Exception as e:
            print("[CMD] error executing command:", e)

def apply_thruster_config(motors):
    """Tulis MOT_n_DIRECTION satu per satu (perlu jeda antar param_set_send)."""
    try:
        for motor, direction in motors.items():
            param = f"MOT_{int(motor)}_DIRECTION"
            print(f"[PARAM] {param} -> {int(direction)}")
            master.mav.param_set_send(
                master.target_system,
                master.target_component,
                param.encode("utf-8"),
                float(int(direction)),
                mavutil.mavlink.MAV_PARAM_TYPE_INT8,
            )
            time.sleep(0.1)
        print("[PARAM] Thruster configuration updated.")
    except Exception as e:
        print("[PARAM] gagal set thruster config:", e)


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
    """Kirim MANUAL_CONTROL 20 Hz.

    PENTING: konversi axis -> field MAVLink WAJIB lewat axes_to_manual_control()
    (rov_axes.py). Konvensi GUI adalah -1000..1000 dengan 0 = diam untuk KEEMPAT
    axis, sedangkan ArduSub mengharapkan z pada 0..1000 dengan 500 = netral.
    Mengirim heave mentah sebagai z membuat "diam" berarti MENYELAM PENUH —
    termasuk saat E-Stop dan saat link GUI putus.
    """
    global master

    while True:
        if master is not None:
            with joystick_lock:
                axes = dict(joystick)
                idle = (time.time() - last_joystick_update) > JOYSTICK_IDLE_TIMEOUT

            # Fail-safe: GUI diam terlalu lama (crash / joystick dicabut / link
            # putus) -> tahan posisi netral, jangan ulangi input terakhir.
            mc = NEUTRAL if idle else axes_to_manual_control(**axes)

            mc = apply_depth_hold_bias(mc, axes)

        try:
            master.mav.manual_control_send(
                master.target_system,
                mc["x"], mc["y"], mc["z"], mc["r"], mc["buttons"],
            )

        time.sleep(0.05)

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
def connect_pixhawk():
    """Buka link serial + tunggu heartbeat + minta stream. Kembalikan koneksi."""
    global master

    print(f"[MAV] Connecting to Pixhawk on {PIXHAWK_PORT} @ {PIXHAWK_BAUD} ...")
    link = mavutil.mavlink_connection(PIXHAWK_PORT, baud=PIXHAWK_BAUD)

    print("[MAV] Waiting heartbeat...")
    if link.wait_heartbeat(timeout=30) is None:
        raise RuntimeError("tidak ada heartbeat dari Pixhawk dalam 30 detik")

    print("[MAV] Heartbeat received!")
    print(f"[MAV] System {link.target_system}, Component {link.target_component}")

    master = link

    # Minta stream data secara periodik
    try:
        link.mav.request_data_stream_send(
            link.target_system,
            link.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_ALL,
            10,   # 10 Hz
            1
        )
    except Exception as e:
        print("[MAV] request_data_stream_send warning:", e)

    # Request AHRS2 (Depth)
    try:
        link.mav.command_long_send(
            link.target_system,
            link.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            mavutil.mavlink.MAVLINK_MSG_ID_AHRS2,
            100000,      # 10 Hz (100000 µs)
            0, 0, 0, 0, 0
        )
    except Exception as e:
        print("[MAV] AHRS2 request warning:", e)

    return link


def drop_link(reason):
    """Tandai link mati supaya thread sender berhenti mengirim, lalu tutup."""
    global master

    link, master = master, None
    print(f"[MAV] link terputus ({reason}) — mencoba sambung ulang...")

    # Jangan tampilkan status basi di GUI.
    state["armed"] = False
    state["mode"] = "unknown"

    if link is not None:
        try:
            link.close()
        except Exception:
            pass


def main():
    global prev_attitude_ts

    connect_pixhawk()

    # Thread listener command
    threading.Thread(target=command_listener, daemon=True).start()
    threading.Thread(target=joystick_sender, daemon=True).start()
    threading.Thread(target=gripper_sender, daemon=True).start()

    last_send = 0
    last_hb = 0
    last_rx = time.time()

    while True:
        if master is None:
            try:
                connect_pixhawk()
                last_rx = time.time()
            except Exception as e:
                print("[MAV] gagal menyambung ulang:", e)
                time.sleep(2)
            continue

        try:
            msg = master.recv_match(blocking=True, timeout=1)
        except Exception as e:
            drop_link(f"error baca: {e}")
            continue

        now = time.time()

        # Heartbeat GCS 1 Hz — WAJIB, kalau tidak ArduSub disarm sendiri.
        if now - last_hb >= 1.0:
            try:
                send_gcs_heartbeat()
                last_hb = now
            except Exception as e:
                drop_link(f"gagal kirim heartbeat: {e}")
                continue

        if msg is None:
            # Tidak ada satu pun pesan selama LINK_TIMEOUT -> anggap link mati.
            if now - last_rx > LINK_TIMEOUT:
                drop_link("tidak ada data")
            continue

        last_rx = now
        mtype = msg.get_type()

        if mtype == "STATUSTEXT":
            print("[PIXHAWK]", msg.text)

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
            if msg.command == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
                ok = msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
                print(f"[MAV] ARM/DISARM {'diterima' if ok else 'DITOLAK'} "
                      f"(result={msg.result})")
            else:
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

            # Mode yang diminta sudah terkonfirmasi -> tidak perlu ditahan lagi.
            if requested_mode is not None and state["mode"] == requested_mode:
                globals()["requested_mode"] = None

        # Kirim telemetry periodik ke laptop
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
