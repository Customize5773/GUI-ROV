"""
vision/aruco_qr.py — KKI 2026 ROV Vision Pipeline
===================================================
Deteksi QR Code dan ArUco marker dari kamera.

Mendukung 3 sumber kamera:
  - 'mock'  : simulasi tanpa kamera fisik (untuk testing)
  - 'usb'   : USB webcam langsung di laptop (cv2.VideoCapture(index))
  - 'rtsp'  : stream dari Raspberry Pi / Jetson via RTSP/HTTP

Output:
  - Callback on_detection(result: dict) dipanggil tiap ada deteksi
  - result = {
      'type': 'qr' | 'aruco',
      'data': str,           # isi QR atau ID ArUco
      'wall': 'A'|'B'|'C'|'D' | None,  # sisi kolam dari QR
      'center': (x, y),     # pusat marker di frame
      'area': float,         # area bounding box (proxy jarak)
      'frame': ndarray,      # frame dengan anotasi
      'timestamp': float,
    }

Instalasi dependensi:
  pip install opencv-python pyzbar
  apt install libzbar0        # untuk pyzbar
"""

import time
import threading
import logging
from typing import Callable, Optional
import numpy as np

log = logging.getLogger(__name__)

# ── Coba import cv2 dan pyzbar ────────────────────────────────────────────────
try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False
    log.warning("[vision] opencv-python tidak tersedia — hanya mode mock aktif")

try:
    from pyzbar import pyzbar
    PYZBAR_OK = True
except ImportError:
    PYZBAR_OK = False
    log.warning("[vision] pyzbar tidak tersedia — QR detection dinonaktifkan")

# ── Mapping QR data → sisi kolam ──────────────────────────────────────────────
# QR code di payload berisi teks seperti 'A', 'B', 'C', atau 'D'
# sesuai panduan KKI 2026 halaman 52
WALL_MAP = {'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D'}


class VisionPipeline:
    """
    Pipeline deteksi QR + ArUco untuk misi ROV KKI 2026.

    Contoh penggunaan:
        def on_det(result):
            print(result['type'], result['data'], result['wall'])

        cam = VisionPipeline(source='usb', device=0, callback=on_det)
        cam.start()
        # ... jalankan misi ...
        cam.stop()
    """

    def __init__(
        self,
        source: str = 'mock',
        device=0,
        rtsp_url: str = 'rtsp://hydroship:8554/cam',
        callback: Optional[Callable] = None,
        fps: int = 10,
        aruco_dict: str = 'DICT_4X4_50',
    ):
        """
        Parameters
        ----------
        source      : 'mock' | 'usb' | 'rtsp'
        device      : index USB webcam (default 0)
        rtsp_url    : URL RTSP/HTTP jika source='rtsp'
        callback    : fungsi dipanggil tiap deteksi
        fps         : target frame-rate capture
        aruco_dict  : nama kamus ArUco (cv2.aruco.DICT_*)
        """
        self.source = source
        self.device = device
        self.rtsp_url = rtsp_url
        self.callback = callback
        self.fps = fps
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cap = None
        self._last_result: Optional[dict] = None
        self._last_aruco: Optional[dict] = None   # deteksi ArUco terakhir (utk visual servo)

        # Setup ArUco detector
        self._aruco_detector = None
        if CV2_OK and hasattr(cv2, 'aruco'):
            try:
                dict_id = getattr(cv2.aruco, aruco_dict)
                aruco_d = cv2.aruco.getPredefinedDictionary(dict_id)
                params = cv2.aruco.DetectorParameters()
                self._aruco_detector = cv2.aruco.ArucoDetector(aruco_d, params)
                log.info("[vision] ArUco detector siap: %s", aruco_dict)
            except Exception as e:
                log.warning("[vision] ArUco init gagal: %s", e)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Mulai thread capture di background."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name='VisionThread')
        self._thread.start()
        log.info("[vision] Started (source=%s)", self.source)

    def stop(self):
        """Hentikan thread capture."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._cap and CV2_OK:
            self._cap.release()
        log.info("[vision] Stopped")

    def last_result(self) -> Optional[dict]:
        """Kembalikan hasil deteksi terakhir."""
        return self._last_result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self):
        if self.source == 'mock':
            self._run_mock()
        elif CV2_OK:
            self._run_camera()
        else:
            log.error("[vision] opencv tidak tersedia dan source bukan mock")

    def _run_mock(self):
        """Simulasi deteksi: kirim QR 'A' setelah 2 detik, lalu 'B', dst."""
        log.info("[vision] Mock mode aktif — simulasi deteksi QR")
        sequence = [
            (2.0,  'qr',    'A', (320, 240)),
            (8.0,  'qr',    'C', (300, 260)),
            (15.0, 'aruco', '7', (310, 250)),
        ]
        t0 = time.time()
        idx = 0
        while self._running:
            now = time.time() - t0
            if idx < len(sequence):
                delay, det_type, data, center = sequence[idx]
                if now >= delay:
                    frame = self._mock_frame(det_type, data, center)
                    result = self._build_result(det_type, data, center, 2500.0, frame)
                    self._dispatch(result)
                    idx += 1
            time.sleep(1.0 / self.fps)

    def _run_camera(self):
        """Capture loop nyata (USB / RTSP)."""
        src = self.device if self.source == 'usb' else self.rtsp_url
        self._cap = cv2.VideoCapture(src)
        if not self._cap.isOpened():
            log.error("[vision] Tidak bisa membuka sumber kamera: %s", src)
            return

        interval = 1.0 / self.fps
        log.info("[vision] Kamera terbuka: %s", src)

        while self._running:
            t_start = time.time()
            ret, frame = self._cap.read()
            if not ret:
                log.warning("[vision] Frame gagal dibaca, retry...")
                time.sleep(0.5)
                continue

            # Deteksi QR code
            if PYZBAR_OK:
                for obj in pyzbar.decode(frame):
                    data = obj.data.decode('utf-8').strip().upper()
                    pts = np.array([[p.x, p.y] for p in obj.polygon])
                    center = (int(pts[:, 0].mean()), int(pts[:, 1].mean()))
                    area = float(cv2.contourArea(pts.reshape(-1, 1, 2)))
                    frame = self._annotate(frame, 'qr', data, center, pts)
                    result = self._build_result('qr', data, center, area, frame)
                    self._dispatch(result)

            # Deteksi ArUco
            if self._aruco_detector is not None:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                corners, ids, _ = self._aruco_detector.detectMarkers(gray)
                if ids is not None:
                    for corner, aid in zip(corners, ids.flatten()):
                        pts = corner.reshape(4, 2).astype(int)
                        center = (int(pts[:, 0].mean()), int(pts[:, 1].mean()))
                        area = float(cv2.contourArea(pts.reshape(-1, 1, 2)))
                        data = str(aid)
                        frame = self._annotate(frame, 'aruco', data, center, pts)
                        result = self._build_result('aruco', data, center, area, frame)
                        self._dispatch(result)

            elapsed = time.time() - t_start
            sleep_t = max(0, interval - elapsed)
            time.sleep(sleep_t)

    # ── Helper ────────────────────────────────────────────────────────────────

    def _build_result(self, det_type, data, center, area, frame) -> dict:
        wall = WALL_MAP.get(data) if det_type == 'qr' else None
        h, w = (frame.shape[0], frame.shape[1]) if frame is not None else (480, 640)
        result = {
            'type': det_type,
            'data': data,
            'wall': wall,
            'center': center,
            'area': area,
            'frame': frame,
            'frame_w': w,
            'frame_h': h,
            'timestamp': time.time(),
        }
        self._last_result = result
        if det_type == 'aruco':
            self._last_aruco = result
        return result

    def latest_aruco(self, max_age=0.5, marker_id=None) -> Optional[dict]:
        """Deteksi ArUco terakhir bila masih segar (utk closed-loop servo)."""
        r = self._last_aruco
        if not r or (time.time() - r['timestamp']) > max_age:
            return None
        if marker_id is not None and str(r['data']) != str(marker_id):
            return None
        return r

    def _dispatch(self, result: dict):
        log.info("[vision] Deteksi %s data=%s wall=%s center=%s",
                 result['type'], result['data'], result['wall'], result['center'])
        if self.callback:
            try:
                self.callback(result)
            except Exception as e:
                log.error("[vision] Callback error: %s", e)

    def _annotate(self, frame, det_type, data, center, pts):
        if not CV2_OK:
            return frame
        color = (0, 255, 0) if det_type == 'qr' else (255, 100, 0)
        cv2.polylines(frame, [pts], True, color, 2)
        cv2.putText(frame, f"{det_type.upper()}:{data}", center,
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        return frame

    def _mock_frame(self, det_type, data, center):
        """Buat frame dummy 640x480 untuk mock mode."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        if CV2_OK:
            color = (0, 255, 0) if det_type == 'qr' else (255, 100, 0)
            cv2.rectangle(frame,
                          (center[0]-30, center[1]-30),
                          (center[0]+30, center[1]+30),
                          color, 2)
            cv2.putText(frame, f"MOCK {det_type.upper()}:{data}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        return frame


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    ap = argparse.ArgumentParser(description='Vision pipeline test')
    ap.add_argument('--source', default='mock', choices=['mock', 'usb', 'rtsp'])
    ap.add_argument('--device', type=int, default=0)
    ap.add_argument('--rtsp', default='rtsp://192.168.1.10:8554/cam')
    args = ap.parse_args()

    detections = []

    def on_det(r):
        detections.append(r)
        print(f"  → {r['type']} | data={r['data']} | wall={r['wall']} | area={r['area']:.0f}")

    cam = VisionPipeline(source=args.source, device=args.device,
                         rtsp_url=args.rtsp, callback=on_det)
    cam.start()
    print(f"[test] Pipeline jalan (source={args.source}). Ctrl+C untuk berhenti.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    cam.stop()
    print(f"[test] Total deteksi: {len(detections)}")
