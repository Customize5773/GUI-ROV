#!/usr/bin/env python3
"""Backend YOLO worker: CAM BOTTOM -> qr_vision JSONL.

Melokalisasi region QR dengan best_new.pt (model detect-only, 1 kelas), meng-crop
ROI dari bbox itu, lalu men-decode QR DI DALAM crop — bukan memindai frame penuh.
Node GUI yang memiliki proses ini; worker tidak pernah mengirim perintah wahana.

Kenapa di laptop dan bukan di Pi: ultralytics hanya terpasang di laptop, dan Pi
sengaja dijaga bebas torch. Decode QR lokal di Pi TETAP jalan sebagai fallback —
lihat _fresh_payload di fsm/mission5.py.
"""

import argparse
import logging
import sys
import time

# Lewat paket `tools` supaya import ini sah baik saat worker dijalankan sebagai
# skrip oleh Node (PYTHONPATH=autonomy) maupun saat diimpor tes.
from tools.hook_vision_worker import LatestFrame, emit


def _quad_from_bbox(bbox):
    """bbox (x, y, w, h) -> 4 sudut searah jarum jam, seed untuk crop ROI."""
    x, y, w, h = bbox
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _jsonable(value):
    """Buang tipe numpy supaya hasilnya aman di-JSON-kan ke Node/UDP."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, 'tolist'):
        return value.tolist()
    return value


def main():
    ap = argparse.ArgumentParser(description='Laptop-side YOLO QR worker')
    ap.add_argument('--camera', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--calib', default=None,
                    help='npz kalibrasi CAM BOTTOM; tanpa ini pose=None dan '
                         'gate squaring M5_QR_DOCK ikut mati')
    ap.add_argument('--qr-size', type=float, default=0.04,
                    help='sisi QR dalam meter (spesifikasi KKI 4 cm)')
    # 0.6 = puncak F1 dari report training best_new.pt. Titik awal, BUKAN angka
    # mati: air kolam menurunkan confidence, jadi setel lewat QR_VISION_CONF.
    ap.add_argument('--conf', type=float, default=0.6)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--fps', type=float, default=10.0)
    args = ap.parse_args()

    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format='[qr-worker] %(levelname)s %(message)s')
    try:
        import cv2
        import numpy as np
        from vision.qr_detect import (_decode_tracked_roi, _order_quad_points,
                                      estimate_pose_pts, parse_payload, wall_from_qr)
        from vision.yolo_hook import YOLOHookDetector
        # YOLOHookDetector dipakai apa adanya: untuk model detect-only blok
        # keypoint-nya menghasilkan None dan TTA menyesuaikan sendiri. Tidak ada
        # perubahan di yolo_hook.py, jadi jalur hook/pose tidak tersentuh.
        detector = YOLOHookDetector(args.model, conf=args.conf, imgsz=args.imgsz)
        K = dist = None
        if args.calib:
            from vision.hook_localization import load_calibration
            calibration = load_calibration(args.calib)
            K, dist = calibration['K'], calibration['dist']
        else:
            logging.warning('tanpa --calib: pose=None, gate squaring M5_QR_DOCK mati')
    except Exception as exc:
        emit({'status': 'worker_error', 'reason': str(exc), 'timestamp': time.time()})
        return 2

    def _open_camera():
        new_cap = cv2.VideoCapture(args.camera)
        try:
            new_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return new_cap

    cap = _open_camera()
    if not cap.isOpened():
        emit({'status': 'camera_error',
              'reason': 'tidak bisa membuka ' + str(args.camera),
              'timestamp': time.time()})
        return 3

    camera = LatestFrame(cap, opener=_open_camera).start()
    interval = 1.0 / max(0.1, args.fps)
    last_status = None
    last_emit = 0.0
    last_seq = 0

    try:
        while True:
            started = time.monotonic()
            last_seq, frame, captured_at, failed = camera.take(last_seq)
            if frame is None:
                if not failed:
                    # Belum ada frame BARU sejak siklus lalu — bukan error.
                    time.sleep(min(interval, 0.01))
                    continue
                result = {'status': 'camera_error', 'reason': 'frame gagal dibaca',
                          'timestamp': time.time()}
            else:
                h, w = frame.shape[:2]
                detection = detector.detect(frame)
                decoded = (_decode_tracked_roi(frame,
                                               _quad_from_bbox(detection['bbox']),
                                               full_cascade=True)
                           if detection is not None else None)
                if not decoded:
                    # Region tanpa decode tidak dilaporkan: tanpa teks QR, FSM
                    # tak boleh menggerakkan apa pun, jadi hasilnya sama saja
                    # dengan tidak ada deteksi.
                    result = {'status': 'no_detection', 'timestamp': time.time()}
                else:
                    det = decoded[0]
                    pts = np.asarray(det['pts'], dtype=np.float32).reshape(-1, 2)
                    data = det['data']
                    ordered = _order_quad_points(pts)
                    pose = (estimate_pose_pts(ordered, args.qr_size, K, dist)
                            if ordered is not None else None)
                    result = {
                        'status': 'ok',
                        'data': data,
                        'payload': parse_payload(data),
                        'wall': wall_from_qr(data),
                        'center': [float(pts[:, 0].mean()), float(pts[:, 1].mean())],
                        'area': float(cv2.contourArea(pts.astype(np.int32))),
                        'pts': _jsonable(pts),
                        'pose': _jsonable(pose) if pose else None,
                        'confidence': detection.get('confidence'),
                        'bbox': [float(v) for v in detection['bbox']],
                        'frame_w': w, 'frame_h': h,
                        'timestamp': time.time(),
                        'capture_ts': captured_at,
                        # Umur frame saat hasil ini dibuat: antrean kamera +
                        # inferensi + decode. Pi membuang yang basi sebelum
                        # boleh menggerakkan ROV.
                        'age_ms': round((time.time() - captured_at) * 1000.0, 1),
                        'method': 'yolo_qr',
                        'active_cam': 'BOTTOM',
                    }

            now = time.time()
            # Setiap decode sukses dikirim: servo docking butuh geometri tiap
            # frame, bukan ringkasan status tiap 0,5 s seperti worker hook.
            if (result.get('status') == 'ok' or result.get('status') != last_status
                    or now - last_emit >= 0.5):
                emit(result)
                last_status, last_emit = result.get('status'), now
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    finally:
        camera.stop()
        camera.release()


if __name__ == '__main__':
    raise SystemExit(main())
