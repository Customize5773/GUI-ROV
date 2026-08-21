import os
import socket
import json
import time
import threading
import math
from pymavlink import mavutil

from rov_axes import (
    AXIS_NEUTRAL,
    AXIS_RANGE,
    axes_to_manual_control,
    clamp_axis,
    resolve_manual_packet,
)

from rov_modes import (
    depth_bias_engaged,
    depth_hold_allowed,
    is_poshold_request,
    poshold_mode_ok,
    resolve_pilot_mode,
)
from rov_heading import heading_bias, operator_holding_yaw
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
    DEPTH_BIAS_ENGAGE,
    DEPTH_BIAS_LIMIT,
    DEPTH_BIAS_MAX_CORRECTION,
    DEPTH_BIAS_RELEASE,
    clamp_depth_target,
    depth_bias_active,
    depth_hold_bias,
    resolve_pid_writes,
    smooth_depth,
    valid_pool_depth,
)

from attitude_filter import AttitudeFilter
from gripper_controller import GripperController

# =========================
# Konfigurasi jaringan
# =========================
# Default di bawah ini adalah topologi tether standar (lihat connect_raspi.md).
# Bisa ditimpa lewat environment variable — lihat .env.example.
LAPTOP_IP = os.environ.get("LAPTOP_IP", "192.168.2.1")   # IP laptop / ground station
UDP_TELEM_PORT = int(os.environ.get("UDP_IN", "14551"))  # telemetry ke laptop (sesuai server.js)
UDP_CMD_PORT = int(os.environ.get("UDP_OUT", "14550"))   # command dari laptop ke Pi

# Jembatan QGroundControl: semua MAVLink dari Pixhawk diteruskan ke QGC, dan
# perintah dari QGC diteruskan balik ke Pixhawk. Terpisah dari port telemetri
# dashboard (14551) supaya keduanya bisa hidup bersamaan.
#
# Kode ini SEMPAT hanya ada di salinan Pi dan hilang saat deploy 19 Agu 2026 —
# dimasukkan ke git supaya tidak terulang.
QGC_IP = os.environ.get("QGC_IP", "192.168.2.1")
QGC_PORT = int(os.environ.get("QGC_PORT", "14561"))       # MAVLink Pi -> QGC
QGC_IN_PORT = int(os.environ.get("QGC_IN_PORT", "14560"))  # MAVLink QGC -> Pi

# =========================
# Konfigurasi Pixhawk
# =========================
PIXHAWK_PORT = os.environ.get("PIXHAWK_PORT", "/dev/ttyACM0")
PIXHAWK_BAUD = int(os.environ.get("PIXHAWK_BAUD", "115200"))

# Tidak ada satu pun pesan MAVLink selama ini -> link dianggap mati dan
# disambungkan ulang (USB lepas / Pixhawk re-enumerate).
LINK_TIMEOUT = 3.0
thruster_gain = 1.0

# =========================
# Socket UDP
# =========================
telem_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
cmd_sock.bind(("0.0.0.0", UDP_CMD_PORT))
cmd_sock.settimeout(0.2)

qgc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
qgc_addr = (QGC_IP, QGC_PORT)


def forward_mavlink_to_qgc(msg):
    """Teruskan satu pesan MAVLink mentah ke QGC. Kegagalan TIDAK boleh
    menghentikan loop RX: QGC cuma pemantau, wahana harus tetap jalan tanpanya."""
    try:
        data = msg.get_msgbuf()
        if data:
            qgc_sock.sendto(data, qgc_addr)
    except Exception as e:
        print(f"[QGC] MAVLink forward error: {e}")


def qgc_command_receiver():
    """Terima MAVLink dari QGC lalu teruskan apa adanya ke Pixhawk.

    Sengaja tanpa filter: QGC dipakai untuk kalibrasi/param/uji motor, dan
    memilah pesan mana yang boleh lewat berarti memelihara daftar putih yang
    pasti ketinggalan. Konsekuensinya QGC punya otoritas penuh atas wahana —
    perlakukan seperti GCS kedua, bukan alat pantau pasif.
    """
    qgc_in = mavutil.mavlink_connection(f"udpin:0.0.0.0:{QGC_IN_PORT}")

    print(f"[QGC] Listening MAVLink commands on UDP :{QGC_IN_PORT}")

    while True:
        try:
            msg = qgc_in.recv_match(blocking=True, timeout=1)

            if msg is None:
                continue

            if msg.get_type() == "MANUAL_CONTROL":
                print(
                    f"[QGC RX] MANUAL_CONTROL "
                    f"x={msg.x} y={msg.y} z={msg.z} r={msg.r}"
                )
            else:
                print(f"[QGC RX] {msg.get_type()}")

            if master is None:
                continue
            with master_lock:
                master.mav.send(msg)

        except Exception as e:
            print(f"[QGC] command receive error: {e}")
            time.sleep(1)

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
    "mode": "manual",
    # Gate otoritas GUI ("manual"|"autonomous"). SENGAJA kunci sendiri, BUKAN
    # "mode" di atas — yang itu pilot mode ArduSub dari HEARTBEAT. Pernah
    # ditimpa di send_telemetry() dan itu mematikan seluruh gerbang depth-hold.
    "control_mode": "manual",
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
    # PWM per-thruster horizontal, TIDAK dirata-rata: T6 adalah satu-satunya
    # thruster lateral, dan di frame BlueROV1 ArduSub ikut memakainya untuk ROLL
    # (faktor roll -0,25, lihat CONTROL-MAPPING.md). Jadi koreksi roll bocor
    # keluar sebagai gaya menyamping, dan T6 sendirian adalah satu-satunya
    # pengukuran yang memisahkan "FC memerintahkan dorongan lateral" dari
    # "air/tether mendorong lambung". Merata-ratakannya dengan T1/T2 akan
    # menghapus persis angka yang dicari.
    "thruster_lateral_pwm": 0,
    "thruster_surge_pwm": [0, 0],
    "pid_p_out": 0.0,
    "pid_i_out": 0.0,
    "pid_d_out": 0.0,
    # Sama seperti di atas tapi axis ROLL/PITCH — lihat handler PID_TUNING.
    # Butuh GCS_PID_MASK menyalakan bit roll/pitch di FC (bukan cuma ACCZ).
    "pid_roll_p_out": 0.0,
    "pid_roll_i_out": 0.0,
    "pid_roll_d_out": 0.0,
    "pid_pitch_p_out": 0.0,
    "pid_pitch_i_out": 0.0,
    "pid_pitch_d_out": 0.0,
    # Posisi lokal dari EKF ArduSub (LOCAL_POSITION_NED, meter, utara/timur+).
    # None selama pesan itu belum pernah diterima, supaya frontend bisa
    # membedakan "belum ada data" dari "posisi 0,0" dan jatuh ke fallback.
    "pos_n": None,
    "pos_e": None,
}

master = None
gripper = None
# Melindungi SEMUA akses I/O ke `master` (send & recv) — port serial dipakai
# bersama oleh reader thread (main) dan beberapa sender thread (joystick,
# gripper, rotate, command_listener). Tanpa lock ini, dua thread bisa
# menulis ke fd yang sama bersamaan dan merusak frame MAVLink di tengah
# jalan, yang berujung pada drop_link() / mode tidak terkonfirmasi.
master_lock = threading.Lock()

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

# Setpoint kedalaman, meter, positif ke bawah. None = BELUM PERNAH DI-SET.
#
# None sengaja dibedakan dari 0.0: 0.0 adalah setpoint yang sah ("tahan di
# permukaan"), jadi memakai 0.0 sebagai "belum ada" membuat depth-set tidak
# bisa membedakan operator yang menekan SET tepat di permukaan dari operator
# yang belum menekan SET sama sekali — yang pertama harus menahan, yang kedua
# tidak boleh mengirim bias apa pun.
depth_target = None

# Saklar depth-set dari operator (tombol ON/OFF di GUI atau gamepad). Depth-set
# TIDAK pernah menyala sendiri: masuk ALT_HOLD/POSHOLD tidak menyentuhnya.
# Lihat apply_depth_hold_bias() untuk gerbang lengkapnya.
depth_hold_enabled = False

# Offset tare permukaan (meter), diset lewat command `set_surface`. state["depth"]
# dihitung sebagai `_raw_depth - depth_offset` (lihat handler AHRS2) supaya
# depth 0 operator = permukaan sungguhan, bukan origin baro/EKF mentah yang
# bisa drift atau tidak pas 0 saat ROV di-arm. TANPA ini, apply_depth_hold_bias()
# menghitung error dari depth mentah sementara GUI menampilkan angka yang sudah
# ditare secara kosmetik — bias yang dikirim ke thruster bisa jauh dari nol
# padahal GUI terlihat "Depth = 0.00 m, aman".
depth_offset = 0.0

# Depth mentah (belum ditare) dari AHRS2 terakhir, diisi di handler AHRS2.
# Dipakai saat command `set_surface` datang: offset yang disimpan harus nilai
# mentah SAAT ITU, bukan state["depth"] yang sudah ditare sebelumnya.
_raw_depth = 0.0

depth_lock = threading.Lock()

# Buffer sampel kedalaman untuk smoothing saat SET. Baro bergetar ±0.02–0.05 m,
# dan SET yang merekam satu sampel bisa meleset. Rata-rata 1 detik membersihkan
# noise itu. Diisi setiap kali AHRS2 datang, dibaca + direset saat SET ditekan.
# Ringkas: sampel di sini hanya 10 poin terakhir (0.1 s @ 10 Hz), cukup untuk
# smoothing. Buffer penuh 1 detik tidak perlu — depth hold bisa mulai bekerja
# segera sesudah SET.
_depth_samples = []
_DEPTH_SAMPLE_BUFFER_SIZE = 10  # 0.1 s @ 10 Hz

# State histeresis untuk depth_hold_bias(). Diperbarui tiap kali bias dihitung.
# Perlu memisahkan "bias sedang mengalir?" dari "bias boleh mengalir untuk error
# ini?" supaya histeresis bekerja.
_bias_active = False

# EMA depth untuk estimasi laju (rate) — dipakai meredam bias saat vehicle
# sudah bergerak cepat menuju target, lihat DEPTH_BIAS_DAMPING di rov_pid.py.
# Alpha lebih rendah dari smooth_depth() (0,5-0,7): turunan memperkuat noise
# baro lebih dari posisi mentah, meniru pola AttitudeFilter (attitude_filter.py).
_DEPTH_RATE_ALPHA = 0.3
_depth_ema = None
_depth_ema_ts = None

# Throttle log diagnostik depth-hold ke 1 Hz — sama seperti _last_telem_log.
# Ditambahkan setelah trial 15 Agu 2026 menunjukkan bias TERHITUNG besar
# (z~660) tapi PWM thruster vertikal nyaris tidak bergerak saat surge/yaw
# aktif bersamaan. Log ini menjawab langsung: apakah z yang dihitung memang
# yang dikirim, dan apakah sempat mentok DEPTH_BIAS_LIMIT — tanpa itu,
# "depth-hold tidak respons" cuma bisa direkonstruksi manual dari state SEND.
_last_depth_diag = 0.0

# Tombol SET (dan toggle ON/OFF) sifatnya sekali-pencet, tapi backend tidak
# boleh percaya penuh pada klien: gamepad yang ter-bounce atau klien nakal bisa
# mengirim puluhan `depth_set` per detik dan membanjiri log + event GUI. 2 Hz
# jauh di atas kecepatan jempol manusia, jadi tidak pernah terasa oleh operator.
_DEPTH_CMD_RATE_HZ = 2.0
_depth_cmd_rate = RateLimiter(_DEPTH_CMD_RATE_HZ)

# manual_control_send() gagal di 20 Hz -> tanpa throttle satu link serial
# yang goyah bisa membanjiri GUI dengan event beruntun. 1 Hz cukup untuk
# operator tahu ada masalah tanpa firehose.
_joy_send_err_rate = RateLimiter(1.0)

# Kedalaman kolam uji (meter), dikirim GUI lewat command `pool_depth`.
# None = belum diberi tahu -> depth_target hanya dibatasi di permukaan, persis
# perilaku sebelumnya. Begitu diisi, jadi batas bawah depth_target (lihat
# rov_pid.clamp_depth_target): menekan SET di dekat dasar kolam yang keruh bisa
# merekam pembacaan baro yang meleset dan menekan ROV ke dasar tanpa henti.
pool_depth = None

# Mode yang TERAKHIR DIMINTA lewat set_mode. state["mode"] hanya ter-update saat
# HEARTBEAT datang, jadi tanpa ini depth-set yang di-ON-kan tepat setelah ganti
# mode akan diam saja selama satu-dua tick pertama.
requested_mode = None

# Kapan requested_mode di-set (time.time()). Kalau FC MENOLAK perpindahan mode
# (pre-arm check gagal, EKF belum siap, dsb.) HEARTBEAT tidak pernah membawa
# state["mode"] == requested_mode, jadi tanpa batas waktu requested_mode akan
# dipercaya SELAMANYA dan depth-set terus mengirim bias walau wahana
# sebenarnya masih di mode lama. REQUESTED_MODE_TIMEOUT membatasi jendela
# percaya-optimistis ini; sesudahnya depth_hold_mode_ok() jatuh balik ke
# state["mode"] yang benar-benar terkonfirmasi.
requested_mode_ts = 0.0
REQUESTED_MODE_TIMEOUT = 3.0

HEAVE_MANUAL_EPSILON = 20 # |heave| di atas ini dianggap operator sedang memegang stik

def _effective_requested_mode():
    """requested_mode kalau masih dalam jendela REQUESTED_MODE_TIMEOUT, else None.

    Tanpa expiry ini, mode yang DITOLAK firmware (pre-arm check gagal, EKF
    belum siap) membuat requested_mode tersangkut selamanya karena
    state["mode"] tidak akan pernah menyusulnya lewat HEARTBEAT.
    """
    if requested_mode is None:
        return None
    if time.time() - requested_mode_ts > REQUESTED_MODE_TIMEOUT:
        return None
    return requested_mode


def _current_pixhawk_mode():
    """Mode ArduSub efektif saat ini: requested_mode selama belum kedaluwarsa
    (lihat _effective_requested_mode), else state["mode"] yang benar-benar
    terkonfirmasi HEARTBEAT. Tanpa fallback requested_mode, sesuatu yang
    di-ON-kan tepat setelah ganti mode akan diam selama satu-dua tick pertama.
    """
    return _effective_requested_mode() or state["mode"]


def depth_hold_mode_ok():
    """True hanya di mode yang memang menahan kedalaman (lihat rov_modes.py).

    HANYA soal mode — bukan "depth-set sedang aktif". Saklar operator ada di
    depth_hold_enabled, dan keduanya sengaja terpisah: mode tidak pernah
    menyalakan depth-set, depth-set tidak pernah memindahkan mode.

    MANUAL/ALT_HOLD/POSHOLD sengaja TIDAK termasuk — lihat DEPTH_HOLD_MODES
    di rov_modes.py untuk mode mana yang jadi syaratnya sekarang.
    """
    return depth_hold_allowed(_current_pixhawk_mode())


def apply_depth_hold_bias(mc, axes):
    """Geser MANUAL_CONTROL.z ke arah depth_target saat depth-set aktif.

    Empat gerbang, semuanya harus terbuka (lihat depth_bias_engaged di
    rov_modes.py untuk aturannya dalam bentuk yang bisa diuji):
      1. operator sudah menekan SET  -> depth_target bukan None
      2. operator sudah meng-ON-kan  -> depth_hold_enabled
      3. mode ArduSub ada di DEPTH_HOLD_MODES (rov_modes.py)
      4. stik heave netral           -> begitu operator menyentuh stik, input
                                        manual menang mutlak
    """
    global _bias_active, _depth_ema, _depth_ema_ts, _last_depth_diag

    with depth_lock:
        target = depth_target
        enabled = depth_hold_enabled

    if not depth_bias_engaged(
        enabled,
        target,
        _effective_requested_mode() or state["mode"],
        axes.get("heave", 0),
        HEAVE_MANUAL_EPSILON,
    ):
        _bias_active = False
        # Buang EMA saat gerbang tertutup: begitu bias hidup lagi nanti
        # (mis. depth-set di-ON-kan ulang), rate tidak boleh dihitung dari
        # depth yang mungkin sudah lama dan sangat berbeda.
        _depth_ema = None
        _depth_ema_ts = None
        return mc

    now = time.time()
    depth_now = state["depth"]

    # EMA + turunan waktu untuk closing_rate (lihat DEPTH_BIAS_DAMPING di
    # rov_pid.py). Sampel pertama sejak gerbang terbuka: seed EMA, rate=0 —
    # tidak ada dt yang valid untuk diturunkan.
    closing_rate = 0.0
    if _depth_ema is None:
        _depth_ema = depth_now
        _depth_ema_ts = now
    else:
        dt = now - _depth_ema_ts
        ema_lama = _depth_ema
        _depth_ema += _DEPTH_RATE_ALPHA * (depth_now - _depth_ema)
        _depth_ema_ts = now
        if dt > 0:
            rate = (_depth_ema - ema_lama) / dt
            err_lama = target - ema_lama
            # Mendekat = |error| mengecil = closing_rate POSITIF, terlepas
            # dari arah target ada di atas atau di bawah depth sekarang.
            if err_lama > 0:
                closing_rate = rate
            elif err_lama < 0:
                closing_rate = -rate

    # depth & target dalam meter, positif ke bawah. Error positif = perlu turun.
    # HISTERESIS untuk menghindari limit cycle (naik-turun terus di sekitar
    # setpoint). depth_bias_active() memakai _bias_active yang diperbarui di
    # sini: begitu error kecil (< RELEASE) bias berhenti, dan bias-nya hanya
    # menyala kembali saat error membesar (>= ENGAGE). Selisih antara keduanya
    # adalah zona tenang tempat noise baro tidak lagi bisa trigger osilasi.
    _bias_active = depth_bias_active(
        target - state["depth"],
        _bias_active
    )
    error = target - state["depth"]
    bias = depth_hold_bias(error, _bias_active, closing_rate)
    if bias == 0.0:
        # Beda dari "sudah dekat target, tidak perlu koreksi" (senyap, itu
        # normal): ini KHUSUS kasus _bias_active True (histeresis bilang mau
        # mengoreksi) tapi errornya melebihi DEPTH_BIAS_MAX_CORRECTION —
        # operator perlu tahu kenapa depth-hold terlihat diam, bukan menebak
        # tombolnya rusak. Lihat catatan DEPTH_BIAS_MAX_CORRECTION di rov_pid.py.
        if _bias_active and abs(error) > DEPTH_BIAS_MAX_CORRECTION and now - _last_depth_diag >= 1.0:
            _last_depth_diag = now
            print(f"[DEPTH] target {target:.2f} m terlalu jauh (error {error:+.2f} m) — "
                  f"dekati dulu pakai stik, depth-hold cuma menahan trim dekat")
        return mc

    out = dict(mc)
    out["z"] = max(0, min(1000, int(round(mc["z"] - bias))))

    if now - _last_depth_diag >= 1.0:
        _last_depth_diag = now
        mentok = " MENTOK LIMIT" if abs(bias) >= DEPTH_BIAS_LIMIT - 0.5 else ""
        print(f"[DEPTH] diag target={target:.2f} depth={state['depth']:.2f} "
              f"err={target - state['depth']:+.3f} rate={closing_rate:+.3f} "
              f"bias={bias:+.1f} z={out['z']}{mentok}")

    return out


# =========================
# POSHOLD (station-keep)
# =========================
# BUKAN mode POSHOLD firmware ArduSub — itu butuh estimasi posisi horizontal
# dari EKF yang tidak tersedia di bawah air (tidak ada GPS/DVL/optical flow di
# wahana ini). Lihat docstring rov_modes.py.
#
# Yang dilakukan mode ini: ALT_HOLD (kedalaman ditahan cascade PID ArduSub) DITAMBAH
# koreksi heading proporsional dari sisi Pi (rov_heading.py). Yang TIDAK
# dilakukan: menahan posisi x/y. Arus lateral tetap menggeser wahana dan tidak
# ada sensor yang bisa melihatnya.
poshold_active = False

# Heading yang sedang ditahan (derajat). None = belum di-seed; tick berikutnya
# akan mengisinya dari heading saat itu. Sengaja bukan 0.0: 0° adalah heading
# yang sah, jadi tidak bisa dipakai sebagai penanda "belum diisi".
heading_target = None
heading_lock = threading.Lock()


def poshold_engaged():
    """True kalau overlay heading-hold benar-benar boleh menulis ke r.

    Syaratnya dua: operator memang meminta POSHOLD, DAN wahana ada di ALT_HOLD
    (POSHOLD_BASE_MODE di rov_modes.py). Kalau wahana ditarik keluar ALT_HOLD
    lewat saklar RC atau GCS lain, overlay ikut mati sendiri — bukan diam-diam
    terus mengoreksi yaw di MANUAL.

    Gerbangnya poshold_mode_ok(), BUKAN depth_hold_allowed(): yang kedua adalah
    syarat bias depth-set (STABILIZE) dan tidak ada hubungannya dengan mode
    dasar overlay ini.
    """
    return poshold_active and poshold_mode_ok(_current_pixhawk_mode())


def apply_heading_hold(mc, axes):
    """Tambahkan koreksi ke MANUAL_CONTROL.r agar heading tetap di heading_target.

    Aturan mainnya sama persis dengan apply_depth_hold_bias(): operator menang
    mutlak. Begitu stik yaw disentuh, target DIBUANG (bukan sekadar diabaikan)
    supaya saat stik dilepas wahana menahan heading BARU tempat operator
    meninggalkannya, bukan memutar balik ke heading lama.
    """
    global heading_target

    if not poshold_engaged():
        return mc

    if operator_holding_yaw(axes):
        with heading_lock:
            heading_target = None
        return mc

    with heading_lock:
        if heading_target is None:
            # Seed lalu keluar tanpa koreksi: pada tick ini heading masih
            # bergerak sisa dari input operator, jadi mengoreksinya langsung
            # hanya menghasilkan sentakan.
            heading_target = state["heading"]
            print(f"[POSHOLD] Heading target = {heading_target:.1f}°")
            return mc
        target = heading_target

    bias = heading_bias(target, state["heading"])
    if bias == 0:
        return mc

    out = dict(mc)
    out["r"] = max(-1000, min(1000, int(mc["r"]) + bias))
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
    "snapshot",       # tangkapan frame di browser
    "record",         # perekaman di browser
    "viewer_access",  # buka/tutup akses viewer mobile — murni sisi server/GUI
})

# =========================
# Gripper
# =========================

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
        # null = operator belum menekan SET. GUI menampilkannya sebagai "—",
        # bukan 0.00 m, supaya tidak terbaca seolah setpoint permukaan aktif.
        state["depth_target"] = depth_target
        state["depth_hold"] = depth_hold_enabled

    # POSHOLD tidak terlihat di HEARTBEAT (ia berjalan di ALT_HOLD), jadi
    # INILAH satu-satunya cara GUI tahu overlay sedang hidup dan tab mana yang
    # harus menyala. heading_target None = belum di-seed (stik yaw masih
    # dipegang, atau baru saja masuk mode).
    state["poshold"] = poshold_engaged()
    with heading_lock:
        state["heading_target"] = heading_target

    # Dipantulkan supaya operator bisa memastikan wahana benar-benar TAHU
    # kedalaman kolamnya — null berarti jepitan depth_target belum aktif.
    state["pool_depth"] = pool_depth

    # Gate otoritas untuk mission5 FSM (toggle autonomous/manual di GUI).
    # HARUS kunci sendiri: dulu ini menulis ke state["mode"] dan menimpa pilot
    # mode ArduSub dari HEARTBEAT 10x/detik. Akibatnya requested_mode tak pernah
    # terkonfirmasi, dan sesudah REQUESTED_MODE_TIMEOUT semua gerbang
    # depth_hold_allowed() jatuh ke "manual" — depth-set dan overlay POSHOLD
    # berhenti diam-diam persis 3 detik setelah masuk ALT_HOLD.
    # Pola yang sama dipakai autonomy/rov_link.py dan server/server.js.
    state["control_mode"] = current_control_mode
    state["thruster_gain"] = thruster_gain * 100.0
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
    with master_lock:
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
    with master_lock:
        master.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_GCS,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0, 0, 0,
        )

# =========================
# Command handler dari laptop
# =========================
def command_listener():
    global last_joystick_update
    global depth_target
    global requested_mode
    global requested_mode_ts
    global current_control_mode
    global mavlink_stream_requested_at
    global pool_depth
    global depth_offset
    global depth_hold_enabled
    global poshold_active
    global heading_target
    global thruster_gain

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
            if not quiet:
                send_to_gui({
                    "type": "event",
                    "text": f"Command '{name}' ditolak — Pixhawk belum terhubung",
                    "level": "err",
                })
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

                # Tidak semua mode ada di semua build/frame ArduSub.
                # mode_mapping() berasal dari firmware yang benar-benar
                # terpasang, jadi ini satu-satunya cek yang bisa dipercaya.
                mode_mapping = master.mode_mapping() or {}

                if pixhawk_mode not in mode_mapping:
                    print(f"[PILOT] {pixhawk_mode} not supported oleh firmware ini")
                    continue

                previous_requested_mode = requested_mode
                master.set_mode(mode_mapping[pixhawk_mode])
                requested_mode = pixhawk_mode
                requested_mode_ts = time.time()

                # "poshold" dan "depth_hold" sama-sama berujung di ALT_HOLD,
                # jadi pixhawk_mode TIDAK cukup untuk membedakannya — yang
                # menentukan overlay hidup/mati adalah nama yang diminta GUI.
                # Setiap permintaan mode apa pun mematikan overlay lebih dulu,
                # supaya tidak ada jalur yang meninggalkannya menyala.
                poshold_active = is_poshold_request(value)

                # Selalu dibuang, baik masuk maupun keluar POSHOLD: masuk mode
                # ini berarti "tahan heading tempat saya sekarang", bukan
                # heading dari sesi POSHOLD sebelumnya.
                with heading_lock:
                    heading_target = None

                # CATATAN: perpindahan mode sengaja TIDAK menyentuh depth_target
                # maupun depth_hold_enabled. Menuju setpoint tersimpan adalah
                # fitur terpisah (command depth_set / depth_hold) yang di-arm
                # operator sendiri — lihat DEPTH_HOLD_MODES di rov_modes.py
                # untuk mode mana yang jadi syarat bias-nya sekarang.

                if depth_hold_allowed(pixhawk_mode):
                    was_depth_hold = (
                        previous_requested_mode is not None
                        and depth_hold_allowed(previous_requested_mode)
                    )

                    if not was_depth_hold:
                        with depth_lock:
                            depth_target = clamp_depth_target(
                                state["depth"],
                                pool_depth
                            )

                        print(
                            f"[DEPTH] Hold target mengikuti depth saat ini = "
                            f"{depth_target:.2f} m"
                        )
                    else:
                        with depth_lock:
                            depth_target = clamp_depth_target(
                                depth_target,
                                pool_depth
                            )

                        print(
                            f"[DEPTH] Hold target dipertahankan = "
                            f"{depth_target:.2f} m"
                        )
                # Pindah ke depth-hold dengan error BESAR + depth-set ON =
                # penyelaman mendadak. Peringatkan ke operator dan matikan saklar.
                # Ini mencegah kejutan: operator pindah balik dari Manual ke Alt
                # Hold saat wahana di permukaan, saklar masih ON dari kedalaman
                # kerja sebelumnya (0.5 m) → bias langsung dorong penuh tanpa
                # orang menekan apa pun.
                if (
                    depth_hold_mode_ok() and
                    depth_hold_enabled and
                    depth_target is not None and
                    abs(depth_target - state["depth"]) > 0.3
                ):
                    with depth_lock:
                        depth_hold_enabled = False
                    send_to_gui({
                        "type": "event",
                        "text": (
                            f"Depth-set OFF — error {abs(depth_target - state['depth']):.2f} m "
                            f"terlalu besar, tahan dulu dengan stik"
                        ),
                        "level": "warn",
                    })
                    print(
                        f"[DEPTH] Depth-set OFF — error besar saat pindah "
                        f"ke depth-hold"
                    )

                print("====================================")
                print(f" PILOT MODE : {pixhawk_mode}")
                if poshold_active:
                    print(" POSHOLD    : heading hold AKTIF (posisi x/y TIDAK ditahan)")
                print("====================================")

            elif name == "stop":
                # Failsafe sederhana: netralkan axis lalu disarm
                print("[MAV] STOP -> DISARM")
                with joystick_lock:
                    joystick.update(AXIS_NEUTRAL)

                # E-Stop tidak boleh meninggalkan overlay yang masih menulis ke
                # r: axis dinetralkan di sini, dan koreksi heading akan
                # mengisinya kembali pada tick berikutnya.
                poshold_active = False
                with heading_lock:
                    heading_target = None
                # Depth-set juga dimatikan: E-Stop berarti operator ingin semua
                # yang menulis ke thruster berhenti, dan setelah re-arm wahana
                # tidak boleh langsung berenang sendiri ke setpoint lama.
                # depth_target sengaja DIPERTAHANKAN — kedalaman kerja yang
                # sudah direkam masih berguna, operator tinggal menekan ON lagi.
                with depth_lock:
                    depth_hold_enabled = False
                send_arm_disarm(False)

            elif name == "light":
                # Belum dihubungkan ke hardware lampu, simpan status saja
                state["light"] = bool(value)
                print(f"[LIGHT] set to {state['light']}")

            elif name == "set_surface":

                # Tare permukaan HARUS di backend, bukan cuma tampilan GUI:
                # apply_depth_hold_bias() menghitung error dari state["depth"],
                # jadi kalau tare hanya kosmetik di browser, bias yang benar-
                # benar dikirim ke thruster dihitung dari depth mentah yang
                # bisa jauh dari nol walau GUI menampilkan "Depth = 0.00 m".
                depth_offset = _raw_depth
                print(f"[DEPTH] Surface di-set — offset = {depth_offset:.2f} m")
                send_to_gui({
                    "type": "event",
                    "text": f"Surface level diset — offset {depth_offset:.2f} m",
                    "level": "ok",
                })

            elif name == "depth_set":

                # Tombol SET: rekam kedalaman SAAT INI sebagai setpoint.
                #
                # Sengaja tidak menuntut armed maupun mode tertentu — merekam
                # sebuah angka tidak menggerakkan apa pun, dan operator memang
                # sering ingin mengunci kedalaman kerja dulu (mis. saat masih
                # meluncur di MANUAL) baru menyalakannya belakangan.
                if not _depth_cmd_rate.allow("depth_set", time.time()):
                    continue

                # Smoothing: sampel baro bergetar ±0.02–0.05 m. Rata-rata
                # menghilangkan noise tanpa lag berarti (buffer 0.1 s). Operator
                # akan melihat angka yang stabil di GUI sebelum tekan SET.
                global _depth_samples
                smoothed = smooth_depth(_depth_samples, alpha=0.7)

                with depth_lock:
                    # Dijepit ke [0, pool_depth]: pembacaan baro bisa meleset di
                    # dekat dasar, dan setpoint di luar kolam membuat bias
                    # menekan wahana ke dasar tanpa henti.
                    depth_target = clamp_depth_target(smoothed, pool_depth)
                    shown = depth_target
                    global _bias_active
                    _bias_active = False  # Reset histeresis saat SET
                print(f"[DEPTH] Set = {shown:.2f} m")
                send_to_gui({
                    "type": "event",
                    "text": f"Depth di-set = {shown:.2f} m",
                    "level": "ok",
                })

            elif name == "depth_hold":

                # Saklar ON/OFF depth-set. value bool eksplisit dari tombol GUI,
                # atau None dari tombol gamepad (= toggle).
                #
                # OFF adalah operasi yang WAJIB berhasil — mematikan sesuatu tidak
                # boleh pernah tertahan rate limiter. Rate limit hanya untuk ON,
                # yang memicu aksi penting. OFF hanya membaca dan matikan state.
                with depth_lock:
                    want = (not depth_hold_enabled) if value is None else bool(value)
                    target_now = depth_target

                # OFF selalu diterima tanpa syarat.
                if not want:
                    with depth_lock:
                        depth_hold_enabled = False
                    print("[DEPTH] Depth-set OFF")
                    send_to_gui({
                        "type": "event",
                        "text": "Depth-set OFF",
                        "level": "ok",
                    })
                    continue

                # ON saja yang dikontrol rate limiter.
                if not _depth_cmd_rate.allow("depth_hold", time.time()):
                    continue

                if target_now is None:
                    print("[DEPTH] ON diabaikan — belum ada setpoint")
                    send_to_gui({
                        "type": "event",
                        "text": "Depth-set belum di-set — tekan SET dulu",
                        "level": "warn",
                    })
                    continue

                # Disarmed -> ArduSub mengabaikan MANUAL_CONTROL sepenuhnya,
                # jadi menyalakan depth-set di sini hanya membuat GUI terlihat
                # "menahan" padahal wahana tidak menerima apa pun.
                if not state.get("armed"):
                    print("[DEPTH] ON diabaikan — vehicle belum armed")
                    send_to_gui({
                        "type": "event",
                        "text": "Depth-set diabaikan — vehicle belum armed",
                        "level": "warn",
                    })
                    continue

                with depth_lock:
                    depth_hold_enabled = True
                print(f"[DEPTH] Depth-set ON -> {target_now:.2f} m")

                # Mode yang salah TIDAK menolak permintaan, hanya memperingatkan:
                # inilah yang membuat depth-set benar-benar lepas dari mode.
                # Saklarnya tetap menyala, dan begitu operator pindah ke Alt Hold
                # bias langsung bekerja tanpa perlu menekan ON lagi.
                if depth_hold_mode_ok():
                    send_to_gui({
                        "type": "event",
                        "text": f"Depth-set ON — menahan {target_now:.2f} m",
                        "level": "ok",
                    })
                else:
                    send_to_gui({
                        "type": "event",
                        "text": "Depth-set ON tapi mode bukan Alt Hold — belum akan menahan",
                        "level": "warn",
                    })

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
                # harus ikut turun sekarang — bukan menunggu penekanan SET
                # berikutnya. None dibiarkan None: "belum di-set" tidak boleh
                # berubah jadi setpoint 0 m gara-gara halaman Setup disimpan.
                with depth_lock:
                    if depth_target is not None:
                        depth_target = clamp_depth_target(depth_target, pool_depth)
                    shown = depth_target
                shown_txt = "belum di-set" if shown is None else f"{shown:.2f} m"
                print(f"[POOL] Kedalaman kolam = {pool_depth:.2f} m "
                      f"(target: {shown_txt})")

            elif name == "thruster_gain_inc":

                gain = min(100.0, thruster_gain * 100.0 + 10.0)
                thruster_gain = gain / 100.0

                print(f"[THRUSTER GAIN] +10% -> {gain:.0f}%")

                send_to_gui({
                    "type": "event",
                    "text": f"Thruster Gain = {gain:.0f}%",
                    "level": "ok",
                })

            elif name == "thruster_gain_dec":

                gain = max(0.0, thruster_gain * 100.0 - 10.0)
                thruster_gain = gain / 100.0

                print(f"[THRUSTER GAIN] -10% -> {gain:.0f}%")

                send_to_gui({
                    "type": "event",
                    "text": f"Thruster Gain = {gain:.0f}%",
                    "level": "ok",
                })

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

                motors = msg.get("motors", {})

                try:
                    gain = float(msg.get("gain", 100))
                except (TypeError, ValueError):
                    gain = 100.0

                gain = max(0.0, min(100.0, gain))
                thruster_gain = gain / 100.0

                print("[DEBUG] Motors received:", motors)
                print(f"[DEBUG] Thruster gain received: {gain:.0f}%")
                print(f"[DEBUG] thruster_gain factor: {thruster_gain:.2f}")

                threading.Thread(
                    target=apply_thruster_config,
                    args=(motors,),
                    daemon=True
                ).start()

            elif name == "motor_test":
                # server.js kirim motor/throttle/duration/direction sebagai
                # field top-level (bukan di dalam "value" — motor_test tidak
                # punya axis tunggal seperti command lain), jadi payload harus
                # dirakit dari msg langsung, bukan msg.get("value") yang selalu None.
                payload = {
                    "motor": msg.get("motor"),
                    "throttle": msg.get("throttle"),
                    "duration": msg.get("duration"),
                    "direction": msg.get("direction"),
                }
                # Thread terpisah: command_long_send tidak blocking lama, tapi
                # tetap dipisah dari command_listener demi konsistensi pola.
                threading.Thread(
                    target=run_motor_test, args=(payload,), daemon=True
                ).start()

            elif name == "param_list":
                # Minta seluruh tabel param FC. Jawabannya ~980 PARAM_VALUE
                # yang ditangani & di-batch di loop RX main().
                with _param_lock:
                    _param_batch.clear()
                print("[PARAM] minta seluruh daftar param")
                with master_lock:
                    master.mav.param_request_list_send(
                        master.target_system,
                        master.target_component,
                    )

            elif name == "param_get":
                param = normalize_param_name(value)
                if param is None:
                    print(f"[PARAM] nama param ditolak: {value!r}")
                    continue
                with master_lock:
                    master.mav.param_request_read_send(
                        master.target_system,
                        master.target_component,
                        param.encode("utf-8"),
                        -1,   # -1 = cari berdasarkan nama, bukan indeks
                    )

            elif name == "param_set":
                # value = {"name": ..., "value": ..., "type": <MAV_PARAM_TYPE>}
                # Gerbang konfirmasi ada di sisi GUI (halaman Vehicle) — di
                # sini yang dijaga hanya validitas.
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

                if gripper is None:
                    print("[GRIPPER] Controller belum siap")
                    continue

                if value == "open":
                    gripper.open()

                elif value == "close":
                    gripper.close()

                elif value == "stop":
                    gripper.stop()

                else:
                    print(f"[GRIPPER] Unknown command: {value}")

            elif name == "gripper_rotate":

                if gripper is None:
                    print("[ROTATE] Controller belum siap")
                    continue

                if value == "left":
                    gripper.rotate_left()

                elif value == "right":
                    gripper.rotate_right()

                elif value == "stop":
                    gripper.rotate_stop()

                else:
                        print(f"[ROTATE] Unknown command: {value}")

            elif name in AXIS_RANGE:
                new_value = clamp_axis(name, value)

                with joystick_lock:
                    joystick[name] = new_value
                    last_joystick_update = time.time()

            elif name in GUI_ONLY_COMMANDS:
                # Murni urusan dashboard, tidak ada padanannya di wahana.
                pass

            else:
                print(f"[CMD] unknown command: {name} = {value}")

        except Exception as e:
            print("[CMD] error executing command:", e)
            send_to_gui({
                "type": "event",
                "text": f"Command '{name}' gagal dieksekusi: {e}",
                "level": "err",
            })

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

    with master_lock:
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

        with master_lock:
            master.mav.command_long_send(
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST,
                0,
                motor,
                mavutil.mavlink.MOTOR_TEST_THROTTLE_PERCENT,
                signed_throttle,
                duration,
                0,
                # param6 = motor test order. ArduSub menolak apa pun selain BOARD
                # ("bad test type %.2f") — DEFAULT(0) yang dulu dipakai di sini
                # SELALU ditolak firmware, itu sebabnya motor test timeout.
                mavutil.mavlink.MOTOR_TEST_ORDER_BOARD,
                0,
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

        # Ramp TIDAK BOLEH menunda perintah berhenti. E-Stop menetralkan axis
        # lalu disarm; tanpa reset di sini, nilai ter-shape masih meluncur
        # turun selama ~0,2 detik sesudahnya. Disarm memang sudah mematikan
        # thruster, tapi keselamatan tidak boleh bergantung pada satu lapis.
        
        # Fail-safe: GUI diam terlalu lama (crash / joystick dicabut / link
        # putus) -> tahan posisi netral, jangan ulangi input terakhir.
        mc, stale = resolve_manual_packet(axes, last_update, time.time())
        eff_axes = AXIS_NEUTRAL if stale else axes

        # Beri tahu dashboard bahwa yang mengalir sekarang adalah netral buatan
        # Pi, bukan perintah operator.
        state["cmd_link"] = "stale" if stale else "ok"

        # =========================
        # THRUSTER GAIN
        # =========================
        gain = thruster_gain

        scaled_axes = {
            "surge": int(round(eff_axes.get("surge", 0) * gain)),
            "sway": int(round(eff_axes.get("sway", 0) * gain)),
            "heave": int(round(eff_axes.get("heave", 0) * gain)),
            "yaw": int(round(eff_axes.get("yaw", 0) * gain)),
        }

        # Buat ulang MANUAL_CONTROL berdasarkan axis
        # yang sudah dikalikan thruster gain.
        mc = axes_to_manual_control(
            surge=scaled_axes["surge"],
            sway=scaled_axes["sway"],
            yaw=scaled_axes["yaw"],
            heave=scaled_axes["heave"],
        )

        # Depth hold dan heading hold bekerja SETELAH gain untuk `mc` (dorongan
        # nyata ke thruster) — TAPI gerbang "operator sedang memegang stik" di
        # kedua fungsi itu (HEAVE_MANUAL_EPSILON/YAW_MANUAL_EPSILON) sengaja
        # diberi `eff_axes`, BUKAN `scaled_axes`. Itu niat pilot, bukan
        # kekuatan aktual ke thruster: kalau thruster_gain diturunkan jauh
        # (mis. 20%), stik yang didorong penuh (100) jadi cuma 20 setelah
        # dikalikan gain — pas di ambang epsilon atau di bawahnya — dan
        # gerbang bisa gagal mendeteksi stik yang sebenarnya sedang dipegang.
        mc = apply_depth_hold_bias(mc, eff_axes)

        # Sesudah depth-hold: keduanya menulis field yang berbeda (z vs r), jadi
        # urutannya tidak penting untuk hasil — hanya dijaga konsisten supaya
        # mudah dibaca. Saat stale, axes netral yang dipakai sehingga overlay
        # tidak menyimpulkan "operator sedang memegang stik" dari input basi.
        mc = apply_heading_hold(mc, eff_axes)

        try:
            with master_lock:
                master.mav.manual_control_send(
                    master.target_system,
                    mc["x"], mc["y"], mc["z"], mc["r"], mc["buttons"],
                )
        except Exception as e:
            print("[JOY] gagal kirim MANUAL_CONTROL:", e)

        time.sleep(JOYSTICK_SEND_INTERVAL)

# =========================
# Main koneksi Pixhawk
# =========================
def connect_pixhawk():
    """Buka link serial + tunggu heartbeat + minta stream. Kembalikan koneksi."""
    global master, gripper

    print(f"[MAV] Connecting to Pixhawk on {PIXHAWK_PORT} @ {PIXHAWK_BAUD} ...")
    link = mavutil.mavlink_connection(PIXHAWK_PORT, baud=PIXHAWK_BAUD)

    print("[MAV] Waiting heartbeat...")
    if link.wait_heartbeat(timeout=30) is None:
        raise RuntimeError("tidak ada heartbeat dari Pixhawk dalam 30 detik")

    print("[MAV] Heartbeat received!")
    print(f"[MAV] System {link.target_system}, Component {link.target_component}")

    master = link
    gripper = GripperController(master)
    print("[GRIPPER] Controller initialized")

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
    global master, gripper

    link, master = master, None
    if gripper is not None:
        gripper.shutdown()
    gripper = None
    
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
    global _raw_depth

    connect_pixhawk()

    # Thread listener command
    threading.Thread(target=command_listener, daemon=True).start()
    threading.Thread(target=joystick_sender, daemon=True).start()
    threading.Thread(target=qgc_command_receiver, daemon=True).start()

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
        forward_mavlink_to_qgc(msg)

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
        # LOCAL_POSITION_NED: estimasi posisi EKF (utara/timur, meter).
        # Sudah mengalir lewat MAV_DATA_STREAM_ALL, cuma belum pernah dibaca.
        # --------------------------------
        elif mtype == "LOCAL_POSITION_NED":
            state["pos_n"] = float(msg.x)
            state["pos_e"] = float(msg.y)

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
                _raw_depth = max(0.0, -float(msg.altitude))
                # depth_offset ditare lewat command `set_surface` (lihat
                # command_listener) supaya depth 0 = permukaan sungguhan,
                # bukan origin baro/EKF mentah yang bisa drift atau tidak pas
                # 0 saat ROV di-arm.
                state["depth"] = max(0.0, _raw_depth - depth_offset)

                # Isi buffer sampel untuk SET smoothing (lihat depth_set handler).
                global _depth_samples
                _depth_samples.append(state["depth"])
                if len(_depth_samples) > _DEPTH_SAMPLE_BUFFER_SIZE:
                    _depth_samples.pop(0)

        # --------------------------------
        # SERVO_OUTPUT_RAW: PWM thruster vertikal (T3/T4/T5 = heave)
        # untuk CSV tuning depth-hold di halaman Telemetry.
        # --------------------------------
        elif mtype == "SERVO_OUTPUT_RAW":
            vals = [v for v in (msg.servo3_raw, msg.servo4_raw, msg.servo5_raw) if v]
            if vals:
                state["thruster_vertical_pwm"] = int(sum(vals) / len(vals))

            # T6 = lateral/sway, T1/T2 = surge+yaw. Dulu dibuang di sini padahal
            # SERVO_OUTPUT_RAW sudah diminta 10 Hz (lihat connect_pixhawk) —
            # akibatnya tidak ada satu pun catatan PWM thruster horizontal di
            # disk, dan drift menyamping saat stik netral jadi tidak bisa
            # didiagnosis sama sekali. Ikut mengalir ke baris [SEND] 1 Hz.
            #
            # 0 berarti "FC tidak mengirim channel itu", bukan "thruster diam":
            # thruster yang diam ada di sekitar SERVOn_TRIM (1500), bukan 0.
            state["thruster_lateral_pwm"] = int(msg.servo6_raw or 0)
            state["thruster_surge_pwm"] = [
                int(msg.servo1_raw or 0), int(msg.servo2_raw or 0),
            ]

        # --------------------------------
        # PID_TUNING: P/I/D per axis untuk CSV tuning + diagnosa offline.
        # Diam per axis kalau bit-nya di GCS_PID_MASK belum menyala di FC —
        # bukan error, field itu tetap 0.0.
        #
        # ROLL/PITCH ditambahkan setelah trial 15 Agu 2026 (GCS_PID_MASK
        # dinaikkan dari 8 ke 15): sebelumnya attitude loop cuma terlihat
        # LIVE di halaman Analyze (maybe_stream_mavlink), tidak pernah masuk
        # hydroships*.log — jadi tidak bisa dianalisis offline setelah trial
        # selesai. ACCZ tetap dipetakan ke pid_p_out/i/d_out (nama lama,
        # dipakai CSV tuning depth-hold yang sudah ada); ROLL/PITCH dapat
        # field terpisah supaya tidak menimpa itu.
        # --------------------------------
        elif mtype == "PID_TUNING":
            axis = getattr(msg, "axis", None)
            if axis == mavutil.mavlink.PID_TUNING_ACCZ:
                state["pid_p_out"] = float(msg.P)
                state["pid_i_out"] = float(msg.I)
                state["pid_d_out"] = float(msg.D)
            elif axis == mavutil.mavlink.PID_TUNING_ROLL:
                state["pid_roll_p_out"] = float(msg.P)
                state["pid_roll_i_out"] = float(msg.I)
                state["pid_roll_d_out"] = float(msg.D)
            elif axis == mavutil.mavlink.PID_TUNING_PITCH:
                state["pid_pitch_p_out"] = float(msg.P)
                state["pid_pitch_i_out"] = float(msg.I)
                state["pid_pitch_d_out"] = float(msg.D)

        # --------------------------------
        # HEARTBEAT: mode dan armed
        # --------------------------------
        elif mtype == "HEARTBEAT":
            try:
                state["mode"] = mavutil.mode_string_v10(msg)
            except Exception:
                pass

            base_mode = msg.base_mode
            was_armed = state["armed"]
            state["armed"] = bool(base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

            # Disarm dari JALUR MANA PUN mematikan depth-set: tombol DISARM,
            # failsafe FC, saklar RC, atau GCS lain. Handler `stop` sudah
            # menanganinya untuk E-Stop, tapi tanpa cek transisi di sini
            # depth_hold_enabled tetap True selama wahana disarm — dan begitu
            # di-arm ulang wahana langsung berenang sendiri ke setpoint lama
            # tanpa operator menekan apa pun. Itu persis kejutan yang hendak
            # dihilangkan oleh tombol ON/OFF ini.
            #
            # depth_target sengaja DIPERTAHANKAN (sama seperti handler `stop`):
            # kedalaman kerja yang sudah direkam masih berguna, operator tinggal
            # menekan ON lagi setelah re-arm.
            if was_armed and not state["armed"]:
                with depth_lock:
                    if depth_hold_enabled:
                        globals()["depth_hold_enabled"] = False
                        print("[DEPTH] Depth-set OFF — vehicle disarm")

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
