"""Hukum kendali heading-hold (POSHOLD) — MURNI, tanpa pymavlink/socket/thread.

Dipisah dari rov_agent.py dengan alasan yang sama seperti rov_axes.py dan
rov_modes.py: penanganan wrap sudut dan clamp output adalah tempat bug paling
mudah bersembunyi (target 350°, heading 10° -> error harus -20°, bukan +340°),
dan itu bisa diuji tanpa hardware sama sekali.

Kenapa P saja, bukan PID
    - Tanpa suku I tidak ada windup saat wahana ditahan arus dan koreksi yaw
      tidak sanggup melawannya. Yang terjadi hanya offset tetap, bukan output
      yang menumpuk lalu meledak begitu wahana lepas.
    - Tanpa suku D tidak ada penguatan derau yaw. Heading datang dari
      AttitudeFilter yang sudah di-EMA; menurunkannya lagi hanya memperkuat
      sisa jitter menjadi getaran thruster.
    Kalau setelah trial ternyata terlalu lambat, naikkan HEADING_P dulu sebelum
    memikirkan suku lain.

Kelas PID di autonomy/control/visual_servo.py sengaja TIDAK dipakai di sini:
paket autonomy/ tidak diimpor rov_agent.py sama sekali, dan menariknya masuk
berarti menyeret dependensi vision (cv2, numpy) ke jalur kontrol utama yang
harus tetap bisa jalan di Pi meski kamera bermasalah.
"""

# Penguatan proporsional: unit r MANUAL_CONTROL per derajat error.
# 6.0 dipilih supaya error 30° menghasilkan 180 unit — terasa tegas tapi masih
# jauh di bawah setengah otoritas yaw penuh (1000), sehingga operator tetap
# bisa mengalahkannya kalau perlu.
HEADING_P = 6.0

# Batas |koreksi| terhadap r. Ditahan di 250 (25% otoritas yaw) supaya mode ini
# tidak pernah bisa memutar wahana secepat perintah operator: kalau tanda
# koreksi ternyata terbalik di kolam, wahana berputar pelan dan jelas terlihat,
# bukan langsung berputar liar.
HEADING_LIMIT = 250.0

# Error di bawah ini dianggap nol. Tanpa deadband, derau yaw beberapa persepuluh
# derajat membuat thruster yaw terus mencicit bolak-balik (limit cycle) tanpa
# pernah memperbaiki apa pun.
HEADING_DEADBAND_DEG = 2.0

# |yaw| stik di atas ini dianggap operator sedang memegang stik. Sepadan dengan
# HEAVE_MANUAL_EPSILON di rov_agent.py supaya kedua overlay (depth & heading)
# menyerah pada operator di ambang yang sama.
YAW_MANUAL_EPSILON = 20


def heading_error(target, actual):
    """Error heading ter-wrap ke rentang (-180, 180] derajat.

    Positif = wahana perlu berputar SEARAH JARUM JAM (heading bertambah) untuk
    mencapai target. Mengembalikan 0.0 kalau salah satu argumen bukan angka —
    heading yang belum pernah terisi tidak boleh menghasilkan koreksi.
    """
    try:
        err = float(target) - float(actual)
    except (TypeError, ValueError):
        return 0.0
    return (err + 180.0) % 360.0 - 180.0


def heading_bias(target, actual, gain=HEADING_P, limit=HEADING_LIMIT,
                 deadband=HEADING_DEADBAND_DEG):
    """Koreksi yang harus DITAMBAHKAN ke MANUAL_CONTROL.r untuk menahan heading.

    Mengembalikan int dalam rentang ±limit; 0 di dalam deadband.
    """
    err = heading_error(target, actual)
    if abs(err) < deadband:
        return 0
    out = max(-limit, min(limit, err * gain))
    return int(round(out))


def operator_holding_yaw(axes, epsilon=YAW_MANUAL_EPSILON):
    """True kalau stik yaw sedang disentuh operator (input manual menang)."""
    try:
        return abs(float(axes.get("yaw", 0))) > epsilon
    except (TypeError, ValueError):
        return False
