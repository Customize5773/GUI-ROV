#!/usr/bin/env python3
"""
tools/extract_video_frames.py — Ekstrak frame dari video (mis. rekaman checkerboard)
ke folder gambar, agar bisa dipakai calibrate_camera.py --from-folder.

PEMAKAIAN
  python tools/extract_video_frames.py --video a.mp4 b.mp4 --out-dir frames_extracted --every 10
"""
import argparse
import os

import cv2

ap = argparse.ArgumentParser()
ap.add_argument("--video", nargs="+", required=True, help="path video (bisa lebih dari satu)")
ap.add_argument("--out-dir", required=True)
ap.add_argument("--every", type=int, default=10, help="ambil 1 frame tiap N frame")
args = ap.parse_args()

os.makedirs(args.out_dir, exist_ok=True)

total_saved = 0
for vi, vpath in enumerate(args.video):
    cap = cv2.VideoCapture(vpath)
    if not cap.isOpened():
        print(f"[!] gagal buka: {vpath}")
        continue
    idx = 0
    saved = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % args.every == 0:
            fname = os.path.join(args.out_dir, f"vid{vi}_frame{idx:05d}.png")
            cv2.imwrite(fname, frame)
            saved += 1
        idx += 1
    cap.release()
    print(f"  {os.path.basename(vpath)}: {saved} frame disimpan (dari {idx} total)")
    total_saved += saved

print(f"[OK] total {total_saved} frame di {args.out_dir}")
