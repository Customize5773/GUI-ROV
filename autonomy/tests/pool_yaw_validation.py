"""Validasi kolam QR-02 (yaw squaring) — SEBELUM menaikkan SERVO_KP_YAW dari 0.

Script ini PASIF: cuma decode QR + solvePnP lewat QRVision lalu log yaw_deg,
tak pernah mengirim command ke rov_link. Sesuai M7 di VERIFIKASI_ARDUSUB.md —
buktikan dulu TANDA dan STABILITAS estimasi yaw sebelum FSM diizinkan
memakainya untuk squaring aktif.

Cara pakai di kolam (operator kemudikan ROV manual via GUI/joystick seperti biasa):
    python -m autonomy.tests.pool_yaw_validation --calib kalib.npz --qr-size 0.04 \
        --device 0 --duration 30

Prosedur:
  1. Posisikan ROV lurus menghadap QR (yaw≈0 visual). Jalankan script.
  2. Putar ROV pelan ke KANAN (yaw+ menurut kompas/QGC) beberapa derajat, tahan diam.
  3. Amati kolom yaw_deg — HARUS berubah tanda konsisten & sepadan besarnya
     dengan rotasi asli. Ulangi ke KIRI.
  4. Kalau tanda terbalik → nanti balik `invert_yaw=True` di VisualServo/PoseServo
     (visual_servo.py) sebelum menaikkan SERVO_KP_YAW. Kalau nilai lompat-lompat
     acak (bukan cuma noise kecil) → yaw dari 1 QR planar memang belum stabil,
     JANGAN aktifkan SERVO_KP_YAW, andalkan heading-hold ArduSub (sesuai catatan M7).
"""
import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vision.qr_detect import VisionPipeline


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vision', default='usb', choices=['usb', 'rtsp', 'mock'])
    ap.add_argument('--device', type=int, default=0)
    ap.add_argument('--rtsp', default='rtsp://192.168.1.10:8554/cam')
    ap.add_argument('--calib', required=True, help='.npz kalibrasi kamera (wajib utk pose/yaw)')
    ap.add_argument('--qr-size', type=float, default=0.04, help='sisi QR fisik (m)')
    ap.add_argument('--duration', type=float, default=30.0, help='lama rekam (detik)')
    ap.add_argument('--out', default='pool_yaw_log.csv')
    args = ap.parse_args()

    rows = []

    def on_qr(result):
        pose = result.get('pose')
        if pose is None:
            return
        t = time.monotonic()
        row = (t, pose['x'], pose['y'], pose['z'], pose['dist'], pose['yaw_deg'])
        rows.append(row)
        print(f"t={t:7.2f}s  x={pose['x']:+.3f} y={pose['y']:+.3f} z={pose['z']:.3f} "
              f"dist={pose['dist']:.3f}  yaw_deg={pose['yaw_deg']:+7.2f}")

    vision = VisionPipeline(source=args.vision, device=args.device, rtsp_url=args.rtsp,
                             calib_file=args.calib, qr_length=args.qr_size, callback=on_qr)
    vision.start()
    print(f"[pool_yaw_validation] Merekam {args.duration}s. Vision sumber={args.vision}. "
          f"Tak ada command dikirim ke ROV — putar manual & amati yaw_deg. Ctrl+C utk stop lebih awal.")
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        vision.stop()

    if not rows:
        print("[pool_yaw_validation] TIDAK ADA pose terekam — cek kalibrasi/pencahayaan QR.")
        return

    with open(args.out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'x', 'y', 'z', 'dist', 'yaw_deg'])
        w.writerows(rows)
    yaws = [r[5] for r in rows]
    print(f"\n[pool_yaw_validation] {len(rows)} sample -> {args.out}")
    print(f"yaw_deg: min={min(yaws):+.2f} max={max(yaws):+.2f} "
          f"terakhir={yaws[-1]:+.2f}")


def _demo():
    """ponytail self-check: on_qr accumulates rows correctly from a fake pose stream."""
    rows = []

    def on_qr(result):
        pose = result.get('pose')
        if pose is None:
            return
        rows.append((0.0, pose['x'], pose['y'], pose['z'], pose['dist'], pose['yaw_deg']))

    on_qr({'pose': None})
    assert rows == []
    on_qr({'pose': {'x': 0.01, 'y': 0.0, 'z': 0.3, 'dist': 0.3, 'yaw_deg': 12.5}})
    assert len(rows) == 1 and rows[0][5] == 12.5
    print("pool_yaw_validation self-check OK")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--self-check':
        _demo()
    else:
        main()
