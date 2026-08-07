import math
import threading
import time

from pymavlink import mavutil

GRIPPER_SERVO_CH = 7
ROTATE_SERVO_CH = 8

GRIPPER_PWM_OPEN = 1550
GRIPPER_PWM_NEUTRAL = 1500
GRIPPER_PWM_CLOSE = 1450

# Axis gamepad dikirim di rentang kira-kira -1000..1000 (lihat joystick-state.js).
GRIPPER_AXIS_DEADZONE = 100
GRIPPER_AXIS_MAX = 1000

# Batas kecepatan gerak servo, dalam satuan PWM microsecond per detik.
# 500 PWM/s berarti dari netral (1500) ke ujung (1100/1900) makan waktu ~0.8s.
GRIPPER_MAX_SPEED_PWM_S = 500
ROTATE_MAX_SPEED_PWM_S = 500
GRIPPER_UPDATE_HZ = 20


def clamp_pwm(value):
    """Klem nilai PWM ke rentang aman gripper. Nilai tak valid -> netral."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(GRIPPER_PWM_NEUTRAL)
    if math.isnan(value):
        return float(GRIPPER_PWM_NEUTRAL)

    lo = min(GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE)
    hi = max(GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE)
    return max(lo, min(hi, value))


def gripper_value_to_pwm(value):
    """Terjemahkan command gripper (string/bool/axis analog) ke PWM target.

    - "open"/"close" (case-insensitive, boleh ada spasi)
    - bool: True = close, False = open (sesuai konvensi rov_link.py)
    - angka: axis analog, positif = menutup, negatif = membuka, proporsional
      terhadap GRIPPER_AXIS_MAX, dengan deadzone di sekitar nol
    - nilai lain / tak dikenal -> netral
    """
    if isinstance(value, bool):
        return GRIPPER_PWM_CLOSE if value else GRIPPER_PWM_OPEN

    if isinstance(value, str):
        v = value.strip().lower()
        if v == "close":
            return GRIPPER_PWM_CLOSE
        if v == "open":
            return GRIPPER_PWM_OPEN
        return GRIPPER_PWM_NEUTRAL

    if isinstance(value, (int, float)):
        if math.isnan(value):
            return GRIPPER_PWM_NEUTRAL
        if abs(value) < GRIPPER_AXIS_DEADZONE:
            return GRIPPER_PWM_NEUTRAL
        frac = min(abs(value) / GRIPPER_AXIS_MAX, 1.0)
        if value > 0:
            return GRIPPER_PWM_NEUTRAL + frac * (GRIPPER_PWM_CLOSE - GRIPPER_PWM_NEUTRAL)
        return GRIPPER_PWM_NEUTRAL + frac * (GRIPPER_PWM_OPEN - GRIPPER_PWM_NEUTRAL)

    return GRIPPER_PWM_NEUTRAL


def slew_toward(current, target, dt, max_speed=GRIPPER_MAX_SPEED_PWM_S):
    """Gerakkan `current` menuju `target` dibatasi kecepatan `max_speed` PWM/detik.

    dt <= 0 tidak menggerakkan apa pun. Target selalu diklem ke rentang aman.
    """
    if dt <= 0:
        return float(current)

    target = clamp_pwm(target)
    max_delta = max_speed * dt
    diff = target - current
    if abs(diff) <= max_delta:
        return float(target)
    return current + max_delta * (1.0 if diff > 0 else -1.0)


class GripperController:

    GRIP_CHANNEL = GRIPPER_SERVO_CH
    ROTATE_CHANNEL = ROTATE_SERVO_CH

    PWM_FORWARD = GRIPPER_PWM_OPEN
    PWM_STOP = GRIPPER_PWM_NEUTRAL
    PWM_REVERSE = GRIPPER_PWM_CLOSE

    def __init__(
        self,
        master,
        max_speed_pwm_s=GRIPPER_MAX_SPEED_PWM_S,
        rotate_max_speed_pwm_s=ROTATE_MAX_SPEED_PWM_S,
        update_hz=GRIPPER_UPDATE_HZ,
    ):
        self.master = master
        self._max_speed = max_speed_pwm_s
        self._rotate_max_speed = rotate_max_speed_pwm_s
        self._current = float(GRIPPER_PWM_NEUTRAL)
        self._target = float(GRIPPER_PWM_NEUTRAL)
        self._rotate_current = float(GRIPPER_PWM_NEUTRAL)
        self._rotate_target = float(GRIPPER_PWM_NEUTRAL)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._interval = 1.0 / update_hz
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        last = time.time()
        while not self._stop_event.is_set():
            now = time.time()
            dt = now - last
            last = now

            with self._lock:
                target = self._target
                current = self._current
                rotate_target = self._rotate_target
                rotate_current = self._rotate_current

            new_current = slew_toward(current, target, dt, self._max_speed)
            if new_current != current:
                self._set_pwm(self.GRIP_CHANNEL, int(round(new_current)))

            new_rotate_current = slew_toward(rotate_current, rotate_target, dt, self._rotate_max_speed)
            if new_rotate_current != rotate_current:
                self._set_pwm(self.ROTATE_CHANNEL, int(round(new_rotate_current)))

            with self._lock:
                self._current = new_current
                self._rotate_current = new_rotate_current

            time.sleep(self._interval)

    def shutdown(self):
        self._stop_event.set()

    def _set_pwm(self, channel, pwm):

        if self.master is None:
            return

        print(f"[SERVO] CH{channel} -> {pwm}")

        self.master.mav.command_long_send(
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
            0,
            channel,
            pwm,
            0,
            0,
            0,
            0,
            0,
        )

    # =====================================
    # Grip (slew-rate limited)
    # =====================================

    def _set_target(self, pwm):
        with self._lock:
            self._target = clamp_pwm(pwm)

    def open(self):
        print("[GRIPPER] OPEN")
        self._set_target(GRIPPER_PWM_OPEN)

    def close(self):
        print("[GRIPPER] CLOSE")
        self._set_target(GRIPPER_PWM_CLOSE)

    def stop(self):
        print("[GRIPPER] STOP")
        self._set_target(GRIPPER_PWM_NEUTRAL)

    def set_value(self, value):
        """Terima command mentah (string/bool/axis analog) dan set target slew."""
        self._set_target(gripper_value_to_pwm(value))

    # =====================================
    # Rotate (slew-rate limited)
    # =====================================

    def _set_rotate_target(self, pwm):
        with self._lock:
            self._rotate_target = clamp_pwm(pwm)

    def rotate_left(self):
        print("[ROTATE] LEFT")
        self._set_rotate_target(self.PWM_REVERSE)

    def rotate_right(self):
        print("[ROTATE] RIGHT")
        self._set_rotate_target(self.PWM_FORWARD)

    def rotate_stop(self):
        print("[ROTATE] STOP")
        self._set_rotate_target(self.PWM_STOP)

    def set_rotate_value(self, value):
        """Terima command mentah (string/bool/axis analog) dan set target slew rotate."""
        self._set_rotate_target(gripper_value_to_pwm(value))
