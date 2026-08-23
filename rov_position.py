"""rov_position.py — Hukum kendali position-hold x/y dari optical flow —
MURNI, tanpa cv2/pymavlink/state global (pola sama dengan rov_heading.py).

Beda dari rov_heading.py (P terhadap heading ABSOLUT dari kompas): TIDAK ADA
posisi absolut yang bisa dibaca di sini — tidak ada GPS/DVL di bawah air
(lihat rov_modes.py baris 34-48). Yang ada cuma kecepatan SESAAT dari
optical flow (rov_drift.py). Jadi "target" di modul ini bukan koordinat,
melainkan AKUMULASI pergeseran (integral drift_vx/vy) sejak operator
melepas stick surge/sway — P-correction mendorong akumulasi itu balik ke
nol, meniru cara heading_bias() mendorong error heading balik ke nol.

KONSEKUENSI yang harus diketahui operator: akumulasi ini sendiri ikut
hanyut (noise/bias optical flow yang terintegrasi terus-menerus), jadi ini
position-hold JANGKA PENDEK (detik sampai puluhan detik) — bukan pengganti
DVL/GPS sungguhan. Semakin lama stick dilepas, semakin besar kemungkinan
akumulasi meleset dari posisi fisik sebenarnya.

Arah x/y di sini mengasumsikan drift_vx/vy (piksel kamera -> meter, lihat
rov_drift.py) SUDAH selaras dengan surge/sway ROV — asumsi identitas yang
BELUM divalidasi di kolam. Lihat gerbang default-OFF di apply_position_hold
(rov_agent.py).
"""

# Penguatan proporsional: unit x/y MANUAL_CONTROL per meter akumulasi error.
# 400 dipilih supaya error 0,5 m menghasilkan 200 unit (20% otoritas) —
# tebakan awal, BELUM divalidasi di kolam (lihat POSITION_LIMIT).
POSITION_P = 400.0

# Batas |koreksi| per axis. Ditahan jauh di bawah otoritas penuh (1000) —
# kalau tanda koreksi ternyata terbalik di kolam (axis kamera-vs-body salah),
# wahana mendorong pelan dan jelas terlihat, bukan langsung melawan penuh.
POSITION_LIMIT = 300.0

# Error di bawah ini dianggap nol. Tanpa deadband, noise flow beberapa
# milimeter per tick membuat thruster x/y terus mencicit tanpa pernah
# memperbaiki apa pun — pola sama dengan HEADING_DEADBAND_DEG.
POSITION_DEADBAND_M = 0.05

# |surge|/|sway| di atas ini dianggap operator sedang memegang stik.
# Sepadan dengan BRAKE_MANUAL_EPSILON di rov_agent.py.
SURGE_SWAY_MANUAL_EPSILON = 20


def position_correction(error_x, error_y, gain=POSITION_P, limit=POSITION_LIMIT,
                         deadband=POSITION_DEADBAND_M):
    """Akumulasi PERGESERAN (meter, integral drift_vx/vy — lihat docstring
    modul) -> koreksi MANUAL_CONTROL (-limit..limit) yang MELAWAN arahnya.

    PENTING soal tanda: `error` di sini adalah "seberapa jauh & ke mana
    wahana sudah bergeser" (actual - target implisit), KEBALIKAN dari
    konvensi heading_bias() di rov_heading.py (target - actual). Jadi
    koreksinya dinegasikan di sini — kalau ini dibalik, wahana akan
    MEMPERKUAT hanyut alih-alih melawannya. (Bug ini sempat lolos ke draf
    pertama, ketahuan dari simulasi sebelum sempat di-deploy.)

    Mengembalikan 0 per axis di dalam deadband, per-axis independen (x dan
    y tidak saling memengaruhi).
    """
    def _one(e):
        try:
            e = float(e)
        except (TypeError, ValueError):
            return 0
        if abs(e) < deadband:
            return 0
        out = max(-limit, min(limit, -e * gain))
        return int(round(out))

    return _one(error_x), _one(error_y)


def operator_holding_translation(axes, epsilon=SURGE_SWAY_MANUAL_EPSILON):
    """True kalau stick surge ATAU sway sedang disentuh operator (input
    manual menang mutlak atas koreksi position-hold apa pun)."""
    try:
        surge_held = abs(float(axes.get("surge", 0))) > epsilon
        sway_held = abs(float(axes.get("sway", 0))) > epsilon
    except (TypeError, ValueError):
        return False
    return surge_held or sway_held
