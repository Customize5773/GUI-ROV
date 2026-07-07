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
"""
import argparse
import math
import os
import sys

import cv2
import numpy as np

try:
    from pyzbar import pyzbar
except ImportError:
    sys.exit("pyzbar tidak tersedia — `pip install pyzbar` (+ apt install libzbar0)")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from control.visual_servo import PoseServo
from vision.qr_detect import VisionPipeline   # pakai _order_corners yang sama dgn pipeline

ap = argparse.ArgumentParser()
ap.add_argument("--calib", required=True, help="file .npz kalibrasi (K, dist)")
ap.add_argument("--qr-size", type=float, default=0.04, help="sisi QR payload fisik (m) — KKI 2026 = 0.04")
ap.add_argument("--data", default=None, help="hanya proses QR yang isinya memuat substring ini")
ap.add_argument("--device", type=int, default=0)
ap.add_argument("--target-dist", type=float, default=0.30)
a = ap.parse_args()

d = np.load(a.calib)
K, dist = d['K'], d['dist']
L = a.qr_size
objp = np.array([[-L/2, L/2, 0], [L/2, L/2, 0], [L/2, -L/2, 0], [-L/2, -L/2, 0]], np.float32)

servo = PoseServo(target_dist=a.target_dist)

cap = cv2.VideoCapture(a.device)
if not cap.isOpened():
    sys.exit(f"Tidak bisa membuka webcam {a.device}")
print(f"PBVS test — QR payload, sisi={L} m, target {a.target_dist} m. 'q' keluar.")

while True:
    ok, frame = cap.read()
    if not ok:
        break
    found = False
    for obj in pyzbar.decode(frame):
        data = obj.data.decode('utf-8', 'ignore').strip().upper()
        if a.data and a.data.upper() not in data:
            continue
        found = True
        pts = np.array([[p.x, p.y] for p in obj.polygon], dtype=np.float32)
        if len(pts) < 4:
            break
        img = VisionPipeline._order_corners(pts)
        flags = getattr(cv2, 'SOLVEPNP_IPPE_SQUARE', cv2.SOLVEPNP_ITERATIVE)
        ok2, rvec, tvec = cv2.solvePnP(objp, img, K, dist, flags=flags)
        if not ok2:
            break
        x, y, z = float(tvec[0]), float(tvec[1]), float(tvec[2])
        R, _ = cv2.Rodrigues(rvec)
        yaw = math.degrees(math.atan2(R[0, 2], R[2, 2]))
        o = servo.step(x, y, z, yaw)
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
        break
    if not found:
        servo.reset()
        cv2.putText(frame, "cari QR payload...", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    cv2.imshow("PBVS pose test (q=keluar)", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break
cap.release()
cv2.destroyAllWindows()
