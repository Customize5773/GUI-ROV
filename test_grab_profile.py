"""Production calibration through actual CASE 4/5, with captured commands only."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from test_control_servo_hook import _load_control_main


def run_case(records):
    cm = _load_control_main()
    packets = []
    cm.send_packet = packets.append
    clock = [100.0]
    try:
        with patch.object(cm.time, 'monotonic', side_effect=lambda: clock[0]):
            cm.load_servo_config()
            assert cm.servo_cfg is not None
            cm.autonomous_reset()
            cm.enter_case(4)
            cm.current_mode = cm.MODE_AUTONOMOUS
            for i, record in enumerate(records):
                cm.accept_vision_message(dict(type='qr_vision',value=record,
                                             age=0,received=100+i*.25))
                for _ in range(5):
                    cm.accept_vision_message(dict(type='vehicle_state',value=dict(depth=1.0,armed=True)))
                    cm.autonomous_control()
                    clock[0] += .05
            return packets
    finally:
        cm.out_sock.close()


def record(x=.477, y=.51, area=.009, data='C'):
    return dict(center=[x*1280,y*720],frame_w=1280,frame_h=720,
                area=area*1280*720,data=data,method='qr_decode')


@pytest.mark.parametrize('r,expected', [
    (record(),1), (record(area=.0056),0), (record(area=.013),0),
    (record(y=.55),0), (record(x=.42),0), (record(data='A'),0)])
def test_production_profile_gate(r, expected):
    packets = run_case([r]*15)
    closes = [p for p in packets if p.get('name')=='gripper' and p.get('value')=='close']
    assert len(closes) == expected
    if expected:
        motion = [p for p in packets if p.get('type')=='control']
        assert all(p['surge']==0 for p in motion)


def test_replay_calibration_through_actual_fsm():
    path = Path(__file__).parent/'logs/grab-calibration/fallback-validation.json'
    if not path.exists():
        pytest.skip('Run validate_grab_calibration.py with captured calibration frames first')
    report = json.loads(path.read_text())
    for name in report['summaries']:
        rows = [r for r in report['details'] if r['position']==name]
        packets = run_case([record(r['x'],r['y'],r['area'],r['data'])
                            if r['data'] else {'status': 'no_detection'} for r in rows])
        closes = [p for p in packets if p.get('name')=='gripper' and p.get('value')=='close']
        # Left edge is reachable but must first sway into center tolerance.
        assert len(closes) == (0 if name in ('left','outside') else 1), name


def closes(packets):
    return [p for p in packets if p.get('name')=='gripper' and p.get('value')=='close']


def test_logged_quarter_pixel_excursion_preserves_progress():
    # Logged trial: 7 valid frames, Y=378.75 px (0.25 beyond edge), back inside.
    packets = run_case([record(y=.522)]*7 + [record(y=378.75/720)] + [record(y=.522)]*7)
    assert len(closes(packets)) == 1
    assert all(p['surge']==0 for p in packets if p.get('type')=='control')


def test_hysteresis_never_starts_or_finishes_grab_outside_original_roi():
    edge = record(y=378.75/720)
    assert closes(run_case([edge]*20)) == []
    assert closes(run_case([record()]*7 + [edge]*13)) == []


def test_real_exit_resets_streak():
    assert closes(run_case([record()]*7 + [record(y=.54)] + [record()]*7)) == []


def test_six_second_timeout_allows_acquisition_before_stable_frames():
    assert len(closes(run_case([record(area=.005)]*8 + [record()]*12))) == 1
