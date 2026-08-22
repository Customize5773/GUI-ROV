"""Pemetaan gain PID dari GUI ke parameter ArduSub.

Dipisah dari rov_agent.py supaya pemetaan parameter dan validasi
gain dapat di-unit-test tanpa pymavlink, socket, atau hardware.

Nama GUI:
    yaw / roll / pitch / depth
    p / i / d

Nama parameter ArduSub:
    ATC_RAT_YAW_*
    ATC_RAT_RLL_*
    ATC_RAT_PIT_*
    PSC_ACCZ_*
"""

# MAV_PARAM_TYPE_REAL32.
# Disalin agar modul tetap murni dan tidak membutuhkan pymavlink.
REAL32 = 9


# (sumbu, gain) -> (nama parameter ArduSub, tipe, minimum, maksimum)
PID_PARAM_MAP = {
    ("yaw", "p"): ("ATC_RAT_YAW_P", REAL32, 0.0, 1.0),
    ("yaw", "i"): ("ATC_RAT_YAW_I", REAL32, 0.0, 1.0),
    ("yaw", "d"): ("ATC_RAT_YAW_D", REAL32, 0.0, 0.05),

    ("roll", "p"): ("ATC_RAT_RLL_P", REAL32, 0.0, 1.0),
    ("roll", "i"): ("ATC_RAT_RLL_I", REAL32, 0.0, 1.0),
    ("roll", "d"): ("ATC_RAT_RLL_D", REAL32, 0.0, 0.05),

    ("pitch", "p"): ("ATC_RAT_PIT_P", REAL32, 0.0, 1.0),
    ("pitch", "i"): ("ATC_RAT_PIT_I", REAL32, 0.0, 1.0),
    ("pitch", "d"): ("ATC_RAT_PIT_D", REAL32, 0.0, 0.05),

    # Depth Hold native ArduSub menggunakan cascade controller.
    # PSC_ACCZ_* adalah loop acceleration/throttle.
    ("depth", "p"): ("PSC_ACCZ_P", REAL32, 0.2, 1.5),
    ("depth", "i"): ("PSC_ACCZ_I", REAL32, 0.0, 3.0),
    ("depth", "d"): ("PSC_ACCZ_D", REAL32, 0.0, 0.4),
}


# Urutan penulisan parameter.
PID_WRITE_ORDER = (
    ("yaw", "p"),
    ("yaw", "i"),
    ("yaw", "d"),

    ("roll", "p"),
    ("roll", "i"),
    ("roll", "d"),

    ("pitch", "p"),
    ("pitch", "i"),
    ("pitch", "d"),

    ("depth", "p"),
    ("depth", "i"),
    ("depth", "d"),
)


# Nama parameter -> (sumbu, gain).
PARAM_TO_PID = {
    name: key
    for key, (name, _type, _lo, _hi) in PID_PARAM_MAP.items()
}


def pid_param_names():
    """Mengembalikan nama parameter PID dalam urutan penulisan."""
    return [
        PID_PARAM_MAP[key][0]
        for key in PID_WRITE_ORDER
    ]


def resolve_pid_writes(payload):
    """Terjemahkan payload command PID menjadi daftar parameter ArduSub.

    Returns:
        writes:
            [(nama_param, nilai_float, tipe), ...]

        rejects:
            [(label, alasan), ...]

    Field yang tidak dikirim dilewati.
    Field yang dikirim tetapi tidak valid ditolak.
    """

    writes = []
    rejects = []

    if not isinstance(payload, dict):
        return writes, [
            ("pid", "payload bukan objek")
        ]

    axis_ditolak = set()

    for axis, gain in PID_WRITE_ORDER:

        section = payload.get(axis)

        if section is None:
            continue

        if not isinstance(section, dict):
            if axis not in axis_ditolak:
                axis_ditolak.add(axis)
                rejects.append(
                    (axis, "bagian bukan objek")
                )
            continue

        if gain not in section:
            continue

        name, type_id, lo, hi = PID_PARAM_MAP[
            (axis, gain)
        ]

        raw = section[gain]

        # bool adalah subclass int di Python.
        if isinstance(raw, bool):
            rejects.append(
                (name, f"nilai bukan angka: {raw!r}")
            )
            continue

        try:
            value = float(raw)
        except (TypeError, ValueError):
            rejects.append(
                (name, f"nilai bukan angka: {raw!r}")
            )
            continue

        # Tolak NaN dan infinity.
        if value != value or value in (
            float("inf"),
            float("-inf"),
        ):
            rejects.append(
                (name, "nilai harus finite")
            )
            continue

        # Nilai di luar batas ditolak, bukan dijepit.
        if not (lo <= value <= hi):
            rejects.append(
                (
                    name,
                    f"{value:g} di luar rentang aman "
                    f"{lo:g}..{hi:g}"
                )
            )
            continue

        writes.append(
            (name, value, type_id)
        )

    if not writes and not rejects:
        rejects.append(
            ("pid", "tidak ada gain yang dikenali di payload")
        )

    return writes, rejects


def valid_pool_depth(value):
    """Kembalikan kedalaman kolam yang valid dalam meter.

    Mengembalikan None jika nilai tidak valid.
    """

    if isinstance(value, bool):
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if value != value:
        return None

    if value in (
        float("inf"),
        float("-inf"),
    ):
        return None

    if value <= 0:
        return None

    return value