#!/usr/bin/env python3
"""
tools/make_marker.py — Buat gambar marker ArUco untuk uji APPROACH_HOOK.

Marker hook default FSM = DICT_4X4_50, ID 7 (lihat HOOK_ARUCO_ID di mission5.py).

  pip install opencv-contrib-python numpy
  python tools/make_marker.py --id 7 --size 800 --out hook_marker_id7.png

Lalu CETAK:
  - ukuran fisik ~10-20 cm (makin besar makin mudah dideteksi dari jauh),
  - WAJIB ada border/area putih di sekeliling (quiet zone) — sudah ditambahkan,
  - tempel di permukaan KAKU & RATA (karton/akrilik), hindari kertas melengkung,
  - cetak matte (hindari glossy/silau).
"""
import argparse
import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--id", type=int, default=7)
ap.add_argument("--size", type=int, default=800, help="sisi marker (px)")
ap.add_argument("--dict", default="DICT_4X4_50")
ap.add_argument("--out", default="hook_marker_id7.png")
a = ap.parse_args()

d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, a.dict))
marker = cv2.aruco.generateImageMarker(d, a.id, a.size)

# quiet zone putih = 15% sisi (penting agar terdeteksi)
b = int(a.size * 0.15)
canvas = 255 * np.ones((a.size + 2 * b, a.size + 2 * b), dtype=np.uint8)
canvas[b:b + a.size, b:b + a.size] = marker
cv2.imwrite(a.out, canvas)
print(f"OK: {a.out}  ({a.dict}, id={a.id})  — cetak dgn quiet zone putih, permukaan kaku")
