"""
tests/test_preflight_check.py — Uji fungsi murni tools/preflight_check.py.

Tanpa kamera nyata: `_detection_rate` diuji dgn fake VideoCapture (mock
`.read()`), `check_mark`/`check_depth` diuji dgn dict telemetry biasa. Tool
ini pada dasarnya I/O manual (kamera+jaringan) — tak ada test end-to-end.
"""
import os
import sys
import time

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

cv2 = pytest.importorskip("cv2")

from tools.preflight_check import (
    _detection_rate, check_mark, check_depth, PASS, WARN, FAIL,
)


class _FakeCap:
    """Fake cv2.VideoCapture: .read() menyusuri daftar frame yg disiapkan test."""

    def __init__(self, frames):
        self._frames = list(frames)

    def read(self):
        if not self._frames:
            return False, None
        return True, self._frames.pop(0)


def test_detection_rate_all_hit():
    cap = _FakeCap([object()] * 5)
    rate, hits = _detection_rate(cap, lambda f: {"data": "x"}, n_frames=5, fps=1000)
    assert rate == pytest.approx(1.0)
    assert len(hits) == 5


def test_detection_rate_none_hit():
    cap = _FakeCap([object()] * 5)
    rate, hits = _detection_rate(cap, lambda f: None, n_frames=5, fps=1000)
    assert rate == 0.0
    assert hits == []


def test_detection_rate_partial():
    cap = _FakeCap([object()] * 4)
    calls = {"n": 0}

    def half(_frame):
        calls["n"] += 1
        return {"ok": True} if calls["n"] % 2 == 0 else None

    rate, hits = _detection_rate(cap, half, n_frames=4, fps=1000)
    assert rate == pytest.approx(0.5)
    assert len(hits) == 2


def test_detection_rate_no_frames_read_is_zero_not_crash():
    cap = _FakeCap([])   # kamera gagal baca sama sekali
    rate, hits = _detection_rate(cap, lambda f: {"x": 1}, n_frames=3, fps=1000)
    assert rate == 0.0
    assert hits == []


def test_check_mark_pass_when_marked():
    results = []
    check_mark({"marked_heading": 90.0, "marked_depth": 0.45}, results)
    assert results[-1][1] == PASS


def test_check_mark_warn_when_unmarked():
    results = []
    check_mark({"marked_heading": None, "marked_depth": None}, results)
    assert results[-1][1] == WARN


def test_check_mark_warn_when_no_telemetry():
    results = []
    check_mark(None, results)
    assert results[-1][1] == WARN


def test_check_depth_pass_in_range():
    results = []
    check_depth({"depth": 0.5}, results, pool_depth=0.9)
    assert results[-1][1] == PASS


def test_check_depth_fail_negative():
    results = []
    check_depth({"depth": -0.1}, results, pool_depth=0.9)
    assert results[-1][1] == FAIL


def test_check_depth_fail_exceeds_pool():
    results = []
    check_depth({"depth": 1.5}, results, pool_depth=0.9)
    assert results[-1][1] == FAIL


def test_check_depth_fail_missing_field():
    results = []
    check_depth({}, results, pool_depth=0.9)
    assert results[-1][1] == FAIL


def test_check_depth_warn_when_no_telemetry():
    results = []
    check_depth(None, results, pool_depth=0.9)
    assert results[-1][1] == WARN
