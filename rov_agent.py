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
    axis_released,
    clamp_axis,
    resolve_manual_packet,
)

from rov_modes import (
    depth_bias_engaged,
    depth_bias_is_continuous,
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
    DEPTH_PULSE_DURATION_S,
    DEPTH_PULSE_MAGNITUDE,
    BRAKE_PULSE_DURATION_S,
    BRAKE_PULSE_MAGNITUDE,
    SWAY_BRAKE_PULSE_DURATION_S,
    SWAY_BRAKE_PULSE_MAGNITUDE,
    HEAVE_FULL_DEFLECTION_THRESHOLD,
    HEAVE_FULL_HOLD_MIN_S,
    HEAVE_BRAKE_PULSE_DURATION_S,
    HEAVE_BRAKE_PULSE_MAGNITUDE,
    clamp_depth_target,
    depth_bias_active,
    depth_hold_bias,
    resolve_pid_writes,
    smooth_depth,
    valid_pool_depth,

)

from attitude_filter import AttitudeFilter
from gripper_controller import GripperController
from rov_drift import flow_to_velocity, integrate_accel, IMU_GAP_FILL_MAX_S
from rov_position import position_correction, operator_holding_translation

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
    # Counter trial Misi 2/3 (command `mission_counter`) — default trial 1/skor 15
    # sebelum pilot menekan "Gagal, Ulangi" atau "Reset" pertama kali.
    "mission_counter": {"m2_fails": 0, "m2_score": 15, "m3_fails": 0, "m3_score": 15},
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
    # Tanda gantungan dari tombol MARK (command `mark_hook`). None = belum
    # ditandai — lihat marked_heading/marked_depth untuk kenapa ini heading+depth
    # dan bukan koordinat x/y.
    "marked_heading": None,
    "marked_depth": None,
    # Flag mentah toggle poshold operator — lihat send_telemetry() untuk
    # kenapa GUI butuh ini terpisah dari "mode"/"depth_hold".
    "poshold": False,
    # Kecepatan drift dari kamera BOTTOM (optical flow, lihat rov_drift.py).
    # quality 0 berarti tak ada bacaan segar (cv2/kamera tak ada, atau
    # pool_depth belum di-set) — vx/vy TETAP 0.0, bukan angka basi yang bisa
    # disalahartikan operator sebagai "sedang diam".
    "drift_vx": 0.0,
    "drift_vy": 0.0,
    "drift_quality": 0,
    # "flow" = bacaan optical flow segar, "imu" = tambalan celah singkat
    # (integrate_accel, lihat rov_drift.py), "none" = tak ada keduanya.
    "drift_source": "none",
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

# Mission5 FSM (misi 5 autonomous). Dibuat saat pertama kali dibutuhkan di
# main(), dinyalakan/dimatikan oleh toggle control_mode di command_listener.
# None berarti belum di-setup (mis. import autonomy gagal) — kontrol manual
# tetap jalan penuh, lihat rov_mission5_bridge.Mission5Runner.
mission5_runner = None

# Axis yang sedang diperintahkan FSM (skala GUI -1000..1000). Terpisah dari
# `joystick` (axis operator) supaya keduanya tidak pernah saling menimpa: yang
# menentukan mana yang dipakai adalah current_control_mode di joystick_sender.
fsm_axes = {"surge": 0, "sway": 0, "heave": 0, "yaw": 0}
fsm_axes_lock = threading.Lock()

# Complementary filter + EMA untuk roll/pitch/yaw dari ATTITUDE (lihat
# attitude_filter.py). Meredam jitter sensor tanpa menambah lag berarti.
attitude_filter = AttitudeFilter()
prev_attitude_ts = None

# Body rate (roll/pitch/yaw, rad/s) mentah dari ATTITUDE terakhir — dipakai
# suku gyro OPTICAL_FLOW_RAD di drift_estimator_thread() (Tahap 2). Sengaja
# TIDAK dari attitude_filter (itu sudah di-EMA untuk tampilan, bukan untuk
# kompensasi rotasi per-frame flow).
_last_body_rates = (0.0, 0.0, 0.0)

# Akselerasi body-frame (m/s^2, x=depan/surge, y=kanan/sway) dari SCALED_IMU2
# terakhir — dipakai integrate_accel() di drift_estimator_thread() untuk
# menambal celah SINGKAT saat optical flow kosong (lihat rov_drift.py).
_last_body_accel = (0.0, 0.0)

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

# Pulsa sekali-tembak depth_up/down di ALT_HOLD (lihat DEPTH_PULSE_* di
# rov_pid.py) — beda dari depth_target/bias berkelanjutan yang dipakai
# STABILIZE. _depth_pulse_until = 0.0 berarti tidak ada pulsa aktif.
_depth_pulse_until = 0.0
_depth_pulse_dir = 0

# Tanda gantungan (command `mark_hook`): heading & depth SAAT operator menekan
# MARK, yaitu tepat ketika payload tergantung di hook pada misi 3. Dipakai
# Mission5FSM.M5_REDIVE untuk kembali ke sana saat misi 5 autonomous.
#
# Kenapa heading+depth dan BUKAN koordinat x/y: wahana ini tidak punya estimasi
# posisi horizontal sama sekali (tidak ada GPS/DVL/optical flow — lihat bagian
# POSHOLD di bawah). state["pos_n"]/["pos_e"] tetap None karena
# LOCAL_POSITION_NED tak pernah dikirim FC. Jadi tak ada koordinat untuk direkam.
#
# None = belum pernah di-MARK. Dibedakan tegas dari 0.0 yang merupakan nilai sah
# (heading 0° = utara), pola sama dengan depth_target di atas.
#
# Sengaja HANYA di memori: hilang saat rov-agent.service restart. Satu run lomba
# 10 menit, dan status mark dipantulkan ke telemetri supaya operator melihat
# kalau ia terhapus, bukan menemukannya saat wahana sudah menyelam.
marked_heading = None
marked_depth = None

# Hitungan trial gagal Misi 2 (grab) & Misi 3 (hang), command `mission_counter`.
# Skor 15/10/5 per Guidebook KKI 2026 §4.7.4 ditentukan dari jumlah trial —
# ROV tak punya sensor grip-force, jadi gagal/sukses dilihat & ditandai pilot
# dari kamera secara langsung. Sengaja HANYA di memori sama seperti marked_*
# di atas: hilang saat restart, satu run lomba 10 menit.
mission_counter_fails = {"m2": 0, "m3": 0}


def _tier_score(fails: int) -> int:
    """Skor trial per Guidebook §4.7.4: trial 1=15, trial 2=10, trial >2=5."""
    trial = fails + 1
    return 15 if trial == 1 else 10 if trial == 2 else 5


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

# |surge|/|sway| di atas ini dianggap operator sedang memegang stik — dipakai
# apply_translation_brake untuk deteksi edge-release. Filosofi sama dengan
# HEAVE_MANUAL_EPSILON.
BRAKE_MANUAL_EPSILON = 20

# State pulsa rem surge/sway (rasa "brake" ala DJI, lihat apply_translation_brake).
# _prev_surge/_prev_sway menyimpan axis tick sebelumnya untuk deteksi edge;
# _*_pulse_until = 0.0 berarti tidak ada pulsa aktif untuk axis itu.
_prev_surge = 0
_prev_sway = 0
_surge_pulse_until = 0.0
_surge_pulse_dir = 0
_sway_pulse_until = 0.0
_sway_pulse_dir = 0

# State rem heave (lihat apply_heave_brake) — beda dari surge/sway: butuh
# pelacakan LAMA stik ditahan di full-deflection, bukan cuma edge tick
# sebelumnya. _heave_full_since = None berarti stik sedang tidak di
# full-deflection; _heave_sustained menyala begitu tertahan cukup lama, dan
# tetap menyala sampai stik benar-benar dilepas (arm rem saat itu terjadi).
_prev_heave = 0
_heave_full_since = None
_heave_sustained = False
_heave_pulse_until = 0.0
_heave_pulse_dir = 0

# Kill-switch autonomy: axis operator di atas ambang ini saat mode autonomous
# membatalkan FSM dan mengembalikan kendali manual. Skalanya SAMA dengan axis
# GUI, -1000..1000 (clampAxis di server.js, AXIS_RANGE di rov_axes.py) — jadi
# 15 ≈ 1,5% skala penuh, BUKAN 15%.
#
# Yang menyaring drift stik bukan angka ini, melainkan deadzone sisi-GUI
# (DEFAULT_DEADZONE=0.12 + expo 1.6 di shared/joystick-profile.js). Efek
# gabungannya: kill-switch menyala di ~20% defleksi stik fisik — peka untuk
# merebut kendali, tuli terhadap noise. Nilainya sengaja sama persis dengan
# KILL_SWITCH_DEADZONE di autonomy/rov_link.py (jalur autonomous yang lain);
# JANGAN naikkan ke 150 dengan anggapan skalanya -100..100.
KILL_SWITCH_DEADZONE = 15

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

    HANYA soal mode, dipakai badge GUI sebagai indikator "mode ini menahan
    kedalaman" — bukan "bias sedang benar-benar mendorong" (lihat
    depth_bias_engaged untuk syarat lengkapnya).

    MANUAL/ALT_HOLD/POSHOLD sengaja TIDAK termasuk — lihat DEPTH_HOLD_MODES
    di rov_modes.py untuk mode mana yang jadi syaratnya sekarang.
    """
    return depth_hold_allowed(_current_pixhawk_mode())


def apply_depth_hold_bias(mc, axes):
    """Geser MANUAL_CONTROL.z ke arah depth_target saat depth-set aktif.

    Tiga gerbang, semuanya harus terbuka (lihat depth_bias_engaged di
    rov_modes.py untuk aturannya dalam bentuk yang bisa diuji). Tak ada saklar
    ON/OFF operator terpisah — target sendiri yang jadi penanda "aktif":
      1. depth_target sudah terisi   -> None sampai heave pernah disentuh
                                        (di bawah) atau depth_up/depth_down ditekan
      2. mode ArduSub ada di DEPTH_HOLD_MODES (rov_modes.py)
      3. stik heave netral           -> begitu operator menyentuh stik, input
                                        manual menang mutlak, dan target
                                        mengikuti kedalaman sampai stik dilepas
    """
    global depth_target
    global _bias_active, _depth_ema, _depth_ema_ts, _last_depth_diag
    global _depth_pulse_until, _depth_pulse_dir

    # Pulsa sekali-tembak (ALT_HOLD) diperiksa SEBELUM gerbang bias
    # berkelanjutan (STABILIZE) — keduanya tidak pernah aktif bersamaan,
    # lihat DEPTH_PULSE_* di rov_pid.py untuk alasannya.
    if time.time() < _depth_pulse_until:
        _mode_now = _effective_requested_mode() or state["mode"]
        if (abs(axes.get("heave", 0)) > HEAVE_MANUAL_EPSILON
                or not depth_hold_allowed(_mode_now)):
            # Operator menyentuh stik heave menang mutlak, atau mode berubah
            # (mis. ke MANUAL) sebelum pulsa selesai — batalkan pulsa.
            _depth_pulse_until = 0.0
        else:
            out = dict(mc)
            out["z"] = max(0, min(1000, int(round(mc["z"] + _depth_pulse_dir * DEPTH_PULSE_MAGNITUDE))))
            return out

    with depth_lock:
        target = depth_target

    _mode_now = _effective_requested_mode() or state["mode"]
    if not (depth_bias_is_continuous(_mode_now) and depth_bias_engaged(
        target,
        _mode_now,
        axes.get("heave", 0),
        HEAVE_MANUAL_EPSILON,
    )):
        # Auto-follow depth_target ke stik heave HANYA di mode kontinu
        # (STABILIZE): di sana depth_target satu-satunya yang menahan
        # kedalaman, jadi harus ikut supaya tak "menyelam balik" ke target
        # lama begitu stik dilepas tanpa sempat depth_up/down. Di ALT_HOLD
        # cascade ArduSub sendiri yang menahan -- depth_target di sana cuma
        # dipakai sebagai arah pulsa depth_up/down (rov_pid.py DEPTH_PULSE_*),
        # jadi HARUS diam kecuali operator menekan depth_up/down. Riwayat:
        # auto-follow ini sempat dihapus (13c1d4a, 10 Agu) dengan alasan
        # yang sama, lalu tertulis ulang tanpa sengaja di redesign 21 Agu
        # (8a23060) -- sekarang dipersempit lagi ke STABILIZE saja.
        if (depth_bias_is_continuous(_mode_now)
                and abs(axes.get("heave", 0)) > HEAVE_MANUAL_EPSILON):
            with depth_lock:
                depth_target = clamp_depth_target(
                    state["depth"],
                    pool_depth
                )

        _bias_active = False
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


# =========================
# POSITION HOLD x/y (dari optical flow — lihat rov_position.py)
# =========================
# Default OFF (POSITION_HOLD_TO_THRUSTER, env M5_POSITION_HOLD) — sama
# alasan dengan OPTFLOW_TO_EKF: arah dx/dy kamera-vs-body BELUM divalidasi
# (rov_drift.py). Kalau tandanya terbalik, fitur ini AKTIF MENDORONG wahana
# ke arah salah, bukan cuma "kurang membantu". Nyalakan hanya setelah HUD
# drift_vx/vy dikonfirmasi arahnya benar di kolam.
POSITION_HOLD_TO_THRUSTER = os.environ.get("M5_POSITION_HOLD", "0") == "1"

# Akumulasi pergeseran (meter) sejak stick surge/sway terakhir dilepas —
# BUKAN posisi absolut (tidak ada GPS/DVL). Lihat docstring rov_position.py.
position_error_x = 0.0
position_error_y = 0.0
position_lock = threading.Lock()

# Timestamp tick terakhir position hold benar-benar mengakumulasi error.
# None berarti belum ada dt yang valid untuk tick berikutnya — dipakai
# supaya integrasi tidak melompat besar begitu stick baru saja dilepas
# setelah lama dipegang (lihat apply_position_hold).
_position_last_ts = None


def apply_position_hold(mc, axes):
    """Tambahkan koreksi x/y untuk menahan pergeseran RELATIF sejak stick
    surge/sway dilepas, pakai drift_vx/vy (optical flow) sebagai umpan balik.

    Aturan sama seperti apply_heading_hold(): operator menang mutlak, dan
    akumulasi error DIBUANG begitu stick disentuh (bukan cuma diabaikan)
    supaya wahana menahan posisi BARU tempat operator meninggalkannya.

    Cuma percaya bacaan visual asli (drift_source == "flow") — TIDAK pernah
    mendorong thruster berdasar tambalan IMU (integrate_accel), yang
    akurasinya jauh di bawah standar untuk loop tertutup yang benar-benar
    menggerakkan wahana (beda dari sekadar tampilan HUD).
    """
    global position_error_x, position_error_y, _position_last_ts

    if not POSITION_HOLD_TO_THRUSTER or not poshold_engaged():
        with position_lock:
            position_error_x = 0.0
            position_error_y = 0.0
        _position_last_ts = None
        return mc

    if operator_holding_translation(axes):
        with position_lock:
            position_error_x = 0.0
            position_error_y = 0.0
        _position_last_ts = None
        return mc

    if state.get("drift_source") != "flow":
        # Data tak segar/tak dipercaya: BEKUKAN akumulasi (jangan integrasi
        # dengan dt besar begitu flow segar lagi), tapi jangan buang — flow
        # bisa saja cuma kedip sesaat, bukan operator menyentuh stik.
        _position_last_ts = None
        return mc

    now = time.time()
    dt = (now - _position_last_ts) if _position_last_ts is not None else 0.0
    _position_last_ts = now

    with position_lock:
        position_error_x += state.get("drift_vx", 0.0) * dt
        position_error_y += state.get("drift_vy", 0.0) * dt
        ex, ey = position_error_x, position_error_y

    cx, cy = position_correction(ex, ey)
    if cx == 0 and cy == 0:
        return mc

    out = dict(mc)
    out["x"] = max(-1000, min(1000, int(mc["x"]) + cx))
    out["y"] = max(-1000, min(1000, int(mc["y"]) + cy))
    return out


# =========================
# BRAKE (rem surge/sway ala DJI)
# =========================
# TIDAK ADA DVL/GPS di bawah air, jadi ini BUKAN position hold: hanya
# menghentikan momentum ROV sendiri lebih cepat lewat pulsa dorongan balik
# singkat begitu stick dilepas. Arus lateral tetap menggeser wahana sesudahnya
# (lihat rov_modes.py baris 34-48). Pola pulsa sama persis dengan
# depth_up/down (_depth_pulse_until/_depth_pulse_dir di apply_depth_hold_bias).
def apply_translation_brake(mc, axes):
    """Dorong pulsa balik singkat di x/y begitu stick surge/sway dilepas.

    Operator selalu menang mutlak: menyentuh stick axis itu lagi — searah
    ataupun berlawanan — langsung membatalkan pulsa yang sedang jalan.
    Hanya aktif saat kendali manual; axis FSM (autonomous) tidak diberi rem
    yang tak diminta.

    SENGAJA TIDAK ADA rem heave (dicoba 23 Agu 2026, dicabut hari yang
    sama). Beda dari surge/sway — di mana "lepas stick" nyaris selalu
    berarti "selesai bergerak" — pilot lazim melakukan tap-tap KECIL di
    heave untuk menyetel kedalaman manual sambil ALT_HOLD/cascade berusaha
    menahan. Tiap tap kecil itu tetap melewati BRAKE_MANUAL_EPSILON, jadi
    rem 45% otoritas selama 0,45 detik menyala berkali-kali dan JUSTRU
    melawan cascade depth-hold ArduSub — trial menunjukkan depth mengembara
    0,20-0,54 m alih-alih menetap. Kalau heave butuh rasa "brake" suatu
    hari nanti, itu harus lewat jalur berbeda (mis. HANYA setelah heave
    ditahan lama di luar deadzone, bukan edge-detect polos seperti ini).
    """
    global _prev_surge, _prev_sway
    global _surge_pulse_until, _surge_pulse_dir
    global _sway_pulse_until, _sway_pulse_dir

    surge = axes.get("surge", 0)
    sway = axes.get("sway", 0)

    if current_control_mode != "manual":
        _prev_surge, _prev_sway = surge, sway
        _surge_pulse_until = 0.0
        _sway_pulse_until = 0.0
        return mc

    now = time.time()
    out = dict(mc)

    if axis_released(_prev_surge, surge, BRAKE_MANUAL_EPSILON):
        _surge_pulse_until = now + BRAKE_PULSE_DURATION_S
        _surge_pulse_dir = -1 if _prev_surge > 0 else 1
    if abs(surge) > BRAKE_MANUAL_EPSILON:
        _surge_pulse_until = 0.0
    elif now < _surge_pulse_until:
        out["x"] = max(-1000, min(1000, out["x"] + _surge_pulse_dir * BRAKE_PULSE_MAGNITUDE))

    if axis_released(_prev_sway, sway, BRAKE_MANUAL_EPSILON):
        _sway_pulse_until = now + SWAY_BRAKE_PULSE_DURATION_S
        _sway_pulse_dir = -1 if _prev_sway > 0 else 1
    if abs(sway) > BRAKE_MANUAL_EPSILON:
        _sway_pulse_until = 0.0
    elif now < _sway_pulse_until:
        out["y"] = max(-1000, min(1000, out["y"] + _sway_pulse_dir * SWAY_BRAKE_PULSE_MAGNITUDE))

    _prev_surge, _prev_sway = surge, sway
    return out


# Rem heave, versi KEDUA (lihat catatan HEAVE_* di rov_pid.py untuk kenapa
# versi pertama dicabut). HANYA aktif di ALT_HOLD: di sana stik z diteruskan
# mentah ke Pixhawk sebagai perintah RATE (lihat apply_depth_hold_bias),
# jadi menahannya lama-lama menghasilkan turun/naik jauh melebihi perkiraan
# defleksi stik — beda dari STABILIZE yang sudah punya auto-follow
# depth_target sendiri.
def apply_heave_brake(mc, axes):
    """Dorong pulsa balik singkat di z HANYA setelah stik heave ditahan
    dekat full-deflection >= HEAVE_FULL_HOLD_MIN_S lalu dilepas.

    Tap-tap kecil untuk menyetel kedalaman manual tidak pernah menyentuh
    HEAVE_FULL_DEFLECTION_THRESHOLD, jadi tidak pernah arm — beda dari versi
    pertama yang edge-detect di HEAVE_MANUAL_EPSILON polos dan melawan
    cascade depth-hold ArduSub di tiap tap.
    """
    global _prev_heave, _heave_full_since, _heave_sustained
    global _heave_pulse_until, _heave_pulse_dir

    heave = axes.get("heave", 0)

    _mode_now = _effective_requested_mode() or state["mode"]
    if (current_control_mode != "manual"
            or not depth_hold_allowed(_mode_now)
            or depth_bias_is_continuous(_mode_now)):
        _prev_heave = heave
        _heave_full_since = None
        _heave_sustained = False
        _heave_pulse_until = 0.0
        return mc

    now = time.time()
    out = dict(mc)

    if abs(heave) >= HEAVE_FULL_DEFLECTION_THRESHOLD:
        if _heave_full_since is None:
            _heave_full_since = now
        elif now - _heave_full_since >= HEAVE_FULL_HOLD_MIN_S:
            _heave_sustained = True
    else:
        _heave_full_since = None

    if axis_released(_prev_heave, heave, HEAVE_MANUAL_EPSILON):
        if _heave_sustained:
            _heave_pulse_until = now + HEAVE_BRAKE_PULSE_DURATION_S
            _heave_pulse_dir = -1 if _prev_heave > 0 else 1
        _heave_sustained = False

    if abs(heave) > HEAVE_MANUAL_EPSILON:
        _heave_pulse_until = 0.0
    elif now < _heave_pulse_until:
        out["z"] = max(0, min(1000, out["z"] + _heave_pulse_dir * HEAVE_BRAKE_PULSE_MAGNITUDE))

    _prev_heave = heave
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
        state["depth_target"] = depth_target

    state["depth_hold"] = depth_hold_mode_ok()

    # Flag mentah dari toggle operator (bukan poshold_engaged()): GUI
    # membutuhkan ini APA ADANYA untuk membedakan tab "poshold" dari
    # "depth_hold" — keduanya sama-sama ArduSub ALT_HOLD, HEARTBEAT tidak
    # bisa membedakan (lihat docstring rov_modes.py). BUG lama: field ini tak
    # pernah dikirim sama sekali, jadi tab Pos Hold di GUI tak pernah
    # terkonfirmasi walau overlay heading-hold-nya sudah benar-benar aktif.
    state["poshold"] = poshold_active

    with heading_lock:
        state["heading_target"] = heading_target

    # Dipantulkan supaya operator bisa memastikan wahana benar-benar TAHU
    # kedalaman kolamnya — null berarti jepitan depth_target belum aktif.
    state["pool_depth"] = pool_depth

    # Tanda gantungan (tombol MARK). null = belum di-MARK, dan GUI WAJIB
    # menampilkannya begitu: tanpa mark, M5_REDIVE tak punya arah dan hanya
    # menyapu pelan sampai timeout. Operator harus tahu itu SEBELUM menekan
    # AUTONOMOUS, bukan setelah wahana menyelam dan gagal.
    state["marked_heading"] = marked_heading
    state["marked_depth"] = marked_depth

    # Gate otoritas untuk mission5 FSM (toggle autonomous/manual di GUI).
    # HARUS kunci sendiri: dulu ini menulis ke state["mode"] dan menimpa pilot
    # mode ArduSub dari HEARTBEAT 10x/detik. Akibatnya requested_mode tak pernah
    # terkonfirmasi, dan sesudah REQUESTED_MODE_TIMEOUT semua gerbang
    # depth_hold_allowed() jatuh ke "manual" — depth-set dan overlay POSHOLD
    # berhenti diam-diam persis 3 detik setelah masuk ALT_HOLD.
    # Pola yang sama dipakai autonomy/rov_link.py dan server/server.js.
    state["control_mode"] = current_control_mode
    state["thruster_gain"] = thruster_gain * 100.0

    # State FSM misi 5 (mis. "M5_DOCK") + hasil vision (qr_data/qr_wall) supaya
    # operator melihat progres misi dan readout QR di Control pakai deteksi
    # pipeline Python (robust), bukan fallback jsQR sisi klien. None = FSM
    # tidak sedang jalan.
    m5_telem = mission5_runner.telemetry() if mission5_runner is not None else None
    state["mission5_state"] = m5_telem.get("state") if m5_telem else None
    state["mission5"] = m5_telem
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
# Mission5 FSM (misi 5 autonomous)
# =========================
def _fsm_set_axis(surge=0, sway=0, yaw=0, heave=0):
    """Dipanggil Mission5FSM lewat adapter. Menulis ke fsm_axes, BUKAN joystick.

    Nilai sudah dalam skala GUI -1000..1000 (adapter yang mengalikan ×10 dari
    persen milik FSM). Di-clamp lewat jalur yang sama dengan axis operator
    supaya tidak ada cara FSM mengirim nilai di luar rentang yang sah.
    """
    with fsm_axes_lock:
        fsm_axes["surge"] = clamp_axis("surge", surge)
        fsm_axes["sway"] = clamp_axis("sway", sway)
        fsm_axes["yaw"] = clamp_axis("yaw", yaw)
        fsm_axes["heave"] = clamp_axis("heave", heave)


def _fsm_set_gripper(close):
    if gripper is None:
        return
    if close:
        gripper.close()
    else:
        gripper.open()


def _fsm_emergency_stop():
    """Failsafe FSM: netralkan axis FSM + disarm.

    Sengaja TIDAK menyentuh gripper — melepas payload saat abort justru
    menjatuhkannya di tempat yang salah.
    """
    with fsm_axes_lock:
        fsm_axes.update({"surge": 0, "sway": 0, "yaw": 0, "heave": 0})
    send_arm_disarm(False)


def _fsm_read_state():
    """Telemetri untuk FSM: dict `state` apa adanya.

    FSM membaca 'depth', 'heading', 'roll', 'pitch', dan 'control_mode' —
    semuanya sudah diisi loop utama & send_telemetry.
    """
    return dict(state)


# Kalibrasi default misi 5. HARUS cocok resolusi stream kamera (1280x720).
# Override per-kamera lewat env M5_CALIB_BOTTOM / M5_CALIB_WALL; set ke string
# kosong untuk mematikan PBVS sama sekali (IBVS murni, tak butuh kalibrasi).
# dwe_v3 (24 Agu, dataset "Calib image/" — 7 sesi rekaman checkerboard 10x7
# SUNGGUHAN DI DALAM AIR): RMS 0.87px dari 58 pose (tools/calibrate_camera.py
# --trim-rounds 4), jauh lebih baik drpd dwe_trial2 (RMS 2.36px) yang aktif
# sebelumnya.
M5_CALIB_DEFAULT = "vision/calibration/dwe_v3.npz"

# Config geometri arena, dipisah koma. Default menunjuk config lomba, BUKAN
# kosong: default kosong berarti konstanta modul fsm/mission5.py, yang HANYA
# benar bila kolam persis 0,9 m (Guidebook mengukur hook 0,45 m dari DASAR,
# sedangkan HOOK_DEPTH default 0,45 m dari PERMUKAAN).
#
# Sengaja di sini, bukan Environment= di unit systemd: unit hanya ada di Pi dan
# tidak ikut ter-deploy, jadi env yang lupa dipasang = diam-diam pakai default
# modul. Path relatif thd WorkingDirectory service (=~/rov-agent). Gagal-lunak
# bila file tak ada (lihat Mission5Runner._apply_configs).
M5_CONFIG_DEFAULT = "config/rov_tuned.yaml,config/pool_kki_running.yaml"


def setup_mission5_runner():
    """Siapkan runner FSM. Gagal-lunak: None berarti misi 5 tak tersedia."""
    global mission5_runner
    try:
        from rov_mission5_bridge import (Mission5CommandAdapter,
                                         Mission5TelemetryAdapter,
                                         Mission5Runner)
    except Exception as e:
        print(f"[M5] bridge tidak tersedia: {e}")
        return None

    cmd = Mission5CommandAdapter(
        set_axis=_fsm_set_axis,
        set_gripper=_fsm_set_gripper,
        arm=send_arm_disarm,
        emergency_stop=_fsm_emergency_stop,
    )
    telem = Mission5TelemetryAdapter(read_state=_fsm_read_state)
    cfg = {
        "vision_source": os.environ.get("M5_VISION_SOURCE", "usb"),
        # PERBAIKAN 23 Agu 2026: default lama (bottom=8080, wall=8081) TERBALIK
        # dari pemasangan fisik — dikonfirmasi operator: 8080 menghadap DEPAN
        # (gripper), 8081 menghadap BAWAH (dasar kolam). QR docking (butuh
        # kamera BAWAH) & drift sensing (rov_drift.py) sama-sama memakai
        # bottom_url ini, jadi keduanya ikut salah kamera sebelum baris ini
        # diperbaiki. public/js/config.js CONFIG.CAMERAS punya label
        # BOTTOM/WALL yang SAMA salahnya — belum ikut dibetulkan di sini,
        # lihat percakapan sesi ini sebelum menyalin asumsi role dari sana.
        "bottom_url": os.environ.get("M5_BOTTOM_URL", "http://127.0.0.1:8081/stream"),
        "wall_url": os.environ.get("M5_WALL_URL", "http://127.0.0.1:8080/stream"),
        # Path relatif thd WorkingDirectory service (=~/rov-agent). Kalibrasi
        # HARUS sepadan resolusi stream (1280x720). Lihat alasan pemilihan
        # dwe_trial2.npz (dan kenapa BUKAN rms terendah) di autonomy/rov_link.py.
        # Salah resolusi tidak lagi lolos diam-diam: _verify_calib_size() di
        # vision/qr_detect.py mematikan PBVS + menulis ERROR bila tak cocok.
        "calib_bottom": os.environ.get("M5_CALIB_BOTTOM", M5_CALIB_DEFAULT),
        "calib_wall": os.environ.get("M5_CALIB_WALL", M5_CALIB_DEFAULT),
        "start_state": os.environ.get("M5_START_STATE", "M5_REDIVE"),
        # Geometri kolam + tuning. WAJIB diisi bila kedalaman kolam bukan 0,9 m
        # (lihat Mission5Runner._apply_configs). Arena lomba:
        #   M5_CONFIG="config/rov_tuned.yaml,config/pool_kki_running.yaml"
        "config_files": os.environ.get("M5_CONFIG", M5_CONFIG_DEFAULT),
        # Dibaca SAAT toggle autonomous, bukan sekarang: operator menekan MARK
        # di tengah misi 3, jauh sesudah runner ini dibuat.
        "read_mark": lambda: (marked_heading, marked_depth),
        # Dibaca SAAT trial berakhir (bukan disimpan sekarang): skor manual
        # misi 2/3 bisa berubah kapan saja sebelum FSM misi 5 selesai, jadi
        # nilai final harus diambil live utk masuk ke run_log.
        "read_mission_counter": lambda: {
            "m2": _tier_score(mission_counter_fails["m2"]),
            "m3": _tier_score(mission_counter_fails["m3"]),
        },
    }
    mission5_runner = Mission5Runner(cmd, telem, config=cfg, log=print)
    return mission5_runner


# =========================
# Drift sensing — kamera BOTTOM, optical flow (lihat rov_drift.py)
# =========================
# Beda dari Mission5Runner: HIDUP TERUS sejak boot, tidak menunggu toggle
# Autonomous — pilot butuh tahu dirinya hanyut saat terbang MANUAL, bukan
# cuma saat misi otonom jalan. Gagal-lunak di setiap langkah (import
# opencv/numpy, muat kalibrasi, buka kamera): kontrol manual tidak pernah
# ikut terganggu, lihat pola yang sama di setup_mission5_runner().
#
# Saklar total (23 Agu 2026): thread ini sendirian memakai ~85% CPU di Pi 4
# dan mengganggu loop kontrol utama — ALT_HOLD naik-turun sendiri saat
# trial, kemungkinan besar jitter MANUAL_CONTROL akibat GIL/CPU direbut
# terus-menerus oleh OpenCV. M5_DRIFT_ENABLED=0 mematikan drift sensing
# SELURUHNYA (thread tidak pernah start) untuk penyelaman yang tak boleh
# terganggu sama sekali, tanpa perlu ubah kode. Default tetap ON tapi
# DRIFT_FPS diturunkan + frame di-downscale (lihat optical_flow.py
# DOWNSCALE) supaya beban CPU jauh lebih kecil dari sebelumnya.
DRIFT_ENABLED = os.environ.get("M5_DRIFT_ENABLED", "1") == "1"
DRIFT_FPS = 5
DRIFT_LOOP_INTERVAL = 1.0 / DRIFT_FPS

# Tahap 2 (kirim OPTICAL_FLOW_RAD ke EKF ArduSub, EK3_SRC1_VELXY=5): default
# OFF. Nyalakan lewat M5_OPTFLOW_TO_EKF=1 HANYA setelah drift_vx/vy di HUD
# sudah divalidasi di kolam beberapa sesi — data flow yang salah tanda/
# berisik di jalur ini akan membuat thruster auto-koreksi ke arah salah,
# beda dari sekadar "kurang berasa" seperti BRAKE_PULSE_*.
OPTFLOW_TO_EKF = os.environ.get("M5_OPTFLOW_TO_EKF", "0") == "1"


def drift_estimator_thread():
    """Baca flow kamera BOTTOM, ubah ke m/s, tulis ke state['drift_vx'/'vy'
    /'quality'/'source']. Kalau OPTFLOW_TO_EKF aktif, relai juga ke EKF
    ArduSub lewat OPTICAL_FLOW_RAD di loop yang sama (satu polling
    latest_flow(), bukan dua thread terpisah yang berebut data).

    Penambal celah IMU: begitu flow kosong (air keruh, fitur hilang, kamera
    freeze), integrate_accel() menambal drift_vx/vy dari SCALED_IMU2 selama
    maksimal IMU_GAP_FILL_MAX_S detik supaya HUD tidak langsung jatuh ke
    "tidak ada data" untuk gangguan sesaat. Begitu flow segar muncul lagi,
    integrator IMU di-snap ke nilai flow itu (bukan menumpuk dari nilai lama)
    — lihat rov_drift.py untuk kenapa ini TIDAK dimaksudkan sebagai dead-
    reckoning berkepanjangan.
    """
    if not DRIFT_ENABLED:
        print("[DRIFT] nonaktif (M5_DRIFT_ENABLED=0) — drift sensing tidak start")
        return

    try:
        import numpy as np
        from vision.optical_flow import FlowTracker
    except Exception as e:
        print(f"[DRIFT] TIDAK BISA START — opencv/numpy gagal diimpor: {e}")
        return

    calib_path = os.environ.get("M5_CALIB_BOTTOM", M5_CALIB_DEFAULT)
    try:
        data = np.load(calib_path)
        focal_px = float(data["K"][0, 0])
    except Exception as e:
        print(f"[DRIFT] gagal muat kalibrasi {calib_path}: {e} — drift sensing nonaktif")
        return

    # Default 8081 (bukan 8080) — lihat catatan perbaikan port kamera BOTTOM
    # di setup_mission5_runner(), M5_BOTTOM_URL dipakai bersama.
    url = os.environ.get("M5_BOTTOM_URL", "http://127.0.0.1:8081/stream")
    tracker = FlowTracker(url=url, fps=DRIFT_FPS)
    tracker.start()
    print(f"[DRIFT] FlowTracker aktif di {url} (focal={focal_px:.1f}px, "
          f"ke-EKF={OPTFLOW_TO_EKF})")

    # Kecepatan yang sedang ditambal integrate_accel() selama celah flow, dan
    # sejak kapan celah itu mulai (None = tidak sedang dalam celah).
    imu_vx, imu_vy = 0.0, 0.0
    gap_started_at = None

    while True:
        flow = tracker.latest_flow(max_age=0.5)
        altitude = (pool_depth - state["depth"]) if pool_depth is not None else None
        have_flow = flow is not None and altitude is not None and altitude > 0

        if have_flow:
            vx, vy = flow_to_velocity(flow["dx"], flow["dy"], flow["dt"], altitude, focal_px)
            state["drift_vx"] = vx
            state["drift_vy"] = vy
            state["drift_quality"] = flow["quality"]
            state["drift_source"] = "flow"
            # Snap-back: begitu flow segar lagi, integrator IMU mulai lagi
            # dari SINI (bukan menumpuk dari tambalan celah sebelumnya).
            imu_vx, imu_vy = vx, vy
            gap_started_at = None

            if OPTFLOW_TO_EKF and master is not None:
                # ponytail: integrated_xgyro/ygyro/zgyro pakai rate SESAAT * dt
                # (bukan integral sungguhan sepanjang window), dan
                # time_delta_distance_us=0 (anggap altitude sinkron dgn flow).
                # Penyederhanaan yang perlu divalidasi di kolam — lihat Tahap 2
                # di plan drift sensing sebelum dipercaya penuh.
                rr, pr, yr = _last_body_rates
                dt = flow["dt"]
                quality = max(0, min(255, flow["quality"]))
                try:
                    with master_lock:
                        master.mav.optical_flow_rad_send(
                            int(time.time() * 1e6),   # time_usec
                            0,                          # sensor_id
                            int(dt * 1e6),              # integration_time_us
                            flow["dx"] / focal_px,      # integrated_x (rad)
                            flow["dy"] / focal_px,       # integrated_y (rad)
                            rr * dt, pr * dt, yr * dt,   # integrated_x/y/zgyro (rad)
                            0.0,                        # temperature
                            quality,                    # quality 0-255
                            0,                           # time_delta_distance_us
                            altitude,                    # distance (m)
                        )
                except Exception as e:
                    print(f"[DRIFT] gagal kirim optical_flow_rad: {e}")
        else:
            now = time.time()
            if gap_started_at is None:
                gap_started_at = now
            if (now - gap_started_at) <= IMU_GAP_FILL_MAX_S:
                ax, ay = _last_body_accel
                imu_vx, imu_vy = integrate_accel(ax, ay, DRIFT_LOOP_INTERVAL, imu_vx, imu_vy)
                state["drift_vx"] = imu_vx
                state["drift_vy"] = imu_vy
                state["drift_quality"] = 0   # flow feature count sungguhan, bukan tambalan
                state["drift_source"] = "imu"
            else:
                # Celah kelewat lama — jangan terus berkembang tanpa batas.
                imu_vx, imu_vy = 0.0, 0.0
                state["drift_vx"] = 0.0
                state["drift_vy"] = 0.0
                state["drift_quality"] = 0
                state["drift_source"] = "none"

        time.sleep(DRIFT_LOOP_INTERVAL)


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
    global marked_heading
    global marked_depth
    global poshold_active
    global heading_target
    global thruster_gain
    global _depth_pulse_until, _depth_pulse_dir

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

                print(f"[CONTROL] {requested}")

                # Sampai 22 Agu 2026 satu-satunya efek toggle ini adalah baris
                # cetak di atas: string diubah, selesai. FSM-nya ada, tapi di
                # autonomy/rov_link.py — program terpisah yang tak pernah jalan
                # di Pi. Sekarang toggle benar-benar menyalakan/mematikannya.
                if requested == "autonomous":
                    # Axis FSM dinolkan DULU: sisa setpoint dari sesi
                    # sebelumnya tidak boleh ikut terbawa saat FSM baru mulai.
                    with fsm_axes_lock:
                        fsm_axes.update({"surge": 0, "sway": 0, "yaw": 0, "heave": 0})

                    # Axis OPERATOR juga dinolkan — ini yang menjatuhkan run 3 & 4
                    # trial 22 Agu. `joystick` menyimpan nilai TERAKHIR yang
                    # dikirim GUI dan tak pernah kembali ke nol sendiri, jadi sisa
                    # >1,5% dari sesi manual sebelumnya langsung dibaca gerbang
                    # kill-switch di joystick_sender sebagai "operator nyetir" —
                    # abort pada detik yang sama dengan toggle, sebelum FSM
                    # sempat menggerakkan apa pun (journal 11:48:05, 11:50:35).
                    # Kill-switch memang harus memicu pada gerakan BARU; nilai
                    # basi bukan gerakan.
                    with joystick_lock:
                        joystick.update({"surge": 0, "sway": 0, "yaw": 0, "heave": 0})

                    # Mode dipindah SESUDAH start() berhasil. Kalau lebih dulu,
                    # joystick_sender melihat "autonomous" selama ~1 detik yang
                    # dihabiskan VisionPipeline.start() membuka kamera — dan
                    # stop() dari kill-switch di jendela itu menemukan _fsm masih
                    # None, lalu diam, meninggalkan thread FSM yatim yang jalan
                    # sampai timeout tanpa satu pun perintahnya dipakai.
                    if mission5_runner is None:
                        print("[M5] runner tidak tersedia — toggle autonomous "
                              "tidak menjalankan FSM (kontrol manual tetap normal)")
                    elif not mission5_runner.start():
                        print("[M5] start GAGAL — tetap di mode manual")
                        continue
                    current_control_mode = "autonomous"
                else:
                    current_control_mode = "manual"
                    if mission5_runner is not None:
                        mission5_runner.stop()

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

                # Masuk mode depth-hold (lihat DEPTH_HOLD_MODES di rov_modes.py)
                # dari mode LAIN mengisi depth_target dari kedalaman saat ini —
                # operator tidak perlu memegang-lalu-lepas heave dulu hanya
                # untuk membuat target awal. Tetap di dalam depth-hold (ganti
                # mode depth-hold ke depth-hold lain) mempertahankan target lama.
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
                # tidak boleh langsung berenang sendiri ke setpoint lama tanpa
                # operator menekan apa pun. Tak ada saklar enabled terpisah lagi
                # (lihat apply_depth_hold_bias) — depth_target SENDIRI dibuang,
                # supaya operator harus memegang-lalu-lepas heave dulu untuk
                # membentuk target baru sebelum bias aktif lagi.
                with depth_lock:
                    depth_target = None
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

            elif name == "mark_hook":

                # Rekam DI MANA payload digantung, untuk dipakai M5_REDIVE saat
                # misi 5 autonomous. Ditekan operator pada misi 3, tepat setelah
                # payload tersangkut di gantungan dinding.
                #
                # Sengaja tanpa syarat armed/mode: merekam dua angka tidak
                # menggerakkan apa pun, dan memaksa syarat justru membuat
                # operator kehilangan momen yang benar (payload sudah tergantung,
                # ROV mungkin sudah dinetralkan).
                marked_heading = state["heading"]
                marked_depth = state["depth"]
                print(f"[MARK] gantungan ditandai — heading={marked_heading:.0f}° "
                      f"depth={marked_depth:.2f} m")
                send_to_gui({
                    "type": "event",
                    "text": (f"Gantungan ditandai — heading {marked_heading:.0f}° "
                             f"depth {marked_depth:.2f} m"),
                    "level": "ok",
                })

            elif name == "mission_counter":

                # Counter trial Misi 2/3 (Guidebook §4.7.4) — pilot menekan
                # "Gagal, Ulangi" saat melihat sendiri dari kamera bahwa
                # grab/hang gagal (tak ada sensor grip-force di ROV ini).
                # Event "set" mengisi trial ke-N langsung (input angka manual
                # di Setup) — dipakai untuk koreksi salah klik atau eksperimen
                # nilai saat uji coba di kolam sebelum ukuran lomba sebenarnya.
                mission = (value or {}).get("mission")
                event = (value or {}).get("event")
                if event == "reset":
                    mission_counter_fails["m2"] = 0
                    mission_counter_fails["m3"] = 0
                elif event == "fail" and mission in mission_counter_fails:
                    mission_counter_fails[mission] += 1
                elif event == "set" and mission in mission_counter_fails:
                    try:
                        trial = int((value or {}).get("trial"))
                    except (TypeError, ValueError):
                        trial = None
                    if trial is not None and trial >= 1:
                        mission_counter_fails[mission] = trial - 1
                state["mission_counter"] = {
                    "m2_fails": mission_counter_fails["m2"],
                    "m2_score": _tier_score(mission_counter_fails["m2"]),
                    "m3_fails": mission_counter_fails["m3"],
                    "m3_score": _tier_score(mission_counter_fails["m3"]),
                }
                print(f"[COUNTER] {mission or 'ALL'}: {event} → {state['mission_counter']}")
                send_to_gui({
                    "type": "event",
                    "text": f"Counter {mission or 'semua misi'}: {event}",
                    "level": "warn" if event == "fail" else "ok",
                })

            elif name in ("depth_up", "depth_down"):

                if not state["armed"]:
                    continue

                step = -0.05 if name == "depth_up" else 0.05

                with depth_lock:
                    if depth_target is None:
                        depth_target = state["depth"]

                    depth_target = clamp_depth_target(
                        depth_target + step,
                        pool_depth
                    )

                    shown = depth_target

                # STABILIZE: depth_target di atas SUDAH cukup, apply_depth_hold_bias()
                # per-tick yang mendorong terus-menerus (tak ada cascade PID ArduSub
                # di mode ini). ALT_HOLD (depth_hold/poshold): cascade ArduSub
                # sendiri yang menahan, jadi kita cuma memicu pulsa sekali-tembak
                # yang meniru operator menyentuh-lalu-lepas stik heave — lihat
                # DEPTH_PULSE_* di rov_pid.py.
                _mode_now = _current_pixhawk_mode()
                if depth_hold_allowed(_mode_now) and not depth_bias_is_continuous(_mode_now):
                    _depth_pulse_until = time.time() + DEPTH_PULSE_DURATION_S
                    _depth_pulse_dir = -1 if name == "depth_up" else 1

                arah = "NAIK 5 cm" if step < 0 else "TURUN 5 cm"

                print(f"[DEPTH] {arah} -> target {shown:.2f} m")

                send_to_gui({
                    "type": "event",
                    "text": f"Depth target {arah} -> {shown:.2f} m",
                    "level": "ok",
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
    global current_control_mode

    while True:
        # Link Pixhawk sedang putus/menyambung ulang — tidak ada tujuan kirim.
        if master is None:
            time.sleep(JOYSTICK_SEND_INTERVAL)
            continue

        with joystick_lock:
            axes = dict(joystick)
            last_update = last_joystick_update

        # ── Otoritas: FSM vs operator ──────────────────────────────────────
        # Saat autonomous, axis datang dari FSM. TAPI stik operator selalu
        # menang: dorongan nyata di atas deadzone langsung membatalkan
        # autonomy dan mengembalikan kendali. Ambang KILL_SWITCH_DEADZONE
        # hidup di skala yang sama dgn axis GUI (-1000..1000) — lihat
        # autonomy/rov_link.py utk analisis lengkapnya; yang menyaring drift
        # stik adalah deadzone sisi-GUI (0.12), bukan angka ini.
        if current_control_mode == "autonomous":
            operator_nyetir = any(
                abs(axes.get(k, 0)) > KILL_SWITCH_DEADZONE
                for k in ("surge", "sway", "yaw", "heave")
            )
            if operator_nyetir:
                print("[KILL-SWITCH] stik operator digerakkan saat autonomous "
                      "— abort FSM, kembali ke manual")
                current_control_mode = "manual"
                if mission5_runner is not None:
                    mission5_runner.stop()
            else:
                with fsm_axes_lock:
                    axes = dict(fsm_axes)
                # Axis FSM tidak lewat command_listener, jadi fail-safe idle
                # (yang mengukur last_joystick_update) tak berlaku untuknya —
                # kalau tidak di-refresh, FSM selalu dianggap "stale" dan
                # setiap perintahnya diganti netral.
                last_update = time.time()

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

        # Rem heave, hanya ALT_HOLD & hanya setelah full-deflection tertahan
        # lama — lihat apply_heave_brake untuk kenapa beda dari versi pertama
        # yang dicabut.
        mc = apply_heave_brake(mc, eff_axes)

        # Sesudah depth-hold: keduanya menulis field yang berbeda (z vs r), jadi
        # urutannya tidak penting untuk hasil — hanya dijaga konsisten supaya
        # mudah dibaca. Saat stale, axes netral yang dipakai sehingga overlay
        # tidak menyimpulkan "operator sedang memegang stik" dari input basi.
        mc = apply_heading_hold(mc, eff_axes)

        # Rem surge/sway ala DJI — lihat apply_translation_brake untuk batas
        # fisiknya (bukan position hold, cuma menghentikan momentum sendiri).
        mc = apply_translation_brake(mc, eff_axes)

        # Position hold x/y dari optical flow — default OFF, lihat
        # POSITION_HOLD_TO_THRUSTER & docstring apply_position_hold.
        mc = apply_position_hold(mc, eff_axes)

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

    # Runner FSM misi 5. Gagal-lunak: kalau paket autonomy/opencv belum ada di
    # Pi, ini mencetak alasannya dan mengembalikan None — agent tetap jalan
    # penuh untuk kontrol manual, cuma toggle Autonomous yang tidak berefek.
    if setup_mission5_runner() is not None:
        print("[M5] runner siap — toggle Autonomous di GUI akan menjalankan FSM")

    # Tahap 2 drift sensing (default OFF, lihat OPTFLOW_TO_EKF di atas): arahkan
    # EKF ArduSub memakai optical flow sebagai sumber kecepatan horizontal.
    # Kalau OFF, param FC ini tidak disentuh sama sekali.
    if OPTFLOW_TO_EKF:
        set_param("EK3_SRC1_VELXY", 5, mavutil.mavlink.MAV_PARAM_TYPE_INT8)

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

    # Request SCALED_IMU2 (akselerasi body-frame, penambal celah drift IMU —
    # lihat _last_body_accel & drift_estimator_thread)
    try:
        link.mav.command_long_send(
            link.target_system,
            link.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            mavutil.mavlink.MAVLINK_MSG_ID_SCALED_IMU2,
            100000,      # 10 Hz (100000 µs)
            0, 0, 0, 0, 0
        )
    except Exception as e:
        print("[MAV] SCALED_IMU2 request warning:", e)

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
    global depth_target
    global _last_body_rates
    global _last_body_accel

    connect_pixhawk()

    # Thread listener command
    threading.Thread(target=command_listener, daemon=True).start()
    threading.Thread(target=joystick_sender, daemon=True).start()
    threading.Thread(target=qgc_command_receiver, daemon=True).start()
    threading.Thread(target=drift_estimator_thread, daemon=True).start()

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
            _last_body_rates = (msg.rollspeed, msg.pitchspeed, msg.yawspeed)
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
        # SCALED_IMU2: akselerasi body-frame mentah, penambal celah drift IMU
        # (lihat _last_body_accel & drift_estimator_thread). mg -> m/s^2.
        # --------------------------------
        if mtype == "SCALED_IMU2":
            _last_body_accel = (
                msg.xacc * 9.80665 / 1000.0,
                msg.yacc * 9.80665 / 1000.0,
            )

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

            # Disarm dari JALUR MANA PUN membuang depth_target: tombol DISARM,
            # failsafe FC, saklar RC, atau GCS lain. Handler `stop` sudah
            # menanganinya untuk E-Stop, tapi tanpa cek transisi di sini
            # target lama tetap tersimpan selama wahana disarm — dan begitu
            # di-arm ulang (tanpa mode berubah) bias langsung mendorong ke
            # setpoint lama tanpa operator menyentuh apa pun. Sama seperti
            # blok E-Stop: operator harus memegang-lalu-lepas heave dulu untuk
            # membentuk target baru.
            if was_armed and not state["armed"]:
                with depth_lock:
                    if depth_target is not None:
                        depth_target = None
                        print("[DEPTH] target dibuang — vehicle disarm")

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
