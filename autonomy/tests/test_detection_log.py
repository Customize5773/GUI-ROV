"""
tests/test_detection_log.py — Uji DetectionCsvLogger (murni, tanpa webcam).
"""
import csv
import importlib.util
import os
import sys

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

_SPEC = importlib.util.spec_from_file_location(
    "detection_log", os.path.join(_AUTONOMY, "tools", "detection_log.py"))
detection_log = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(detection_log)
DetectionCsvLogger = detection_log.DetectionCsvLogger


def test_csv_written_with_header_and_rows(tmp_path):
    path = tmp_path / "run.csv"
    log = DetectionCsvLogger(str(path))
    log.log(detected=True, data='{"id":"A"}', z=0.40, dist=0.40, surge=10, aligned=False)
    log.log(detected=False, hint_detected=True)
    log.log(detected=True, data='{"id":"A"}', z=0.30, dist=0.30, surge=2, aligned=True)
    log.close()

    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert rows[0]["detected"] == "1" and rows[0]["dist"] == "0.4"
    assert rows[1]["detected"] == "0" and rows[1]["data"] == ""   # frame kosong bersih
    assert rows[1]["hint_detected"] == "1"
    assert rows[2]["aligned"] == "1"


def test_summary_detection_rate_and_ranges(tmp_path):
    log = DetectionCsvLogger(str(tmp_path / "run.csv"))
    log.log(detected=True, dist=0.50, area=1500)
    log.log(detected=False)
    log.log(detected=True, dist=0.30, area=3000)
    log.log(detected=False)
    s = log.summary()
    assert s["frames"] == 4 and s["detected"] == 2
    assert s["rate_pct"] == 50.0
    assert s["dist_min"] == 0.30 and s["dist_max"] == 0.50
    assert s["area_min"] == 1500 and s["area_max"] == 3000
    log.close()


def test_summary_empty_is_zero_rate(tmp_path):
    log = DetectionCsvLogger(str(tmp_path / "run.csv"))
    s = log.summary()
    assert s == {"frames": 0, "detected": 0, "rate_pct": 0.0,
                 "hint_detected": 0, "hint_rate_pct": 0.0}
    log.close()


def test_hint_detected_counted_separately_from_decode():
    """M9(c) VERIFIKASI_ARDUSUB.md: quad terlokalisasi TANPA decode harus
    terhitung terpisah dari deteksi decode-berhasil, supaya rasio jarak
    'terlihat' vs 'terbaca' bisa dihitung dari CSV."""
    log = DetectionCsvLogger(str(_tmp_csv()))
    log.log(detected=True, dist=0.30)                    # decode berhasil
    log.log(detected=False, hint_detected=True)           # quad terlihat, decode gagal
    log.log(detected=False, hint_detected=False)          # tak terlihat sama sekali
    s = log.summary()
    assert s["detected"] == 1
    assert s["hint_detected"] == 1
    assert s["hint_rate_pct"] == pytest.approx(100.0 / 3, abs=0.1)
    log.close()


def test_hint_distance_tracked_separately_from_decode_distance():
    """Jarak hint HARUS masuk kolom terpisah (hint_dist_*) — bukan _dists milik
    decode (`if detected:` di log()), atau rasio jarak M9c tak bisa dihitung."""
    log = DetectionCsvLogger(str(_tmp_csv()))
    log.log(detected=True, dist=0.30)                          # decode di 0.30 m
    log.log(detected=False, hint_detected=True, dist=1.50)      # hint di jarak lebih jauh
    log.log(detected=False, hint_detected=True, dist=1.80)
    s = log.summary()
    assert s["dist_min"] == 0.30 and s["dist_max"] == 0.30
    assert s["hint_dist_min"] == 1.50 and s["hint_dist_max"] == 1.80
    log.close()


def _tmp_csv():
    import tempfile
    return os.path.join(tempfile.mkdtemp(), "run.csv")
