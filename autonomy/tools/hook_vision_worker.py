#!/usr/bin/env python3
"""Backend YOLO worker: CAM WALL -> hook_xy JSONL.

The Node GUI server owns this process. The worker never sends vehicle commands;
it only reads video/telemetry and writes vision results to stdout.
"""

import argparse
import json
import logging
import sys
import threading
import time


def emit(value):
    sys.stdout.write(json.dumps(value, separators=(',', ':')) + '\n')
    sys.stdout.flush()


def telemetry_reader(state, lock):
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            if msg.get('type') != 'telemetry':
                continue
            with lock:
                state.update(msg.get('data') or {})
        except (ValueError, TypeError):
            continue


def main():
    ap = argparse.ArgumentParser(description='Laptop-side YOLO Hook worker')
    ap.add_argument('--camera', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--map', required=True)
    ap.add_argument('--calib', required=True)
    ap.add_argument('--conf', type=float, default=0.20)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--fps', type=float, default=10.0)
    ap.add_argument('--no-tta', action='store_true', help='matikan test-time augmentation')
    ap.add_argument('--no-underwater-enhance', action='store_true',
                    help='matikan CLAHE untuk haze underwater')
    args = ap.parse_args()

    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format='[hook-worker] %(levelname)s %(message)s')
    try:
        import cv2
        from vision.hook_localization import HookTracker, load_calibration, load_hook_map, localize_hook
        from vision.yolo_hook import YOLOHookDetector
        detector = YOLOHookDetector(
            args.model, conf=args.conf, imgsz=args.imgsz,
            augment=not args.no_tta,
            enhance_underwater=not args.no_underwater_enhance,
        )
        calibration = load_calibration(args.calib)
        hook_map = load_hook_map(args.map)
    except Exception as exc:
        emit({'status': 'worker_error', 'reason': str(exc), 'timestamp': time.time()})
        return 2

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        emit({'status': 'camera_error', 'reason': f'tidak bisa membuka {args.camera}',
              'timestamp': time.time()})
        return 3

    state, lock = {}, threading.Lock()
    threading.Thread(target=telemetry_reader, args=(state, lock), daemon=True).start()
    tracker = HookTracker()
    interval = 1.0 / max(0.1, args.fps)
    last_status = None
    last_emit = 0.0

    try:
        while True:
            started = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                result = {'status': 'camera_error', 'reason': 'frame gagal dibaca',
                          'timestamp': time.time()}
            else:
                detection = detector.detect(frame)
                if detection is None:
                    result = {'status': 'no_detection', 'timestamp': time.time()}
                else:
                    with lock:
                        vehicle = dict(state)
                    localized = localize_hook(
                        detection, calibration, hook_map=hook_map,
                        vehicle_state=vehicle, tracker=tracker, frame=frame)
                    pose = localized.get('pose_map_base') or {}
                    rel = localized.get('relative_pose_base') or {}
                    covariance = localized.get('covariance') or []
                    result = {
                        'status': localized.get('status'),
                        'hook_id': localized.get('hook_id'),
                        'x': pose.get('x'), 'y': pose.get('y'), 'z': pose.get('z'),
                        'relative_x': rel.get('x'), 'relative_y': rel.get('y'),
                        'relative_z': rel.get('z'),
                        'sigma_xy_m': (covariance[0] ** 0.5 if covariance else None),
                        'reproj_px': localized.get('reprojection_error_px'),
                        'reason': localized.get('reason'),
                        'confidence': detection.get('confidence'),
                        'bbox': detection.get('bbox'),
                        'keypoints': detection.get('keypoints'),
                        'frame_w': detection.get('frame_w'),
                        'frame_h': detection.get('frame_h'),
                        'offset_x': detection['center'][0] - detection['frame_w'] / 2.0,
                        'offset_y': detection['center'][1] - detection['frame_h'] / 2.0,
                        'timestamp': detection.get('timestamp', time.time()),
                        'method': 'yolov8',
                        'active_cam': 'WALL',
                    }

            now = time.time()
            if result.get('status') != last_status or now - last_emit >= 0.5:
                emit(result)
                last_status, last_emit = result.get('status'), now
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    finally:
        cap.release()


if __name__ == '__main__':
    raise SystemExit(main())
