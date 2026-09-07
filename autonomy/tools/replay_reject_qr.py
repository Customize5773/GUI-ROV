#!/usr/bin/env python3
"""
tools/replay_reject_qr.py — Jalankan jalur QR SUNGGUHAN lewat tahap-tahap
SUNGGUHAN dan laporkan DI TAHAP MANA ia berhenti.

Sejajar dengan tools/replay_reject.py (hook), tapi terpisah karena jalur QR
punya EMPAT tahap, bukan dua, dan kegagalan di masing-masing tahap punya arti
yang beda sekali:

  1. region_detect — model region (best_new*.onnx) tak menemukan bbox QR sama
                      sekali. Bisa berarti QR tak ada di frame, ATAU model tak
                      mampu melihatnya pada jarak/sudut ini.
  2. decode         — region ketemu, tapi crop-nya tak terbaca oleh cascade
                      decode_qr()/_decode_tracked_roi() yang SAMA dipakai
                      worker Pi. Ini yang membedakan "QR terlihat tapi tak
                      terbaca" dari "QR memang tak terlihat".
  3. rov_agent      — decode berhasil tapi validator batas jaringan menolak
                      skema/rentang nilainya.
  4. fsm_payload    — decode DAN validator lolos, tapi isinya bukan payload
                      misi ini (_is_target_payload).

Tak ada penolakan di keempatnya = LOLOS, persis jalur yang dipakai FSM lewat
_fresh_payload().

Juga mengukur module_px (piksel per modul QR) lewat
vision.qr_detect.estimate_module_px() — SATU-SATUNYA cara mendapat angka pada
frame yang decode-nya justru GAGAL, yaitu persis kasus yang mau didiagnosis.
Bandingkan dengan ambang decode robust (~4-5 px minimum; 27 Agu 2026 terukur
~3.2 px di 720p pada jarak kerja normal).

    python autonomy/tools/replay_reject_qr.py "autonomy/tests/fixtures/*/*.png" -v
    python autonomy/tools/replay_reject_qr.py --stream http://192.168.2.2:8081/?action=stream -n 30 -v
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

import rov_agent                                                   # noqa: E402
from fsm import mission5                                           # noqa: E402
from vision.qr_detect import _decode_tracked_roi, _to_gray_clahe, estimate_module_px  # noqa: E402
from vision.yolo_hook import make_detector                         # noqa: E402

DEFAULT_WEIGHTS = os.path.join(_AUTONOMY, "vision", "best_new_320.onnx")


def _quad_from_bbox(bbox):
    """PERSIS qr_vision_worker._quad_from_bbox — jangan tulis ulang beda."""
    x, y, w, h = bbox
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


class _PayloadGateProbe:
    """_is_target_payload tanpa FSM lengkap — dipinjam apa adanya, bukan disalin."""
    _reject = mission5.Mission5FSM._reject
    _is_target_payload = mission5.Mission5FSM._is_target_payload

    def __init__(self):
        self.telemetry_out = {"reject_reason": None, "lock_progress": None}


def _worker_record(decoded, detection, w, h):
    """Amplop yang PERSIS dikirim qr_vision_worker.emit() ke rov_agent."""
    import numpy as np
    from vision.qr_detect import parse_payload, wall_from_qr

    pts = np.asarray(decoded["pts"], dtype=np.float32).reshape(-1, 2)
    data = decoded["data"]
    return {
        "status": "ok",
        "method": "yolo_qr",
        "data": data,
        "payload": parse_payload(data),
        "wall": wall_from_qr(data),
        "center": [float(pts[:, 0].mean()), float(pts[:, 1].mean())],
        "area": float(cv2.contourArea(pts.astype("int32"))),
        "confidence": detection.get("confidence"),
        "frame_w": w,
        "frame_h": h,
    }


def _probe(detector, frame, verbose, nama):
    h, w = frame.shape[:2]
    # CLAHE dulu, sama seperti decode_qr(enhance=True): foto kolam ber-kontras
    # rendah membuat Otsu polos gagal menyegmentasi finder pattern sama sekali
    # (terukur 7 Sep 2026 — 0 kandidat tanpa CLAHE, 2 kandidat @~2.1px dengan
    # CLAHE, pada fixture yang SAMA).
    module_px = estimate_module_px(_to_gray_clahe(frame))

    detection = detector.detect(frame)
    if detection is None:
        return "region_detect", "no_detection", module_px

    decoded = _decode_tracked_roi(frame, _quad_from_bbox(detection["bbox"]),
                                  full_cascade=True)
    if not decoded:
        return "decode", "decode_failed", module_px

    record = _worker_record(decoded[0], detection, w, h)
    rov_agent.last_vision_reject.update(hook=None, qr=None)
    clean = rov_agent._validate_qr_vision(record)
    if clean is None:
        return "rov_agent", rov_agent.last_vision_reject["qr"], module_px

    probe = _PayloadGateProbe()
    if not probe._is_target_payload(clean):
        return "fsm_payload", probe.telemetry_out["reject_reason"], module_px

    if verbose:
        print(f"    {nama}: LOLOS  data={clean['data']!r}  conf={clean['confidence']:.3f}")
    return None, None, module_px


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", help="file gambar (boleh glob)")
    ap.add_argument("--stream", help="URL/indeks kamera, ganti input gambar")
    ap.add_argument("-n", "--frames", type=int, default=30)
    ap.add_argument("--weights", default=DEFAULT_WEIGHTS,
                    help="model region QR (default: yang ter-deploy di unit systemd)")
    ap.add_argument("--conf", type=float, default=0.6,
                    help="ambang region-detect — 0.6 = default qr_vision_worker.py")
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    paths = []
    for p in args.images:
        paths.extend(sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p])
    if not paths and not args.stream:
        ap.error("beri file gambar atau --stream")

    detector = make_detector(args.weights, conf=args.conf, imgsz=args.imgsz)
    print(f"model region : {args.weights}  (conf>={args.conf}, imgsz={args.imgsz})")
    print("ambang decode robust yg pernah terukur: ~4-5 px/modul minimum "
          "(27 Agu 2026: ~3.2px di 720p pada jarak kerja normal)")

    tahap = collections.Counter()
    alasan = collections.Counter()
    contoh = {}
    semua_module_px = []
    total = 0

    def catat(frame, nama):
        nonlocal total
        total += 1
        stage, reason, module_px = _probe(detector, frame, args.verbose, nama)
        tahap["LOLOS" if stage is None else stage] += 1
        semua_module_px.extend(module_px)
        mod_txt = (f"module_px~{min(module_px):.1f}-{max(module_px):.1f}"
                   if module_px else "module_px=tak_terukur")
        if reason:
            keluarga = str(reason).split(":")[0]
            alasan[keluarga] += 1
            contoh.setdefault(keluarga, reason)
            if args.verbose:
                print(f"    {nama}: DITOLAK di {stage} — {reason}  ({mod_txt})")
        elif args.verbose:
            print(f"    {nama}: ({mod_txt})")

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
        sys.exit("tidak ada frame terbaca")

    print(f"\n{total} frame")
    print("\n  Berhenti di tahap mana:")
    for nama, jumlah in tahap.most_common():
        print(f"    {jumlah:4d}  ({100.0 * jumlah / total:5.1f}%)  {nama}")
    if alasan:
        print("\n  Alasan penolakan:")
        for keluarga, jumlah in alasan.most_common():
            print(f"    {jumlah:4d}  {keluarga:<28} contoh: {contoh[keluarga]}")
        print(f"\n  DOMINAN: {alasan.most_common(1)[0][0]}")
    if semua_module_px:
        semua_module_px.sort()
        n = len(semua_module_px)
        print(f"\n  module_px terukur ({n} kandidat finder pattern, dari semua frame):")
        print(f"    min={semua_module_px[0]:.2f}  median={semua_module_px[n // 2]:.2f}  "
              f"max={semua_module_px[-1]:.2f}")
        if semua_module_px[n // 2] < 4.0:
            print("    -> DI BAWAH ambang decode robust (~4-5px). Konsisten dengan "
                 "'QR di luar jarak kerja', bukan bukti model tak mampu.")
    else:
        print("\n  module_px: TIDAK ADA finder pattern tersegmentasi di frame mana pun "
             "— TIDAK BISA memisahkan 'di luar jarak kerja' dari 'model tak mampu' "
             "dari data ini saja.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
