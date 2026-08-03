"""Pemetaan gain PID dari GUI ke parameter ArduSub + batas kedalaman kolam.

Dipisah dari rov_agent.py dengan alasan yang sama seperti rov_axes.py,
rov_modes.py, dan rov_params.py: bisa di-unit-test tanpa pymavlink/socket/
hardware. (rov_agent.py bind UDP 14550 di scope modul, jadi mengimpornya dari
test memang tidak mungkin.)

Dua lapis nama yang sengaja dibedakan
    - Nama GUI    : "yaw"/"depth" x "p"/"i"/"d" — yang dikirim halaman Setup
      lewat command {"name": "pid", "value": {"yaw": {...}, "depth": {...}}}.
    - Nama ArduSub: ATC_RAT_YAW_* dan PSC_ACCZ_* — yang benar-benar ditulis ke FC.

Kenapa depth -> PSC_ACCZ_*
    Depth hold ArduSub adalah KASKADE tiga loop: POSZ (posisi -> kecepatan),
    VELZ (kecepatan -> akselerasi), lalu ACCZ (akselerasi -> throttle). Tidak
    ada satu "PID depth". ACCZ dipilih karena itu knob tuning depth-hold
    standar di panduan ArduPilot DAN satu-satunya dari ketiganya yang punya
    P, I, dan D lengkap — jadi form 3 kolom di halaman Setup cocok apa adanya
    tanpa kolom yang menganggur atau menyesatkan.

Kenapa ada BATAS, dan kenapa MENOLAK bukan MENJEPIT
    Default PID di public/js/config.js bukan dalam satuan ArduSub (yaw.p = 2.0
    padahal ATC_RAT_YAW_P di wahana 0.18). Tanpa batas, sekali klik "Apply PID
    Gains" menaikkan gain rate yaw ~11x dan wahana bisa berosilasi hebat.

    Nilai di luar batas DITOLAK, bukan dijepit ke tepi rentang: menjepit
    diam-diam membuat operator mengira menulis 2.0 padahal yang masuk 1.0 —
    salah paham yang jauh lebih berbahaya daripada perintah yang gagal terang-
    terangan.

    RENTANG DI BAWAH INI DITULIS TANGAN, bukan dibaca dari metadata param
    ArduSub — metadata itu memang tidak tersedia offline (lihat
    Planning/PLAN-QgroundControl.md §6). Angkanya konservatif dan bertujuan
    menyaring kesalahan besaran (salah orde), BUKAN menjamin kestabilan.
    Menyetel gain tetap butuh uji kolam.
"""

# MAV_PARAM_TYPE_REAL32. Disalin, bukan diimpor dari pymavlink, supaya modul
# ini tetap murni; sudah dikonfirmasi dari dump nyata Pixhawk (kolom tipe = 9
# untuk keenam param di bawah).
REAL32 = 9

# (sumbu, gain) -> (nama param ArduSub, tipe, minimum, maksimum)
PID_PARAM_MAP = {
    ("yaw", "p"): ("ATC_RAT_YAW_P", REAL32, 0.0, 1.0),
    ("yaw", "i"): ("ATC_RAT_YAW_I", REAL32, 0.0, 1.0),
    ("yaw", "d"): ("ATC_RAT_YAW_D", REAL32, 0.0, 0.05),
    ("depth", "p"): ("PSC_ACCZ_P", REAL32, 0.2, 1.5),
    ("depth", "i"): ("PSC_ACCZ_I", REAL32, 0.0, 3.0),
    ("depth", "d"): ("PSC_ACCZ_D", REAL32, 0.0, 0.4),
}

# Urutan tulis yang stabil: P lalu I lalu D, yaw sebelum depth. Bukan sekadar
# rapi — urutan yang tetap membuat log agent bisa dibandingkan antar percobaan.
PID_WRITE_ORDER = (
    ("yaw", "p"), ("yaw", "i"), ("yaw", "d"),
    ("depth", "p"), ("depth", "i"), ("depth", "d"),
)

# Nama param -> (sumbu, gain). Dipakai sisi GUI/agent untuk arah sebaliknya:
# mengisi form dari PARAM_VALUE yang datang dari FC.
PARAM_TO_PID = {name: key for key, (name, _t, _lo, _hi) in PID_PARAM_MAP.items()}


def pid_param_names():
    """Nama keenam param, dalam urutan tulis. Dipakai untuk param_get massal."""
    return [PID_PARAM_MAP[key][0] for key in PID_WRITE_ORDER]


def resolve_pid_writes(payload):
    """Terjemahkan payload command `pid` jadi daftar tulis param.

    Mengembalikan (writes, rejects):
        writes  = [(nama_param, nilai_float, tipe), ...] siap ke set_param()
        rejects = [(label, alasan), ...] untuk dilaporkan balik ke GUI

    Kunci yang TIDAK ADA di payload dilewati diam-diam (bukan reject): halaman
    Setup boleh mengirim sebagian gain saja. Yang ADA tapi tidak valid selalu
    jadi reject — jangan pernah diam-diam mengabaikan angka yang sudah diketik
    operator.
    """
    writes = []
    rejects = []

    if not isinstance(payload, dict):
        return writes, [("pid", "payload bukan objek")]

    # PID_WRITE_ORDER melewati tiap sumbu tiga kali (p/i/d). Sumbu yang cacat
    # dilaporkan SEKALI saja, kalau tidak operator melihat tiga pesan identik
    # untuk satu kesalahan yang sama.
    axis_ditolak = set()

    for axis, gain in PID_WRITE_ORDER:
        section = payload.get(axis)
        if section is None:
            continue
        if not isinstance(section, dict):
            if axis not in axis_ditolak:
                axis_ditolak.add(axis)
                rejects.append((axis, "bagian bukan objek"))
            continue
        if gain not in section:
            continue

        name, type_id, lo, hi = PID_PARAM_MAP[(axis, gain)]
        raw = section[gain]

        # bool adalah subclass int di Python; True sebagai gain hampir pasti bug.
        if isinstance(raw, bool):
            rejects.append((name, f"nilai bukan angka: {raw!r}"))
            continue

        try:
            value = float(raw)
        except (TypeError, ValueError):
            rejects.append((name, f"nilai bukan angka: {raw!r}"))
            continue

        if value != value or value in (float("inf"), float("-inf")):
            rejects.append((name, "nilai harus finite"))
            continue

        if not (lo <= value <= hi):
            rejects.append((
                name,
                f"{value:g} di luar rentang aman {lo:g}..{hi:g} — "
                f"periksa satuan (nilai ArduSub, bukan skala GUI lama)",
            ))
            continue

        writes.append((name, value, type_id))

    if not writes and not rejects:
        rejects.append(("pid", "tidak ada gain yang dikenali di payload"))

    return writes, rejects


# Setpoint kedalaman yang dipakai setiap kali wahana MASUK mode depth hold.
# Sengaja nilai tetap, bukan "kedalaman saat ini": operator ingin menekan satu
# tombol dan langsung berada di kedalaman kerja yang sama tiap kali. Bisa
# ditimpa dari halaman Setup lewat command `depth_default`.
DEFAULT_DEPTH_TARGET = 0.3


def clamp_depth_target(value, pool_depth=None):
    """Jepit setpoint kedalaman ke rentang yang masuk akal untuk kolam ini.

    Batas atas 0 m (permukaan) selalu berlaku. Batas bawah baru aktif setelah
    operator mengirim `pool_depth` — tanpa itu perilakunya persis seperti
    sebelumnya, jadi lupa mengirimnya tidak pernah membuat keadaan lebih buruk.

    Tanpa batas bawah, menahan gain_inc di kolam 0.9 m bisa menyetel target
    belasan meter: bias throttle memang dibatasi DEPTH_BIAS_LIMIT sehingga
    bukan runaway, tapi ROV tetap ditekan ke dasar tanpa henti dan target butuh
    puluhan penekanan gain_dec untuk kembali masuk akal.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0

    if v != v:              # NaN
        return 0.0

    v = max(0.0, v)

    if pool_depth is not None:
        try:
            limit = float(pool_depth)
        except (TypeError, ValueError):
            return v
        if limit == limit and limit > 0:
            v = min(v, limit)

    return v


def valid_depth_target(value):
    """Kembalikan setpoint kedalaman default yang sah (meter, >= 0), atau None.

    Beda dari valid_pool_depth: 0 m DITERIMA (artinya "tahan di permukaan").
    Penjepitan terhadap pool_depth tidak dilakukan di sini — itu tugas
    clamp_depth_target() saat nilainya benar-benar dipakai, supaya mengubah
    pool_depth belakangan tidak meninggalkan default yang basi.
    """
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    if v < 0:
        return None
    return v


def valid_pool_depth(value):
    """Kembalikan pool depth yang sah (meter, > 0), atau None kalau ditolak."""
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    if v <= 0:
        return None
    return v
