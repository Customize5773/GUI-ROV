#!/usr/bin/env python3
"""
tools/probe_stream.py — Tahap 1 validasi runtime: BUKA STREAM SECARA PASIF.

Hanya MEMBACA. Tak pernah membuka socket command, tak menyentuh CommandSender,
tak pernah arm/disarm. Aman dijalankan saat ROV di air maupun di darat.

Yang dilaporkan (metrik Tahap 1):
  • resolusi aktual stream  — DAN kecocokannya dgn `image_size` file kalibrasi.
    Ini yang paling penting: K dari resolusi lain bikin jarak z ~ fx·W/w_px
    meleset berlipat, DIAM-DIAM tanpa error (kelas bug 22 Agu 2026, lihat
    qr_detect._verify_calib_size dan gate resolusi di hook_localization).
  • FPS terukur (bukan yang diminta), p50/p95 interval antar-frame
  • usia frame: selisih wall-clock saat frame diterima vs saat loop mulai
  • frame gagal baca / dropout
  • CPU & RSS proses ini (beban sisi laptop)

CPU/RAM/suhu Raspberry Pi TIDAK diambil dari sini — jalankan di Pi sendiri:
    vcgencmd measure_temp ; top -bn1 | head -5

PEMAKAIAN
    python3 tools/probe_stream.py http://192.168.2.2:8081/stream \
            --calib vision/calibration/wall.npz --seconds 20

    # sekalian hitung berapa frame yang menghasilkan deteksi hook (masih pasif):
    python3 tools/probe_stream.py <url> --calib <npz> --detect-hook
"""
import argparse
import os
import resource
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser(description='Probe stream kamera — PASIF, tanpa command')
    ap.add_argument('url', help='URL stream (mis. http://192.168.2.2:8081/stream)')
    ap.add_argument('--calib', default=None, help='.npz kalibrasi utk cek kecocokan resolusi')
    ap.add_argument('--seconds', type=float, default=15.0, help='durasi pengamatan')
    ap.add_argument('--detect-hook', action='store_true',
                    help='hitung detection rate hook (tetap pasif, tak ada command)')
    args = ap.parse_args()

    import cv2
    import numpy as np

    calib_size = None
    if args.calib:
        d = np.load(args.calib)
        calib_size = tuple(int(v) for v in d['image_size']) if 'image_size' in d else None
        print(f"kalibrasi   : {args.calib}  fx={float(d['K'][0, 0]):.1f}  image_size={calib_size}")

    print(f"membuka     : {args.url}")
    t_open = time.monotonic()
    cap = cv2.VideoCapture(args.url)
    if not cap.isOpened():
        raise SystemExit(f"GAGAL membuka stream: {args.url}")
    print(f"terbuka     : {time.monotonic() - t_open:.2f} s")

    stamps, gagal, deteksi, ukuran = [], 0, 0, None
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.seconds:
        t_baca = time.monotonic()
        ok, frame = cap.read()
        if not ok or frame is None:
            gagal += 1
            time.sleep(0.05)
            continue
        stamps.append((t_baca, time.monotonic() - t_baca))
        ukuran = (frame.shape[1], frame.shape[0])
        if args.detect_hook:
            from vision.hook_detect import detect_hook
            if detect_hook(frame) is not None:
                deteksi += 1
    cap.release()

    n = len(stamps)
    if n < 2:
        raise SystemExit(f"cuma {n} frame terbaca dalam {args.seconds}s — stream tak sehat")
    durasi = stamps[-1][0] - stamps[0][0]
    gaps = [stamps[i][0] - stamps[i - 1][0] for i in range(1, n)]
    baca = sorted(s[1] for s in stamps)
    ru = resource.getrusage(resource.RUSAGE_SELF)

    print("\n── Hasil (PASIF, tak satu pun command dikirim) ──")
    print(f"frame        : {n} dalam {durasi:.1f}s  (gagal baca: {gagal})")
    print(f"FPS terukur  : {n / durasi:.2f}")
    print(f"interval     : p50 {1000 * statistics.median(gaps):.0f} ms  "
          f"p95 {1000 * sorted(gaps)[int(0.95 * len(gaps))]:.0f} ms  "
          f"max {1000 * max(gaps):.0f} ms")
    print(f"latensi baca : p50 {1000 * statistics.median(baca):.0f} ms  "
          f"p95 {1000 * baca[int(0.95 * len(baca))]:.0f} ms")
    print(f"resolusi     : {ukuran[0]}x{ukuran[1]}")
    print(f"CPU proses   : {ru.ru_utime + ru.ru_stime:.1f}s  RSS {ru.ru_maxrss / 1024:.0f} MB")
    if args.detect_hook:
        print(f"deteksi hook : {deteksi}/{n} frame ({100.0 * deteksi / n:.1f}%)")

    if calib_size and tuple(calib_size) != ukuran:
        print(f"\n⚠ RESOLUSI TAK COCOK: kalibrasi {calib_size[0]}x{calib_size[1]} vs "
              f"stream {ukuran[0]}x{ukuran[1]}.\n"
              "  K/dist TIDAK valid pada resolusi ini — hook_localization akan menolak "
              "(status 'rejected').\n"
              "  Perbaiki dgn kalibrasi ulang pada resolusi stream, atau samakan "
              "resolusi kamera dgn saat kalibrasi.")
        return 1
    if calib_size:
        print("\n✓ resolusi stream COCOK dgn kalibrasi")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
