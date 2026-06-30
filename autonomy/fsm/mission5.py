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
  - State machine: IDLE → DIVE → SCAN_QR → GRAB → NAV_WALL → HANG →
                   SURFACE → DOCK → AUTO_RELEASE → DONE

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
from vision.aruco_qr import VisionPipeline

log = logging.getLogger(__name__)

# ── Tuning parameter (sesuaikan saat uji di kolam) ───────────────────────────
DEPTH_TARGET_BOTTOM   = 0.70   # m — target depth ke dasar (0.7-0.9m pool)
DEPTH_TARGET_SURFACE  = 0.05   # m — threshold "di permukaan"
DEPTH_TOLERANCE       = 0.05   # m — toleransi depth

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
TIMEOUT_DOCK          = 15.0   # detik max docking
TIMEOUT_RELEASE       = 10.0   # detik max lepas payload autonomous

# Heading target tiap sisi kolam (sesuai orientasi kolam, kalibrasi di lokasi)
WALL_HEADING = {'A': 270, 'B': 90, 'C': 0, 'D': 180}


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
    AUTO_RELEASE  = auto()   # Misi 5: lepas payload autonomous
    DONE          = auto()
    ABORT         = auto()


# ── Telemetri dari rov_link (diterima via UDP) ────────────────────────────────
class TelemetryReceiver:
    """Dengarkan telemetri JSON dari rov_link.py di port 14551."""

    def __init__(self, host='0.0.0.0', port=14551):
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

        self._state   = State.IDLE
        self._state_t = time.time()   # waktu masuk state saat ini
        self._target_wall: Optional[str] = None
        self._score   = {'m1': 0, 'm2': 0, 'm3': 0, 'm4': 0, 'm5': 0}
        self._running = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, start_state: State = State.DIVE):
        """Mulai eksekusi misi dari state tertentu (default DIVE = full misi 1-5).

        Untuk strategi 'misi 1-4 manual, hanya misi 5 autonomous':
        jalankan operator manual via GUI sampai docking, lalu start_state=AUTO_RELEASE.
        """
        log.info("[FSM] ===== MISI ROV KKI 2026 DIMULAI (start=%s) =====", start_state.name)
        self._running = True
        self.cmd.arm(True)          # WAJIB: arm dulu sebelum thruster merespons
        time.sleep(0.5)
        self._transition(start_state)
        self._loop()

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
            vis   = self.vision.last_result()

            if self._state == State.DIVE:
                self._state_dive(telem)
            elif self._state == State.SCAN_QR:
                self._state_scan_qr(telem, vis)
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
            elif self._state == State.AUTO_RELEASE:
                self._state_auto_release(telem)

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
            self._transition(State.AUTO_RELEASE)

    def _state_auto_release(self, telem):
        """
        Misi 5 (40 poin): lepas payload secara AUTONOMOUS.
        Ini adalah misi bernilai tertinggi di KKI 2026.

        Urutan:
          1. Selam kembali ke posisi hook
          2. Ambil payload dari hook (buka gripper → tutup → tarik)
          3. Bawa ke permukaan
        """
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_RELEASE + 20:
            log.error("[FSM] AUTO_RELEASE timeout!")
            self._score['m5'] = 10  # partial credit: remotely
            self._transition(State.DONE)
            return

        depth = telem.get('depth', 0.0)

        # Phase 1: selam kembali ke level hook (0-8s)
        if elapsed < 8.0:
            if depth < 0.45:  # hook ada di kedalaman 0.45m (lihat panduan)
                self.cmd.send(vert=-DIVE_SPEED)
                log.debug("[FSM] AUTO_RELEASE selam ke hook depth=%.2f", depth)
            else:
                self.cmd.send(vert=0)
        # Phase 2: maju ke hook & ambil (8-15s)
        elif elapsed < 12.0:
            self.cmd.send(surge=15, gripper=0)
            log.debug("[FSM] AUTO_RELEASE mendekati hook")
        elif elapsed < 15.0:
            self.cmd.send(surge=0, gripper=1)
            log.debug("[FSM] AUTO_RELEASE tutup gripper ambil payload")
        # Phase 3: mundur dari dinding (15-18s)
        elif elapsed < 18.0:
            self.cmd.send(surge=-20, gripper=1)
            log.debug("[FSM] AUTO_RELEASE mundur dari dinding")
        # Phase 4: naik ke permukaan (18-26s)
        elif elapsed < 26.0:
            if depth > DEPTH_TARGET_SURFACE:
                self.cmd.send(vert=ASCEND_SPEED, gripper=1)
            else:
                self.cmd.send(vert=0, gripper=1)
            log.debug("[FSM] AUTO_RELEASE naik ke permukaan depth=%.2f", depth)
        # Phase 5: buka gripper lepas payload & selesai
        else:
            self.cmd.send(gripper=0)
            time.sleep(1.0)
            self.cmd.stop_all()
            self._score['m5'] = 40
            log.info("[FSM] ✓ Misi 5 AUTONOMOUS selesai (+40 poin)!")
            self._transition(State.DONE)

    # ── Utility ────────────────────────────────────────────────────────────────

    def _transition(self, new_state: State):
        log.info("[FSM] %s → %s", self._state.name, new_state.name)
        self._state   = new_state
        self._state_t = time.time()

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
    ap.add_argument('--telem-port', type=int, default=14551, help='Port telemetri dari rov_link')
    ap.add_argument('--vision', default='mock', choices=['mock', 'usb', 'rtsp'],
                    help='Sumber kamera')
    ap.add_argument('--device', type=int, default=0, help='Index USB webcam')
    ap.add_argument('--rtsp', default='rtsp://192.168.1.10:8554/cam',
                    help='URL RTSP jika --vision=rtsp')
    ap.add_argument('--start-state', default='DIVE',
                    choices=['DIVE', 'AUTO_RELEASE'],
                    help='DIVE=full misi 1-5 autonomous; AUTO_RELEASE=hanya misi 5 '
                         '(jalankan misi 1-4 manual via GUI dulu)')
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
                           rtsp_url=args.rtsp)

    telem.start()
    cam.start()

    log.info("[main] Mulai setelah 3 detik... (Ctrl+C untuk abort)")
    time.sleep(3)

    fsm = Mission5FSM(cmd=cmd, telem=telem, vision=cam)
    try:
        fsm.start(start_state=State[args.start_state])
    except KeyboardInterrupt:
        fsm.abort()
    finally:
        cam.stop()
        telem.stop()
        cmd.close()
        log.info("[main] Selesai. Skor: %s", fsm.score())


if __name__ == '__main__':
    main()
