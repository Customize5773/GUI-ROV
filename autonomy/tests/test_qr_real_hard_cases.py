"""
tests/test_qr_real_hard_cases.py — Korpus regresi dari foto trial NYATA (bukan sintetis).

Kenapa modul ini ada: `tests/underwater_sim.py` terbukti under-model turbiditas
nyata (kalibrasi kamera KKI 2026 26 Agu — 'deep' preset decode 100% di simulasi,
padahal foto trial nyata gagal total). 4 foto berikut (fixtures/real_hard_cases/,
direkam pilot, QR terlihat tapi decode_qr() GAGAL) adalah bukti fisik yang lebih
jujur drpd simulasi manapun.

Test ini SENGAJA TIDAK assert sukses — foto-foto ini diverifikasi manual (26 Agu)
gagal di SEMUA jenjang decode_qr() termasuk zxing-cpp; crop area QR yang gagal
punya local contrast std≈7/255, sudah dicoba CLAHE clip sampai 40, percentile-
stretch, & Otsu threshold, tak ada yang berhasil. Ini plafon fisik (cahaya/jarak/
material stiker), bukan celah software. Tujuan test: (1) pastikan decode_qr()
TAK CRASH pada foto nyata degradasi ekstrem, (2) jadi penanda progres jujur —
kalau suatu saat sebuah file mulai berhasil dibaca berkat perbaikan software,
itu SINYAL NYATA yang harus dicatat di komentar ini, bukan diam-diam dihapus
dari korpus atau dianggap kebetulan.

Status per 26 Agu 2026 (baseline): 4/4 gagal decode (raw & enhance).
"""
import glob
import os

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "real_hard_cases")


def _fixture_files():
    return sorted(glob.glob(os.path.join(_FIXTURES, "*.png")))


def test_real_hard_cases_fixtures_present():
    files = _fixture_files()
    assert len(files) == 4, f"korpus harus 4 foto unik, ketemu {len(files)} -- cek dedup"


def test_decode_qr_does_not_crash_on_real_hard_cases():
    cv2 = pytest.importorskip("cv2")
    from vision.qr_detect import decode_qr

    files = _fixture_files()
    assert files, f"tak ada fixture di {_FIXTURES}"

    results = {}
    for f in files:
        img = cv2.imread(f)
        assert img is not None, f"gagal baca {f}"
        res = decode_qr(img, enhance=True)   # tak boleh melempar exception
        results[os.path.basename(f)] = bool(res)

    n_ok = sum(results.values())
    print(f"\n[real_hard_cases] {n_ok}/{len(results)} berhasil decode: {results}")
    # BUKAN assert n_ok == 0 -- kalau software membaik & salah satu mulai
    # terbaca, itu progres bagus yg harus dirayakan, bukan bikin test merah.
    # Baseline (26 Agu): 0/4. Update komentar modul di atas kalau angka ini naik.
