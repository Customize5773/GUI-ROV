"""
vision/hook_detect.py — KKI 2026 ROV Hook (Shepherd's-Hook) Vision Pipeline
============================================================================
Deteksi **hook PVC** (pipa ¾" / 25 mm, ujung bawah berbentuk **U** / candy-cane)
di dinding kolam, dari kamera CAM WALL. Dipakai untuk docking closed-loop:

  - Misi 3b (HANG) — menggantungkan payload ke hook (mendekat + sejajar sblm lepas gripper).
  - Misi 4  (DOCK) — surface docking bersandar ke sisi dinding payload.

Berbeda dengan `qr_detect.py` (target planar dgn simbol yang bisa di-decode), hook
**tidak punya QR/marker sendiri** — targetnya geometrik: silinder/pipa berujung U.
Karena itu deteksi memakai bentuk (contour/edge/lingkaran), BUKAN hanya warna.

Output `detect_hook(frame) -> dict | None`:
    {
      'type': 'hook',
      'center': (cx, cy),        # pusat piksel hook di frame
      'bbox': (x, y, w, h),      # bounding box piksel
      'area': float,             # luas contour (px^2) — proxy jarak (mirip area QR)
      'width_px': float,         # lebar pipa (px) — utk estimasi jarak dari diameter fisik
      'confidence': float,       # 0..1 skor keyakinan (solidity × ukuran × bentuk)
      'method': 'color'|'contour'|'hough',  # jenjang mana yang berhasil
      'frame_w', 'frame_h': int,
      'pose': {x,y,z,dist} | None,  # estimasi 3D bila focal_px diberikan (kamera terkalibrasi)
      'timestamp': float,
    }

Bentuk `dict` dibuat **kompatibel** dgn `Mission5FSM._hook_servo_step()` — field
`center`/`area`/`frame_w`/`frame_h` (IBVS) dan `pose` (PBVS) sama seperti deteksi QR,
sehingga `VisualServo`/`PoseServo` yang sudah ada bisa langsung dipakai (tanpa kelas baru).

Preprocessing **berjenjang** (pola sama `decode_qr()` — berhenti di jenjang pertama
yang berhasil, jalur cepat tetap cepat):
  1. Color mask (HANYA bila `hsv_range` disediakan) → contour terbesar berbentuk pipa.
  2. Grayscale + CLAHE → Canny edge → contour (jalur utama non-warna, visibilitas rendah).
  3. HoughCircles (menangkap lengkung-U / lubang) sbg fallback bila contour gagal.

┌─ ASUMSI / CATATAN TUNING KOLAM (butuh validasi venue/hardware — lihat PR-AUTONOMY.md HOOK-01) ─┐
│ • Warna PVC hook TAK disebut pasti di Panduan KKI 2026 (kemungkinan putih/abu-abu polos).      │
│   Karena itu `hsv_range` default = None → deteksi jatuh ke jalur non-warna (contour/edge).     │
│   JANGAN hardcode satu HSV sbg satu-satunya jalur; set `hsv_range` hanya setelah warna PVC     │
│   asli & kontrasnya terhadap dinding kolam terukur di venue.                                    │
│ • Exposure/gain kamera WALL, kekeruhan air, dan glare permukaan mengubah kontras drastis →     │
│   ambang (`min_area`, Canny) WAJIB di-tuning ulang di kolam.                                    │
│ • Estimasi jarak (`width_px` → z) memakai diameter pipa fisik 25 mm & focal length kamera;     │
│   ini proxy KASAR (lebar pipa di piksel sulit stabil pada bentuk-U) — verifikasi di air.       │
└────────────────────────────────────────────────────────────────────────────────────────────────┘

Instalasi dependensi (sama dgn qr_detect): `pip install opencv-python numpy`.
"""

import time
import logging
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False
    log.warning("[hook] opencv-python tidak tersedia — deteksi hook dinonaktifkan")


# ── Parameter default deteksi (semua OPSIONAL, bisa dioverride via config) ────────
# NOTE: nilai ini titik-awal uji-meja; WAJIB di-tuning ulang di kolam (lihat asumsi di atas).
HOOK_MIN_AREA     = 150.0    # luas contour minimum (px^2) agar dianggap kandidat hook
HOOK_CLAHE_CLIP   = 2.0      # kekuatan CLAHE (lawan cahaya tak rata / glare) — sama gaya qr_detect
HOOK_CLAHE_TILE   = 8        # ukuran grid CLAHE
HOOK_CANNY_LO     = 50       # ambang bawah Canny edge
HOOK_CANNY_HI     = 150      # ambang atas Canny edge
HOOK_PIPE_DIAM_M  = 0.025    # diameter fisik pipa PVC ¾" (25 mm) — utk estimasi jarak
# Rentang rasio-aspek bounding box yang masuk akal utk hook-U (lebar:tinggi). Longgar
# karena orientasi kamera bervariasi; dipakai hanya utk menolak blob yang jelas bukan pipa.
HOOK_ASPECT_MIN   = 0.15
HOOK_ASPECT_MAX   = 6.0
HOOK_COLOR_HSV_RANGE = None  # None → JANGAN pakai jalur warna (lihat asumsi). [[h,s,v],[h,s,v]] bila di-tune.


def _to_gray_clahe(frame, clip=HOOK_CLAHE_CLIP, tile=HOOK_CLAHE_TILE):
    """BGR/gray → grayscale + CLAHE (kontras lokal). Sama gaya `qr_detect._to_gray_clahe`."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile))
    return clahe.apply(gray)


def _score_contour(cnt, min_area):
    """Nilai satu contour sbg kandidat hook → (area, bbox, width_px, confidence) atau None.

    confidence = solidity (area/luas-bbox) dibobot ukuran; menolak blob yg terlalu kecil
    atau rasio-aspek tak masuk akal utk pipa berujung-U."""
    area = float(cv2.contourArea(cnt))
    if area < min_area:
        return None
    x, y, w, h = cv2.boundingRect(cnt)
    if w <= 0 or h <= 0:
        return None
    aspect = w / float(h)
    if not (HOOK_ASPECT_MIN <= aspect <= HOOK_ASPECT_MAX):
        return None
    solidity = area / float(w * h)
    # lebar pipa (px): sisi PENDEK dari minAreaRect ≈ diameter pipa (bentuk memanjang).
    rect = cv2.minAreaRect(cnt)
    (_, _), (rw, rh), _ = rect
    width_px = float(min(rw, rh)) if rw > 0 and rh > 0 else float(min(w, h))
    # keyakinan: solidity (0..1) dominan, sedikit ditarik naik oleh ukuran relatif.
    conf = float(np.clip(0.6 * solidity + 0.4 * min(1.0, area / 4000.0), 0.0, 1.0))
    cx, cy = x + w / 2.0, y + h / 2.0
    return area, (x, y, w, h), (cx, cy), width_px, conf


def _best_contour(mask, min_area):
    """Ambil contour terbaik (confidence tertinggi) dari mask biner. None bila tak ada."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for cnt in cnts:
        scored = _score_contour(cnt, min_area)
        if scored is None:
            continue
        if best is None or scored[4] > best[4]:
            best = scored
    return best


def _detect_by_color(frame, hsv_range, min_area):
    """Jenjang 1: mask HSV → contour. Hanya dipakai bila hsv_range disediakan (di-tune venue)."""
    if frame.ndim != 3:
        return None
    lower = np.array(hsv_range[0], dtype=np.uint8)
    upper = np.array(hsv_range[1], dtype=np.uint8)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    best = _best_contour(mask, min_area)
    return (best, 'color') if best else None


def _detect_by_contour(frame, min_area, canny_lo, canny_hi):
    """Jenjang 2: grayscale+CLAHE → Canny edge → dilate → contour. Jalur UTAMA non-warna."""
    gray = _to_gray_clahe(frame)
    edges = cv2.Canny(gray, canny_lo, canny_hi)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    best = _best_contour(edges, min_area)
    return (best, 'contour') if best else None


def _detect_by_hough(frame, min_area):
    """Jenjang 3: HoughCircles menangkap lengkung-U / lubang payload (Ø3 cm) sbg fallback."""
    gray = _to_gray_clahe(frame)
    gray = cv2.medianBlur(gray, 5)
    minr = max(3, int((min_area / 3.14) ** 0.5))
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=40,
                               param1=100, param2=30, minRadius=minr, maxRadius=0)
    if circles is None:
        return None
    circles = np.round(circles[0, :]).astype(int)
    # pilih lingkaran terbesar (paling dekat/paling jelas)
    cx, cy, r = max(circles, key=lambda c: c[2])
    area = float(np.pi * r * r)
    if area < min_area:
        return None
    bbox = (int(cx - r), int(cy - r), int(2 * r), int(2 * r))
    conf = float(np.clip(0.4 + 0.3 * min(1.0, area / 4000.0), 0.0, 0.8))  # < contour: fallback
    best = (area, bbox, (float(cx), float(cy)), float(2 * r), conf)
    return best, 'hough'


def detect_hook(frame,
                hsv_range=HOOK_COLOR_HSV_RANGE,
                min_area=HOOK_MIN_AREA,
                pipe_diam_m=HOOK_PIPE_DIAM_M,
                focal_px: Optional[float] = None,
                canny_lo=HOOK_CANNY_LO,
                canny_hi=HOOK_CANNY_HI,
                enhance=True) -> Optional[dict]:
    """Deteksi hook dari 1 frame → dict (lihat modul docstring) atau None.

    Jenjang berhenti di yg pertama berhasil (pola `decode_qr`):
      1. color mask (bila `hsv_range` diberikan) — JANGAN andalkan warna sbg satu-satunya jalur.
      2. grayscale+CLAHE → Canny contour (utama, non-warna).
      3. HoughCircles (lengkung-U / lubang) — fallback.
    enhance=False → hanya jenjang 1 bila ada warna, else langsung None (utk benchmark A/B).

    focal_px : panjang fokus kamera (px, mis. K[0,0] hasil kalibrasi). Bila diberikan,
               `pose` diisi dgn estimasi jarak z = focal_px·pipe_diam_m / width_px (proxy kasar).
    """
    if not CV2_OK or frame is None:
        return None

    found = None
    if hsv_range is not None:
        found = _detect_by_color(frame, hsv_range, min_area)
    if found is None and enhance:
        found = _detect_by_contour(frame, min_area, canny_lo, canny_hi)
    if found is None and enhance:
        found = _detect_by_hough(frame, min_area)
    if found is None:
        return None

    (area, bbox, center, width_px, conf), method = found
    h, w = frame.shape[0], frame.shape[1]
    result = {
        'type': 'hook',
        'center': (int(round(center[0])), int(round(center[1]))),
        'bbox': tuple(int(v) for v in bbox),
        'area': float(area),
        'width_px': float(width_px),
        'confidence': float(conf),
        'method': method,
        'frame_w': int(w),
        'frame_h': int(h),
        'pose': None,
        'timestamp': time.time(),
    }
    # Estimasi 3D kasar bila kalibrasi (focal_px) tersedia — meniru PBVS QR (kalib → pose).
    if focal_px and width_px > 1e-3:
        z = float(focal_px) * float(pipe_diam_m) / float(width_px)   # jarak ke pipa (m)
        cx, cy = center
        x = (cx - w / 2.0) * z / float(focal_px)                     # camera-frame OpenCV
        y = (cy - h / 2.0) * z / float(focal_px)
        result['pose'] = {'x': x, 'y': y, 'z': z, 'dist': float((x * x + y * y + z * z) ** 0.5)}
    return result


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    ap = argparse.ArgumentParser(description='Uji detect_hook dari webcam/gambar')
    ap.add_argument('--device', type=int, default=0)
    args = ap.parse_args()
    if not CV2_OK:
        raise SystemExit("opencv tidak tersedia")
    cap = cv2.VideoCapture(args.device)
    print("[test] Tekan Ctrl+C untuk berhenti")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            det = detect_hook(frame)
            if det:
                print(f"  → hook center={det['center']} area={det['area']:.0f} "
                      f"conf={det['confidence']:.2f} method={det['method']}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    cap.release()
