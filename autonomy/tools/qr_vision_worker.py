#!/usr/bin/env python3
"""Backend YOLO worker: CAM BOTTOM -> qr_vision JSONL.

Melokalisasi region QR dengan best_new.pt (model detect-only, 1 kelas), meng-crop
ROI dari bbox itu, lalu men-decode QR DI DALAM crop — bukan memindai frame penuh.
Node GUI yang memiliki proses ini; worker tidak pernah mengirim perintah wahana.

Sejak 7 Sep 2026 worker ini berjalan DI PI, bukan di laptop: inferensi memakai
cv2.dnn atas bobot .onnx sehingga Pi tetap bebas torch. Decode QR lokal di Pi
TETAP jalan sebagai fallback — lihat _fresh_payload di fsm/mission5.py.
"""

import argparse
import logging
import os
import sys
import threading
import time

# Lewat paket `tools` supaya import ini sah baik saat worker dijalankan sebagai
# skrip oleh Node (PYTHONPATH=autonomy) maupun saat diimpor tes.
from tools.hook_vision_worker import (LatestFrame, emit, enable_udp_emit,
                                      inference_wanted, limit_cv_threads,
                                      telemetry_listener)


def _quad_from_bbox(bbox):
    """bbox (x, y, w, h) -> 4 sudut searah jarum jam, seed untuk crop ROI."""
    x, y, w, h = bbox
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _jsonable(value):
    """Buang tipe numpy supaya hasilnya aman di-JSON-kan ke Node/UDP."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, 'tolist'):
        return value.tolist()
    return value


def build_arg_parser():
    from vision.qr_gripper import parse_roi
    ap = argparse.ArgumentParser(description='Laptop-side YOLO QR worker')
    ap.add_argument('--camera', required=True)
    ap.add_argument('--gripper-roi', type=parse_roi,
                    default=os.environ.get('QR_GRIPPER_ROI', '0.25,0.10,0.75,0.80'),
                    help='area gripper x1,y1,x2,y2, fraksi frame asli')
    ap.add_argument('--proposal-conf', type=float,
                    default=float(os.environ.get('QR_PROPOSAL_CONF', 0.1)),
                    help='kandidat di bawah --conf wajib berhasil decode')
    ap.add_argument('--model', required=True)
    ap.add_argument('--calib', default=None,
                    help='npz kalibrasi CAM BOTTOM; tanpa ini pose=None dan '
                         'gate squaring M5_QR_DOCK ikut mati')
    ap.add_argument('--qr-size', type=float, default=0.04,
                    help='sisi QR dalam meter (spesifikasi KKI 4 cm)')
    # 0.6 = puncak F1 dari report training best_new.pt. Titik awal, BUKAN angka
    # mati: air kolam menurunkan confidence, jadi setel lewat QR_VISION_CONF.
    #
    # 7 Sep 2026: sebelum baris ini, QR_VISION_CONF/IMGSZ/FPS disebut di
    # KOMENTAR tapi tak pernah dibaca os.environ di mana pun (grep seluruh
    # repo) — unit systemd menghardcode --conf 0.6 langsung di ExecStart,
    # jadi "setel lewat QR_VISION_CONF" tak pernah benar-benar bisa
    # dilakukan. CLI flag tetap menang kalau eksplisit diberikan (seperti
    # ExecStart saat ini), jadi menambah default env di sini TIDAK
    # mengubah perilaku yang sudah di-deploy — baru berlaku begitu unit
    # file dilepas dari --conf/--imgsz/--fps eksplisit (lihat A7).
    ap.add_argument('--conf', type=float,
                    default=float(os.environ.get('QR_VISION_CONF', 0.6)))
    ap.add_argument('--imgsz', type=int,
                    default=int(os.environ.get('QR_VISION_IMGSZ', 640)))
    ap.add_argument('--fps', type=float,
                    default=float(os.environ.get('QR_VISION_FPS', 10.0)))
    # --- jalur Raspberry Pi -------------------------------------------------
    ap.add_argument('--emit-udp', default=None, metavar='HOST:PORT',
                    help='kirim hasil sbg UDP JSON ke rov_agent (mis. 127.0.0.1:14550)')
    ap.add_argument('--telemetry-port', type=int, default=None,
                    help='dengarkan telemetri rov_agent di port UDP ini (gate vision_want)')
    ap.add_argument('--cv-threads', type=int, default=2,
                    help='jumlah thread OpenCV; samakan dgn CPUAffinity unit systemd')
    return ap


def main():
    args = build_arg_parser().parse_args()

    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format='[qr-worker] %(levelname)s %(message)s')
    limit_cv_threads(args.cv_threads)
    if args.emit_udp:
        enable_udp_emit(args.emit_udp, 'qr_vision')
    try:
        import cv2
        import numpy as np
        from vision.qr_detect import (_order_quad_points,
                                      estimate_pose_pts, parse_payload, wall_from_qr)
        from vision.yolo_hook import make_detector
        # Detektor dipakai apa adanya: untuk model detect-only blok keypoint-nya
        # menghasilkan None di kedua backend, jadi jalur hook/pose tak tersentuh.
        from vision.qr_gripper import detect_gripper_qr
        if not 0 < args.proposal_conf <= args.conf <= 1:
            raise ValueError('require 0 < proposal-conf <= conf <= 1')
        detector = make_detector(args.model, conf=args.proposal_conf, imgsz=args.imgsz)
        K = dist = None
        calibration = None
        if args.calib:
            from vision.hook_localization import load_calibration, verify_calib_size
            calibration = load_calibration(args.calib)
            K, dist = calibration['K'], calibration['dist']
        else:
            logging.warning('tanpa --calib: pose=None, gate squaring M5_QR_DOCK mati')
    except Exception as exc:
        emit({'status': 'worker_error', 'reason': str(exc), 'timestamp': time.time()})
        return 2

    calib_checked = [False]

    def _open_camera():
        new_cap = cv2.VideoCapture(args.camera)
        try:
            new_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return new_cap

    cap = _open_camera()
    if not cap.isOpened():
        emit({'status': 'camera_error',
              'reason': 'tidak bisa membuka ' + str(args.camera),
              'timestamp': time.time()})
        return 3

    state, lock = {}, threading.Lock()
    if args.telemetry_port:
        threading.Thread(target=telemetry_listener,
                         args=(state, lock, args.telemetry_port), daemon=True).start()
    camera = LatestFrame(cap, opener=_open_camera).start()
    interval = 1.0 / max(0.1, args.fps)
    last_status = None
    last_emit = 0.0
    last_seq = 0

    try:
        while True:
            started = time.monotonic()
            if not inference_wanted(state, lock, 'BOTTOM'):
                # State FSM saat ini tidak membaca CAM BOTTOM — lihat VISION_WANT.
                time.sleep(interval)
                continue
            last_seq, frame, captured_at, failed = camera.take(last_seq)
            if frame is None:
                if not failed:
                    # Belum ada frame BARU sejak siklus lalu — bukan error.
                    time.sleep(min(interval, 0.01))
                    continue
                result = {'status': 'camera_error', 'reason': 'frame gagal dibaca',
                          'timestamp': time.time()}
            else:
                h, w = frame.shape[:2]
                # Sekali saja, pada frame NYATA pertama: kalibrasi yang dibuat
                # di resolusi lain membuat pose PBVS meleset sebanding rasionya
                # — dan pose itulah yang menggerakkan servo docking. Diperiksa
                # di sini, bukan saat load, karena resolusi stream baru
                # diketahui setelah frame pertama tiba.
                if calibration is not None and not calib_checked[0]:
                    calib_checked[0] = True
                    if not verify_calib_size(calibration, frame):
                        K = dist = None
                        emit({'status': 'calib_mismatch',
                              'reason': 'kalibrasi %s != stream %dx%d' % (
                                  calibration.get('image_size'), w, h),
                              'timestamp': time.time()})
                detection, decoded = detect_gripper_qr(
                    detector, frame, args.gripper_roi, args.conf)
                if not decoded:
                    # Tanpa teks QR, FSM Mission 5 tak boleh menggerakkan apa
                    # pun — jadi `qr_vision` TETAP kosong di sini, kontraknya
                    # tidak berubah sedikit pun.
                    #
                    # Tapi region-nya sendiri berguna untuk kendali LATERAL:
                    # menengahkan kotak QR di frame tidak butuh tahu isinya, dan
                    # decode adalah bagian yang paling sering gagal di air
                    # berriak. Region karena itu dilaporkan di kanal TERPISAH
                    # `qr_region`, yang dipakai servo CASE 4 di
                    # server/control_main.py. Konsumen yang butuh teks QR tidak
                    # pernah melihat kanal ini.
                    result = {'status': 'no_detection', 'timestamp': time.time()}

                    if detection is not None:
                        emit({
                            'status': 'region',
                            'center': [float(detection['center'][0]),
                                       float(detection['center'][1])],
                            'bbox': [float(v) for v in detection['bbox']],
                            'area': float(detection['area']),
                            'confidence': detection.get('confidence'),
                            'frame_w': w, 'frame_h': h,
                            'timestamp': time.time(),
                            'capture_ts': captured_at,
                            'age_ms': round((time.time() - captured_at) * 1000.0, 1),
                            'method': 'yolo_qr_region',
                            'active_cam': 'BOTTOM',
                        }, channel='qr_region')
                else:
                    det = decoded[0]
                    pts = np.asarray(det['pts'], dtype=np.float32).reshape(-1, 2)
                    data = det['data']
                    ordered = _order_quad_points(pts)
                    pose = (estimate_pose_pts(ordered, args.qr_size, K, dist)
                            if ordered is not None else None)
                    result = {
                        'status': 'ok',
                        'data': data,
                        'payload': parse_payload(data),
                        'wall': wall_from_qr(data),
                        'center': [float(pts[:, 0].mean()), float(pts[:, 1].mean())],
                        'area': float(cv2.contourArea(pts.astype(np.int32))),
                        'pts': _jsonable(pts),
                        'pose': _jsonable(pose) if pose else None,
                        'confidence': detection.get('confidence'),
                        'bbox': [float(v) for v in detection['bbox']],
                        'frame_w': w, 'frame_h': h,
                        'timestamp': time.time(),
                        'capture_ts': captured_at,
                        # Umur frame saat hasil ini dibuat: antrean kamera +
                        # inferensi + decode. Pi membuang yang basi sebelum
                        # boleh menggerakkan ROV.
                        'age_ms': round((time.time() - captured_at) * 1000.0, 1),
                        'method': 'yolo_qr',
                        'active_cam': 'BOTTOM',
                    }

            now = time.time()
            # Setiap decode sukses dikirim: servo docking butuh geometri tiap
            # frame, bukan ringkasan status tiap 0,5 s seperti worker hook.
            if (result.get('status') == 'ok' or result.get('status') != last_status
                    or now - last_emit >= 0.5):
                emit(result)
                last_status, last_emit = result.get('status'), now
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    finally:
        camera.stop()
        camera.release()


if __name__ == '__main__':
    raise SystemExit(main())
