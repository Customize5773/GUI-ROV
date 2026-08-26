"""
tests/test_qr_detect.py — Uji decode_qr() robust (preprocessing berjenjang).

Membuktikan perbaikan isu Fase 0 ("QR terdeteksi hanya jarak dekat / sensitif cahaya"):
QR yang diperkecil + kontras-rendah GAGAL di pyzbar.decode() mentah, tapi BERHASIL lewat
decode_qr() (grayscale+CLAHE / upscale). Butuh cv2 + pyzbar + segno; di-skip bila absen.
"""
import os
import sys

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


def test_decode_qr_no_qr_returns_empty():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.qr_detect import decode_qr
    blank = np.full((480, 640, 3), 200, np.uint8)
    assert decode_qr(blank) == []


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


# ── Jenjang-5: median-stack antar-frame (lawan riak/kaustik transien) ──────────
def test_decode_stacked_needs_full_buffer_then_decodes():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    from vision.qr_detect import VisionPipeline, STACK_N
    text = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"D"}'
    qr = _render_qr_bgr(cv2, np, segno, text, module_px=8)
    frame, _ = _place_on_canvas(cv2, np, qr)

    vp = VisionPipeline()
    # buffer belum penuh (STACK_N=3) -> jenjang-5 harus diam, bukan decode dini
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
