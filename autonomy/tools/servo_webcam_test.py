#!/usr/bin/env python3
"""
tools/servo_webcam_test.py — Validasi closed-loop visual servo docking QR payload
pakai WEBCAM + QR cetak, TANPA ROV.

Dua mode:
  IBVS (default, tanpa kalibrasi): pakai error piksel + luas QR.
  PBVS (--calib dwe.npz):          pakai pose 3D (solvePnP) — jarak/sudut sebenarnya.

Gerakkan QR → command bereaksi:
  - QR KANAN  → sway +   ·  QR KECIL/JAUH → surge +  ·  QR BAWAH → vert -
  - di tengah & jarak engage → ALIGNED

  pip install opencv-python pyzbar numpy      # + apt install libzbar0
  # IBVS:
  python tools/servo_webcam_test.py --device 0
  # PBVS (setelah kalibrasi):
  python tools/servo_webcam_test.py --device 0 --calib vision/calibration/dwe.npz --qr-size 0.04

Tekan 'q' keluar. CATATAN: menguji LOGIKA & TANDA servo dgn kamera nyata; loop fisik
baru tertutup di kolam. Tanda terbalik → set invert_* di VisualServo/PoseServo
(lihat VERIFIKASI_ARDUSUB.md). Opsi --data menyaring QR tertentu (mis. 'HYDROSHIP').

Deteksi via decode_qr() (preprocessing berjenjang: CLAHE + upscale → QR jauh/cahaya
buruk lebih terbaca). Log CSV per-frame utk diagnosa deteksi:
  python tools/servo_webcam_test.py --device 0 --csv run.csv --cam-width 1280 --cam-height 720
Saat keluar dicetak detection-rate % + rentang jarak/area terdeteksi.
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
from vision.qr_detect import VisionPipeline, decode_qr   # _order_corners + decode_qr robust
from tools.detection_log import DetectionCsvLogger

ap = argparse.ArgumentParser()
ap.add_argument("--device", type=int, default=0)
ap.add_argument("--url", default=None,
                help="stream kamera ROV (mis. http://192.168.2.2:8080/stream) — override --device")
ap.add_argument("--data", default=None, help="hanya proses QR yang isinya memuat substring ini")
ap.add_argument("--target-area", type=float, default=3000.0, help="IBVS: luas engage (px^2)")
ap.add_argument("--calib", default=None, help="path .npz kalibrasi → mode PBVS")
ap.add_argument("--qr-size", type=float, default=0.04, help="PBVS: sisi QR payload (m)")
ap.add_argument("--target-dist", type=float, default=0.30, help="PBVS: jarak engage (m)")
ap.add_argument("--csv", default=None, help="tulis log deteksi per-frame ke file CSV ini")
ap.add_argument("--cam-width", type=int, default=1280, help="minta resolusi lebar kamera (bantu QR jauh)")
ap.add_argument("--cam-height", type=int, default=720, help="minta resolusi tinggi kamera")
args = ap.parse_args()

PBVS = bool(args.calib)
if PBVS:
    data = np.load(args.calib)
    K, DIST = data['K'], data['dist']
    servo = PoseServo(target_dist=args.target_dist)
    L = args.qr_size
    OBJP = np.array([[-L/2, L/2, 0], [L/2, L/2, 0], [L/2, -L/2, 0], [-L/2, -L/2, 0]], np.float32)
    print(f"Mode PBVS (solvePnP) — calib={args.calib}, QR={L} m")
else:
    servo = VisualServo(target_area=args.target_area)
    print("Mode IBVS (piksel) — beri --calib untuk PBVS")

src = args.url if args.url else args.device
cap = cv2.VideoCapture(src)
if not cap.isOpened():
    sys.exit(f"Tidak bisa membuka sumber kamera: {src}")
cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.cam_width)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.cam_height)

logger = DetectionCsvLogger(args.csv) if args.csv else None
t_prev = time.time()
try:
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        H, W = frame.shape[:2]
        cv2.drawMarker(frame, (W // 2, H // 2), (120, 120, 120), cv2.MARKER_CROSS, 30, 1)
        now = time.time(); dt = max(1e-3, now - t_prev); t_prev = now

        found = False
        row = {}
        for det in decode_qr(frame):
            qdata = det['data']                          # JSON payload: sudah strip, tak di-upper
            if args.data and args.data.upper() not in qdata.upper():
                continue
            pts = np.asarray(det['pts'], dtype=np.float32)
            if len(pts) < 4:
                continue
            found = True
            cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
            cv2.polylines(frame, [pts.astype(int)], True, (0, 255, 0), 2)

            if PBVS:
                img = VisionPipeline._order_corners(pts)
                okp, rvec, tvec = cv2.solvePnP(
                    OBJP, img, K, DIST,
                    flags=getattr(cv2, 'SOLVEPNP_IPPE_SQUARE', cv2.SOLVEPNP_ITERATIVE))
                if not okp:
                    break
                tvec = np.asarray(tvec, dtype=float).ravel()
                x, y, z = float(tvec[0]), float(tvec[1]), float(tvec[2])
                Rm, _ = cv2.Rodrigues(rvec)
                yaw = math.degrees(math.atan2(Rm[0, 2], Rm[2, 2]))
                o = servo.step(x, y, z, yaw, dt)
                dist = math.sqrt(x * x + y * y + z * z)
                cv2.drawFrameAxes(frame, K, DIST, rvec, tvec, args.qr_size * 0.5)
                lines = [f"QR:{qdata}",
                         f"x={x:+.2f} y={y:+.2f} z={z:.2f} m (target {args.target_dist} m)",
                         f"dist={dist:.2f} m  yaw={yaw:+.0f}",
                         f"surge={o.surge:+5.1f} sway={o.sway:+5.1f} vert={o.vert:+5.1f}"]
                row = dict(data=qdata, x=round(x, 4), y=round(y, 4), z=round(z, 4),
                           dist=round(dist, 4), yaw=round(yaw, 1))
            else:
                area = float(cv2.contourArea(pts))
                o = servo.step(cx, cy, area, W, H, dt)
                lines = [f"QR:{qdata}",
                         f"AREA={area:6.0f} (target {args.target_area:.0f})",
                         f"ex={o.ex:+.2f} ey={o.ey:+.2f} ea={o.ea:+.2f}",
                         f"surge={o.surge:+5.1f} sway={o.sway:+5.1f} vert={o.vert:+5.1f}"]
                row = dict(data=qdata, cx=round(cx, 1), cy=round(cy, 1), area=round(area, 1),
                           ex=round(o.ex, 4), ey=round(o.ey, 4), ea=round(o.ea, 4))
            row.update(surge=round(o.surge, 2), sway=round(o.sway, 2),
                       vert=round(o.vert, 2), aligned=o.aligned)

            cv2.arrowedLine(frame, (int(cx), int(cy)),
                            (int(cx + o.sway * 3), int(cy - o.vert * 3)), (0, 200, 255), 3)
            col = (0, 255, 0) if o.aligned else (0, 200, 255)
            for k, txt in enumerate(lines + ["ALIGNED" if o.aligned else "servoing..."]):
                cv2.putText(frame, txt, (10, 30 + 26 * k),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, col, 2)
            break
        if not found:
            servo.reset()
            cv2.putText(frame, "cari QR payload...", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        if logger:
            logger.log(detected=found, **row)

        cv2.imshow("Docking QR servo test (q=keluar)", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
finally:
    cap.release()
    cv2.destroyAllWindows()
    if logger:
        logger.close()
