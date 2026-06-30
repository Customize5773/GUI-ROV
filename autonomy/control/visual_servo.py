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


@dataclass
class PoseServoOutput:
    surge: float
    sway: float
    yaw: float
    vert: float
    aligned: bool
    x: float
    y: float
    z: float


class PoseServo:
    """
    PBVS — pakai pose 3D marker dari solvePnP (meter + derajat), bukan piksel.
    Lebih presisi dari IBVS karena tahu jarak/sudut sebenarnya (butuh kalibrasi kamera).

    Konvensi camera-frame OpenCV: +x KANAN, +y BAWAH, +z KE DEPAN (menjauh dari kamera).
    Target: x→0 (lurus), y→0 (setinggi marker), z→target_dist (jarak engage), yaw→0 (tegak lurus).
    """

    def __init__(
        self,
        target_dist=0.50,       # m — jarak engage ideal ke marker
        tol_xy=0.05,            # m — toleransi lateral & vertikal
        tol_dist=0.05,          # m — toleransi jarak
        tol_yaw=8.0,            # derajat
        kp_sway=140.0, kp_surge=140.0, kp_vert=110.0, kp_yaw=0.0,
        ki=0.0, kd=0.0,
        max_speed=35.0,
        invert_sway=False, invert_vert=False, invert_yaw=False,
    ):
        self.target_dist = target_dist
        self.tol_xy, self.tol_dist, self.tol_yaw = tol_xy, tol_dist, tol_yaw
        self.s_sway = -1 if invert_sway else 1
        self.s_vert = -1 if invert_vert else 1
        self.s_yaw = -1 if invert_yaw else 1
        self.use_yaw = kp_yaw != 0.0
        self._pid_sway = PID(kp_sway, ki, kd, max_speed)
        self._pid_surge = PID(kp_surge, ki, kd, max_speed)
        self._pid_vert = PID(kp_vert, ki, kd, max_speed)
        self._pid_yaw = PID(kp_yaw, ki, kd, max_speed)
        self._hits = 0

    def reset(self):
        for p in (self._pid_sway, self._pid_surge, self._pid_vert, self._pid_yaw):
            p.reset()
        self._hits = 0

    def step(self, x, y, z, yaw_deg=0.0, dt=0.1) -> PoseServoOutput:
        ez = z - self.target_dist          # + = terlalu jauh → maju
        sway = self.s_sway * self._pid_sway.step(x, dt)     # marker kanan (x>0) → geser kanan
        surge = self._pid_surge.step(ez, dt)
        vert = self.s_vert * self._pid_vert.step(-y, dt)    # marker bawah (y>0) → turun
        yaw = self.s_yaw * self._pid_yaw.step(yaw_deg, dt) if self.use_yaw else 0.0

        in_tol = (abs(x) < self.tol_xy and abs(y) < self.tol_xy and abs(ez) < self.tol_dist
                  and (not self.use_yaw or abs(yaw_deg) < self.tol_yaw))
        self._hits = self._hits + 1 if in_tol else 0
        aligned = self._hits >= 5

        return PoseServoOutput(surge=surge, sway=sway, yaw=yaw, vert=vert,
                               aligned=aligned, x=x, y=y, z=z)
