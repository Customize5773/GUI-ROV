import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pytest
from vision.qr_gripper import detect_gripper_qr, parse_roi


class Detector:
    def __init__(self, confidence):
        self.confidence = confidence

    def detect(self, frame):
        assert frame.shape[:2] == (504, 640)
        return dict(bbox=(100, 100, 80, 80), center=(140, 140), area=6400,
                    confidence=self.confidence)


def test_decode_required_for_low_confidence_and_coordinates_restored(monkeypatch):
    points = np.array([[110,110],[170,110],[170,170],[110,170]], dtype=np.float32)
    monkeypatch.setattr('vision.qr_detect._decode_tracked_roi',
                        lambda *a, **kw: [dict(data='C', pts=points)])
    detection, decoded = detect_gripper_qr(Detector(.2), np.zeros((720,1280,3),np.uint8))
    assert detection['bbox'] == (420,172,80,80)
    assert detection['center'] == (460,212)
    assert detection['frame_w'] == 1280
    np.testing.assert_allclose(decoded[0]['pts'].mean(axis=0), (460,212))
    np.testing.assert_allclose(points.mean(axis=0), (140,140))


@pytest.mark.parametrize('confidence,accepted', [(.2,False),(.6,True),(.9,True)])
def test_undecoded_region_keeps_high_confidence_gate(monkeypatch, confidence, accepted):
    monkeypatch.setattr('vision.qr_detect._decode_tracked_roi', lambda *a, **kw: [])
    detection, decoded = detect_gripper_qr(Detector(confidence), np.zeros((720,1280,3),np.uint8))
    assert (detection is not None) == accepted
    assert decoded == []


def test_neighbor_qr_cannot_validate_low_confidence_box(monkeypatch):
    monkeypatch.setattr('vision.qr_detect._decode_tracked_roi', lambda *a, **kw:
                        [dict(data='C', pts=np.array([[1,1],[5,1],[5,5],[1,5]]))])
    assert detect_gripper_qr(Detector(.2), np.zeros((720,1280,3),np.uint8)) == (None, [])


@pytest.mark.parametrize('value', ['nan,0,1,1','0,0,2,1','1,0,0,1','0,0,1'])
def test_invalid_roi_rejected(value):
    with pytest.raises(ValueError):
        parse_roi(value)
