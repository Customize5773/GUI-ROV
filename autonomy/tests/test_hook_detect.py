"""
tests/test_hook_detect.py — Uji detect_hook() (deteksi hook PVC ujung-U, misi 3b/4).

Pola sama `test_qr_detect.py`: variasi pencahayaan/skala/offset + jenjang preprocessing
(contour/CLAHE/HoughCircle). Butuh cv2 + numpy; di-skip bila cv2 absen. Hook digambar
sintetis (dua batang vertikal + lengkung-U) — cukup utk menguji logika deteksi tanpa
kamera/kolam (validasi warna PVC & exposure nyata = backlog venue, lihat PR-AUTONOMY HOOK-01).
"""
import os
import sys

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)


def _make_hook(cv2, np, center=(320, 240), scale=1.0, bg=60, fg=200, contrast=1.0):
    """Gambar hook-U sintetis (BGR): dua batang vertikal + lengkung bawah. contrast<1
    menekan ke arah latar (mensimulasi cahaya buruk / air keruh)."""
    frame = np.full((480, 640, 3), bg, np.uint8)
    cx, cy = center
    pipe = int(12 * scale)      # setengah lebar pipa (px)
    hlen = int(90 * scale)      # tinggi batang
    ubot = int(35 * scale)      # radius lengkung-U
    col = (fg, fg, fg)
    cv2.rectangle(frame, (cx - ubot - pipe, cy - hlen), (cx - ubot + pipe, cy), col, -1)
    cv2.rectangle(frame, (cx + ubot - pipe, cy - hlen), (cx + ubot + pipe, cy), col, -1)
    cv2.ellipse(frame, (cx, cy), (ubot, ubot), 0, 0, 180, col, 2 * pipe)
    if contrast < 1.0:
        frame = (bg + (frame.astype(np.float32) - bg) * contrast).astype(np.uint8)
    return frame


def test_detect_hook_finds_center():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    det = detect_hook(_make_hook(cv2, np))
    assert det is not None
    assert det['type'] == 'hook'
    cx, cy = det['center']
    assert abs(cx - 320) < 40                 # terpusat horizontal (batang simetris)
    assert det['area'] > 150 and 0.0 <= det['confidence'] <= 1.0
    assert det['method'] == 'contour'         # jalur non-warna (utama)


@pytest.mark.parametrize('scale', [0.6, 1.0, 1.4])
def test_detect_hook_scale_invariant(scale):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    det = detect_hook(_make_hook(cv2, np, scale=scale))
    assert det is not None, f"hook skala {scale} tak terdeteksi"
    assert abs(det['center'][0] - 320) < 40


def test_detect_hook_tracks_offset():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    det = detect_hook(_make_hook(cv2, np, center=(200, 200)))
    assert det is not None
    assert det['center'][0] < 300             # ikut bergeser ke kiri dari pusat frame
    assert det['center'][0] > 120


def test_detect_hook_low_contrast_needs_enhance():
    """A/B pada frame SAMA: enhance=False (tanpa jalur contour/CLAHE) → None,
    enhance=True (CLAHE) memulihkan deteksi → membuktikan preprocessing pembedanya."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    frame = _make_hook(cv2, np, contrast=0.3)          # kontras rendah
    assert detect_hook(frame, enhance=False) is None   # tanpa warna & tanpa enhance → tak ada jalur
    assert detect_hook(frame, enhance=True) is not None


def test_detect_hook_blank_returns_none():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    blank = np.full((480, 640, 3), 128, np.uint8)
    assert detect_hook(blank) is None


def test_detect_hook_none_frame_returns_none():
    pytest.importorskip("cv2")
    from vision.hook_detect import detect_hook
    assert detect_hook(None) is None


def test_detect_hook_color_path():
    """hsv_range disediakan (warna PVC ter-tuning) → jenjang warna dipakai lebih dulu."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    frame = _make_hook(cv2, np, fg=210)
    det = detect_hook(frame, hsv_range=[[0, 0, 150], [180, 60, 255]])  # abu-abu terang low-sat
    assert det is not None
    assert det['method'] == 'color'
    assert abs(det['center'][0] - 320) < 40


def test_detect_hook_min_area_threshold():
    """min_area besar menolak semua kandidat → None (ambang tunable di kolam)."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    frame = _make_hook(cv2, np)
    assert detect_hook(frame, min_area=1e9) is None
    assert detect_hook(frame, min_area=150) is not None


def test_detect_hook_distance_monotonic_with_size():
    """Estimasi jarak (proxy) turun saat hook tampak lebih besar (lebih dekat). focal_px
    diberikan → pose diisi. Nilai absolut = proxy kasar (butuh validasi venue)."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    near = detect_hook(_make_hook(cv2, np, scale=1.4), focal_px=500)
    far = detect_hook(_make_hook(cv2, np, scale=0.6), focal_px=500)
    assert near['pose'] is not None and far['pose'] is not None
    assert near['pose']['z'] > 0 and far['pose']['z'] > 0
    assert near['pose']['z'] < far['pose']['z']   # lebih besar di frame → lebih dekat


def test_detect_hook_no_pose_without_focal():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    det = detect_hook(_make_hook(cv2, np))         # tanpa focal_px → IBVS (pose None)
    assert det is not None and det['pose'] is None


def test_detect_by_hough_circle_fallback():
    """Jenjang 3 (HoughCircles) menangkap lengkung-U / lubang payload (Ø3 cm)."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import _detect_by_hough
    frame = np.full((480, 640, 3), 60, np.uint8)
    cv2.circle(frame, (300, 240), 40, (210, 210, 210), 6)
    out = _detect_by_hough(frame, 150)
    assert out is not None
    best, method = out
    assert method == 'hough'
    assert abs(best[2][0] - 300) < 25              # pusat lingkaran ~terdeteksi


def test_detect_hook_result_compatible_with_servo():
    """Field hasil deteksi hook kompatibel dgn Mission5FSM._hook_servo_step (IBVS & PBVS)."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    det = detect_hook(_make_hook(cv2, np), focal_px=500)
    for key in ('center', 'area', 'frame_w', 'frame_h', 'pose'):
        assert key in det
    assert isinstance(det['center'], tuple) and len(det['center']) == 2
    assert det['frame_w'] == 640 and det['frame_h'] == 480
    assert set(det['pose'].keys()) >= {'x', 'y', 'z'}


# ── Regresi: blob raksasa (air keruh) TIDAK boleh lolos sebagai hook ────────
def test_detect_hook_rejects_full_frame_blob():
    """Uji kolam 22 Agu 2026: di air keruh, Canny menghasilkan SATU contour yang
    membungkus hampir seluruh frame. Karena bounding box-nya = frame, solidity ≈ 1,
    dan suku ukuran di _score_contour sudah jenuh di 4000 px² — jadi skornya justru
    SEMPURNA. Hasil nyata dari kamera WALL: center (640,360) persis titik tengah,
    area 917.542 dari 921.600 px² (99,6%), confidence 1,00, di SETIAP frame.

    Fatal bagi FSM, bukan cuma berisik: _hook_servo_step membacanya sebagai
    "hook tepat di tengah, sangat dekat", sehingga HANG/DOCK mengira sudah
    sejajar sempurna sejak frame pertama lalu mendudukkan payload ke tempat
    yang salah. HOOK_MIN_AREA tak menyaring apa pun di sini — yang hilang
    adalah batas ATAS (HOOK_MAX_AREA_FRAC)."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook

    # Frame yang hampir seluruhnya satu blob terang di atas latar gelap tipis —
    # meniru kontur raksasa hasil air keruh/kontras rendah.
    frame = np.full((480, 640, 3), 20, np.uint8)
    cv2.rectangle(frame, (2, 2), (637, 477), (220, 220, 220), -1)

    det = detect_hook(frame)
    if det is not None:
        frame_area = 480 * 640
        assert det['area'] <= 0.25 * frame_area, (
            f"blob seluas {det['area']:.0f} px² "
            f"({100 * det['area'] / frame_area:.1f}% frame) lolos sebagai hook")


def test_detect_hook_max_area_frac_configurable():
    """Batasnya harus bisa dilonggarkan/diketatkan di kolam tanpa edit kode."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook

    hook = _make_hook(cv2, np)
    # Hook sintetis normal jauh di bawah 25% frame → tetap terdeteksi.
    assert detect_hook(hook) is not None
    # Ambang mustahil (0.0001 = 30 px²) harus membuang bahkan hook yang sah,
    # membuktikan parameternya benar-benar mengalir sampai ke penyaring.
    assert detect_hook(hook, max_area_frac=0.0001) is None


def test_detect_hook_close_range_not_rejected():
    """Hook sah yang jauh LEBIH DEKAT (lebih besar) dari jarak docking nyata
    (~10% frame, ~8x lebih besar dari kasus setengah-jarak-docking) tetap
    lolos — HOOK_MAX_AREA_FRAC=0.25 hanya membuang blob patologis, bukan
    deteksi sah jarak dekat."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.hook_detect import detect_hook
    det = detect_hook(_make_hook(cv2, np, scale=2.0))
    assert det is not None, "hook jarak-dekat sah ikut tertolak oleh HOOK_MAX_AREA_FRAC"
    frame_area = 480 * 640
    frac = det['area'] / frame_area
    assert 0.05 < frac < 0.25, f"area {frac:.3f} di luar rentang uji yang dimaksud"
