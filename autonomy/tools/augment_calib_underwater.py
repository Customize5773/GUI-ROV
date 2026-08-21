#!/usr/bin/env python3
"""
tools/augment_calib_underwater.py — Perluas dataset kalibrasi checkerboard dengan
simulasi bawah air, agar dwe.npz lebih siap untuk kondisi kolam KKI 2026 (0.9m).

Memakai ulang mesin simulasi tests/underwater_sim.py (dibangun utk uji ketahanan QR)
langsung pada foto checkerboard di Cleaned/ — bukan reimplementasi baru.

CATATAN RIAK: rentang amp_x/amp_y di underwater_sim._RANGES dikalibrasi utk modul QR,
bukan sudut checkerboard. Pada depth 'deep' riak bisa melengkungkan grid cukup jauh utk
menggagalkan findChessboardCorners atau -- lebih buruk -- lolos deteksi tapi membiaskan
hasil calibrateCamera. --ripple-scale meredam amplitudo sebelum diterapkan.

PEMAKAIAN
  python tools/augment_calib_underwater.py \
      --src Cleaned --out Cleaned_underwater --cols 9 --rows 6
"""
import argparse
import csv
import glob
import os
import sys

import cv2
import numpy as np

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

from tests.underwater_sim import params_for_depth, apply_color_attenuation, \
    apply_backscatter, apply_ripple_distortion, apply_blur, DEPTH_ANCHORS

ap = argparse.ArgumentParser()
ap.add_argument("--src", default="Cleaned", help="folder foto checkerboard dry-land")
ap.add_argument("--out", default="Cleaned_underwater", help="folder output tersimulasi")
ap.add_argument("--depths", nargs="+", default=list(DEPTH_ANCHORS), choices=list(DEPTH_ANCHORS))
ap.add_argument("--seeds-per-depth", type=int, default=3)
ap.add_argument("--ripple-scale", type=float, default=0.35,
                help="faktor peredam amplitudo riak (1.0 = rentang asli, tuning QR)")
ap.add_argument("--cols", type=int, default=9, help="jumlah sudut-dalam per baris (cocokkan calibrate_camera.py)")
ap.add_argument("--rows", type=int, default=6, help="jumlah sudut-dalam per kolom")
ap.add_argument("--min-detect-rate", type=float, default=0.85)
args = ap.parse_args()

PAT = (args.cols, args.rows)
CB_FLAGS = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE


def render(img, depth, seed):
    rng = np.random.default_rng(seed)
    p = params_for_depth(depth, rng=rng, jitter=0.08)
    p['amp_x'] *= args.ripple_scale
    p['amp_y'] *= args.ripple_scale

    out = apply_color_attenuation(img, p['red_gain'], p['green_gain'], p['blue_gain'])
    out = apply_backscatter(out, p['black_point'], p['white_point'], p['haze_bgr'], p['haze_alpha'])
    out = apply_ripple_distortion(out, p['amp_x'], p['amp_y'], p['freq_x'], p['freq_y'],
                                   p['phase_x'], p['phase_y'])
    out = apply_blur(out, p['ksize'], p['sigma'])
    return out, p


def main():
    files = sorted(glob.glob(os.path.join(args.src, "*.jpg")) +
                    glob.glob(os.path.join(args.src, "*.jpeg")) +
                    glob.glob(os.path.join(args.src, "*.png")))
    if not files:
        sys.exit(f"Tidak ada gambar di {args.src}")

    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "manifest.csv")
    detected, total = 0, 0
    per_depth = {d: [0, 0] for d in args.depths}

    with open(manifest_path, "w", newline="") as mf:
        writer = csv.writer(mf)
        writer.writerow(["source", "depth", "seed", "out_file", "detected", "params"])

        for f in files:
            img = cv2.imread(f)
            stem = os.path.splitext(os.path.basename(f))[0]
            for depth in args.depths:
                for seed in range(args.seeds_per_depth):
                    out_img, p = render(img, depth, seed)
                    out_name = f"{stem}__{depth}_s{seed}.jpg"
                    out_path = os.path.join(args.out, out_name)
                    cv2.imwrite(out_path, out_img)

                    gray = cv2.cvtColor(out_img, cv2.COLOR_BGR2GRAY)
                    ok, _ = cv2.findChessboardCorners(gray, PAT, CB_FLAGS)
                    total += 1
                    per_depth[depth][1] += 1
                    if ok:
                        detected += 1
                        per_depth[depth][0] += 1

                    writer.writerow([os.path.basename(f), depth, seed, out_name, ok, p])

    rate = detected / total if total else 0.0
    print(f"[OK] {total} frame ditulis ke {args.out}/ ; deteksi papan {detected}/{total} ({rate:.1%})")
    for d, (ok_n, n) in per_depth.items():
        print(f"  {d:8s}: {ok_n}/{n} ({ok_n / n:.1%})")

    assert rate >= args.min_detect_rate, (
        f"Tingkat deteksi {rate:.1%} < ambang {args.min_detect_rate:.0%} -- dataset ini "
        f"akan menghasilkan kalibrasi buruk. Coba turunkan --ripple-scale atau perbesar "
        f"toleransi --min-detect-rate.")


if __name__ == "__main__":
    main()
