#!/usr/bin/env python3
"""
tools/rescale_calib.py — Skalakan kalibrasi .npz ke resolusi stream baru.

KAPAN VALID DIPAKAI (baca dulu, jangan asal jalankan): HANYA bila resolusi
baru berasal dari SENSOR & FOV YANG SAMA persis dengan saat kalibrasi --
mis. kamera UVC yang menawarkan 1280x720 & 1920x1080 sama-sama 16:9 dari
satu sensor (mode kecil = downscale ISP/encoder, BUKAN crop area sensor
berbeda). Kalau resolusi baru datang dari crop FOV berbeda (area sensor
lebih sempit/lebar), K TIDAK BOLEH cuma diskalakan -- fx/fy relatif thd cx/cy
akan salah dan PBVS mengira jarak QR berbeda dari sebenarnya (kelas bug yang
sama dgn insiden kalibrasi 22 Agu — lihat qr_detect._verify_calib_size()).

Kenapa scaling matematis ini VALID utk kasus downscale-sama-FOV: fx, fy, cx,
cy semuanya dalam satuan piksel, dan piksel bertambah proporsional dgn
resolusi pd downscale seragam -- jadi K_baru = K_lama * skala (kecuali baris
[2] tetap [0,0,1]). Koefisien distorsi (dist) TAK BERUBAH -- ia dinyatakan
relatif thd koordinat piksel yang SUDAH dinormalisasi oleh K, jadi tak
tergantung resolusi selama FOV sama.

INI APROKSIMASI, bukan pengganti kalibrasi checkerboard penuh -- dipakai saat
recapture fisik belum sempat dilakukan (mis. ganti resolusi live di lapangan).
Jalankan tools/select_calib_frames.py + calibrate_camera.py ulang di resolusi
baru begitu ada kesempatan, utk verifikasi RMS sungguhan di piksel asli
(bukan hasil skala).

PEMAKAIAN
  python tools/rescale_calib.py vision/calibration/bottom.npz \
         --to 1920x1080 --out vision/calibration/bottom.npz
  # (in-place aman: baca dulu ke memori sebelum overwrite file yg sama)
"""
import argparse
import os

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("calib_file", help=".npz kalibrasi sumber")
    ap.add_argument("--to", required=True, help="resolusi tujuan, mis. 1920x1080")
    ap.add_argument("--out", required=True, help="path .npz keluaran")
    args = ap.parse_args()

    new_w, new_h = (int(v) for v in args.to.lower().split("x"))
    data = dict(np.load(args.calib_file))
    old_w, old_h = (int(v) for v in data["image_size"])

    sx, sy = new_w / old_w, new_h / old_h
    if abs(sx - sy) > 1e-3:
        raise SystemExit(
            f"[GAGAL] rasio skala x ({sx:.4f}) != y ({sy:.4f}) -- {old_w}x{old_h} -> "
            f"{new_w}x{new_h} BUKAN downscale aspect-ratio sama, jangan diskalakan "
            f"(lihat docstring modul ini). Kalibrasi ulang sungguhan diperlukan.")

    K = data["K"].copy()
    K[0, 0] *= sx   # fx
    K[1, 1] *= sy   # fy
    K[0, 2] *= sx   # cx
    K[1, 2] *= sy   # cy
    data["K"] = K
    data["image_size"] = np.array([new_w, new_h])
    # dist TAK diskalakan (lihat docstring) & rms TAK berlaku lagi di resolusi
    # baru (RMS lama diukur di piksel lama) -- tandai sbg estimasi, bukan hasil ukur.
    data["rms"] = np.array(-1.0)   # -1 = "diskalakan, belum diverifikasi ulang"

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    np.savez(args.out, **data)
    print(f"[OK] {args.calib_file} ({old_w}x{old_h}) -> {args.out} ({new_w}x{new_h})")
    print(f"  skala = {sx:.4f}x")
    print(f"  K baru =\n{K}")
    print(f"  dist (tak berubah) = {data['dist'].ravel()}")
    print("  rms = -1 (diskalakan, BELUM diverifikasi via checkerboard di resolusi baru --")
    print("        jalankan tools/calibrate_camera.py --from-folder ... saat sempat)")


if __name__ == "__main__":
    main()
