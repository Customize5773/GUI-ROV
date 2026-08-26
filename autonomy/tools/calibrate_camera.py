#!/usr/bin/env python3
"""
tools/calibrate_camera.py — Kalibrasi kamera (checkerboard) → intrinsics utk PBVS (solvePnP).

Hasil disimpan .npz berisi: K (camera matrix), dist (distortion), image_size, rms.
Dipakai otomatis oleh VisionPipeline(calib_file=...) untuk menghitung pose marker.

PERSIAPAN
  - Cetak papan checkerboard (default 9x6 SUDUT-DALAM = papan 10x7 kotak), kotak ~25 mm,
    tempel di permukaan KAKU & RATA.  (generator: markhedleyjones.com/projects/calibration-checkerboard-collection)
  - pip install opencv-contrib-python numpy

PENTING UNTUK KAMERA DWE (bawah air):
  Refraksi air mengubah focal length efektif. Untuk akurasi jarak di kolam, kalibrasi
  DI DALAM AIR di balik housing/dome yang SAMA dengan saat misi (papan tahan air / di
  balik kaca akuarium). Kalibrasi di udara hanya pendekatan kasar.

PEMAKAIAN
  # Mode LIVE (webcam): kumpulkan ~15 pose beragam, lalu kalibrasi
  cd ~/GUI-ROV/autonomy
  # 3) Tes deteksi QR + servo pakai QR payload kamu (dari PDF, atau tampilkan di HP)
python tools/servo_webcam_test.py --device 0                      # IBVS (tanpa kalibrasi)
python tools/servo_webcam_test.py --device 0 --calib vision/calibration/laptop.npz  # PBVS
python tools/pose_webcam_test.py  --device 0 --calib vision/calibration/laptop.npz  # x/y/z meter
#    Filter QR tertentu: --data HYDROSHIP
  python tools/calibrate_camera.py --device 0 --cols 9 --rows 6 --square 25 \
         --out vision/calibration/dwe.npz
     SPACE = ambil frame (saat papan terdeteksi) · c = kalibrasi · q = keluar
     Tombol tak berfungsi? KLIK dulu jendela video (fokus keyboard) — atau pakai --auto.

  # Mode AUTO (tanpa tombol): pose diambil otomatis saat papan terdeteksi & digerakkan.
  # Simpan juga frame-nya agar bisa dikalibrasi ulang tanpa kamera.
  python tools/calibrate_camera.py --device 0 --auto --save-dir calib_imgs \
         --cols 9 --rows 6 --square 25 --out vision/calibration/dwe.npz

  # Mode STREAM ROV (--url): kalibrasi pakai kamera ROV langsung (bottom & wall),
  # bukan webcam laptop. URL sesuai public/js/config.js (CAMERAS[].url).
  # Jalankan SEKALI PER KAMERA (satu proses = satu kamera), --out berbeda tiap kamera.
  # Lakukan DI DALAM AIR di kolam (lihat catatan refraksi di atas) sambil papan
  # catur digerakkan perlahan di depan kamera.
  python tools/calibrate_camera.py --url http://192.168.2.2:8080/stream --auto \
         --save-dir calib_imgs_bottom --cols 9 --rows 6 --square 25 \
         --out vision/calibration/bottom.npz
  python tools/calibrate_camera.py --url http://192.168.2.2:8081/stream --auto \
         --save-dir calib_imgs_wall --cols 9 --rows 6 --square 25 \
         --out vision/calibration/wall.npz

  # Mode FOLDER (dari gambar tersimpan — paling anti-gagal, tak butuh GUI/tombol)
  python tools/calibrate_camera.py --from-folder calib_imgs --cols 9 --rows 6 --square 25 \
         --out vision/calibration/dwe.npz

  # Dataset BESAR (ratusan+ pose, mis. hasil tools/select_calib_frames.py dari
  # video): tambah --trim-rounds utk membuang pose ber-reprojection-error
  # terburuk & kalibrasi ulang berulang -- menekan RMS jauh lebih efektif drpd
  # cuma menambah pose (blur/riak air bikin sebagian pose jauh lebih noisy).
  python tools/calibrate_camera.py --from-folder calib_imgs_v3 --cols 10 --rows 7 \
         --square 25 --trim-rounds 4 --out vision/calibration/dwe_v3.npz
"""
import argparse
import glob
import os
import sys
import time

import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--device", type=int, default=0)
ap.add_argument("--url", default=None,
                help="stream kamera ROV (mis. http://192.168.2.2:8080/stream) — override --device")
ap.add_argument("--from-folder", default=None, help="kalibrasi dari folder gambar (*.png/*.jpg)")
ap.add_argument("--cols", type=int, default=9, help="jumlah SUDUT-DALAM per baris")
ap.add_argument("--rows", type=int, default=6, help="jumlah SUDUT-DALAM per kolom")
ap.add_argument("--square", type=float, default=25.0, help="ukuran kotak (mm) — tak memengaruhi K")
ap.add_argument("--need", type=int, default=15, help="jumlah pose minimum (mode live)")
ap.add_argument("--out", default="vision/calibration/dwe.npz")
# ── Auto-capture: tak perlu tekan tombol (jaga-jaga bila SPACE/c tak tertangkap
#    karena fokus keyboard bukan di jendela video, umum di macOS/WSL) ──
ap.add_argument("--auto", action="store_true",
                help="auto-capture pose otomatis saat papan terdeteksi (tanpa tombol)")
ap.add_argument("--interval", type=float, default=1.5,
                help="jeda detik antar auto-capture (mode --auto)")
ap.add_argument("--move", type=float, default=25.0,
                help="mode --auto: min pergeseran papan (px) dari capture terakhir agar variasi pose")
ap.add_argument("--save-dir", default=None,
                help="simpan frame tertangkap ke folder ini (bisa dikalibrasi ulang via --from-folder)")
ap.add_argument("--trim-rounds", type=int, default=0,
                help="buang pose ber-reprojection-error TERBURUK lalu kalibrasi ulang, "
                     "berulang N ronde (0 = mati, perilaku lama). Blur/riak bikin sebagian "
                     "pose jauh lebih noisy dari yang lain -- membuangnya menekan RMS jauh "
                     "lebih efektif drpd cuma menambah pose. Butuh cukup pose awal (mode "
                     "--from-folder dgn dataset besar) supaya sisa akhir tetap >= --need.")
ap.add_argument("--trim-frac", type=float, default=0.2,
                help="fraksi pose terburuk yg dibuang tiap ronde --trim-rounds (default 20%%)")
args = ap.parse_args()

PAT = (args.cols, args.rows)
CRIT = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)

# titik objek papan (z=0), satuan mm (skala tak memengaruhi K)
objp = np.zeros((args.rows * args.cols, 3), np.float32)
objp[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2) * args.square

obj_points, img_points = [], []
image_size = None


def try_frame(gray, vis=None):
    """Cari checkerboard; jika ketemu refine + return corners."""
    ok, corners = cv2.findChessboardCorners(
        gray, PAT, cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok:
        return None
    corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), CRIT)
    if vis is not None:
        cv2.drawChessboardCorners(vis, PAT, corners, ok)
    return corners


def calibrate_and_save():
    if len(obj_points) < 5:
        print(f"Terlalu sedikit pose ({len(obj_points)}) — butuh >=5 (ideal {args.need}).")
        return False
    # Salinan lokal — trim TIDAK boleh menghapus pose dari list global, kalau
    # calibrate_and_save() dipanggil di tengah sesi live (mode --auto/SPACE)
    # capture harus tetap bisa lanjut menumpuk dari state semula.
    obj, img = list(obj_points), list(img_points)
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(obj, img, image_size, None, None)

    # --trim-rounds: buang pose ber-reprojection-error TERBURUK & kalibrasi ulang,
    # berulang. Sebagian pose (blur ekstraksi video, riak/refraksi air) jauh lebih
    # noisy dari yang lain — beberapa pose buruk saja bisa menyeret RMS keseluruhan,
    # dan membuangnya menekan RMS jauh lebih efektif drpd sekadar menambah pose.
    # Diverifikasi di dataset kalibrasi bawah air KKI 2026: 144 pose RMS 1.94 ->
    # 4 ronde (buang 20%/ronde) -> 58 pose RMS 0.87.
    for rnd in range(args.trim_rounds):
        if len(obj) * (1.0 - args.trim_frac) < args.need:
            print(f"  [trim] berhenti di ronde {rnd}: sisa pose akan turun di bawah --need")
            break
        errs = []
        for i in range(len(obj)):
            proj, _ = cv2.projectPoints(obj[i], rvecs[i], tvecs[i], K, dist)
            proj = proj.reshape(-1, 1, 2).astype(np.float32)
            gt = img[i].reshape(-1, 1, 2).astype(np.float32)
            errs.append(float(np.sqrt(np.mean(np.sum((proj - gt) ** 2, axis=2)))))
        errs = np.asarray(errs)
        keep = errs <= np.percentile(errs, (1.0 - args.trim_frac) * 100)
        dropped = int((~keep).sum())
        obj = [o for o, k in zip(obj, keep) if k]
        img = [p for p, k in zip(img, keep) if k]
        rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(obj, img, image_size, None, None)
        print(f"  [trim] ronde {rnd + 1}: buang {dropped} pose terburuk -> "
              f"RMS={rms:.3f} (sisa {len(obj)} pose)")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    np.savez(args.out, K=K, dist=dist, image_size=np.array(image_size), rms=rms)
    print(f"\n[OK] disimpan: {args.out}  ({len(obj)} pose dipakai)")
    print(f"  RMS reproj error = {rms:.3f} px (bagus bila < 0.5; <1.0 masih oke)")
    print(f"  K =\n{K}\n  dist = {dist.ravel()}")
    print(f"  Pakai: VisionPipeline(source='usb', calib_file='{args.out}', qr_length=<meter>)")
    return True


# ── Mode FOLDER ──────────────────────────────────────────────────────────────
if args.from_folder:
    files = sorted(glob.glob(os.path.join(args.from_folder, "*.png")) +
                   glob.glob(os.path.join(args.from_folder, "*.jpg")) +
                   glob.glob(os.path.join(args.from_folder, "*.jpeg")))
    if not files:
        sys.exit(f"Tidak ada gambar di {args.from_folder}")
    for f in files:
        img = cv2.imread(f)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        image_size = gray.shape[::-1]
        c = try_frame(gray)
        if c is not None:
            obj_points.append(objp.copy()); img_points.append(c)
            print(f"  ✓ {os.path.basename(f)}")
        else:
            print(f"  ✗ papan tak terdeteksi: {os.path.basename(f)}")
    calibrate_and_save()
    sys.exit(0)

# ── Mode LIVE ────────────────────────────────────────────────────────────────
src = args.url if args.url else args.device
cap = cv2.VideoCapture(src)
if not cap.isOpened():
    sys.exit(f"Tidak bisa membuka sumber kamera: {src}")

last_cap_t = 0.0
last_center = None


def _centroid(corners):
    return corners.reshape(-1, 2).mean(axis=0)


def capture(corners, raw):
    """Rekam satu pose (+ simpan frame mentah bila --save-dir)."""
    obj_points.append(objp.copy())
    img_points.append(corners)
    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
        cv2.imwrite(os.path.join(args.save_dir, f"calib_{len(obj_points):02d}.png"), raw)
    print(f"  + pose {len(obj_points)}/{args.need}")


print("Tunjukkan PAPAN CATUR (checkerboard) ke kamera — BUKAN QR code.")
print("  Belum punya papan? Cetak dulu: python tools/make_checkerboard.py --cols 9 --rows 6")
print("  (Kalibrasi ini OPSIONAL — hanya utk PBVS/jarak meter. Tes deteksi QR + servo")
print("   bisa langsung pakai tools/servo_webcam_test.py TANPA kalibrasi.)")
if args.auto:
    print(f"LIVE AUTO: pose diambil OTOMATIS tiap papan catur terdeteksi & digerakkan "
          f"(tiap ~{args.interval}s). Kalibrasi otomatis saat {args.need} pose. q=keluar.")
else:
    print("LIVE: SPACE=ambil · c=kalibrasi · q=keluar")
    print("  ⚠ Tombol tak berfungsi? KLIK dulu jendela video (fokus keyboard), "
          "atau jalankan dgn --auto (tanpa tombol).")

while True:
    ok, frame = cap.read()
    if not ok:
        break
    raw = frame.copy()                          # sebelum digambari sudut (utk --save-dir)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    image_size = gray.shape[::-1]
    corners = try_frame(gray, vis=frame)

    # ── auto-capture: ambil bila papan terdeteksi, sudah lewat interval, & papan
    #    bergerak cukup jauh dari capture terakhir (agar variasi pose) ──
    if args.auto and corners is not None:
        now = time.time()
        c = _centroid(corners)
        moved = last_center is None or float(np.linalg.norm(c - last_center)) > args.move
        if (now - last_cap_t) > args.interval and moved:
            capture(corners, raw)
            last_cap_t, last_center = now, c
            if len(obj_points) >= args.need and calibrate_and_save():
                break

    mode = "AUTO" if args.auto else "SPACE=ambil"
    cv2.putText(frame, f"pose: {len(obj_points)}/{args.need}  [{mode}]"
                + ("  papan catur OK" if corners is not None else "  cari PAPAN CATUR (bukan QR)"),
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 0) if corners is not None else (0, 0, 255), 2)
    cv2.imshow("Kalibrasi (SPACE/c/q)", frame)

    k = cv2.waitKey(1) & 0xFF
    if k == ord('q'):
        break
    elif k == ord(' ') and corners is not None:   # manual tetap ada
        capture(corners, raw)
    elif k == ord('c'):
        if calibrate_and_save():
            break

cap.release()
cv2.destroyAllWindows()
