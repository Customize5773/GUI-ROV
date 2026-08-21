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

API internal (parameter CommandSender.send): surge/sway/yaw/vert dalam PERSEN
(-100..100), sama seperti PID/VisualServo di modul ini. Di boundary wire,
CommandSender mengalikan ×10 dan mengirim key "heave" (bukan "vert") supaya
cocok dengan konvensi rov_link.py/server.js yang pakai skala -1000..1000:
  {"surge": -1000..1000, "sway": -1000..1000, "yaw": -1000..1000,
   "heave": -1000..1000, "gripper": 0|1}

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
HOOK_DEPTH            = 0.45   # m — kedalaman hook DARI PERMUKAAN. Lihat _derive_depths():
                               #     hook_depth = kedalaman_kolam − tinggi_hook_dari_dasar.
                               #     Guidebook L46 mengukur hook 0.45 m DARI DASAR, jadi 0.45
                               #     di sini hanya benar bila kolam persis 0.9 m.

# ── Geometri kolam (opsional, via config `pool:`) ─────────────────────────────
# Diisi dari file config lokasi (pool_trial.yaml / pool_kki.yaml). Bila terisi,
# _derive_depths() menghitung HOOK_DEPTH & DEPTH_TARGET_BOTTOM dari sini — supaya
# hasil tuning di kolam latihan bisa dipindah ke arena lomba cukup dgn menukar
# angka kedalaman kolam, tanpa menghitung ulang setpoint absolut.
POOL_DEPTH             = None  # m — kedalaman air kolam (ukur di lokasi)
HOOK_HEIGHT_FROM_FLOOR = None  # m — tinggi ujung hook dari DASAR (KKI 2026 = 0.45)
BOTTOM_CLEARANCE       = None  # m — jarak aman titik-tengah ROV di atas dasar (BERPINDAH antar venue)

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
CALIB_FILE         = "vision/calibration/dwe_underwater.npz"  # jalur satu-kamera lama; None → IBVS
CALIB_FILE_BOTTOM  = "vision/calibration/bottom.npz"  # kalibrasi kamera QR/BOTTOM (mode dual-camera)
CALIB_FILE_WALL    = "vision/calibration/wall.npz"    # kalibrasi kamera hook/WALL (mode dual-camera)

# Gain PID servo docking (TUNE di kolam — pindahkan lwt --config bila sering diubah)
IBVS_KP_SWAY, IBVS_KP_SURGE, IBVS_KP_VERT = 45.0, 40.0, 35.0    # mode IBVS (piksel)
PBVS_KP_SWAY, PBVS_KP_SURGE, PBVS_KP_VERT = 140.0, 140.0, 110.0  # mode PBVS (meter)

# Validasi payload QR JSON terstruktur ({"mission":5,"type":"payload","id":"A"}) agar
# FSM tak salah pungut objek lain. QR JSON dicek mission & type; QR string biasa (legacy)
# tanpa JSON tetap diterima apa adanya.
PAYLOAD_MISSION    = 5
PAYLOAD_TYPE       = 'payload'

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

# ── Misi 3b (HANG) & Misi 4 (DOCK): docking closed-loop ke HOOK (CAM WALL) ────
# Target visual = hook PVC ¾" (25mm) ujung-U di dinding — TANPA QR/marker sendiri.
# Deteksi geometrik (vision/hook_detect.py). Servo reuse VisualServo/PoseServo (spt QR).
# Primary = closed-loop visual; timed lama disimpan sbg fallback degradasi eksplisit
# (pola sama _state_m5_dock → M5_FALLBACK).
HOOK_TARGET_AREA   = 3000.0   # IBVS: luas hook (px^2) saat jarak docking (TUNE di kolam)
HOOK_TARGET_DIST   = 0.30     # PBVS: jarak docking ke hook (m) — payload/gripper mencapai hook
HOOK_LOCK_GRACE_T  = 0.6      # detik dead-reckon hold saat deteksi hook dropout sesaat
HOOK_ACQUIRE_T     = 8.0      # detik max cari/akuisisi hook sblm degradasi ke jalur timed
HOOK_MIN_AREA      = 150.0    # luas contour minimum (px^2) kandidat hook (diteruskan ke detect_hook)
HOOK_PIPE_DIAM_M   = 0.025    # diameter pipa hook fisik (m) — ¾" PVC, utk estimasi jarak
HOOK_COLOR_HSV_RANGE = None   # [[h,s,v],[h,s,v]] opsional — JANGAN andalkan warna (warna PVC belum pasti)
DOCK_APPROACH_SPEED = 20      # % surge mendekati dinding (surface docking & seating hang)

# Fase mekanis lepas payload ke hook pasca-align visual (misi 3b) — TUNE di kolam
HANG_SEAT_T        = 1.5      # detik dorong halus dudukkan lubang payload ke ujung hook
HANG_OPEN_T        = 1.5      # detik buka gripper (payload tergantung)
HANG_BACK_T        = 2.0      # detik mundur agar lubang bebas dari hook


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
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # abort() punya dua jalur pemanggil independen yang bisa nyaris
        # bersamaan (rov_link.handle_command('control_mode') DAN self-check
        # Mission5FSM._loop() sendiri — keduanya sengaja rangkap, lihat komentar
        # di _loop). Lock ini bikin close() idempotent & _emit() setelah close
        # jadi no-op alih-alih race lempar OSError Bad file descriptor yang
        # mematikan thread loop_rx_json rov_link.
        self._lock = threading.Lock()
        self._closed = False

    def _emit(self, name, value):
        """Kirim SATU command {name,value} — format yang dipahami rov_link/server.js."""
        with self._lock:
            if self._closed:
                return
            raw = json.dumps({'name': name, 'value': value}).encode()
            self._sock.sendto(raw, (self._host, self._port))
        log.debug("[cmd] %s=%s", name, value)

    def send(self, surge=0, sway=0, yaw=0, vert=0, gripper=None):
        # Internal FSM pakai skala persen (-100..100); rov_link/server.js pakai -1000..1000.
        self._emit('surge', surge * 10)
        self._emit('sway', sway * 10)
        self._emit('yaw', yaw * 10)
        self._emit('heave', vert * 10)
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
        with self._lock:
            if self._closed:
                return
            self._closed = True
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
                 vision: VisionPipeline, runlog=None):
        self.cmd    = cmd
        self.telem  = telem
        self.vision = vision
        self.runlog = runlog        # tools.run_log.RunLogger | None (None = tak merekam)
        self._sample_t = 0.0        # timestamp sample JSONL terakhir (throttle ~2 Hz)
        # Servo docking ke QR payload (IBVS piksel / PBVS meter). Arah sumbu = SERVO_INVERT.
        self.servo      = VisualServo(target_area=SERVO_TARGET_AREA, kp_yaw=SERVO_KP_YAW,
                                      kp_sway=IBVS_KP_SWAY, kp_surge=IBVS_KP_SURGE,
                                      kp_vert=IBVS_KP_VERT, **SERVO_INVERT)      # IBVS (piksel)
        self.pose_servo = PoseServo(target_dist=SERVO_TARGET_DIST, kp_yaw=SERVO_KP_YAW,
                                    kp_sway=PBVS_KP_SWAY, kp_surge=PBVS_KP_SURGE,
                                    kp_vert=PBVS_KP_VERT, **SERVO_INVERT)        # PBVS (meter)
        # Servo docking ke HOOK (misi 3b HANG + misi 4 DOCK). Gain sama spt docking QR
        # (reuse kelas yang sama), hanya target area/jarak khusus hook.
        self.hook_servo      = VisualServo(target_area=HOOK_TARGET_AREA, kp_yaw=SERVO_KP_YAW,
                                           kp_sway=IBVS_KP_SWAY, kp_surge=IBVS_KP_SURGE,
                                           kp_vert=IBVS_KP_VERT, **SERVO_INVERT)   # IBVS (piksel)
        self.hook_pose_servo = PoseServo(target_dist=HOOK_TARGET_DIST, kp_yaw=SERVO_KP_YAW,
                                         kp_sway=PBVS_KP_SWAY, kp_surge=PBVS_KP_SURGE,
                                         kp_vert=PBVS_KP_VERT, **SERVO_INVERT)     # PBVS (meter)

        self._state   = State.IDLE
        self._state_t = time.time()   # waktu masuk state saat ini
        self._target_wall: Optional[str] = None
        self._score   = {'m1': 0, 'm2': 0, 'm3': 0, 'm4': 0, 'm5': 0}
        self._running = False
        self._require_auto = True      # bila True, abort saat mode balik ke MANUAL
        # Loss-of-lock tracker untuk docking misi 5 (M5_DOCK / M5_ENGAGE)
        self._m5_last_det_t  = 0.0     # waktu terakhir QR payload terlihat
        self._m5_search_dir  = 1       # arah sapu reacquire = sisi QR terakhir (+kanan/−kiri)
        # Loss-of-lock tracker untuk docking HOOK (HANG misi 3b / DOCK misi 4)
        self._hook_last_det_t = 0.0    # waktu terakhir hook terlihat
        self._hook_search_dir = 1      # arah sapu reacquire = sisi hook terakhir
        # Sub-fase & degradasi HANG/DOCK (None = belum aktif)
        self._hang_release_t   = None  # timestamp mulai fase lepas payload pasca-align
        self._hang_fallback_t  = None  # timestamp mulai jalur timed HANG (degradasi)
        self._dock_fallback_t  = None  # timestamp mulai jalur timed DOCK (degradasi)
        self._hang_used_fallback = False  # instrumentasi: HANG jatuh ke fallback timed?
        self._dock_used_fallback = False  # instrumentasi: DOCK jatuh ke fallback timed?

        # Telemetri live untuk GUI (dibaca rov_link.py, diteruskan sbg field "mission5").
        self.telemetry_out = {
            'state': self._state.name, 'active_cam': None,
            'distance_z': None, 'offset_x': None, 'offset_y': None,
            # Hasil decode QR terakhir dari pipeline vision Python (bukan scan
            # jsQR di browser) — dibaca readout QR di halaman Control.
            'qr_data': None, 'qr_wall': None,
        }

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
        """Blok sampai operator menekan toggle GUI → mode 'autonomous' (via rov_link telem).

        Membaca `control_mode`, BUKAN `mode`. Keduanya ada di telemetry dan
        mudah tertukar, tapi artinya berbeda (lihat rov_link.py loop_telem_tx):
            mode          = mode ArduSub dari HEARTBEAT — 'MANUAL'/'ALT_HOLD'/...
            control_mode  = gate otoritas GUI          — 'manual'/'autonomous'
        Sampai 2026-08-21 fungsi ini membandingkan `mode` dengan 'autonomous',
        nilai yang TIDAK PERNAH muncul di field itu, jadi ia menunggu selamanya.
        Tidak ketahuan karena satu-satunya jalur GUI yang nyata
        (rov_link.start_mission5) memakai wait_mode=False sehingga melewati
        fungsi ini sama sekali.
        """
        log.info("[FSM] Menunggu mode AUTONOMOUS dari GUI (toggle header)... Ctrl+C batal")
        t0 = time.time()
        while self._running:
            if self.telem.get().get('control_mode') == 'autonomous':
                log.info("[FSM] Mode AUTONOMOUS terdeteksi — mulai eksekusi misi 5")
                return True
            if timeout and (time.time() - t0) > timeout:
                return False
            time.sleep(0.2)
        return False

    def abort(self):
        """Hentikan semua gerak dan masuk ABORT (failsafe + disarm)."""
        # Kirim emergency_stop SEBELUM _running=False: begitu _running jadi False,
        # thread FSM sendiri (lihat rov_link.start_mission5 _run finally) langsung
        # cmd.close() — kalau urutannya kebalik, sendto() di atas race dengan
        # close() itu dan lempar OSError Bad file descriptor (mematikan thread
        # loop_rx_json rov_link, jadi semua command GUI berhenti masuk).
        self.cmd.emergency_stop()
        self._running = False
        self._state = State.ABORT
        log.warning("[FSM] ABORT — failsafe, thruster netral + disarm")

    def score(self) -> dict:
        total = sum(self._score.values())
        return {**self._score, 'total': total}

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self):
        while self._running and self._state not in (State.DONE, State.ABORT):
            telem = self.telem.get()
            self.telemetry_out['state'] = self._state.name

            # QR terakhir yang masih segar, independen dari state saat ini —
            # supaya operator lihat hasil scan pipeline vision di GUI kapan pun.
            qr = self.vision.latest_qr(max_age=2.0)
            self.telemetry_out['qr_data'] = qr['data'] if qr else None
            self.telemetry_out['qr_wall'] = qr['wall'] if qr else None

            # Handoff GUI: bila operator kembalikan ke MANUAL saat autonomous → abort.
            #
            # `control_mode`, bukan `mode` — lihat catatan di _wait_for_autonomous().
            # Cek ini adalah lapis KEDUA: rov_link.handle_command('control_mode')
            # sudah memanggil stop_mission5() lebih dulu. Sengaja dibiarkan
            # rangkap, supaya FSM tetap berhenti sendiri kalau suatu saat ia
            # dijalankan sebagai proses terpisah (mission5.py CLI) di mana
            # rov_link tidak memegang referensi ke thread-nya.
            if self._require_auto and telem.get('control_mode') == 'manual':
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

            self._log_sample(telem)
            time.sleep(0.1)

        self.cmd.stop_all()
        self._print_score()

    def _log_sample(self, telem):
        """Cuplik telemetri ke run log ~2 Hz (loop jalan 10 Hz — merekam tiap iterasi
        bikin file 5x lebih besar tanpa menambah info; dinamika ROV jauh lebih lambat)."""
        if not self.runlog:
            return
        now = time.time()
        if now - self._sample_t < 0.5:
            return
        self._sample_t = now
        t = self.telemetry_out
        self.runlog.event('sample',
                          state=t['state'], active_cam=t['active_cam'],
                          depth=telem.get('depth'), heading=telem.get('heading'),
                          distance_z=t['distance_z'],
                          offset_x=t['offset_x'], offset_y=t['offset_y'],
                          qr_data=t['qr_data'], qr_wall=t['qr_wall'],
                          target_wall=self._target_wall)

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

        if (vis and vis['type'] == 'qr' and vis['wall'] is not None
                and self._is_target_payload(vis)):
            self._target_wall = vis['wall']
            log.info("[FSM] QR payload terdeteksi: data=%s → target wall=%s",
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
        """Misi 3b: gantungkan payload ke hook — CLOSED-LOOP servo visual ke hook (CAM WALL).

        Primary: servo (PBVS/IBVS) mendekati & mensejajarkan payload ke hook terdeteksi,
        lalu lepas gripper (fase mekanis pendek). Dropout deteksi sesaat ditutup dead-reckon
        hold (HOOK_LOCK_GRACE_T). Fallback: jalur timed lama (degradasi eksplisit) bila hook
        tak pernah ter-lock — pola sama _state_m5_dock → M5_FALLBACK."""
        # Fase pasca-align: dudukkan lubang ke hook → buka gripper → mundur (mekanis, timed pendek)
        if self._hang_release_t is not None:
            self._hang_release(telem)
            return
        # Jalur degradasi timed penuh (hook tak pernah ter-lock)
        if self._hang_used_fallback:
            self._hang_fallback(telem)
            return

        elapsed = self._elapsed()
        if elapsed > TIMEOUT_HANG:
            log.warning("[FSM] HANG timeout — degradasi ke jalur timed (tanpa lock hook)")
            self._enter_hang_fallback()
            return

        det = self._fresh_hook(0.5)
        if det is None:
            since = time.time() - self._hook_last_det_t
            if since < HOOK_LOCK_GRACE_T:
                self.cmd.send(surge=0, sway=0, vert=0, gripper=1)  # dropout sesaat → hold
                log.debug("[FSM] HANG hook dropout %.2fs — dead-reckon hold", since)
            elif elapsed > HOOK_ACQUIRE_T:
                log.warning("[FSM] HANG hook tak terakuisisi %.1fs — degradasi timed", elapsed)
                self._enter_hang_fallback()
            else:
                # Cari hook: naik ke level hook (0.45m) sambil sapu terarah ke sisi terakhir
                depth = telem.get('depth', 0.0)
                vert = ASCEND_SPEED if depth > HOOK_DEPTH + DEPTH_TOLERANCE else 0
                self.cmd.send(vert=vert, yaw=YAW_SPEED * self._hook_search_dir, gripper=1)
                log.debug("[FSM] HANG cari hook depth=%.2f dir=%+d", depth, self._hook_search_dir)
            return

        self._note_hook(det)
        out, mode = self._hook_servo_step(det)
        self.cmd.send(surge=out.surge, sway=out.sway, yaw=out.yaw, vert=out.vert, gripper=1)
        if out.aligned:
            log.info("[FSM] ✓ Hook ALIGNED (%s) — mulai lepas payload ke hook", mode)
            self.cmd.stop_all()
            self._hang_release_t = time.time()

    def _hang_release(self, telem):
        """Fase mekanis pasca-align (misi 3b): dudukkan lubang payload ke ujung hook,
        buka gripper (gantung), lalu mundur agar lubang bebas. Timed pendek & deterministik
        — analog M5_ENGAGE yang menjalankan mekanis pasca-align M5_DOCK."""
        dt = time.time() - self._hang_release_t
        if dt < HANG_SEAT_T:
            self.cmd.send(surge=DOCK_APPROACH_SPEED, gripper=1)      # dorong halus dudukkan
            log.debug("[FSM] HANG dudukkan lubang payload ke hook")
        elif dt < HANG_SEAT_T + HANG_OPEN_T:
            self.cmd.send(surge=0, gripper=0)                        # buka gripper → gantung
            log.debug("[FSM] HANG buka gripper — payload tergantung")
        elif dt < HANG_SEAT_T + HANG_OPEN_T + HANG_BACK_T:
            self.cmd.send(surge=-20, gripper=0)                      # mundur bebas dari hook
            log.debug("[FSM] HANG mundur")
        else:
            self.cmd.stop_all()
            self._score['m3'] = 15
            log.info("[FSM] ✓ Misi 3 selesai (+15 poin) — payload tergantung di wall %s (visual)",
                     self._target_wall)
            self._transition(State.SURFACE)

    def _enter_hang_fallback(self):
        """Aktifkan jalur timed HANG (degradasi) — dipakai bila hook tak ter-lock."""
        self._hang_used_fallback = True
        self._hang_fallback_t = time.time()

    def _hang_fallback(self, telem):
        """Jalur DEGRADED timed misi 3 (tanpa lock hook) — perilaku lama sbg jaring pengaman.
        Tetap autonomous, reliabilitas rendah (buta terhadap posisi hook sebenarnya)."""
        dt = time.time() - self._hang_fallback_t
        if dt > TIMEOUT_HANG:
            log.error("[FSM] HANG fallback timeout!")
            self._transition(State.ABORT)
            return
        if dt < 5.0:                                # naik ke posisi hook
            self.cmd.send(vert=ASCEND_SPEED, gripper=1)
        elif dt < 8.0:                              # tekan ke dinding
            self.cmd.send(surge=DOCK_APPROACH_SPEED, vert=0, gripper=1)
        elif dt < 11.0:                             # buka gripper — gantung
            self.cmd.send(surge=0, gripper=0)
        elif dt < 13.0:                             # mundur konfirmasi
            self.cmd.send(surge=-20, gripper=0)
        else:
            self.cmd.stop_all()
            self._score['m3'] = 15
            log.warning("[FSM] Misi 3 selesai via FALLBACK timed (degraded, tanpa lock hook)")
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
        """Misi 4b: surface docking — CLOSED-LOOP servo visual ke hook sisi target (CAM WALL).

        Primary: servo mendekati hook sampai jarak/pose docking wajar (aligned), baru berhenti.
        Dropout deteksi ditutup dead-reckon hold. Fallback: jalur timed lama (degradasi eksplisit)
        bila hook tak pernah ter-lock — pola sama _state_m5_dock → M5_FALLBACK."""
        if self._dock_used_fallback:
            self._dock_fallback(telem)
            return

        elapsed = self._elapsed()
        if elapsed > TIMEOUT_DOCK:
            log.warning("[FSM] DOCK timeout — degradasi ke jalur timed (tanpa lock hook)")
            self._enter_dock_fallback()
            return

        det = self._fresh_hook(0.5)
        if det is None:
            since = time.time() - self._hook_last_det_t
            if since < HOOK_LOCK_GRACE_T:
                self.cmd.stop_all()                 # dropout sesaat → hold, jangan overshoot
                log.debug("[FSM] DOCK hook dropout %.2fs — dead-reckon hold", since)
            elif elapsed > HOOK_ACQUIRE_T:
                log.warning("[FSM] DOCK hook tak terakuisisi %.1fs — degradasi timed", elapsed)
                self._enter_dock_fallback()
            else:
                # Cari hook: merayap pelan ke dinding sambil sapu terarah ke sisi terakhir
                self.cmd.send(surge=int(DOCK_APPROACH_SPEED * 0.5),
                              yaw=YAW_SPEED * self._hook_search_dir)
                log.debug("[FSM] DOCK cari hook dir=%+d", self._hook_search_dir)
            return

        self._note_hook(det)
        out, mode = self._hook_servo_step(det)
        self.cmd.send(surge=out.surge, sway=out.sway, yaw=out.yaw, vert=out.vert)
        if out.aligned:
            self.cmd.stop_all()
            self._score['m4'] = 15
            log.info("[FSM] ✓ Misi 4 selesai (+15 poin) — surface docking wall %s (visual)",
                     self._target_wall)
            self.servo.reset()
            self.pose_servo.reset()
            self._transition(State.M5_REDIVE)

    def _enter_dock_fallback(self):
        """Aktifkan jalur timed DOCK (degradasi) — dipakai bila hook tak ter-lock."""
        self._dock_used_fallback = True
        self._dock_fallback_t = time.time()

    def _dock_fallback(self, telem):
        """Jalur DEGRADED timed misi 4 (tanpa lock hook) — perilaku lama (maju buta ke dinding)."""
        dt = time.time() - self._dock_fallback_t
        if dt > TIMEOUT_DOCK:
            log.error("[FSM] DOCK fallback timeout!")
            self._transition(State.ABORT)
            return
        if dt < 8.0:
            self.cmd.send(surge=DOCK_APPROACH_SPEED)
            log.debug("[FSM] DOCK fallback mendekati dinding")
        else:
            self.cmd.stop_all()
            self._score['m4'] = 15
            log.warning("[FSM] Misi 4 selesai via FALLBACK timed (degraded, tanpa lock hook)")
            self.servo.reset()
            self.pose_servo.reset()
            self._transition(State.M5_REDIVE)

    def _servo_step(self, det):
        """Satu langkah visual servo dari deteksi QR. PBVS (pose 3D) bila ada, IBVS bila tidak.
        Kembalikan (ServoOutput, 'PBVS'|'IBVS'). Dipakai M5_DOCK & M5_ENGAGE (hold x/y)."""
        pose = det.get('pose')
        self.telemetry_out['active_cam'] = 'BOTTOM'
        if pose is not None:                       # PBVS — pose 3D (m) bila terkalibrasi
            out = self.pose_servo.step(pose['x'], pose['y'], pose['z'],
                                       pose.get('yaw_deg', 0.0), dt=0.1)
            log.debug("[FSM] servo(PBVS) x=%.2f y=%.2f z=%.2f → su=%.0f sw=%.0f vt=%.0f",
                      pose['x'], pose['y'], pose['z'], out.surge, out.sway, out.vert)
            self.telemetry_out.update(distance_z=out.z, offset_x=out.x, offset_y=out.y)
            return out, 'PBVS'
        cx, cy = det['center']                     # IBVS — fallback error piksel
        out = self.servo.step(cx, cy, det['area'], det['frame_w'], det['frame_h'], dt=0.1)
        log.debug("[FSM] servo(IBVS) ex=%.2f ey=%.2f ea=%.2f → su=%.0f sw=%.0f vt=%.0f",
                  out.ex, out.ey, out.ea, out.surge, out.sway, out.vert)
        self.telemetry_out.update(distance_z=None, offset_x=out.ex, offset_y=out.ey)
        return out, 'IBVS'

    def _hook_servo_step(self, det):
        """Satu langkah visual servo dari deteksi HOOK. PBVS (pose 3D) bila ada, IBVS bila tidak.
        Kembalikan (ServoOutput, 'PBVS'|'IBVS'). Reuse VisualServo/PoseServo — hanya instans &
        target khusus hook (lihat _servo_step untuk versi QR)."""
        pose = det.get('pose')
        self.telemetry_out['active_cam'] = 'WALL'
        if pose is not None:                       # PBVS — pose 3D (m) bila kamera terkalibrasi
            out = self.hook_pose_servo.step(pose['x'], pose['y'], pose['z'],
                                            pose.get('yaw_deg', 0.0), dt=0.1)
            log.debug("[FSM] hook_servo(PBVS) x=%.2f y=%.2f z=%.2f → su=%.0f sw=%.0f vt=%.0f",
                      pose['x'], pose['y'], pose['z'], out.surge, out.sway, out.vert)
            self.telemetry_out.update(distance_z=out.z, offset_x=out.x, offset_y=out.y)
            return out, 'PBVS'
        cx, cy = det['center']                     # IBVS — fallback error piksel
        out = self.hook_servo.step(cx, cy, det['area'], det['frame_w'], det['frame_h'], dt=0.1)
        log.debug("[FSM] hook_servo(IBVS) ex=%.2f ey=%.2f ea=%.2f → su=%.0f sw=%.0f vt=%.0f",
                  out.ex, out.ey, out.ea, out.surge, out.sway, out.vert)
        self.telemetry_out.update(distance_z=None, offset_x=out.ex, offset_y=out.ey)
        return out, 'IBVS'

    def _fresh_hook(self, max_age=0.5):
        """Deteksi hook terbaru yang masih segar (else None). Tak butuh validasi payload
        seperti QR — hook adalah target geometrik tunggal di dinding."""
        return self.vision.latest_hook(max_age=max_age)

    def _note_hook(self, det):
        """Catat deteksi hook segar: perbarui timer lock + arah sapu reacquire (sisi lateral
        hook terakhir), agar bila lock hilang ROV menyapu MENUJU hook, bukan menjauh."""
        self._hook_last_det_t = time.time()
        pose = det.get('pose')
        lat = pose['x'] if pose is not None else (det['center'][0] - det['frame_w'] / 2.0)
        if abs(lat) > 1e-6:
            self._hook_search_dir = 1 if lat > 0 else -1

    def _state_m5_redive(self, telem):
        """Misi 5a: dari permukaan, selam ulang ke kedalaman hook sambil akuisisi QR payload."""
        depth   = telem.get('depth', 0.0)
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_REDIVE:
            log.warning("[FSM] M5_REDIVE timeout — QR tak diperoleh, degradasi ke fallback timed")
            self.cmd.stop_all()
            self._transition(State.M5_FALLBACK)
            return

        qr   = self._fresh_payload(0.5)
        near = depth >= HOOK_DEPTH - DEPTH_TOLERANCE

        if qr is not None and near:
            log.info("[FSM] QR payload diperoleh @depth=%.2f (%s) — mulai docking",
                     depth, qr.get('data'))
            self.cmd.stop_all()
            self.servo.reset()
            self.pose_servo.reset()
            self._transition(State.M5_DOCK)
        elif not near:
            # Turun ke level hook; sambil arahkan heading balik ke dinding target (WALL_HEADING),
            # bukan sapu buta — dinding sudah diketahui dari QR misi 1, tak perlu "ingat" posisi.
            yaw = 0 if qr is not None else self._heading_toward_wall(telem)
            self.cmd.send(vert=-DIVE_SPEED, yaw=yaw)
            log.debug("[FSM] M5_REDIVE selam depth=%.2f→%.2f qr=%s", depth, HOOK_DEPTH, bool(qr))
        else:
            yaw = YAW_SPEED if self._target_wall is None else self._heading_toward_wall(telem)
            self.cmd.send(yaw=yaw)   # sudah di level hook, QR belum terlihat → arah dinding target
            log.debug("[FSM] M5_REDIVE sapu cari QR @depth=%.2f", depth)

    def _heading_toward_wall(self, telem) -> int:
        """Yaw menuju heading dinding target (sama dgn NAV_WALL) — dipakai M5_REDIVE
        agar sapu cari QR terarah, bukan sapu buta satu arah."""
        if self._target_wall is None:
            return int(YAW_SPEED * 0.6)
        heading    = telem.get('heading', 0.0)
        target_hdg = WALL_HEADING.get(self._target_wall, heading)
        err = self._heading_error(heading, target_hdg)
        if abs(err) < 10:
            return 0
        return YAW_SPEED if err > 0 else -YAW_SPEED

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

        det = self._fresh_payload(0.5)
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
        det = self._fresh_payload(0.5)
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
        if self.runlog:
            self.runlog.event('transition', frm=self._state.name, to=new_state.name,
                              lama_state_s=round(time.time() - self._state_t, 2))
        self._state   = new_state
        self._state_t = time.time()
        # Mulai grace lock "segar" saat masuk fase docking (QR baru diakuisisi di REDIVE)
        if new_state in (State.M5_DOCK, State.M5_ENGAGE):
            self._m5_last_det_t = self._state_t
        # Reset tracker & servo hook saat masuk HANG (misi 3b) / DOCK (misi 4)
        if new_state == State.HANG:
            self._hook_last_det_t = self._state_t
            self._hang_release_t = None
            self._hang_fallback_t = None
            self._hang_used_fallback = False
            self.hook_servo.reset()
            self.hook_pose_servo.reset()
        elif new_state == State.DOCK:
            self._hook_last_det_t = self._state_t
            self._dock_fallback_t = None
            self._dock_used_fallback = False
            self.hook_servo.reset()
            self.hook_pose_servo.reset()

    def _is_target_payload(self, det) -> bool:
        """True bila deteksi QR adalah payload misi ini. QR JSON terstruktur divalidasi
        (mission & type); QR string biasa (tanpa JSON) diterima apa adanya (legacy)."""
        payload = det.get('payload')
        if payload is None:
            return True
        m = payload.get('mission')
        if m is not None and str(m) != str(PAYLOAD_MISSION):
            return False
        ptype = payload.get('type')
        if ptype is not None and str(ptype).lower() != PAYLOAD_TYPE:
            return False
        return True

    def _fresh_payload(self, max_age=0.5):
        """latest_qr yang TERVALIDASI sebagai payload target (else None) — dipakai
        akuisisi & servo misi 5 agar tak mengunci QR/objek yang salah."""
        det = self.vision.latest_qr(max_age=max_age)
        if det is not None and not self._is_target_payload(det):
            return None
        return det

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


# ── Turunan geometri kolam ────────────────────────────────────────────────────
def _derive_depths(explicit: set):
    """Hitung setpoint kedalaman dari geometri kolam (`pool:` di config).

    Setpoint absolut TIDAK berpindah antar venue — kolam latihan 0.8 m dan arena
    KKI 0.7–0.9 m memberi angka berbeda. Yang berpindah adalah geometrinya:
    tinggi hook dari dasar (0.45 m per Guidebook) dan clearance aman hasil trial.
    Jadi keduanya diturunkan, bukan disalin:

        HOOK_DEPTH          = POOL_DEPTH − HOOK_HEIGHT_FROM_FLOOR
        DEPTH_TARGET_BOTTOM = POOL_DEPTH − BOTTOM_CLEARANCE

    `explicit` = nama konstanta yang sudah diset langsung oleh file config; nilai
    eksplisit selalu menang (jalan keluar bila kedalaman diukur langsung di lokasi).
    """
    g = globals()
    if POOL_DEPTH is None:
        return
    for attr, part, label in (('HOOK_DEPTH', HOOK_HEIGHT_FROM_FLOOR, 'hook_height_from_floor'),
                              ('DEPTH_TARGET_BOTTOM', BOTTOM_CLEARANCE, 'bottom_clearance')):
        if part is None:
            continue
        if attr in explicit:
            log.info("[main] %s diset eksplisit di config — turunan pool.%s diabaikan",
                     attr, label)
            continue
        val = round(POOL_DEPTH - part, 4)
        if val <= 0:
            raise ValueError(
                f"pool.{label} ({part} m) >= pool.depth ({POOL_DEPTH} m) → {attr}={val}. "
                "Periksa ukuran kolam di config.")
        log.info("[main] %s = %.3f m (turunan: pool.depth %.3f − pool.%s %.3f)",
                 attr, val, POOL_DEPTH, label, part)
        g[attr] = val


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    # Logging sementara (agar log pra-parse config di bawah ini tampil) — direkonfigurasi
    # ulang sesuai --loglevel setelah argparse penuh selesai (force=True).
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%H:%M:%S')

    # Pra-parse --config LEBIH DULU: bila diberikan, override konstanta tuning
    # (globals() modul ini) SEBELUM argparse penuh dibangun, supaya default flag
    # lain (mis. --calib, --qr-size) memakai nilai config yang sudah dioverride.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--config', action='append', default=None, metavar='FILE',
                     help='path config tuning .yaml/.yml/.json — pindahkan gain PID, '
                          'target docking, depth, timing, WALL_HEADING, invert_* KELUAR '
                          'dari kode (lihat config/mission5.example.yaml). BOLEH DIULANG: '
                          'file belakangan menang, mis. --config config/rov_tuned.yaml '
                          '--config config/pool_trial.yaml (tuning ROV + geometri lokasi)')
    pre_args, _ = pre.parse_known_args()
    cfg_files = pre_args.config or []
    cfg_keys = set()          # nama konstanta yg dioverride EKSPLISIT oleh config
    for path in cfg_files:
        from config.loader import load_config, apply_config
        applied = apply_config(globals(), load_config(path))
        cfg_keys.update(name for name, _old, _new in applied)
        log.info("[main] Config tuning dimuat: %s (%d nilai dioverride)", path, len(applied))
    _derive_depths(cfg_keys)

    ap = argparse.ArgumentParser(description='Mission 5 FSM — KKI 2026 ROV', parents=[pre])
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
                    help='path .npz kalibrasi kamera (jalur satu-kamera lama) → aktifkan PBVS. Tanpa ini = IBVS')
    ap.add_argument('--qr-size', type=float, default=QR_SIDE_M,
                    help='sisi QR payload fisik (m) utk solvePnP PBVS (KKI 2026 = 0.04)')
    ap.add_argument('--bottom-url', default=None,
                    help='URL stream kamera BOTTOM (QR docking). Isi bersama --wall-url utk mode dual-camera.')
    ap.add_argument('--wall-url', default=None,
                    help='URL stream kamera WALL (hook). Isi bersama --bottom-url utk mode dual-camera.')
    ap.add_argument('--calib-bottom', default=CALIB_FILE_BOTTOM,
                    help='kalibrasi .npz kamera BOTTOM (mode dual-camera)')
    ap.add_argument('--calib-wall', default=CALIB_FILE_WALL,
                    help='kalibrasi .npz kamera WALL (mode dual-camera)')
    ap.add_argument('--no-wall-cnn', action='store_true',
                    help='matikan fallback wall-CNN saat decode_qr() gagal (default: AKTIF)')
    ap.add_argument('--wall-cnn-votes', type=int, default=3,
                    help='jumlah frame berturut-turut sepakat sebelum wall-CNN dipercaya')
    ap.add_argument('--wall-cnn-min-conf', type=float, default=0.8,
                    help='confidence minimum agar tebakan wall-CNN dihitung')
    ap.add_argument('--start-state', default='DIVE',
                    choices=['DIVE', 'M5_REDIVE', 'M5_DOCK'],
                    help='DIVE=full misi 1-5; M5_REDIVE=misi 5 autonomous (1-4 manual via GUI); '
                         'M5_DOCK=uji docking QR saja (sudah di kedalaman hook)')
    ap.add_argument('--no-wait-autonomous', action='store_true',
                    help='langsung jalan tanpa menunggu toggle GUI mode=autonomous (uji SITL/mock)')
    ap.add_argument('--loglevel', default='INFO')
    ap.add_argument('--log-file', default=None,
                    help='arsipkan log TEKS run ke file')
    ap.add_argument('--run-log', nargs='?', const='auto', default=None, metavar='FILE.jsonl',
                    help='rekam run terstruktur ke JSONL utk analisis pasca-trial '
                         '(tools/analyze_run.py + panel "Run terakhir" di GUI). '
                         'Tanpa nilai → logs/run_YYYYmmdd_HHMMSS.jsonl')
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.loglevel.upper()),
        format='%(asctime)s %(levelname)-8s %(message)s',
        datefmt='%H:%M:%S',
        force=True,   # timpa basicConfig sementara di atas (dipakai saat load --config)
    )
    if args.log_file:
        fh = logging.FileHandler(args.log_file)
        fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)-8s %(message)s', datefmt='%H:%M:%S'))
        logging.getLogger().addHandler(fh)

    runlog = None
    if args.run_log:
        from tools.run_log import RunLogger
        path = args.run_log
        if path == 'auto':
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'logs', time.strftime('run_%Y%m%d_%H%M%S.jsonl'))
        runlog = RunLogger(path)
        # Kaitkan run ini ke nilai tuning yang menghasilkannya — tanpa ini tabel
        # agregat antar-trial tak bisa dipakai mengambil keputusan.
        runlog.event('config', files=cfg_files, start_state=args.start_state,
                     nilai={k: globals().get(k) for k in sorted(
                         cfg_keys | {'HOOK_DEPTH', 'DEPTH_TARGET_BOTTOM'})})
        log.info("[main] Run log: %s", path)

    log.info("[main] Inisialisasi komponen...")

    cmd   = CommandSender(host=args.server, port=args.cmd_port)
    telem = TelemetryReceiver(port=args.telem_port)
    cam   = VisionPipeline(source=args.vision, device=args.device,
                           rtsp_url=args.rtsp,
                           calib_file=args.calib, qr_length=args.qr_size,
                           hook_hsv_range=HOOK_COLOR_HSV_RANGE,
                           hook_min_area=HOOK_MIN_AREA, hook_pipe_diam=HOOK_PIPE_DIAM_M,
                           qr_url=args.bottom_url, hook_url=args.wall_url,
                           calib_file_qr=args.calib_bottom, calib_file_hook=args.calib_wall,
                           wall_cnn=None if args.no_wall_cnn else True,
                           wall_cnn_votes=args.wall_cnn_votes,
                           wall_cnn_min_conf=args.wall_cnn_min_conf)
    log.info("[main] Mode visi: %s", "PBVS (solvePnP)" if args.calib else "IBVS (piksel)")

    telem.start()
    cam.start()

    log.info("[main] Mulai setelah 3 detik... (Ctrl+C untuk abort)")
    time.sleep(3)

    fsm = Mission5FSM(cmd=cmd, telem=telem, vision=cam, runlog=runlog)
    alasan = 'selesai'
    try:
        fsm.start(start_state=State[args.start_state], wait_mode=not args.no_wait_autonomous)
    except KeyboardInterrupt:
        alasan = 'ctrl-c'
        fsm.abort()
    finally:
        cam.stop()
        telem.stop()
        cmd.close()
        if runlog:
            runlog.close(alasan=alasan, state_akhir=fsm._state.name, skor=fsm.score(),
                         target_wall=fsm._target_wall,
                         hang_used_fallback=fsm._hang_used_fallback,
                         dock_used_fallback=fsm._dock_used_fallback)
        log.info("[main] Selesai. Skor: %s", fsm.score())


if __name__ == '__main__':
    main()
