#!/usr/bin/env python3
"""
tools/calibrate_camera.py — Kalibrasi kamera (checkerboard) → intrinsics utk PBVS (solvePnP).

Hasil disimpan .npz berisi: K (camera matrix), dist (distortion), image_size, rms.
Dipakai otomatis oleh VisionPipeline(calib_file=...) untuk menghitung pose marker.

PERSIAPAN
  - Cetak papan checkerboard (default 9x6 SUDUT-DALAM = papan 10x7 kotak), kotak ~25 mm,
    tempel di permukaan KAKU & RATA.  (generator: markhedleyjones.com/projects/calibration-checkerboard-collection)
  - pip install opencv-contrib-python numpy

PENTING UNTUK KAMERA DWE (bawah air):
  Refraksi air mengubah focal length efektif. Untuk akurasi jarak di kolam, kalibrasi
  DI DALAM AIR di balik housing/dome yang SAMA dengan saat misi (papan tahan air / di
  balik kaca akuarium). Kalibrasi di udara hanya pendekatan kasar.

PEMAKAIAN
  # Mode LIVE (webcam): kumpulkan ~15 pose beragam, lalu kalibrasi
  python tools/calibrate_camera.py --device 0 --cols 9 --rows 6 --square 25 \
         --out vision/calibration/dwe.npz
     SPACE = ambil frame (saat papan terdeteksi) · c = kalibrasi · q = keluar

  # Mode FOLDER (dari gambar tersimpan)
  python tools/calibrate_camera.py --from-folder calib_imgs --cols 9 --rows 6 --square 25 \
         --out vision/calibration/dwe.npz
"""
import argparse
import glob
import os
import sys

import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--device", type=int, default=0)
ap.add_argument("--from-folder", default=None, help="kalibrasi dari folder gambar (*.png/*.jpg)")
ap.add_argument("--cols", type=int, default=9, help="jumlah SUDUT-DALAM per baris")
ap.add_argument("--rows", type=int, default=6, help="jumlah SUDUT-DALAM per kolom")
ap.add_argument("--square", type=float, default=25.0, help="ukuran kotak (mm) — tak memengaruhi K")
ap.add_argument("--need", type=int, default=15, help="jumlah pose minimum (mode live)")
ap.add_argument("--out", default="vision/calibration/dwe.npz")
args = ap.parse_args()

PAT = (args.cols, args.rows)
CRIT = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)

# titik objek papan (z=0), satuan mm (skala tak memengaruhi K)
objp = np.zeros((args.rows * args.cols, 3), np.float32)
objp[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2) * args.square

obj_points, img_points = [], []
image_size = None


def try_frame(gray, vis=None):
    """Cari checkerboard; jika ketemu refine + return corners."""
    ok, corners = cv2.findChessboardCorners(
        gray, PAT, cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok:
        return None
    corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), CRIT)
    if vis is not None:
        cv2.drawChessboardCorners(vis, PAT, corners, ok)
    return corners


def calibrate_and_save():
    if len(obj_points) < 5:
        print(f"Terlalu sedikit pose ({len(obj_points)}) — butuh >=5 (ideal {args.need}).")
        return False
    rms, K, dist, _, _ = cv2.calibrateCamera(obj_points, img_points, image_size, None, None)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    np.savez(args.out, K=K, dist=dist, image_size=np.array(image_size), rms=rms)
    print(f"\n[OK] disimpan: {args.out}")
    print(f"  RMS reproj error = {rms:.3f} px (bagus bila < 0.5; <1.0 masih oke)")
    print(f"  K =\n{K}\n  dist = {dist.ravel()}")
    print(f"  Pakai: VisionPipeline(source='usb', calib_file='{args.out}', marker_length=<meter>)")
    return True


# ── Mode FOLDER ──────────────────────────────────────────────────────────────
if args.from_folder:
    files = sorted(glob.glob(os.path.join(args.from_folder, "*.png")) +
                   glob.glob(os.path.join(args.from_folder, "*.jpg")))
    if not files:
        sys.exit(f"Tidak ada gambar di {args.from_folder}")
    for f in files:
        img = cv2.imread(f)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        image_size = gray.shape[::-1]
        c = try_frame(gray)
        if c is not None:
            obj_points.append(objp.copy()); img_points.append(c)
            print(f"  ✓ {os.path.basename(f)}")
        else:
            print(f"  ✗ papan tak terdeteksi: {os.path.basename(f)}")
    calibrate_and_save()
    sys.exit(0)

# ── Mode LIVE ────────────────────────────────────────────────────────────────
cap = cv2.VideoCapture(args.device)
if not cap.isOpened():
    sys.exit(f"Tidak bisa membuka webcam index {args.device}")
print("LIVE: SPACE=ambil (saat papan terdeteksi) · c=kalibrasi · q=keluar")
while True:
    ok, frame = cap.read()
    if not ok:
        break
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    image_size = gray.shape[::-1]
    corners = try_frame(gray, vis=frame)
    cv2.putText(frame, f"pose terkumpul: {len(obj_points)}/{args.need}"
                + ("  [papan OK - SPACE]" if corners is not None else "  [cari papan]"),
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 0) if corners is not None else (0, 0, 255), 2)
    cv2.imshow("Kalibrasi (SPACE/c/q)", frame)
    k = cv2.waitKey(1) & 0xFF
    if k == ord('q'):
        break
    elif k == ord(' ') and corners is not None:
        obj_points.append(objp.copy()); img_points.append(corners)
        print(f"  + pose {len(obj_points)}")
    elif k == ord('c'):
        if calibrate_and_save():
            break
cap.release()
cv2.destroyAllWindows()
