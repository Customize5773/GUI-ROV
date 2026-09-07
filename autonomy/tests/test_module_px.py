"""tests/test_module_px.py — estimate_module_px() harus memulihkan module_px yang
SUDAH DIKETAHUI, sebelum dipercaya untuk mengukur foto asli yang tak diketahui.

Kenapa ada: keputusan A5 ("QR di luar jarak kerja" vs "model region tak mampu")
butuh angka module_px pada frame yang decode_qr()-nya justru GAGAL — situasi di
mana tak ada satu pun fungsi lain di repo ini yang menghasilkan angka. Sebelum
angka itu dipercaya untuk diagnosis, metode pengukurannya sendiri harus
terbukti benar pada kasus yang jawabannya sudah diketahui: QR sintetis (segno)
yang di-render pada module_px tertentu.
"""
import os
import sys

import pytest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_AUTONOMY = os.path.dirname(_TESTS)
for _path in (_AUTONOMY, os.path.dirname(_AUTONOMY)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
pytest.importorskip("segno")

from vision.qr_detect import estimate_module_px  # noqa: E402


def _render_synthetic_qr(module_px, border=4):
    """QR sintetis sebagai grayscale ndarray — pola sama tests/underwater_sim.py
    render_qr_bgr(): segno tak punya to_pil di versi ini, jadi lewat PNG buffer."""
    import io

    import segno
    buf = io.BytesIO()
    segno.make("MISSION5|TYPE=A", error="m").save(buf, kind="png",
                                                   scale=module_px, border=border)
    buf.seek(0)
    array = np.frombuffer(buf.read(), np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)


@pytest.mark.parametrize("known_module_px", [4, 6, 8, 12, 16])
def test_recovers_known_module_px_on_synthetic_qr(known_module_px):
    gray = _render_synthetic_qr(known_module_px)
    measured = estimate_module_px(gray)
    assert measured, f"module_px={known_module_px}: tak ada finder pattern tersegmentasi"
    best = min(measured, key=lambda m: abs(m - known_module_px))
    error_pct = 100.0 * abs(best - known_module_px) / known_module_px
    assert error_pct < 10.0, (
        f"module_px={known_module_px}: pengukuran {best:.2f} meleset {error_pct:.1f}%")


def test_blank_image_reports_nothing_rather_than_a_wrong_number():
    """Tanpa QR sama sekali harus kosong, bukan angka acak dari derau kertas."""
    blank = np.full((300, 300), 255, np.uint8)
    assert estimate_module_px(blank) == []


def test_finds_three_finder_patterns_on_clean_synthetic_qr():
    """QR bersih punya tepat 3 finder pattern (kiri-atas, kanan-atas, kiri-bawah)
    — kalau metode ini menghitung jauh lebih banyak/sedikit, ia menangkap blob
    yang salah, bukan finder pattern sungguhan."""
    gray = _render_synthetic_qr(10)
    measured = estimate_module_px(gray)
    close_to_10 = [m for m in measured if abs(m - 10) / 10 < 0.15]
    assert len(close_to_10) == 3, (
        f"diharap 3 finder pattern @~10px, ketemu {len(close_to_10)}: {measured}")
