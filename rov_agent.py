import os
import socket
import json
import time
import threading
import math
from pymavlink import mavutil

from rov_axes import AXIS_NEUTRAL, AXIS_RANGE, clamp_axis, resolve_manual_packet
from rov_modes import depth_hold_allowed, is_risky_mode, resolve_pilot_mode, warning_for_mode
from rov_motor_test import validate_motor_test
from rov_params import (
    coerce_param_value,
    decode_param_id,
    normalize_param_name,
    param_matches,
    param_type_name,
)
from rov_mavlink import RateLimiter, sanitize_fields, stream_still_wanted
from rov_pid import (
    DEFAULT_DEPTH_TARGET,
    clamp_depth_target,
    resolve_pid_writes,
    valid_depth_target,
    valid_pool_depth,
)
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
# Default di bawah ini adalah topologi tether standar (lihat connect_raspi.md).
# Bisa ditimpa lewat environment variable — lihat .env.example.
LAPTOP_IP = os.environ.get("LAPTOP_IP", "192.168.2.1")   # IP laptop / ground station
UDP_TELEM_PORT = int(os.environ.get("UDP_IN", "14551"))  # telemetry ke laptop (sesuai server.js)
UDP_CMD_PORT = int(os.environ.get("UDP_OUT", "14550"))   # command dari laptop ke Pi

# =========================
# Konfigurasi Pixhawk
# =========================
PIXHAWK_PORT = os.environ.get("PIXHAWK_PORT", "/dev/ttyACM0")
PIXHAWK_BAUD = int(os.environ.get("PIXHAWK_BAUD", "115200"))

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
    # Untuk tuning PID depth-hold (halaman Telemetry, ekspor CSV): rata-rata
    # PWM T3/T4/T5 dari SERVO_OUTPUT_RAW, dan P/I/D dari PID_TUNING (axis
    # ACCZ). Tetap 0 kalau FC tidak mengirim pesan itu (mis. PID_TUNING_MASK
    # belum diset) — bukan error, hanya kolom kosong di CSV.
    "thruster_vertical_pwm": 0,
    "pid_p_out": 0.0,
    "pid_i_out": 0.0,
    "pid_d_out": 0.0,
}

master = None

# Gate otoritas dari GUI: "manual" | "autonomous". Sengaja BERBEDA dari pilot
# mode ArduSub di bawah — yang ini menentukan siapa yang boleh memerintah,
# bukan hukum kendali apa yang dipakai wahana.
current_control_mode = "manual"

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

# Setpoint yang dipasang otomatis setiap kali masuk mode depth hold. Nilai awal
# dari rov_pid.DEFAULT_DEPTH_TARGET, bisa diubah operator lewat command
# `depth_default` (halaman Setup) tanpa mengubah kode.
default_depth_target = DEFAULT_DEPTH_TARGET

# Langkah geser setpoint per penekanan D-pad ↑/↓ (atau Arrow ↑/↓ di keyboard).
# 0.05 m dipilih terhadap kolam uji ~0.9 m: cukup halus untuk membidik, dan
# penekanan beruntun (auto-repeat sisi GUI) menutup jarak besar dengan cepat.
DEPTH_STEP = 0.05
depth_lock = threading.Lock()

# Kedalaman kolam uji (meter), dikirim GUI lewat command `pool_depth`.
# None = belum diberi tahu -> depth_target hanya dibatasi di permukaan, persis
# perilaku sebelumnya. Begitu diisi, jadi batas bawah depth_target (lihat
# rov_pid.clamp_depth_target): di kolam 0.9 m, menahan gain_inc tanpa batas ini
# menyetel target belasan meter dan menekan ROV ke dasar tanpa henti.
pool_depth = None

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
    """True hanya di mode yang memang menahan kedalaman (lihat rov_modes.py).

    requested_mode ikut dipakai karena state["mode"] baru ter-update saat
    HEARTBEAT berikutnya datang. Tanpa itu, penekanan gain_inc/gain_dec tepat
    setelah ganti mode akan diabaikan diam-diam.

    ACRO sengaja TIDAK termasuk: di sana throttle netral bukan berarti tahan
    kedalaman, jadi bias depth-hold hanya akan mendorong wahana tanpa umpan
    balik apa pun yang menstabilkannya.
    """
    return depth_hold_allowed(requested_mode or state["mode"])


def apply_depth_hold_bias(mc, axes):
    """Geser MANUAL_CONTROL.z sedikit ke arah depth_target saat ALT_HOLD.

    Hanya berlaku kalau stik heave benar-benar netral — begitu operator
    menyentuh stik, input manual menang mutlak. Di MANUAL/ACRO fungsi ini
    selalu mengembalikan `mc` apa adanya.
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
    "viewer_access",  # buka/tutup akses viewer mobile — murni sisi server/GUI
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

# =========================
# Rotate (pitch gripper)
# =========================
# Sama seperti gripper di atas: target diubah oleh handle_manipulator(),
# rotate_sender() yang menggeser posisi servo perlahan lewat slew_toward()
# supaya motor T-200 pitch tidak menyentak full-speed instan.
ROTATE_SEND_INTERVAL = 0.1      # 10 Hz
ROTATE_SEND_EPSILON = 1.0       # jangan spam MAVLink kalau beda < 1 PWM
ROTATE_MAX_SPEED_PWM_S = 500.0  # bisa disetel beda dari grip kalau perlu
ROTATE_EMA_ALPHA = 0.35

rotate_target = float(SERVO_NEUTRAL)
rotate_filtered = float(SERVO_NEUTRAL)
rotate_lock = threading.Lock()

# =========================
# Parameter FC (halaman Vehicle)
# =========================
# PARAM_VALUE hanya ditangani di loop RX main(). Alasannya sama persis dengan
# COMMAND_ACK: recv_match(blocking=True) di thread command akan memblokir
# thread itu DAN mencuri pesan dari loop utama.
#
# Satu param_request_list ArduSub = ~980 PARAM_VALUE. Mengirimnya satu per satu
# berarti ~980 datagram UDP + ~980 frame WebSocket; dikumpulkan dulu jadi batch
# supaya browser tidak kebanjiran.
PARAM_BATCH_SIZE = 50
PARAM_BATCH_INTERVAL = 0.2      # flush walau batch belum penuh
PARAM_ACK_TIMEOUT = 2.0         # ArduPilot DIAM saat menolak — timeout satu-satunya sinyal gagal

_param_batch = []
_param_batch_ts = 0.0
_param_lock = threading.Lock()

# nama -> (nilai_yang_dikirim, type_id, deadline)
pending_params = {}

# =========================
# Stream MAVLink generik (halaman Analyze)
# =========================
# Mati secara default: menyalakannya berarti setiap message dari MAV_DATA_STREAM_ALL
# ikut dikirim ke GUI. Halaman Analyze menyalakannya saat dibuka dan memperbarui
# permintaan tiap 10 detik; tanpa pembaruan itu stream mati sendiri (lihat
# stream_still_wanted) supaya tab yang ditutup mendadak tidak meninggalkan
# firehose yang tidak ada yang mendengarkan.
mavlink_stream_requested_at = None
_mav_rate = RateLimiter()

_last_telem_log = 0.0

def send_to_gui(obj):
    """Kirim satu pesan JSON ke laptop lewat socket telemetry.

    Aturan envelope (dipakai juga oleh server.js saat merutekan):
      - telemetry  : dict `state` TELANJANG, tanpa field "type"
      - selain itu : selalu punya field "type" (param_batch, param_ack,
                     mavlink_msg, statustext)

    Telemetry sengaja dibiarkan tanpa "type" supaya server lama/baru tetap
    saling kompatibel dan tap perekam Replay tidak ikut berubah.
    """
    try:
        payload = json.dumps(obj, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as e:
        # Satu pesan yang tidak bisa diserialisasi tidak boleh mematikan
        # seluruh loop RX.
        print("[UDP] gagal serialisasi pesan ke GUI:", e)
        return

    try:
        telem_sock.sendto(payload, (LAPTOP_IP, UDP_TELEM_PORT))
    except OSError as e:
        print("[UDP] gagal kirim ke GUI:", e)


def send_telemetry():
    """Kirim state ke laptop 10 Hz, tapi LOG hanya 1 Hz.

    Mencetak tiap paket (10 Hz) plus log joystick 20 Hz membuat stdout yang
    ter-pipe jadi blocking dan menimbulkan jitter pada loop kontrol di Pi.
    """
    global _last_telem_log

    with depth_lock:
        state["depth_target"] = depth_target

    # Dipantulkan supaya operator bisa memastikan wahana benar-benar TAHU
    # kedalaman kolamnya — null berarti jepitan depth_target belum aktif.
    state["pool_depth"] = pool_depth

    # Ikut dipantulkan supaya halaman Setup menampilkan nilai yang benar-benar
    # aktif di wahana, bukan sekadar isi localStorage browser.
    state["depth_default"] = default_depth_target

    send_to_gui(state)

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
    global last_joystick_update
    global gripper_target
    global depth_target
    global requested_mode
    global current_control_mode
    global mavlink_stream_requested_at
    global pool_depth
    global default_depth_target

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

                requested = str(value).lower()

                if requested not in ("manual", "autonomous"):
                    print(f"[CONTROL] Unknown mode: {requested}")
                    continue

                current_control_mode = requested
                print(f"[CONTROL] {current_control_mode}")

            elif name == "pilot_mode":

                # Peta nama GUI -> nama ArduSub ada di rov_modes.py (satu
                # sumber, sudah ada unit test-nya). Nama tak dikenal DITOLAK,
                # tidak di-fallback ke MANUAL.
                pixhawk_mode = resolve_pilot_mode(value)

                if pixhawk_mode is None:
                    print(f"[PILOT] Unknown mode: {value}")
                    continue

                # ACRO tidak ada di semua build/frame ArduSub. mode_mapping()
                # berasal dari firmware yang benar-benar terpasang, jadi ini
                # satu-satunya cek yang bisa dipercaya.
                mode_mapping = master.mode_mapping() or {}

                if pixhawk_mode not in mode_mapping:
                    print(f"[PILOT] {pixhawk_mode} not supported oleh firmware ini")
                    continue

                master.set_mode(mode_mapping[pixhawk_mode])
                requested_mode = pixhawk_mode

                # Hanya mode yang benar-benar menahan kedalaman yang perlu
                # setpoint awal. Di MANUAL/ACRO depth_target tidak dipakai.
                #
                # Setpoint-nya nilai TETAP (default_depth_target), bukan
                # kedalaman saat ini: menekan tombol mode = "turun ke kedalaman
                # kerja", perilaku yang sama tiap kali. Konsekuensinya kalau
                # ditekan saat mengapung di permukaan wahana langsung menyelam —
                # itu memang yang diinginkan, tapi berarti tombol ini tidak
                # boleh ditekan di darat.
                if depth_hold_allowed(pixhawk_mode):
                    with depth_lock:
                        # Dijepit: default bisa lebih dalam dari dasar kolam
                        # kalau pool_depth diperkecil belakangan, dan target di
                        # luar batas membuat bias throttle menekan ke bawah
                        # tanpa henti.
                        depth_target = clamp_depth_target(default_depth_target, pool_depth)
                    print(f"[DEPTH] Target initialized = {depth_target:.2f} m")

                print("====================================")
                print(f" PILOT MODE : {pixhawk_mode}")
                if is_risky_mode(pixhawk_mode):
                    msg = warning_for_mode(pixhawk_mode)
                    if msg:
                        print(f" !! {msg}")
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
                    depth_target = clamp_depth_target(depth_target + step, pool_depth)
                    shown = depth_target
                print(f"[DEPTH] Target = {shown:.2f} m")

            elif name == "pool_depth":

                # Kedalaman kolam uji. Bukan cuma catatan: jadi batas bawah
                # depth_target (lihat rov_pid.clamp_depth_target).
                depth_m = valid_pool_depth(value)
                if depth_m is None:
                    print(f"[POOL] nilai pool_depth tidak valid: {value!r}")
                    continue

                pool_depth = depth_m

                # Jepit ULANG target yang sedang berjalan. Kalau operator
                # memperkecil pool depth saat target sudah lebih dalam, target
                # harus ikut turun sekarang — bukan menunggu penekanan
                # gain_inc/gain_dec berikutnya.
                with depth_lock:
                    depth_target = clamp_depth_target(depth_target, pool_depth)
                    shown = depth_target
                print(f"[POOL] Kedalaman kolam = {pool_depth:.2f} m "
                      f"(target dijepit ke {shown:.2f} m)")

            elif name == "depth_default":

                # Setpoint yang dipasang otomatis saat MASUK depth hold.
                # Sengaja TIDAK menggeser depth_target yang sedang berjalan:
                # mengubah angka di halaman Setup tidak boleh memindahkan
                # wahana yang sedang menyelam. Berlaku pada perpindahan mode
                # berikutnya.
                depth_m = valid_depth_target(value)
                if depth_m is None:
                    print(f"[DEPTH] nilai depth_default tidak valid: {value!r}")
                    continue

                default_depth_target = depth_m
                print(f"[DEPTH] Target default = {default_depth_target:.2f} m "
                      f"(berlaku saat masuk depth hold berikutnya)")

            elif name == "pid":

                # Gain yaw/depth dari Setup -> param ArduSub. Pemetaan dan batas
                # aman ada di rov_pid.py (dengan alasannya); di sini hanya
                # eksekusi + pelaporan.
                writes, rejects = resolve_pid_writes(value)

                for param, reason in rejects:
                    print(f"[PID] DITOLAK {param}: {reason}")
                    send_to_gui({
                        "type": "param_ack",
                        "name": param,
                        "ok": False,
                        "reason": reason,
                    })

                if writes:
                    # Thread terpisah dengan alasan yang sama seperti
                    # thruster_config: loop param_set_send + sleep menahan
                    # listener ini ~0.6 s dan membuat axis/mode ikut tertunda.
                    threading.Thread(
                        target=apply_pid_gains, args=(writes,), daemon=True
                    ).start()

            elif name == "thruster_config":

                # Dijalankan di thread terpisah: loop param_set_send + sleep
                # menahan listener ini ~0.6 s dan membuat axis/mode ikut tertunda.
                motors = msg.get("motors", {})
                print("[DEBUG] Motors received:", motors)
                threading.Thread(
                    target=apply_thruster_config, args=(motors,), daemon=True
                ).start()

            elif name == "motor_test":
                # value = {"motor": 1..6, "throttle": 1-100, "duration": s, "direction": "forward"|"reverse"}
                # Thread terpisah: command_long_send tidak blocking lama, tapi
                # tetap dipisah dari command_listener demi konsistensi pola.
                threading.Thread(
                    target=run_motor_test, args=(value,), daemon=True
                ).start()

            elif name == "param_list":
                # Minta seluruh tabel param FC. Jawabannya ~980 PARAM_VALUE
                # yang ditangani & di-batch di loop RX main().
                with _param_lock:
                    _param_batch.clear()
                print("[PARAM] minta seluruh daftar param")
                master.mav.param_request_list_send(
                    master.target_system,
                    master.target_component,
                )

            elif name == "param_get":
                param = normalize_param_name(value)
                if param is None:
                    print(f"[PARAM] nama param ditolak: {value!r}")
                    continue
                master.mav.param_request_read_send(
                    master.target_system,
                    master.target_component,
                    param.encode("utf-8"),
                    -1,   # -1 = cari berdasarkan nama, bukan indeks
                )

            elif name == "param_set":
                # value = {"name": ..., "value": ..., "type": <MAV_PARAM_TYPE>}
                # Gerbang konfirmasi ada di sisi GUI (halaman Vehicle), sama
                # seperti gerbang ACRO — di sini yang dijaga hanya validitas.
                if not isinstance(value, dict):
                    print(f"[PARAM] payload param_set tidak valid: {value!r}")
                    continue

                try:
                    type_id = int(value.get("type"))
                except (TypeError, ValueError):
                    print(f"[PARAM] tipe param tidak valid: {value.get('type')!r}")
                    continue

                if param_type_name(type_id) is None:
                    print(f"[PARAM] tipe param tak dikenal: {type_id}")
                    continue

                written = set_param(value.get("name"), value.get("value"), type_id)
                if written is None:
                    # Ditolak sebelum menyentuh MAVLink — beri tahu GUI supaya
                    # badge baris tidak menggantung di "pending" sampai timeout.
                    send_to_gui({
                        "type": "param_ack",
                        "name": str(value.get("name")),
                        "ok": False,
                        "reason": "ditolak agent (nama/nilai/tipe tidak valid)",
                    })

            elif name == "mavlink_stream":
                # Halaman Analyze menyalakan ini saat dibuka dan memperbaruinya
                # tiap 10 detik. Yang disimpan TIMESTAMP, bukan boolean, supaya
                # stream mati sendiri kalau GUI berhenti memperbarui.
                if value:
                    was_off = mavlink_stream_requested_at is None
                    mavlink_stream_requested_at = time.time()
                    if was_off:
                        _mav_rate.reset()
                        print("[MAVSTREAM] on")
                else:
                    if mavlink_stream_requested_at is not None:
                        print("[MAVSTREAM] off")
                    mavlink_stream_requested_at = None

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
                # Murni urusan dashboard, tidak ada padanannya di wahana.
                pass

            else:
                print(f"[CMD] unknown command: {name} = {value}")

        except Exception as e:
            print("[CMD] error executing command:", e)

def set_param(name, value, type_id, expect_ack=True):
    """Tulis SATU parameter ke FC. Mengembalikan nama kanoniknya, atau None.

    Primitif tunggal untuk semua penulisan param — dipakai halaman Vehicle
    (`param_set`) maupun Setup → Thruster (`apply_thruster_config`).

    TIDAK menunggu echo di sini: menunggu berarti recv_match() di thread ini,
    yang akan mencuri pesan dari loop RX main() (alasan yang sama seperti ARM,
    lihat command_listener). Verifikasi dilakukan asinkron — nama didaftarkan
    ke pending_params dan diselesaikan handler PARAM_VALUE di main().
    """
    canonical = normalize_param_name(name)
    if canonical is None:
        print(f"[PARAM] nama param ditolak: {name!r}")
        return None

    try:
        numeric = coerce_param_value(value, type_id)
    except ValueError as e:
        print(f"[PARAM] {canonical}: {e}")
        return None

    master.mav.param_set_send(
        master.target_system,
        master.target_component,
        canonical.encode("utf-8"),
        numeric,
        type_id,
    )

    if expect_ack:
        with _param_lock:
            pending_params[canonical] = (numeric, type_id, time.time() + PARAM_ACK_TIMEOUT)

    print(f"[PARAM] {canonical} -> {numeric} ({param_type_name(type_id) or type_id})")
    return canonical


def apply_thruster_config(motors):
    """Tulis MOT_n_DIRECTION satu per satu (perlu jeda antar param_set_send)."""
    try:
        for motor, direction in motors.items():
            set_param(
                f"MOT_{int(motor)}_DIRECTION",
                direction,
                mavutil.mavlink.MAV_PARAM_TYPE_INT8,
            )
            # Jeda ada di pemanggil batch, bukan di set_param(): menulis
            # belasan param beruntun tanpa jeda membuat FC menjatuhkan sebagian.
            time.sleep(0.1)
        print("[PARAM] Thruster configuration updated.")
    except Exception as e:
        print("[PARAM] gagal set thruster config:", e)


def run_motor_test(payload):
    """Putar SATU thruster sebentar lewat MAV_CMD_DO_MOTOR_TEST (ArduSub).

    Dipakai panel "Thruster Test" di halaman Setup untuk memverifikasi arah
    putar satu thruster tanpa menyalakan mixing penuh (MANUAL_CONTROL tidak
    bisa mengisolasi satu motor). Klem/validasi ada di rov_motor_test.py
    (defense in depth) selain klem di sisi GUI.
    """
    motor = payload.get("motor") if isinstance(payload, dict) else None
    try:
        motor, throttle, duration, direction, signed_throttle = validate_motor_test(payload)

        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST,
            0,
            motor,
            mavutil.mavlink.MOTOR_TEST_THROTTLE_PERCENT,
            signed_throttle,
            duration,
            0, 0, 0,
        )
        print(f"[MOTORTEST] motor {motor} {direction} {throttle}% selama {duration}s")
        send_to_gui({
            "type": "motor_test_ack",
            "motor": motor,
            "direction": direction,
            "ok": True,
        })
    except Exception as e:
        print("[MOTORTEST] gagal:", e)
        send_to_gui({
            "type": "motor_test_ack",
            "motor": motor,
            "ok": False,
            "reason": str(e),
        })


def apply_pid_gains(writes):
    """Tulis gain PID yang sudah lolos validasi rov_pid.resolve_pid_writes().

    Bentuknya sengaja kembar dengan apply_thruster_config(): primitif yang sama
    (set_param), jeda yang sama antar tulis, dan verifikasi echo yang sama.
    """
    try:
        for param, value, type_id in writes:
            set_param(param, value, type_id)
            # Jeda ada di pemanggil batch, bukan di set_param(): menulis enam
            # param beruntun tanpa jeda membuat FC menjatuhkan sebagian.
            time.sleep(0.1)
        print(f"[PID] {len(writes)} gain dikirim ke FC.")
    except Exception as e:
        print("[PID] gagal set gain:", e)


def handle_param_value(msg, now):
    """Tangani satu PARAM_VALUE: masukkan ke batch + selesaikan pending set.

    Dipanggil HANYA dari loop RX main().
    """
    name = decode_param_id(msg.param_id)
    if not name:
        return

    entry = {
        "name": name,
        "value": float(msg.param_value),
        "ptype": int(msg.param_type),
        "index": int(msg.param_index),
        "count": int(msg.param_count),
    }

    with _param_lock:
        _param_batch.append(entry)
        pending = pending_params.pop(name, None)

    if pending is not None:
        sent_value, type_id, _deadline = pending
        ok = param_matches(sent_value, entry["value"], type_id)
        send_to_gui({
            "type": "param_ack",
            "name": name,
            "ok": ok,
            "value": entry["value"],
            "reason": None if ok else "FC mengembalikan nilai berbeda",
        })
        print(f"[PARAM] ACK {name} = {entry['value']} ({'ok' if ok else 'BEDA'})")


def flush_param_batch(now, force=False):
    """Kirim batch PARAM_VALUE yang terkumpul ke GUI.

    param_index == param_count - 1 menandai param terakhir dari satu
    param_request_list; itu yang dipakai GUI untuk menutup progress bar.
    """
    global _param_batch_ts

    with _param_lock:
        if not _param_batch:
            _param_batch_ts = now
            return

        penuh = len(_param_batch) >= PARAM_BATCH_SIZE
        kedaluwarsa = (now - _param_batch_ts) >= PARAM_BATCH_INTERVAL

        # Param terakhir dari satu daftar penuh harus langsung dikirim, jangan
        # menunggu batch penuh yang tidak akan pernah datang.
        terakhir = any(
            e["count"] > 0 and e["index"] == e["count"] - 1 for e in _param_batch
        )

        if not (force or penuh or kedaluwarsa or terakhir):
            return

        payload = list(_param_batch)
        _param_batch.clear()
        _param_batch_ts = now

    send_to_gui({"type": "param_batch", "params": payload, "done": terakhir})


def expire_pending_params(now):
    """Laporkan param_set yang tidak pernah di-echo balik sebagai GAGAL.

    ArduPilot tidak mengirim NACK saat menolak param (nama tak dikenal, nilai
    di luar rentang, param read-only) — ia hanya DIAM. Timeout adalah
    satu-satunya cara operator tahu tulisannya tidak masuk.
    """
    with _param_lock:
        basi = [n for n, (_v, _t, deadline) in pending_params.items() if now > deadline]
        for n in basi:
            pending_params.pop(n, None)

    for n in basi:
        send_to_gui({
            "type": "param_ack",
            "name": n,
            "ok": False,
            "reason": "tidak ada balasan dari FC (ditolak atau tidak sampai)",
        })
        print(f"[PARAM] ACK {n} TIMEOUT — kemungkinan ditolak FC")


def maybe_stream_mavlink(msg, mtype, now):
    """Teruskan satu message MAVLink mentah ke halaman Analyze (ter-throttle).

    Dipanggil untuk SETIAP message, jadi jalur cepatnya (stream mati) harus
    murah — cek boolean dulu sebelum apa pun yang lain.
    """
    global mavlink_stream_requested_at

    if mavlink_stream_requested_at is None:
        return

    if not stream_still_wanted(mavlink_stream_requested_at, now):
        mavlink_stream_requested_at = None
        print("[MAVSTREAM] off (GUI berhenti memperbarui permintaan)")
        return

    if not _mav_rate.allow(mtype, now):
        return

    try:
        fields = sanitize_fields(msg.to_dict())
    except Exception as e:
        print(f"[MAVSTREAM] gagal serialisasi {mtype}: {e}")
        return

    send_to_gui({"type": "mavlink_msg", "msg": mtype, "t": now, "fields": fields})


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

        global rotate_target

        with rotate_lock:
            if action == "start" and direction == "left":
                rotate_target = ROTATE_LEFT_PWM

            elif action == "start" and direction == "right":
                rotate_target = ROTATE_RIGHT_PWM

            elif action == "stop":
                rotate_target = SERVO_NEUTRAL

def joystick_sender():
    """Kirim MANUAL_CONTROL 20 Hz.

    PENTING: konversi axis -> field MAVLink WAJIB lewat axes_to_manual_control()
    (rov_axes.py). Konvensi GUI adalah -1000..1000 dengan 0 = diam untuk KEEMPAT
    axis, sedangkan ArduSub mengharapkan z pada 0..1000 dengan 500 = netral.
    Mengirim heave mentah sebagai z membuat "diam" berarti MENYELAM PENUH —
    termasuk saat E-Stop dan saat link GUI putus.
    """
    while True:
        # Link Pixhawk sedang putus/menyambung ulang — tidak ada tujuan kirim.
        if master is None:
            time.sleep(JOYSTICK_SEND_INTERVAL)
            continue

        with joystick_lock:
            axes = dict(joystick)
            last_update = last_joystick_update

        # Fail-safe: GUI diam terlalu lama (crash / joystick dicabut / link
        # putus) -> tahan posisi netral, jangan ulangi input terakhir.
        mc, stale = resolve_manual_packet(axes, last_update, time.time())

        # Beri tahu dashboard bahwa yang mengalir sekarang adalah netral buatan
        # Pi, bukan perintah operator.
        state["cmd_link"] = "stale" if stale else "ok"

        # Saat stale, axes yang dipakai juga harus netral supaya bias
        # depth-hold tidak dihitung dari input basi.
        mc = apply_depth_hold_bias(mc, AXIS_NEUTRAL if stale else axes)

        try:
            master.mav.manual_control_send(
                master.target_system,
                mc["x"], mc["y"], mc["z"], mc["r"], mc["buttons"],
            )
        except Exception as e:
            print("[JOY] gagal kirim MANUAL_CONTROL:", e)

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

def rotate_sender():
    """Gerakkan servo pitch gripper menuju target 10 Hz dgn rate-limit + EMA.

    Pola sama persis dengan gripper_sender(): perintah dari GUI hanya
    mengubah rotate_target, thread ini yang menggeser posisi servo sedikit
    demi sedikit lewat rov_gripper.slew_toward(), supaya motor T-200 pitch
    tidak menyentak full-speed instan seperti sebelumnya.
    """
    global rotate_filtered

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

        with rotate_lock:
            target = rotate_target

        rotate_filtered = slew_toward(
            rotate_filtered, target, dt,
            max_speed=ROTATE_MAX_SPEED_PWM_S, ema_alpha=ROTATE_EMA_ALPHA,
        )

        # Hanya kirim kalau posisi benar-benar berubah — hindari membanjiri
        # link serial 115200 yang dipakai bersama telemetry.
        if last_sent_pwm is None or abs(rotate_filtered - last_sent_pwm) >= ROTATE_SEND_EPSILON:
            pwm = int(round(rotate_filtered))
            try:
                master.mav.command_long_send(
                    master.target_system,
                    master.target_component,
                    mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
                    0,
                    ROTATE_CHANNEL,
                    pwm,
                    0, 0, 0, 0, 0
                )
                last_sent_pwm = rotate_filtered
            except Exception as e:
                print("[ROTATE] gagal kirim:", e)

        time.sleep(ROTATE_SEND_INTERVAL)

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

    # Request SERVO_OUTPUT_RAW (PWM thruster, untuk CSV tuning depth-hold)
    try:
        link.mav.command_long_send(
            link.target_system,
            link.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            mavutil.mavlink.MAVLINK_MSG_ID_SERVO_OUTPUT_RAW,
            100000,      # 10 Hz (100000 µs)
            0, 0, 0, 0, 0
        )
    except Exception as e:
        print("[MAV] SERVO_OUTPUT_RAW request warning:", e)

    # Request PID_TUNING (P/I/D depth-hold, untuk CSV tuning). Hanya benar-benar
    # mengalir kalau parameter PID_TUNING_MASK di FC sudah menyalakan bit ACCZ —
    # kalau belum, permintaan ini diterima tapi FC tetap diam, dan kolom
    # pid_*_out di CSV akan tetap 0.0 (bukan error).
    try:
        link.mav.command_long_send(
            link.target_system,
            link.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            mavutil.mavlink.MAVLINK_MSG_ID_PID_TUNING,
            100000,      # 10 Hz (100000 µs)
            0, 0, 0, 0, 0
        )
    except Exception as e:
        print("[MAV] PID_TUNING request warning:", e)

    return link


def drop_link(reason):
    """Tandai link mati supaya thread sender berhenti mengirim, lalu tutup."""
    global master

    link, master = master, None
    print(f"[MAV] link terputus ({reason}) — mencoba sambung ulang...")

    # Jangan tampilkan status basi di GUI.
    state["armed"] = False
    state["mode"] = "unknown"

    # Daftar param yang setengah terkirim tidak boleh disambung ke daftar
    # berikutnya, dan throttle Inspector harus lupa timestamp lama supaya
    # message pertama setelah sambung ulang tidak tertahan.
    with _param_lock:
        _param_batch.clear()
        basi = list(pending_params)
        pending_params.clear()
    _mav_rate.reset()

    for n in basi:
        send_to_gui({
            "type": "param_ack",
            "name": n,
            "ok": False,
            "reason": "link MAVLink terputus sebelum konfirmasi",
        })

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
    threading.Thread(target=rotate_sender, daemon=True).start()

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

        # Batch param & timeout ACK diurus SEBELUM cabang "msg is None".
        # Justru saat tidak ada pesan masuk-lah pending param harus kedaluwarsa:
        # kalau ditaruh di ekor loop, `continue` di bawah membuatnya tidak
        # pernah jalan persis ketika FC berhenti menjawab.
        flush_param_batch(now)
        expire_pending_params(now)

        if msg is None:
            # Tidak ada satu pun pesan selama LINK_TIMEOUT -> anggap link mati.
            if now - last_rx > LINK_TIMEOUT:
                drop_link("tidak ada data")
            continue

        last_rx = now
        mtype = msg.get_type()

        # MAVLink Inspector (halaman Analyze). Sengaja PALING ATAS supaya
        # Inspector melihat message apa adanya — termasuk yang tidak punya
        # handler khusus di bawah, yang justru paling berguna saat diagnosa.
        maybe_stream_mavlink(msg, mtype, now)

        if mtype == "STATUSTEXT":
            text = msg.text.decode("utf-8", errors="replace") if isinstance(
                msg.text, (bytes, bytearray)
            ) else str(msg.text)
            text = text.split("\x00", 1)[0]
            print("[PIXHAWK]", text)
            # Diteruskan ke console dashboard: ini cara FC melaporkan penolakan
            # param & error pre-arm, yang tanpa ini hanya terlihat di stdout Pi.
            send_to_gui({
                "type": "statustext",
                "text": text,
                "severity": int(getattr(msg, "severity", 6)),
            })

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
        # PARAM_VALUE: tabel param (halaman Vehicle) + verifikasi param_set.
        # Di sini, bukan di command_listener, dengan alasan yang sama seperti
        # COMMAND_ACK di bawah.
        # --------------------------------
        elif mtype == "PARAM_VALUE":
            handle_param_value(msg, now)

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
            elif msg.command == mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST:
                ok = msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
                print(f"[MAV] DO_MOTOR_TEST {'diterima' if ok else 'DITOLAK'} "
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
        # SERVO_OUTPUT_RAW: PWM thruster vertikal (T3/T4/T5 = heave)
        # untuk CSV tuning depth-hold di halaman Telemetry.
        # --------------------------------
        elif mtype == "SERVO_OUTPUT_RAW":
            vals = [v for v in (msg.servo3_raw, msg.servo4_raw, msg.servo5_raw) if v]
            if vals:
                state["thruster_vertical_pwm"] = int(sum(vals) / len(vals))

        # --------------------------------
        # PID_TUNING: P/I/D depth-hold (axis ACCZ = PSC_ACCZ, controller
        # vertikal ArduSub) untuk CSV tuning. Diam kalau PID_TUNING_MASK
        # belum menyalakan bit ACCZ di FC — bukan error, state tetap 0.0.
        # --------------------------------
        elif mtype == "PID_TUNING":
            if getattr(msg, "axis", None) == mavutil.mavlink.PID_TUNING_ACCZ:
                state["pid_p_out"] = float(msg.P)
                state["pid_i_out"] = float(msg.I)
                state["pid_d_out"] = float(msg.D)

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
