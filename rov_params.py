"""Sumber kebenaran TUNGGAL untuk penanganan parameter ArduSub di sisi Python.

Dipisah dari rov_agent.py dengan alasan yang sama seperti rov_axes.py dan
rov_modes.py: normalisasi nama, koersi nilai, dan pencocokan echo bisa
di-unit-test tanpa pymavlink, socket, atau hardware. (rov_agent.py bind UDP
14550 di scope modul, jadi mengimpornya dari test memang tidak mungkin.)

Kenapa nomor MAV_PARAM_TYPE di-hardcode di sini
    Nilai enum MAV_PARAM_TYPE bagian dari wire format MAVLink — sudah beku
    sejak MAVLink 1 dan tidak akan berubah. Menyalinnya ke sini menjaga modul
    ini bebas pymavlink. Nomor yang sama juga dipakai kolom terakhir file dump
    parameters_ardusub.params (2=INT8, 4=INT16, 6=INT32, 9=REAL32), sehingga
    modul ini bisa dipakai membaca dump itu juga.

Kenapa nilai selalu dikirim sebagai float
    PARAM_SET/PARAM_VALUE MAVLink membawa nilai di satu field float32
    (param_union). Tipe integer tetap ditulis sebagai float yang kebetulan
    bulat; FC yang menafsirkannya kembali sesuai param_type.
"""

import math

# id enum -> (nama, apakah integer)
PARAM_TYPES = {
    1: ("UINT8", True),
    2: ("INT8", True),
    3: ("UINT16", True),
    4: ("INT16", True),
    5: ("UINT32", True),
    6: ("INT32", True),
    7: ("UINT64", True),
    8: ("INT64", True),
    9: ("REAL32", False),
    10: ("REAL64", False),
}

# char[16] di PARAM_SET/PARAM_VALUE — tanpa NUL terminator kalau pas 16.
PARAM_NAME_MAX = 16

# float32 tidak pernah kembali bit-identik dari FC (ArduPilot menyimpan,
# membulatkan, lalu mengirim ulang). Toleransi relatif dipakai saat
# memverifikasi echo agar param_set yang BERHASIL tidak dilaporkan gagal.
REAL_MATCH_REL_TOL = 1e-6
REAL_MATCH_ABS_TOL = 1e-9


def param_type_name(type_id):
    """Nama tipe untuk ditampilkan di GUI; None kalau id tak dikenal."""
    entry = PARAM_TYPES.get(type_id)
    return entry[0] if entry else None


def is_integer_type(type_id):
    """True untuk tipe integer. Tipe tak dikenal dianggap non-integer supaya
    nilai tidak pernah dibulatkan diam-diam."""
    entry = PARAM_TYPES.get(type_id)
    return bool(entry and entry[1])


def normalize_param_name(name):
    """Normalkan nama param jadi bentuk kanonik ArduPilot, atau None kalau tolak.

    Mengembalikan None (bukan melempar) untuk apa pun yang mencurigakan supaya
    pemanggil bisa MENOLAK perintah, bukan menebak param mana yang dimaksud —
    menulis ke param yang salah di FC jauh lebih mahal daripada gagal.
    """
    if not isinstance(name, str):
        return None

    candidate = name.strip().upper()

    if not candidate or len(candidate) > PARAM_NAME_MAX:
        return None

    # Hanya A-Z, 0-9, dan underscore. Menolak non-ASCII & simbol sekaligus
    # menutup upaya menyelipkan NUL/pemisah ke dalam char[16] di wire.
    for ch in candidate:
        if not (ch.isascii() and (ch.isalnum() or ch == "_")):
            return None

    # Tidak ada param ArduPilot yang diawali angka.
    if candidate[0].isdigit():
        return None

    return candidate


def coerce_param_value(value, type_id):
    """Ubah nilai dari GUI jadi float siap kirim ke param_set_send.

    Melempar ValueError untuk nilai yang tidak bisa dikirim (non-numerik,
    NaN, inf) — ini kesalahan pemanggil, bukan kondisi normal.
    """
    if isinstance(value, bool):
        # bool adalah subclass int di Python. Diterima dan dianggap 0/1
        # secara eksplisit supaya tidak lolos diam-diam lewat float().
        numeric = 1.0 if value else 0.0
    elif isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value.strip())
        except ValueError:
            raise ValueError(f"nilai param bukan angka: {value!r}")
    else:
        raise ValueError(f"nilai param bukan angka: {value!r}")

    if not math.isfinite(numeric):
        raise ValueError(f"nilai param harus finite: {value!r}")

    if is_integer_type(type_id):
        # round() half-to-even sudah cukup: GUI mengirim nilai yang memang
        # dimaksud bulat, pembulatan di sini hanya membersihkan galat float.
        numeric = float(round(numeric))

    return numeric


def param_matches(sent, echoed, type_id):
    """True kalau PARAM_VALUE yang kembali dari FC cocok dengan yang dikirim.

    Ini satu-satunya bukti param_set berhasil: ArduPilot DIAM saat menolak
    (nama tak dikenal / nilai di luar rentang), tidak mengirim NACK.
    """
    try:
        a = float(sent)
        b = float(echoed)
    except (TypeError, ValueError):
        return False

    if not (math.isfinite(a) and math.isfinite(b)):
        return False

    if is_integer_type(type_id):
        return round(a) == round(b)

    return math.isclose(a, b, rel_tol=REAL_MATCH_REL_TOL, abs_tol=REAL_MATCH_ABS_TOL)


def decode_param_id(param_id):
    """Bersihkan param_id dari PARAM_VALUE jadi str.

    pymavlink bisa mengembalikan str, bytes, atau bytearray tergantung dialek
    dan versi; yang pas 16 karakter tidak ber-NUL, yang lebih pendek dipadding
    NUL. Keduanya harus jadi nama yang sama.
    """
    if isinstance(param_id, (bytes, bytearray)):
        param_id = param_id.decode("utf-8", errors="replace")
    if not isinstance(param_id, str):
        return ""
    return param_id.split("\x00", 1)[0].strip()


def parse_param_dump(text):
    """Baca file dump param format QGroundControl -> {nama: (nilai, tipe)}.

    Format (parameters_ardusub.params di root repo):
        # komentar
        <sysid>\\t<compid>\\t<NAMA>\\t<nilai>\\t<tipe>

    Dipakai mode simulasi supaya halaman Vehicle bisa dikembangkan tanpa FC.
    Baris yang tidak cocok dilewati, bukan menggagalkan seluruh file — dump
    ini artefak eksternal, satu baris rusak tidak boleh mematikan server.
    """
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if len(parts) < 5:
            continue

        name = normalize_param_name(parts[2])
        if name is None:
            continue

        try:
            value = float(parts[3])
            type_id = int(parts[4])
        except ValueError:
            continue

        if type_id not in PARAM_TYPES:
            continue

        out[name] = (value, type_id)

    return out
