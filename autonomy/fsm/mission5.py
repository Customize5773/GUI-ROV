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
import math
import logging
import threading
import argparse
from enum import Enum, auto
from typing import Optional

# Import vision pipeline
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vision.qr_detect import VisionPipeline, normalize_plane_yaw
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
# Diisi dari file config lokasi (pool_trial.yaml / pool_kki_trial.yaml /
# pool_kki_running.yaml). Bila terisi,
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

# SCAN_QR dulu cuma yaw di tempat menunggu decode penuh — di air keruh QR baru terbaca
# dari jarak jauh lebih dekat drpd air jernih (24 Agu: foto lapangan gagal decode walau QR
# sudah cukup besar di frame), jadi "spin & hope" sering timeout. SCAN_CREEP_MAX_SPEED
# membatasi seberapa cepat ROV boleh mendekat ke TEBAKAN CNN wall-hint (BELUM tervalidasi
# decode, lihat latest_wall_hint) — pelan drpd servo docking biasa krn sumbernya bisa salah.
SCAN_CREEP_MAX_SPEED  = 18     # % — TUNE di kolam (lebih pelan dari servo docking tervalidasi)

TIMEOUT_DIVE          = 15.0   # detik max untuk menyelam
TIMEOUT_SCAN          = 20.0   # detik max untuk scan QR
SCAN_SWEEP_T           = 3.0    # detik tiap arah sweep yaw saat QR belum terlihat
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
SERVO_TARGET_X     = 0.0      # PBVS: offset sumbu kamera ke mulut gripper (m)
SERVO_TARGET_Y     = 0.0      # PBVS: offset sumbu kamera ke mulut gripper (m)
SERVO_TARGET_YAW_DEG = 0.0    # PBVS: yaw QR saat tepat di mulut gripper
SERVO_KP_YAW       = 0.0      # >0 → ROV squaring tegak lurus dinding saat dock (aktifkan stlh verifikasi)
SERVO_MAX_SPEED    = 35.0     # % — pagar servo QR; bench dapat override ke 100
CALIB_FILE         = "vision/calibration/dwe_underwater.npz"  # jalur satu-kamera lama; None → IBVS
CALIB_FILE_BOTTOM  = "vision/calibration/bottom.npz"  # kalibrasi kamera QR/BOTTOM (mode dual-camera)
CALIB_FILE_WALL    = "vision/calibration/wall.npz"    # kalibrasi kamera hook/WALL (mode dual-camera)

# Gain PID servo docking (TUNE di kolam — pindahkan lwt --config bila sering diubah)
IBVS_KP_SWAY, IBVS_KP_SURGE, IBVS_KP_VERT = 45.0, 40.0, 35.0    # mode IBVS (piksel)
PBVS_KP_SWAY, PBVS_KP_SURGE, PBVS_KP_VERT = 140.0, 140.0, 110.0  # mode PBVS (meter)

# Peredam approach docking — agar mendekat MULUS, bukan mematuk-matuk lalu meleset.
# Semua TUNE di kolam (tersedia di --config, lihat config/loader.py). Ki sengaja
# dibiarkan 0: integral baru berguna setelah trim buoyancy terukur di air, dan
# windup saat approach panjang lebih berbahaya drpd sisa error tetap kecil.
SERVO_KD_IBVS      = 6.0     # derivative IBVS (error ternormalisasi) — redam overshoot
SERVO_KD_PBVS      = 25.0    # derivative PBVS (error meter)
SERVO_SLEW         = 120.0   # %/detik batas laju command — anti-sentak (sentak → ROV miring)
SERVO_DEADBAND_NORM = 0.02   # IBVS: |error| ternormalisasi di bawah ini dianggap 0
SERVO_DEADBAND_M   = 0.01    # PBVS: |error| (m) di bawah ini dianggap 0
SERVO_APPROACH_FLOOR = 0.15  # fraksi surge minimum selagi masih melenceng lateral

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
UNHOOK_LIFT_M      = 0.12     # m naik sementara; tinggi bukaan U tidak diberi di guidebook
UNHOOK_LIFT_T      = 3.0      # detik angkat khusus jalur fallback tanpa feedback
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

# ── M5_SEARCH: pencarian lateral kembali ke gantungan ────────────────────────
# MARK merekam heading + depth — 2 dari 3 derajat kebebasan. Itu cukup untuk MENGHADAP
# dinding di kedalaman benar, tapi TIDAK memberi tahu ROV ada di sebelah mana sepanjang
# dinding (posisi sandar misi 4 terserah pilot). Sapu yaw di tempat secara fundamental
# tak bisa memperbaiki lenceng lateral — maka ladder di bawah: mundur dulu memperlebar
# bidang pandang, lalu zigzag menyusur dinding dgn leg yang MEMBESAR.
#
# Gerak lateral lewat yaw+surge, BUKAN sway: frame 3-2-1 cuma punya 1 thruster sway,
# lemah dan menimbulkan roll mekanis. Yaw bolak-balik pakai kompas ABSOLUT sehingga
# galat arah tak menumpuk antar leg.
#
# SEMUA angka detik di bawah adalah TEBAKAN sampai SEARCH_SPEED dikalibrasi ke m/s di
# kolam (surge 20% selama 10 s, ukur jarak) — lihat ROADMAP_MISI5.md Fase 3.
TIMEOUT_SEARCH     = 90.0     # detik max pencarian sebelum degradasi ke fallback timed

# ── Jam total heat (jaga-jaga di ATAS jumlah TIMEOUT_* per state) ────────────
# Tiap TIMEOUT_* di atas independen per state — dijumlahkan berurutan, worst-case
# misi 5 saja (SEARCH+DOCK+ENGAGE+UNHOOK+ASCEND) >2,5 menit, DI LUAR misi 1-4.
# TIME_BUDGET_TOTAL adalah pagar KEDUA: kalau waktu heat tersisa sudah lebih
# kecil dari kebutuhan MINIMUM buat menuntaskan sisa rantai lewat fallback
# tercepat, state lompat ke fallback SEKARANG alih-alih menghabiskan timeout-nya
# sendiri dulu — menjamin M5_FALLBACK (yang tetap kasih skor) sempat jalan
# sebelum peluit, bukan terpotong ABORT diam. Default besar (lebih dari jumlah
# semua TIMEOUT_* gabungan) supaya SITL/simulator existing TIDAK berubah
# perilaku kecuali tim mengetatkan lewat --config sesuai durasi heat venue.
TIME_BUDGET_TOTAL  = 600.0    # detik — TUNE ke durasi heat KKI via --config
SEARCH_SPEED       = 20       # % surge saat mundur & menyusur (seordo DOCK_APPROACH_SPEED)
SEARCH_BACKOFF_T   = 6.0      # detik mundur perlebar FOV (~1.2 m; HFOV 60° ⇒ ±0.87 m dari 1.5 m)
SEARCH_LOOK_T      = 2.0      # detik diam menghadap dinding (≥3 frame vision @10 Hz)
SEARCH_LEG_T0      = 3.0      # detik leg pertama (~0.6 m ≈ satu lebar FOV, tanpa celah)
SEARCH_LEG_GROW    = 1.5      # faktor pembesar leg: 3 → 4.5 → 6.75 → 10 s
SEARCH_LEG_T_MAX   = 10.0     # detik leg maksimum (~2 m; lebih dari itu keluar kolam)
SEARCH_SPAN_MAX_T  = 12.0     # PAGAR KERAS: |dead-reckon lateral| ≤ ~2.4 m dari titik mark
SEARCH_YAW_TOL     = 8        # derajat — lebih ketat dari deadband 10° agar leg tegak lurus
SEARCH_CREEP_MAX_T = 8.0      # detik max merayap ke hint TAK tervalidasi (pola HOOK_ACQUIRE_T)

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

# ── Misi 5 sisi kiri (alur lomba 2026) ──────────────────────────────────────
# LANGKAH 1-2 (prep surge+sway+depth, lalu putar menghadap dinding) TIDAK ada di
# sini: keduanya dijalankan sistem MOTION sbg CASE di rov_mission5_bridge.py, dgn
# ALT_HOLD memegang depth dan _heading_control memegang heading. FSM mengambil
# alih dari langkah 3 (M5_YOLO_SEARCH) dan menerima heading CASE terakhir lewat
# parameter `heading_hold` untuk ditahan selagi mencari.
#
# Urutan di sini: maju cari YOLO → bidik ujung hook "J" → QR yaw+sway → gripper
# close → M5_UNHOOK → surface.
# Langkah "mundur" TIDAK berdiri sendiri: payload duduk di hook candy-cane, jadi
# lubangnya harus DIANGKAT dulu baru ditarik — itu persis _state_m5_unhook
# (angkat berbasis depth UNHOOK_LIFT_M, lalu tarik M5_UNHOOK_SURGE selama
# UNHOOK_PULL_T). Menarik mundur tanpa angkat cuma menyeret ROV di hook.
# Nilai gerak/waktu adalah knob trial kolam, bukan klaim sudah terkalibrasi.
LEFT_TIMEOUT_YOLO     = 30.0   # jam ABORT — bukan jam maju, lihat LEFT_ADVANCE_MAX_T
LEFT_TIMEOUT_ALIGN    = 20.0   # detik max membidik ujung J sebelum degradasi
# PAGAR JARAK maju buta di M5_YOLO_SEARCH. ROV tak punya sensor jarak depan, jadi
# satu-satunya rem sebelum menabrak dinding adalah detik × kecepatan (pola sama
# SEARCH_SPAN_MAX_T). Perintah SEARCH_SPEED masih menjadi target, tetapi limiter
# visual di bawah membatasi output aktual ke LEFT_VISUAL_MAX_SURGE. Jarak aktual
# wajib diukur lagi di kolam setelah perubahan cap/slew ini.
# Habis budget → berhenti maju tapi TETAP melihat sampai LEFT_TIMEOUT_YOLO.
# ⚠ SKALAKAN per arena (pool_kki_running.yaml 10×10 m sudah di-override).
LEFT_ADVANCE_MAX_T    = 15.0
LEFT_YOLO_SOURCE_GRACE = 3.0
LEFT_YOLO_CONF        = 0.35
HOOK_KEYPOINT_CONF    = 0.35   # minimum confidence titik kontrol kritis 2..5
LEFT_YOLO_AREA_FRAC   = 0.08   # bbox/frame saat jarak kerja; ukur lewat
                               # preflight_check.py --hook-model di jarak gripper.
                               # Dipakai sbg target_area hook_servo, DIKALIKAN luas
                               # frame nyata supaya bebas resolusi (lihat _align_target).
LEFT_YOLO_LOCK_FRAMES = 5
# Titik bidik langkah 4: KEPALA UJUNG HOOK "J" — tempat payload benar-benar
# tergantung — bukan centroid bbox. Fraksi terhadap bbox YOLO; ujung J ada di
# SISI BAWAH. ⚠ CEK pada frame nyata (preflight --hook-model) sebelum masuk air:
# titik ini menentukan ke mana gripper diarahkan, dan salah sedikit = meleset dari
# payload. 0.9 masih tebakan awal.
HOOK_TIP_X_FRAC       = 0.5    # 0=kiri bbox, 1=kanan bbox
HOOK_TIP_Y_FRAC       = 0.9    # 0=atas bbox, 1=bawah bbox
LEFT_HOOK_YAW_KP      = 20.0   # % yaw per error-X ternormalisasi point 5
LEFT_HOOK_MAX_YAW     = 8.0    # approach hook lebih pelan daripada QR final
LEFT_QR_YAW_KP        = 20.0   # dipakai HANYA bila pose QR tak ada (IBVS, tanpa kalibrasi)
# Sumber error yaw saat pose tersedia: KEMIRINGAN bidang QR (solvePnP yaw_deg),
# bukan lenceng lateral. Dulu yaw dan sway sama-sama digerakkan oleh ex sehingga
# "QR di tengah gambar" bisa dicapai dgn MENGHADAP, bukan dgn BERADA DI DEPAN —
# gripper menutup miring. Dgn dua sumber terpisah, sway mengurus posisi dan yaw
# mengurus ketegaklurusan, dan syarat selesai menuntut keduanya.
# ⚠ VERIFIKASI ARAH di kolam (lihat SERVO_INVERT): balik tanda bila error MEMBESAR.
LEFT_QR_YAW_KP_DEG    = 1.0    # % yaw per derajat kemiringan (20° → cap 20%)
LEFT_QR_YAW_TOL_DEG   = 8.0    # derajat — seordo SEARCH_YAW_TOL
LEFT_QR_MAX_YAW       = 20.0
LEFT_GRIP_T           = 2.0
# Limiter khusus alur visual langkah 3-5. Servo umum tetap punya tuning lama;
# mendekati hook/payload dibatasi lebih rendah agar ROV tidak meluncur saat
# deteksi pertama muncul atau ketika error QR berubah antar-frame.
LEFT_VISUAL_MAX_SURGE = 12.0    # % maju/mundur
LEFT_VISUAL_MAX_SWAY  = 12.0    # % geser lateral
LEFT_VISUAL_MAX_YAW   = 10.0    # % putar
LEFT_VISUAL_MAX_VERT  = 10.0    # % koreksi tinggi
LEFT_VISUAL_SLEW      = 20.0    # %/detik; 0 -> 10% memerlukan sekitar 0,5 s


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
    M5_SEARCH     = auto()   # Misi 5a': cari gantungan menyusur dinding (lenceng lateral)
    M5_DOCK       = auto()   # Misi 5b: docking closed-loop ke QR (PBVS/IBVS) — "nembak x & y"
    M5_ENGAGE     = auto()   # Misi 5c: grab payload (tetap hold x/y via pose)
    M5_UNHOOK     = auto()   # Misi 5d: angkat lubang payload lepas dari hook
    M5_ASCEND     = auto()   # Misi 5e: naik ke permukaan bawa payload
    M5_FALLBACK   = auto()   # Misi 5*: jalur timed (degraded) bila visual gagal
    # Langkah 1-2 dijalankan CASE sistem MOTION di bridge, bukan state di sini.
    M5_YOLO_SEARCH = auto()  # langkah 3: maju sampai YOLO laptop melihat hook
    M5_HOOK_ALIGN = auto()   # langkah 4: bidik kepala ujung hook "J"
    M5_QR_DOCK    = auto()   # langkah 5: QR — auto yaw + auto sway
    M5_GRIP       = auto()   # tutup gripper (lalu M5_UNHOOK: angkat + tarik)
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
            # src='fsm' menandai frame ini datang dari autonomy, bukan operator.
            # Kill-switch di rov_link.handle_command pakai tanda ini — DULU dia
            # nebak dari alamat pengirim (127.0.0.1 = loopback = FSM), yang diam-diam
            # mati begitu server.js jalan sehost dgn rov_link (SITL/GUI di Pi):
            # axis operator ikut ber-IP loopback, dianggap FSM, abort tak pernah nyala.
            raw = json.dumps({'name': name, 'value': value, 'src': 'fsm'}).encode()
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
                 vision: VisionPipeline, runlog=None,
                 marked_heading=None, marked_depth=None, hook_map_file=None,
                 hook_calib_file=None, yolo_source=None, heading_hold=None,
                 bench_qr_dock=False, qr_source=None):
        # Tanda gantungan yang direkam operator lewat tombol MARK saat misi 3
        # (command `mark_hook` di rov_agent.py), yaitu SAAT payload benar-benar
        # tergantung di hook. Dipakai M5_REDIVE untuk kembali ke sana.
        #
        # Kenapa heading+depth dan BUKAN koordinat x/y: wahana ini tak punya
        # estimasi posisi horizontal sama sekali — tidak ada GPS/DVL/optical
        # flow, LOCAL_POSITION_NED tak pernah tiba (pos_n/pos_e selalu None).
        # Lihat rov_agent.py bagian POSHOLD.
        #
        # Keduanya None → perilaku lama utuh (WALL_HEADING + HOOK_DEPTH), jadi
        # jalur SITL dan misi 1→5 penuh tidak berubah sama sekali.
        self._marked_heading = marked_heading
        self._marked_depth   = marked_depth
        # Heading absolut yang dicapai CASE MOTION terakhir (langkah 2), diserahkan
        # bridge saat rantai CASE→FSM. M5_YOLO_SEARCH menahannya selagi maju supaya
        # ROV tak melenceng dari dinding yang sudah dihadapkan. None = tak menahan.
        self._heading_hold   = heading_hold
        self._bench_qr_dock  = bool(bench_qr_dock)
        self.cmd    = cmd
        self.telem  = telem
        self.vision = vision
        self.runlog = runlog        # tools.run_log.RunLogger | None (None = tak merekam)
        self._yolo_source = yolo_source or (lambda: None)
        # Worker QR sisi-laptop (best_new.pt: bbox region QR -> crop -> decode).
        # HANYA sumber tambahan: bila kosong/basi, _fresh_payload jatuh ke decode
        # lokal Pi seperti sebelumnya, jadi putusnya link laptop tidak mematikan
        # QR docking.
        self._qr_source = qr_source or (lambda: None)
        self._sample_t = 0.0        # timestamp sample JSONL terakhir (throttle ~2 Hz)
        # Peredam approach — dipakai SEMUA instans servo di bawah agar satu tuning
        # berlaku seragam (IBVS & PBVS beda satuan error, jadi deadband-nya beda).
        _ibvs_smooth = dict(kd=SERVO_KD_IBVS, slew=SERVO_SLEW,
                            deadband=SERVO_DEADBAND_NORM, approach_floor=SERVO_APPROACH_FLOOR)
        _pbvs_smooth = dict(kd=SERVO_KD_PBVS, slew=SERVO_SLEW,
                            deadband=SERVO_DEADBAND_M, approach_floor=SERVO_APPROACH_FLOOR)
        # Servo docking ke QR payload (IBVS piksel / PBVS meter). Arah sumbu = SERVO_INVERT.
        self.servo      = VisualServo(target_area=SERVO_TARGET_AREA, kp_yaw=SERVO_KP_YAW,
                                      kp_sway=IBVS_KP_SWAY, kp_surge=IBVS_KP_SURGE,
                                      kp_vert=IBVS_KP_VERT, max_speed=SERVO_MAX_SPEED,
                                      **_ibvs_smooth, **SERVO_INVERT)
        self.pose_servo = PoseServo(target_dist=SERVO_TARGET_DIST, kp_yaw=SERVO_KP_YAW,
                                    kp_sway=PBVS_KP_SWAY, kp_surge=PBVS_KP_SURGE,
                                    kp_vert=PBVS_KP_VERT, max_speed=SERVO_MAX_SPEED,
                                    **_pbvs_smooth, **SERVO_INVERT)
        # Servo "creep" SCAN_QR — mendekat ke tebakan CNN wall-hint (BELUM tervalidasi
        # decode) sebelum decode penuh berhasil, gantikan yaw-di-tempat murni saat air
        # keruh butuh jarak baca lebih dekat. Target sama dgn SERVO_TARGET_AREA (satu
        # knob jarak) tapi max_speed jauh lebih pelan & tanpa yaw-align (sumber kasar).
        self.scan_creep_servo = VisualServo(target_area=SERVO_TARGET_AREA, kp_yaw=0.0,
                                            kp_sway=IBVS_KP_SWAY, kp_surge=IBVS_KP_SURGE,
                                            kp_vert=IBVS_KP_VERT, max_speed=SCAN_CREEP_MAX_SPEED,
                                            **_ibvs_smooth, **SERVO_INVERT)
        # Servo docking ke HOOK (misi 3b HANG + misi 4 DOCK). Gain sama spt docking QR
        # (reuse kelas yang sama), hanya target area/jarak khusus hook.
        self.hook_servo      = VisualServo(target_area=HOOK_TARGET_AREA, kp_yaw=SERVO_KP_YAW,
                                           kp_sway=IBVS_KP_SWAY, kp_surge=IBVS_KP_SURGE,
                                           kp_vert=IBVS_KP_VERT, **_ibvs_smooth, **SERVO_INVERT)
        self.hook_pose_servo = PoseServo(target_dist=HOOK_TARGET_DIST, kp_yaw=SERVO_KP_YAW,
                                         kp_sway=PBVS_KP_SWAY, kp_surge=PBVS_KP_SURGE,
                                         kp_vert=PBVS_KP_VERT, **_pbvs_smooth, **SERVO_INVERT)
        self._servo_t = None        # timestamp langkah servo terakhir (utk dt nyata)

        self._state   = State.IDLE
        self._state_t = time.time()   # waktu masuk state saat ini
        self._mission_t0 = None       # diisi start() — None = belum mulai (_time_left "banyak")
        self._target_wall: Optional[str] = None
        self._score   = {'m1': 0, 'm2': 0, 'm3': 0, 'm4': 0, 'm5': 0}
        self._running = False
        self._require_auto = True      # bila True, abort saat mode balik ke MANUAL
        # Loss-of-lock tracker untuk docking misi 5 (M5_DOCK / M5_ENGAGE)
        self._m5_last_det_t  = 0.0     # waktu terakhir QR payload terlihat
        self._m5_search_dir  = 1       # arah sapu reacquire = sisi QR terakhir (+kanan/−kiri)
        self._scan_sweep_dir = 1
        self._scan_sweep_t = self._state_t
        # Loss-of-lock tracker untuk docking HOOK (HANG misi 3b / DOCK misi 4)
        self._hook_last_det_t = 0.0    # waktu terakhir hook terlihat
        self._hook_search_dir = 1      # arah sapu reacquire = sisi hook terakhir
        # M5_SEARCH — ladder pencarian lateral (di-reset di _transition)
        self._search_phase   = 'backoff'  # backoff → look → turn_out → traverse → turn_back → look…
        self._search_phase_t = 0.0        # waktu masuk sub-fase saat ini
        self._search_leg_t   = SEARCH_LEG_T0   # durasi leg menyusur saat ini (membesar)
        self._search_dir     = 1          # sisi menyusur (+kanan/−kiri dari marked_heading)
        self._search_pos_t   = 0.0        # ponytail: dead-reckon integral WAKTU bertanda,
                                          # satu-satunya "odometri" — murni pagar span,
                                          # ganti dgn posisi sungguhan bila ada DVL
        self._search_creep_t = None       # timestamp mulai merayap ke hint (None = tak merayap)
        self._search_creep_block = False  # True = jangan merayap sampai ROV pindah posisi
        # Sub-fase & degradasi HANG/DOCK (None = belum aktif)
        self._hang_release_t   = None  # timestamp mulai fase lepas payload pasca-align
        self._hang_fallback_t  = None  # timestamp mulai jalur timed HANG (degradasi)
        self._dock_fallback_t  = None  # timestamp mulai jalur timed DOCK (degradasi)
        self._hang_used_fallback = False  # instrumentasi: HANG jatuh ke fallback timed?
        self._dock_used_fallback = False  # instrumentasi: DOCK jatuh ke fallback timed?
        self._unhook_start_depth = None
        self._unhook_pull_t = None
        self._left_depth = None   # target depth alur kiri (marked_depth / HOOK_DEPTH)
        self._left_search_hits = 0
        self._align_target_set = False  # target_area hook_servo sudah diskalakan ke frame?
        self._left_visual_t = None
        self._left_visual_cmd = {"surge": 0.0, "sway": 0.0,
                                 "yaw": 0.0, "vert": 0.0}

        # Telemetri live untuk GUI (dibaca rov_link.py, diteruskan sbg field "mission5").
        self.telemetry_out = {
            'state': self._state.name, 'active_cam': None,
            'distance_z': None, 'offset_x': None, 'offset_y': None,
            # bbox (x,y,w,h) + confidence (0..1) dari detect_hook() — overlay
            # kepercayaan pilot di GUI selama docking HOOK (kamera WALL saja).
            'bbox': None, 'confidence': None,
            # Hasil decode QR terakhir dari pipeline vision Python (bukan scan
            # jsQR di browser) — dibaca readout QR di halaman Control.
            'qr_data': None, 'qr_wall': None,
            # Sisa jam heat (detik) — lihat _time_left(). None sampai start().
            'time_left': None,
            # Lokalisasi hook (OPSIONAL, lihat --hook-map). None = fitur mati,
            # yang juga kondisi default. Field baru saja — rov_link.py menyalin
            # dict ini bulat-bulat, jadi field lama tak terganggu.
            'hook_loc': None,
        }

        # ── Lokalisasi hook (OPSIONAL) ────────────────────────────────────────
        # Mati total kecuali --hook-map diberikan. Gagal muat = warning, BUKAN
        # abort: ini fitur tambahan, misi 5 QR harus tetap jalan tanpanya.
        self.hook_loc = None
        self._hook_loc_t = 0.0        # throttle (~2 Hz, pola sama _log_sample)
        if hook_map_file:
            try:
                from vision.hook_localization import (HookTracker, load_calibration,
                                                      load_hook_map)
                # Kalibrasi dimuat dari file (bukan dari K yang sudah ada di
                # VisionPipeline) SEMATA supaya `image_size` ikut terbaca — tanpa
                # itu gate resolusi tak bisa menilai apa pun, dan gate itulah yang
                # menangkap kelas bug 22 Agu (K resolusi lain → jarak meleset
                # berlipat, diam-diam). Lihat qr_detect._verify_calib_size().
                calib = load_calibration(hook_calib_file) if hook_calib_file else None
                self.hook_loc = {
                    'map': load_hook_map(hook_map_file),
                    'tracker': HookTracker(),
                    'calib': calib,
                }
                log.info("[FSM] Hook localization AKTIF — map %s, kalibrasi %s",
                         hook_map_file, hook_calib_file or '(dari VisionPipeline)')
            except Exception as e:
                log.warning("[FSM] Hook localization NONAKTIF — gagal muat %s: %s",
                            hook_map_file, e)
                self.hook_loc = None

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
        # Selalu katakan sumber arah yang dipakai. Gerbang yang diam-diam tak
        # menyala sudah dua kali memakan waktu debug di proyek ini; operator
        # harus bisa melihat SEBELUM wahana bergerak apakah MARK terbaca.
        if self._marked_heading is not None or self._marked_depth is not None:
            log.info("[FSM] MARK gantungan dipakai — heading=%s depth=%s",
                     "-" if self._marked_heading is None else f"{self._marked_heading:.0f}°",
                     "-" if self._marked_depth is None else f"{self._marked_depth:.2f} m")
        else:
            log.warning("[FSM] TANPA MARK — arah dari WALL_HEADING/QR misi 1. "
                        "Pada alur misi 1-4 manual keduanya kosong, jadi M5_REDIVE "
                        "akan menyapu pelan dan mungkin timeout. Tekan MARK di gantungan.")
        self._running = True
        self._require_auto = wait_mode
        if wait_mode and not self._wait_for_autonomous():
            log.warning("[FSM] Batal: tidak masuk mode AUTONOMOUS")
            return
        if self._bench_qr_dock:
            log.info("[FSM] BENCH QR — menunggu decode fresh sebelum ARM")
            while self._running and self._fresh_payload(0.5) is None:
                time.sleep(0.1)
            if not self._running:
                return
        self.cmd.arm(True)
        if not self._bench_qr_dock:
            time.sleep(0.5)
        self._mission_t0 = time.time()
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
            self.telemetry_out['time_left'] = round(self._time_left(), 1)

            # QR terakhir yang masih segar, independen dari state saat ini —
            # supaya operator lihat hasil scan pipeline vision di GUI kapan pun.
            qr = self.vision.latest_qr(max_age=2.0)
            self.telemetry_out['qr_data'] = qr['data'] if qr else None
            self.telemetry_out['qr_wall'] = qr['wall'] if qr else None

            self._hook_localize(telem)

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
            elif self._state == State.M5_SEARCH:
                self._state_m5_search(telem)
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
            elif self._state == State.M5_YOLO_SEARCH:
                self._state_m5_yolo_search(telem)
            elif self._state == State.M5_HOOK_ALIGN:
                self._state_m5_hook_align(telem)
            elif self._state == State.M5_QR_DOCK:
                self._state_m5_qr_dock(telem)
            elif self._state == State.M5_GRIP:
                self._state_m5_grip(telem)

            self._log_sample(telem)
            time.sleep(0.1)

        self.cmd.stop_all()
        self._print_score()

    def _hook_localize(self, telem):
        """Lokalisasi hook OPSIONAL → telemetry_out['hook_loc'] (+ run log ~2 Hz).

        Sengaja dipanggil DI SINI (satu tempat, di refresh telemetri _loop) dan
        BUKAN di _hook_servo_step(): satu titik menutup semua state, dan logika
        servo yang sudah tervalidasi kolam tak ikut tersentuh sama sekali. Modul
        ini murni PENGAMAT — tak satu pun state membaca hasilnya untuk mengambil
        keputusan gerak."""
        if self.hook_loc is None:
            return
        now = time.time()
        if now - self._hook_loc_t < 0.5:      # 2 Hz cukup; loop 10 Hz cuma bikin panas
            return
        self._hook_loc_t = now
        try:
            det = self.vision.latest_hook(max_age=1.0)
            if det is None:
                self.telemetry_out['hook_loc'] = None
                return
            from vision.hook_localization import localize_hook
            hl = self.hook_loc
            calib = hl['calib']
            if calib is None:                 # tak ada file → pinjam K VisionPipeline
                # `is None` eksplisit, BUKAN `a or b`: K/dist itu ndarray, dan
                # bool(ndarray) melempar ValueError "truth value is ambiguous" —
                # yang di sini akan tertelan except di bawah & mematikan fitur
                # diam-diam tiap siklus.
                K = getattr(self.vision, '_K_hook', None)
                dist = getattr(self.vision, '_dist_hook', None)
                if K is None:
                    K = getattr(self.vision, '_K', None)
                    dist = getattr(self.vision, '_dist', None)
                if K is None:
                    return
                calib = {'K': K, 'dist': dist, 'image_size': None,
                         'name': 'VisionPipeline'}
            res = localize_hook(det, calib, hook_map=hl['map'], vehicle_state=telem,
                                camera_to_base=hl['map']['camera_to_base'],
                                tracker=hl['tracker'], frame=det.get('_frame'))
            # Ringkas utk telemetri — covariance 36 elemen tiap paket UDP itu
            # boros dan tak dipakai GUI; ambil sigma diagonal posisi saja.
            cov = res.get('covariance')
            self.telemetry_out['hook_loc'] = {
                'status': res['status'], 'hook_id': res['hook_id'],
                'pose_map': res['pose_map_base'], 'rel_base': res['relative_pose_base'],
                'reproj_px': res['reprojection_error_px'],
                'sigma_xy_m': round(cov[0] ** 0.5, 3) if cov else None,
                'reason': res['reason'],
            }
            if self.runlog:
                self.runlog.event('hook_loc', **self.telemetry_out['hook_loc'])
        except Exception as e:
            # Fitur tambahan TIDAK boleh menjatuhkan loop misi.
            log.debug("[FSM] hook localization error: %s", e)
            self.telemetry_out['hook_loc'] = None

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
            self.scan_creep_servo.reset()
            self._transition(State.GRAB)
            return

        # Decode penuh belum berhasil — kalau CNN wall-hint (lihat qr_detect.py
        # latest_wall_hint) menemukan bentuk QR meski tak terbaca, mendekat ke situ
        # alih-alih cuma diam berputar (air keruh butuh jarak baca lebih dekat).
        # Tebakan BELUM tervalidasi: dipakai HANYA utk arah/jarak kasar, TIDAK
        # pernah mengisi self._target_wall (itu hanya dari decode penuh di atas).
        hint = self.vision.latest_wall_hint(max_age=1.0)
        if hint and hint.get('center') is not None and hint.get('area') is not None:
            out = self.scan_creep_servo.step(hint['center'][0], hint['center'][1],
                                              hint['area'], hint['frame_w'], hint['frame_h'],
                                              dt=self._servo_dt())
            if out.aligned:
                # Sudah sedekat target engage tapi decode masih gagal — jangan terus
                # maju berbekal tebakan tak tervalidasi (risiko tabrak dinding). Diam,
                # tetap coba decode tiap tick sampai TIMEOUT_SCAN.
                self.cmd.stop_all()
            else:
                self.cmd.send(surge=out.surge, sway=out.sway, vert=out.vert)
            log.debug("[FSM] SCAN_QR mendekat ke tebakan wall=%s conf=%.2f aligned=%s",
                      hint['wall'], hint['confidence'], out.aligned)
        else:
            self.scan_creep_servo.reset()
            # Sweep bolak-balik mencegah ROV terus berputar satu arah.
            if time.time() - self._scan_sweep_t >= SCAN_SWEEP_T:
                self._scan_sweep_dir *= -1
                self._scan_sweep_t = time.time()
            self.cmd.send(yaw=self._scan_sweep_dir * YAW_SPEED)
            log.debug("[FSM] SCAN_QR sweep dir=%+d elapsed=%.1fs", self._scan_sweep_dir, elapsed)

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
                # Cari hook: merayap pelan ke dinding + turun ke level hook (0.45m) sambil
                # sapu terarah ke sisi terakhir. WAJIB turun: DOCK masuk dari SURFACE
                # (depth~0.05m) sedangkan hook fisik ada di HOOK_DEPTH (0.45m) — kamera
                # depan terpasang LEVEL (tanpa tunduk), jadi tanpa koreksi depth ini hook
                # selalu di luar FOV vertikal sepanjang pencarian (root cause DOCK 0%
                # akuisisi, lihat memory dock-hook-acquisition-depth-mismatch).
                # Proporsional (bukan bang-bang spt HANG): diuji live di Gazebo — bang-bang
                # penuh (-DIVE_SPEED) numpuk momentum & OVERSHOOT ~0.3m sampai nabrak dasar
                # kolam (target 0.415m, dasar cuma 0.8m, jarak aman tipis). Proporsional
                # mengecil otomatis mendekati target (diverifikasi konvergen ke pita
                # 0.35-0.53m tanpa overshoot ke dasar), tak butuh tuning gain halus.
                depth = telem.get('depth', 0.0)
                depth_err = HOOK_DEPTH - depth       # positif = perlu turun
                if depth_err > DEPTH_TOLERANCE:
                    vert = -min(DIVE_SPEED, max(10, int(depth_err * 60)))
                elif depth_err < -DEPTH_TOLERANCE:
                    vert = min(ASCEND_SPEED, max(10, int(-depth_err * 60)))
                else:
                    vert = 0
                self.cmd.send(surge=int(DOCK_APPROACH_SPEED * 0.5), vert=vert,
                              yaw=YAW_SPEED * self._hook_search_dir)
                log.debug("[FSM] DOCK cari hook depth=%.2f dir=%+d", depth, self._hook_search_dir)
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

    def _servo_dt(self):
        """dt NYATA antar langkah servo (detik).

        Loop tidur 0.1 s DITAMBAH waktu kerja (decode QR, I/O), jadi `dt=0.1`
        hardcoded selalu meleset dan bikin suku D/I salah skala. Di-clamp: dt
        raksasa sehabis dropout panjang atau ganti state tak boleh berubah jadi
        lonjakan derivative yang menyentak thruster.
        """
        now = time.time()
        prev, self._servo_t = self._servo_t, now
        if prev is None:
            return 0.1
        return max(0.02, min(0.5, now - prev))

    def _servo_step(self, det):
        """Satu langkah visual servo dari deteksi QR. PBVS (pose 3D) bila ada, IBVS bila tidak.
        Kembalikan (ServoOutput, 'PBVS'|'IBVS'). Dipakai M5_REDIVE, M5_DOCK,
        dan M5_ENGAGE (hold x/y)."""
        dt = self._servo_dt()
        pose = det.get('pose')
        self.telemetry_out['active_cam'] = 'WALL'
        self.telemetry_out.update(bbox=None, confidence=None)  # bbox hook cuma dari cam WALL
        if pose is not None:                       # PBVS — pose 3D (m) bila terkalibrasi
            x_err = pose['x'] - SERVO_TARGET_X
            y_err = pose['y'] - SERVO_TARGET_Y
            yaw_err = normalize_plane_yaw(
                pose.get('yaw_deg', 0.0) - SERVO_TARGET_YAW_DEG)
            out = self.pose_servo.step(x_err, y_err, pose['z'], yaw_err, dt=dt)
            log.debug("[FSM] servo(PBVS) x=%.2f y=%.2f z=%.2f → su=%.0f sw=%.0f vt=%.0f",
                      pose['x'], pose['y'], pose['z'], out.surge, out.sway, out.vert)
            self.telemetry_out.update(distance_z=out.z, offset_x=out.x, offset_y=out.y)
            return out, 'PBVS'
        cx, cy = det['center']                     # IBVS — fallback error piksel
        out = self.servo.step(cx, cy, det['area'], det['frame_w'], det['frame_h'], dt=dt)
        log.debug("[FSM] servo(IBVS) ex=%.2f ey=%.2f ea=%.2f → su=%.0f sw=%.0f vt=%.0f",
                  out.ex, out.ey, out.ea, out.surge, out.sway, out.vert)
        self.telemetry_out.update(distance_z=None, offset_x=out.ex, offset_y=out.ey)
        return out, 'IBVS'

    def _hook_servo_step(self, det):
        """Satu langkah visual servo dari deteksi HOOK. PBVS (pose 3D) bila ada, IBVS bila tidak.
        Kembalikan (ServoOutput, 'PBVS'|'IBVS'). Reuse VisualServo/PoseServo — hanya instans &
        target khusus hook (lihat _servo_step untuk versi QR)."""
        dt = self._servo_dt()
        pose = det.get('pose')
        self.telemetry_out['active_cam'] = 'WALL'
        self.telemetry_out.update(bbox=det.get('bbox'), confidence=det.get('confidence'))
        if pose is not None:                       # PBVS — pose 3D (m) bila kamera terkalibrasi
            out = self.hook_pose_servo.step(pose['x'], pose['y'], pose['z'],
                                            pose.get('yaw_deg', 0.0), dt=dt)
            log.debug("[FSM] hook_servo(PBVS) x=%.2f y=%.2f z=%.2f → su=%.0f sw=%.0f vt=%.0f",
                      pose['x'], pose['y'], pose['z'], out.surge, out.sway, out.vert)
            self.telemetry_out.update(distance_z=out.z, offset_x=out.x, offset_y=out.y)
            return out, 'PBVS'
        cx, cy = det['center']                     # IBVS — fallback error piksel
        out = self.hook_servo.step(cx, cy, det['area'], det['frame_w'], det['frame_h'], dt=dt)
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
        qr = self._fresh_payload(0.5)
        # Kedalaman yang di-MARK menang atas HOOK_DEPTH: ia direkam di gantungan
        # sungguhan, jadi ikut mencoret offset tare permukaan (kedua pembacaan
        # memakai referensi yang sama). HOOK_DEPTH sendiri hanya benar bila
        # geometri kolam di config sudah diisi — lihat _derive_depths().
        target_depth = self._marked_depth if self._marked_depth is not None else HOOK_DEPTH
        near = depth >= target_depth - DEPTH_TOLERANCE

        # Timeout REDIVE kini cuma berarti "gagal MENYELAM", bukan "gagal cari QR" —
        # pencarian pindah ke M5_SEARCH yang punya anggaran waktunya sendiri. Selama
        # kedalaman sudah masuk akal, tetap layak mencari drpd langsung lepas buta.
        if elapsed > TIMEOUT_REDIVE:
            if depth >= target_depth - 2 * DEPTH_TOLERANCE:
                log.warning("[FSM] M5_REDIVE timeout tapi kedalaman tercapai — lanjut mencari")
                self.cmd.stop_all()
                self._transition(State.M5_SEARCH)
            else:
                log.error("[FSM] M5_REDIVE gagal menyelam (%.2f/%.2f m) — ABORT",
                            depth, target_depth)
                self.abort()
            return

        if qr is not None and near:
            log.info("[FSM] QR payload diperoleh @depth=%.2f (%s) — mulai docking",
                     depth, qr.get('data'))
            self.cmd.stop_all()
            self.servo.reset()
            self.pose_servo.reset()
            self._transition(State.M5_DOCK)
        elif not near:
            if qr is not None:
                # QR payload menjadi patokan posisi relatif selama turun. Terapkan
                # koreksi lateral/heading saja; jangan maju sebelum level hook agar
                # tidak menabrak dinding. Kedalaman tetap dikendalikan pressure sensor.
                self._note_detection(qr)
                out, mode = self._servo_step(qr)
                self.cmd.send(sway=out.sway, yaw=out.yaw, vert=-DIVE_SPEED)
                log.debug("[FSM] M5_REDIVE visual %s depth=%.2f→%.2f sway=%.0f yaw=%.0f",
                          mode, depth, target_depth, out.sway, out.yaw)
            else:
                # QR belum terlihat: gunakan heading hasil MARK untuk pendekatan kasar.
                self.cmd.send(vert=-DIVE_SPEED, yaw=self._heading_toward_wall(telem))
                log.debug("[FSM] M5_REDIVE selam depth=%.2f→%.2f qr=False",
                          depth, target_depth)
        else:
            # Sudah di level hook tapi QR belum terlihat. Dulu cabang ini menyapu yaw
            # DI TEMPAT sampai timeout — dan berputar di tempat secara fundamental tak
            # bisa memperbaiki LENCENG LATERAL sepanjang dinding (posisi sandar misi 4
            # terserah pilot). Serahkan ke M5_SEARCH yang benar-benar menyusur dinding.
            log.info("[FSM] M5_REDIVE @depth=%.2f — QR belum terlihat, mulai pencarian lateral", depth)
            self.cmd.stop_all()
            self._transition(State.M5_SEARCH)

    def _hold_depth(self, telem, target: float) -> int:
        """Perintah vert untuk MENAHAN kedalaman (bang-bang, 0 di dalam toleransi).

        Dipakai M5_SEARCH: pencarian bisa berlangsung puluhan detik, dan cabang sapu
        lama mengirim `send(yaw=…)` TANPA vert sama sekali sehingga kedalaman hanyut
        (ROV ini sedikit apung) — QR keluar dari bidang pandang vertikal justru saat
        sedang dicari. Bang-bang, bukan PID: konsisten dgn _state_dive/_state_surface.
        """
        depth = telem.get('depth', 0.0)
        err = target - depth                       # + = masih terlalu dangkal → turun
        if abs(err) <= DEPTH_TOLERANCE:
            return 0
        return -DIVE_SPEED if err > 0 else ASCEND_SPEED

    def _search_next_phase(self, phase: str):
        """Pindah sub-fase ladder M5_SEARCH + catat waktunya."""
        self._search_phase = phase
        self._search_phase_t = time.time()

    def _search_yaw_to(self, telem, target_hdg: float):
        """Yaw bang-bang ke heading absolut. Kembalikan (yaw_cmd, sudah_sampai).

        Kompas ABSOLUT — inilah yang membuat zigzag ladder self-correcting: tiap kali
        ROV berbelok balik menghadap dinding ia mengacu ke marked_heading lagi, jadi
        galat arah tak menumpuk antar leg (beda dgn dead-reckon yaw-rate).
        """
        err = self._heading_error(telem.get('heading', 0.0), target_hdg)
        if abs(err) < SEARCH_YAW_TOL:
            return 0, True
        return (YAW_SPEED if err > 0 else -YAW_SPEED), False

    def _state_m5_search(self, telem):
        """Misi 5a': cari gantungan yang lenceng ke SAMPING, menyusur dinding.

        MARK memberi heading + depth (2 dari 3 DOF) — arah hadap & kedalaman benar, tapi
        posisi SEPANJANG dinding tak diketahui. Ladder di bawah menyisir dimensi yang
        tak diketahui itu: mundur dulu memperlebar bidang pandang, lalu zigzag dgn leg
        membesar, memakai yaw+surge (sumbu kuat; sway cuma 1 thruster & bikin roll).

        Reakuisisi bertingkat tiap tick — QR terdecode > QR terlihat-tanpa-decode > hook.
        Tingkat kedua itu kuncinya: quad QR bisa DILOKALISASI jauh sebelum bisa DIBACA,
        jadi ROV boleh mencari dari jarak lebar lalu merayap mendekat sampai decode jadi.
        """
        target_depth = self._marked_depth if self._marked_depth is not None else HOOK_DEPTH
        vert = self._hold_depth(telem, target_depth)   # SELALU disertakan di tiap send()

        if self._elapsed() > TIMEOUT_SEARCH:
            log.error("[FSM] M5_SEARCH timeout %.0fs — gantungan tak ketemu, ABORT",
                        TIMEOUT_SEARCH)
            self.abort()
            return
        if self._time_left() < self._min_time_needed_from(State.M5_SEARCH):
            log.error("[FSM] Waktu heat tersisa %.0fs < kebutuhan minimum — ABORT pencarian",
                      self._time_left())
            self.abort()
            return

        # ── (a) Reakuisisi berprioritas ──────────────────────────────────────
        qr = self._fresh_payload(0.5)
        if qr is not None:
            log.info("[FSM] ✓ M5_SEARCH menemukan QR payload (%s) — mulai docking", qr.get('data'))
            self._note_detection(qr)
            self.cmd.stop_all()
            self.servo.reset()
            self.pose_servo.reset()
            self._transition(State.M5_DOCK)
            return

        # QR terlihat tapi belum terbaca (quad dari QRCodeDetector.detect) lebih
        # dipercaya drpd hook: ia memang QR payload, sedangkan hook bisa tertukar
        # dgn pipa/tangga lain di kolam.
        det = self.vision.latest_wall_hint(max_age=1.0) or self._fresh_hook(0.5)
        if det is not None and det.get('center') is not None and not self._search_creep_block:
            self._search_creep(det, vert)
            return
        if self._search_creep_t is not None:      # target hilang → kembali ke ladder
            self._end_search_creep("target hilang")

        # ── (b) Ladder yaw+surge ─────────────────────────────────────────────
        hdg_wall = self._marked_heading if self._marked_heading is not None \
            else telem.get('heading', 0.0)
        phase_el = time.time() - self._search_phase_t
        phase = self._search_phase

        if phase == 'backoff':
            # Mundur = memperlebar sapuan kamera tanpa gerak lateral sama sekali.
            yaw, _ = self._search_yaw_to(telem, hdg_wall)
            self.cmd.send(surge=-SEARCH_SPEED, yaw=yaw, vert=vert)
            if phase_el > SEARCH_BACKOFF_T:
                self._search_next_phase('look')
        elif phase == 'look':
            self.cmd.send(vert=vert)             # diam — beri vision waktu decode
            if phase_el > SEARCH_LOOK_T:
                self._search_next_phase('turn_out')
        elif phase == 'turn_out':
            yaw, done = self._search_yaw_to(telem, hdg_wall + self._search_dir * 90)
            self.cmd.send(yaw=yaw, vert=vert)
            if done:
                self._search_next_phase('traverse')
        elif phase == 'traverse':
            # Span = akumulasi WAKTU menyusur bertanda; leg berjalan dihitung dari
            # phase_el (bukan ditambah per-tick) supaya tak bergantung laju loop.
            span = self._search_pos_t + self._search_dir * phase_el
            capped = abs(span) >= SEARCH_SPAN_MAX_T
            self.cmd.send(surge=0 if capped else SEARCH_SPEED, vert=vert)
            if capped or phase_el > self._search_leg_t:
                self._search_pos_t = span        # commit leg ini ke total
                log.debug("[FSM] M5_SEARCH leg selesai (%.1fs, span=%.1f%s)",
                          phase_el, span, " CAPPED" if capped else "")
                self._search_next_phase('turn_back')
        elif phase == 'turn_back':
            yaw, done = self._search_yaw_to(telem, hdg_wall)
            self.cmd.send(yaw=yaw, vert=vert)
            if done:
                self._search_creep_block = False  # sudah pindah → kandidat baru boleh dicoba
                self._search_dir *= -1           # zigzag: sisi berikutnya berlawanan
                self._search_leg_t = min(self._search_leg_t * SEARCH_LEG_GROW, SEARCH_LEG_T_MAX)
                self._search_next_phase('look')

    def _search_creep(self, det, vert):
        """Merayap mendekat ke target yang TERLIHAT tapi belum tervalidasi decode."""
        if self._search_creep_t is None:
            self._search_creep_t = time.time()
            self.scan_creep_servo.reset()
            log.info("[FSM] M5_SEARCH melihat kandidat gantungan — merayap mendekat")
        if time.time() - self._search_creep_t > SEARCH_CREEP_MAX_T:
            # Sudah lama merayap tapi decode tak kunjung jadi: kemungkinan besar
            # objek lain. Jangan menempel dinding sampai timeout — lanjut menyisir.
            # BLOKIR creep sampai ROV benar-benar PINDAH (akhir leg berikutnya):
            # tanpa ini kandidat palsu yang menetap (pipa/tangga, atau hook yang
            # terlihat tapi QR-nya tak terbaca) langsung menarik ROV kembali tiap
            # tick — creep→menyerah→creep tanpa henti, ladder tak pernah jalan.
            self._search_creep_block = True
            self._end_search_creep("decode tak jadi — blokir sampai pindah")
            return
        cx, cy = det['center']
        out = self.scan_creep_servo.step(cx, cy, det['area'], det['frame_w'], det['frame_h'],
                                         dt=self._servo_dt())
        if out.aligned:
            self.cmd.send(vert=vert)             # sudah sedekat target — diam, tunggu decode
        else:
            self.cmd.send(surge=out.surge, sway=out.sway, vert=vert)

    def _end_search_creep(self, reason: str):
        """Hentikan fase merayap, kembali menyisir dinding dari fase 'look'."""
        log.debug("[FSM] M5_SEARCH berhenti merayap (%s) — lanjut ladder", reason)
        self._search_creep_t = None
        self._search_next_phase('look')

    def _heading_toward_wall(self, telem) -> int:
        """Yaw menuju heading dinding target (sama dgn NAV_WALL) — dipakai M5_REDIVE
        agar sapu cari QR terarah, bukan sapu buta satu arah.

        Urutan sumber arah, dari yang paling dipercaya:
          1. `_marked_heading` — heading TERUKUR saat operator menekan MARK di
             gantungan sungguhan. Menang atas WALL_HEADING karena tabel itu
             masih placeholder yang wajib dikalibrasi ulang tiap arena.
          2. WALL_HEADING[_target_wall] — hanya ada bila FSM sendiri yang
             menjalankan misi 1 (SCAN_QR). Pada alur misi 1-4 MANUAL,
             _target_wall SELALU None.
          3. Tak ada keduanya → sapu pelan; ini yang membuat run 22 Agu
             berputar buta sampai timeout.
        """
        heading = telem.get('heading', 0.0)
        if self._marked_heading is not None:
            target_hdg = self._marked_heading
        elif self._target_wall is not None:
            target_hdg = WALL_HEADING.get(self._target_wall, heading)
        else:
            return int(YAW_SPEED * 0.6)
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
        if self._time_left() < self._min_time_needed_from(State.M5_DOCK):
            log.warning("[FSM] Waktu heat tersisa %.0fs < kebutuhan minimum → "
                        "degradasi dini dari M5_DOCK", self._time_left())
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
        if self._time_left() < self._min_time_needed_from(State.M5_ENGAGE):
            log.warning("[FSM] Waktu heat tersisa %.0fs < kebutuhan minimum → "
                        "degradasi dini dari M5_ENGAGE", self._time_left())
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
        """Angkat berbasis depth sampai lubang bebas, lalu mundur dari hook."""
        elapsed = self._elapsed()
        if elapsed > TIMEOUT_UNHOOK:
            # Payload SUDAH digenggam — ABORT di sini menjamin skor 0. M5_ASCEND
            # memakai perintah naik yang sama (vert=+30), cuma dgn syarat selesai
            # yang berbeda, jadi tak ada bahaya baru: kalau lubang masih tersangkut
            # ROV mentok dan ASCEND timeout → kredit parsial, bukan nol.
            log.warning("[FSM] M5_UNHOOK timeout (angkat %.3f m belum cukup) — "
                        "lanjut naik, payload mungkin masih tersangkut", 
                        (self._unhook_start_depth - telem.get('depth', 0.0))
                        if self._unhook_start_depth is not None else 0.0)
            self.cmd.stop_all()
            self._transition(State.M5_ASCEND)
            return

        depth = telem.get('depth')
        if not isinstance(depth, (int, float)) or not math.isfinite(depth) or depth <= 0:
            log.error("[FSM] M5_UNHOOK depth invalid (%r) — ABORT", depth)
            self.abort()
            return
        if self._unhook_start_depth is None:
            self._unhook_start_depth = float(depth)

        lifted = self._unhook_start_depth - float(depth)
        if lifted < UNHOOK_LIFT_M:
            self.cmd.send(vert=M5_UNHOOK_VERT, gripper=1)
            log.debug("[FSM] M5_UNHOOK angkat %.3f/%.3f m", lifted, UNHOOK_LIFT_M)
            return

        if self._unhook_pull_t is None:
            self._unhook_pull_t = time.time()
        if time.time() - self._unhook_pull_t < UNHOOK_PULL_T:
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

    def _fresh_external_yolo(self, det=None):
        """Deteksi YOLO yang sudah divalidasi umur/skema oleh rov_agent."""
        if det is None:
            det = self._yolo_source()
        if not isinstance(det, dict) or det.get('method') != 'yolov8':
            return None
        if float(det.get('confidence', 0.0)) < LEFT_YOLO_CONF:
            return None
        return det

    @staticmethod
    def _yolo_source_failed(det):
        return isinstance(det, dict) and str(det.get('status', '')).endswith('_error')

    # Tahap visual tidak boleh berubah menjadi gerak timed/buta ketika YOLO atau
    # QR hilang. Fallback lama tetap tersedia untuk skenario legacy yang masuk
    # M5_FALLBACK secara eksplisit, tetapi alur langkah 3-8 selalu fail-safe.
    LEFT_DEGRADABLE = (State.M5_YOLO_SEARCH, State.M5_HOOK_ALIGN, State.M5_QR_DOCK)

    def _left_abort(self, reason):
        if self._bench_qr_dock:
            log.error("[FSM] BENCH QR ABORT — %s", reason)
            self.abort()
            return
        log.error("[FSM] alur M5 sisi kiri ABORT — %s", reason)
        self.abort()

    def _left_out_of_time(self) -> bool:
        """Abort dini bila sisa jam heat tak cukup menuntaskan rantai dengan aman."""
        if self._time_left() >= self._min_time_needed_from(self._state):
            return False
        self._left_abort("sisa jam heat %.0fs < kebutuhan minimum" % self._time_left())
        return True

    def _left_hold(self, telem) -> int:
        """Vert penahan kedalaman hook untuk SELURUH alur sisi kiri.

        Tanpa ini state 2-7 mengirim send() tanpa vert sama sekali selama
        puluhan detik — ROV sedikit apung, hook/QR hanyut keluar bidang pandang
        vertikal justru saat sedang dicari (bug yang sama dgn M5_SEARCH dulu)."""
        if self._left_depth is None:
            self._left_depth = (self._marked_depth if self._marked_depth is not None
                                else HOOK_DEPTH)
        return self._hold_depth(telem, self._left_depth)

    def _left_yaw_hold(self, telem) -> int:
        """Yaw penahan heading yang diserahkan CASE MOTION terakhir (langkah 2).

        Pakai ulang _search_yaw_to (bang-bang berbasis kompas absolut, dipakai
        M5_SEARCH) — tanpa ini heading hanyut selama ROV maju, dan dinding yang
        sudah susah payah dihadapkan CASE perlahan keluar dari bidang pandang."""
        if self._heading_hold is None:
            return 0
        yaw, _sampai = self._search_yaw_to(telem, self._heading_hold)
        return yaw

    def _left_visual_reset(self):
        self._left_visual_t = None
        self._left_visual_cmd.update(surge=0.0, sway=0.0, yaw=0.0, vert=0.0)

    def _left_visual_send(self, surge=0, sway=0, yaw=0, vert=0, gripper=None):
        """Kirim axis visual melalui cap + slew agar docking lambat dan mulus."""
        now = time.monotonic()
        if self._left_visual_t is None:
            dt = 0.1
        else:
            dt = max(0.02, min(0.25, now - self._left_visual_t))
        self._left_visual_t = now
        caps = {"surge": LEFT_VISUAL_MAX_SURGE, "sway": LEFT_VISUAL_MAX_SWAY,
                "yaw": LEFT_VISUAL_MAX_YAW, "vert": LEFT_VISUAL_MAX_VERT}
        requested = {"surge": surge, "sway": sway, "yaw": yaw, "vert": vert}
        step = LEFT_VISUAL_SLEW * dt
        output = {}
        for axis, cap in caps.items():
            target = max(-cap, min(cap, float(requested[axis] or 0.0)))
            previous = self._left_visual_cmd[axis]
            value = max(previous - step, min(previous + step, target))
            output[axis] = 0.0 if abs(value) < 0.05 else value
        self._left_visual_cmd.update(output)
        self.cmd.send(**output, gripper=gripper)

    @staticmethod
    def _hook_skeleton(det):
        """Validasi bagian hook yang diperlukan untuk membidik tip J.

        Model tetap mengirim 0..2 untuk batang, 3..4 untuk kepala, dan 5 untuk
        tip. Pada hook kolam, batang dapat berlanjut keluar frame sehingga 0/1
        sah menjadi low-confidence/clipped walaupun kepala dan tip terlihat
        jelas. Kontrol karena itu mewajibkan 2..5; 0/1 tetap diteruskan untuk
        overlay/diagnostik tetapi tidak boleh memblokir point 5 yang valid.
        """
        raw = det.get('keypoints')
        if not isinstance(raw, (list, tuple)):
            return None
        points = {}
        confidences = {}
        try:
            for item in raw:
                if not isinstance(item, dict):
                    return None
                index = int(item['id'])
                if index not in range(6) or index in points:
                    return None
                confidence = item.get('confidence')
                if confidence is None or not math.isfinite(float(confidence)):
                    return None
                x, y = float(item['x']), float(item['y'])
                frame_w, frame_h = float(det['frame_w']), float(det['frame_h'])
                if (not math.isfinite(x) or not math.isfinite(y)
                        or x < 0 or y < 0 or x > frame_w or y > frame_h):
                    return None
                points[index] = (x, y)
                confidences[index] = float(confidence)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if set(points) != set(range(6)):
            return None

        required = (2, 3, 4, 5)
        edge_margin = max(2.0, 0.005 * min(frame_w, frame_h))
        for index in required:
            x, y = points[index]
            if (confidences[index] < HOOK_KEYPOINT_CONF
                    or x < edge_margin or y < edge_margin
                    or x > frame_w - edge_margin or y > frame_h - edge_margin):
                return None

        # Tolak skeleton kolaps. Span 2→3 memvalidasi pangkal kepala, 3→4
        # lengkungan, dan 4→5 sambungan ke tip yang benar-benar dibidik.
        span_min = max(2.0, 0.003 * max(float(det['frame_w']), float(det['frame_h'])))
        spans = (math.dist(points[2], points[3]),
                 math.dist(points[3], points[4]),
                 math.dist(points[4], points[5]))
        return points if all(span >= span_min for span in spans) else None

    def _hook_tip(self, det):
        """Piksel point 5: ujung J tempat payload tergantung."""
        skeleton = self._hook_skeleton(det)
        return skeleton[5] if skeleton is not None else None

    def _state_m5_yolo_search(self, telem):
        """3. Maju sampai hasil YOLO laptop yang segar terdeteksi."""
        if self._elapsed() > LEFT_TIMEOUT_YOLO:
            self._left_abort("YOLO tidak mendeteksi hook")
            return
        if self._left_out_of_time():
            return
        raw = self._yolo_source()
        if self._yolo_source_failed(raw):
            self._left_abort("worker/kamera YOLO error")
            return
        if raw is None:
            self.cmd.stop_all()
            self._left_visual_reset()
            if self._elapsed() > LEFT_YOLO_SOURCE_GRACE:
                self._left_abort("stream YOLO tidak tersedia atau basi")
            return
        det = self._fresh_external_yolo(raw)
        if det is not None and self._hook_tip(det) is not None:
            # Voting spt M5_HOOK_ALIGN di bawah — SATU frame tak cukup. Model ini
            # sesekali menyatakan "Hook" pada frame tanpa hook (uji 40 frame CAM
            # WALL: 1 lolos gate di conf 0,41), dan latch palsu menghentikan ROV
            # jauh dari dinding. Berhenti maju selagi mengonfirmasi: kalau memang
            # hook, ~1 detik tak hilang; kalau hantu, ROV tak terlanjur berhenti.
            self._left_search_hits += 1
            self._left_visual_send(vert=self._left_hold(telem),
                                   yaw=self._left_yaw_hold(telem), gripper=0)
            if self._left_search_hits >= LEFT_YOLO_LOCK_FRAMES:
                self.cmd.stop_all()
                self._transition(State.M5_HOOK_ALIGN)
            return
        if det is not None:
            # Bbox yakin tetapi titik kontrol 2..5 belum valid: jangan terus
            # mendekat hanya karena classifier melihat hook. Tunggu pose pulih.
            self._left_search_hits = 0
            self.cmd.stop_all()
            self._left_visual_reset()
            return
        self._left_search_hits = max(0, self._left_search_hits - 1)
        # Budget jarak habis: berhenti maju (tanpa sensor jarak, terus merangsek =
        # menabrak dinding) lalu SAPU YAW. Galat sisa dari langkah 1-2 bersifat
        # SUDUT, bukan lateral, jadi memutar di tempat yang menemukan hook —
        # bukan ladder zigzag M5_SEARCH yang mengobati lenceng posisi. Sapuan
        # SENGAJA menang atas heading hold: mencari > menahan.
        if self._elapsed() > LEFT_ADVANCE_MAX_T:
            fase = int((self._elapsed() - LEFT_ADVANCE_MAX_T) / SCAN_SWEEP_T)
            # Budget maju adalah pagar keras: jangan biarkan ramp-down masih
            # membawa ROV mendekati dinding setelah waktunya habis.
            self._left_visual_cmd["surge"] = 0.0
            self._left_visual_send(yaw=YAW_SPEED if fase % 2 == 0 else -YAW_SPEED,
                                   vert=self._left_hold(telem), gripper=0)
            return
        self._left_visual_send(surge=SEARCH_SPEED, vert=self._left_hold(telem),
                               yaw=self._left_yaw_hold(telem), gripper=0)

    def _align_target(self, det):
        """Skalakan target jarak hook_servo ke resolusi frame YANG SEBENARNYA.

        hook_servo dibangun dgn HOOK_TARGET_AREA dalam px² yang dikalibrasi di
        640x480, sedangkan worker YOLO mengirim frame 1280x736 — luas piksel yang
        sama berarti JARAK yang berbeda. LEFT_YOLO_AREA_FRAC adalah fraksi, jadi
        bebas resolusi, dan itulah angka yang diukur preflight --hook-model."""
        if self._align_target_set:
            return
        self.hook_servo.target_area = (LEFT_YOLO_AREA_FRAC
                                       * det['frame_w'] * det['frame_h'])
        self._align_target_set = True
        log.info("[FSM] M5_HOOK_ALIGN target luas = %.0f px² (%.3f x %dx%d)",
                 self.hook_servo.target_area, LEFT_YOLO_AREA_FRAC,
                 det['frame_w'], det['frame_h'])

    def _state_m5_hook_align(self, telem):
        """4. Bidik kepala ujung hook "J" — tempat payload digantungkan.

        Servo memakai TITIK UJUNG J, bukan centroid bbox. Skeleton 2→3 (batang),
        3→4 (kepala), dan 4→5 (ujung J) wajib valid. Saat mendekat, error-X
        point 5 mengarahkan yaw; QR kemudian mengoreksi sudut docking akhir.
        hook_servo menggerbang surge sampai terpusat ("center dulu, baru maju")
        lalu menyatakan out.aligned setelah 3 sumbu stabil."""
        if self._elapsed() > LEFT_TIMEOUT_ALIGN:
            self._left_abort("ujung hook tidak tersejajarkan")
            return
        if self._left_out_of_time():
            return
        raw = self._yolo_source()
        if self._yolo_source_failed(raw):
            self._left_abort("worker/kamera YOLO error")
            return
        det = self._fresh_external_yolo(raw)
        if det is None:
            # Horizontal WAJIB nol — tanpa lock, maju/berputar = gerak buta.
            # Tapi vertikal TIDAK boleh ikut mati: stop_all() mematikan juga
            # depth-hold, sehingga ROV mengendap di kedalaman saat lock hilang.
            # Bila di kedalaman itu hook berada di luar tepi ATAS frame, YOLO
            # tak akan pernah melihatnya lagi dan state ini membeku sampai
            # timeout — terukur 85 detik diam pada uji 04:38 (depth mandek
            # 0,45-0,47 m padahal hook_depth 0,30 m). Menahan kedalaman hook
            # memakai pola yang sama dengan M5_YOLO_SEARCH membuat pandangan
            # pulih sendiri tanpa menambah satu pun gerak horizontal buta.
            self._left_visual_reset()
            self._left_visual_send(surge=0, sway=0, yaw=0,
                                   vert=self._left_hold(telem), gripper=0)
            return

        self._align_target(det)
        bx, by, bw, bh = det['bbox']
        tip = self._hook_tip(det)
        if tip is None:
            # Jangan kembali ke centroid bbox: itu dapat mengarahkan gripper ke
            # batang hook saat point 5 hilang atau confidence-nya lemah.
            # Sama seperti cabang det-None di atas: horizontal nol, kedalaman
            # tetap ditahan agar tip bisa kembali masuk bidang pandang.
            self._left_visual_reset()
            self._left_visual_send(surge=0, sway=0, yaw=0,
                                   vert=self._left_hold(telem), gripper=0)
            return
        # det disalin dangkal dgn center = point 5, supaya _hook_servo_step yang
        # sudah ada (deadband/slew/D-filter/approach gate/tally) dipakai apa adanya.
        area = float(bw * bh)
        area_frac = area / max(1.0, float(det['frame_w']) * float(det['frame_h']))
        # Tahap 3: saat hook masih jauh, jangan biarkan error Y point 5 yang
        # besar mengecilkan surge sampai ROV diam. Depth hook berasal dari
        # bridge/ALT_HOLD, bukan piksel Y. YOLO mengurus arah horizontal:
        # far = yaw hidung ke point 5, near = tambah sway untuk centering akhir.
        far_approach = area_frac < LEFT_YOLO_AREA_FRAC * 0.75
        center = (tip[0], det['frame_h'] / 2.0)
        aim = dict(det, center=center, area=area, pose=None)
        out, _mode = self._hook_servo_step(aim)
        hook_yaw = max(-LEFT_HOOK_MAX_YAW,
                       min(LEFT_HOOK_MAX_YAW, LEFT_HOOK_YAW_KP * out.ex))
        if far_approach:
            self._left_visual_send(surge=out.surge, sway=0, yaw=hook_yaw,
                                   vert=self._left_hold(telem), gripper=0)
            return
        if out.aligned:
            self.cmd.stop_all()
            self._transition(State.M5_QR_DOCK)
            return
        self._left_visual_send(surge=out.surge, sway=out.sway,
                               yaw=hook_yaw, vert=self._left_hold(telem), gripper=0)

    def _state_m5_qr_dock(self, telem):
        """5. Pusatkan QR memakai yaw+sway; jarak/depth dipertahankan."""
        # QR-direct dipakai saat payload sudah berada di area mulut gripper.
        # Jangan auto-abort/disarm hanya karena decode sempat hilang atau waktu
        # docking habis; tahan netral dan tunggu observasi fresh berikutnya.
        if not self._bench_qr_dock and self._elapsed() > TIMEOUT_M5_DOCK:
            self._left_abort("QR docking timeout")
            return
        if self._left_out_of_time():
            return
        det = self._fresh_payload(0.5)
        if det is None:
            since = time.time() - self._m5_last_det_t
            if since < M5_LOCK_GRACE_T:
                self.cmd.stop_all()
                self._left_visual_reset()
            elif self._bench_qr_dock:
                self.cmd.stop_all()
                self._left_visual_reset()
            else:
                self._left_visual_send(yaw=YAW_SPEED * self._m5_search_dir,
                                       vert=self._left_hold(telem), gripper=0)
            return

        self._note_detection(det)
        out, _mode = self._servo_step(det)
        pose = det.get('pose')
        if pose is not None:
            yaw_deg = normalize_plane_yaw(
                pose.get('yaw_deg', 0.0) - SERVO_TARGET_YAW_DEG)
            yaw = out.yaw if SERVO_KP_YAW else LEFT_QR_YAW_KP_DEG * yaw_deg
            square = abs(yaw_deg) <= LEFT_QR_YAW_TOL_DEG
        else:
            # Tanpa kalibrasi, kemiringan tak terukur sama sekali — ex satu-satunya
            # sinyal yang ada, jadi ambiguitas lama tetap melekat di mode IBVS.
            yaw = out.yaw if SERVO_KP_YAW else LEFT_QR_YAW_KP * out.ex
            square = True
        yaw = max(-LEFT_QR_MAX_YAW, min(LEFT_QR_MAX_YAW, yaw))
        # Keluaran servo dipakai UTUH, bukan cuma sway. Ia sudah menggerbang surge
        # sampai terpusat ("center dulu, baru maju" — _approach_gate), memegang
        # jarak ke target metrik saat PBVS, mengoreksi tinggi dari QR itu sendiri,
        # dan out.aligned sudah menghitung tally 3 sumbu (ex/ey/ea) berhisteresis.
        # Menulis ulang semua itu di sini dulu justru membalik urutannya: merapat
        # menyerong, lalu menggeser lateral di jarak paling sempit.
        surge, sway = out.surge, out.sway
        # Bench QR memberi seluruh 4-DOF ke visual servo. Di jalur lomba,
        # depth-hold lama tetap menjadi fallback saat error vertikal tepat nol.
        vert = out.vert if self._bench_qr_dock else out.vert or self._left_hold(telem)
        if self._bench_qr_dock:
            # Bench punya profil otoritas tersendiri dan sudah dihaluskan oleh
            # VisualServo. Limiter rendah ini hanya untuk alur misi di air.
            self.cmd.send(surge=surge, sway=sway, yaw=yaw, vert=vert, gripper=0)
        else:
            self._left_visual_send(surge=surge, sway=sway, yaw=yaw,
                                   vert=vert, gripper=0)
        if out.aligned and square:
            self.cmd.stop_all()
            self._transition(State.M5_GRIP)

    def _state_m5_grip(self, telem):
        """6. Tutup gripper, lalu serahkan langkah 7 (mundur) ke M5_UNHOOK —
        yang mengangkat lubang payload lepas dari hook DULU sebelum menarik."""
        self.cmd.send(vert=0 if self._bench_qr_dock else self._left_hold(telem), gripper=1)
        if self._elapsed() >= LEFT_GRIP_T:
            self.cmd.stop_all()
            if self._bench_qr_dock:
                self._transition(State.DONE)
            else:
                self._transition(State.M5_UNHOOK)

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
            # Jalur ini TAK PERNAH melihat payload — urutannya timed murni, jadi
            # ia tak tahu apakah gripper menjepit payload atau air. Mengklaim 40
            # membuat log berbohong (mis. worker YOLO mati → degradasi di detik 3
            # → "selesai +40" padahal ROV belum bergerak ke dinding). Kredit
            # parsial saja; angka sesungguhnya ditentukan juri.
            self._score['m5'] = 10
            log.warning("[FSM] Misi 5 selesai via FALLBACK timed — TANPA verifikasi visual, "
                        "payload belum tentu terambil (skor dicatat sbg kredit parsial)")
            self._transition(State.DONE)

    # ── Utility ────────────────────────────────────────────────────────────────

    def _transition(self, new_state: State):
        log.info("[FSM] %s → %s", self._state.name, new_state.name)
        if self.runlog:
            self.runlog.event('transition', frm=self._state.name, to=new_state.name,
                              lama_state_s=round(time.time() - self._state_t, 2))
        self._state   = new_state
        self._state_t = time.time()
        if new_state == State.SCAN_QR:
            self._scan_sweep_dir = 1
            self._scan_sweep_t = self._state_t
        # Mulai grace lock "segar" saat masuk fase docking (QR baru diakuisisi di REDIVE)
        if new_state in (State.M5_DOCK, State.M5_ENGAGE, State.M5_QR_DOCK):
            self._m5_last_det_t = self._state_t
        if new_state == State.M5_UNHOOK:
            self._unhook_start_depth = None
            self._unhook_pull_t = None
        if new_state == State.M5_YOLO_SEARCH:
            self._left_search_hits = 0
            self._left_visual_reset()
        elif new_state == State.M5_HOOK_ALIGN:
            self._left_visual_reset()
            self.hook_servo.reset()
            self.hook_pose_servo.reset()
        elif new_state == State.M5_QR_DOCK:
            self._left_visual_reset()
            self.servo.reset()
            self.pose_servo.reset()
        # Ladder pencarian selalu mulai dari nol tiap kali masuk M5_SEARCH
        if new_state == State.M5_SEARCH:
            self._search_phase   = 'backoff'
            self._search_phase_t = self._state_t
            self._search_leg_t   = SEARCH_LEG_T0
            self._search_dir     = self._m5_search_dir   # mulai ke sisi QR terakhir terlihat
            self._search_pos_t   = 0.0
            self._search_creep_t = None
            self._search_creep_block = False
            self.scan_creep_servo.reset()
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

    def _external_qr(self):
        """Hasil worker QR laptop; umur/skema sudah divalidasi rov_agent.

        Umur SENGAJA tidak dicek di sini: `timestamp` pada hasil ini berasal dari
        jam laptop yang tidak tersinkron dengan Pi, jadi membandingkannya dengan
        time.time() lokal akan salah. Kesegaran dijaga rov_agent memakai waktu
        terima — pola yang sama dengan hook_vision.
        """
        det = self._qr_source()
        return det if isinstance(det, dict) and det.get('method') == 'yolo_qr' else None

    def _fresh_payload(self, max_age=0.5):
        """latest_qr yang TERVALIDASI sebagai payload target (else None) — dipakai
        akuisisi & servo misi 5 agar tak mengunci QR/objek yang salah.

        Worker QR laptop (best_new.pt: bbox region -> crop -> decode) diutamakan.
        Bila kosong, JATUH ke decode lokal Pi persis seperti sebelumnya — link
        laptop putus tidak boleh mematikan QR docking.
        """
        det = self._external_qr()
        if det is None:
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

    def _time_left(self) -> float:
        """Detik tersisa dari TIME_BUDGET_TOTAL sejak start(). Belum start()
        (mission_t0 None, mis. dipanggil dari test tanpa start()) dianggap
        'banyak waktu' — kembalikan budget penuh, bukan 0 (0 akan memicu
        degradasi dini yang keliru sebelum misi benar-benar berjalan)."""
        if self._mission_t0 is None:
            return TIME_BUDGET_TOTAL
        return TIME_BUDGET_TOTAL - (time.time() - self._mission_t0)

    def _min_time_needed_from(self, state: State) -> float:
        """Waktu MINIMUM yang masih dibutuhkan untuk menuntaskan sisa rantai
        misi 5 dari `state` lewat fallback tercepat (bukan jalur visual penuh)
        — dipakai sbg ambang degradasi dini di _time_left()."""
        chain = {
            State.M5_SEARCH: TIMEOUT_M5_DOCK + TIMEOUT_M5_ENGAGE + TIMEOUT_UNHOOK + TIMEOUT_M5_ASCEND,
            State.M5_DOCK:   TIMEOUT_M5_ENGAGE + TIMEOUT_UNHOOK + TIMEOUT_M5_ASCEND,
            State.M5_ENGAGE: TIMEOUT_UNHOOK + TIMEOUT_M5_ASCEND,
            # Alur sisi kiri — sisa rantainya berakhir di M5_UNHOOK yang sama.
            State.M5_YOLO_SEARCH: (LEFT_TIMEOUT_ALIGN + TIMEOUT_M5_DOCK + LEFT_GRIP_T
                                   + TIMEOUT_UNHOOK + TIMEOUT_M5_ASCEND),
            State.M5_HOOK_ALIGN:  (TIMEOUT_M5_DOCK + LEFT_GRIP_T
                                   + TIMEOUT_UNHOOK + TIMEOUT_M5_ASCEND),
            State.M5_QR_DOCK:     LEFT_GRIP_T + TIMEOUT_UNHOOK + TIMEOUT_M5_ASCEND,
        }
        return chain.get(state, 0.0)

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
    ap.add_argument('--hook-model', default=None, metavar='BEST.PT',
                    help='opsional bobot YOLOv8 Hook di laptop, mis. autonomy/vision/best.pt; '
                         'menggantikan detector OpenCV dan memberi bbox/offset X-Y relatif')
    ap.add_argument('--hook-map', default=None, metavar='FILE',
                    help='AKTIFKAN lokalisasi hook (OPSIONAL, default MATI): path map arena '
                         '.yaml/.yml/.json — lihat config/hook_map.example.yaml. Hasilnya cuma '
                         'diterbitkan ke telemetri/run-log; TIDAK ada state yang memakainya '
                         'untuk mengambil keputusan gerak, dan jalur M5 QR tak berubah.')
    ap.add_argument('--no-wall-cnn', action='store_true',
                    help='matikan fallback wall-CNN saat decode_qr() gagal (default: AKTIF)')
    ap.add_argument('--wall-cnn-votes', type=int, default=3,
                    help='jumlah frame berturut-turut sepakat sebelum wall-CNN dipercaya')
    ap.add_argument('--wall-cnn-min-conf', type=float, default=0.8,
                    help='confidence minimum agar tebakan wall-CNN dihitung')
    ap.add_argument('--start-state', default='DIVE',
                    choices=['DIVE', 'M5_REDIVE', 'M5_DOCK', 'M5_YOLO_SEARCH'],
                    help='DIVE=full misi 1-5; M5_REDIVE=misi 5 autonomous (1-4 manual via GUI); '
                         'M5_DOCK=uji docking QR saja; M5_YOLO_SEARCH=alur lomba langkah 3-8 '
                         '(langkah 1-2 dijalankan CASE MOTION di bridge, tak ada di sini). '
                         'Dry-run WAJIB --hook-model kecuali --vision mock')
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
                           hook_model=args.hook_model,
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

    # Sumber deteksi YOLO alur sisi kiri, jalur STANDALONE = pipeline lokal
    # (--hook-model). Field telemetri 'hook_vision' SENGAJA tidak dibaca di sini:
    # yang mengisinya cuma rov_agent._fsm_read_state (YOLO jalan di laptop), dan
    # jalur itu lewat rov_mission5_bridge yang punya yolo_source-nya sendiri —
    # rov_link.py tak pernah mengirim field itu. Skema keduanya identik
    # (method/bbox/frame_w/confidence) dan sama-sama sudah disaring umur.
    def _yolo_source():
        det = cam.latest_hook(max_age=1.0)
        if det is not None and args.vision == 'mock':
            # Mock meniru worker laptop supaya alur kiri bisa diuji di SITL. Relabel
            # ditaruh DI SINI, bukan dgn melonggarkan gate method=='yolov8' di FSM:
            # gate itu yang menjaga hasil OpenCV/mock tak menyetir ROV sungguhan.
            det = dict(det, method='yolov8')
        return det

    fsm = Mission5FSM(cmd=cmd, telem=telem, vision=cam, runlog=runlog,
                      yolo_source=_yolo_source,
                      hook_map_file=args.hook_map,
                      hook_calib_file=args.calib_wall if args.hook_map else None)
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
