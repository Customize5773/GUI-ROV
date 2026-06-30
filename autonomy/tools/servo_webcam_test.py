#!/usr/bin/env python3
"""
tools/servo_webcam_test.py — Validasi closed-loop visual servo APPROACH_HOOK
pakai WEBCAM + marker cetak, TANPA ROV.

Tampilkan deteksi ArUco + command servo (sway/surge/vert) + status ALIGNED secara
live. Gerakkan marker → command harus bereaksi:
  - marker di KANAN frame  → sway KANAN (+)         agar ke tengah
  - marker terlalu KECIL   → surge MAJU  (+)         (terlalu jauh)
  - marker di BAWAH frame  → vert TURUN (-)          agar ke tengah
  - marker di tengah & cukup besar (≈ target area) → ALIGNED

  pip install opencv-contrib-python numpy
  python tools/servo_webcam_test.py --device 0 --id 7
Tekan 'q' keluar. Pakai readout AREA di layar untuk menyetel --target-area.

CATATAN: ini menguji LOGIKA & TANDA servo dgn kamera nyata. Loop fisik (ROV gerak)
baru tertutup saat di kolam. Bila tanda terbalik, sesuaikan flag invert_* VisualServo
(lihat VERIFIKASI_ARDUSUB.md).
"""
import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from control.visual_servo import VisualServo

ap = argparse.ArgumentParser()
ap.add_argument("--device", type=int, default=0, help="index webcam")
ap.add_argument("--id", type=int, default=7, help="ID marker hook")
ap.add_argument("--dict", default="DICT_4X4_50")
ap.add_argument("--target-area", type=float, default=30000.0,
                help="luas marker (px^2) saat jarak engage — setel dari readout AREA")
args = ap.parse_args()

aruco = cv2.aruco.ArucoDetector(
    cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, args.dict)),
    cv2.aruco.DetectorParameters())
servo = VisualServo(target_area=args.target_area)

cap = cv2.VideoCapture(args.device)
if not cap.isOpened():
    sys.exit(f"Tidak bisa membuka webcam index {args.device}")

print("Tekan 'q' untuk keluar. Arahkan marker id=%d ke kamera." % args.id)
t_prev = time.time()
while True:
    ok, frame = cap.read()
    if not ok:
        break
    H, W = frame.shape[:2]
    cx0, cy0 = W // 2, H // 2
    cv2.drawMarker(frame, (cx0, cy0), (120, 120, 120), cv2.MARKER_CROSS, 30, 1)

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
            area = float(cv2.contourArea(pts.astype(np.float32)))
            o = servo.step(cx, cy, area, W, H, dt)
            cv2.polylines(frame, [pts.astype(int)], True, (0, 255, 0), 2)
            # panah command (sway horizontal, vert vertikal) dari pusat marker
            cv2.arrowedLine(frame, (int(cx), int(cy)),
                            (int(cx + o.sway * 3), int(cy - o.vert * 3)), (0, 200, 255), 3)
            col = (0, 255, 0) if o.aligned else (0, 200, 255)
            for k, txt in enumerate([
                f"AREA={area:6.0f} (target {args.target_area:.0f})",
                f"ex={o.ex:+.2f} ey={o.ey:+.2f} ea={o.ea:+.2f}",
                f"surge={o.surge:+5.1f} sway={o.sway:+5.1f} vert={o.vert:+5.1f}",
                "ALIGNED" if o.aligned else "servoing...",
            ]):
                cv2.putText(frame, txt, (10, 30 + 26 * k),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
            break
    if not found:
        servo.reset()
        cv2.putText(frame, f"cari marker id={args.id}...", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    cv2.imshow("APPROACH_HOOK visual servo test (q=keluar)", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
