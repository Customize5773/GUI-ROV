import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pytest
from vision.qr_gripper import detect_gripper_qr, parse_roi


@pytest.fixture(autouse=True)
def no_unmocked_fallback(monkeypatch):
    monkeypatch.setattr('vision.qr_detect.decode_qr', lambda *a, **kw: [])


def test_fallback_restores_coordinates_without_claiming_yolo(monkeypatch):
    class Missing:
        def detect(self, frame):
            return None
    monkeypatch.setattr('vision.qr_detect.decode_qr', lambda *a, **kw:
                        [dict(data='C', pts=np.array([[100,100],[180,100],[180,180],[100,180]]))])
    detection, decoded = detect_gripper_qr(Missing(), np.zeros((720,1280,3),np.uint8))
    assert detection['method'] == 'qr_decode'
    assert detection['confidence'] == 0.0
    np.testing.assert_allclose(decoded[0]['pts'].mean(axis=0), (460,212))
    assert detection['area'] == 6400


def test_multiple_fallback_qrs_do_not_choose_arbitrary_target(monkeypatch):
    monkeypatch.setattr('vision.qr_detect._decode_tracked_roi', lambda *a, **kw: [])
    q = dict(data='C', pts=np.array([[1,1],[5,1],[5,5],[1,5]]))
    monkeypatch.setattr('vision.qr_detect.decode_qr', lambda *a, **kw: [q,q])
    assert detect_gripper_qr(Detector(.8), np.zeros((720,1280,3),np.uint8)) == (None, [])


def test_fallback_network_validator_preserves_method_and_requires_text():
    import ast
    import math
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[2]/'rov_agent.py').read_text(encoding='utf-8'))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('_reject_vision', '_validate_qr_vision')]
    env = dict(math=math, last_vision_reject={'qr': None}, QR_DATA_MAX_LEN=1024)
    exec(compile(ast.Module(body=functions,type_ignores=[]),'validator','exec'),env)
    record = dict(method='qr_decode',data='C',confidence=0.0,frame_w=1280,frame_h=720,
                  center=[610,372],area=8000)
    result = env['_validate_qr_vision'](record)
    assert result['method'] == 'qr_decode'
    assert result['confidence'] == 0.0
    assert env['_validate_qr_vision'](dict(record,data='')) is None
    assert env['_validate_qr_vision'](dict(record,center=[float('nan'),372])) is None


class Detector:
    def __init__(self, confidence):
        self.confidence = confidence

    def detect(self, frame):
        assert frame.shape[:2] == (504, 640)
        return dict(bbox=(100, 100, 80, 80), center=(140, 140), area=6400,
                    confidence=self.confidence)


def test_live_failed_frame_never_enters_exhaustive_decode(monkeypatch):
    calls = []
    monkeypatch.setattr('vision.qr_detect.ZXING_OK', True)
    monkeypatch.setattr('vision.qr_detect._zxing_qr', lambda *a: calls.append(a[0].shape) or [])
    def forbidden(*a, **kw):
        raise AssertionError('exhaustive cascade entered live pipeline')
    monkeypatch.setattr('vision.qr_detect.decode_qr', forbidden)
    monkeypatch.setattr('vision.qr_detect._decode_tracked_roi', forbidden)
    detection, decoded = detect_gripper_qr(Detector(.8), np.zeros((720,1280,3),np.uint8), live=True)
    assert not decoded
    assert detection['confidence'] == .8
    assert len(calls) == 8  # raw ROI + six bounded candidate passes + stretched ROI
    assert max(max(s) for s in calls) <= 1280


def test_live_requires_fast_decoder(monkeypatch):
    monkeypatch.setattr('vision.qr_detect.ZXING_OK', False)
    with pytest.raises(RuntimeError, match='zxing-cpp'):
        detect_gripper_qr(Detector(.8), np.zeros((720,1280,3),np.uint8), live=True)


def test_live_candidate_restores_coordinates(monkeypatch):
    from vision.qr_gripper import _decode_live_candidate
    monkeypatch.setattr('vision.qr_detect._zxing_qr', lambda *a:
                        [dict(data='C', pts=np.array([[80.,80.],[140.,80.],[140.,140.],[80.,140.]]))])
    decoded = _decode_live_candidate(np.zeros((504,640,3),np.uint8),
                                    [[100,100],[180,100],[180,180],[100,180]])
    np.testing.assert_allclose(decoded[0]['pts'].mean(axis=0), (138,138))


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
