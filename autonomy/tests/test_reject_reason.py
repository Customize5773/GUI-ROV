"""tests/test_reject_reason.py — setiap penolakan vision HARUS menyebut alasannya.

Kelas bug yang diuji di sini: ROV "benar mendeteksi" (bbox tergambar di GUI)
tapi tidak bergerak. Overlay bbox datang dari jalur telemetri yang TIDAK sama
dengan jalur yang menyetir FSM, jadi setiap gate yang menolak dengan
`return None` diam-diam menghasilkan gejala yang identik untuk delapan sebab
berbeda: ROV diam. Instrumentasi ini yang membedakannya, jadi kalau ia rusak
diagnosis kembali jadi tebak-tebakan — karena itu diuji, bukan cuma ditulis.

Yang diuji:
  1. validator batas jaringan rov_agent.py  -> last_vision_reject[kanal]
  2. gate FSM mission5.py                   -> telemetry_out['reject_reason']
  3. gate kesegaran _fsm_read_state()       -> telem['vision_reject']
  4. ringkasan tools/analyze_run.py         -> alasan dominan per trial
"""
import io
import json
import os
import sys

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_AUTONOMY)
for _path in (_AUTONOMY, _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import rov_agent  # noqa: E402
from fsm import mission5  # noqa: E402


HOOK_RECORD = {
    'status': 'ok', 'method': 'yolov8', 'confidence': 0.62,
    'bbox': [520.0, 300.0, 180.0, 140.0], 'frame_w': 1280, 'frame_h': 720,
    'keypoints': [{'id': i, 'x': 560.0 + 20 * i, 'y': 320.0 + 20 * i,
                   'confidence': 0.71} for i in range(6)],
}

QR_RECORD = {
    'status': 'ok', 'method': 'yolo_qr', 'data': 'MISSION5|TYPE=A',
    'payload': {'mission': 5, 'type': 'payload'}, 'wall': 'B',
    'center': [640.0, 360.0], 'area': 900.0, 'confidence': 0.71,
    'frame_w': 1280, 'frame_h': 720,
    'pose': {'x': 0.01, 'y': -0.02, 'z': 0.30, 'dist': 0.30, 'yaw_deg': 3.5},
}


def _hook(**overrides):
    record = dict(HOOK_RECORD)
    record['bbox'] = list(HOOK_RECORD['bbox'])
    record['keypoints'] = [dict(k) for k in HOOK_RECORD['keypoints']]
    record.update(overrides)
    return record


def _qr(**overrides):
    record = dict(QR_RECORD)
    record['center'] = list(QR_RECORD['center'])
    record['payload'] = dict(QR_RECORD['payload'])
    record['pose'] = dict(QR_RECORD['pose'])
    record.update(overrides)
    return record


@pytest.fixture(autouse=True)
def _clear_reject():
    rov_agent.last_vision_reject.update(hook=None, qr=None)
    yield


# ── 1. Validator batas jaringan (rov_agent.py) ────────────────────────────────

def test_valid_hook_record_leaves_no_reason():
    """Sanity: kalau record sehat pun meninggalkan alasan, semua uji lain palsu."""
    assert rov_agent._validate_hook_vision(_hook()) is not None
    assert rov_agent.last_vision_reject['hook'] is None


def test_valid_hook_record_clears_previous_reason():
    """Alasan basi yang menempel = diagnosis salah pada trial berikutnya."""
    rov_agent.last_vision_reject['hook'] = 'bbox_malformed'
    assert rov_agent._validate_hook_vision(_hook()) is not None
    assert rov_agent.last_vision_reject['hook'] is None


@pytest.mark.parametrize("record,keluarga", [
    ({'method': 'opencv'}, 'bad_method'),
    (_hook(confidence=1.4), 'conf_out_of_range'),
    (_hook(bbox=[10.0, 10.0, 0.0, 50.0]), 'bbox_malformed'),
    (_hook(bbox=[1200.0, 300.0, 400.0, 140.0]), 'bbox_outside_frame'),
    (_hook(keypoints=HOOK_RECORD['keypoints'][:5]), 'keypoint_count_not_6'),
])
def test_hook_rejection_names_its_reason(record, keluarga):
    assert rov_agent._validate_hook_vision(record) is None
    assert rov_agent.last_vision_reject['hook'].split(':')[0] == keluarga


def test_hook_duplicate_keypoint_id_is_distinguishable():
    """Duplikat id != kurang keypoint: yang satu bug model, yang satu bug decoder."""
    record = _hook()
    record['keypoints'][3]['id'] = 2
    assert rov_agent._validate_hook_vision(record) is None
    assert rov_agent.last_vision_reject['hook'].startswith('keypoint_id_duplicate')


def test_hook_absurd_keypoint_reports_position():
    """Alasannya harus membawa ANGKA — 'di luar frame' saja tak bisa ditindak."""
    record = _hook()
    record['keypoints'][0]['y'] = -99999.0
    assert rov_agent._validate_hook_vision(record) is None
    reason = rov_agent.last_vision_reject['hook']
    assert reason.startswith('keypoint_out_of_frame:0@') and '-99999' in reason


def test_hook_keypoint_conf_invalid_is_distinguishable():
    record = _hook()
    record['keypoints'][4]['confidence'] = 1.5
    assert rov_agent._validate_hook_vision(record) is None
    assert rov_agent.last_vision_reject['hook'].startswith('keypoint_conf_invalid')


@pytest.mark.parametrize("record,keluarga", [
    ({'method': 'jsqr'}, 'bad_method'),
    (_qr(data=''), 'data_empty_or_too_long'),
    (_qr(data='x' * (rov_agent.QR_DATA_MAX_LEN + 1)), 'data_empty_or_too_long'),
    (_qr(confidence=-0.1), 'conf_out_of_range'),
    (_qr(area=0.0), 'area_invalid'),
    (_qr(center=[5000.0, 360.0]), 'center_outside_frame'),
    (_qr(wall='Z'), 'wall_invalid'),
    (_qr(pose={'x': 0.0, 'y': 0.0, 'z': float('nan'), 'dist': 1.0, 'yaw_deg': 0.0}),
     'pose_non_finite'),
])
def test_qr_rejection_names_its_reason(record, keluarga):
    assert rov_agent._validate_qr_vision(record) is None
    assert rov_agent.last_vision_reject['qr'].split(':')[0] == keluarga


def test_hook_and_qr_reasons_do_not_overwrite_each_other():
    """Satu slot bersama = kanal yang sehat menghapus alasan kanal yang sakit."""
    rov_agent._validate_hook_vision({'method': 'opencv'})
    rov_agent._validate_qr_vision(_qr())
    assert rov_agent.last_vision_reject['hook'] == 'bad_method'
    assert rov_agent.last_vision_reject['qr'] is None


# ── 2. Gate kesegaran (_fsm_read_state) ───────────────────────────────────────

def test_stale_hook_reports_actual_age(monkeypatch):
    """Umur SEBENARNYA, bukan flag: 1,05 s (tether pelan) dan 8 s (worker mati)
    menuntut perbaikan yang sama sekali berbeda."""
    monkeypatch.setattr(rov_agent, 'latest_hook_vision', dict(HOOK_RECORD))
    monkeypatch.setattr(rov_agent, 'latest_hook_vision_received',
                        rov_agent.time.monotonic() - 1.34)
    monkeypatch.setattr(rov_agent, 'latest_qr_vision', None)
    data = rov_agent._fsm_read_state()
    assert data['hook_vision'] is None
    assert data['vision_reject'].startswith('stale_hook_1.3')


def test_fresh_data_reports_no_reason(monkeypatch):
    monkeypatch.setattr(rov_agent, 'latest_hook_vision', dict(HOOK_RECORD))
    monkeypatch.setattr(rov_agent, 'latest_hook_vision_received',
                        rov_agent.time.monotonic())
    monkeypatch.setattr(rov_agent, 'latest_qr_vision', None)
    data = rov_agent._fsm_read_state()
    assert data['hook_vision'] is not None and data['vision_reject'] is None


def test_stale_and_validator_reasons_both_survive(monkeypatch):
    """Record ditolak validator membuat cache lama ikut menua. Kalau salah satu
    alasan menimpa yang lain, yang terlihat cuma gejala hilirnya."""
    monkeypatch.setattr(rov_agent, 'latest_qr_vision', dict(QR_RECORD))
    monkeypatch.setattr(rov_agent, 'latest_qr_vision_received',
                        rov_agent.time.monotonic() - 3.0)
    monkeypatch.setattr(rov_agent, 'latest_hook_vision', None)
    rov_agent.last_vision_reject['qr'] = 'center_outside_frame'
    reasons = rov_agent._fsm_read_state()['vision_reject']
    assert 'stale_qr_3.0' in reasons and 'qr:center_outside_frame' in reasons


# ── 3. Gate FSM (mission5.py) ─────────────────────────────────────────────────

class _FSMStub:
    """Cukup untuk menguji gate — Mission5FSM lengkap butuh kamera & MAVLink."""
    _reject = mission5.Mission5FSM._reject
    _fresh_external_yolo = mission5.Mission5FSM._fresh_external_yolo
    _hook_skeleton = mission5.Mission5FSM._hook_skeleton
    _hook_tip = mission5.Mission5FSM._hook_tip
    _is_target_payload = mission5.Mission5FSM._is_target_payload

    def __init__(self):
        self.telemetry_out = {'reject_reason': None, 'lock_progress': None}
        self._yolo_source = lambda: None


@pytest.fixture
def fsm():
    return _FSMStub()


def test_conf_below_gate_reports_both_numbers(fsm):
    """'conf_below_gate:0.28<0.35' membedakan ambang tak sinkron (worker emit
    jauh di bawah gate FSM) dari model yang memang tak yakin."""
    assert fsm._fresh_external_yolo(_hook(confidence=0.28)) is None
    reason = fsm.telemetry_out['reject_reason']
    assert reason.startswith('conf_below_gate:0.28<')
    assert reason.endswith('%.2f' % mission5.LEFT_YOLO_CONF)


def test_yolo_absent_differs_from_low_confidence(fsm):
    assert fsm._fresh_external_yolo(None) is None
    assert fsm.telemetry_out['reject_reason'] == 'yolo_absent'


def test_hook_skeleton_reports_which_keypoint_failed(fsm):
    record = _hook()
    record['keypoints'][4]['confidence'] = 0.05
    assert fsm._hook_tip(record) is None
    assert fsm.telemetry_out['reject_reason'].startswith('keypoint_conf_low:4:')


def test_hook_skeleton_reports_edge_margin_separately(fsm):
    """Margin tepi != confidence rendah: yang satu framing, yang satu model."""
    record = _hook()
    record['keypoints'][5].update(x=1.0, y=1.0)
    assert fsm._hook_tip(record) is None
    assert fsm.telemetry_out['reject_reason'].startswith('keypoint_edge_margin:5@')


def test_hook_skeleton_names_the_collapsed_span(fsm):
    """Span mana yang kolaps menunjuk bagian model mana yang rusak."""
    record = _hook()
    record['keypoints'][4].update(x=record['keypoints'][5]['x'],
                                  y=record['keypoints'][5]['y'])
    assert fsm._hook_tip(record) is None
    assert fsm.telemetry_out['reject_reason'].startswith('skeleton_collapsed:4-5:')


def test_healthy_skeleton_passes(fsm):
    """Kalau fixture sehat pun ditolak, semua uji di atas kehilangan makna."""
    assert fsm._hook_tip(_hook()) is not None


def test_payload_mismatch_reports_received_and_expected(fsm):
    det = {'payload': {'mission': 3, 'type': mission5.PAYLOAD_TYPE}}
    assert fsm._is_target_payload(det) is False
    reason = fsm.telemetry_out['reject_reason']
    assert reason.startswith('payload_mismatch:mission=')
    assert '3' in reason and str(mission5.PAYLOAD_MISSION) in reason


def test_lock_progress_field_exists_in_telemetry_contract():
    """rov_link.py menyalin telemetry_out bulat-bulat; field yang tak ada di
    dict awal tak akan pernah sampai ke GUI di tick-tick pertama."""
    src = io.open(os.path.join(_AUTONOMY, 'fsm', 'mission5.py'), encoding='utf-8').read()
    head = src[src.index('self.telemetry_out = {'):]
    head = head[:head.index('\n        }')]
    assert "'reject_reason': None" in head and "'lock_progress': None" in head


# ── 4. Ringkasan run log (tools/analyze_run.py) ───────────────────────────────

def test_analyze_run_reports_dominant_reason():
    """Yang dicari operator setelah trial adalah SATU alasan dominan, dan
    dominan diukur dari LAMA BERTAHAN, bukan berapa kali muncul.

    File ditulis ke autonomy/logs/ (sudah di .gitignore) dan bukan tmp_path:
    direktori temp Windows di mesin ini menolak scandir, jadi tmp_path bikin
    uji ini gagal karena lingkungan, bukan karena logikanya salah."""
    from tools.analyze_run import summarize

    folder = os.path.join(_AUTONOMY, 'logs')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'run_test_reject_reason.jsonl')
    events = [
        {'kind': 'config', 't': 0.0, 'start_state': 'M5_YOLO_SEARCH'},
        # berkedip: sering muncul, tapi total cuma ~0,2 s
        {'kind': 'reject', 't': 1.0, 'reason': 'yolo_absent', 'lock_progress': '0/5'},
        {'kind': 'reject', 't': 1.1, 'reason': 'conf_below_gate:0.28<0.35',
         'lock_progress': '2/5'},
        {'kind': 'reject', 't': 1.2, 'reason': 'yolo_absent', 'lock_progress': '1/5'},
        # bertahan 60 s — inilah yang harus dilaporkan
        {'kind': 'reject', 't': 1.3, 'reason': 'conf_below_gate:0.31<0.35',
         'lock_progress': '3/5'},
        {'kind': 'end', 't': 61.3, 'state_akhir': 'ABORT', 'alasan': 'timeout',
         'durasi_s': 61.3, 'skor': {'total': 0}},
    ]
    with io.open(path, 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e) + '\n')
    try:
        s = summarize(path)
    finally:
        os.remove(path)
    assert s['reject_dominan'] == 'conf_below_gate'
    assert s['reject_lama_s']['conf_below_gate'] > s['reject_lama_s']['yolo_absent']
    assert s['reject_contoh'].startswith('conf_below_gate:0.')
    assert s['lock_maks'] == '3/5', "latch tertinggi = bukti deteksi berkedip"
