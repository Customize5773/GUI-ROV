"""
tests/test_detection_log.py — Uji DetectionCsvLogger (murni, tanpa webcam).
"""
import csv
import importlib.util
import os
import sys

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
    log.log(detected=False)
    log.log(detected=True, data='{"id":"A"}', z=0.30, dist=0.30, surge=2, aligned=True)
    log.close()

    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert rows[0]["detected"] == "1" and rows[0]["dist"] == "0.4"
    assert rows[1]["detected"] == "0" and rows[1]["data"] == ""   # frame kosong bersih
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
    assert s == {"frames": 0, "detected": 0, "rate_pct": 0.0}
    log.close()
