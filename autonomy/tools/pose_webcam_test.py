#!/usr/bin/env python3
"""
tools/pose_webcam_test.py — Validasi PBVS (solvePnP + PoseServo) dgn webcam + QR payload.

Butuh file kalibrasi (.npz dari calibrate_camera.py) + sisi QR fisik (meter).
Tampilkan sumbu 3D QR + jarak (z) + command PoseServo + status ALIGNED.

  pip install opencv-python pyzbar numpy      # + apt install libzbar0
  python tools/pose_webcam_test.py --calib vision/calibration/dwe.npz \
         --qr-size 0.04

Gerakkan QR → x/y/z (meter) & command bereaksi; di jarak engage & lurus → ALIGNED.
Bila tanda terbalik, set invert_* pada PoseServo (lihat VERIFIKASI_ARDUSUB.md).
Opsi --data <str> menyaring hanya QR yang isinya memuat substring itu (mis. 'HYDROSHIP').

Deteksi via decode_qr() (CLAHE + upscale → QR jauh/cahaya buruk lebih terbaca).
Log CSV per-frame (detection-rate + pose) utk diagnosa:
  python tools/pose_webcam_test.py --calib k.npz --csv run.csv --cam-width 1280 --cam-height 720
"""
import argparse
import math
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from control.visual_servo import PoseServo
from vision.qr_detect import VisionPipeline, decode_qr   # _order_corners + decode_qr robust
from tools.detection_log import DetectionCsvLogger

ap = argparse.ArgumentParser()
ap.add_argument("--calib", default="vision/calibration/dwe_underwater.npz", help="file .npz kalibrasi (K, dist)")
ap.add_argument("--qr-size", type=float, default=0.04, help="sisi QR payload fisik (m) — KKI 2026 = 0.04")
ap.add_argument("--data", default=None, help="hanya proses QR yang isinya memuat substring ini")
ap.add_argument("--device", type=int, default=0)
ap.add_argument("--url", default=None,
                help="stream kamera ROV (mis. http://192.168.2.2:8080/stream) — override --device")
ap.add_argument("--target-dist", type=float, default=0.30)
ap.add_argument("--csv", default=None, help="tulis log deteksi per-frame ke file CSV ini")
ap.add_argument("--cam-width", type=int, default=1280, help="minta resolusi lebar kamera (bantu QR jauh)")
ap.add_argument("--cam-height", type=int, default=720, help="minta resolusi tinggi kamera")
a = ap.parse_args()

d = np.load(a.calib)
K, dist = d['K'], d['dist']
L = a.qr_size
objp = np.array([[-L/2, L/2, 0], [L/2, L/2, 0], [L/2, -L/2, 0], [-L/2, -L/2, 0]], np.float32)

servo = PoseServo(target_dist=a.target_dist)

src = a.url if a.url else a.device
cap = cv2.VideoCapture(src)
if not cap.isOpened():
    sys.exit(f"Tidak bisa membuka sumber kamera: {src}")
cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.cam_width)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.cam_height)
print(f"PBVS test — QR payload, sisi={L} m, target {a.target_dist} m. 'q' keluar.")

logger = DetectionCsvLogger(a.csv) if a.csv else None
try:
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        found = False
        row = {}
        for det in decode_qr(frame):
            data = det['data']                          # JSON payload: sudah strip, tak di-upper
            if a.data and a.data.upper() not in data.upper():
                continue
            pts = np.asarray(det['pts'], dtype=np.float32)
            if len(pts) < 4:
                break
            found = True
            img = VisionPipeline._order_corners(pts)
            flags = getattr(cv2, 'SOLVEPNP_IPPE_SQUARE', cv2.SOLVEPNP_ITERATIVE)
            ok2, rvec, tvec = cv2.solvePnP(objp, img, K, dist, flags=flags)
            if not ok2:
                break
            tvec = np.asarray(tvec, dtype=float).ravel()
            x, y, z = float(tvec[0]), float(tvec[1]), float(tvec[2])
            R, _ = cv2.Rodrigues(rvec)
            yaw = math.degrees(math.atan2(R[0, 2], R[2, 2]))
            o = servo.step(x, y, z, yaw)
            dist_m = math.sqrt(x * x + y * y + z * z)
            cv2.drawFrameAxes(frame, K, dist, rvec, tvec, L * 0.5)
            cv2.polylines(frame, [img.astype(int)], True, (0, 255, 0), 2)
            col = (0, 255, 0) if o.aligned else (0, 200, 255)
            for k, t in enumerate([
                f"QR:{data}",
                f"x={x:+.3f} y={y:+.3f} z={z:.3f} m  yaw={yaw:+.1f}",
                f"surge={o.surge:+5.1f} sway={o.sway:+5.1f} vert={o.vert:+5.1f}",
                "ALIGNED" if o.aligned else "servoing (PBVS)...",
            ]):
                cv2.putText(frame, t, (10, 30 + 26 * k),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
            row = dict(data=data, x=round(x, 4), y=round(y, 4), z=round(z, 4),
                       dist=round(dist_m, 4), yaw=round(yaw, 1),
                       surge=round(o.surge, 2), sway=round(o.sway, 2),
                       vert=round(o.vert, 2), aligned=o.aligned)
            break
        if not found:
            servo.reset()
            cv2.putText(frame, "cari QR payload...", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        if logger:
            logger.log(detected=found, **row)
        cv2.imshow("PBVS pose test (q=keluar)", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
finally:
    cap.release()
    cv2.destroyAllWindows()
    if logger:
        logger.close()
