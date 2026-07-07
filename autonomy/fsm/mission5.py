"""
fsm/mission5.py — State Machine Misi ROV KKI 2026
===================================================
Mengeksekusi 5 misi ROV sub-kategori KKI 2026 secara autonomous:

  Misi 1 (15%) — Scan QR code di dasar kolam
  Misi 2 (15%) — Ambil payload dengan gripper
  Misi 3 (15%) — Pindahkan payload ke gantungan dinding
  Misi 4 (15%) — Surface docking di sisi dinding payload
  Misi 5 (40%) — Lepas payload secara AUTONOMOUS ← nilai tertinggi

Cara kerja:
  - Kirim command JSON ke rov_link.py via UDP (:14550) persis seperti joystick manual
  - Terima telemetri (depth, heading, attitude) dari rov_link
  - State machine: IDLE → DIVE → SCAN_QR → GRAB → NAV_WALL → HANG → SURFACE → DOCK →
                   [Misi 5] M5_REDIVE → M5_DOCK → M5_ENGAGE → M5_UNHOOK → M5_ASCEND → DONE
                   (Misi 5 = docking closed-loop ke QR payload; M5_FALLBACK = jalur timed degraded)

Command JSON format (sama dengan server.js):
  {"surge": 0-100, "sway": 0-100, "yaw": 0-100, "vert": 0-100, "gripper": 0|1}

Nilai positif/negatif: surge+ = maju, vert+ = naik, gripper 1 = tutup, 0 = buka

Penggunaan:
  python fsm/mission5.py --server 127.0.0.1 --vision mock
  python fsm/mission5.py --server 127.0.0.1 --vision usb --device 0
"""

import json
import socket
import time
import logging
import threading
import argparse
from enum import Enum, auto
from typing import Optional

# Import vision pipeline
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vision.qr_detect import VisionPipeline
from control.visual_servo import VisualServo, PoseServo

log = logging.getLogger(__name__)

# ── Tuning parameter (sesuaikan saat uji di kolam) ───────────────────────────
DEPTH_TARGET_BOTTOM   = 0.70   # m — target depth ke dasar (0.7-0.9m pool)
DEPTH_TARGET_SURFACE  = 0.05   # m — threshold "di permukaan"
DEPTH_TOLERANCE       = 0.05   # m — toleransi depth
HOOK_DEPTH            = 0.45   # m — kedalaman hook DARI PERMUKAAN (tip 0.45m dari dasar, kolam 0.9m; Panduan hal.52)

DIVE_SPEED            = 30     # % thruster vertikal saat menyelam
ASCEND_SPEED          = 30     # % thruster vertikal saat naik
SURGE_SPEED           = 35     # % surge saat navigasi horizontal
YAW_SPEED             = 25     # % yaw saat rotasi

TIMEOUT_DIVE          = 15.0   # detik max untuk menyelam
TIMEOUT_SCAN          = 20.0   # detik max untuk scan QR
TIMEOUT_GRAB          = 10.0   # detik max untuk ambil payload
TIMEOUT_NAV           = 30.0   # detik max navigasi ke dinding
TIMEOUT_HANG          = 15.0   # detik max gantung payload
TIMEOUT_SURFACE       = 15.0   # detik max naik ke permukaan
TIMEOUT_DOCK          = 15.0   # detik max docking (misi 4 surface dock)

# Heading target tiap sisi kolam (sesuai orientasi kolam, kalibrasi di lokasi)
WALL_HEADING = {'A': 270, 'B': 90, 'C': 0, 'D': 180}

# ── Misi 5: docking closed-loop ke QR payload ("nembak x & y") ────────────────
# Target visual = QR CODE di payload (4×4 cm). PBVS bila kamera terkalibrasi, else IBVS.
QR_SIDE_M          = 0.04     # sisi fisik QR payload (m) — KKI 2026 = 4 cm (utk solvePnP)
SERVO_TARGET_AREA  = 3000.0   # IBVS: luas QR (px^2) saat jarak engage (tanpa kalibrasi)
SERVO_TARGET_DIST  = 0.30     # PBVS: jarak engage (m) — gripper mencapai payload (TUNE di kolam)
SERVO_KP_YAW       = 0.0      # >0 → ROV squaring tegak lurus dinding saat dock (aktifkan stlh verifikasi)
CALIB_FILE         = None     # path .npz kalibrasi kamera; None → IBVS (piksel)

# Arah sumbu servo — VERIFIKASI di kolam (lihat VERIFIKASI_ARDUSUB.md). Balik bila error MEMBESAR.
SERVO_INVERT = dict(invert_sway=False, invert_vert=False, invert_surge=False, invert_yaw=False)

# Gerak mekanis lepas-hook (semua TUNE + verifikasi arah di kolam)
M5_ENGAGE_SURGE    = 15       # % surge merayap seat payload ke gripper
M5_UNHOOK_VERT     = 30       # % vert angkat lubang payload lepas dari ujung hook
M5_UNHOOK_SURGE    = -20      # % surge tarik mundur agar lubang bebas dari candy-cane
UNHOOK_LIFT_T      = 3.0      # detik fase angkat
UNHOOK_PULL_T      = 2.0      # detik fase tarik mundur

TIMEOUT_REDIVE     = 15.0     # detik max selam ulang + akuisisi QR
TIMEOUT_M5_DOCK    = 25.0     # detik max dock visual sebelum degradasi ke fallback
TIMEOUT_M5_ENGAGE  = 12.0     # detik max grab payload
TIMEOUT_UNHOOK     = 10.0     # detik max lepas-hook
TIMEOUT_M5_ASCEND  = 20.0     # detik max naik ke permukaan bawa payload
TIMEOUT_FALLBACK   = 30.0     # detik max jalur timed (degraded, tanpa visual)

# Loss-of-lock: deteksi QR bisa dropout 1-2 frame karena riak air/glare. Jangan
# langsung menyapu (bisa overshoot & benar-benar kehilangan target). Beri grace
# singkat "dead-reckon hold", baru menyapu TERARAH ke sisi QR terakhir terlihat.
M5_LOCK_GRACE_T    = 0.6      # detik hold saat dropout sesaat sebelum mulai menyapu


# ── State machine states ───────────────────────────────────────────────────────
class State(Enum):
    IDLE          = auto()
    DIVE          = auto()   # Misi 1: menyelam ke dasar
    SCAN_QR       = auto()   # Misi 1: scan QR code
    GRAB          = auto()   # Misi 2: ambil payload
    NAV_WALL      = auto()   # Misi 3: navigasi ke dinding target
    HANG          = auto()   # Misi 3: gantung payload
    SURFACE       = auto()   # Misi 4: naik ke permukaan
    DOCK          = auto()   # Misi 4: docking di sisi dinding
    # ── Misi 5 (40 poin) — rantai autonomous closed-loop lepas payload ──
    M5_REDIVE     = auto()   # Misi 5a: selam ulang dari permukaan + akuisisi QR payload
    M5_DOCK       = auto()   # Misi 5b: docking closed-loop ke QR (PBVS/IBVS) — "nembak x & y"
    M5_ENGAGE     = auto()   # Misi 5c: grab payload (tetap hold x/y via pose)
    M5_UNHOOK     = auto()   # Misi 5d: angkat lubang payload lepas dari hook
    M5_ASCEND     = auto()   # Misi 5e: naik ke permukaan bawa payload
    M5_FALLBACK   = auto()   # Misi 5*: jalur timed (degraded) bila visual gagal
    DONE          = auto()
    ABORT         = auto()


# ── Telemetri dari rov_link (diterima via UDP) ────────────────────────────────
class TelemetryReceiver:
    """Dengarkan telemetri JSON dari rov_link.py di port 14551."""

    def __init__(self, host='0.0.0.0', port=14552):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self._sock.settimeout(0.5)
        self._data = {'depth': 0.0, 'heading': 0.0, 'roll': 0.0, 'pitch': 0.0}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._sock.close()

    def get(self):
        return dict(self._data)

    def _recv_loop(self):
        while self._running:
            try:
                raw, _ = self._sock.recvfrom(4096)
                pkt = json.loads(raw.decode())
                self._data.update(pkt)
            except socket.timeout:
                pass
            except Exception as e:
                log.debug("[telem] recv error: %s", e)


# ── Command sender ke rov_link ────────────────────────────────────────────────
class CommandSender:
    """Kirim command JSON ke rov_link.py via UDP port 14550."""

    def __init__(self, host='127.0.0.1', port=14550):
        self._host = host
        self._port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _emit(self, name, value):
        """Kirim SATU command {name,value} — format yang dipahami rov_link/server.js."""
        raw = json.dumps({'name': name, 'value': value}).encode()
        self._sock.sendto(raw, (self._host, self._port))
        log.debug("[cmd] %s=%s", name, value)

    def send(self, surge=0, sway=0, yaw=0, vert=0, gripper=None):
        self._emit('surge', surge)
        self._emit('sway', sway)
        self._emit('yaw', yaw)
        self._emit('vert', vert)
        if gripper is not None:
            # gripper truthy = tutup (jepit), falsy = buka
            self._emit('gripper', 'close' if gripper else 'open')

    def arm(self, on=True):
        self._emit('arm', bool(on))

    def stop_all(self):
        """Netralkan axis TAPI tetap armed (dipakai antar-state)."""
        self.send(surge=0, sway=0, yaw=0, vert=0)

    def emergency_stop(self):
        """Failsafe rov_link: netral + DISARM (hanya untuk abort)."""
        self._emit('stop', True)

    def close(self):
        self._sock.close()


# ── State Machine Utama ───────────────────────────────────────────────────────
class Mission5FSM:
    """
    State machine 5 misi ROV KKI 2026.

    Skor target:
      Misi 1 = 15 | Misi 2 = 15 | Misi 3 = 15 | Misi 4 = 15 | Misi 5 = 40
      Total  = 100 (jika semua berhasil autonomous)
    """

    def __init__(self, cmd: CommandSender, telem: TelemetryReceiver,
                 vision: VisionPipeline):
        self.cmd    = cmd
        self.telem  = telem
        self.vision = vision
        # Servo docking ke QR payload (IBVS piksel / PBVS meter). Arah sumbu = SERVO_INVERT.
        self.servo      = VisualServo(target_area=SERVO_TARGET_AREA,
                                      kp_yaw=SERVO_KP_YAW, **SERVO_INVERT)     # IBVS (piksel)
        self.pose_servo = PoseServo(target_dist=SERVO_TARGET_DIST,
                                    kp_yaw=SERVO_KP_YAW, **SERVO_INVERT)       # PBVS (meter)

        self._state   = State.IDLE
        self._state_t = time.time()   # waktu masuk state saat ini
        self._target_wall: Optional[str] = None
        self._score   = {'m1': 0, 'm2': 0, 'm3': 0, 'm4': 0, 'm5': 0}
        self._running = False
        self._require_auto = True      # bila True, abort saat mode balik ke MANUAL
        # Loss-of-lock tracker untuk docking misi 5 (M5_DOCK / M5_ENGAGE)
        self._m5_last_det_t  = 0.0     # waktu terakhir QR payload terlihat
        self._m5_search_dir  = 1       # arah sapu reacquire = sisi QR terakhir (+kanan/−kiri)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, start_state: State = State.DIVE, wait_mode: bool = True):
        """Mulai eksekusi misi dari state tertentu (default DIVE = full misi 1-5).

        Strategi lomba (direkomendasikan): 'misi 1-4 manual, hanya misi 5 autonomous'.
        Operator kemudikan 1-4 via GUI, lalu tekan toggle header → AUTONOMOUS. FSM ini
        (sudah berjalan di Pi) MENUNGGU mode=autonomous lalu menjalankan rantai misi 5.

        wait_mode : True → tunggu telemetri mode=='autonomous' sebelum eksekusi (handoff GUI).
                    False → langsung jalan (untuk uji SITL/mock tanpa GUI).
        """
        log.info("[FSM] ===== MISI ROV KKI 2026 DIMULAI (start=%s) =====", start_state.name)
        self._running = True
        self._require_auto = wait_mode
        if wait_mode and not self._wait_for_autonomous():
            log.warning("[FSM] Batal: tidak masuk mode AUTONOMOUS")
            return
        self.cmd.arm(True)          # WAJIB: arm dulu sebelum thruster merespons
        time.sleep(0.5)
        self._transition(start_state)
        self._loop()

    def _wait_for_autonomous(self, timeout: Optional[float] = None) -> bool:
        """Blok sampai operator menekan toggle GUI → mode 'autonomous' (via rov_link telem)."""
        log.info("[FSM] Menunggu mode AUTONOMOUS dari GUI (toggle header)... Ctrl+C batal")
        t0 = time.time()
        while self._running:
            if self.telem.get().get('mode') == 'autonomous':
                log.info("[FSM] Mode AUTONOMOUS terdeteksi — mulai eksekusi misi 5")
                return True
            if timeout and (time.time() - t0) > timeout:
                return False
            time.sleep(0.2)
        return False

    def abort(self):
        """Hentikan semua gerak dan masuk ABORT (failsafe + disarm)."""
        self._running = False
        self.cmd.emergency_stop()
        self._state = State.ABORT
        log.warning("[FSM] ABORT — failsafe, thruster netral + disarm")

    def score(self) -> dict:
        total = sum(self._score.values())
        return {**self._score, 'total': total}

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self):
        while self._running and self._state not in (State.DONE, State.ABORT):
            telem = self.telem.get()

            # Handoff GUI: bila operator kembalikan ke MANUAL saat autonomous → abort.
            if self._require_auto and telem.get('mode') == 'manual':
                log.warning("[FSM] Mode kembali ke MANUAL — abort autonomous")
                self.abort()
                break

            if self._state == State.DIVE:
                self._state_dive(telem)
            elif self._state == State.SCAN_QR:
                # pakai deteksi QR yang MASIH SEGAR agar tak transisi dari hasil basi
                self._state_scan_qr(telem, self.vision.latest_qr(max_age=1.0))
            elif self._state == State.GRAB:
                self._state_grab(telem)
            elif self._state == State.NAV_WALL:
                self._state_nav_wall(telem)
            elif self._state == State.HANG:
                self._state_hang(telem)
            elif self._state == State.SURFACE:
                self._state_surface(telem)
            elif self._state == State.DOCK:
                self._state_dock(telem)
            elif self._state == State.M5_REDIVE:
                self._state_m5_redive(telem)
            elif self._state == State.M5_DOCK:
                self._state_m5_dock(telem)
            elif self._state == State.M5_ENGAGE:
                self._state_m5_engage(telem)
            elif self._state == State.M5_UNHOOK:
                self._state_m5_unhook(telem)
            elif self._state == State.M5_ASCEND:
                self._state_m5_ascend(telem)
            elif self._state == State.M5_FALLBACK:
                self._state_m5_fallback(telem)

            time.sleep(0.1)

        self.cmd.stop_all()
        self._print_score()

    # ── State handlers ────────────────────────────────────────────────────────

    def _state_dive(self, telem):
        """Misi 1a: menyelam ke dasar kolam (0.7-0.9m)."""
        depth = telem.get('depth', 0.0)
        elapsed = self._elapsed()

        if elapsed > TIMEOUT_DIVE:
            log.error("[FSM] DIVE timeout!")
            self._transition(State.ABORT)
            return

        if depth >= DEPTH_TARGET_BOTTOM - DEPTH_TOLERANCE:
            log.info("[FSM] Dasar tercapai depth=%.2fm", depth)
            self.cmd.stop_all()
            self._transition(State.SCAN_QR)
        else:
            # Turun: vert negatif = tenggelam (sesuaikan sign dengan ROV kamu)
            self.cmd.send(vert=-DIVE_SPEED)
            log.debug("[FSM] DIVE depth=%.2f target=%.2f", depth, DEPTH_TARGET_BOTTOM)

    def _state_scan_qr(self, telem, vis):
        """Misi 1b: scan QR code untuk menentukan target wall."""
        elapsed = self._elapsed()

        if elapsed > TIMEOUT_SCAN:
            log.error("[FSM] SCAN_QR timeout — tidak ada QR terdeteksi!")
            self._transition(State.ABORT)
            return

        if vis and vis['type'] == 'qr' and vis['wall'] is not None:
            self._target_wall = vis['wall']
            log.info("[FSM] QR terdeteksi: data=%s → target wall=%s",
                     vis['data'], self._target_wall)
            self._score['m1'] = 15
            log.info("[FSM] ✓ Misi 1 selesai (+15 poin)")
            self.cmd.stop_all()
            self._transition(State.GRAB)
        else:
            # Rotasi perlahan untuk cari QR
            self.cmd.send(yaw=YAW_SPEED)
            log.debug("[FSM] SCAN_QR mencari QR elapsed=%.1fs", elapsed)

    def _state_grab(self, telem):
        """Misi 2: ambil payload dengan gripper."""
        elapsed = self._elapsed()

        if elapsed > TIMEOUT_GRAB:
            log.error("[FSM] GRAB timeout!")
            self._transition(State.ABORT)
            return

        # Phase 1: buka gripper (0-1s)
        if elapsed < 1.0:
            self.cmd.send(gripper=0)
            log.debug("[FSM] GRAB buka gripper")
        # Phase 2: maju sedikit ke payload (1-4s)
        elif elapsed < 4.0:
            self.cmd.send(surge=SURGE_SPEED, gripper=0)
            log.debug("[FSM] GRAB maju ke payload")
        # Phase 3: tutup gripper (4-7s)
        elif elapsed < 7.0:
            self.cmd.send(surge=0, gripper=1)
            log.debug("[FSM] GRAB tutup gripper")
        # Phase 4: konfirmasi & lanjut
        else:
            self.cmd.send(surge=0, gripper=1)
            self._score['m2'] = 15
            log.info("[FSM] ✓ Misi 2 selesai (+15 poin) — payload diambil")
            self._transition(State.NAV_WALL)

    def _state_nav_wall(self, telem):
        """Misi 3a: navigasi ke dinding target sesuai QR."""
        if self._target_wall is None:
            log.error("[FSM] NAV_WALL: target wall tidak diketahui!")
            self._transition(State.ABORT)
            return

        elapsed = self._elapsed()
        if elapsed > TIMEOUT_NAV:
            log.error("[FSM] NAV_WALL timeout!")
            self._transition(State.ABORT)
            return

        heading     = telem.get('heading', 0.0)
        target_hdg  = WALL_HEADING.get(self._target_wall, 0)
        hdg_error   = self._heading_error(heading, target_hdg)

        log.debug("[FSM] NAV_WALL hdg=%.0f target=%.0f err=%.0f wall=%s",
                  heading, target_hdg, hdg_error, self._target_wall)

        # Luruskan heading dulu
        if abs(hdg_error) > 10:
            yaw_dir = YAW_SPEED if hdg_error > 0 else -YAW_SPEED
            self.cmd.send(yaw=yaw_dir, gripper=1)
        else:
            # Heading sudah lurus → maju ke dinding
            if elapsed > 5.0:  # beri waktu 5s rotasi sebelum maju
                self.cmd.send(surge=SURGE_SPEED, gripper=1)

        # Estimasi tiba di dinding berdasarkan waktu
        # (idealnya gunakan DVL / sonar / depth kamera untuk presisi)
        if elapsed > 18.0:
            self.cmd.stop_all()
            self._transition(State.HANG)

    def _state_hang(self, telem):
        """Misi 3b: gantungkan payload ke hook di dinding."""
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_HANG:
            log.error("[FSM] HANG timeout!")
            self._transition(State.ABORT)
            return

        # Phase 1: naik sedikit agar payload sejajar hook (0-5s)
        if elapsed < 5.0:
            self.cmd.send(vert=ASCEND_SPEED, gripper=1)
            log.debug("[FSM] HANG naik ke posisi hook")
        # Phase 2: tekan ke dinding (5-8s)
        elif elapsed < 8.0:
            self.cmd.send(surge=20, vert=0, gripper=1)
            log.debug("[FSM] HANG mendekati hook")
        # Phase 3: buka gripper untuk gantung (8-11s)
        elif elapsed < 11.0:
            self.cmd.send(surge=0, gripper=0)
            log.debug("[FSM] HANG buka gripper — gantung payload")
        # Phase 4: mundur sedikit, konfirmasi
        elif elapsed < 13.0:
            self.cmd.send(surge=-20, gripper=0)
        else:
            self.cmd.stop_all()
            self._score['m3'] = 15
            log.info("[FSM] ✓ Misi 3 selesai (+15 poin) — payload tergantung di wall %s",
                     self._target_wall)
            self._transition(State.SURFACE)

    def _state_surface(self, telem):
        """Misi 4a: naik ke permukaan."""
        depth   = telem.get('depth', 0.0)
        elapsed = self._elapsed()

        if elapsed > TIMEOUT_SURFACE:
            log.error("[FSM] SURFACE timeout!")
            self._transition(State.ABORT)
            return

        if depth <= DEPTH_TARGET_SURFACE:
            log.info("[FSM] Permukaan tercapai depth=%.2fm", depth)
            self.cmd.stop_all()
            self._transition(State.DOCK)
        else:
            self.cmd.send(vert=ASCEND_SPEED)
            log.debug("[FSM] SURFACE naik depth=%.2f target=%.2f", depth, DEPTH_TARGET_SURFACE)

    def _state_dock(self, telem):
        """Misi 4b: bersandar di sisi dinding payload (surface docking)."""
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_DOCK:
            log.error("[FSM] DOCK timeout!")
            self._transition(State.ABORT)
            return

        # Maju perlahan ke dinding sambil di permukaan
        if elapsed < 8.0:
            self.cmd.send(surge=20)
            log.debug("[FSM] DOCK mendekati dinding")
        else:
            self.cmd.stop_all()
            self._score['m4'] = 15
            log.info("[FSM] ✓ Misi 4 selesai (+15 poin) — docking di sisi wall %s",
                     self._target_wall)
            self.servo.reset()
            self.pose_servo.reset()
            self._transition(State.M5_REDIVE)

    def _servo_step(self, det):
        """Satu langkah visual servo dari deteksi QR. PBVS (pose 3D) bila ada, IBVS bila tidak.
        Kembalikan (ServoOutput, 'PBVS'|'IBVS'). Dipakai M5_DOCK & M5_ENGAGE (hold x/y)."""
        pose = det.get('pose')
        if pose is not None:                       # PBVS — pose 3D (m) bila terkalibrasi
            out = self.pose_servo.step(pose['x'], pose['y'], pose['z'],
                                       pose.get('yaw_deg', 0.0), dt=0.1)
            log.debug("[FSM] servo(PBVS) x=%.2f y=%.2f z=%.2f → su=%.0f sw=%.0f vt=%.0f",
                      pose['x'], pose['y'], pose['z'], out.surge, out.sway, out.vert)
            return out, 'PBVS'
        cx, cy = det['center']                     # IBVS — fallback error piksel
        out = self.servo.step(cx, cy, det['area'], det['frame_w'], det['frame_h'], dt=0.1)
        log.debug("[FSM] servo(IBVS) ex=%.2f ey=%.2f ea=%.2f → su=%.0f sw=%.0f vt=%.0f",
                  out.ex, out.ey, out.ea, out.surge, out.sway, out.vert)
        return out, 'IBVS'

    def _state_m5_redive(self, telem):
        """Misi 5a: dari permukaan, selam ulang ke kedalaman hook sambil akuisisi QR payload."""
        depth   = telem.get('depth', 0.0)
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_REDIVE:
            log.warning("[FSM] M5_REDIVE timeout — QR tak diperoleh, degradasi ke fallback timed")
            self.cmd.stop_all()
            self._transition(State.M5_FALLBACK)
            return

        qr   = self.vision.latest_qr(max_age=0.5)
        near = depth >= HOOK_DEPTH - DEPTH_TOLERANCE

        if qr is not None and near:
            log.info("[FSM] QR payload diperoleh @depth=%.2f (%s) — mulai docking",
                     depth, qr.get('data'))
            self.cmd.stop_all()
            self.servo.reset()
            self.pose_servo.reset()
            self._transition(State.M5_DOCK)
        elif not near:
            # Turun ke level hook; sapu pelan bila QR belum terlihat
            yaw = 0 if qr is not None else int(YAW_SPEED * 0.6)
            self.cmd.send(vert=-DIVE_SPEED, yaw=yaw)
            log.debug("[FSM] M5_REDIVE selam depth=%.2f→%.2f qr=%s", depth, HOOK_DEPTH, bool(qr))
        else:
            self.cmd.send(yaw=YAW_SPEED)   # sudah di level hook tapi QR belum terlihat → sapu
            log.debug("[FSM] M5_REDIVE sapu cari QR @depth=%.2f", depth)

    def _state_m5_dock(self, telem):
        """Misi 5b: docking closed-loop ke QR payload ("nembak x & y"). PBVS bila terkalibrasi.

        Pusatkan x (sway→0) & y (vert→0), capai jarak engage z; QR hilang → sapu yaw;
        timeout → degradasi ke fallback timed."""
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_M5_DOCK:
            log.warning("[FSM] M5_DOCK timeout — degradasi ke fallback timed")
            self.cmd.stop_all()
            self._transition(State.M5_FALLBACK)
            return

        det = self.vision.latest_qr(max_age=0.5)
        if det is None:
            since = time.time() - self._m5_last_det_t
            if since < M5_LOCK_GRACE_T:
                self.cmd.stop_all()        # dropout sesaat → hold, jangan overshoot
                log.debug("[FSM] M5_DOCK dropout %.2fs — dead-reckon hold", since)
            else:
                self.cmd.send(yaw=YAW_SPEED * self._m5_search_dir)   # sapu terarah ke sisi terakhir
                log.debug("[FSM] M5_DOCK QR hilang %.1fs — sapu terarah dir=%+d",
                          since, self._m5_search_dir)
            return

        self._note_detection(det)
        out, mode = self._servo_step(det)
        self.cmd.send(surge=out.surge, sway=out.sway, yaw=out.yaw, vert=out.vert)
        if out.aligned:
            log.info("[FSM] ✓ QR payload ALIGNED (%s) — engage gripper", mode)
            self.cmd.stop_all()
            self._transition(State.M5_ENGAGE)

    def _state_m5_engage(self, telem):
        """Misi 5c: grab payload — buka gripper → merayap → tutup — sambil HOLD x/y dari pose.

        Koreksi lateral/vertikal tetap dijalankan agar payload tetap center saat merayap
        (rubrik menilai 'steady positioning attached to QR')."""
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_M5_ENGAGE:
            log.warning("[FSM] M5_ENGAGE timeout — degradasi ke fallback timed")
            self._transition(State.M5_FALLBACK)
            return

        # Hold x/y dari deteksi QR terbaru (surge dikendalikan fase, bukan servo)
        sway = vert = 0.0
        det = self.vision.latest_qr(max_age=0.5)
        if det is not None:
            self._note_detection(det)
            out, _ = self._servo_step(det)
            sway, vert = out.sway, out.vert
        # Jangan merayap MAJU secara buta bila lock hilang lebih dari grace: risiko
        # menabrak dinding/hook di luar frame. Tahan surge sampai QR ter-lock lagi.
        lost_long = (time.time() - self._m5_last_det_t) > M5_LOCK_GRACE_T

        if elapsed < 1.5:                          # buka gripper
            self.cmd.send(surge=0, sway=sway, vert=vert, gripper=0)
            log.debug("[FSM] M5_ENGAGE buka gripper")
        elif elapsed < 4.5:                        # merayap seat payload ke gripper
            creep = 0 if lost_long else M5_ENGAGE_SURGE
            self.cmd.send(surge=creep, sway=sway, vert=vert, gripper=0)
            log.debug("[FSM] M5_ENGAGE merayap ke payload (surge=%d lost=%s)", creep, lost_long)
        elif elapsed < 6.5:                        # tutup gripper
            self.cmd.send(surge=0, sway=sway, vert=vert, gripper=1)
            log.debug("[FSM] M5_ENGAGE tutup gripper — payload dicengkeram")
        else:
            self.cmd.stop_all()
            self._transition(State.M5_UNHOOK)

    def _state_m5_unhook(self, telem):
        """Misi 5d: angkat lubang payload lepas dari ujung hook, lalu tarik bebas candy-cane.
        ARAH & tinggi angkat WAJIB diverifikasi di kolam (lihat VERIFIKASI_ARDUSUB.md)."""
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_UNHOOK:
            log.warning("[FSM] M5_UNHOOK timeout — lanjut naik dengan payload")
            self._transition(State.M5_ASCEND)
            return

        if elapsed < UNHOOK_LIFT_T:                        # angkat lepas dari hook
            self.cmd.send(vert=M5_UNHOOK_VERT, gripper=1)
            log.debug("[FSM] M5_UNHOOK angkat lubang lepas hook")
        elif elapsed < UNHOOK_LIFT_T + UNHOOK_PULL_T:      # tarik mundur bebas candy-cane
            self.cmd.send(surge=M5_UNHOOK_SURGE, gripper=1)
            log.debug("[FSM] M5_UNHOOK tarik mundur")
        else:
            self.cmd.stop_all()
            log.info("[FSM] payload terlepas dari hook — naik ke permukaan")
            self._transition(State.M5_ASCEND)

    def _state_m5_ascend(self, telem):
        """Misi 5e: naik ke permukaan membawa payload (gripper tetap tutup)."""
        depth   = telem.get('depth', 0.0)
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_M5_ASCEND:
            log.warning("[FSM] M5_ASCEND timeout depth=%.2f — kredit parsial", depth)
            self.cmd.stop_all()
            self._score['m5'] = 10
            self._transition(State.DONE)
            return

        if depth <= DEPTH_TARGET_SURFACE:
            self.cmd.stop_all()
            self._score['m5'] = 40
            log.info("[FSM] ✓ Misi 5 AUTONOMOUS selesai (+40 poin) — payload di permukaan!")
            self._transition(State.DONE)
            return
        self.cmd.send(vert=ASCEND_SPEED, gripper=1)
        log.debug("[FSM] M5_ASCEND naik depth=%.2f", depth)

    def _state_m5_fallback(self, telem):
        """Misi 5*: jalur DEGRADED timed (tanpa lock visual) — jaring pengaman bila QR gagal.
        Tetap autonomous (tanpa kemudi manual), namun reliabilitas rendah."""
        elapsed = self._elapsed()
        depth   = telem.get('depth', 0.0)
        if elapsed > TIMEOUT_FALLBACK:
            log.error("[FSM] M5_FALLBACK timeout!")
            self._score['m5'] = 10   # kredit parsial
            self._transition(State.DONE)
            return

        # Fase timed: selam ke hook → grab → angkat → tarik → naik
        if elapsed < 8.0:
            self.cmd.send(vert=(-DIVE_SPEED if depth < HOOK_DEPTH else 0), gripper=0)
        elif elapsed < 11.0:
            self.cmd.send(surge=M5_ENGAGE_SURGE, gripper=0)
        elif elapsed < 14.0:
            self.cmd.send(surge=0, gripper=1)
        elif elapsed < 14.0 + UNHOOK_LIFT_T:
            self.cmd.send(vert=M5_UNHOOK_VERT, gripper=1)
        elif elapsed < 14.0 + UNHOOK_LIFT_T + UNHOOK_PULL_T:
            self.cmd.send(surge=M5_UNHOOK_SURGE, gripper=1)
        elif depth > DEPTH_TARGET_SURFACE:
            self.cmd.send(vert=ASCEND_SPEED, gripper=1)
        else:
            self.cmd.stop_all()
            self._score['m5'] = 40
            log.warning("[FSM] Misi 5 selesai via FALLBACK timed (degraded, tanpa lock visual)")
            self._transition(State.DONE)

    # ── Utility ────────────────────────────────────────────────────────────────

    def _transition(self, new_state: State):
        log.info("[FSM] %s → %s", self._state.name, new_state.name)
        self._state   = new_state
        self._state_t = time.time()
        # Mulai grace lock "segar" saat masuk fase docking (QR baru diakuisisi di REDIVE)
        if new_state in (State.M5_DOCK, State.M5_ENGAGE):
            self._m5_last_det_t = self._state_t

    def _note_detection(self, det):
        """Catat deteksi QR payload segar: perbarui timer lock + arah sapu reacquire.
        Arah sapu diambil dari sisi lateral QR terakhir (pose.x bila PBVS, else error piksel)
        agar bila lock hilang ROV menyapu MENUJU target, bukan menjauh."""
        self._m5_last_det_t = time.time()
        pose = det.get('pose')
        lat = pose['x'] if pose is not None else (det['center'][0] - det['frame_w'] / 2.0)
        if abs(lat) > 1e-6:
            self._m5_search_dir = 1 if lat > 0 else -1

    def _elapsed(self) -> float:
        return time.time() - self._state_t

    @staticmethod
    def _heading_error(current, target) -> float:
        """Hitung selisih heading −180..+180 derajat."""
        err = (target - current + 180) % 360 - 180
        return err

    def _print_score(self):
        sc = self.score()
        log.info("[FSM] ===== SKOR AKHIR =====")
        log.info("[FSM]  Misi 1 (Scan QR)     : %d/15", sc['m1'])
        log.info("[FSM]  Misi 2 (Grab Payload): %d/15", sc['m2'])
        log.info("[FSM]  Misi 3 (Hang Payload): %d/15", sc['m3'])
        log.info("[FSM]  Misi 4 (Surface Dock): %d/15", sc['m4'])
        log.info("[FSM]  Misi 5 (Auto Release): %d/40", sc['m5'])
        log.info("[FSM]  TOTAL               : %d/100", sc['total'])


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='Mission 5 FSM — KKI 2026 ROV')
    ap.add_argument('--server', default='127.0.0.1', help='IP rov_link')
    ap.add_argument('--cmd-port', type=int, default=14550, help='Port command ke rov_link')
    ap.add_argument('--telem-port', type=int, default=14552,
                    help='Port telemetri dari rov_link (14552 = fan-out FSM via '
                         'rov_link --telem-extra; 14551 dipakai server.js/GUI)')
    ap.add_argument('--vision', default='mock', choices=['mock', 'usb', 'rtsp'],
                    help='Sumber kamera')
    ap.add_argument('--device', type=int, default=0, help='Index USB webcam')
    ap.add_argument('--rtsp', default='rtsp://192.168.1.10:8554/cam',
                    help='URL RTSP jika --vision=rtsp')
    ap.add_argument('--calib', default=CALIB_FILE,
                    help='path .npz kalibrasi kamera → aktifkan PBVS (solvePnP). Tanpa ini = IBVS')
    ap.add_argument('--qr-size', type=float, default=QR_SIDE_M,
                    help='sisi QR payload fisik (m) utk solvePnP PBVS (KKI 2026 = 0.04)')
    ap.add_argument('--start-state', default='DIVE',
                    choices=['DIVE', 'M5_REDIVE', 'M5_DOCK'],
                    help='DIVE=full misi 1-5; M5_REDIVE=misi 5 autonomous (1-4 manual via GUI); '
                         'M5_DOCK=uji docking QR saja (sudah di kedalaman hook)')
    ap.add_argument('--no-wait-autonomous', action='store_true',
                    help='langsung jalan tanpa menunggu toggle GUI mode=autonomous (uji SITL/mock)')
    ap.add_argument('--loglevel', default='INFO')
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.loglevel.upper()),
        format='%(asctime)s %(levelname)-8s %(message)s',
        datefmt='%H:%M:%S',
    )

    log.info("[main] Inisialisasi komponen...")

    cmd   = CommandSender(host=args.server, port=args.cmd_port)
    telem = TelemetryReceiver(port=args.telem_port)
    cam   = VisionPipeline(source=args.vision, device=args.device,
                           rtsp_url=args.rtsp,
                           calib_file=args.calib, qr_length=args.qr_size)
    log.info("[main] Mode visi: %s", "PBVS (solvePnP)" if args.calib else "IBVS (piksel)")

    telem.start()
    cam.start()

    log.info("[main] Mulai setelah 3 detik... (Ctrl+C untuk abort)")
    time.sleep(3)

    fsm = Mission5FSM(cmd=cmd, telem=telem, vision=cam)
    try:
        fsm.start(start_state=State[args.start_state], wait_mode=not args.no_wait_autonomous)
    except KeyboardInterrupt:
        fsm.abort()
    finally:
        cam.stop()
        telem.stop()
        cmd.close()
        log.info("[main] Selesai. Skor: %s", fsm.score())


if __name__ == '__main__':
    main()
