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
                 and n.name in ('_reject_vision', '_qr_overlay_fields', '_validate_qr_vision', '_validate_qr_region')]
    env = dict(math=math, last_vision_reject={'qr': None}, QR_DATA_MAX_LEN=1024)
    exec(compile(ast.Module(body=functions,type_ignores=[]),'validator','exec'),env)
    record = dict(method='qr_decode',data='C',confidence=0.0,frame_w=1280,frame_h=720,
                  center=[610,372],area=8000, bbox=[560,320,100,100], active_cam='WALL')
    result = env['_validate_qr_vision'](record)
    assert result['bbox'] == [560,320,100,100]
    assert result['active_cam'] == 'WALL'
    region = env['_validate_qr_region'](dict(record,method='yolo_qr_region'))
    assert region['bbox'] == result['bbox'] and region['active_cam'] == 'WALL'
    assert 'data' not in region
    assert 'bbox' not in env['_validate_qr_vision'](dict(record,bbox=[0,0,float('nan'),5]))
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
    assert len(calls) == 9  # raw ROI + six candidate passes + stretch + upscale
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


def test_live_small_qr_fallback_passes_scale_and_restores_roi(monkeypatch):
    class Missing:
        def detect(self, frame):
            return None
    monkeypatch.setattr('vision.qr_detect.ZXING_OK', True)
    def decode(image, scale=1):
        if scale == 1:
            return []
        assert scale == 2 and image.shape == (1008,1280)
        return [dict(data='C', pts=np.array([[100,100],[180,100],[180,180],[100,180]]))]
    monkeypatch.setattr('vision.qr_detect._zxing_qr', decode)
    detection, decoded = detect_gripper_qr(Missing(), np.zeros((720,1280,3),np.uint8), live=True)
    assert detection['method'] == 'qr_decode'
    np.testing.assert_allclose(decoded[0]['pts'].mean(axis=0), (460,212))


def test_wechat_recovery_restores_scaled_crop_points(monkeypatch):
    from vision.qr_gripper import _decode_wechat_candidate
    calls = []
    class Decoder:
        def detectAndDecode(self, image):
            calls.append(image.shape)
            if len(calls) < 3:
                return (), ()
            return ('D',), (np.float32([[60,60],[180,60],[180,180],[60,180]]),)
    monkeypatch.setattr('vision.qr_gripper._wechat_decoder', lambda: Decoder())
    found = _decode_wechat_candidate(np.zeros((504,640,3),np.uint8),
                                    [[100,100],[180,100],[180,180],[100,180]])
    assert calls == [(120,120),(240,240),(360,360)]
    assert found[0]['data'] == 'D'
    np.testing.assert_allclose(found[0]['pts'].mean(axis=0), [120,120])


def test_wechat_failed_workload_is_bounded_and_bad_geometry_rejected(monkeypatch):
    from vision.qr_gripper import _decode_wechat_candidate
    shapes = []
    class Decoder:
        def detectAndDecode(self, image):
            shapes.append(image.shape)
            return ('C',), (np.full((4,2), np.nan),)
    monkeypatch.setattr('vision.qr_gripper._wechat_decoder', lambda: Decoder())
    assert _decode_wechat_candidate(np.zeros((720,1280,3),np.uint8),
                                   [[200,100],[1000,100],[1000,600],[200,600]]) == []
    assert len(shapes) == 4 and max(max(s) for s in shapes) <= 640
    monkeypatch.setattr('vision.qr_gripper._wechat_decoder', lambda: None)
    assert _decode_wechat_candidate(np.zeros((504,640,3),np.uint8),
                                   [[100,100],[180,100],[180,180],[100,180]]) == []


def test_recovery_cannot_choose_between_two_payloads(monkeypatch):
    monkeypatch.setattr('vision.qr_detect._zxing_qr', lambda *a: [])
    points = np.float32([[110,110],[170,110],[170,170],[110,170]])
    monkeypatch.setattr('vision.qr_gripper._decode_wechat_candidate', lambda *a:
                        [dict(data='C',pts=points),dict(data='D',pts=points)])
    assert detect_gripper_qr(Detector(.8), np.zeros((720,1280,3),np.uint8), live=True) == (None, [])


def test_real_wall_haze_recovered_without_payload_cache():
    import cv2
    from pathlib import Path
    from vision.qr_gripper import _decode_wechat_candidate
    if not hasattr(cv2, 'wechat_qrcode_WeChatQRCode'):
        pytest.skip('opencv-contrib-python required for independent QR recovery')
    frame = cv2.imread(str(Path(__file__).parent / 'fixtures/qr_wall_haze.png'))
    assert frame is not None
    # Coordinates from YOLO on this real video frame, not a known payload template.
    quad = [[305,187],[372,187],[372,256],[305,256]]
    recovered = _decode_wechat_candidate(frame, quad)
    assert recovered and recovered[0]['data'] == 'C'
    assert _decode_wechat_candidate(np.zeros_like(frame), quad) == []


def test_live_candidate_prefers_small_crop_and_retains_zxing_fallback(monkeypatch):
    from vision.qr_gripper import _detect_yolo_gripper_qr
    calls = []
    points = np.float32([[110,110],[170,110],[170,170],[110,170]])
    def wechat(*args):
        calls.append('wechat')
        return [dict(data='D',pts=points)]
    def zxing(*args):
        calls.append('zxing')
        return [dict(data='D',pts=points)]
    monkeypatch.setattr('vision.qr_gripper._decode_wechat_candidate', wechat)
    monkeypatch.setattr('vision.qr_gripper._decode_live_candidate', zxing)
    frame = np.zeros((720,1280,3),np.uint8)
    _, decoded = _detect_yolo_gripper_qr(Detector(.8), frame, live=True)
    assert decoded[0]['data'] == 'D' and calls == ['wechat']
    calls.clear()
    monkeypatch.setattr('vision.qr_gripper._decode_wechat_candidate', lambda *a: [])
    _, decoded = _detect_yolo_gripper_qr(Detector(.8), frame, live=True)
    assert decoded[0]['data'] == 'D' and calls == ['zxing']


def test_small_candidate_prioritizes_enlarged_green(monkeypatch):
    from vision.qr_gripper import _decode_wechat_candidate
    class Decoder:
        def detectAndDecode(self, image):
            assert image.shape == (180,180)
            assert np.all(image == 177)
            return ('D',), (np.float32([[30,30],[90,30],[90,90],[30,90]]),)
    monkeypatch.setattr('vision.qr_gripper._wechat_decoder', lambda: Decoder())
    frame = np.zeros((504,640,3),np.uint8)
    frame[:,:,1] = 177
    found = _decode_wechat_candidate(frame, [[100,100],[140,100],[140,140],[100,140]])
    assert found[0]['data'] == 'D'
    np.testing.assert_allclose(found[0]['pts'].mean(axis=0), [110,110])
