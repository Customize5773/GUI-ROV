"""Sumber kebenaran TUNGGAL untuk pilot mode ArduSub di sisi Python.

Dipisah dari rov_agent.py dengan alasan yang sama seperti rov_axes.py: logika
pemetaan nama mode dan aturan keselamatan per-mode bisa di-unit-test tanpa
pymavlink, socket, atau hardware.

Dua lapis nama yang sengaja dibedakan:
    - Nama GUI  : "manual", "stabilize", "depth_hold", "acro" — yang dikirim
      dashboard lewat command JSON {"name": "pilot_mode", "value": ...}.
    - Nama ArduSub : "MANUAL", "STABILIZE", "ALT_HOLD", "ACRO" — yang dipakai
      master.mode_mapping() dan yang dilaporkan balik lewat HEARTBEAT.

Kenapa "stabilize" sekarang alias ALT_HOLD
    Di STABILIZE attitude (roll/pitch) distabilkan, TAPI kedalaman tidak —
    ArduSub hanya menjalankan cascade PID kedalaman (POSZ->VELZ->PSC_ACCZ) di
    ALT_HOLD. Artinya MANUAL_CONTROL.z = 500 di ALT_HOLD berarti "tahan
    kedalaman", sedangkan di STABILIZE cuma "tidak ada dorongan vertikal":
    wahana yang tidak neutrally buoyant tenggelam/naik kecuali pilot menahan
    stick throttle terus-menerus. Untuk operasi kolam dangkal ini tidak pernah
    menjadi pilihan yang berguna, sementara ALT_HOLD sudah memberi attitude
    stabilized DAN depth hold sekaligus. Jadi kedua nama GUI ("stabilize" dan
    "depth_hold") sekarang mengarah ke ALT_HOLD, dan tab STABILIZE dihapus dari
    dashboard. Aliasnya dipertahankan (bukan dihapus) supaya profil joystick
    tersimpan dengan aksi `mode_stabilize` tetap bekerja.

Tentang ACRO
    Di ACRO tidak ada stabilisasi attitude sama sekali: stik memerintahkan RATE
    (kecepatan sudut), bukan sudut, dan throttle netral juga tidak menahan
    kedalaman. Karena itu ACRO tidak masuk DEPTH_HOLD_MODES: bias depth-hold
    tidak boleh ikut campur saat tidak ada cascade PID kedalaman yang menahan
    wahana.
"""

# Nama GUI -> nama mode ArduSub. "stabilize" sengaja menunjuk ALT_HOLD juga —
# lihat catatan di docstring modul.
PILOT_MODE_MAP = {
    "manual": "MANUAL",
    "stabilize": "ALT_HOLD",
    "depth_hold": "ALT_HOLD",
    "acro": "ACRO",
}

# Mode yang menjalankan cascade PID kedalaman dari autopilot, sehingga
# menggeser setpoint kedalaman lewat bias throttle masuk akal.
#
# MANUAL tidak masuk: tidak ada yang menahan kedalaman, bias hanya akan
# mendorong wahana tanpa umpan balik.
# STABILIZE tidak masuk: attitude distabilkan, tapi kedalaman tidak — lihat
# catatan di docstring modul. Mode ini tidak bisa lagi diminta dari GUI, tapi
# wahana masih bisa berada di sana lewat saklar RC / GCS lain.
# ACRO tidak masuk: lihat catatan di docstring modul.
DEPTH_HOLD_MODES = frozenset({"ALT_HOLD"})

# Mode yang memerlukan konfirmasi eksplisit SEBELUM diminta operator. STABILIZE
# tidak ada di sini karena tidak bisa lagi diminta dari GUI — tapi peringatannya
# tetap hidup di MODE_WARNINGS untuk kasus wahana masuk STABILIZE dari luar.
RISKY_MODES = frozenset({"ACRO"})

ACRO_WARNING = (
    "ACRO: tanpa stabilisasi attitude. Stik = rate, dan throttle netral "
    "TIDAK menahan kedalaman. Depth hold dinonaktifkan."
)

STABILIZE_WARNING = (
    "STABILIZE: attitude distabilkan, tapi kedalaman TIDAK. Throttle netral "
    "berarti dorongan vertikal nol, bukan tahan kedalaman. Depth hold "
    "dinonaktifkan — tahan stick throttle secara manual."
)

# Peta mode -> pesan peringatan operator, dipakai oleh warning_for_mode().
# Sengaja LEBIH LUAS dari RISKY_MODES: STABILIZE tidak bisa diminta dari GUI
# (jadi tidak perlu gerbang konfirmasi), tapi kalau wahana ternyata berada di
# sana peringatannya tetap perlu muncul.
MODE_WARNINGS = {
    "ACRO": ACRO_WARNING,
    "STABILIZE": STABILIZE_WARNING,
}


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


def warning_for_mode(mode):
    """Pesan peringatan operator untuk `mode`, atau None kalau tidak ada."""
    return MODE_WARNINGS.get(mode)
