#!/usr/bin/env python3
"""
tools/select_calib_frames.py — Kurasi subset tajam & beragam dari folder besar
frame kalibrasi (mis. hasil ekstrak video), siap dipakai calibrate_camera.py --from-folder.

Kenapa perlu: dataset besar (ribuan frame video checkerboard) kebanyakan REDUNDAN
(frame berurutan nyaris identik) & sebagian BURAM (motion blur ekstraksi video).
Memakai semua mentah-mentah ke calibrateCamera tak menambah akurasi, cuma
memperlambat & bisa membiaskan hasil ke pose yang paling sering terulang.
Tool ini reuse deteksi papan yang sama seperti calibrate_camera.py (try_frame:
findChessboardCorners + cornerSubPix), lalu pilih subset dgn dua kriteria:

  1. TERTAJAM dulu — varians Laplacian DI DALAM bounding box papan terdeteksi
     saja (bukan seluruh frame: background bertekstur tinggi di kolam bikin
     skor frame-penuh ngaco, nyaris selalu "tajam" walau papannya sendiri buram).
  2. BERAGAM pose — dilewati bila centroid papannya terlalu dekat (piksel) dgn
     yang sudah terpilih (reuse ide `--move` di calibrate_camera.py mode --auto,
     lihat _centroid()/`moved` di sana), supaya subset akhir tak didominasi
     banyak frame video nyaris kembar.

PEMAKAIAN
  python tools/select_calib_frames.py --src frame_besar_d1 frame_besar_d2 \
         --out calib_imgs_v3 --cols 10 --rows 7 --n 60 --move 40
  # lanjut ke kalibrasi sungguhan (tool yang sudah ada, tak diubah):
  python tools/calibrate_camera.py --from-folder calib_imgs_v3 \
         --cols 10 --rows 7 --square 25 --out vision/calibration/dwe_v3.npz
"""
import argparse
import glob
import os
import shutil

import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True, nargs="+", help="folder frame mentah (bisa lebih dari satu)")
ap.add_argument("--out", required=True, help="folder output frame terkurasi")
ap.add_argument("--cols", type=int, default=9, help="jumlah SUDUT-DALAM per baris (cocokkan papan)")
ap.add_argument("--rows", type=int, default=6, help="jumlah SUDUT-DALAM per kolom")
ap.add_argument("--n", type=int, default=60, help="target jumlah frame terpilih")
ap.add_argument("--move", type=float, default=40.0,
                help="jarak minimum (px) centroid papan dari semua frame terpilih sebelumnya")
args = ap.parse_args()

PAT = (args.cols, args.rows)
CRIT = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)
FLAGS = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE


def find_board(gray):
    """Sama seperti try_frame() di calibrate_camera.py: deteksi + refine sudut."""
    ok, corners = cv2.findChessboardCorners(gray, PAT, FLAGS)
    if not ok:
        return None
    return cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), CRIT)


def sharpness(gray, corners):
    """Varians Laplacian DI DALAM bounding box papan saja (lihat docstring modul)."""
    pts = corners.reshape(-1, 2)
    x0, y0 = pts.min(axis=0).astype(int)
    x1, y1 = pts.max(axis=0).astype(int)
    pad = 10
    h, w = gray.shape[:2]
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(w, x1 + pad), min(h, y1 + pad)
    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return 0.0
    return float(cv2.Laplacian(crop, cv2.CV_64F).var())


def main():
    files = []
    for src in args.src:
        files += sorted(glob.glob(os.path.join(src, "*.jpg")) +
                        glob.glob(os.path.join(src, "*.jpeg")) +
                        glob.glob(os.path.join(src, "*.png")))
    if not files:
        raise SystemExit(f"Tidak ada gambar di {args.src}")

    print(f"[1/3] memindai {len(files)} frame, papan {PAT[0]}x{PAT[1]} sudut-dalam...")
    candidates = []
    for i, f in enumerate(files):
        img = cv2.imread(f)
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        corners = find_board(gray)
        if corners is None:
            continue
        centroid = corners.reshape(-1, 2).mean(axis=0)
        score = sharpness(gray, corners)
        candidates.append((f, centroid, score))
        if (i + 1) % 200 == 0:
            print(f"  ...{i + 1}/{len(files)} discan, {len(candidates)} papan ketemu")

    print(f"[2/3] papan terdeteksi di {len(candidates)}/{len(files)} frame "
          f"({len(candidates) / len(files):.0%})")
    if not candidates:
        raise SystemExit("Tak ada papan terdeteksi sama sekali -- cek --cols/--rows.")

    # Greedy: urut tertajam dulu, ambil kalau centroidnya cukup jauh dari yang
    # sudah terpilih (diversitas pose, reuse ide --move di calibrate_camera.py).
    candidates.sort(key=lambda c: c[2], reverse=True)
    selected = []
    for f, centroid, score in candidates:
        if len(selected) >= args.n:
            break
        if all(np.linalg.norm(centroid - c[1]) > args.move for c in selected):
            selected.append((f, centroid, score))

    print(f"[3/3] {len(selected)} frame terpilih (target {args.n}) -> {args.out}")
    os.makedirs(args.out, exist_ok=True)
    for i, (f, _centroid, _score) in enumerate(selected):
        ext = os.path.splitext(f)[1]
        dst = os.path.join(args.out, f"sel_{i:03d}{ext}")
        shutil.copy(f, dst)
    print(f"[OK] {len(selected)} frame disalin ke {args.out}/")
    print(f"  Lanjut: python tools/calibrate_camera.py --from-folder {args.out} "
          f"--cols {args.cols} --rows {args.rows} --square <mm> --out vision/calibration/<nama>.npz")


if __name__ == "__main__":
    main()
