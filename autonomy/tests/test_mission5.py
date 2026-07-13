"""
tests/test_mission5.py — Uji unit + integrasi misi 5 (KKI 2026)
================================================================
Dijalankan dengan pytest, cepat & deterministik (jam virtual FakeClock):

    cd autonomy
    pytest tests/ -v

Cakupan:
  • Unit   : PID, VisualServo (IBVS), PoseServo (PBVS), heading error, wall_from_qr.
  • Integrasi : Mission5FSM ditutup-loop dgn SimPlant — rantai misi 1→5 & misi-5-saja,
               mode PBVS & IBVS, plus ketahanan loss-of-lock (dropout QR).
"""

import os
import sys

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

import fsm.mission5 as m5
from control.visual_servo import PID, VisualServo, PoseServo
from vision.qr_detect import wall_from_qr, parse_payload
from fsm.mission5 import Mission5FSM, State
from config.loader import read_file, flatten, load_config, apply_config
from tests.evaluate_mission5 import run_scenario
from tests.sim_plant import FakeClock, SimPlant, SimCommandLink, SimTelemetry, SimVision


def _make_fsm():
    """FSM ringan dgn adapter sim — cukup untuk menguji helper murni (validasi payload)."""
    clock = FakeClock()
    plant = SimPlant()
    return Mission5FSM(SimCommandLink(plant), SimTelemetry(plant),
                       SimVision(plant, clock))


# ── Unit: PID ────────────────────────────────────────────────────────────────
def test_pid_proportional_and_clamp():
    pid = PID(kp=10.0, out_limit=100.0)
    assert pid.step(1.0, 0.1) == pytest.approx(10.0)
    # error besar → keluaran ter-clamp ke out_limit
    assert pid.step(1000.0, 0.1) == 100.0
    # error negatif → keluaran negatif
    assert pid.step(-1000.0, 0.1) == -100.0


def test_pid_integral_anti_windup():
    pid = PID(kp=0.0, ki=1.0, out_limit=100.0, i_limit=5.0)
    for _ in range(100):
        pid.step(10.0, 1.0)          # akumulasi integral besar
    # anti-windup: integral dibatasi i_limit → keluaran = ki*i_limit
    assert pid.step(0.0, 1.0) == pytest.approx(5.0)


# ── Unit: heading error ──────────────────────────────────────────────────────
@pytest.mark.parametrize('cur,tgt,exp', [
    (0, 0, 0), (10, 20, 10), (350, 10, 20), (10, 350, -20),
])
def test_heading_error_wraps(cur, tgt, exp):
    assert Mission5FSM._heading_error(cur, tgt) == pytest.approx(exp)


def test_heading_error_at_180_is_boundary():
    # 180° adalah batas ±180 (kedua tanda sah); pastikan magnitudonya 180.
    assert abs(Mission5FSM._heading_error(0, 180)) == pytest.approx(180)


# ── Unit: wall_from_qr (QR JSON terstruktur + string legacy) ─────────────────
@pytest.mark.parametrize('data,wall', [
    # JSON payload KKI 2026 (format baru)
    ('{"mission":5,"team":"HYDROSHIP","type":"payload","id":"A"}', 'A'),
    ('{"mission":5,"type":"payload","id":"b"}', 'B'),   # id huruf kecil → tetap dipetakan
    ('{"id":"Z"}', None),                                # id di luar A-D
    # String biasa (kompatibilitas mundur)
    ('A', 'A'), ('SIDE_B', 'B'), ('WALL-C', 'C'), ('HYDROSHIP-M5-D', 'D'),
    ('AREA', None), ('12345', None),
])
def test_wall_from_qr(data, wall):
    assert wall_from_qr(data) == wall


def test_parse_payload_json_vs_plain():
    assert parse_payload('{"id":"A","mission":5}') == {'id': 'A', 'mission': 5}
    assert parse_payload('A') is None            # bukan JSON
    assert parse_payload('[1,2,3]') is None       # JSON tapi bukan object
    assert parse_payload('12345') is None         # JSON angka


# ── Unit: validasi payload FSM (mission==5 & type==payload) ──────────────────
def test_payload_validation_accepts_target():
    fsm = _make_fsm()
    assert fsm._is_target_payload({'payload': {'mission': 5, 'type': 'payload', 'id': 'A'}})


def test_payload_validation_rejects_wrong_mission_or_type():
    fsm = _make_fsm()
    assert not fsm._is_target_payload({'payload': {'mission': 3, 'type': 'payload', 'id': 'A'}})
    assert not fsm._is_target_payload({'payload': {'mission': 5, 'type': 'debris', 'id': 'A'}})


def test_payload_validation_accepts_legacy_non_json():
    fsm = _make_fsm()
    assert fsm._is_target_payload({'payload': None})   # QR string biasa → tak divalidasi


# ── Unit: VisualServo (IBVS) konvergen ke aligned ────────────────────────────
def test_visual_servo_converges_when_centered():
    servo = VisualServo(target_area=3000.0, aligned_frames=3)
    out = None
    for _ in range(5):
        out = servo.step(320, 240, 3000.0, 640, 480, dt=0.1)   # tepat center & jarak
    assert out.aligned
    assert abs(out.ex) < 1e-6 and abs(out.ey) < 1e-6


def test_visual_servo_commands_reduce_offcenter_error():
    servo = VisualServo()
    out = servo.step(600, 240, 1000.0, 640, 480, dt=0.1)  # marker jauh di kanan & kecil
    assert not out.aligned
    assert out.sway != 0.0 and out.surge != 0.0           # ada koreksi geser & maju


# ── Unit: PoseServo (PBVS) konvergen ke aligned ──────────────────────────────
def test_pose_servo_converges_at_target_distance():
    servo = PoseServo(target_dist=0.30, aligned_frames=3)
    out = None
    for _ in range(5):
        out = servo.step(0.0, 0.0, 0.30, 0.0, dt=0.1)
    assert out.aligned


def test_pose_servo_not_aligned_when_far():
    servo = PoseServo(target_dist=0.30, aligned_frames=3)
    out = servo.step(0.20, 0.15, 0.90, 0.0, dt=0.1)
    assert not out.aligned
    assert out.surge > 0                                   # terlalu jauh → maju


# ── Integrasi: rantai misi penuh 1→5 mencapai skor sempurna ──────────────────
@pytest.mark.parametrize('provide_pose,label', [(True, 'PBVS'), (False, 'IBVS')])
def test_full_mission_reaches_100(provide_pose, label):
    rep = run_scenario(start_state=State.DIVE, provide_pose=provide_pose)
    assert rep['state_akhir'] == 'DONE', f'{label}: {rep["transitions"]}'
    assert rep['nilai_total'] == 100, f'{label}: skor={rep["skor"]}'
    assert rep['nilai_misi_5'] == 40
    assert rep['jalur']['used_visual_dock'] is True
    assert rep['jalur']['used_fallback'] is False
    assert rep['lulus'] is True


# ── Integrasi: misi-5-saja (handoff 1-4 manual → autonomous) ─────────────────
def test_mission5_only_autonomous_release():
    rep = run_scenario(start_state=State.M5_REDIVE, provide_pose=True)
    assert rep['state_akhir'] == 'DONE'
    assert rep['nilai_misi_5'] == 40
    assert rep['jalur']['used_visual_dock'] is True
    assert rep['payload']['unhooked'] is True
    assert rep['payload']['grabbed'] is True


# ── Integrasi: akurasi docking closed-loop (nembak x & y) ────────────────────
def test_docking_accuracy_within_tolerance():
    rep = run_scenario(start_state=State.M5_DOCK, provide_pose=True)
    acc = rep['akurasi_docking']
    assert acc is not None
    assert acc['radial_xy'] <= 0.06                        # terpusat < 6 cm saat engage
    assert abs(acc['rz'] - 0.30) <= 0.06                   # jarak engage sesuai target


# ── Integrasi: ketahanan loss-of-lock (dropout QR sesaat) ────────────────────
def test_docking_survives_intermittent_dropout():
    # buang 2 dari tiap 6 frame QR (0.2s < grace 0.6s) → dead-reckon hold menutupi,
    # docking tetap konvergen tanpa jatuh ke fallback timed.
    rep = run_scenario(start_state=State.M5_REDIVE, provide_pose=True,
                       dropout=lambda c: (c % 6) < 2)
    assert rep['state_akhir'] == 'DONE'
    assert rep['nilai_misi_5'] == 40
    assert rep['jalur']['used_visual_dock'] is True
    assert rep['jalur']['used_fallback'] is False


# ── Integrasi: HANG (misi 3b) & DOCK (misi 4) closed-loop ke HOOK ────────────
@pytest.mark.parametrize('provide_pose,label', [(True, 'PBVS'), (False, 'IBVS')])
def test_hang_dock_use_visual_hook_path(provide_pose, label):
    """Misi 3b & 4 mencapai skor via jalur VISUAL hook (bukan timer buta) — closed-loop
    jadi primary. Instrumentasi sim mengonfirmasi payload tergantung & ROV bersandar."""
    rep = run_scenario(start_state=State.DIVE, provide_pose=provide_pose)
    assert rep['state_akhir'] == 'DONE', f'{label}: {rep["transitions"]}'
    assert rep['skor']['m3'] == 15 and rep['skor']['m4'] == 15, label
    assert rep['jalur']['hang_used_fallback'] is False, f'{label}: HANG jatuh ke fallback'
    assert rep['jalur']['dock_used_fallback'] is False, f'{label}: DOCK jatuh ke fallback'
    assert rep['payload']['hung'] is True, f'{label}: payload tak tergantung ke hook'
    assert rep['payload']['docked'] is True, f'{label}: ROV tak bersandar saat dock'


def test_hang_dock_survive_intermittent_hook_dropout():
    """Dropout deteksi hook sesaat (2 dari tiap 6 frame ≈ 0.2s < grace 0.6s) ditutup
    dead-reckon hold → HANG/DOCK tetap konvergen visual tanpa jatuh ke fallback timed."""
    rep = run_scenario(start_state=State.DIVE, provide_pose=True,
                       hook_dropout=lambda c: (c % 6) < 2)
    assert rep['state_akhir'] == 'DONE'
    assert rep['skor']['m3'] == 15 and rep['skor']['m4'] == 15
    assert rep['jalur']['hang_used_fallback'] is False
    assert rep['jalur']['dock_used_fallback'] is False


def test_hang_dock_degrade_to_timed_when_hook_never_locks():
    """Hook TAK PERNAH terdeteksi → HANG & DOCK degradasi eksplisit ke jalur timed
    (jaring pengaman), misi tetap selesai. Membuktikan fallback = degradasi, bukan primary."""
    rep = run_scenario(start_state=State.DIVE, provide_pose=True,
                       hook_dropout=lambda c: True)
    assert rep['state_akhir'] == 'DONE'
    assert rep['skor']['m3'] == 15 and rep['skor']['m4'] == 15
    assert rep['jalur']['hang_used_fallback'] is True
    assert rep['jalur']['dock_used_fallback'] is True


def test_hook_servo_step_pbvs_and_ibvs_selection():
    """_hook_servo_step memilih PBVS bila det punya pose, IBVS (piksel) bila tidak —
    reuse VisualServo/PoseServo yang sama seperti servo QR."""
    fsm = _make_fsm()
    det_ibvs = {'center': (400, 200), 'area': 1000.0, 'frame_w': 640, 'frame_h': 480,
                'pose': None}
    out, mode = fsm._hook_servo_step(det_ibvs)
    assert mode == 'IBVS'
    assert out.sway != 0.0 and out.surge != 0.0       # ada koreksi geser & maju
    det_pbvs = dict(det_ibvs, pose={'x': 0.2, 'y': 0.1, 'z': 0.9})
    out, mode = fsm._hook_servo_step(det_pbvs)
    assert mode == 'PBVS'
    assert out.surge > 0                               # z>target → maju


def test_note_hook_sets_search_direction():
    """_note_hook mengambil arah sapu reacquire dari sisi lateral hook terakhir
    (agar bila lock hilang ROV menyapu MENUJU hook)."""
    fsm = _make_fsm()
    fsm._note_hook({'center': (500, 240), 'frame_w': 640, 'frame_h': 480, 'pose': None})
    assert fsm._hook_search_dir == 1                   # hook di kanan → sapu +
    fsm._note_hook({'center': (100, 240), 'frame_w': 640, 'frame_h': 480, 'pose': None})
    assert fsm._hook_search_dir == -1                  # hook di kiri → sapu −


# ── Real decode: QR JSON via pyzbar + pose solvePnP (skip bila lib tak ada) ──
def test_real_qr_json_decode_and_pose():
    cv2 = pytest.importorskip("cv2")
    segno = pytest.importorskip("segno")
    pyz = pytest.importorskip("pyzbar.pyzbar")
    import io, json
    import numpy as np
    from vision.qr_detect import VisionPipeline

    cam = VisionPipeline(source='usb')
    cam._K = np.array([[800, 0, 320], [0, 800, 240], [0, 0, 1]], float)
    cam._dist = np.zeros(5)
    payload = {"mission": 5, "team": "HYDROSHIP", "type": "payload", "id": "A"}
    text = json.dumps(payload)

    png = io.BytesIO()
    segno.make(text, error='m').save(png, kind='png', scale=8, border=4)
    png.seek(0)
    qr = cv2.imdecode(np.frombuffer(png.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
    frame = np.full((480, 640, 3), 255, np.uint8)
    h, w = qr.shape
    y0, x0 = (480 - h) // 2, (640 - w) // 2
    frame[y0:y0 + h, x0:x0 + w] = cv2.cvtColor(qr, cv2.COLOR_GRAY2BGR)

    objs = pyz.decode(frame)
    assert objs, "QR tidak terdecode oleh pyzbar"
    o = objs[0]
    data = o.data.decode('utf-8').strip()            # persis jalur _run_camera (tanpa .upper())
    pts = np.array([[p.x, p.y] for p in o.polygon])
    center = (int(pts[:, 0].mean()), int(pts[:, 1].mean()))
    area = float(cv2.contourArea(pts.reshape(-1, 1, 2)))
    res = cam._build_result(data, center, area, frame)

    # JSON tak rusak (case-sensitive) → field langsung dipakai
    assert res['data'] == text
    assert res['payload'] == payload
    assert res['payload']['id'] == 'A' and res['payload']['mission'] == 5
    assert res['wall'] == 'A'
    # PBVS solvePnP tidak crash di numpy 2.x & mengembalikan jarak positif
    pose = cam._estimate_pose_pts(cam._order_corners(pts), 0.04)
    assert pose is not None and pose['z'] > 0


# ── Integrasi: skenario semua sisi kolam A/B/C/D ─────────────────────────────
@pytest.mark.parametrize('wall', ['A', 'B', 'C', 'D'])
def test_full_mission_each_wall(wall):
    rep = run_scenario(start_state=State.DIVE, provide_pose=True, target_wall=wall)
    assert rep['state_akhir'] == 'DONE'
    assert rep['nilai_total'] == 100
    assert rep['skenario']['target_wall'] == wall


# ── Unit: config/loader.py — flatten & merge ─────────────────────────────────
def test_config_flatten_scalar_and_merge_keys():
    raw = {
        'depth': {'hook_depth': 0.55},
        'docking': {'servo_target_dist': 0.42},
        'wall_heading': {'A': 45},                 # partial — B/C/D sengaja tak diisi
        'invert': {'sway': True},                  # partial — axis lain sengaja tak diisi
        'unknown_group': {'x': 1},                  # kunci tak dikenal harus diabaikan
    }
    cfg = flatten(raw)
    assert cfg['HOOK_DEPTH'] == 0.55
    assert cfg['SERVO_TARGET_DIST'] == 0.42
    assert cfg['_WALL_HEADING_MERGE'] == {'A': 45}
    assert cfg['_SERVO_INVERT_MERGE'] == {'invert_sway': True}
    assert 'unknown_group' not in cfg and 'x' not in cfg.values()


def test_config_flatten_empty_when_no_matching_keys():
    assert flatten({'tidak_relevan': 123}) == {}


def test_config_apply_merges_partial_dict_without_clobbering():
    ns = {'WALL_HEADING': {'A': 270, 'B': 90, 'C': 0, 'D': 180},
         'SERVO_INVERT': {'invert_sway': False, 'invert_vert': False,
                          'invert_surge': False, 'invert_yaw': False}}
    apply_config(ns, {'_WALL_HEADING_MERGE': {'A': 45},
                      '_SERVO_INVERT_MERGE': {'invert_sway': True}})
    assert ns['WALL_HEADING'] == {'A': 45, 'B': 90, 'C': 0, 'D': 180}
    assert ns['SERVO_INVERT']['invert_sway'] is True
    assert ns['SERVO_INVERT']['invert_vert'] is False   # kunci lain tak ikut berubah


def test_config_load_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_config('/tidak/ada/mission5.local.yaml')


def test_config_load_unknown_extension_raises(tmp_path):
    bad = tmp_path / "cfg.txt"
    bad.write_text("HOOK_DEPTH: 0.5")
    with pytest.raises(ValueError):
        load_config(str(bad))


def test_config_json_and_yaml_equivalent(tmp_path):
    """Format .json harus menghasilkan flatten yang SAMA dgn .yaml berisi struktur identik
    (tim yang tak mau install PyYAML bisa pakai .json apa adanya)."""
    import json
    raw = {'depth': {'hook_depth': 0.5}, 'invert': {'yaw': True}}
    jf = tmp_path / "cfg.json"
    jf.write_text(json.dumps(raw))
    assert load_config(str(jf)) == flatten(raw)


def test_config_example_yaml_loads_and_covers_expected_constants():
    """File config/mission5.example.yaml yang di-commit harus tetap valid & lengkap."""
    example = os.path.join(_AUTONOMY, 'config', 'mission5.example.yaml')
    cfg = load_config(example)
    for attr in ('HOOK_DEPTH', 'SERVO_TARGET_DIST', 'SERVO_TARGET_AREA',
                'IBVS_KP_SWAY', 'PBVS_KP_SWAY', 'M5_LOCK_GRACE_T',
                'PAYLOAD_MISSION', 'PAYLOAD_TYPE',
                'HOOK_TARGET_AREA', 'HOOK_TARGET_DIST', 'HOOK_LOCK_GRACE_T',
                'HOOK_ACQUIRE_T', 'DOCK_APPROACH_SPEED', 'HOOK_MIN_AREA',
                'HOOK_PIPE_DIAM_M', 'HANG_SEAT_T', 'HANG_OPEN_T', 'HANG_BACK_T'):
        assert attr in cfg, f"{attr} hilang dari contoh config"
    assert cfg['_WALL_HEADING_MERGE'] == {'A': 270, 'B': 90, 'C': 0, 'D': 180}
    assert set(cfg['_SERVO_INVERT_MERGE'].keys()) == {
        'invert_sway', 'invert_vert', 'invert_surge', 'invert_yaw'}


# ── Integrasi: config yang diterapkan BENAR-BENAR mengubah perilaku FSM ──────
def test_config_override_changes_docking_target_at_runtime():
    """Bukti bahwa apply_config() bukan cuma mengubah nilai konstanta, tapi juga
    perilaku Mission5FSM saat dijalankan (late-binding global Python)."""
    backup = {k: getattr(m5, k) for k in ('SERVO_TARGET_DIST',)}
    try:
        apply_config(vars(m5), {'SERVO_TARGET_DIST': 0.45})
        rep = run_scenario(start_state=State.M5_REDIVE, provide_pose=True)
        assert rep['state_akhir'] == 'DONE'
        assert rep['nilai_misi_5'] == 40
        acc = rep['akurasi_docking']
        assert abs(acc['rz'] - 0.45) <= 0.06   # docking ke jarak BARU dari config
    finally:
        for k, v in backup.items():
            setattr(m5, k, v)   # jangan bocorkan override ke test lain
