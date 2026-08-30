"""YOLOv8 hook detector untuk pemrosesan laptop-side.

Opt-in saja: import Ultralytics dilakukan saat kelas dibuat, sehingga Raspberry Pi
dan jalur detector OpenCV lama tidak ikut membutuhkan dependensi YOLO.
"""

import logging
import time

log = logging.getLogger(__name__)


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
        log.info("[vision] YOLO hook aktif: %s (conf>=%.2f, imgsz=%d, tta=%s, underwater=%s)",
                 self.weights, self.conf, self.imgsz, self.augment,
                 self.enhance_underwater)

    @staticmethod
    def _enhance(frame):
        import cv2
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        light, a, b = cv2.split(lab)
        light = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(light)
        return cv2.cvtColor(cv2.merge((light, a, b)), cv2.COLOR_LAB2BGR)

    def detect(self, frame):
        """Kembalikan satu deteksi Hook dengan confidence tertinggi, atau None.

        `width_px` sengaja None: lebar bounding-box bukan diameter pipa, jadi tidak
        boleh dipakai sebagai jarak 3D tanpa kalibrasi/geometri tambahan.
        """
        if frame is None:
            return None
        source = self._enhance(frame) if self.enhance_underwater else frame
        result = self.model.predict(source=source, conf=self.conf, imgsz=self.imgsz,
                                    device=self.device, augment=self.augment,
                                    verbose=False)[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return None

        scores = boxes.conf.detach().cpu().numpy()
        index = int(scores.argmax())
        x1, y1, x2, y2 = boxes.xyxy[index].detach().cpu().numpy().tolist()
        h, w = frame.shape[:2]
        x1, y1 = max(0, min(w - 1, int(round(x1)))), max(0, min(h - 1, int(round(y1))))
        x2, y2 = max(x1 + 1, min(w, int(round(x2)))), max(y1 + 1, min(h, int(round(y2))))
        bw, bh = x2 - x1, y2 - y1
        return {
            'type': 'hook',
            'center': (int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))),
            'bbox': (x1, y1, bw, bh),
            'area': float(bw * bh),
            'width_px': None,
            'confidence': float(scores[index]),
            'method': 'yolov8',
            'frame_w': int(w),
            'frame_h': int(h),
            'pose': None,
            'timestamp': time.time(),
        }
