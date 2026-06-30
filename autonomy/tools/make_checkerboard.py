#!/usr/bin/env python3
"""
tools/make_checkerboard.py — Buat papan catur (checkerboard) untuk kalibrasi kamera.

  pip install opencv-contrib-python numpy
  python tools/make_checkerboard.py --cols 9 --rows 6 --square 30 --out checkerboard_9x6.png

cols/rows = jumlah SUDUT-DALAM (inner corners), BUKAN jumlah kotak.
  → papan 10x7 kotak = 9x6 inner corners (default, cocok utk calibrate_camera.py).

Cetak di A4, ukur sisi 1 kotak yang TERCETAK (mm) dengan penggaris, lalu pakai nilai
itu sebagai --square saat kalibrasi (penting untuk skala jarak yang benar). Tempel
RATA di papan kaku (jangan melengkung).
"""
import argparse
import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--cols", type=int, default=9, help="inner corners horizontal")
ap.add_argument("--rows", type=int, default=6, help="inner corners vertikal")
ap.add_argument("--square", type=int, default=80, help="sisi kotak dalam piksel gambar")
ap.add_argument("--out", default="checkerboard_9x6.png")
a = ap.parse_args()

nx, ny = a.cols + 1, a.rows + 1          # jumlah kotak = inner + 1
s = a.square
img = np.zeros((ny * s, nx * s), dtype=np.uint8)
for j in range(ny):
    for i in range(nx):
        if (i + j) % 2 == 0:
            img[j * s:(j + 1) * s, i * s:(i + 1) * s] = 255
border = s
canvas = 255 * np.ones((img.shape[0] + 2 * border, img.shape[1] + 2 * border), np.uint8)
canvas[border:border + img.shape[0], border:border + img.shape[1]] = img
cv2.imwrite(a.out, canvas)
print(f"OK: {a.out}  ({a.cols}x{a.rows} inner corners, {nx}x{ny} kotak)")
print("Cetak, lalu UKUR sisi 1 kotak tercetak (mm) untuk --square saat kalibrasi.")
