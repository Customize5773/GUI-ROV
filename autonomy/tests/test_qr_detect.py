"""
tests/test_qr_detect.py — Uji decode_qr() robust (preprocessing berjenjang).

Membuktikan perbaikan isu Fase 0 ("QR terdeteksi hanya jarak dekat / sensitif cahaya"):
QR yang diperkecil + kontras-rendah GAGAL di pyzbar.decode() mentah, tapi BERHASIL lewat
decode_qr() (grayscale+CLAHE / upscale). Butuh cv2 + pyzbar + segno; di-skip bila absen.
"""
import os
import sys
import tempfile

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)


# Helper render/tempel QR kini dipakai bersama tests/test_qr_underwater.py dan
# harness dataset → dipindah ke tests/underwater_sim.py (perilaku identik).
# Wrapper tipis ini menjaga pemanggilan lama (cv2, np, segno dioper) tetap apa adanya.
def _render_qr_bgr(cv2, np, segno, text, module_px, border=4):
    """Render QR -> gambar BGR, ukuran modul = module_px piksel/modul."""
    from tests.underwater_sim import render_qr_bgr
    return render_qr_bgr(text, module_px=module_px, border=border, segno=segno)


def _place_on_canvas(cv2, np, qr_bgr, canvas_wh=(640, 480), contrast=1.0, offset=(0, 0)):
    """Tempel QR di kanvas abu-abu; contrast<1 menurunkan kontras (mensimulasi cahaya buruk)."""
    from tests.underwater_sim import place_on_canvas
    return place_on_canvas(qr_bgr, canvas_wh=canvas_wh, contrast=contrast, offset=offset)


def test_decode_qr_clear_uses_fast_path():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    from vision.qr_detect import decode_qr
    import json
    text = json.dumps({"mission": 5, "team": "HYDROSHIP", "type": "payload", "id": "A"})
    qr = _render_qr_bgr(cv2, np, segno, text, module_px=8)
    frame, _ = _place_on_canvas(cv2, np, qr)
    res = decode_qr(frame)
    assert len(res) == 1
    assert res[0]['data'] == text            # JSON utuh (tak di-uppercase)
    assert res[0]['pts'].shape[0] >= 4


def test_decode_qr_prefers_checksum_verified_zxing_fast_path(monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    pts = np.float32([[10, 10], [30, 10], [30, 30], [10, 30]])
    monkeypatch.setattr(qd, "ZXING_OK", True)
    monkeypatch.setattr(qd, "_zxing_qr", lambda gray, scale=1.0: [
        {"data": "B", "pts": pts}
    ])
    monkeypatch.setattr(
        qd.pyzbar, "decode",
        lambda image: pytest.fail("pyzbar tidak boleh dipanggil setelah ZXing 1x sukses"),
    )

    frame = np.zeros((80, 100, 3), np.uint8)
    assert qd.decode_qr(frame, enhance=True)[0]["data"] == "B"


# ── Escalation + rescale (deterministik, tak bergantung ketangguhan pyzbar nyata) ──
# pyzbar SANGAT tangguh pada gambar sintetis bersih → sulit memaksa "raw gagal" secara
# deterministik. Jadi logika berjenjang & koreksi koordinat diuji dgn memalsukan
# pyzbar.decode: jenjang mana yang berhasil dikontrol, lalu dicek pts termap ke frame ASLI.
class _FakePt:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _FakeSym:
    type = 'QRCODE'

    def __init__(self, data, poly):
        self.data = data
        self.polygon = [_FakePt(x, y) for x, y in poly]


def test_decode_qr_escalates_to_upscale_and_rescales_coords(monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    calls = {'n': 0}

    def fake_decode(img):
        calls['n'] += 1
        # jenjang 1 (raw), 2 (CLAHE) & 3 (adaptive-threshold) gagal; jenjang 4
        # (upscale UPSCALE×) berhasil, mengembalikan polygon di koordinat
        # gambar yg SUDAH diperbesar (200 px).
        if calls['n'] < 4:
            return []
        return [_FakeSym(b'{"id":"A"}',
                         [(200, 200), (240, 200), (240, 240), (200, 240)])]

    # Kanvas seragam tak punya quiet zone nyata — bypass gate itu, test ini soal
    # urutan/hitungan eskalasi jenjang, bukan geometri quiet zone (diuji terpisah).
    monkeypatch.setattr(qd, '_quiet_zone_ok', lambda *a, **kw: True)
    monkeypatch.setattr(qd.pyzbar, 'decode', fake_decode)
    frame = np.full((480, 640, 3), 128, np.uint8)
    res = qd.decode_qr(frame, enhance=True)

    assert calls['n'] == 4, "harus mengeskalasi raw -> CLAHE -> adaptive-threshold -> upscale"
    assert len(res) == 1 and res[0]['data'] == '{"id":"A"}'
    # koordinat harus dibagi UPSCALE agar kembali ke frame ASLI (200/2 = 100)
    pts = res[0]['pts']
    assert abs(pts[:, 0].min() - 100) < 1e-3 and abs(pts[:, 1].min() - 100) < 1e-3


def test_decode_qr_escalates_to_adaptive_threshold(monkeypatch):
    """jenjang 3 (adaptive-threshold) harus dicoba SEBELUM upscale, dan bisa
    berhasil sendiri tanpa perlu naik ke upscale (target: latar berfaset/riak
    yang lolos CLAHE tapi terpisah oleh threshold lokal)."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    calls = {'n': 0}

    def fake_decode(img):
        calls['n'] += 1
        if calls['n'] < 3:
            return []
        return [_FakeSym(b'{"id":"B"}',
                         [(10, 10), (50, 10), (50, 50), (10, 50)])]

    monkeypatch.setattr(qd, '_quiet_zone_ok', lambda *a, **kw: True)
    monkeypatch.setattr(qd.pyzbar, 'decode', fake_decode)
    frame = np.full((480, 640, 3), 128, np.uint8)
    res = qd.decode_qr(frame, enhance=True)

    assert calls['n'] == 3, "harus berhenti di adaptive-threshold, tak lanjut ke upscale"
    assert len(res) == 1 and res[0]['data'] == '{"id":"B"}'


def test_decode_qr_enhance_false_stops_at_raw(monkeypatch):
    """enhance=False HANYA memanggil pyzbar sekali (frame mentah); enhance=True
    mengeskalasi (>1 panggilan) → membuktikan preprocessing hanya jalan saat diminta."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    calls = {'n': 0}
    # fallback QRCodeDetector memakai cv2, bukan pyzbar → tak menambah hitungan panggilan ini
    monkeypatch.setattr(qd.pyzbar, 'decode', lambda img: calls.__setitem__('n', calls['n'] + 1) or [])
    frame = np.full((480, 640, 3), 128, np.uint8)

    calls['n'] = 0
    assert qd.decode_qr(frame, enhance=False) == []
    assert calls['n'] == 1                    # hanya frame mentah

    calls['n'] = 0
    assert qd.decode_qr(frame, enhance=True) == []
    assert calls['n'] == 4                     # raw + CLAHE + adaptive-threshold + upscale


def test_decode_qr_pts_map_to_original_frame_coords():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    from vision.qr_detect import decode_qr

    # payload realistis (teks pendek spt '{"id":"B"}' membuat QR versi-1 yg pyzbar
    # sering gagal decode — bukan soal decode_qr; pakai payload penuh spt di lapangan)
    text = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"B"}'
    qr = _render_qr_bgr(cv2, np, segno, text, module_px=6)
    offset = (80, -40)                        # geser QR dari pusat
    frame, (x0, y0, w, h) = _place_on_canvas(cv2, np, qr, offset=offset)

    res = decode_qr(frame)
    assert res, "QR tak terdeteksi"
    pts = res[0]['pts']
    cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
    # pusat deteksi harus dekat pusat QR yang ditempel di frame ASLI (toleransi longgar)
    exp_cx, exp_cy = x0 + w / 2, y0 + h / 2
    assert abs(cx - exp_cx) < w * 0.5 and abs(cy - exp_cy) < h * 0.5


def test_decode_tracked_roi_maps_points_back_to_frame(monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    frame = np.zeros((200, 300, 3), np.uint8)
    seen = {}

    def fake_decode(crop, enhance=True):
        seen["shape"] = crop.shape[:2]
        return [{"data": "B", "pts": np.float32([[5, 6], [15, 6], [15, 16], [5, 16]])}]

    monkeypatch.setattr(qd, "ZXING_OK", False)
    monkeypatch.setattr(qd, "decode_qr", fake_decode)
    out = qd._decode_tracked_roi(frame, np.float32([[100, 80], [120, 80],
                                                       [120, 100], [100, 100]]))
    assert out[0]["data"] == "B"
    assert seen["shape"] == (56, 56)
    np.testing.assert_allclose(out[0]["pts"],
                               np.float32([[87, 68], [97, 68], [97, 78], [87, 78]]))


def test_decode_tracked_roi_zxing_miss_returns_to_full_frame(monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    monkeypatch.setattr(qd, "ZXING_OK", True)
    monkeypatch.setattr(qd, "_zxing_qr", lambda gray: [])
    monkeypatch.setattr(
        qd, "decode_qr",
        lambda *args, **kwargs: pytest.fail("ROI tidak boleh menjalankan cascade mahal"),
    )
    frame = np.zeros((200, 300, 3), np.uint8)
    pts = np.float32([[100, 80], [120, 80], [120, 100], [100, 100]])

    assert qd._decode_tracked_roi(frame, pts) == []


def test_hook_detector_can_be_disabled_for_qr_only_bench(monkeypatch):
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    monkeypatch.setattr(
        qd, "detect_hook",
        lambda *args, **kwargs: pytest.fail("detector hook tidak boleh berjalan"),
    )
    vp = qd.VisionPipeline(hook_enabled=False)

    assert vp._detect_hook(np.zeros((20, 20, 3), np.uint8), None) is None


def test_tracked_frame_miss_skips_expensive_decode_cascade(monkeypatch):
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    vp = qd.VisionPipeline()
    vp._last_qr_pts = np.float32([[10, 10], [30, 10], [30, 30], [10, 30]])
    vp._last_qr_pts_time = qd.time.time()
    monkeypatch.setattr(qd, "ZXING_OK", True)
    monkeypatch.setattr(qd, "_decode_tracked_roi",
                        lambda frame, pts, full_cascade=False: [])
    monkeypatch.setattr(
        qd, "decode_qr",
        lambda frame, enhance=True: pytest.fail(
            "tracking miss tidak boleh masuk cascade frame-penuh"),
    )
    # Fallback yang BOLEH dipakai: sapuan multi-proyeksi skala 1x saja
    # (~0,02 s) — jangan sampai diam-diam naik ke skala besar.
    seen = []
    monkeypatch.setattr(qd, "_sweep_projections",
                        lambda frame, scales=qd.SWEEP_SCALES: seen.append(scales) or [])

    assert vp._decode_qr_frame(np.zeros((80, 100, 3), np.uint8)) == []
    assert seen == [(1.0,)]


def test_decode_qr_no_qr_returns_empty():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.qr_detect import decode_qr
    blank = np.full((480, 640, 3), 200, np.uint8)
    assert decode_qr(blank) == []


# ── Jenjang-6: zxing-cpp (toleransi tilt/perspective yg pyzbar+cv2 tak punya) ──
# Divalidasi manual sebelum ditambahkan: pipeline pyzbar/CLAHE/upscale/cv2 (jenjang
# 1-5) SUDAH gagal total pada tilt 20°, sedangkan zxing-cpp bertahan sampai ~45°.
# ROV jarang persis tegak lurus ke payload sebelum visual servo align penuh, jadi
# ini kelas kegagalan nyata, bukan kasus buatan.
def _warp_tilt(cv2, np, img, angle_deg, cx, cy, w, h):
    """Simulasikan lihat QR dari sudut (tilt sekitar sumbu vertikal) via homografi."""
    ang = np.radians(angle_deg)
    shrink = np.sin(ang) * w * 0.5
    src = np.float32([[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2],
                      [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]])
    dst = np.float32([[cx - w / 2 + shrink, cy - h / 2], [cx + w / 2 - shrink, cy - h / 2],
                      [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]])
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, M, (img.shape[1], img.shape[0]), borderValue=(128, 128, 128))


def test_decode_qr_zxing_rescues_tilted_qr_pyzbar_cv2_miss():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    pytest.importorskip("zxingcpp")
    import vision.qr_detect as qd
    if not qd.ZXING_OK:
        pytest.skip("zxingcpp terpasang tapi gagal diimpor di qr_detect (lihat log)")
    text = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"A"}'
    qr = _render_qr_bgr(cv2, np, segno, text, module_px=6)
    frame, (x0, y0, w, h) = _place_on_canvas(cv2, np, qr, canvas_wh=(640, 480))
    tilted = _warp_tilt(cv2, np, frame, 30.0, x0 + w / 2, y0 + h / 2, w, h)

    res = qd.decode_qr(tilted, enhance=True)
    assert res and res[0]['data'] == text, "jenjang zxing seharusnya membaca QR tilt 30°"


def test_zxing_qr_rescales_checksum_verified_position(monkeypatch):
    """Fallback ZXing 2x/4x harus mengembalikan koordinat frame asli."""
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    class _Corner:
        def __init__(self, x, y):
            self.x, self.y = x, y

    class _Position:
        top_left = _Corner(200, 120)
        top_right = _Corner(280, 120)
        bottom_right = _Corner(280, 200)
        bottom_left = _Corner(200, 200)

    class _Result:
        text = "B"
        position = _Position()

    class _Format:
        QRCode = object()

    class _ZXing:
        BarcodeFormat = _Format

        @staticmethod
        def read_barcodes(image, formats):
            assert formats is _Format.QRCode
            return [_Result()]

    monkeypatch.setattr(qd, 'zxingcpp', _ZXing)
    res = qd._zxing_qr(np.zeros((10, 10), np.uint8), scale=2.0)
    assert res[0]['data'] == 'B'
    assert np.allclose(res[0]['pts'][0], (100, 60))


def test_decode_rectified_reads_perspective_qr():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    from vision.qr_detect import _decode_rectified

    text = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"B"}'
    qr = _render_qr_bgr(cv2, np, segno, text, module_px=6)
    frame, (x0, y0, w, h) = _place_on_canvas(cv2, np, qr, canvas_wh=(640, 480))
    tilted = _warp_tilt(cv2, np, frame, 15.0, x0 + w / 2, y0 + h / 2, w, h)
    res = _decode_rectified(tilted, cv2.cvtColor(tilted, cv2.COLOR_BGR2GRAY))

    assert res and res[0]['data'] == text


# ── Gate quiet-zone (port dari ros2_ws qr_logic.py) ────────────────────────────
def test_quiet_zone_ok_accepts_real_qr_border():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    from vision.qr_detect import _quiet_zone_ok
    from pyzbar import pyzbar

    text = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"A"}'
    qr = _render_qr_bgr(cv2, np, segno, text, module_px=8)
    frame, _ = _place_on_canvas(cv2, np, qr)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    objs = pyzbar.decode(frame)
    assert objs, "setup test rusak: QR asli harus terbaca pyzbar"
    pts = np.array([[p.x, p.y] for p in objs[0].polygon], dtype=np.float32)
    assert _quiet_zone_ok(gray, pts) is True


def test_quiet_zone_ok_rejects_silhouette_without_border():
    """Kontur mirip QR TANPA quiet zone putih di sekelilingnya (mis. siluet hook/
    dinding yg kebetulan membentuk quad) — harus DITOLAK meski geometrinya valid."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.qr_detect import _quiet_zone_ok

    gray = np.full((480, 640), 60, np.uint8)          # kanvas gelap seragam
    cv2.rectangle(gray, (280, 200), (360, 280), 90, thickness=-1)  # "quad" sedikit lebih terang
    pts = np.array([[280, 200], [360, 200], [360, 280], [280, 280]], dtype=np.float32)
    assert _quiet_zone_ok(gray, pts) is False


def test_decode_qr_rejects_fake_corner_without_quiet_zone(monkeypatch):
    """Integrasi: pyzbar 'berhasil' decode di quad yg dikelilingi kanvas SERAGAM
    (nol kontras quiet zone) — decode_qr harus tetap menolak di semua jenjang."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    import vision.qr_detect as qd

    monkeypatch.setattr(qd.pyzbar, 'decode', lambda img, *a, **kw: [
        _FakeSym(b'{"id":"X"}', [(280, 200), (360, 200), (360, 280), (280, 280)])])
    frame = np.full((480, 640, 3), 128, np.uint8)     # tak ada quiet zone di mana pun
    assert qd.decode_qr(frame, enhance=True) == []


# ── Jenjang-7: median-stack antar-frame (lawan riak/kaustik transien) ──────────
def test_decode_stacked_needs_full_buffer_then_decodes():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    from vision.qr_detect import VisionPipeline, STACK_N
    text = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"D"}'
    qr = _render_qr_bgr(cv2, np, segno, text, module_px=8)
    frame, _ = _place_on_canvas(cv2, np, qr)

    vp = VisionPipeline()
    # buffer belum penuh (STACK_N=3) -> jenjang-7 harus diam, bukan decode dini
    for _ in range(STACK_N - 1):
        assert vp._decode_stacked(frame) == []
    # frame ke-STACK_N melengkapi buffer -> median dari QR identik tetap terbaca
    res = vp._decode_stacked(frame)
    assert len(res) == 1 and res[0]['data'] == text


def test_decode_qr_enhance_false_is_raw_only():
    """A/B pada FRAME yang SAMA: enhance=False (hanya pyzbar mentah) GAGAL, tapi
    enhance=True (default, CLAHE/upscale) BERHASIL → membuktikan preprocessing-lah
    pembedanya, bukan kebetulan."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    from vision.qr_detect import decode_qr
    text = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"C"}'
    qr = _render_qr_bgr(cv2, np, segno, text, module_px=2)      # kecil
    frame, _ = _place_on_canvas(cv2, np, qr, contrast=0.35)      # + kontras rendah
    if decode_qr(frame, enhance=False):
        pytest.skip("pyzbar mentah kebetulan berhasil di env ini — A/B tak diskriminatif")
    assert decode_qr(frame, enhance=True), "preprocessing seharusnya memulihkan deteksi"


# ── Undistort sebelum decode (27 Agu) ──────────────────────────────────────────
# K/dist kalibrasi sebelumnya HANYA dipakai _estimate_pose_pts() SESUDAH decode
# sukses -- tak pernah membenahi frame SEBELUM decode dicoba. Dua hal diuji: (1)
# mekanisme _undistort() sendiri (no-op tanpa kalibrasi, cache per-resolusi,
# benar2 mengubah piksel), (2) titik kritis dari perubahan ini -- setelah frame
# di-undistort, _estimate_pose_pts() WAJIB dipanggil dgn dist=0 (bukan dist asli)
# supaya distorsi tak dikoreksi dua kali (kelas bug sama dgn insiden kalibrasi
# 22 Agu skala kalibrasi, lihat _verify_calib_size()).
def test_undistort_is_noop_without_calibration():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.qr_detect import VisionPipeline
    vp = VisionPipeline()
    frame = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    out = vp._undistort(frame, None, None, {})
    assert out is frame, "tanpa K/dist, _undistort harus no-op (bukan copy/proses)"


def test_undistort_changes_pixels_and_caches_map_per_resolution():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.qr_detect import VisionPipeline
    vp = VisionPipeline()
    K = np.array([[788.0, 0, 592.0], [0, 784.0, 218.0], [0, 0, 1]], dtype=np.float64)
    dist = np.array([-0.365, 0.251, 0.004, -0.007, -0.104])
    frame = np.random.default_rng(0).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    cache = {}

    out = vp._undistort(frame, K, dist, cache)
    assert not np.array_equal(out, frame), "kalibrasi ada -> piksel harus benar2 berubah"
    assert list(cache.keys()) == [(1280, 720)]

    out2 = vp._undistort(frame, K, dist, cache)
    assert np.array_equal(out, out2), "hasil harus deterministik/konsisten"
    assert len(cache) == 1, "resolusi sama -> peta remap dipakai ulang, bukan dihitung lagi"


def test_estimate_pose_after_undistort_requires_zero_dist():
    """Properti paling kritis dari perubahan ini: undistort-frame + dist=0 di
    _estimate_pose_pts() harus memulihkan pose SEBENARNYA, sedangkan memakai
    dist ASLI lagi (bug double-correction) harus menyimpang jelas -- membuktikan
    testnya memang menangkap kelas bug itu, bukan sekadar lolos kebetulan."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.qr_detect import VisionPipeline

    K = np.array([[788.0, 0, 592.0], [0, 784.0, 218.0], [0, 0, 1]], dtype=np.float64)
    dist = np.array([-0.365, 0.251, 0.004, -0.007, -0.104])   # k1 kuat, spt dwe_v3.npz nyata
    L = 0.04   # sisi QR 4x4 cm (KKI 2026)
    objp = np.array([[-L / 2, L / 2, 0], [L / 2, L / 2, 0],
                     [L / 2, -L / 2, 0], [-L / 2, -L / 2, 0]], dtype=np.float32)
    # Pose GT sengaja OFF-CENTER (distorsi paling kuat jauh dari pusat optik --
    # persis skenario yg dimotivasi perubahan ini).
    rvec_gt = np.array([0.05, -0.1, 0.02])
    tvec_gt = np.array([0.18, 0.14, 0.30])

    # Titik yg akan terlihat kamera NYATA (dgn distorsi lensa asli) di frame mentah.
    distorted_pts, _ = cv2.projectPoints(objp, rvec_gt, tvec_gt, K, dist)
    distorted_pts = distorted_pts.reshape(-1, 2).astype(np.float32)
    # Simulasi apa yg decode_qr() temukan pada FRAME yg SUDAH di-undistort (setara
    # cv2.remap+deteksi -- cv2.undistortPoints menghitung pemetaan yg sama di ruang titik).
    corrected_pts = cv2.undistortPoints(
        distorted_pts.reshape(-1, 1, 2), K, dist, P=K).reshape(-1, 2)

    vp = VisionPipeline()
    correct = vp._estimate_pose_pts(corrected_pts, L, K=K, dist=np.zeros_like(dist))
    buggy = vp._estimate_pose_pts(corrected_pts, L, K=K, dist=dist)   # double-correction

    assert correct is not None and buggy is not None
    assert abs(correct['z'] - tvec_gt[2]) < 0.01, \
        f"dist=0 pasca-undistort harus pulihkan z sebenarnya ({tvec_gt[2]}), dapat {correct['z']}"
    assert abs(buggy['z'] - tvec_gt[2]) > 0.05, \
        "dist asli dipakai lagi pasca-undistort (bug) seharusnya menyimpang jelas dari z sebenarnya"


@pytest.mark.parametrize('raw,expected', [(-167.0, 13.0), (193.0, 13.0), (12.0, 12.0)])
def test_normalize_plane_yaw_menghapus_ambiguitas_180(raw, expected):
    from vision.qr_detect import normalize_plane_yaw
    assert normalize_plane_yaw(raw) == pytest.approx(expected)


# ── Source 'image' (file gambar statis) ───────────────────────────────────────
def test_image_source_decodes_qr_and_dispatches():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    from vision.qr_detect import VisionPipeline

    text = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"D"}'
    qr = _render_qr_bgr(cv2, np, segno, text, module_px=8)
    frame, _ = _place_on_canvas(cv2, np, qr)

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        cv2.imwrite(tmp.name, frame)
        path = tmp.name

    try:
        results = []
        def on_det(r):
            results.append(r)

        cam = VisionPipeline(source='image', image_path=path, callback=on_det)
        cam.start()
        cam._thread.join(timeout=5)
        assert not cam._thread.is_alive(), "thread image harus berhenti sendiri setelah satu pass"
        assert len(results) == 1
        assert results[0]['data'] == text
        assert results[0]['wall'] == 'D'
        cx, cy = results[0]['center']
        assert abs(cx - 320) < 2 and abs(cy - 240) < 2
        latest = cam.latest_qr()
        assert latest is not None and latest['data'] == text
    finally:
        os.unlink(path)


def test_image_source_missing_file_logs_error_and_stops():
    cv2 = pytest.importorskip("cv2")
    from vision.qr_detect import VisionPipeline

    cam = VisionPipeline(source='image', image_path='/path/tdk/ada.jpg')
    cam.start()
    cam._thread.join(timeout=3)
    assert not cam._thread.is_alive(), "thread harus berhenti tanpa crash walau file hilang"


def test_image_source_empty_path_falls_back_to_mock():
    from vision.qr_detect import VisionPipeline

    cam = VisionPipeline(source='image', image_path=None)
    assert cam.source == 'mock', "image_path kosong harus fallback ke mock"
