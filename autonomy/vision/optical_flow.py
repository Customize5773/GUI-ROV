"""
vision/optical_flow.py — Kamera BOTTOM sebagai sensor drift (sparse optical flow).

Kenapa modul terpisah, bukan menumpang VisionPipeline (qr_detect.py)
    VisionPipeline dimiliki & di-start/stop oleh Mission5Runner, mengikuti
    toggle Autonomous di GUI. Drift-sensing ini justru harus HIDUP TERUS
    selama agent hidup, terlepas dari mission5 — pilot butuh tahu dia hanyut
    saat terbang MANUAL, bukan cuma saat misi otonom jalan. Menumpang siklus
    hidup VisionPipeline berarti drift mati tiap kali operator kembali ke
    manual, kebalikan dari yang diminta.

    Kedua modul BOLEH membaca stream MJPEG BOTTOM yang sama secara
    bersamaan — mjpg-streamer sudah fan-out ke banyak klien (lihat
    server/server.js), jadi tidak ada rebutan device seperti dua proses
    membuka /dev/video0 sekaligus.

Kenapa sparse (goodFeaturesToTrack + calcOpticalFlowPyrLK), bukan Farneback
    Farneback (dense flow) menghitung vektor per piksel — jauh lebih berat
    untuk Pi 4 yang juga menjalankan MAVLink (dan kadang QR di kamera lain).
    Sparse LK cukup untuk sekadar mengukur median pergeseran adegan.
"""

import time
import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    CV2_OK = True
except ImportError:
    CV2_OK = False
    log.warning("[flow] opencv-python/numpy tidak tersedia — drift sensing nonaktif")

# Parameter goodFeaturesToTrack / calcOpticalFlowPyrLK — nilai umum OpenCV
# untuk sparse LK, belum ditala khusus untuk tekstur dasar kolam ini.
# maxCorners diturunkan dari 200 (23 Agu 2026): thread ini sendirian memakai
# ~85% CPU di Pi 4 dan mengganggu loop kontrol utama (ALT_HOLD naik-turun
# sendiri saat trial) — lihat juga DOWNSCALE di bawah & DRIFT_FPS di
# rov_agent.py, dua-duanya diturunkan bersamaan untuk alasan yang sama.
_FEATURE_PARAMS = dict(maxCorners=100, qualityLevel=0.3, minDistance=7, blockSize=7)

# Skala downsize sebelum goodFeaturesToTrack/calcOpticalFlowPyrLK — biaya
# sparse LK naik kira-kira sebanding luas piksel, jadi 0.5 (setengah
# panjang/lebar) ~4x lebih murah dari resolusi asli. dx/dy hasil DIBAGI
# balik dengan skala ini sebelum dikembalikan supaya tetap dalam satuan
# piksel FRAME ASLI (flow_to_velocity di rov_drift.py pakai focal_px dari
# kalibrasi resolusi asli).
DOWNSCALE = 0.5
_LK_PARAMS = dict(
    winSize=(15, 15), maxLevel=2,
    criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03),
) if CV2_OK else {}

# Re-seed fitur kalau jumlah yang berhasil dilacak turun di bawah ini — tanpa
# ini flow makin tak stabil seiring fitur hilang satu per satu antar-frame.
MIN_TRACKED_FEATURES = 30


class FlowTracker:
    """Kamera BOTTOM -> pixel flow antar-frame berurutan. Siklus hidup MANDIRI
    (tidak menumpang VisionPipeline) — lihat alasan di docstring modul.

    Contoh:
        ft = FlowTracker(url="http://127.0.0.1:8081/stream")
        ft.start()
        ...
        flow = ft.latest_flow()   # {'dx','dy','dt','quality','timestamp'} | None
    """

    def __init__(self, url: Optional[str] = None, device=None, fps: int = 10):
        """
        url    : stream MJPEG (mis. http://127.0.0.1:8081/stream). Dipakai
                 kalau diisi — sama seperti qr_url di VisionPipeline.
        device : index/path USB langsung, dipakai kalau `url` None.
        fps    : target frame-rate capture.
        """
        if url is None and device is None:
            device = 0
        self._src = url if url is not None else device
        self.fps = fps
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cap = None
        self._prev_gray = None
        self._prev_pts = None
        self._prev_ts = None
        self._last_flow: Optional[dict] = None
        self._lock = threading.Lock()

    def start(self):
        """Mulai thread capture di background. Tidak melakukan apa pun kalau
        cv2 tak ada — pemanggil (rov_agent.py) tetap jalan penuh untuk kontrol
        manual, cuma drift sensing yang nonaktif."""
        if self._running:
            return
        if not CV2_OK:
            log.warning("[flow] cv2 tidak ada — FlowTracker tidak start")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="FlowTrackerThread")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._cap and CV2_OK:
            self._cap.release()

    def latest_flow(self, max_age: float = 0.5) -> Optional[dict]:
        """Flow pixel terakhir bila masih segar (hindari transisi dari data
        basi — pola sama dengan latest_qr()/latest_hook() di qr_detect.py).

        Return: {'dx','dy'} median perpindahan titik terlacak antar-frame
        dalam PIKSEL KAMERA (bukan dunia nyata — konversi ke m/s ada di
        rov_drift.flow_to_velocity()), 'dt' selang waktu antar-frame (detik),
        'quality' jumlah titik yang berhasil dilacak, 'timestamp'.
        """
        with self._lock:
            r = self._last_flow
        if not r or (time.time() - r["timestamp"]) > max_age:
            return None
        return r

    def _run(self):
        self._cap = cv2.VideoCapture(self._src)
        if not self._cap.isOpened():
            log.error("[flow] Tidak bisa membuka sumber kamera: %s", self._src)
            return
        log.info("[flow] Kamera terbuka: %s", self._src)
        interval = 1.0 / self.fps

        while self._running:
            t_start = time.time()
            ret, frame = self._cap.read()
            if not ret:
                log.warning("[flow] Frame gagal dibaca, retry...")
                time.sleep(0.5)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if DOWNSCALE != 1.0:
                gray = cv2.resize(gray, None, fx=DOWNSCALE, fy=DOWNSCALE,
                                   interpolation=cv2.INTER_AREA)
            self._process(gray, t_start)

            elapsed = time.time() - t_start
            time.sleep(max(0, interval - elapsed))

    def _process(self, gray, ts):
        """Lacak fitur dari frame sebelumnya ke frame ini, tulis _last_flow."""
        if (self._prev_gray is None or self._prev_pts is None
                or len(self._prev_pts) < MIN_TRACKED_FEATURES):
            self._prev_gray = gray
            self._prev_pts = cv2.goodFeaturesToTrack(gray, mask=None, **_FEATURE_PARAMS)
            self._prev_ts = ts
            return

        next_pts, status, _err = cv2.calcOpticalFlowPyrLK(
            self._prev_gray, gray, self._prev_pts, None, **_LK_PARAMS)
        dt = ts - self._prev_ts
        quality = 0

        if next_pts is not None and status is not None:
            mask = status.flatten() == 1
            good_new = next_pts[mask]
            good_old = self._prev_pts[mask]
            quality = len(good_new)
            if quality > 0 and dt > 0:
                disp = (good_new - good_old).reshape(-1, 2)
                # Balik ke satuan piksel FRAME ASLI (lihat DOWNSCALE) — kalau
                # tidak, flow_to_velocity di rov_drift.py memakai focal_px
                # kalibrasi resolusi asli terhadap dx/dy yang sudah diperkecil,
                # dan kecepatan yang dihitung jadi terlalu kecil.
                dx = float(np.median(disp[:, 0])) / DOWNSCALE
                dy = float(np.median(disp[:, 1])) / DOWNSCALE
                with self._lock:
                    self._last_flow = {
                        "dx": dx, "dy": dy, "dt": dt,
                        "quality": quality, "timestamp": ts,
                    }

        self._prev_gray = gray
        self._prev_ts = ts
        if quality < MIN_TRACKED_FEATURES:
            self._prev_pts = cv2.goodFeaturesToTrack(gray, mask=None, **_FEATURE_PARAMS)
        else:
            self._prev_pts = good_new.reshape(-1, 1, 2)
