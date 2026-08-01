"""Sumber kebenaran TUNGGAL untuk pilot mode ArduSub di sisi Python.

Dipisah dari rov_agent.py dengan alasan yang sama seperti rov_axes.py: logika
pemetaan nama mode dan aturan keselamatan per-mode bisa di-unit-test tanpa
pymavlink, socket, atau hardware.

Dua lapis nama yang sengaja dibedakan:
    - Nama GUI  : "manual", "stabilize", "depth_hold", "acro" — yang dikirim
      dashboard lewat command JSON {"name": "pilot_mode", "value": ...}.
    - Nama ArduSub : "MANUAL", "STABILIZE", "ALT_HOLD", "ACRO" — yang dipakai
      master.mode_mapping() dan yang dilaporkan balik lewat HEARTBEAT.

Tentang ACRO
    Di ACRO tidak ada stabilisasi attitude: stik memerintahkan RATE (kecepatan
    sudut), bukan sudut. Konsekuensi yang paling mudah terlewat adalah pada
    THROTTLE — MANUAL_CONTROL.z = 500 di ALT_HOLD berarti "tahan kedalaman",
    tapi di ACRO artinya cuma "tidak ada dorongan vertikal". Karena itu ACRO
    sengaja TIDAK masuk DEPTH_HOLD_MODES: bias depth-hold tidak boleh ikut
    campur saat tidak ada yang menstabilkan wahana.
"""

# Nama GUI -> nama mode ArduSub.
PILOT_MODE_MAP = {
    "manual": "MANUAL",
    "stabilize": "STABILIZE",
    "depth_hold": "ALT_HOLD",
    "acro": "ACRO",
}

# Mode yang punya kendali kedalaman/attitude dari autopilot, sehingga menggeser
# setpoint kedalaman lewat bias throttle masuk akal.
#
# MANUAL tidak masuk: tidak ada yang menahan kedalaman, bias hanya akan
# mendorong wahana tanpa umpan balik.
# ACRO tidak masuk: lihat catatan di docstring modul.
DEPTH_HOLD_MODES = frozenset({"STABILIZE", "ALT_HOLD"})

# Mode yang memerlukan konfirmasi/peringatan eksplisit ke operator.
RISKY_MODES = frozenset({"ACRO"})

ACRO_WARNING = (
    "ACRO: tanpa stabilisasi attitude. Stik = rate, dan throttle netral "
    "TIDAK menahan kedalaman. Depth hold dinonaktifkan."
)


def resolve_pilot_mode(name):
    """Terjemahkan nama mode dari GUI ke nama mode ArduSub.

    Menerima huruf besar/kecil bebas dan spasi di tepi. Mengembalikan None
    untuk nama yang tidak dikenal (termasuk None / non-string), supaya pemanggil
    bisa menolak perintah alih-alih diam-diam menebak mode.
    """
    if not isinstance(name, str):
        return None
    return PILOT_MODE_MAP.get(name.strip().lower())


def depth_hold_allowed(mode):
    """True kalau bias depth-hold + gain_inc/gain_dec boleh aktif di `mode`.

    `mode` adalah nama ArduSub (mis. dari HEARTBEAT atau requested_mode).
    """
    return mode in DEPTH_HOLD_MODES


def is_risky_mode(mode):
    """True untuk mode yang perlu peringatan menonjol ke operator."""
    return mode in RISKY_MODES
