"""Sumber kebenaran untuk pilot mode ArduSub di sisi Python.

Nama GUI:
    manual
    stabilize
    depth_hold
    poshold

Nama ArduSub:
    MANUAL
    STABILIZE
    ALT_HOLD

Depth Hold menggunakan ALT_HOLD ArduSub secara langsung.
Tidak ada lagi depth-set atau depth-bias custom di Python.

POSHOLD adalah overlay heading-hold sisi Python yang berjalan
di atas ALT_HOLD ArduSub. Overlay ini menahan heading, bukan
posisi horizontal.
"""

# =========================
# Pemetaan pilot mode
# =========================

PILOT_MODE_MAP = {
    "manual": "MANUAL",
    "stabilize": "STABILIZE",
    "depth_hold": "ALT_HOLD",
    "poshold": "ALT_HOLD",
}

# Nama GUI untuk station-keep.
POSHOLD_MODE = "poshold"

# Mode ArduSub tempat overlay POSHOLD berjalan.
# POSHOLD firmware tidak digunakan karena ROV tidak mempunyai
# sumber posisi horizontal yang diperlukan.
POSHOLD_BASE_MODE = PILOT_MODE_MAP[POSHOLD_MODE]


def resolve_pilot_mode(name):
    """Terjemahkan nama mode GUI ke nama mode ArduSub.

    Menerima huruf besar/kecil dan spasi di tepi.
    Mengembalikan None jika nama mode tidak dikenal.
    """
    if not isinstance(name, str):
        return None

    return PILOT_MODE_MAP.get(name.strip().lower())


def is_poshold_request(name):
    """True jika nama mode GUI meminta overlay POSHOLD."""
    if not isinstance(name, str):
        return False

    return name.strip().lower() == POSHOLD_MODE


def poshold_mode_ok(mode):
    """True jika overlay heading-hold boleh bekerja pada mode tersebut."""
    return mode == POSHOLD_BASE_MODE