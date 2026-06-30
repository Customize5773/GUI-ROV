"""
control/visual_servo.py — Closed-loop visual servoing untuk approach hook (KKI 2026)
====================================================================================
Mengubah posisi marker ArUco di frame kamera menjadi koreksi gerak ROV
(sway / surge / vert / yaw) agar ROV mendekat & sejajar dengan hook.

Pendekatan: Image-Based Visual Servoing (IBVS) — pakai error piksel + luas marker,
TANPA butuh kalibrasi kamera. Begitu kalibrasi (intrinsics + ukuran marker) tersedia,
bisa di-upgrade ke Pose-Based (solvePnP) tanpa mengubah antarmuka FSM.

Error yang dipakai:
  ex = (cx - W/2)/(W/2)      # -1..1, + = marker di KANAN frame
  ey = (cy - H/2)/(H/2)      # -1..1, + = marker di BAWAH frame
  ea = (target_area - area)/target_area   # + = marker terlalu kecil (terlalu jauh)

Mapping → command (-100..100):
  sway  = PID(ex)            # marker kanan → geser kanan agar ke tengah
  vert  = PID(-ey)           # marker bawah → turun agar ke tengah
  surge = PID(ea)            # terlalu jauh → maju
  yaw   = PID(ex) (opsional) # alternatif/penyelaras heading

CATATAN VERIFIKASI (hardware): arah tanda sumbu & orientasi kamera bisa berbeda —
gunakan flag invert_* dan cek di kolam (lihat VERIFIKASI_ARDUSUB.md).
"""

from dataclasses import dataclass


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class PID:
    """PID sederhana dengan clamp output & anti-windup integral."""

    def __init__(self, kp, ki=0.0, kd=0.0, out_limit=100.0, i_limit=40.0):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.out_limit, self.i_limit = out_limit, i_limit
        self._i = 0.0
        self._prev = None

    def reset(self):
        self._i = 0.0
        self._prev = None

    def step(self, error, dt):
        self._i = _clamp(self._i + error * dt, -self.i_limit, self.i_limit)
        d = 0.0 if (self._prev is None or dt <= 0) else (error - self._prev) / dt
        self._prev = error
        out = self.kp * error + self.ki * self._i + self.kd * d
        return _clamp(out, -self.out_limit, self.out_limit)


@dataclass
class ServoOutput:
    surge: float
    sway: float
    yaw: float
    vert: float
    aligned: bool
    ex: float
    ey: float
    ea: float


class VisualServo:
    """
    Hitung koreksi gerak dari posisi marker di frame.

    Contoh:
        servo = VisualServo(target_area=3000)
        out = servo.step(cx, cy, area, frame_w, frame_h, dt)
        cmd.send(surge=out.surge, sway=out.sway, vert=out.vert, yaw=out.yaw)
        if out.aligned: ...  # siap engage gripper
    """

    def __init__(
        self,
        target_area=3000.0,     # luas marker (px^2) saat jarak engage ideal
        tol_norm=0.08,          # toleransi error piksel ternormalisasi (≈8% frame)
        tol_area=0.15,          # toleransi error luas (15%)
        kp_sway=45.0, kp_surge=40.0, kp_vert=35.0, kp_yaw=0.0,
        ki=0.0, kd=0.0,
        max_speed=35.0,         # batas command (%) — pelan utk presisi
        invert_sway=False, invert_vert=False, invert_yaw=False,
        aligned_frames=5,       # butuh N step beruntun "in-tolerance" agar aligned
    ):
        self.tol_norm, self.tol_area = tol_norm, tol_area
        self.max_speed = max_speed
        self.s_sway = -1 if invert_sway else 1
        self.s_vert = -1 if invert_vert else 1
        self.s_yaw = -1 if invert_yaw else 1
        self.use_yaw = kp_yaw != 0.0
        self.target_area = target_area
        self._pid_sway = PID(kp_sway, ki, kd, max_speed)
        self._pid_vert = PID(kp_vert, ki, kd, max_speed)
        self._pid_surge = PID(kp_surge, ki, kd, max_speed)
        self._pid_yaw = PID(kp_yaw, ki, kd, max_speed)
        self._hits = 0

    def reset(self):
        for p in (self._pid_sway, self._pid_vert, self._pid_surge, self._pid_yaw):
            p.reset()
        self._hits = 0

    def step(self, cx, cy, area, frame_w, frame_h, dt=0.1) -> ServoOutput:
        ex = (cx - frame_w / 2.0) / (frame_w / 2.0)
        ey = (cy - frame_h / 2.0) / (frame_h / 2.0)
        ea = (self.target_area - area) / self.target_area

        sway = self.s_sway * self._pid_sway.step(ex, dt)
        vert = self.s_vert * self._pid_vert.step(-ey, dt)
        surge = self._pid_surge.step(ea, dt)
        yaw = self.s_yaw * self._pid_yaw.step(ex, dt) if self.use_yaw else 0.0

        in_tol = abs(ex) < self.tol_norm and abs(ey) < self.tol_norm and abs(ea) < self.tol_area
        self._hits = self._hits + 1 if in_tol else 0
        aligned = self._hits >= 5

        return ServoOutput(surge=surge, sway=sway, yaw=yaw, vert=vert,
                           aligned=aligned, ex=ex, ey=ey, ea=ea)
