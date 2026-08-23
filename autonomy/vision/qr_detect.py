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
from collections import deque
from typing import Callable, Optional
import numpy as np

from vision.hook_detect import detect_hook

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
except Exception as _e:
    # ImportError (paket tak terpasang) ATAU error load DLL native (mis. libzbar-64.dll /
    # libiconv.dll tak ketemu di Windows) — keduanya berarti pyzbar tak bisa dipakai.
    # decode_qr() tetap jalan lewat fallback cv2.QRCodeDetector di bawah.
    PYZBAR_OK = False
    log.warning(f"[vision] pyzbar tidak tersedia ({_e}) — pakai fallback cv2.QRCodeDetector")

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
STACK_N      = 3       # jumlah frame utk median-stack jenjang-5 (lawan riak/kaustik transien)


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
        cam_width: int = 1280,
        cam_height: int = 720,
        calib_file: Optional[str] = None,
        qr_length: float = 0.04,
        hook_hsv_range=None,
        hook_min_area: float = 150.0,
        hook_pipe_diam: float = 0.025,
        wall_cnn=None,
        wall_cnn_votes: int = 3,
        wall_cnn_min_conf: float = 0.8,
        qr_device=None,
        qr_url: Optional[str] = None,
        hook_device=None,
        hook_url: Optional[str] = None,
        calib_file_qr: Optional[str] = None,
        calib_file_hook: Optional[str] = None,
    ):
        """
        Parameters
        ----------
        source     : 'mock' | 'usb' | 'rtsp'
        device     : index USB webcam (default 0)
        rtsp_url   : URL RTSP/HTTP jika source='rtsp'
        callback   : fungsi dipanggil tiap deteksi QR
        fps        : target frame-rate capture
        cam_width, cam_height : resolusi capture USB (px). HARUS sama dgn resolusi saat
                     kalibrasi (lihat calib_file) — K/dist tak valid bila resolusi beda.
                     Default 1280x720 (kalibrasi dwe.npz saat ini).
        calib_file : .npz kalibrasi kamera → aktifkan PBVS (solvePnP). None → IBVS.
        qr_length  : sisi fisik QR payload (m) utk solvePnP — KKI 2026 = 0.04 (4×4 cm)
        hook_hsv_range : [[h,s,v],[h,s,v]] opsional utk deteksi hook berbasis warna
                     (None → jalur non-warna/contour; JANGAN andalkan warna, lihat hook_detect.py)
        hook_min_area  : luas contour minimum (px^2) kandidat hook
        hook_pipe_diam : diameter pipa hook fisik (m) — KKI 2026 ¾" = 0.025 (estimasi jarak)
        wall_cnn   : aktifkan fallback tebak sisi kolam saat decode_qr() GAGAL.
                     True → bobot bawaan (vision/wall_cnn.npz); str → path .npz;
                     None/False → nonaktif (default). Hasilnya TIDAK masuk latest_qr()
                     — ambil lewat latest_wall_hint(). Lihat vision/wall_cnn.py.
        wall_cnn_votes    : jumlah frame berturut-turut yang harus sepakat (WallVoter)
        wall_cnn_min_conf : confidence minimum agar sebuah tebakan dihitung

        qr_device, qr_url, hook_device, hook_url : ROV punya DUA kamera fisik (BOTTOM
                     utk QR docking, WALL utk hook). Isi qr_url/qr_device (utk kamera
                     BOTTOM) DAN hook_url/hook_device (utk kamera WALL) sekaligus untuk
                     mengaktifkan mode dual-camera (dua capture + dua thread independen).
                     Isi salah satu saja → keduanya berbagi kamera itu (mode lama, dgn
                     warning). Tak isi keduanya → source/device/rtsp_url di atas dipakai
                     persis seperti sebelumnya (satu kamera utk QR+hook, tak berubah).
        calib_file_qr, calib_file_hook : kalibrasi terpisah per kamera dlm mode dual
                     (mis. vision/calibration/bottom.npz & wall.npz). Diabaikan bila
                     mode dual tak aktif — pakai calib_file biasa.
        """
        self.source = source
        self.device = device
        self.rtsp_url = rtsp_url
        self.callback = callback
        self.fps = fps
        self.cam_width = cam_width
        self.cam_height = cam_height
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._thread_qr: Optional[threading.Thread] = None
        self._thread_hook: Optional[threading.Thread] = None
        self._cap = None
        self._cap_qr = None
        self._cap_hook = None
        self._last_result: Optional[dict] = None
        self._last_qr: Optional[dict] = None      # deteksi QR terakhir (scan wall + docking)
        self._last_hook: Optional[dict] = None    # deteksi hook terakhir (HANG misi 3b + DOCK misi 4)
        self._last_wall_hint: Optional[dict] = None   # tebakan CNN saat decode GAGAL (opt-in)
        self._frame_buf = deque(maxlen=STACK_N)   # buffer utk jenjang-5 median-stack (lawan riak)

        # ── Dual-camera (BOTTOM utk QR, WALL utk hook) ─────────────────────────
        qr_src = qr_url if qr_url is not None else qr_device
        hook_src = hook_url if hook_url is not None else hook_device
        self._dual = False
        self._qr_src = None
        self._hook_src = None
        if qr_src is not None and hook_src is not None:
            self._dual = True
            self._qr_src = qr_src
            self._hook_src = hook_src
        elif qr_src is not None or hook_src is not None:
            shared = qr_src if qr_src is not None else hook_src
            log.warning("[vision] hanya 1 kamera dikonfigurasi (qr=%s, hook=%s) — "
                        "QR dan hook berbagi kamera itu", qr_src, hook_src)
            self.device = shared
            if self.source != 'mock':
                self.source = 'usb'   # cv2.VideoCapture terima str (URL) maupun int

        # Fallback sisi kolam (opsional). Dimuat malas & gagal-lunak: ROV tetap terbang
        # walau bobot belum diekspor — QR normal tak boleh ikut mati gara-gara ini.
        self._wall_clf = None
        self._wall_voter = None
        if wall_cnn:
            try:
                from vision.wall_cnn import WallClassifier, WallVoter
                self._wall_clf = WallClassifier(
                    None if wall_cnn is True else wall_cnn)
                self._wall_voter = WallVoter(need=wall_cnn_votes,
                                             min_conf=wall_cnn_min_conf)
                log.info("[vision] Fallback wall-CNN AKTIF (butuh %d frame sepakat, conf>=%.2f)",
                         wall_cnn_votes, wall_cnn_min_conf)
            except (ImportError, FileNotFoundError) as e:
                log.warning("[vision] Fallback wall-CNN nonaktif: %s", e)
        # Parameter deteksi hook (CAM WALL) — semua tunable di kolam (lihat hook_detect.py)
        self.hook_hsv_range = hook_hsv_range
        self.hook_min_area = hook_min_area
        self.hook_pipe_diam = hook_pipe_diam

        # Kalibrasi kamera utk PBVS (solvePnP). Bila tak ada → pose=None (fallback IBVS).
        self.qr_length = qr_length
        self._K = None
        self._dist = None
        # Resolusi saat kalibrasi dibuat, utk penjaga _verify_calib_size().
        self._calib_size = None
        self._calib_name = calib_file
        self._calib_checked = False
        if calib_file and CV2_OK:
            try:
                data = np.load(calib_file)
                self._K, self._dist = data['K'], data['dist']
                if 'image_size' in data:
                    self._calib_size = tuple(int(v) for v in data['image_size'])
                log.info("[vision] Kalibrasi dimuat: %s — PBVS (solvePnP) AKTIF", calib_file)
            except Exception as e:
                log.warning("[vision] gagal muat kalibrasi %s: %s — fallback IBVS", calib_file, e)

        # Kalibrasi per-kamera dlm mode dual (dipakai _run_qr_camera/_run_hook_camera).
        # Mode non-dual: _K_qr/_K_hook dibiarkan None, kode fallback ke self._K bersama.
        self._K_qr, self._dist_qr = None, None
        self._K_hook, self._dist_hook = None, None
        if self._dual and CV2_OK:
            if calib_file_qr:
                try:
                    d = np.load(calib_file_qr)
                    self._K_qr, self._dist_qr = d['K'], d['dist']
                    log.info("[vision] Kalibrasi QR/BOTTOM dimuat: %s", calib_file_qr)
                except Exception as e:
                    log.warning("[vision] gagal muat kalibrasi QR %s: %s", calib_file_qr, e)
            if calib_file_hook:
                try:
                    d = np.load(calib_file_hook)
                    self._K_hook, self._dist_hook = d['K'], d['dist']
                    log.info("[vision] Kalibrasi hook/WALL dimuat: %s", calib_file_hook)
                except Exception as e:
                    log.warning("[vision] gagal muat kalibrasi hook %s: %s", calib_file_hook, e)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Mulai thread capture di background."""
        if self._running:
            return
        self._running = True
        if self._dual and self.source != 'mock':
            self._thread_qr = threading.Thread(target=self._run_qr_camera, daemon=True,
                                               name='VisionThreadQR')
            self._thread_hook = threading.Thread(target=self._run_hook_camera, daemon=True,
                                                 name='VisionThreadHook')
            self._thread_qr.start()
            self._thread_hook.start()
            log.info("[vision] Started dual-camera (qr=%s, hook=%s)", self._qr_src, self._hook_src)
        else:
            self._thread = threading.Thread(target=self._run, daemon=True, name='VisionThread')
            self._thread.start()
            log.info("[vision] Started (source=%s)", self.source)

    def stop(self):
        """Hentikan thread capture."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._thread_qr:
            self._thread_qr.join(timeout=3)
        if self._thread_hook:
            self._thread_hook.join(timeout=3)
        if self._cap and CV2_OK:
            self._cap.release()
        if self._cap_qr and CV2_OK:
            self._cap_qr.release()
        if self._cap_hook and CV2_OK:
            self._cap_hook.release()
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

            # Mock hook (CAM WALL) — konvergen seiring `err` meluruh spt QR, agar HANG/DOCK
            # closed-loop bisa dijalankan tanpa kamera (uji launch_sitl --vision mock).
            hcx, hcy = int(320 + 130 * err), int(240 - 100 * err)   # hook di atas → naik ke tengah
            harea = self.MOCK_TARGET_AREA * (1.0 - 0.6 * err)
            self._last_hook = {
                'type': 'hook', 'center': (hcx, hcy), 'bbox': (hcx - 15, hcy - 40, 30, 80),
                'area': harea, 'width_px': 30.0 * (1.0 - 0.5 * err), 'confidence': 0.9,
                'method': 'mock', 'frame_w': 640, 'frame_h': 480, 'pose': None,
                'timestamp': time.time(),
            }
            if self._K is not None:
                zz = self.MOCK_TARGET_DIST + 0.5 * err
                self._last_hook['pose'] = {'x': 0.16 * err, 'y': -0.14 * err, 'z': zz, 'dist': zz}
            time.sleep(1.0 / self.fps)

    def _run_camera(self):
        """Capture loop nyata (USB / RTSP) — QR saja."""
        src = self.device if self.source == 'usb' else self.rtsp_url
        self._cap = cv2.VideoCapture(src)
        if not self._cap.isOpened():
            log.error("[vision] Tidak bisa membuka sumber kamera: %s", src)
            return
        if self.source == 'usb':
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cam_width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cam_height)

        interval = 1.0 / self.fps
        log.info("[vision] Kamera terbuka: %s", src)
        if not PYZBAR_OK:
            log.warning("[vision] pyzbar tidak tersedia — pakai fallback cv2.QRCodeDetector (kurang robust)")

        while self._running:
            t_start = time.time()
            ret, frame = self._cap.read()
            if not ret:
                log.warning("[vision] Frame gagal dibaca, retry...")
                time.sleep(0.5)
                continue

            self._verify_calib_size(frame)

            # Deteksi QR code (decode_qr = preprocessing berjenjang: mentah→CLAHE→upscale)
            dets = decode_qr(frame)
            if not dets:
                dets = self._decode_stacked(frame)   # jenjang-5: median antar-frame
            if not dets:
                self._try_wall_fallback(frame)
            elif self._wall_voter is not None:
                self._wall_voter.reset()      # decode sungguhan jalan lagi → mulai dari nol
                self._last_wall_hint = None
            for det in dets:
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

            # Deteksi hook (CAM WALL) — target geometrik non-QR utk HANG (misi 3b) & DOCK (misi 4).
            # focal_px dari kalibrasi (bila ada) → detect_hook mengisi pose (PBVS), else IBVS.
            focal = float(self._K[0, 0]) if self._K is not None else None
            hook = detect_hook(frame, hsv_range=self.hook_hsv_range,
                               min_area=self.hook_min_area, pipe_diam_m=self.hook_pipe_diam,
                               focal_px=focal)
            if hook is not None:
                self._last_hook = hook
                log.debug("[vision] Deteksi hook center=%s area=%.0f conf=%.2f method=%s",
                          hook['center'], hook['area'], hook['confidence'], hook['method'])

            elapsed = time.time() - t_start
            sleep_t = max(0, interval - elapsed)
            time.sleep(sleep_t)

    def _open_capture(self, src):
        """Buka cv2.VideoCapture utk `src` (index int ATAU URL str), atur resolusi bila USB."""
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            log.error("[vision] Tidak bisa membuka sumber kamera: %s", src)
            return None
        if isinstance(src, int):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cam_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cam_height)
        log.info("[vision] Kamera terbuka: %s", src)
        return cap

    def _run_qr_camera(self):
        """Thread dual-camera — kamera BOTTOM, deteksi QR saja (mirror _run_camera)."""
        self._cap_qr = self._open_capture(self._qr_src)
        if self._cap_qr is None:
            return
        interval = 1.0 / self.fps
        if not PYZBAR_OK:
            log.warning("[vision] pyzbar tidak tersedia — pakai fallback cv2.QRCodeDetector (kurang robust)")
        K = self._K_qr if self._K_qr is not None else self._K
        dist = self._dist_qr if self._K_qr is not None else self._dist

        while self._running:
            t_start = time.time()
            ret, frame = self._cap_qr.read()
            if not ret:
                log.warning("[vision] Frame QR gagal dibaca, retry...")
                time.sleep(0.5)
                continue

            dets = decode_qr(frame)
            if not dets:
                dets = self._decode_stacked(frame)   # jenjang-5: median antar-frame
            if not dets:
                self._try_wall_fallback(frame)
            elif self._wall_voter is not None:
                self._wall_voter.reset()
                self._last_wall_hint = None
            for det in dets:
                data = det['data']
                pts = det['pts']
                center = (int(pts[:, 0].mean()), int(pts[:, 1].mean()))
                area = float(cv2.contourArea(pts.reshape(-1, 1, 2).astype(np.float32)))
                frame = self._annotate(frame, data, center, pts.astype(int))
                result = self._build_result(data, center, area, frame)
                if K is not None and len(pts) >= 4:
                    ordered = self._order_corners(pts)
                    result['pose'] = self._estimate_pose_pts(ordered, self.qr_length, K=K, dist=dist)
                self._dispatch(result)

            elapsed = time.time() - t_start
            time.sleep(max(0, interval - elapsed))

    def _run_hook_camera(self):
        """Thread dual-camera — kamera WALL, deteksi hook saja (mirror _run_camera)."""
        self._cap_hook = self._open_capture(self._hook_src)
        if self._cap_hook is None:
            return
        interval = 1.0 / self.fps
        K = self._K_hook if self._K_hook is not None else self._K

        while self._running:
            t_start = time.time()
            ret, frame = self._cap_hook.read()
            if not ret:
                log.warning("[vision] Frame hook gagal dibaca, retry...")
                time.sleep(0.5)
                continue

            focal = float(K[0, 0]) if K is not None else None
            hook = detect_hook(frame, hsv_range=self.hook_hsv_range,
                               min_area=self.hook_min_area, pipe_diam_m=self.hook_pipe_diam,
                               focal_px=focal)
            if hook is not None:
                self._last_hook = hook
                log.debug("[vision] Deteksi hook center=%s area=%.0f conf=%.2f method=%s",
                          hook['center'], hook['area'], hook['confidence'], hook['method'])

            elapsed = time.time() - t_start
            time.sleep(max(0, interval - elapsed))

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

    def _decode_stacked(self, frame):
        """Jenjang-5: decode_qr() gagal di semua jenjangnya → coba median dari
        STACK_N frame terakhir. Riak berubah tiap frame, QR+ROV relatif diam
        saat SCAN_QR/hover, jadi median menekan distorsi transien yang jadi
        penyebab dominan gagal decode di air (lihat catatan lapangan riak)."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        self._frame_buf.append(gray)
        if len(self._frame_buf) < self._frame_buf.maxlen:
            return []
        stacked = np.median(np.stack(self._frame_buf), axis=0).astype(np.uint8)
        return decode_qr(stacked, enhance=True)

    def _try_wall_fallback(self, frame):
        """decode_qr() gagal → coba tebak SISI KOLAM saja lewat CNN (vision/wall_cnn.py).

        Sengaja TIDAK memanggil _dispatch()/_build_result(): hasil ini tak punya payload,
        dan Mission5FSM._is_target_payload() menerima apa saja yang payload-nya None —
        jadi menyusupkannya ke latest_qr() akan membuat FSM men-servo/GRAB berdasarkan
        tebakan tak tervalidasi. Konsumen harus meminta eksplisit via latest_wall_hint().
        """
        if self._wall_clf is None:
            return
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        try:
            # locate() dulu (murni cv2.QRCodeDetector.detect — TAK butuh decode
            # berhasil), lalu oper pts-nya ke predict_frame supaya tak dideteksi
            # dua kali. pts jugadipakai di bawah utk center/area — sinyal arah
            # kasar buat SCAN_QR mendekat sebelum decode penuh berhasil.
            pts = self._wall_clf.locate(gray)
            out = self._wall_clf.predict_frame(frame, pts=pts) if pts is not None else None
        except Exception as e:            # fallback tak boleh menjatuhkan loop kamera
            log.debug("[vision] wall-CNN error: %s", e)
            return
        wall, conf = out if out else (None, 0.0)
        stable = self._wall_voter.push(wall, conf)
        if stable is None:
            self._last_wall_hint = None
            return
        center = area = None
        if pts is not None:
            center = (int(pts[:, 0].mean()), int(pts[:, 1].mean()))
            area = float(cv2.contourArea(pts.reshape(-1, 1, 2).astype(np.float32)))
        self._last_wall_hint = {
            'type': 'wall_hint', 'wall': stable, 'confidence': float(conf),
            'source': 'cnn_fallback', 'validated': False,
            'center': center, 'area': area,   # lokasi QUAD kasar (bukan dari decode) —
                                               # SCAN_QR pakai ini utk mendekat, BUKAN target_wall.
            'frame_w': frame.shape[1], 'frame_h': frame.shape[0],
            'timestamp': time.time(),
        }
        log.debug("[vision] wall-hint (fallback CNN) wall=%s conf=%.2f", stable, conf)

    def latest_wall_hint(self, max_age=1.0) -> Optional[dict]:
        """Tebakan sisi kolam dari CNN saat decode_qr() GAGAL — atau None.

        BUKAN pengganti latest_qr(): tak ada payload, jadi TAK TERVALIDASI
        (`validated: False`). Sudah disaring WallVoter (butuh N frame sepakat), tapi tetap
        perlakukan sebagai petunjuk berkeyakinan rendah — mis. untuk memilih arah scan
        saat QR tak terbaca, BUKAN untuk memicu GRAB/RELEASE."""
        r = self._last_wall_hint
        if not r or (time.time() - r['timestamp']) > max_age:
            return None
        return r

    def latest_hook(self, max_age=1.0) -> Optional[dict]:
        """Deteksi hook terakhir bila masih segar (dipakai HANG misi 3b & DOCK misi 4).

        Antarmuka sengaja dibuat seragam dgn latest_qr agar FSM memakai pola loss-of-lock
        yang sama untuk kedua target (QR & hook)."""
        r = self._last_hook
        if not r or (time.time() - r['timestamp']) > max_age:
            return None
        return r

    def _verify_calib_size(self, frame):
        """Sekali saja: tolak kalibrasi yang resolusinya beda dari frame sungguhan.

        22 Agu 2026 — `dwe_underwater.npz` dikalibrasi pada 4080x3072 (resolusi
        FOTO) sementara kamera streaming 1280x720. rms-nya paling bagus dari
        semua file (1.75) sehingga tampak paling unggul, padahal rms hanya
        mengukur kecocokan model thd gambar kalibrasinya SENDIRI — ia tak tahu
        apa-apa soal resolusi yang nanti dipakai.

        Kenapa ini berbahaya dan bukan sekadar tak rapi: z ~ fx*W/w_px, jadi fx
        yang 3,2x terlalu besar membuat PBVS mengira QR 3,2x lebih jauh. Dengan
        SERVO_TARGET_DIST=0.30 m, ROV baru berhenti saat jarak ASLI ~9 cm —
        menabrak payload, bukan berhenti di depannya. Diam, tanpa pesan error.

        Sengaja MEMATIKAN PBVS (bukan menskala K): saat aspect ratio juga beda
        (4:3 vs 16:9) field-of-view-nya ikut beda, jadi menskala K TIDAK benar —
        cuma menukar error yang kelihatan dengan error yang tersembunyi. IBVS
        (berbasis area piksel) tak butuh kalibrasi dan tetap bisa docking.
        """
        if self._calib_checked:
            return
        self._calib_checked = True
        if self._K is None or self._calib_size is None or frame is None:
            return
        fh, fw = frame.shape[0], frame.shape[1]
        cw, ch = self._calib_size
        if (cw, ch) == (fw, fh):
            return
        log.error("[vision] KALIBRASI DITOLAK: %s dibuat pada %dx%d, frame nyata %dx%d "
                  "— K/dist tidak valid, PBVS DIMATIKAN, jatuh ke IBVS. "
                  "Pakai kalibrasi pada resolusi stream, atau set resolusi "
                  "kamera sama dengan saat kalibrasi.",
                  self._calib_name, cw, ch, fw, fh)
        self._K = None
        self._dist = None

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

    def _estimate_pose_pts(self, img_pts, side, K=None, dist=None) -> Optional[dict]:
        """Pose fiducial persegi via solvePnP dari 4 sudut TERURUT (TL,TR,BR,BL) + sisi (m).
        Camera frame OpenCV: +x kanan, +y bawah, +z depan. → {x,y,z,dist (m), yaw_deg}.
        K/dist opsional (mode dual-camera, kalibrasi per-kamera) — default self._K/_dist."""
        K = K if K is not None else self._K
        dist = dist if dist is not None else self._dist
        if K is None or not CV2_OK:
            return None
        L = side
        objp = np.array([[-L / 2, L / 2, 0], [L / 2, L / 2, 0],
                         [L / 2, -L / 2, 0], [-L / 2, -L / 2, 0]], dtype=np.float32)
        img = np.asarray(img_pts, dtype=np.float32).reshape(4, 2)
        flags = getattr(cv2, 'SOLVEPNP_IPPE_SQUARE', cv2.SOLVEPNP_ITERATIVE)
        ok, rvec, tvec = cv2.solvePnP(objp, img, K, dist, flags=flags)
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
