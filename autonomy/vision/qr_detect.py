"""
vision/qr_detect.py — KKI 2026 ROV QR Vision Pipeline
=====================================================
Deteksi **QR Code** payload dari kamera (fokus tunggal — deteksi ArUco sudah dihapus).
Target visual misi = QR payload (4×4 cm) yang dicetak tim, BUKAN marker ArUco.

Mendukung 3 sumber kamera:
  - 'mock'  : simulasi tanpa kamera fisik (untuk testing)
  - 'usb'   : USB webcam langsung di laptop (cv2.VideoCapture(index))
  - 'rtsp'  : stream dari Raspberry Pi / Jetson via RTSP/HTTP

Output:
  - Callback on_detection(result: dict) dipanggil tiap ada deteksi QR
  - result = {
      'type': 'qr',
      'data': str,           # isi QR mentah (string)
      'payload': dict|None,  # isi QR JSON terparse {mission,team,type,id} bila JSON
      'wall': 'A'|'B'|'C'|'D' | None,  # sisi kolam dari QR (field id / huruf sisi)
      'center': (x, y),     # pusat QR di frame
      'area': float,         # area bounding box (proxy jarak)
      'frame': ndarray,      # frame dengan anotasi
      'frame_w', 'frame_h': int,
      'pose': {x,y,z,dist,yaw_deg} | None,  # PBVS solvePnP bila kamera terkalibrasi
      'timestamp': float,
    }

Instalasi dependensi:
  pip install opencv-python pyzbar
  apt install libzbar0        # untuk pyzbar
"""

import time
import math
import re
import json
import threading
import logging
from typing import Callable, Optional
import numpy as np

log = logging.getLogger(__name__)

# QR payload KKI 2026 = JSON terstruktur, mis:
#   {"mission":5,"team":"HYDROSHIP","type":"payload","id":"A"}   (A/B/C/D per sisi)
# Sisi dinding diambil dari field "id". Untuk kompatibilitas mundur, QR string biasa
# ("A", "SIDE_B", "WALL-C") tetap didukung via regex huruf sisi terisolasi.
_WALL_RE = re.compile(r'(?:^|[^A-Z])([ABCD])(?![A-Z])')


def parse_payload(data) -> Optional[dict]:
    """Parse isi QR JSON terstruktur → dict, atau None bila bukan JSON object.
    Memudahkan sistem lain: field `id`/`mission`/`team`/`type` langsung tersedia
    tanpa regex (mis. validasi payload['mission']==5 sebelum GRAB)."""
    try:
        obj = json.loads(str(data))
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def wall_from_qr(data) -> Optional[str]:
    """Petakan isi QR → sisi kolam 'A'|'B'|'C'|'D', atau None bila tak ada.

    Prioritas: QR JSON terstruktur (field "id"); fallback ke huruf sisi terisolasi
    pada string biasa (kompatibilitas mundur)."""
    payload = parse_payload(data)
    if payload is not None:
        pid = payload.get('id')
        if isinstance(pid, str):
            pid = pid.strip().upper()
            if pid in ('A', 'B', 'C', 'D'):
                return pid
    m = _WALL_RE.search(str(data).upper())
    return m.group(1) if m else None

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

# Mapping QR → sisi kolam A/B/C/D dilakukan oleh wall_from_qr() di atas
# (isi QR sesuai panduan KKI 2026 hal. 52; toleran terhadap prefiks/sufiks).


# ── Decode QR robust (preprocessing berjenjang) ───────────────────────────────
# Isu lapangan (Fase 0): QR terdeteksi hanya jarak dekat / sensitif cahaya. Sebab:
# frame mentah dikirim langsung ke pyzbar tanpa perbaikan kontras/skala. decode_qr()
# mengeskalasi preprocessing HANYA bila langkah sebelumnya gagal → jalur cepat tetap
# cepat saat QR jelas, tapi QR jauh/kontras-rendah masih terbaca.
CLAHE_CLIP   = 2.0     # kekuatan penyetaraan kontras lokal (CLAHE) — lawan glare/cahaya tak rata
CLAHE_TILE   = 8       # ukuran grid CLAHE (tile CLAHE_TILE×CLAHE_TILE)
UPSCALE      = 2.0     # faktor perbesaran saat QR terlalu kecil utk pyzbar


def _to_gray_clahe(frame):
    """BGR/gray → grayscale dgn CLAHE (kontras lokal). Bantu cahaya tak merata & glare."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=(CLAHE_TILE, CLAHE_TILE))
    return clahe.apply(gray)


def _pyzbar_qr(img, scale=1.0):
    """Decode QR dgn pyzbar; kembalikan list {data, pts} dgn pts di koordinat frame ASLI
    (dibagi `scale` bila img sudah diperbesar). Hanya simbol tipe QRCODE."""
    out = []
    for obj in pyzbar.decode(img):
        if getattr(obj, 'type', 'QRCODE') != 'QRCODE':
            continue
        # JANGAN .upper() — isi QR payload = JSON (key/nilai case-sensitive).
        data = obj.data.decode('utf-8', 'ignore').strip()
        pts = np.array([[p.x / scale, p.y / scale] for p in obj.polygon], dtype=np.float32)
        out.append({'data': data, 'pts': pts})
    return out


def decode_qr(frame, enhance=True):
    """Deteksi QR robust dari 1 frame. Kembalikan list {'data': str,
    'pts': ndarray(N,2) koordinat frame ASLI}.

    Jenjang (berhenti di jenjang pertama yang berhasil):
      1. pyzbar pada frame mentah (cepat, kasus QR jelas).
      2. pyzbar pada grayscale + CLAHE (cahaya tak rata / glare).
      3. pyzbar pada grayscale+CLAHE yang di-upscale UPSCALE× (QR kecil/jauh).
      4. cv2.QRCodeDetector.detectAndDecodeMulti pada grayscale (fallback detektor beda).
    enhance=False → hanya jenjang 1 (perilaku lama; utk benchmark/uji A-B)."""
    if not CV2_OK:
        return []
    if PYZBAR_OK:
        res = _pyzbar_qr(frame, 1.0)
        if res or not enhance:
            return res
        gray_clahe = _to_gray_clahe(frame)
        res = _pyzbar_qr(gray_clahe, 1.0)
        if res:
            return res
        big = cv2.resize(gray_clahe, None, fx=UPSCALE, fy=UPSCALE,
                         interpolation=cv2.INTER_CUBIC)
        res = _pyzbar_qr(big, UPSCALE)
        if res:
            return res
    # Fallback: detektor QR bawaan OpenCV (kadang berhasil di mana pyzbar gagal, & sebaliknya)
    if enhance and hasattr(cv2, 'QRCodeDetector'):
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
            ok, decoded, points, _ = cv2.QRCodeDetector().detectAndDecodeMulti(gray)
            if ok and points is not None:
                out = []
                for data, quad in zip(decoded, points):
                    if data:
                        out.append({'data': str(data).strip(),
                                    'pts': np.asarray(quad, dtype=np.float32).reshape(-1, 2)})
                if out:
                    return out
        except cv2.error:
            pass
    return []


class VisionPipeline:
    """
    Pipeline deteksi QR payload untuk misi ROV KKI 2026.

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
        calib_file: Optional[str] = None,
        qr_length: float = 0.04,
    ):
        """
        Parameters
        ----------
        source     : 'mock' | 'usb' | 'rtsp'
        device     : index USB webcam (default 0)
        rtsp_url   : URL RTSP/HTTP jika source='rtsp'
        callback   : fungsi dipanggil tiap deteksi QR
        fps        : target frame-rate capture
        calib_file : .npz kalibrasi kamera → aktifkan PBVS (solvePnP). None → IBVS.
        qr_length  : sisi fisik QR payload (m) utk solvePnP — KKI 2026 = 0.04 (4×4 cm)
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
        self._last_qr: Optional[dict] = None      # deteksi QR terakhir (scan wall + docking)

        # Kalibrasi kamera utk PBVS (solvePnP). Bila tak ada → pose=None (fallback IBVS).
        self.qr_length = qr_length
        self._K = None
        self._dist = None
        if calib_file and CV2_OK:
            try:
                data = np.load(calib_file)
                self._K, self._dist = data['K'], data['dist']
                log.info("[vision] Kalibrasi dimuat: %s — PBVS (solvePnP) AKTIF", calib_file)
            except Exception as e:
                log.warning("[vision] gagal muat kalibrasi %s: %s — fallback IBVS", calib_file, e)

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

    # Parameter mock docking (mensimulasikan QR payload yang makin center & dekat)
    # data QR payload = JSON terstruktur (wall C) — sama format cetak PDF tim
    MOCK_QR_DATA        = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"C"}'
    MOCK_FAR_SEC        = 3.0                 # detik "jauh & off-center" sebelum konvergen
    MOCK_CONVERGE_SEC   = 3.0                 # durasi ramp jauh → aligned
    MOCK_TARGET_AREA    = 3000.0             # px² saat engage (samakan dgn SERVO_TARGET_AREA FSM)
    MOCK_TARGET_DIST    = 0.30              # m saat engage (samakan dgn SERVO_TARGET_DIST FSM)

    def _run_mock(self):
        """Simulasi deteksi payload QR untuk uji closed-loop misi 5 tanpa kamera.

        QR dipancarkan terus-menerus (dipakai SCAN_QR di misi 1 & docking di misi 5).
        Error (center & pose) meluruh dari 'jauh/off-center' → 'aligned' dalam
        MOCK_FAR_SEC + MOCK_CONVERGE_SEC detik, sehingga PoseServo/VisualServo mencapai
        aligned dan FSM berjalan sampai selesai. Pose hanya dipancarkan bila kalibrasi
        dimuat (self._K) — meniru perilaku kamera nyata (kalib → PBVS, tanpa → IBVS)."""
        log.info("[vision] Mock mode aktif — simulasi payload QR (%s)", self.MOCK_QR_DATA)
        t0 = time.time()
        while self._running:
            t = time.time() - t0
            if t < self.MOCK_FAR_SEC:
                err = 1.0
            elif t < self.MOCK_FAR_SEC + self.MOCK_CONVERGE_SEC:
                err = 1.0 - (t - self.MOCK_FAR_SEC) / self.MOCK_CONVERGE_SEC
            else:
                err = 0.0

            # center meluruh ke tengah frame (320,240); area membesar ke target
            cx = int(320 + 140 * err)
            cy = int(240 + 90 * err)
            area = self.MOCK_TARGET_AREA * (1.0 - 0.6 * err)
            frame = self._mock_frame(self.MOCK_QR_DATA, (cx, cy))
            result = self._build_result(self.MOCK_QR_DATA, (cx, cy), area, frame)

            if self._K is not None:   # kalibrasi ada → sediakan pose (PBVS), spt kamera nyata
                z = self.MOCK_TARGET_DIST + 0.5 * err
                result['pose'] = {'x': 0.18 * err, 'y': 0.12 * err, 'z': z,
                                  'dist': z, 'yaw_deg': 10.0 * err}
            self._dispatch(result)
            time.sleep(1.0 / self.fps)

    def _run_camera(self):
        """Capture loop nyata (USB / RTSP) — QR saja."""
        src = self.device if self.source == 'usb' else self.rtsp_url
        self._cap = cv2.VideoCapture(src)
        if not self._cap.isOpened():
            log.error("[vision] Tidak bisa membuka sumber kamera: %s", src)
            return

        interval = 1.0 / self.fps
        log.info("[vision] Kamera terbuka: %s", src)
        if not PYZBAR_OK:
            log.error("[vision] pyzbar tidak tersedia — QR tak bisa dideteksi dari kamera")

        while self._running:
            t_start = time.time()
            ret, frame = self._cap.read()
            if not ret:
                log.warning("[vision] Frame gagal dibaca, retry...")
                time.sleep(0.5)
                continue

            # Deteksi QR code (decode_qr = preprocessing berjenjang: mentah→CLAHE→upscale)
            for det in decode_qr(frame):
                data = det['data']          # sudah di-strip, TANPA .upper() (jaga JSON payload)
                pts = det['pts']
                center = (int(pts[:, 0].mean()), int(pts[:, 1].mean()))
                area = float(cv2.contourArea(pts.reshape(-1, 1, 2).astype(np.float32)))
                frame = self._annotate(frame, data, center, pts.astype(int))
                result = self._build_result(data, center, area, frame)
                # PBVS: pose 3D QR via solvePnP (butuh kalibrasi + 4 sudut terurut)
                if self._K is not None and len(pts) >= 4:
                    ordered = self._order_corners(pts)
                    result['pose'] = self._estimate_pose_pts(ordered, self.qr_length)
                self._dispatch(result)

            elapsed = time.time() - t_start
            sleep_t = max(0, interval - elapsed)
            time.sleep(sleep_t)

    # ── Helper ────────────────────────────────────────────────────────────────

    def _build_result(self, data, center, area, frame) -> dict:
        h, w = (frame.shape[0], frame.shape[1]) if frame is not None else (480, 640)
        result = {
            'type': 'qr',
            'data': data,
            'payload': parse_payload(data),
            'wall': wall_from_qr(data),
            'center': center,
            'area': area,
            'frame': frame,
            'frame_w': w,
            'frame_h': h,
            'pose': None,        # diisi {x,y,z,dist,yaw_deg} bila kalibrasi tersedia (PBVS)
            'timestamp': time.time(),
        }
        self._last_result = result
        self._last_qr = result
        return result

    def latest_qr(self, max_age=1.0) -> Optional[dict]:
        """Deteksi QR terakhir bila masih segar (hindari transisi dari deteksi basi)."""
        r = self._last_qr
        if not r or (time.time() - r['timestamp']) > max_age:
            return None
        return r

    @staticmethod
    def _order_corners(pts):
        """Urutkan 4 sudut → TL, TR, BR, BL (searah jarum jam dari kiri-atas).

        pyzbar `obj.polygon` tak menjamin urutan; SOLVEPNP_IPPE_SQUARE butuh winding
        tetap yang cocok dengan objek. Metode sum/diff standar."""
        pts = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
        s = pts.sum(axis=1)
        d = (pts[:, 1] - pts[:, 0])         # y - x
        tl = pts[np.argmin(s)]              # x+y terkecil
        br = pts[np.argmax(s)]              # x+y terbesar
        tr = pts[np.argmin(d)]              # y-x terkecil (x besar, y kecil)
        bl = pts[np.argmax(d)]              # y-x terbesar
        return np.array([tl, tr, br, bl], dtype=np.float32)

    def _estimate_pose_pts(self, img_pts, side) -> Optional[dict]:
        """Pose fiducial persegi via solvePnP dari 4 sudut TERURUT (TL,TR,BR,BL) + sisi (m).
        Camera frame OpenCV: +x kanan, +y bawah, +z depan. → {x,y,z,dist (m), yaw_deg}."""
        if self._K is None or not CV2_OK:
            return None
        L = side
        objp = np.array([[-L / 2, L / 2, 0], [L / 2, L / 2, 0],
                         [L / 2, -L / 2, 0], [-L / 2, -L / 2, 0]], dtype=np.float32)
        img = np.asarray(img_pts, dtype=np.float32).reshape(4, 2)
        flags = getattr(cv2, 'SOLVEPNP_IPPE_SQUARE', cv2.SOLVEPNP_ITERATIVE)
        ok, rvec, tvec = cv2.solvePnP(objp, img, self._K, self._dist, flags=flags)
        if not ok:
            return None
        tvec = np.asarray(tvec, dtype=float).ravel()   # (3,1) → (3,); aman di numpy 2.x
        x, y, z = float(tvec[0]), float(tvec[1]), float(tvec[2])
        R, _ = cv2.Rodrigues(rvec)
        # yaw = skew fiducial thd sumbu kamera (utk squaring). Tanda perlu VERIFIKASI hardware.
        yaw_deg = math.degrees(math.atan2(R[0, 2], R[2, 2]))
        return {'x': x, 'y': y, 'z': z, 'dist': math.sqrt(x * x + y * y + z * z),
                'yaw_deg': yaw_deg}

    def _dispatch(self, result: dict):
        # debug: deteksi terjadi tiap frame saat QR di FOV → hindari banjir log INFO
        log.debug("[vision] Deteksi QR data=%s wall=%s center=%s",
                  result['data'], result['wall'], result['center'])
        if self.callback:
            try:
                self.callback(result)
            except Exception as e:
                log.error("[vision] Callback error: %s", e)

    def _annotate(self, frame, data, center, pts):
        if not CV2_OK:
            return frame
        color = (0, 255, 0)
        cv2.polylines(frame, [pts], True, color, 2)
        cv2.putText(frame, f"QR:{data}", center,
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        return frame

    def _mock_frame(self, data, center):
        """Buat frame dummy 640x480 untuk mock mode."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        if CV2_OK:
            color = (0, 255, 0)
            cv2.rectangle(frame,
                          (center[0]-30, center[1]-30),
                          (center[0]+30, center[1]+30),
                          color, 2)
            cv2.putText(frame, f"MOCK QR:{data}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        return frame


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    ap = argparse.ArgumentParser(description='QR vision pipeline test')
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
