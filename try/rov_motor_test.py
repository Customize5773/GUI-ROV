"""Validasi & klem payload `motor_test` (dipakai rov_agent.py::run_motor_test).

Dipisah dari rov_agent.py supaya bisa diuji tanpa pymavlink/socket — sama
seperti rov_axes.py dipisah untuk axis manual control.
"""

MOTOR_TEST_MAX_THROTTLE = 20   # % — sengaja rendah untuk uji di darat/kolam
MOTOR_TEST_MAX_DURATION = 2.0  # detik — pulsa pendek


def validate_motor_test(payload):
    """Klem & validasi payload motor_test. Mengembalikan
    (motor:int, throttle:int, duration:float, direction:str, signed_throttle:int).

    Melempar ValueError kalau `motor` di luar 1..6 — nomor motor TIDAK
    diklem (beda dengan throttle/duration) karena motor yang salah harus
    ditolak, bukan diam-diam diarahkan ke motor lain.
    """
    motor = int(payload.get("motor"))
    if not 1 <= motor <= 6:
        raise ValueError(f"motor di luar jangkauan: {motor}")

    throttle = max(1, min(MOTOR_TEST_MAX_THROTTLE, int(payload.get("throttle", 15))))
    duration = max(0.2, min(MOTOR_TEST_MAX_DURATION, float(payload.get("duration", 1.0))))
    direction = payload.get("direction", "forward")
    signed_throttle = throttle if direction == "forward" else -throttle

    return motor, throttle, duration, direction, signed_throttle
