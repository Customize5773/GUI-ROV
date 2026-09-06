"""Detektor hook YOLOv8 — dua backend, satu kontrak output.

`YOLOHookDetector` (Ultralytics/.pt) dipakai di laptop untuk training, export,
dan test paritas. `OnnxHookDetector` (cv2.dnn/.onnx) dipakai di RUNTIME Pi:
opencv-contrib-python sudah terpasang di Pi (autonomy/requirements.txt), jadi
inferensi berjalan TANPA torch — 1 GB dependensi yang tidak muat nyaman di
Raspberry Pi 4 dan tidak boleh bersaing CPU dengan loop MAVLink.

Keduanya WAJIB mengembalikan dict yang identik field-per-field (lihat `_pack`).
Itulah sebabnya _validate_hook_vision di rov_agent.py dan seluruh fsm/mission5.py
tidak perlu tahu backend mana yang dipakai. Paritasnya bukan asumsi — dijaga
autonomy/tests/test_onnx_parity.py.

Import backend dilakukan saat kelas dibuat (bukan di level modul) supaya jalur
yang tidak memakainya tidak ikut menanggung dependensinya.
"""

import logging
import time

log = logging.getLogger(__name__)


def _pack(x1, y1, x2, y2, confidence, keypoints, frame_w, frame_h):
    """Skema deteksi hook yang dipakai FSM. SATU sumber untuk kedua backend.

    `width_px` sengaja None: lebar bounding-box bukan diameter pipa, jadi tidak
    boleh dipakai sebagai jarak 3D tanpa kalibrasi/geometri tambahan.
    """
    x1 = max(0, min(frame_w - 1, int(round(x1))))
    y1 = max(0, min(frame_h - 1, int(round(y1))))
    x2 = max(x1 + 1, min(frame_w, int(round(x2))))
    y2 = max(y1 + 1, min(frame_h, int(round(y2))))
    bw, bh = x2 - x1, y2 - y1
    return {
        'type': 'hook',
        'center': (int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))),
        'bbox': (x1, y1, bw, bh),
        'area': float(bw * bh),
        'width_px': None,
        'confidence': float(confidence),
        'method': 'yolov8',
        'frame_w': int(frame_w),
        'frame_h': int(frame_h),
        'pose': None,
        'keypoints': keypoints,
        'timestamp': time.time(),
    }


def _enhance(frame):
    """CLAHE pada kanal L — menaikkan kontras lokal di air berkabut."""
    import cv2
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    light, a, b = cv2.split(lab)
    light = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(light)
    return cv2.cvtColor(cv2.merge((light, a, b)), cv2.COLOR_LAB2BGR)


class YOLOHookDetector:
    """Ubah deteksi kelas Hook YOLO menjadi skema deteksi hook yang sudah dipakai FSM."""

    def __init__(self, weights, conf=0.35, imgsz=640, device=None,
                 augment=False, enhance_underwater=False):
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover - depends on laptop install
            raise RuntimeError(
                "YOLO hook membutuhkan paket ultralytics di laptop: pip install ultralytics"
            ) from exc
        self.weights = str(weights)
        self.conf = float(conf)
        self.imgsz = int(imgsz)
        self.device = device
        self.augment = bool(augment)
        self.enhance_underwater = bool(enhance_underwater)
        self.model = YOLO(self.weights)
        # Ultralytics YOLOv8-Pose tidak mendukung test-time augmentation;
        # memaksakan augment=True hanya menghasilkan warning berulang per frame.
        self.effective_augment = self.augment and getattr(self.model, 'task', None) == 'detect'
        log.info("[vision] YOLO hook aktif: %s (conf>=%.2f, imgsz=%d, tta=%s, underwater=%s)",
                 self.weights, self.conf, self.imgsz, self.effective_augment,
                 self.enhance_underwater)

    _enhance = staticmethod(_enhance)

    def detect(self, frame):
        """Kembalikan satu deteksi Hook dengan confidence tertinggi, atau None."""
        if frame is None:
            return None
        source = _enhance(frame) if self.enhance_underwater else frame
        result = self.model.predict(source=source, conf=self.conf, imgsz=self.imgsz,
                                    device=self.device, augment=self.effective_augment,
                                    verbose=False)[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return None

        scores = boxes.conf.detach().cpu().numpy()
        index = int(scores.argmax())
        x1, y1, x2, y2 = boxes.xyxy[index].detach().cpu().numpy().tolist()
        h, w = frame.shape[:2]
        keypoints = None
        pose = getattr(result, 'keypoints', None)
        if pose is not None and getattr(pose, 'xy', None) is not None and len(pose.xy) > index:
            xy = pose.xy[index].detach().cpu().numpy()
            kp_conf = (pose.conf[index].detach().cpu().numpy()
                       if getattr(pose, 'conf', None) is not None else None)
            keypoints = []
            for kp_index, (kx, ky) in enumerate(xy):
                keypoints.append({
                    'id': kp_index,
                    'x': float(kx),
                    'y': float(ky),
                    'confidence': (float(kp_conf[kp_index])
                                   if kp_conf is not None else None),
                })
        return _pack(x1, y1, x2, y2, scores[index], keypoints, w, h)


class OnnxHookDetector:
    """Backend runtime Raspberry Pi: cv2.dnn atas model .onnx, TANPA torch.

    Menerima argumen yang sama dengan YOLOHookDetector supaya kedua worker bisa
    memakai satu jalur konstruksi (`make_detector`).

    `augment` (TTA) diabaikan: ONNX adalah graf statis, tidak bisa multi-skala.
    Praktis tidak ada yang hilang — TTA sudah mati di kedua worker hari ini
    (model pose tidak mendukungnya, worker QR tidak memintanya).
    """

    def __init__(self, weights, conf=0.35, imgsz=640, device=None,
                 augment=False, enhance_underwater=False):
        import cv2
        self.weights = str(weights)
        self.conf = float(conf)
        self.imgsz = int(imgsz)
        self.device = device
        self.enhance_underwater = bool(enhance_underwater)
        if augment:
            log.warning('[vision] augment/TTA tidak tersedia di backend ONNX — diabaikan')
        self.net = cv2.dnn.readNetFromONNX(self.weights)
        # Backend/target default OpenCV. Di Pi 4 tidak ada akselerator, jadi ini
        # sudah jalur tercepat yang tersedia tanpa menambah paket.
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        # Jumlah keypoint DIBACA dari bentuk output, bukan di-hardcode 6: kolom
        # YOLOv8 = 4 bbox + 1 skor kelas + 3 per keypoint. Model detect-only
        # (best_new.pt) menghasilkan 5 -> nk 0 -> keypoints None, persis
        # perilaku YOLOHookDetector untuk model yang sama.
        channels = self._forward_shape()
        self.n_keypoints = max(0, (channels - 5) // 3)
        log.info('[vision] ONNX hook aktif: %s (conf>=%.2f, imgsz=%d, keypoints=%d, underwater=%s)',
                 self.weights, self.conf, self.imgsz, self.n_keypoints,
                 self.enhance_underwater)

    def _forward_shape(self):
        import cv2
        import numpy as np
        blob = cv2.dnn.blobFromImage(
            np.zeros((self.imgsz, self.imgsz, 3), np.uint8), 1 / 255.0,
            (self.imgsz, self.imgsz), swapRB=True, crop=False)
        self.net.setInput(blob)
        return int(np.asarray(self.net.forward()).shape[1])

    def _letterbox(self, frame):
        """Skala jaga-rasio + padding TERPUSAT, sama seperti preprocessing
        Ultralytics. Meregangkan frame 16:9 ke kotak 640x640 (yang dilakukan
        blobFromImage sendirian) menggeser setiap koordinat keluaran — bbox
        masih terlihat masuk akal, tapi keypoint meleset beberapa piksel dan
        itu langsung jadi kesalahan bidik servo docking.
        """
        import cv2
        import numpy as np
        h, w = frame.shape[:2]
        scale = min(self.imgsz / w, self.imgsz / h)
        new_w, new_h = int(round(w * scale)), int(round(h * scale))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((self.imgsz, self.imgsz, 3), 114, np.uint8)
        pad_x, pad_y = (self.imgsz - new_w) // 2, (self.imgsz - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        return canvas, scale, pad_x, pad_y

    def detect(self, frame):
        """Kembalikan satu deteksi Hook dengan confidence tertinggi, atau None."""
        import cv2
        import numpy as np
        if frame is None:
            return None
        source = _enhance(frame) if self.enhance_underwater else frame
        canvas, scale, pad_x, pad_y = self._letterbox(source)
        blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, (self.imgsz, self.imgsz),
                                     swapRB=True, crop=False)
        self.net.setInput(blob)
        # (1, 4+1+3*nk, 8400) -> (8400, 4+1+3*nk): satu baris per kandidat.
        out = np.asarray(self.net.forward())[0].T

        scores = out[:, 4]
        index = int(scores.argmax())
        # TANPA NMS, dan itu disengaja: kontraknya hanya mengembalikan deteksi
        # ber-confidence TERTINGGI, sedangkan NMS selalu mempertahankan kotak
        # skor tertinggi. Menjalankannya di sini hanya membakar CPU Pi untuk
        # hasil yang identik.
        if scores[index] < self.conf:
            return None

        row = out[index]
        cx, cy, bw, bh = row[:4]
        # Batalkan letterbox: buang padding lebih dulu, baru bagi skalanya.
        def unmap(x, y):
            return (float(x) - pad_x) / scale, (float(y) - pad_y) / scale

        x1, y1 = unmap(cx - bw / 2, cy - bh / 2)
        x2, y2 = unmap(cx + bw / 2, cy + bh / 2)

        keypoints = None
        if self.n_keypoints:
            keypoints = []
            for kp_index in range(self.n_keypoints):
                kx, ky, kv = row[5 + kp_index * 3: 8 + kp_index * 3]
                px, py = unmap(kx, ky)
                # Sengaja TIDAK di-clamp ke frame, sama seperti Ultralytics:
                # FSM sendiri yang menolak keypoint di luar margin tepi
                # (_hook_skeleton di fsm/mission5.py).
                keypoints.append({'id': kp_index, 'x': px, 'y': py,
                                  'confidence': float(kv)})

        h, w = frame.shape[:2]
        return _pack(x1, y1, x2, y2, scores[index], keypoints, w, h)


def make_detector(weights, **kwargs):
    """Pilih backend dari ekstensi bobot: .onnx -> cv2.dnn, selain itu -> Ultralytics.

    Satu jalur konstruksi untuk kedua worker, jadi laptop (.pt) dan Pi (.onnx)
    menjalankan kode worker yang sama persis.
    """
    if str(weights).lower().endswith('.onnx'):
        return OnnxHookDetector(weights, **kwargs)
    return YOLOHookDetector(weights, **kwargs)
