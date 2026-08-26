#!/usr/bin/env python3
"""
tools/preflight_check.py — Go/no-go SEBELUM toggle Autonomous di venue.

Kenapa alat ini ada
    Pola bug berulang di PR-AUTONOMY.md (BRIDGE-01, CALIB-01, HOOK-02) semua
    "ketahuan SETELAH ROV bergerak" — satu attempt heat KKI terbuang sebelum
    tim tahu kameranya buram/kabel lepas/air terlalu keruh hari itu. Skrip ini
    mengecek kamera + deteksi QR/hook + MARK/depth SEBELUM pilot menekan
    toggle, murni observasional (read-only) — tidak menyentuh mission5.py.

Reuse langsung, tanpa menulis ulang logika deteksi:
    - vision.qr_detect.decode_qr()   — sama persis dipakai servo_webcam_test.py
    - vision.hook_detect.detect_hook() — sama persis dipakai FSM misi 3b/4/5

Pemakaian:
    # Kamera saja (paling umum — dipanggil dari laptop operator via stream):
    python3 tools/preflight_check.py --bottom-url http://192.168.2.2:8081/stream \\
                                      --wall-url   http://192.168.2.2:8082/stream

    # + telemetry (MARK/depth) — HANYA jalan bila ada telemetri UDP masuk di
    # --telem-port (autonomy/rov_link.py TelemetryReceiver, port 14552 default).
    # Topologi produksi (rov_agent.py + rov_mission5_bridge.py di Pi) TIDAK
    # memancarkan ke port ini — jalankan dari SITL/rov_link, atau lewati cek
    # telemetri dgn --skip-telem di venue produksi.
    python3 tools/preflight_check.py --bottom-url ... --wall-url ... --telem-port 14552

Exit code: 0 = GO (semua PASS/WARN), 1 = NO-GO (ada FAIL).
"""
import argparse
import json
import os
import socket
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vision.qr_detect import decode_qr
from vision.hook_detect import detect_hook, HOOK_MAX_AREA_FRAC

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _report(results, name, status, detail=""):
    results.append((name, status, detail))
    tag = {"PASS": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[status]
    print(f"{tag} {name}" + (f" — {detail}" if detail else ""))


def _open_camera(src, results, name, timeout=3.0):
    """Buka kamera & baca 1 frame dlm `timeout` detik. Return VideoCapture|None."""
    cap = cv2.VideoCapture(src)
    t0 = time.time()
    ok, frame = False, None
    while time.time() - t0 < timeout:
        ok, frame = cap.read()
        if ok and frame is not None:
            break
    if not ok or frame is None:
        _report(results, name, FAIL, f"tak bisa baca frame dari {src} dalam {timeout}s")
        cap.release()
        return None
    _report(results, name, PASS, f"frame {frame.shape[1]}x{frame.shape[0]} dari {src}")
    return cap


def _detection_rate(cap, detector_fn, n_frames, fps):
    """Jalankan `detector_fn(frame) -> dict|None` atas n_frames, return
    (rate 0..1, list hasil non-None)."""
    hits, seen = [], 0
    interval = 1.0 / fps
    for _ in range(n_frames):
        t0 = time.time()
        ok, frame = cap.read()
        if ok and frame is not None:
            seen += 1
            det = detector_fn(frame)
            if det is not None:
                hits.append(det)
        time.sleep(max(0.0, interval - (time.time() - t0)))
    rate = len(hits) / seen if seen else 0.0
    return rate, hits


def check_qr(cap, results, n_frames, fps):
    if cap is None:
        _report(results, "Deteksi QR (BOTTOM)", FAIL, "kamera BOTTOM tak terbuka — dilewati")
        return
    rate, _ = _detection_rate(cap, lambda f: (decode_qr(f) or None), n_frames, fps)
    status = PASS if rate > 0 else FAIL
    _report(results, "Deteksi QR (BOTTOM)", status, f"detection-rate {rate*100:.0f}% dari {n_frames} frame")


def check_hook(cap, results, n_frames, fps):
    if cap is None:
        _report(results, "Deteksi hook (WALL)", FAIL, "kamera WALL tak terbuka — dilewati")
        return
    rate, hits = _detection_rate(cap, detect_hook, n_frames, fps)
    if rate == 0:
        _report(results, "Deteksi hook (WALL)", FAIL, f"detection-rate 0% dari {n_frames} frame")
        return
    avg_frac = sum(h['area'] / (h['frame_w'] * h['frame_h']) for h in hits) / len(hits)
    detail = f"detection-rate {rate*100:.0f}%, area rata {avg_frac*100:.1f}% frame"
    if avg_frac > HOOK_MAX_AREA_FRAC * 0.8:
        # Mendekati batas HOOK_MAX_AREA_FRAC (pola gagal HOOK-02: air keruh →
        # satu contour raksasa membungkus seluruh frame) — WARN, bukan FAIL,
        # supaya operator cek air/kontras sebelum run, bukan baru ketahuan di kolam.
        _report(results, "Deteksi hook (WALL)", WARN,
                detail + f" — dekat batas HOOK_MAX_AREA_FRAC ({HOOK_MAX_AREA_FRAC*100:.0f}%), cek kekeruhan air")
    else:
        _report(results, "Deteksi hook (WALL)", PASS, detail)


def read_telemetry(port, timeout):
    """Dengarkan SATU paket JSON telemetry (pola sama fsm.mission5.TelemetryReceiver).
    Best-effort: return None bila tak ada paket masuk atau port sudah dipakai
    proses lain (mis. GUI server.js/rov_link.py sudah jalan di mesin ini)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('0.0.0.0', port))
    except OSError as e:
        print(f"[WARN] tak bisa bind :{port} ({e}) — port mungkin dipakai proses lain, "
              f"cek telemetri dilewati")
        sock.close()
        return None
    sock.settimeout(timeout)
    try:
        data, _ = sock.recvfrom(65536)
        return json.loads(data.decode())
    except (socket.timeout, json.JSONDecodeError):
        return None
    finally:
        sock.close()


def check_mark(telem, results):
    if telem is None:
        _report(results, "MARK freshness", WARN, "tak ada telemetri — cek dilewati (lihat --telem-port)")
        return
    heading, depth = telem.get('marked_heading'), telem.get('marked_depth')
    if heading is None or depth is None:
        _report(results, "MARK freshness", WARN,
                "belum di-MARK — M5_REDIVE akan sapu buta (lambat, bukan gagal)")
    else:
        _report(results, "MARK freshness", PASS, f"heading={heading:.0f}° depth={depth:.2f}m")


def check_depth(telem, results, pool_depth):
    if telem is None:
        _report(results, "Depth sensor", WARN, "tak ada telemetri — cek dilewati (lihat --telem-port)")
        return
    depth = telem.get('depth')
    if depth is None:
        _report(results, "Depth sensor", FAIL, "field 'depth' tak ada di telemetri")
    elif depth < 0 or (pool_depth is not None and depth > pool_depth):
        _report(results, "Depth sensor", FAIL, f"depth={depth:.2f}m di luar rentang wajar (pool={pool_depth})")
    else:
        _report(results, "Depth sensor", PASS, f"depth={depth:.2f}m")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bottom-url", default=None, help="stream MJPEG kamera BOTTOM (QR)")
    ap.add_argument("--bottom-device", type=int, default=None, help="index USB kamera BOTTOM (alternatif --bottom-url)")
    ap.add_argument("--wall-url", default=None, help="stream MJPEG kamera WALL (hook)")
    ap.add_argument("--wall-device", type=int, default=None, help="index USB kamera WALL (alternatif --wall-url)")
    ap.add_argument("--frames", type=int, default=20, help="jumlah frame per cek deteksi (default 20)")
    ap.add_argument("--fps", type=float, default=10.0, help="target fps sampling (default 10)")
    ap.add_argument("--telem-port", type=int, default=14552, help="port UDP telemetry (default 14552, lihat docstring)")
    ap.add_argument("--telem-timeout", type=float, default=2.0, help="detik tunggu telemetri (default 2)")
    ap.add_argument("--pool-depth", type=float, default=None, help="kedalaman kolam (m) — sanity check depth")
    ap.add_argument("--skip-telem", action="store_true", help="lewati cek MARK/depth (telemetri tak tersedia dari topologi ini)")
    args = ap.parse_args()

    results = []
    print("=== PREFLIGHT CHECK — Misi 5 KKI 2026 ===\n")

    bottom_src = args.bottom_url if args.bottom_url is not None else (args.bottom_device if args.bottom_device is not None else 0)
    wall_src = args.wall_url if args.wall_url is not None else args.wall_device

    bottom_cap = _open_camera(bottom_src, results, "Kamera BOTTOM")
    wall_cap = _open_camera(wall_src, results, "Kamera WALL") if wall_src is not None else None
    if wall_cap is None and wall_src is None:
        _report(results, "Kamera WALL", WARN, "--wall-url/--wall-device tak diisi — cek hook dilewati")

    check_qr(bottom_cap, results, args.frames, args.fps)
    if wall_cap is not None:
        check_hook(wall_cap, results, args.frames, args.fps)

    if bottom_cap is not None:
        bottom_cap.release()
    if wall_cap is not None:
        wall_cap.release()

    if args.skip_telem:
        print("[SKIP] cek MARK/depth dilewati (--skip-telem)")
    else:
        telem = read_telemetry(args.telem_port, args.telem_timeout)
        check_mark(telem, results)
        check_depth(telem, results, args.pool_depth)

    fails = [r for r in results if r[1] == FAIL]
    print()
    if fails:
        print(f"=== NO-GO: {len(fails)} gagal ===")
        for name, _, detail in fails:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    else:
        print("=== GO ===")
        sys.exit(0)


if __name__ == "__main__":
    main()
