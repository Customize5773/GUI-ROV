"""Sumber kebenaran TUNGGAL untuk pilot mode ArduSub di sisi Python.

Dipisah dari rov_agent.py dengan alasan yang sama seperti rov_axes.py: logika
pemetaan nama mode dan aturan keselamatan per-mode bisa di-unit-test tanpa
pymavlink, socket, atau hardware.

Dua lapis nama yang sengaja dibedakan:
    - Nama GUI  : "manual", "stabilize", "depth_hold", "poshold" — yang
      dikirim dashboard lewat command JSON {"name": "pilot_mode", "value": ...}.
    - Nama ArduSub : "MANUAL", "STABILIZE", "ALT_HOLD" — yang dipakai
      master.mode_mapping() dan yang dilaporkan balik lewat HEARTBEAT.

Kenapa "stabilize" adalah mode STABILIZE sungguhan
    Di STABILIZE attitude (roll/pitch) distabilkan, TAPI kedalaman tidak —
    ArduSub hanya menjalankan cascade PID kedalaman (POSZ->VELZ->PSC_ACCZ) di
    ALT_HOLD. Bias depth-set karena itu digeser mengikuti mode ini (lihat
    DEPTH_HOLD_MODES di bawah): tanpa cascade PID ArduSub yang menahan
    kedalaman, bias jadi satu-satunya yang mendorong wahana ke setpoint —
    dorongan open-loop, bukan koreksi di atas PID firmware.

Mode vs depth-set (sengaja TERPISAH)
    ALT_HOLD = "tahan kedalaman tempat wahana berada sekarang", dikerjakan
    sepenuhnya oleh cascade PID ArduSub. POSHOLD = ALT_HOLD + overlay heading
    hold sisi Python. Keduanya TIDAK LAGI menjadi syarat bias depth-set —
    lihat DEPTH_HOLD_MODES.

    "Depth-set" (menuju sebuah setpoint yang DIREKAM operator lewat tombol SET,
    lalu dinyalakan lewat tombol ON/OFF) adalah fitur TERSENDIRI. Masuk
    STABILIZE tidak menyalakannya, dan menyalakannya tidak memindahkan mode.

    Yang TETAP menjadi syarat: bias depth-set hanya dikirim saat mode ArduSub
    ada di DEPTH_HOLD_MODES.

Kenapa "poshold" menunjuk ALT_HOLD, bukan mode POSHOLD ArduSub
    POSHOLD ArduSub butuh estimasi posisi horizontal yang dipercaya EKF
    (EK3_SRC1_POSXY = 3, yaitu GPS — lihat parameters_ardusub.params). Di bawah
    air tidak ada GPS, dan wahana ini tidak punya DVL, optical flow, maupun
    sumber VISION_POSITION_ESTIMATE. Meminta mode POSHOLD firmware karena itu
    akan ditolak, atau lebih buruk: diterima dengan estimasi posisi yang
    ngawur.

    Yang dipakai sebagai gantinya adalah OVERLAY sisi Python di atas ALT_HOLD
    (lihat apply_heading_hold di rov_agent.py + rov_heading.py): kedalaman
    ditahan cascade PID ArduSub, heading ditahan koreksi P dari sisi Pi.

    KONSEKUENSI YANG HARUS DIKETAHUI OPERATOR: mode ini menahan KEDALAMAN dan
    HEADING, BUKAN posisi x/y. Arus lateral tetap menggeser wahana dan tidak
    ada sensor yang bisa mendeteksinya.

    Karena "poshold" dan "depth_hold" memetakan ke mode ArduSub yang SAMA,
    HEARTBEAT tidak bisa membedakan keduanya. Yang membedakan adalah flag
    poshold_active di agent, yang dipantulkan lewat telemetry (state["poshold"])
    dan dipakai GUI untuk menyorot tab yang benar.
"""

# Nama GUI -> nama mode ArduSub.
PILOT_MODE_MAP = {
    "manual": "MANUAL",
    "stabilize": "STABILIZE",
    "depth_hold": "ALT_HOLD",
    # Overlay heading-hold sisi Python, BUKAN mode POSHOLD firmware — lihat
    # docstring modul.
    "poshold": "ALT_HOLD",
}

# Nama GUI untuk station-keep. Dipakai agent supaya tidak membandingkan string
# mentah di tengah handler command.
POSHOLD_MODE = "poshold"

# Mode yang jadi syarat bias depth-set (lihat depth_bias_engaged()).
#
# INI BUKAN saklar depth-set. Berada di mode ini tidak menyalakan depth-set,
# dan menyalakan depth-set tidak memindahkan mode — lihat depth_bias_engaged().
#
# MANUAL tidak masuk: tidak ada yang menahan kedalaman, bias hanya akan
# mendorong wahana tanpa umpan balik.
# STABILIZE: bias jadi satu-satunya yang menahan kedalaman (tidak ada cascade
# PID ArduSub di mode ini).
# ALT_HOLD (menaungi GUI "depth_hold" & "poshold"): bias mendorong
# MANUAL_CONTROL.z menjauh dari netral — mekanisme yang SAMA dengan operator
# menyentuh stik heave untuk memindahkan target ALT_HOLD (lihat gerbang #3 di
# apply_depth_hold_bias, rov_agent.py). Permintaan pilot 2026-08-22: depth
# up/down harus aktif di semua mode kecuali Manual.
DEPTH_HOLD_MODES = frozenset({"STABILIZE", "ALT_HOLD"})

# Mode ArduSub tempat overlay POSHOLD menumpang. SENGAJA BUKAN DEPTH_HOLD_MODES:
# itu syarat bias depth-set (kini STABILIZE), ini syarat overlay heading-hold
# (ALT_HOLD). Dulu keduanya dipakai lewat satu predikat yang sama, jadi begitu
# DEPTH_HOLD_MODES dipersempit ke STABILIZE, gerbang POSHOLD ikut mati diam-diam.
POSHOLD_BASE_MODE = PILOT_MODE_MAP[POSHOLD_MODE]


def resolve_pilot_mode(name):
    """Terjemahkan nama mode dari GUI ke nama mode ArduSub.

    Menerima huruf besar/kecil bebas dan spasi di tepi. Mengembalikan None
    untuk nama yang tidak dikenal (termasuk None / non-string), supaya pemanggil
    bisa menolak perintah alih-alih diam-diam menebak mode.
    """
    if not isinstance(name, str):
        return None
    return PILOT_MODE_MAP.get(name.strip().lower())


def is_poshold_request(name):
    """True kalau nama mode dari GUI meminta overlay station-keep.

    Sengaja terpisah dari resolve_pilot_mode(): keduanya berujung di ALT_HOLD,
    jadi hasil resolve TIDAK cukup untuk tahu apakah overlay harus hidup.
    """
    if not isinstance(name, str):
        return False
    return name.strip().lower() == POSHOLD_MODE


def depth_hold_allowed(mode):
    """True kalau bias depth-set boleh aktif di `mode`.

    Syarat MODE saja — saklar operator diperiksa depth_bias_engaged().

    `mode` adalah nama ArduSub (mis. dari HEARTBEAT atau requested_mode).
    """
    return mode in DEPTH_HOLD_MODES


def poshold_mode_ok(mode):
    """True kalau overlay heading-hold boleh menulis ke MANUAL_CONTROL.r di `mode`.

    Syarat MODE saja — permintaan operator diperiksa poshold_active di agent.
    Sengaja terpisah dari depth_hold_allowed(): lihat POSHOLD_BASE_MODE.
    """
    return mode == POSHOLD_BASE_MODE


def depth_bias_engaged(target, mode, heave, heave_epsilon):
    """True kalau bias throttle depth-set boleh benar-benar dikirim sekarang.

    Semua gerbang depth-set dikumpulkan di satu fungsi murni supaya bisa diuji
    tanpa pymavlink/socket — pemanggilnya (apply_depth_hold_bias di
    rov_agent.py) tinggal membaca state global lalu menyerahkannya ke sini.

    TIGA gerbang, tanpa saklar ON/OFF operator terpisah (desain 21 Agu 2026,
    lihat apply_depth_hold_bias): target mengikuti kedalaman aktual selagi
    stik heave dipegang, dan terkunci begitu stik dilepas. "Aktif" jadi murni
    fungsi target+mode+heave, bukan state tersendiri yang bisa lupa dimatikan.

      - `target is None` (belum pernah menyentuh heave sejak boot/SET awal)
        DIBEDAKAN dari target 0.0 yang sah. Tanpa itu, "belum pernah diisi"
        akan berarti "tahan di permukaan" dan wahana naik sendiri begitu
        masuk mode yang tepat.
      - `mode` harus mode depth hold (lihat DEPTH_HOLD_MODES). Ini bukan
        coupling arah sebaliknya, melainkan syarat fisik: kompensasi deadzone
        yang dipakai bias mengasumsikan ada cascade PID kedalaman yang
        menerimanya (atau, untuk STABILIZE, bias itu sendiri jadi satu-satunya
        yang mendorong). Di MANUAL bias jadi dorongan open-loop tanpa umpan
        balik sama sekali.
      - stik heave yang dipegang selalu menang atas setpoint mana pun.
    """
    if target is None:
        return False
    if not depth_hold_allowed(mode):
        return False
    return abs(heave) <= heave_epsilon
