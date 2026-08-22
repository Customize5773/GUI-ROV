"""Helper murni untuk stream MAVLink generik ke GUI (halaman Analyze).

Sama seperti rov_params.py: dipisah dari rov_agent.py supaya bisa di-unit-test
tanpa pymavlink/socket/hardware.

Kenapa perlu throttle per message-type
    MAVLink Inspector menampilkan SEMUA message, bukan hanya yang sudah dipakai
    telemetri. connect_pixhawk() meminta MAV_DATA_STREAM_ALL 10 Hz, jadi tanpa
    pembatas satu paket UDP + satu frame WebSocket lahir untuk setiap message
    dari setiap stream sekaligus. Yang dibatasi per-TYPE (bukan total) supaya
    message jarang seperti STATUSTEXT tidak pernah kalah oleh ATTITUDE yang
    datang terus-menerus.
"""

# Batas default: 10 Hz per message-type. Cukup untuk mata manusia dan untuk
# grafik 60 detik di halaman Analyze, tapi ~10x lebih hemat daripada raw.
DEFAULT_STREAM_HZ = 10.0

# Stream dimatikan sendiri kalau GUI berhenti memperbaruinya. Tab Analyze
# mengirim keepalive tiap 10 detik; batas ini memberi ruang beberapa kali
# gagal sebelum firehose ditutup. Tanpa ini, tab yang ditutup mendadak (atau
# WS putus) meninggalkan stream jalan terus tanpa ada yang mendengarkan.
STREAM_KEEPALIVE_TIMEOUT = 30.0

# Field yang isinya panjang/berulang dan tidak berguna di Inspector.
_SKIP_FIELDS = frozenset({"mavpackettype"})

# Batas panjang list yang ikut dikirim (mis. voltages[] di BATTERY_STATUS).
_MAX_LIST_LEN = 16


class RateLimiter:
    """Pembatas laju per key, mengikuti idiom throttle yang sudah dipakai
    send_telemetry()/gripper_sender() di rov_agent.py: simpan timestamp
    terakhir, bandingkan dengan `now` yang DIBERIKAN pemanggil.

    `now` sengaja parameter, bukan time.time() internal — persis alasan yang
    sama seperti resolve_manual_packet() di rov_axes.py: supaya bisa diuji
    tanpa menunggu waktu nyata.
    """

    # Toleransi "boleh sedikit lebih awal". Sumbernya sendiri periodik (stream
    # 10 Hz dari FC), jadi tanpa ini galat float sebesar satu ULP membuat tiap
    # pesan kedua tertahan dan laju efektif jadi SETENGAH dari yang diminta.
    # Kirim 1% lebih awal tidak ada ruginya; kehilangan separuh data ada.
    _EARLY_TOLERANCE = 0.01

    def __init__(self, hz=DEFAULT_STREAM_HZ):
        if hz <= 0:
            raise ValueError("hz harus > 0")
        self.interval = 1.0 / float(hz)
        self._min_gap = self.interval * (1.0 - self._EARLY_TOLERANCE)
        self._last = {}

    def allow(self, key, now):
        """True (dan catat) kalau `key` sudah boleh dikirim lagi pada `now`."""
        previous = self._last.get(key)
        if previous is not None and (now - previous) < self._min_gap:
            return False
        self._last[key] = now
        return True

    def reset(self):
        """Lupakan seluruh riwayat — dipanggil saat link MAVLink putus, supaya
        message pertama setelah sambung ulang tidak tertahan sisa timestamp."""
        self._last.clear()


def sanitize_fields(fields):
    """Ubah hasil msg.to_dict() jadi dict yang aman di-json.dumps.

    pymavlink mengembalikan bytearray (mis. STATUSTEXT.text), tuple/list
    (mis. voltages[]), dan sesekali float non-finite — ketiganya membuat
    json.dumps() gagal atau menghasilkan JSON yang ditolak JSON.parse browser
    (NaN/Infinity bukan JSON valid). Satu message rusak tidak boleh mematikan
    seluruh stream, jadi semuanya dijinakkan di sini.
    """
    out = {}
    if not isinstance(fields, dict):
        return out

    for key, value in fields.items():
        if key in _SKIP_FIELDS:
            continue
        out[key] = _sanitize_value(value)

    return out


def _sanitize_value(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        # NaN/Infinity bukan JSON valid; kirim None agar GUI menampilkan "—"
        # alih-alih membuat JSON.parse melempar dan membuang seluruh frame.
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value

    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace").split("\x00", 1)[0]

    if isinstance(value, (list, tuple)):
        return [_sanitize_value(v) for v in value[:_MAX_LIST_LEN]]

    if value is None or isinstance(value, str):
        return value

    return str(value)


def stream_still_wanted(last_request_ts, now, timeout=STREAM_KEEPALIVE_TIMEOUT):
    """False kalau GUI sudah terlalu lama tidak memperbarui permintaan stream.

    last_request_ts = None berarti belum pernah diminta sama sekali.
    """
    if last_request_ts is None:
        return False
    return (now - last_request_ts) < timeout
