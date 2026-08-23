"""
control/visual_servo.py — Closed-loop visual servoing docking QR payload (KKI 2026)
====================================================================================
Mengubah posisi QR payload di frame kamera menjadi koreksi gerak ROV
(sway / surge / vert / yaw) agar ROV mendekat & sejajar dengan payload.

Pendekatan: Image-Based Visual Servoing (IBVS) — pakai error piksel + luas QR,
TANPA butuh kalibrasi kamera. Begitu kalibrasi (intrinsics + ukuran QR) tersedia,
bisa di-upgrade ke Pose-Based (solvePnP) tanpa mengubah antarmuka FSM.

Error yang dipakai:
  ex = (cx - W/2)/(W/2)      # -1..1, + = QR di KANAN frame
  ey = (cy - H/2)/(H/2)      # -1..1, + = QR di BAWAH frame
  ea = (target_area - area)/target_area   # + = QR terlalu kecil (terlalu jauh)

Mapping → command (-100..100):
  sway  = PID(ex)            # QR kanan → geser kanan agar ke tengah
  vert  = PID(-ey)           # QR bawah → turun agar ke tengah
  surge = PID(ea)            # terlalu jauh → maju
  yaw   = PID(ex) (opsional) # alternatif/penyelaras heading

CATATAN VERIFIKASI (hardware): arah tanda sumbu & orientasi kamera bisa berbeda —
gunakan flag invert_* dan cek di kolam (lihat VERIFIKASI_ARDUSUB.md).

ORIENTASI KAMERA (dikonfirmasi 24 Agu 2026): kamera "BOTTOM" — yang dipakai
docking QR payload — MENGHADAP DEPAN, bukan ke bawah; namanya menyesatkan.
Karena itu pemetaan sumbu di bawah BENAR apa adanya:
    x citra → sway, y citra → vert, luas/z → surge.
Kalau kamera itu menghadap bawah, y citra akan memetakan ke SURGE dan
pemetaan ini harus ditukar — pertukaran sumbu semacam itu TIDAK bisa
diperbaiki oleh flag invert_* mana pun (invert cuma membalik tanda).
"""

from dataclasses import dataclass


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class PID:
    """PID dengan clamp output, anti-windup integral, deadband error, derivative
    ter-filter, dan pembatas laju (slew) output.

    Tiga yang terakhir ada demi docking MULUS di air, bukan demi teori kontrol:
      • deadband — error visual tak pernah benar-benar 0 (riak/glare menggeser
        centroid beberapa piksel). Tanpa deadband thruster terus mematuk-matuk
        di dekat target dan ALIGNED jadi kedip-kedip.
      • d_lpf    — turunan dari error visual berisik adalah PENGUAT derau kalau
        diambil mentah; satu kutub low-pass membuat suku D benar-benar meredam.
      • slew     — command yang melompat 0→35% dalam satu tick membuat ROV ini
        miring (roll/pitch ±8-13° saat stik penuh, lihat VERIFIKASI_ARDUSUB.md).
        Tilt menggeser arah pandang kamera → error visual ikut melompat → servo
        mengejar bayangannya sendiri. Membatasi laju memutus umpan balik itu.
    """

    def __init__(self, kp, ki=0.0, kd=0.0, out_limit=100.0, i_limit=40.0,
                 deadband=0.0, d_lpf=0.4, slew=0.0):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.out_limit, self.i_limit = out_limit, i_limit
        self.deadband = deadband
        self.d_lpf = d_lpf          # 0..1 — kecil = lebih halus, lebih lambat
        self.slew = slew            # unit output/detik; 0 = tanpa batas laju
        self._i = 0.0
        self._prev = None
        self._d = 0.0
        self._out = 0.0

    def reset(self):
        self._i = 0.0
        self._prev = None
        self._d = 0.0
        self._out = 0.0

    def step(self, error, dt):
        if abs(error) < self.deadband:      # cukup dekat — diam, jangan buzz thruster
            error = 0.0
        self._i = _clamp(self._i + error * dt, -self.i_limit, self.i_limit)
        d_raw = 0.0 if (self._prev is None or dt <= 0) else (error - self._prev) / dt
        self._prev = error
        self._d += self.d_lpf * (d_raw - self._d)
        out = _clamp(self.kp * error + self.ki * self._i + self.kd * self._d,
                     -self.out_limit, self.out_limit)
        if self.slew > 0 and dt > 0:
            step_max = self.slew * dt
            out = _clamp(out, self._out - step_max, self._out + step_max)
        self._out = out
        return out


def _approach_gate(align_err, floor):
    """Skala surge selagi belum center. `align_err` dalam satuan toleransi
    (1.0 = tepat di batas toleransi lateral/vertikal).

    Mendekat SAMBIL masih melenceng lateral membuat gripper datang menyerong dan
    meleset dari badan payload — center dulu, baru maju. Tidak pernah nol penuh
    (dibatasi `floor`) supaya approach tak pernah benar-benar mandek saat error
    lateral menetap karena arus.
    """
    return 1.0 if align_err <= 1.0 else max(floor, 1.0 / align_err)


def _tally(hits, in_tol):
    """Hitung streak 'in tolerance' — turun 1 saat meleset, BUKAN reset ke 0.

    Satu frame berisik di air keruh tak boleh menghapus seluruh streak (dulu
    bikin ALIGNED tak pernah terkunci lalu docking jatuh ke fallback timed),
    tapi kedip in/out bergantian tetap tak akan pernah mencapai ambang.
    """
    return hits + 1 if in_tol else max(0, hits - 1)


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
        invert_sway=False, invert_vert=False, invert_yaw=False, invert_surge=False,
        aligned_frames=5,       # butuh N step beruntun "in-tolerance" agar aligned
        deadband=0.02,          # error ternormalisasi di bawah ini dianggap 0
        d_lpf=0.4,              # filter suku D (0..1)
        slew=120.0,             # %/detik — batas laju perubahan command
        approach_floor=0.15,    # fraksi surge minimum saat masih melenceng
    ):
        self.tol_norm, self.tol_area = tol_norm, tol_area
        self.max_speed = max_speed
        self.aligned_frames = aligned_frames
        self.approach_floor = approach_floor
        self.s_sway = -1 if invert_sway else 1
        self.s_vert = -1 if invert_vert else 1
        self.s_yaw = -1 if invert_yaw else 1
        self.s_surge = -1 if invert_surge else 1
        self.use_yaw = kp_yaw != 0.0
        self.target_area = target_area
        _opt = dict(deadband=deadband, d_lpf=d_lpf, slew=slew)
        self._pid_sway = PID(kp_sway, ki, kd, max_speed, **_opt)
        self._pid_vert = PID(kp_vert, ki, kd, max_speed, **_opt)
        self._pid_surge = PID(kp_surge, ki, kd, max_speed, **_opt)
        self._pid_yaw = PID(kp_yaw, ki, kd, max_speed, **_opt)   # IBVS: yaw dari ex, satuan sama
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
        surge = self.s_surge * self._pid_surge.step(ea, dt)
        yaw = self.s_yaw * self._pid_yaw.step(ex, dt) if self.use_yaw else 0.0

        # Center dulu, baru maju — approach menyerong bikin gripper meleset payload.
        surge *= _approach_gate(max(abs(ex), abs(ey)) / self.tol_norm, self.approach_floor)

        in_tol = abs(ex) < self.tol_norm and abs(ey) < self.tol_norm and abs(ea) < self.tol_area
        self._hits = _tally(self._hits, in_tol)
        aligned = self._hits >= self.aligned_frames

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
        invert_sway=False, invert_vert=False, invert_yaw=False, invert_surge=False,
        aligned_frames=5,       # butuh N step beruntun "in-tolerance" agar aligned
        deadband=0.01,          # m — error posisi di bawah ini dianggap 0
        deadband_yaw=1.5,       # derajat
        d_lpf=0.4,              # filter suku D (0..1)
        slew=120.0,             # %/detik — batas laju perubahan command
        approach_floor=0.15,    # fraksi surge minimum saat masih melenceng
    ):
        self.target_dist = target_dist
        self.tol_xy, self.tol_dist, self.tol_yaw = tol_xy, tol_dist, tol_yaw
        self.aligned_frames = aligned_frames
        self.approach_floor = approach_floor
        self.s_sway = -1 if invert_sway else 1
        self.s_vert = -1 if invert_vert else 1
        self.s_yaw = -1 if invert_yaw else 1
        self.s_surge = -1 if invert_surge else 1
        self.use_yaw = kp_yaw != 0.0
        _opt = dict(deadband=deadband, d_lpf=d_lpf, slew=slew)
        self._pid_sway = PID(kp_sway, ki, kd, max_speed, **_opt)
        self._pid_surge = PID(kp_surge, ki, kd, max_speed, **_opt)
        self._pid_vert = PID(kp_vert, ki, kd, max_speed, **_opt)
        self._pid_yaw = PID(kp_yaw, ki, kd, max_speed,      # PBVS: yaw dlm DERAJAT
                            deadband=deadband_yaw, d_lpf=d_lpf, slew=slew)
        self._hits = 0

    def reset(self):
        for p in (self._pid_sway, self._pid_surge, self._pid_vert, self._pid_yaw):
            p.reset()
        self._hits = 0

    def step(self, x, y, z, yaw_deg=0.0, dt=0.1) -> PoseServoOutput:
        ez = z - self.target_dist          # + = terlalu jauh → maju
        sway = self.s_sway * self._pid_sway.step(x, dt)     # marker kanan (x>0) → geser kanan
        surge = self.s_surge * self._pid_surge.step(ez, dt)
        vert = self.s_vert * self._pid_vert.step(-y, dt)    # marker bawah (y>0) → turun
        yaw = self.s_yaw * self._pid_yaw.step(yaw_deg, dt) if self.use_yaw else 0.0

        # Center dulu, baru maju — approach menyerong bikin gripper meleset payload.
        surge *= _approach_gate(max(abs(x), abs(y)) / self.tol_xy, self.approach_floor)

        in_tol = (abs(x) < self.tol_xy and abs(y) < self.tol_xy and abs(ez) < self.tol_dist
                  and (not self.use_yaw or abs(yaw_deg) < self.tol_yaw))
        self._hits = _tally(self._hits, in_tol)
        aligned = self._hits >= self.aligned_frames

        return PoseServoOutput(surge=surge, sway=sway, yaw=yaw, vert=vert,
                               aligned=aligned, x=x, y=y, z=z)
