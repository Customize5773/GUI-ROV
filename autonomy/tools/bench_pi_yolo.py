#!/usr/bin/env python3
"""Ukur apakah YOLO benar-benar muat di Raspberry Pi 4 ini. JALANKAN DI PI.

Ini gerbang GO/NO-GO sebelum mempercayai jalur vision Pi di kolam, bukan sekadar
info. Yang diputuskan: imgsz mana yang dipakai, dan apakah arsitektur ini muat
sama sekali.

Ambang berasal dari gate kesegaran yang SUDAH ADA di rov_agent.py — bukan angka
karangan:
  * QR   >= 3 Hz  (QR_VISION_MAX_AGE = 0.5 s, perlu margin di atas 2 Hz)
  * HOOK >= 2 Hz  (gate hook 1.0 s)
Di bawah itu FSM akan membuang hasilnya sebagai basi dan ROV berhenti bergerak
tanpa satu pun pesan error — kegagalan paling mahal yang bisa terjadi di kolam.

WAJIB dijalankan saat `rov-agent` AKTIF. Angka di Pi menganggur menipu: yang
diperebutkan justru CPU yang sama dengan loop MAVLink.

    python3 tools/bench_pi_yolo.py                       # semua varian .onnx
    python3 tools/bench_pi_yolo.py --camera http://127.0.0.1:8081/stream

CATATAN PENTING soal imgsz: graf ONNX hasil export Ultralytics BERUKURAN TETAP.
imgsz bukan knob runtime — ia dipilih saat EXPORT, jadi tiap ukuran adalah file
tersendiri (best_pose.onnx = 640, best_pose_320.onnx = 320, dst). Skrip ini
menguji setiap file yang ada dan membaca ukurannya dari model itu sendiri.
Butuh ukuran lain? Export di laptop:

    yolo export model=autonomy/vision/best_pose.pt format=onnx imgsz=320 simplify=True

Kalau bahkan 320 tidak lolos: JANGAN diam-diam menurunkan target. Naikkan ke
ncnn (pip install ncnn, ~8 MB, 2-3x lebih cepat di ARM) atau laporkan bahwa
arsitektur ini tidak muat di Pi 4.
"""

import argparse
import glob
import os
import statistics
import sys
import time

# (label, pola berkas bobot, ambang Hz) — ambang = gate kesegaran rov_agent.py
MODELS = [
    ('HOOK (CAM WALL)', 'best_pose*.onnx', 2.0),
    ('QR   (CAM BOTTOM)', 'best_new*.onnx', 3.0),
]


def _load_frames(camera, count):
    """Ambil frame NYATA dari uStreamer: JPEG decode di Pi ikut terhitung."""
    import cv2
    capture = cv2.VideoCapture(camera)
    if not capture.isOpened():
        return None
    frames = []
    deadline = time.time() + 20.0
    while len(frames) < count and time.time() < deadline:
        ok, frame = capture.read()
        if ok:
            frames.append(frame)
    capture.release()
    return frames or None


def _synthetic(count):
    import numpy as np
    rng = np.random.default_rng(0)
    return [rng.integers(0, 255, (720, 1280, 3), dtype='uint8') for _ in range(count)]


def _cpu_snapshot():
    try:
        with open('/proc/stat') as handle:
            parts = [float(v) for v in handle.readline().split()[1:]]
        return sum(parts), parts[3]
    except (OSError, IndexError, ValueError):
        return None


def _cpu_percent(before, after):
    if not before or not after:
        return None
    d_total, d_idle = after[0] - before[0], after[1] - before[1]
    return None if d_total <= 0 else round(100.0 * (1.0 - d_idle / d_total), 1)


def main():
    parser = argparse.ArgumentParser(description='Benchmark YOLO di Raspberry Pi')
    parser.add_argument('--camera', default='http://127.0.0.1:8081/stream')
    parser.add_argument('--frames', type=int, default=20)
    parser.add_argument('--synthetic', action='store_true',
                        help='pakai frame acak bila kamera tidak tersedia (angka '
                             'inferensi tetap sah, waktu decode JPEG tidak terhitung)')
    args = parser.parse_args()

    sys.path.insert(0, '.')
    try:
        from vision.yolo_hook import OnnxHookDetector
    except ImportError:
        sys.path.insert(0, 'autonomy')
        from vision.yolo_hook import OnnxHookDetector

    frames = None if args.synthetic else _load_frames(args.camera, args.frames)
    if frames is None:
        if not args.synthetic:
            print(f'[!] kamera {args.camera} tidak terbaca — pakai --synthetic '
                  f'kalau memang ingin mengukur tanpa kamera')
            return 2
        frames = _synthetic(args.frames)
    print(f'frame: {len(frames)} @ {frames[0].shape[1]}x{frames[0].shape[0]}'
          f'{" (sintetis)" if args.synthetic else f" dari {args.camera}"}\n')

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vision_dir = (os.path.join(here, 'vision') if os.path.isdir(os.path.join(here, 'vision'))
                  else 'vision')
    verdict = []
    for label, pattern, floor_hz in MODELS:
        print(f'== {label} — {pattern} (butuh >= {floor_hz:.0f} Hz) ==')
        found = sorted(glob.glob(os.path.join(vision_dir, pattern)))
        if not found:
            print(f'  tidak ada berkas cocok di {vision_dir} — export dulu di laptop')
        for weights in found:
            name = os.path.basename(weights)
            try:
                detector = OnnxHookDetector(weights, conf=0.25)
            except Exception as exc:
                print(f'  {name:<24} GAGAL memuat — {str(exc).splitlines()[-1][:70]}')
                continue
            detector.detect(frames[0])          # warm-up, jangan ikut diukur
            cpu_before = _cpu_snapshot()
            durations = []
            for frame in frames:
                started = time.perf_counter()
                detector.detect(frame)
                durations.append((time.perf_counter() - started) * 1000.0)
            cpu = _cpu_percent(cpu_before, _cpu_snapshot())
            median = statistics.median(durations)
            hz = 1000.0 / median
            ok = hz >= floor_hz
            verdict.append((label, name, detector.imgsz, hz, ok))
            print(f'  {name:<24} imgsz {detector.imgsz:>3}  median {median:7.1f} ms  '
                  f'-> {hz:5.2f} Hz  p95 {sorted(durations)[int(len(durations) * 0.95) - 1]:7.1f} ms  '
                  f'cpu {cpu if cpu is not None else "?"}%  {"LULUS" if ok else "GAGAL"}')
        print()

    print('== Kesimpulan ==')
    for label, _pattern, floor_hz in MODELS:
        passing = [(sz, nm) for lbl, nm, sz, _hz, ok in verdict if lbl == label and ok]
        if passing:
            best = max(passing)
            print(f'  {label}: pakai {best[1]} (imgsz {best[0]}, >= {floor_hz:.0f} Hz terpenuhi)')
        else:
            print(f'  {label}: TIDAK ADA imgsz yang lolos {floor_hz:.0f} Hz — '
                  f'coba ncnn, atau laporkan bahwa Pi 4 tidak sanggup. '
                  f'JANGAN turunkan gate kesegaran diam-diam.')
    print('\nCatatan: jalankan ulang saat rov-agent AKTIF kalau tadi belum — '
          'angka di Pi menganggur tidak mewakili kondisi trial.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
