#!/usr/bin/env python3
"""
tools/servo_webcam_test.py — Validasi closed-loop visual servo APPROACH_HOOK
pakai WEBCAM + marker cetak, TANPA ROV.

Dua mode:
  IBVS (default, tanpa kalibrasi): pakai error piksel + luas marker.
  PBVS (--calib dwe.npz):          pakai pose 3D (solvePnP) — jarak/sudut sebenarnya.

Gerakkan marker → command bereaksi:
  - marker KANAN  → sway +   ·  marker KECIL/JAUH → surge +  ·  marker BAWAH → vert -
  - di tengah & jarak engage → ALIGNED

  pip install opencv-contrib-python numpy
  # IBVS:
  python tools/servo_webcam_test.py --device 0 --id 7
  # PBVS (setelah kalibrasi):
  python tools/servo_webcam_test.py --device 0 --id 7 --calib vision/calibration/dwe.npz --marker-length 0.10

Tekan 'q' keluar. CATATAN: menguji LOGIKA & TANDA servo dgn kamera nyata; loop fisik
baru tertutup di kolam. Tanda terbalik → set invert_* di VisualServo/PoseServo
(lihat VERIFIKASI_ARDUSUB.md).
"""
import argparse
import math
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from control.visual_servo import VisualServo, PoseServo

ap = argparse.ArgumentParser()
ap.add_argument("--device", type=int, default=0)
ap.add_argument("--id", type=int, default=7)
ap.add_argument("--dict", default="DICT_4X4_50")
ap.add_argument("--target-area", type=float, default=30000.0, help="IBVS: luas engage (px^2)")
ap.add_argument("--calib", default=None, help="path .npz kalibrasi → mode PBVS")
ap.add_argument("--marker-length", type=float, default=0.10, help="PBVS: sisi marker (m)")
ap.add_argument("--target-dist", type=float, default=0.50, help="PBVS: jarak engage (m)")
args = ap.parse_args()

aruco = cv2.aruco.ArucoDetector(
    cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, args.dict)),
    cv2.aruco.DetectorParameters())

PBVS = bool(args.calib)
if PBVS:
    data = np.load(args.calib)
    K, DIST = data['K'], data['dist']
    servo = PoseServo(target_dist=args.target_dist)
    L = args.marker_length
    OBJP = np.array([[-L/2, L/2, 0], [L/2, L/2, 0], [L/2, -L/2, 0], [-L/2, -L/2, 0]], np.float32)
    print(f"Mode PBVS (solvePnP) — calib={args.calib}, marker={L} m")
else:
    servo = VisualServo(target_area=args.target_area)
    print("Mode IBVS (piksel) — beri --calib untuk PBVS")

cap = cv2.VideoCapture(args.device)
if not cap.isOpened():
    sys.exit(f"Tidak bisa membuka webcam index {args.device}")

t_prev = time.time()
while True:
    ok, frame = cap.read()
    if not ok:
        break
    H, W = frame.shape[:2]
    cv2.drawMarker(frame, (W // 2, H // 2), (120, 120, 120), cv2.MARKER_CROSS, 30, 1)
    corners, ids, _ = aruco.detectMarkers(frame)
    now = time.time(); dt = max(1e-3, now - t_prev); t_prev = now

    found = False
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            if int(i) != args.id:
                continue
            found = True
            pts = c.reshape(4, 2)
            cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
            cv2.polylines(frame, [pts.astype(int)], True, (0, 255, 0), 2)

            if PBVS:
                okp, rvec, tvec = cv2.solvePnP(
                    OBJP, pts.astype(np.float32), K, DIST,
                    flags=getattr(cv2, 'SOLVEPNP_IPPE_SQUARE', cv2.SOLVEPNP_ITERATIVE))
                if not okp:
                    break
                x, y, z = float(tvec[0]), float(tvec[1]), float(tvec[2])
                Rm, _ = cv2.Rodrigues(rvec)
                yaw = math.degrees(math.atan2(Rm[0, 2], Rm[2, 2]))
                o = servo.step(x, y, z, yaw, dt)
                cv2.drawFrameAxes(frame, K, DIST, rvec, tvec, args.marker_length * 0.5)
                lines = [f"x={x:+.2f} y={y:+.2f} z={z:.2f} m (target {args.target_dist} m)",
                         f"dist={math.sqrt(x*x+y*y+z*z):.2f} m  yaw={yaw:+.0f}",
                         f"surge={o.surge:+5.1f} sway={o.sway:+5.1f} vert={o.vert:+5.1f}"]
            else:
                area = float(cv2.contourArea(pts.astype(np.float32)))
                o = servo.step(cx, cy, area, W, H, dt)
                lines = [f"AREA={area:6.0f} (target {args.target_area:.0f})",
                         f"ex={o.ex:+.2f} ey={o.ey:+.2f} ea={o.ea:+.2f}",
                         f"surge={o.surge:+5.1f} sway={o.sway:+5.1f} vert={o.vert:+5.1f}"]

            cv2.arrowedLine(frame, (int(cx), int(cy)),
                            (int(cx + o.sway * 3), int(cy - o.vert * 3)), (0, 200, 255), 3)
            col = (0, 255, 0) if o.aligned else (0, 200, 255)
            for k, txt in enumerate(lines + ["ALIGNED" if o.aligned else "servoing..."]):
                cv2.putText(frame, txt, (10, 30 + 26 * k),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, col, 2)
            break
    if not found:
        servo.reset()
        cv2.putText(frame, f"cari marker id={args.id}...", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    cv2.imshow("APPROACH_HOOK servo test (q=keluar)", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
