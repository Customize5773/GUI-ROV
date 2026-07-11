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


def _render_qr_bgr(cv2, np, segno, text, module_px, border=4):
    """Render QR -> gambar BGR, ukuran modul = module_px piksel/modul."""
    import io
    png = io.BytesIO()
    segno.make(text, error='m').save(png, kind='png', scale=module_px, border=border)
    png.seek(0)
    gray = cv2.imdecode(np.frombuffer(png.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _place_on_canvas(cv2, np, qr_bgr, canvas_wh=(640, 480), contrast=1.0, offset=(0, 0)):
    """Tempel QR di kanvas abu-abu; contrast<1 menurunkan kontras (mensimulasi cahaya buruk)."""
    W, H = canvas_wh
    frame = np.full((H, W, 3), 128, np.uint8)
    h, w = qr_bgr.shape[:2]
    if contrast < 1.0:                       # tekan ke arah abu-abu 128 → kontras rendah
        qr_bgr = (128 + (qr_bgr.astype(np.float32) - 128) * contrast).astype(np.uint8)
    y0 = (H - h) // 2 + offset[1]
    x0 = (W - w) // 2 + offset[0]
    frame[y0:y0 + h, x0:x0 + w] = qr_bgr
    return frame, (x0, y0, w, h)


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
        # jenjang 1 (raw) & 2 (CLAHE) gagal; jenjang 3 (upscale UPSCALE×) berhasil,
        # mengembalikan polygon di koordinat gambar yg SUDAH diperbesar (200 px).
        if calls['n'] < 3:
            return []
        return [_FakeSym(b'{"id":"A"}',
                         [(200, 200), (240, 200), (240, 240), (200, 240)])]

    monkeypatch.setattr(qd.pyzbar, 'decode', fake_decode)
    frame = np.full((480, 640, 3), 128, np.uint8)
    res = qd.decode_qr(frame, enhance=True)

    assert calls['n'] == 3, "harus mengeskalasi raw -> CLAHE -> upscale"
    assert len(res) == 1 and res[0]['data'] == '{"id":"A"}'
    # koordinat harus dibagi UPSCALE agar kembali ke frame ASLI (200/2 = 100)
    pts = res[0]['pts']
    assert abs(pts[:, 0].min() - 100) < 1e-3 and abs(pts[:, 1].min() - 100) < 1e-3


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
    assert calls['n'] == 3                     # raw + CLAHE + upscale


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
