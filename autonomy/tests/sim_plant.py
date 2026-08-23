"""
tests/sim_plant.py — Simulator ROV in-process untuk uji closed-loop misi 5
==========================================================================
Menutup loop kontrol Mission5FSM TANPA hardware, UDP, kamera, atau ArduSub:

  Mission5FSM ──cmd──► SimCommandLink ─┐
              ◄─telem─ SimTelemetry ───┤──►  SimPlant  (fisika + geometri QR)
              ◄─visi── SimVision ──────┘

Berbeda dengan `vision.qr_detect` mode 'mock' (yang hanya memancarkan deteksi QR yang
meluruh berdasarkan WAKTU), SimPlant memodelkan **umpan balik nyata**: perintah
thruster mengubah depth/heading dan geometri QR relatif, sehingga PoseServo/VisualServo
benar-benar diuji — bukan sekadar timer. Ini membuat evaluasi (`evaluate_mission5.py`)
dan pytest (`test_mission5.py`) repeatable & deterministik.

Semua adapter meniru antarmuka yang dipakai FSM:
  CommandSender   : send/arm/stop_all/emergency_stop/close
  TelemetryReceiver: get
  VisionPipeline  : latest_qr/last_result/start/stop

Waktu memakai FakeClock (virtual): tidak ada sleep nyata → mission penuh selesai
dalam milidetik. Pasang lewat `install_fake_time(mission5, clock)`.
"""

from __future__ import annotations

import json
import types
from dataclasses import dataclass
from typing import Optional

from vision.qr_detect import wall_from_qr   # butuh autonomy/ di sys.path (diatur harness)

# ── Parameter geometri kamera (samakan dgn asumsi VisionPipeline) ────────────────
FRAME_W, FRAME_H = 640, 480
FOCAL_PX         = 500.0          # panjang fokus efektif (px) untuk proyeksi mock
AREA_K           = 270.0          # area = AREA_K / z^2 → area=3000 px² saat z=0.30 m

# ── Regime docking: pose QR hanya tersedia di "band" kedalaman hook (spt kamera nyata
#    yang baru melihat payload saat mendekat). Di luar band hanya QR-data (utk SCAN). ─
HOOK_BAND_LO     = 0.25           # m — mulai terlihat saat menyelam mendekati hook
HOOK_BAND_HI     = 0.65           # m — di atas ini payload di luar FOV docking

# ── Koefisien gerak (per detik, diintegrasi dgn dt) — first-order, tanpa inersia ──
K_HEADING        = 0.40           # yaw%  → deg/s  (100% ≈ 40°/s)
K_DEPTH          = 0.010          # vert% → m/s depth (vert+ = naik → depth turun)
K_SURGE_Z        = 0.010          # surge%→ m/s pd sumbu z kamera (maju → z mengecil)
K_SWAY_X         = 0.010          # sway% → m/s pd sumbu x kamera (geser → x mengecil)
K_VERT_Y         = 0.010          # vert% → m/s pd sumbu y kamera (naik → y mengecil)

# ── Offset akuisisi awal saat ROV masuk band (payload off-center & agak jauh) ─────
INIT_RX, INIT_RY, INIT_RZ = 0.16, 0.10, 0.75      # m (kanan, bawah, jarak)
INIT_YAW_DEG              = 8.0                    # skew fiducial (kosmetik; kp_yaw=0)

# ── Hook (misi 3b HANG & misi 4 DOCK) — target geometrik di dinding (CAM WALL) ────
# Regime kedalaman terpisah dari QR: 'hang' saat menyelam di level hook, 'dock' saat di
# permukaan. Offset awal berbeda: saat HANG hook di ATAS (perlu naik → hky<0); saat DOCK
# di permukaan hook ~setinggi kamera (hky≈0, cukup surge+sway).
HOOK_HANG_LO, HOOK_HANG_HI = 0.30, 0.85           # m — band kedalaman hook terlihat (misi 3b)
HOOK_SURF_HI               = 0.15                 # m — di atas ini = regime surface docking (misi 4)
HANG_HKX0, HANG_HKY0, HANG_HKZ0 = 0.14, -0.16, 0.55   # m (kanan, atas, jarak) saat HANG
DOCK_HKX0, DOCK_HKY0, DOCK_HKZ0 = 0.12,  0.00, 0.50   # m saat surface DOCK


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class FakeClock:
    """Jam virtual: sleep TIDAK menunda nyata, hanya memajukan waktu & men-step plant."""

    def __init__(self, start=0.0, max_ticks=200000):
        self._now = float(start)
        self.on_sleep = None          # callback(dt) — dipasang ke plant.step
        self._ticks = 0
        self._max_ticks = max_ticks   # jaring pengaman: cegah hang bila FSM tak selesai

    def time(self) -> float:
        return self._now

    def sleep(self, dt: float):
        dt = max(0.0, float(dt))
        self._now += dt
        self._ticks += 1
        if self._ticks > self._max_ticks:
            raise RuntimeError(f"FakeClock melebihi {self._max_ticks} tick — FSM tak selesai?")
        if self.on_sleep and dt > 0:
            self.on_sleep(dt)


def install_fake_time(module, clock: FakeClock):
    """Ganti referensi `time` pada modul FSM dgn FakeClock. Kembalikan objek asli
    agar bisa dikembalikan (mission5 memakai `import time; time.time()/time.sleep()`)."""
    original = getattr(module, 'time')
    module.time = types.SimpleNamespace(time=clock.time, sleep=clock.sleep)
    return original


@dataclass
class PlantState:
    depth: float = 0.0            # m (positif = di bawah permukaan)
    heading: float = 90.0        # deg
    roll: float = 0.0
    pitch: float = 0.0
    gripper_closed: bool = False
    armed: bool = False
    # geometri QR payload relatif kamera (m), valid hanya saat in-band
    rx: float = INIT_RX
    ry: float = INIT_RY
    rz: float = INIT_RZ
    yaw_deg: float = INIT_YAW_DEG
    # geometri HOOK relatif kamera (m), valid hanya saat in hook-regime (HANG/DOCK)
    hkx: float = HANG_HKX0
    hky: float = HANG_HKY0
    hkz: float = HANG_HKZ0


class SimPlant:
    """Model ROV + payload sederhana untuk menutup loop misi 5.

    Perintah thruster (surge/sway/yaw/vert dalam %, gripper 0/1) diintegrasi menjadi
    depth, heading, dan pergeseran geometri QR relatif. Konvensi tanda dibuat KONSISTEN
    dengan asumsi PoseServo/VisualServo sehingga controller yang benar konvergen:
      x>0 (QR kanan)  → +sway  menurunkan x
      y>0 (QR bawah)  → vert<0 menurunkan y
      z>target        → +surge menurunkan z
    """

    def __init__(self, target_wall='C', target_dist=0.30, target_area=3000.0):
        self.s = PlantState()
        self.target_wall = target_wall
        # data QR = JSON payload terstruktur KKI 2026 (spt cetak PDF tim)
        self.qr_payload = {'mission': 5, 'team': 'HYDROSHIP',
                           'type': 'payload', 'id': target_wall}
        self.qr_data = json.dumps(self.qr_payload)
        self._in = {'surge': 0.0, 'sway': 0.0, 'yaw': 0.0, 'vert': 0.0}
        self._in_band_prev = False
        # instrumentasi
        self.hooked = True           # payload masih tergantung di hook (dilepas saat unhook)
        self.grabbed = False         # payload pernah dicengkeram gripper di regime docking
        self.aborted = False
        self.hung = False            # payload tergantung ke hook di misi 3b (gripper dibuka, hook terpusat)
        self.docked = False          # ROV bersandar ke hook saat misi 4 (surface docking)

    # ── input dari CommandSender adapter ───────────────────────────────────────
    def set_input(self, surge=0.0, sway=0.0, yaw=0.0, vert=0.0, gripper=None):
        self._in = {'surge': float(surge), 'sway': float(sway),
                    'yaw': float(yaw), 'vert': float(vert)}
        if gripper is not None:
            was_open = not self.s.gripper_closed
            self.s.gripper_closed = bool(gripper)
            # cengkeram sah bila menutup gripper saat menempel payload (in-band, terpusat)
            if self.s.gripper_closed and was_open and self._in_band():
                self.grabbed = True
            # gantung sah bila MEMBUKA gripper saat hook terpusat di regime HANG (misi 3b)
            if (not self.s.gripper_closed and not was_open
                    and self._hook_regime() == 'hang'
                    and abs(self.s.hkx) < 0.08 and abs(self.s.hky) < 0.08):
                self.hung = True

    def arm(self, on: bool):
        self.s.armed = bool(on)

    def emergency_stop(self):
        self._in = {'surge': 0.0, 'sway': 0.0, 'yaw': 0.0, 'vert': 0.0}
        self.s.armed = False
        self.aborted = True

    # ── integrasi fisika ───────────────────────────────────────────────────────
    def step(self, dt: float):
        if not self.s.armed:
            return
        i = self._in
        # heading & depth (berlaku di semua fase)
        self.s.heading = (self.s.heading + K_HEADING * i['yaw'] * dt) % 360.0
        self.s.depth = max(0.0, self.s.depth - K_DEPTH * i['vert'] * dt)
        self.s.roll = _clamp(i['sway'] * 0.15, -20, 20)
        self.s.pitch = _clamp(i['surge'] * 0.10, -15, 15)

        band = self._in_band()
        if band and not self._in_band_prev:
            # baru masuk regime docking → akuisisi ulang payload (offset segar)
            self.s.rx, self.s.ry, self.s.rz = INIT_RX, INIT_RY, INIT_RZ
            self.s.yaw_deg = INIT_YAW_DEG
        if band:
            # tanda dibuat konsisten dgn servo → controller benar akan konvergen:
            #   +sway → rx turun ; turun (vert<0) → ry turun (marker naik ke tengah) ;
            #   +surge → rz turun (mendekat).
            self.s.rx -= K_SWAY_X * i['sway'] * dt
            self.s.ry += K_VERT_Y * i['vert'] * dt
            self.s.rz = max(0.05, self.s.rz - K_SURGE_Z * i['surge'] * dt)
        self._in_band_prev = band

        # ── Geometri HOOK (misi 3b/4) — integrasi sama konvensi tanda dgn QR ──────
        #   +sway → hkx turun ; naik (vert>0) → hky naik menuju 0 (hook di atas) ;
        #   +surge → hkz turun (mendekat). Reset offset ditangani SimVision.reacquire_hook
        #   saat FSM baru mulai memakai hook (gap panggilan latest_hook — pindah fase).
        if self._hook_regime() is not None:
            self.s.hkx -= K_SWAY_X * i['sway'] * dt
            self.s.hky += K_VERT_Y * i['vert'] * dt
            self.s.hkz = max(0.05, self.s.hkz - K_SURGE_Z * i['surge'] * dt)
            if self._hook_regime() == 'dock' and self.s.hkz <= DOCK_HKZ0 * 0.7:
                self.docked = True

        # payload lepas dari hook saat gripper menutup lalu ROV mengangkat (vert>0 kuat)
        if self.grabbed and self.s.gripper_closed and i['vert'] > 15 and self.s.depth < HOOK_BAND_HI:
            self.hooked = False

    def _in_band(self) -> bool:
        return HOOK_BAND_LO <= self.s.depth <= HOOK_BAND_HI

    def _hook_regime(self):
        """Regime deteksi hook berdasar kedalaman: 'dock' (permukaan, misi 4),
        'hang' (level hook, misi 3b), atau None (tak terlihat)."""
        d = self.s.depth
        if d <= HOOK_SURF_HI:
            return 'dock'
        if HOOK_HANG_LO <= d <= HOOK_HANG_HI:
            return 'hang'
        return None

    def reacquire_hook(self):
        """Akuisisi ulang geometri hook (offset segar) sesuai regime saat ini — dipanggil
        SimVision ketika FSM baru mulai memakai hook (mensimulasikan payload masuk FOV)."""
        hr = self._hook_regime()
        if hr == 'hang':
            self.s.hkx, self.s.hky, self.s.hkz = HANG_HKX0, HANG_HKY0, HANG_HKZ0
        elif hr == 'dock':
            self.s.hkx, self.s.hky, self.s.hkz = DOCK_HKX0, DOCK_HKY0, DOCK_HKZ0

    def hook_detection(self, now: float, provide_pose: bool = True):
        """Bentuk dict deteksi HOOK (spt vision.hook_detect.detect_hook), atau None bila hook
        tak terlihat (di luar regime). `pose` diisi hanya bila provide_pose (kamera terkalibrasi)."""
        if self._hook_regime() is None:
            return None
        z = max(self.s.hkz, 1e-3)
        cx = int(FRAME_W / 2 + FOCAL_PX * (self.s.hkx / z))
        cy = int(FRAME_H / 2 + FOCAL_PX * (self.s.hky / z))
        area = AREA_K / (z * z)
        det = {
            'type': 'hook',
            'center': (cx, cy),
            'bbox': (cx - 15, cy - 40, 30, 80),
            'area': area,
            'width_px': 30.0,
            'confidence': 0.9,
            'method': 'sim',
            'frame_w': FRAME_W,
            'frame_h': FRAME_H,
            'pose': None,
            'timestamp': now,
        }
        if provide_pose:
            det['pose'] = {'x': self.s.hkx, 'y': self.s.hky, 'z': z, 'dist': z}
        return det

    # ── keluaran telemetri ─────────────────────────────────────────────────────
    def telemetry(self) -> dict:
        return {
            'depth': round(self.s.depth, 4),
            'heading': round(self.s.heading, 2),
            'roll': round(self.s.roll, 2),
            'pitch': round(self.s.pitch, 2),
            'armed': self.s.armed,
            'mode': 'autonomous',
        }

    # ── keluaran visi (deteksi QR payload) ─────────────────────────────────────
    def detection(self, now: float, provide_pose: bool = True) -> dict:
        """Bentuk dict deteksi QR seperti VisionPipeline._build_result.
        `pose` hanya diisi bila in-band DAN provide_pose (meniru kalibrasi → PBVS)."""
        z = max(self.s.rz, 1e-3)
        cx = int(FRAME_W / 2 + FOCAL_PX * (self.s.rx / z))
        cy = int(FRAME_H / 2 + FOCAL_PX * (self.s.ry / z))
        area = AREA_K / (z * z)
        det = {
            'type': 'qr',
            'data': self.qr_data,
            'payload': dict(self.qr_payload),
            'wall': wall_from_qr(self.qr_data),
            'center': (cx, cy),
            'area': area,
            'frame': None,
            'frame_w': FRAME_W,
            'frame_h': FRAME_H,
            'pose': None,
            'timestamp': now,
        }
        if provide_pose and self._in_band():
            det['pose'] = {'x': self.s.rx, 'y': self.s.ry, 'z': z,
                           'dist': z, 'yaw_deg': self.s.yaw_deg}
        return det


# ── Adapter antarmuka FSM ────────────────────────────────────────────────────────
class SimCommandLink:
    """Menggantikan CommandSender: perintah diarahkan ke SimPlant (bukan UDP)."""

    def __init__(self, plant: SimPlant):
        self.plant = plant

    def send(self, surge=0, sway=0, yaw=0, vert=0, gripper=None):
        self.plant.set_input(surge, sway, yaw, vert, gripper)

    def arm(self, on=True):
        self.plant.arm(on)

    def stop_all(self):
        self.plant.set_input(0, 0, 0, 0)

    def emergency_stop(self):
        self.plant.emergency_stop()

    def close(self):
        pass


class SimTelemetry:
    """Menggantikan TelemetryReceiver: get() membaca state SimPlant."""

    def __init__(self, plant: SimPlant):
        self.plant = plant

    def start(self):
        pass

    def stop(self):
        pass

    def get(self) -> dict:
        return self.plant.telemetry()


class SimVision:
    """Menggantikan VisionPipeline: latest_qr() = deteksi geometrik SimPlant terkini.

    provide_pose=True → meniru kamera terkalibrasi (PBVS); False → IBVS (piksel saja)."""

    def __init__(self, plant: SimPlant, clock: FakeClock, provide_pose=True,
                 dropout=None, hook_dropout=None):
        self.plant = plant
        self.clock = clock
        self.provide_pose = provide_pose
        self.dropout = dropout        # predikat(count)->bool: True = QR hilang (uji loss-of-lock)
        self.hook_dropout = hook_dropout  # predikat(count)->bool: True = hook hilang (loss-of-lock)
        self._count = 0
        self._hook_count = 0
        self._hook_last_call_t = None
        self._last = None
        self._last_hook = None
        self.wall_hint = None         # test bisa set manual utk uji SCAN_QR creep

    def start(self):
        pass

    def stop(self):
        pass

    def latest_wall_hint(self, max_age=1.0) -> Optional[dict]:
        """Default None (skenario biasa decode langsung sukses, tak pernah lewat
        jalur creep). Set `vision.wall_hint = {...}` di test utk mensimulasikannya."""
        return self.wall_hint

    def latest_qr(self, max_age=1.0) -> Optional[dict]:
        self._count += 1
        if self.dropout is not None and self.dropout(self._count):
            return None               # simulasi dropout deteksi (riak/glare)
        self._last = self.plant.detection(self.clock.time(), self.provide_pose)
        return self._last

    def latest_hook(self, max_age=1.0) -> Optional[dict]:
        now = self.clock.time()
        # Gap panggilan besar ⇒ FSM baru mulai memakai hook (pindah ke HANG/DOCK) ⇒ akuisisi
        # ulang offset segar (mensimulasikan hook baru masuk FOV). Panggilan beruntun (servo)
        # tak memicu reset → geometri berintegrasi normal.
        if self._hook_last_call_t is None or (now - self._hook_last_call_t) > 0.4:
            self.plant.reacquire_hook()
        self._hook_last_call_t = now
        self._hook_count += 1
        if self.hook_dropout is not None and self.hook_dropout(self._hook_count):
            return None               # simulasi dropout deteksi hook (riak/glare)
        self._last_hook = self.plant.hook_detection(now, self.provide_pose)
        return self._last_hook

    def last_result(self):
        return self._last
