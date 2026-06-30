#!/usr/bin/env python3
"""
tools/pose_webcam_test.py — Validasi PBVS (solvePnP + PoseServo) dgn webcam + marker cetak.

Butuh file kalibrasi (.npz dari calibrate_camera.py) + panjang marker fisik (meter).
Tampilkan sumbu 3D marker + jarak (z) + command PoseServo + status ALIGNED.

  pip install opencv-contrib-python numpy
  python tools/pose_webcam_test.py --calib vision/calibration/dwe.npz \
         --marker-length 0.10 --id 7

Gerakkan marker → x/y/z (meter) & command bereaksi; di jarak engage & lurus → ALIGNED.
Bila tanda terbalik, set invert_* pada PoseServo (lihat VERIFIKASI_ARDUSUB.md).
"""
import argparse
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from control.visual_servo import PoseServo

ap = argparse.ArgumentParser()
ap.add_argument("--calib", required=True, help="file .npz kalibrasi (K, dist)")
ap.add_argument("--marker-length", type=float, default=0.10, help="sisi marker fisik (m)")
ap.add_argument("--id", type=int, default=7)
ap.add_argument("--dict", default="DICT_4X4_50")
ap.add_argument("--device", type=int, default=0)
ap.add_argument("--target-dist", type=float, default=0.50)
a = ap.parse_args()

d = np.load(a.calib)
K, dist = d['K'], d['dist']
L = a.marker_length
objp = np.array([[-L/2, L/2, 0], [L/2, L/2, 0], [L/2, -L/2, 0], [-L/2, -L/2, 0]], np.float32)

aruco = cv2.aruco.ArucoDetector(
    cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, a.dict)),
    cv2.aruco.DetectorParameters())
servo = PoseServo(target_dist=a.target_dist)

cap = cv2.VideoCapture(a.device)
if not cap.isOpened():
    sys.exit(f"Tidak bisa membuka webcam {a.device}")
print(f"PBVS test — marker id={a.id}, L={L} m, target {a.target_dist} m. 'q' keluar.")

while True:
    ok, frame = cap.read()
    if not ok:
        break
    corners, ids, _ = aruco.detectMarkers(frame)
    found = False
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            if int(i) != a.id:
                continue
            found = True
            img = c.reshape(4, 2).astype(np.float32)
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
                f"x={x:+.3f} y={y:+.3f} z={z:.3f} m  yaw={yaw:+.1f}",
                f"surge={o.surge:+5.1f} sway={o.sway:+5.1f} vert={o.vert:+5.1f}",
                "ALIGNED" if o.aligned else "servoing (PBVS)...",
            ]):
                cv2.putText(frame, t, (10, 30 + 26 * k),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
            break
    if not found:
        servo.reset()
        cv2.putText(frame, f"cari marker id={a.id}...", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    cv2.imshow("PBVS pose test (q=keluar)", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break
cap.release()
cv2.destroyAllWindows()
