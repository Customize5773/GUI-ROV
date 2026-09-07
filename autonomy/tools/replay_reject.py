#!/usr/bin/env python3
"""
tools/replay_reject.py — Jalankan detektor hook SUNGGUHAN lewat gate SUNGGUHAN
dan laporkan kenapa deteksi ditolak.

Ini uji darat untuk Tahap 1: tanpa ROV, tanpa kolam, tanpa MAVLink. Yang dipakai
adalah kode yang sama persis dengan yang jalan di air —
`vision/yolo_hook.make_detector`, `rov_agent._validate_hook_vision`, lalu gate
FSM `_fresh_external_yolo` / `_hook_skeleton`. Tak satu pun ambang ditiru ulang
di sini; kalau angkanya berubah di sana, alat ini ikut berubah sendiri.

    python autonomy/tools/replay_reject.py autonomy/tests/fixtures/real_hard_cases/*.png
    python autonomy/tools/replay_reject.py frame.png --weights autonomy/vision/best_pose.onnx
    python autonomy/tools/replay_reject.py --stream http://192.168.2.2:8080/?action=stream -n 60

Yang dijawab: pada frame hook yang benar-benar sulit, gate MANA yang membuang
deteksi, dan berapa selisihnya dari ambang. Overlay bbox di GUI tidak menjawab
itu — bbox digambar dari jalur telemetri yang tak melewati gate ini sama sekali.
"""
import argparse
import collections
import glob
import os
import sys

import cv2

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_AUTONOMY)
for _path in (_AUTONOMY, _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import rov_agent                                  # noqa: E402
from fsm import mission5                          # noqa: E402
from vision.yolo_hook import make_detector        # noqa: E402

DEFAULT_WEIGHTS = os.path.join(_AUTONOMY, 'vision', 'best_pose.pt')


class _GateProbe:
    """Gate FSM tanpa FSM.

    Mission5FSM lengkap menuntut kamera, MAVLink, dan pipeline vision — tak satu
    pun relevan untuk menanyai gate. Metodenya dipinjam APA ADANYA (bukan
    disalin) supaya alat ini tidak bisa ikut benar saat gate aslinya salah.
    """
    _reject = mission5.Mission5FSM._reject
    _fresh_external_yolo = mission5.Mission5FSM._fresh_external_yolo
    _hook_skeleton = mission5.Mission5FSM._hook_skeleton
    _hook_tip = mission5.Mission5FSM._hook_tip

    def __init__(self):
        self.telemetry_out = {'reject_reason': None, 'lock_progress': None}
        self._yolo_source = lambda: None


def _worker_record(detection):
    """Amplop yang PERSIS dikirim hook_vision_worker.emit() ke rov_agent."""
    return {
        'status': 'ok',
        'method': 'yolov8',
        'confidence': detection.get('confidence'),
        'bbox': list(detection.get('bbox')),
        'keypoints': detection.get('keypoints'),
        'frame_w': detection.get('frame_w'),
        'frame_h': detection.get('frame_h'),
    }


def _probe(detector, frame, nama, verbose):
    """Satu frame -> (tahap_gagal, alasan). tahap_gagal None = lolos semua gate."""
    detection = detector.detect(frame)
    if detection is None:
        return 'detector', 'no_detection'

    record = _worker_record(detection)

    # Gate 1: batas jaringan laptop/Pi.
    rov_agent.last_vision_reject.update(hook=None, qr=None)
    clean = rov_agent._validate_hook_vision(record)
    if clean is None:
        return 'rov_agent', rov_agent.last_vision_reject['hook']

    # Gate 2: confidence FSM.
    probe = _GateProbe()
    det = probe._fresh_external_yolo(clean)
    if det is None:
        return 'fsm_conf', probe.telemetry_out['reject_reason']

    # Gate 3: skeleton / titik bidik servo.
    if probe._hook_tip(det) is None:
        return 'fsm_skeleton', probe.telemetry_out['reject_reason']

    if verbose:
        tip = probe._hook_skeleton(det)[5]
        print(f"    {nama}: LOLOS  conf={clean['confidence']:.3f}  "
              f"tip(id5)=({tip[0]:.1f},{tip[1]:.1f})")
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('images', nargs='*', help='file gambar (boleh glob)')
    ap.add_argument('--stream', help='URL/indeks kamera, ganti input gambar')
    ap.add_argument('-n', '--frames', type=int, default=30, help='jumlah frame dari --stream')
    ap.add_argument('--weights', default=DEFAULT_WEIGHTS)
    # Default 0.10 = nilai yang BENAR-BENAR dipakai hook_vision_worker.py.
    # Menaikkannya di sini menyembunyikan justru deteksi yang sedang diselidiki.
    ap.add_argument('--conf', type=float, default=0.10, help='ambang emit worker')
    ap.add_argument('--imgsz', type=int, default=640)
    # CLAHE underwater MENYALA default, sama seperti hook_vision_worker.py
    # (`enhance_underwater=not args.no_underwater_enhance`). Mematikannya di
    # sini diam-diam berarti menguji pipeline yang bukan yang jalan di air.
    ap.add_argument('--no-enhance', action='store_true',
                    help='matikan CLAHE underwater (worker memakainya default)')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    paths = []
    for p in args.images:
        paths.extend(sorted(glob.glob(p)) if any(c in p for c in '*?[') else [p])
    if not paths and not args.stream:
        ap.error('beri file gambar atau --stream')

    detector = make_detector(args.weights, conf=args.conf, imgsz=args.imgsz,
                             enhance_underwater=not args.no_enhance)
    print(f"model   : {args.weights}")
    print(f"ambang  : worker emit conf>={args.conf}   "
          f"gate FSM LEFT_YOLO_CONF={mission5.LEFT_YOLO_CONF}   "
          f"HOOK_KEYPOINT_CONF={mission5.HOOK_KEYPOINT_CONF}")

    tahap = collections.Counter()
    alasan = collections.Counter()
    contoh = {}
    total = 0

    def catat(frame, nama):
        nonlocal total
        total += 1
        t, r = _probe(detector, frame, nama, args.verbose)
        tahap['LOLOS' if t is None else t] += 1
        if r:
            keluarga = str(r).split(':')[0]
            alasan[keluarga] += 1
            contoh.setdefault(keluarga, r)
            if args.verbose:
                print(f"    {nama}: DITOLAK di {t} — {r}")

    if args.stream:
        source = int(args.stream) if args.stream.isdigit() else args.stream
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            sys.exit(f"tidak bisa membuka stream: {args.stream}")
        try:
            for i in range(args.frames):
                ok, frame = cap.read()
                if not ok:
                    print(f"stream putus setelah {i} frame")
                    break
                catat(frame, f"frame{i:03d}")
        finally:
            cap.release()
    else:
        for path in paths:
            frame = cv2.imread(path)
            if frame is None:
                print(f"  ! gagal baca {path}")
                continue
            catat(frame, os.path.basename(path))

    if not total:
        sys.exit('tidak ada frame terbaca')

    print(f"\n{total} frame")
    print("\n  Berhenti di tahap mana:")
    for nama, jumlah in tahap.most_common():
        print(f"    {jumlah:4d}  ({100.0 * jumlah / total:5.1f}%)  {nama}")
    if alasan:
        print("\n  Alasan penolakan:")
        for keluarga, jumlah in alasan.most_common():
            print(f"    {jumlah:4d}  {keluarga:<28} contoh: {contoh[keluarga]}")
        print(f"\n  DOMINAN: {alasan.most_common(1)[0][0]}")
    else:
        print("\n  Tidak ada penolakan — semua frame lolos ke servo.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
