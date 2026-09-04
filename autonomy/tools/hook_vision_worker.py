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


class LatestFrame:
    """Pembaca kamera yang HANYA menyimpan frame terbaru.

    cv2.VideoCapture atas stream MJPEG mem-buffer frame di sisi FFmpeg/soket.
    Kalau loop deteksi hanya membaca 10 kali per detik sementara kamera
    mengirim ~30 fps, dua dari tiga frame menumpuk di buffer dan frame yang
    akhirnya dibaca YOLO makin lama makin tua — latensinya tumbuh tanpa batas
    sepanjang misi, bukan konstan. Thread ini menguras stream secepat kamera
    mengirim dan membuang yang lama, jadi loop deteksi selalu memegang frame
    paling baru dan usianya terbatas pada satu interval frame.
    """

    def __init__(self, cap, opener=None, reconnect_after=10, reconnect_backoff=0.5):
        """`opener` (opsional): callable tanpa argumen yang mengembalikan
        cv2.VideoCapture BARU. Tanpa opener, kelas berperilaku persis seperti
        sebelumnya (dipakai test dengan capture palsu) — read() gagal hanya
        dilaporkan lewat `failed`, tidak pernah mencoba menyambung ulang.
        """
        self._cap = cap
        self._opener = opener
        self._reconnect_after = max(1, reconnect_after)
        self._reconnect_backoff = reconnect_backoff
        self._lock = threading.Lock()
        self._frame = None
        self._captured_at = 0.0
        self._seq = 0
        self._failed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _reconnect(self):
        """Stream MJPEG-over-HTTP putus TIDAK sembuh sendiri lewat read()
        yang diulang-ulang pada VideoCapture yang sama — soket lamanya sudah
        mati. Lepas capture lama, tunggu sebentar, lalu buka koneksi baru
        lewat `opener`. `_frame`/`_captured_at` sengaja tidak disentuh di
        sini: sampai ada bacaan sukses BARU, `take()` tetap melaporkan
        `failed=True` sehingga age_ms tak pernah dibaca seolah segar dari
        frame basi sebelum putus.
        """
        old_cap = self._cap
        try:
            old_cap.release()
        except Exception:
            pass
        self._stop.wait(self._reconnect_backoff)
        if self._stop.is_set():
            return
        try:
            new_cap = self._opener()
        except Exception:
            new_cap = None
        if new_cap is not None:
            with self._lock:
                self._cap = new_cap

    def _run(self):
        consecutive_failures = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok:
                with self._lock:
                    self._failed = True
                consecutive_failures += 1
                if self._opener is not None and consecutive_failures >= self._reconnect_after:
                    self._reconnect()
                    consecutive_failures = 0
                    continue
                # Jangan sibuk-menunggu saat stream putus; loop utama yang
                # melaporkan camera_error ke GUI.
                self._stop.wait(0.05)
                continue
            consecutive_failures = 0
            now = time.time()
            with self._lock:
                self._frame = frame
                self._captured_at = now
                self._seq += 1
                self._failed = False

    def take(self, last_seq):
        """Frame terbaru yang BELUM diproses: (seq, frame, captured_at, failed)."""
        with self._lock:
            if self._stop.is_set():
                return last_seq, None, 0.0, self._failed
            if self._seq == last_seq:
                return last_seq, None, 0.0, self._failed
            return self._seq, self._frame, self._captured_at, self._failed

    def stop(self):
        self._stop.set()

    def release(self):
        with self._lock:
            cap = self._cap
        try:
            cap.release()
        except Exception:
            pass


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
    # Kolam uji berlatar putih dan hanya berisi hook. Bbox detector boleh
    # masuk sejak confidence rendah agar rangka 2..5 tetap dapat divalidasi
    # oleh FSM; ini bukan izin bergerak tanpa keypoint yang kuat.
    ap.add_argument('--conf', type=float, default=0.10)
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

    def _open_camera():
        new_cap = cv2.VideoCapture(args.camera)
        # Minta backend menyimpan satu frame saja. Tidak semua backend
        # menurutinya (karena itu LatestFrame tetap perlu), tapi kalau
        # dituruti, buffer-nya hilang di sumbernya.
        try:
            new_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return new_cap

    cap = _open_camera()
    if not cap.isOpened():
        emit({'status': 'camera_error', 'reason': f'tidak bisa membuka {args.camera}',
              'timestamp': time.time()})
        return 3

    state, lock = {}, threading.Lock()
    threading.Thread(target=telemetry_reader, args=(state, lock), daemon=True).start()
    camera = LatestFrame(cap, opener=_open_camera).start()
    tracker = HookTracker()
    interval = 1.0 / max(0.1, args.fps)
    last_status = None
    last_emit = 0.0
    last_seq = 0

    try:
        while True:
            started = time.monotonic()
            last_seq, frame, captured_at, failed = camera.take(last_seq)
            if frame is None:
                if not failed:
                    # Belum ada frame BARU sejak siklus lalu — bukan error.
                    # Tidur sebentar lalu cek lagi, jangan emit apa pun.
                    time.sleep(min(interval, 0.01))
                    continue
                result = {'status': 'camera_error', 'reason': 'frame gagal dibaca',
                          'timestamp': time.time()}
            else:
                detection = detector.detect(frame)
                if detection is None:
                    result = {'status': 'no_detection', 'timestamp': time.time()}
                else:
                    with lock:
                        vehicle = dict(state)
                    # Heading relatif dipakai MOTION; lokalisasi map tetap
                    # membutuhkan bearing kompas yang tidak di-zero-kan.
                    compass_heading = vehicle.get('heading_compass')
                    if isinstance(compass_heading, (int, float)):
                        vehicle['heading'] = float(compass_heading)
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
                        # Umur frame saat hasil ini dibuat: waktu antrean
                        # kamera + inferensi. Dipakai untuk melihat apakah
                        # observasi hook masih layak dipercaya FSM.
                        'capture_ts': captured_at,
                        'age_ms': round((time.time() - captured_at) * 1000.0, 1),
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
        camera.stop()
        camera.release()


if __name__ == '__main__':
    raise SystemExit(main())
