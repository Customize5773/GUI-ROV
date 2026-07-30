"""Pemetaan MURNI perintah gripper GUI -> PWM servo.

Dipisah dari rov_agent.py supaya logika clamp/slew bisa di-unit-test tanpa
bergantung pada pymavlink, socket, atau hardware (pola sama dgn rov_axes.py
dan attitude_filter.py).

Nilai PWM harus konsisten dengan autonomy/rov_link.py (GRIPPER_SERVO_CH,
GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE) supaya jalur manual dan jalur autonomous
menggerakkan servo yang sama ke posisi yang sama.

Kontrak perintah "gripper" dari GUI:
    "open" / False        -> buka penuh  (GRIPPER_PWM_OPEN)
    "close" / True        -> tutup penuh (GRIPPER_PWM_CLOSE)
    angka -1000..1000     -> posisi proporsional (analog axis gamepad),
                             0 = netral/tengah
"""

# Channel servo (SERVOn_FUNCTION di ArduSub). Samakan dgn autonomy/rov_link.py.
GRIPPER_SERVO_CH = 10
GRIPPER_PWM_OPEN = 1900
GRIPPER_PWM_CLOSE = 1100
GRIPPER_PWM_NEUTRAL = 1500

# Batas laju gerak servo (PWM per detik) supaya gripper tidak menyentak dan
# tidak menarik arus puncak besar saat perintah berubah mendadak.
GRIPPER_MAX_SPEED_PWM_S = 500.0

# Low-pass EMA di atas hasil rate-limit. 0.0 = tanpa smoothing.
GRIPPER_EMA_ALPHA = 0.35

# Deadzone axis analog (skala -1000..1000) untuk menahan jitter stick.
GRIPPER_AXIS_DEADZONE = 150

_PWM_LO = min(GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE)
_PWM_HI = max(GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE)


def clamp_pwm(pwm):
    """Kurung PWM ke rentang aman gripper. Nilai tidak valid -> netral."""
    try:
        v = float(pwm)
    except (TypeError, ValueError):
        return float(GRIPPER_PWM_NEUTRAL)
    if v != v:  # NaN
        return float(GRIPPER_PWM_NEUTRAL)
    return max(_PWM_LO, min(_PWM_HI, v))


def gripper_value_to_pwm(value):
    """Terjemahkan nilai perintah "gripper" dari GUI menjadi target PWM.

    Menerima string ("open"/"close"), bool, atau angka -1000..1000.
    Nilai tidak dikenal -> netral (aman: gripper tidak bergerak liar).
    """
    if isinstance(value, bool):
        return float(GRIPPER_PWM_CLOSE if value else GRIPPER_PWM_OPEN)

    if isinstance(value, str):
        v = value.strip().lower()
        if v == "close":
            return float(GRIPPER_PWM_CLOSE)
        if v == "open":
            return float(GRIPPER_PWM_OPEN)
        return float(GRIPPER_PWM_NEUTRAL)

    if isinstance(value, (int, float)):
        v = float(value)
        if v != v:  # NaN
            return float(GRIPPER_PWM_NEUTRAL)
        if abs(v) < GRIPPER_AXIS_DEADZONE:
            return float(GRIPPER_PWM_NEUTRAL)
        # -1000..1000 -> OPEN..CLOSE lewat titik netral.
        # v positif = menutup, v negatif = membuka.
        v = max(-1000.0, min(1000.0, v))
        span = (GRIPPER_PWM_CLOSE - GRIPPER_PWM_NEUTRAL) / 1000.0
        return clamp_pwm(GRIPPER_PWM_NEUTRAL + v * span)

    return float(GRIPPER_PWM_NEUTRAL)


def slew_toward(current, target, dt, max_speed=GRIPPER_MAX_SPEED_PWM_S,
                ema_alpha=GRIPPER_EMA_ALPHA):
    """Gerakkan `current` menuju `target` dengan rate-limit + EMA.

    dt dalam detik. Return PWM baru (float, sudah di-clamp).
    """
    cur = clamp_pwm(current)
    tgt = clamp_pwm(target)

    try:
        dt = float(dt)
    except (TypeError, ValueError):
        dt = 0.0
    if dt != dt or dt < 0.0:
        dt = 0.0

    # EMA: perhalus tujuan sebelum dibatasi lajunya.
    smoothed = ema_alpha * cur + (1.0 - ema_alpha) * tgt

    # Rate-limit: jangan melompat lebih dari max_speed * dt per langkah.
    max_delta = max_speed * dt
    delta = smoothed - cur
    if abs(delta) > max_delta:
        delta = max_delta if delta > 0 else -max_delta

    return clamp_pwm(cur + delta)
