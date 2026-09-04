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

import json
import os
import re
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
from tests.sim_plant import (FakeClock, SimPlant, SimCommandLink, SimTelemetry,
                             SimVision, install_fake_time)


def _make_fsm(bench_qr_dock=False):
    """FSM ringan dgn adapter sim — cukup untuk menguji helper murni (validasi payload)."""
    clock = FakeClock()
    plant = SimPlant()
    return Mission5FSM(SimCommandLink(plant), SimTelemetry(plant),
                       SimVision(plant, clock), bench_qr_dock=bench_qr_dock)


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


# ── Alur Misi 5 langkah 3-8 (langkah 1-2 = CASE MOTION di bridge) ───────────
def _hook_keypoints(tip_x, tip_y, conf=0.9):
    """Skeleton fixture: 0..2 batang, 3..4 kepala, point 5 tip J."""
    coords = [(tip_x, tip_y - 60), (tip_x, tip_y - 45),
              (tip_x, tip_y - 30), (tip_x - 12, tip_y - 22),
              (tip_x - 8, tip_y - 10), (tip_x, tip_y)]
    return [{'id': index, 'x': x, 'y': y, 'confidence': conf}
            for index, (x, y) in enumerate(coords)]


def _yolo_at_area_fraction(frac, conf=0.9):
    frame_w, frame_h = 640, 480
    width = 192.0
    height = frac * frame_w * frame_h / width
    bbox = [100.0, 100.0, width, height]
    tip = (bbox[0] + width * m5.HOOK_TIP_X_FRAC,
           bbox[1] + height * m5.HOOK_TIP_Y_FRAC)
    return {'status': 'relative_only', 'method': 'yolov8', 'confidence': conf,
            'bbox': bbox, 'keypoints': _hook_keypoints(*tip),
            'frame_w': frame_w, 'frame_h': frame_h}


def test_hook_align_vert_servo_menang_atas_asumsi_kedalaman():
    """Kamera melihat ujung J langsung — itu acuan vertikal yang lebih baik
    daripada HOOK_DEPTH. _left_hold hanya mengisi saat servo tak berpendapat."""
    fsm = _make_fsm()
    fsm._transition(State.M5_HOOK_ALIGN)
    frame_w, frame_h = 640, 480
    side = (m5.LEFT_YOLO_AREA_FRAC * frame_w * frame_h) ** 0.5
    dangkal = {'depth': m5.HOOK_DEPTH - 0.30}

    # ujung J jauh DI BAWAH tengah frame → servo minta turun, bukan _left_hold
    det = {'status': 'ok', 'method': 'yolov8', 'confidence': 0.9,
           'frame_w': frame_w, 'frame_h': frame_h,
           'bbox': [frame_w / 2 - side / 2, frame_h - side, side, side]}
    bbox = det['bbox']
    det['keypoints'] = _hook_keypoints(
        bbox[0] + bbox[2] * m5.HOOK_TIP_X_FRAC,
        bbox[1] + bbox[3] * m5.HOOK_TIP_Y_FRAC)
    fsm._yolo_source = lambda: det
    fsm._state_m5_hook_align(dangkal)
    assert fsm.cmd.plant._in['vert'] != 0

    # ujung J tepat di tengah vertikal → servo diam, depth hold mengambil alih
    fsm2 = _make_fsm()
    fsm2._transition(State.M5_HOOK_ALIGN)
    by = frame_h / 2 - side * m5.HOOK_TIP_Y_FRAC
    det2 = dict(det, bbox=[frame_w / 2 - side * m5.HOOK_TIP_X_FRAC, by, side, side])
    det2['keypoints'] = _hook_keypoints(frame_w / 2, frame_h / 2)
    fsm2._yolo_source = lambda: det2
    fsm2._state_m5_hook_align(dangkal)
    assert fsm2.cmd.plant._in['vert'] < 0, "dangkal → _left_hold menyuruh menyelam"


def test_hook_tip_memakai_keypoint_5_bukan_centroid_bbox():
    """Point 5 adalah ujung J; bbox hanya dipakai untuk estimasi jarak."""
    fsm = _make_fsm()
    det = {'bbox': [100.0, 20.0, 60.0, 400.0], 'frame_w': 640, 'frame_h': 480,
           'keypoints': _hook_keypoints(155.0, 410.0)}
    tip_x, tip_y = fsm._hook_tip(det)
    centroid_y = 20.0 + 400.0 / 2
    assert tip_y > centroid_y, "ujung J di bawah centroid"
    assert (tip_x, tip_y) == (155.0, 410.0)


def test_hook_tip_menolak_skeleton_lemah_atau_tidak_lengkap():
    fsm = _make_fsm()
    base = {'bbox': [100.0, 20.0, 60.0, 400.0],
            'frame_w': 640, 'frame_h': 480}
    assert fsm._hook_tip(dict(base, keypoints=_hook_keypoints(155, 410, conf=0.1))) is None
    assert fsm._hook_tip(dict(base, keypoints=_hook_keypoints(155, 410)[:-1])) is None


def test_hook_align_target_luas_ikut_resolusi_frame():
    """HOOK_TARGET_AREA px2 dikalibrasi di 640x480; frame worker 1280x736."""
    fsm = _make_fsm()
    det = _yolo_at_area_fraction(0.02)
    det.update(frame_w=1280, frame_h=736)
    fsm._align_target(det)
    assert fsm.hook_servo.target_area == m5.LEFT_YOLO_AREA_FRAC * 1280 * 736


def test_hook_align_konvergen_lalu_ke_qr_dock():
    fsm = _make_fsm()
    fsm._transition(State.M5_HOOK_ALIGN)
    # bbox pas di target luas, ujung J tepat di tengah frame
    frame_w, frame_h = 640, 480
    side = (m5.LEFT_YOLO_AREA_FRAC * frame_w * frame_h) ** 0.5
    bx = frame_w / 2 - side * m5.HOOK_TIP_X_FRAC
    by = frame_h / 2 - side * m5.HOOK_TIP_Y_FRAC
    det = {'status': 'ok', 'method': 'yolov8', 'confidence': 0.9,
           'bbox': [bx, by, side, side], 'frame_w': frame_w, 'frame_h': frame_h,
           'keypoints': _hook_keypoints(frame_w / 2, frame_h / 2)}
    fsm._yolo_source = lambda: det
    for _ in range(fsm.hook_servo.aligned_frames + 2):
        if fsm._state != State.M5_HOOK_ALIGN:
            break
        fsm._state_m5_hook_align({'depth': m5.HOOK_DEPTH})
    assert fsm._state == State.M5_QR_DOCK


def test_hook_align_stop_saat_data_hilang():
    fsm = _make_fsm()
    fsm.cmd.send(surge=30)
    fsm._transition(State.M5_HOOK_ALIGN)
    fsm._state_m5_hook_align({'depth': m5.HOOK_DEPTH})
    assert fsm.cmd.plant._in['surge'] == 0


def test_left_yolo_search_butuh_voting_bukan_satu_frame():
    """best.pt sesekali menyatakan Hook pada frame tanpa hook — satu frame palsu
    tak boleh menghentikan ROV jauh dari dinding."""
    fsm = _make_fsm()
    det = _yolo_at_area_fraction(0.02)          # conf 0.9, lolos gate
    fsm._yolo_source = lambda: det
    fsm._transition(State.M5_YOLO_SEARCH)
    for _ in range(m5.LEFT_YOLO_LOCK_FRAMES - 1):
        fsm._state_m5_yolo_search({'depth': m5.HOOK_DEPTH})
        assert fsm._state == State.M5_YOLO_SEARCH
        assert fsm.cmd.plant._in['surge'] == 0, "berhenti maju selagi konfirmasi"
    fsm._state_m5_yolo_search({'depth': m5.HOOK_DEPTH})
    assert fsm._state == State.M5_HOOK_ALIGN


def test_left_yolo_search_deteksi_sekejap_tidak_mengunci():
    """Satu frame palsu lalu hilang → hitungan luruh, ROV lanjut maju."""
    fsm = _make_fsm()
    hantu = [_yolo_at_area_fraction(0.02), None, None]
    fsm._yolo_source = lambda: hantu.pop(0) if hantu else None
    fsm._transition(State.M5_YOLO_SEARCH)
    fsm._state_m5_yolo_search({'depth': m5.HOOK_DEPTH})     # hantu
    assert fsm._left_search_hits == 1
    fsm._yolo_source = lambda: _yolo_at_area_fraction(0.02, conf=0.1)   # ada stream, di bawah gate
    fsm._state_m5_yolo_search({'depth': m5.HOOK_DEPTH})
    assert fsm._left_search_hits == 0
    assert fsm._state == State.M5_YOLO_SEARCH
    assert fsm.cmd.plant._in['surge'] == m5.SEARCH_SPEED


def test_left_yolo_search_berhenti_maju_setelah_budget_jarak():
    """Tanpa sensor jarak, satu-satunya rem sebelum dinding adalah budget detik."""
    fsm = _make_fsm()
    lemah = dict(_yolo_at_area_fraction(0.01), confidence=0.1)   # ada stream, blm yakin
    fsm._yolo_source = lambda: lemah
    fsm._state = State.M5_YOLO_SEARCH
    fsm._state_t = m5.time.time()
    fsm._state_m5_yolo_search({'depth': m5.HOOK_DEPTH})
    assert fsm.cmd.plant._in['surge'] == m5.SEARCH_SPEED, "pakai kecepatan terkalibrasi"

    fsm._state_t = m5.time.time() - m5.LEFT_ADVANCE_MAX_T - 0.1
    fsm._state_m5_yolo_search({'depth': m5.HOOK_DEPTH})
    assert fsm.cmd.plant._in['surge'] == 0, "budget habis → berhenti maju"
    assert fsm._state == State.M5_YOLO_SEARCH, "tetap melihat sampai LEFT_TIMEOUT_YOLO"


def test_left_yolo_search_tidak_maju_saat_worker_belum_ada():
    fsm = _make_fsm()
    fsm.cmd.send(surge=30)
    fsm._state = State.M5_YOLO_SEARCH
    fsm._state_t = m5.time.time()
    fsm._state_m5_yolo_search({})
    assert fsm.cmd.plant._in['surge'] == 0


def test_unhook_timeout_tetap_naik_bukan_abort_sambil_menggenggam():
    """Payload sudah di gripper: ABORT di sini menjamin skor 0."""
    fsm = _make_fsm()
    fsm._transition(State.M5_UNHOOK)
    fsm._state_t = m5.time.time() - m5.TIMEOUT_UNHOOK - 0.1
    fsm._state_m5_unhook({'depth': m5.HOOK_DEPTH})
    assert fsm._state == State.M5_ASCEND


def test_fallback_tidak_mengklaim_skor_penuh():
    """Jalur timed tak pernah melihat payload — log tak boleh bilang '+40 poin'."""
    fsm = _make_fsm()
    fsm._transition(State.M5_FALLBACK)
    fsm._state_t = m5.time.time() - (14.0 + m5.UNHOOK_LIFT_T + m5.UNHOOK_PULL_T) - 0.1
    fsm._state_m5_fallback({'depth': 0.0})     # sudah di permukaan → fase akhir
    assert fsm._state == State.DONE
    assert 0 < fsm.score()['m5'] < 40


def test_left_gagal_setelah_menghadap_dinding_degradasi_bukan_abort():
    """Misi 40 poin: timeout YOLO jangan pulang dengan skor 0."""
    for state, call in [
        (State.M5_YOLO_SEARCH, lambda f: f._state_m5_yolo_search({})),
        (State.M5_HOOK_ALIGN, lambda f: f._state_m5_hook_align({})),
        (State.M5_QR_DOCK, lambda f: f._state_m5_qr_dock({})),
    ]:
        fsm = _make_fsm()
        fsm._state = state
        fsm._state_t = m5.time.time() - 1e6      # semua timeout terlampaui
        call(fsm)
        assert fsm._state == State.M5_FALLBACK, state.name


def test_left_degradasi_dini_saat_jam_heat_hampir_habis():
    fsm = _make_fsm()
    fsm._state = State.M5_QR_DOCK
    fsm._state_t = m5.time.time()
    fsm._mission_t0 = m5.time.time() - m5.TIME_BUDGET_TOTAL + 5.0   # sisa 5 s
    fsm._state_m5_qr_dock({})
    assert fsm._state == State.M5_FALLBACK, "harus degradasi sebelum peluit, bukan ABORT"


def test_left_grip_menyerahkan_mundur_ke_unhook_yang_mengangkat_dulu():
    """Payload duduk di hook candy-cane: tarik mundur tanpa angkat = nyangkut."""
    fsm = _make_fsm()
    fsm._state = State.M5_GRIP
    fsm._state_t = m5.time.time() - m5.LEFT_GRIP_T - 0.1
    fsm._state_m5_grip({'depth': m5.HOOK_DEPTH})
    assert fsm._state == State.M5_UNHOOK

    # fase pertama M5_UNHOOK harus MENGANGKAT, belum mundur
    fsm._state_m5_unhook({'depth': m5.HOOK_DEPTH})
    axes = fsm.cmd.plant._in
    assert axes['vert'] > 0 and axes['surge'] == 0, "angkat dulu, jangan tarik"

    # setelah naik sejauh UNHOOK_LIFT_M baru menarik mundur
    fsm._state_m5_unhook({'depth': m5.HOOK_DEPTH - m5.UNHOOK_LIFT_M})
    assert fsm.cmd.plant._in['surge'] < 0


def test_qr_dock_yaw_dari_kemiringan_bukan_lenceng_lateral():
    """Dua aktuator tak boleh mengejar error yang sama: QR bisa 'di tengah'
    karena ROV menghadapnya, bukan karena ROV di depannya."""
    fsm = _make_fsm()
    # QR melenceng jauh ke kanan TAPI bidangnya tegak lurus (yaw_deg 0)
    det = {'center': (600, 240), 'area': m5.SERVO_TARGET_AREA, 'frame_w': 640,
           'frame_h': 480, 'payload': None,
           'pose': {'x': 0.2, 'y': 0.0, 'z': 0.3, 'dist': 0.3, 'yaw_deg': 0.0}}
    fsm._fresh_payload = lambda _age=0.5: det
    fsm._transition(State.M5_QR_DOCK)
    fsm._state_m5_qr_dock({'depth': m5.HOOK_DEPTH})
    axes = fsm.cmd.plant._in
    assert axes['sway'] != 0, "lenceng lateral tetap diurus sway"
    assert axes['yaw'] == 0, "sudah tegak lurus → tak ada alasan memutar"

    # Sebaliknya: QR di tengah tapi bidangnya miring → yaw bekerja, sway tidak
    det['center'] = (320, 240)
    det['pose'].update(x=0.0, yaw_deg=20.0)
    fsm2 = _make_fsm()
    fsm2._fresh_payload = lambda _age=0.5: det
    fsm2._transition(State.M5_QR_DOCK)
    fsm2._state_m5_qr_dock({'depth': m5.HOOK_DEPTH})
    assert fsm2.cmd.plant._in['yaw'] != 0


def test_qr_dock_tidak_selesai_selagi_masih_miring():
    """Terpusat saja tak cukup — gripper tak boleh menutup dalam keadaan menyerong."""
    fsm = _make_fsm()
    det = {'center': (320, 240), 'area': m5.SERVO_TARGET_AREA, 'frame_w': 640,
           'frame_h': 480, 'payload': None,
           'pose': {'x': 0.0, 'y': 0.0, 'z': 0.3, 'dist': 0.3,
                    'yaw_deg': m5.LEFT_QR_YAW_TOL_DEG + 5.0}}
    fsm._fresh_payload = lambda _age=0.5: det
    fsm._transition(State.M5_QR_DOCK)
    for _ in range(fsm.servo.aligned_frames + 3):
        fsm._state_m5_qr_dock({'depth': m5.HOOK_DEPTH})
    assert fsm._state == State.M5_QR_DOCK, "masih miring → jangan lanjut ke GRIP"

    det['pose']['yaw_deg'] = 0.0                     # sudah tegak lurus
    for _ in range(fsm.servo.aligned_frames):
        fsm._state_m5_qr_dock({'depth': m5.HOOK_DEPTH})
    assert fsm._state == State.M5_GRIP


def test_qr_dock_center_dulu_baru_maju():
    """Servo menggerbang surge sampai terpusat — merapat menyerong bikin gripper
    meleset. Dulu FSM membuang out.surge dan membalik urutan itu."""
    fsm = _make_fsm()
    jauh = m5.SERVO_TARGET_AREA * 0.4          # masih jauh → ingin maju
    det = {'center': (400, 240), 'area': jauh,  # tapi melenceng lateral
           'frame_w': 640, 'frame_h': 480, 'pose': None, 'payload': None}
    fsm._fresh_payload = lambda _age=0.5: det
    fsm._transition(State.M5_QR_DOCK)
    fsm._state_m5_qr_dock({'depth': m5.HOOK_DEPTH})
    melenceng = fsm.cmd.plant._in['surge']
    assert fsm.cmd.plant._in['sway'] != 0, "lateral dikoreksi lebih dulu"

    det['center'] = (320, 240)                 # sudah terpusat
    fsm2 = _make_fsm()
    fsm2._fresh_payload = lambda _age=0.5: det
    fsm2._transition(State.M5_QR_DOCK)
    fsm2._state_m5_qr_dock({'depth': m5.HOOK_DEPTH})
    assert fsm2.cmd.plant._in['surge'] > melenceng, "terpusat → boleh maju penuh"


def test_bench_qr_target_mulut_gripper_align_dan_berhenti_setelah_grip(monkeypatch):
    monkeypatch.setattr(m5, 'SERVO_TARGET_X', 0.0794)
    monkeypatch.setattr(m5, 'SERVO_TARGET_Y', -0.0349)
    monkeypatch.setattr(m5, 'SERVO_TARGET_DIST', 0.3573)
    monkeypatch.setattr(m5, 'SERVO_TARGET_YAW_DEG', 11.18)
    fsm = _make_fsm(bench_qr_dock=True)
    det = {'center': (613, 401), 'area': 9402.0, 'frame_w': 1280,
           'frame_h': 720, 'payload': None,
           'pose': {'x': 0.0794, 'y': -0.0349, 'z': 0.3573,
                    'dist': 0.368, 'yaw_deg': 11.18}}
    fsm._fresh_payload = lambda _age=0.5: det
    fsm._transition(State.M5_QR_DOCK)
    for _ in range(fsm.pose_servo.aligned_frames + 1):
        fsm._state_m5_qr_dock({'depth': 0.0})
    assert fsm._state == State.M5_GRIP
    assert fsm.cmd.plant._in['vert'] == 0

    fsm._state_t = m5.time.time() - m5.LEFT_GRIP_T - 0.1
    fsm._state_m5_grip({'depth': 0.0})
    assert fsm._state == State.DONE
    assert not fsm.cmd.plant.s.armed


def test_bench_qr_start_menunggu_decode_sebelum_auto_arm(monkeypatch):
    fsm = _make_fsm(bench_qr_dock=True)
    det = {'center': (640, 360), 'area': 9402.0, 'frame_w': 1280,
           'frame_h': 720, 'payload': None, 'pose': None}
    results = iter((None, None, det))
    armed_while_waiting = []
    def fresh(_age=0.5):
        armed_while_waiting.append(fsm.cmd.plant.s.armed)
        return next(results)
    monkeypatch.setattr(fsm, '_fresh_payload', fresh)
    sleeps = []
    monkeypatch.setattr(m5.time, 'sleep', sleeps.append)
    monkeypatch.setattr(fsm, '_loop', lambda: None)
    fsm.start(State.M5_QR_DOCK, wait_mode=False)
    assert armed_while_waiting == [False, False, False]
    assert fsm.cmd.plant.s.armed
    assert sleeps == [0.1, 0.1]


def test_bench_qr_otoritas_penuh_tetap_mulus_dan_qr_hilang_abort(monkeypatch):
    monkeypatch.setattr(m5, 'SERVO_MAX_SPEED', 100.0)
    monkeypatch.setattr(m5, 'SERVO_KP_YAW', 1.0)
    monkeypatch.setattr(m5, 'SERVO_SLEW', 60.0)
    monkeypatch.setattr(m5, 'LEFT_QR_MAX_YAW', 100.0)
    fsm = _make_fsm(bench_qr_dock=True)
    fsm._servo_dt = lambda: 0.1
    det = {'center': (1200, 700), 'area': 100.0, 'frame_w': 1280,
           'frame_h': 720, 'payload': None,
           'pose': {'x': 0.8, 'y': 0.8, 'z': 1.2, 'yaw_deg': 80.0}}
    fsm._fresh_payload = lambda _age=0.5: det
    fsm._transition(State.M5_QR_DOCK)
    samples = []
    for _ in range(8):
        fsm._state_m5_qr_dock({'depth': 0.0})
        samples.append(dict(fsm.cmd.plant._in))
    assert all(abs(samples[0][k]) <= 6.0 for k in ('surge', 'sway', 'yaw', 'vert'))
    assert all(abs(samples[i][k] - samples[i - 1][k]) <= 6.01
               for i in range(1, len(samples))
               for k in ('surge', 'sway', 'yaw', 'vert'))
    assert all(abs(samples[-1][k]) > 10.0 for k in ('sway', 'yaw', 'vert'))
    assert samples[-1]['surge'] > 0, "surge merayap sampai QR terpusat"

    # Setelah lateral/vertikal terpusat, surge juga boleh memakai rentang >10%.
    fsm_center = _make_fsm(bench_qr_dock=True)
    fsm_center._servo_dt = lambda: 0.1
    centered = dict(det, pose={'x': 0.0, 'y': 0.0, 'z': 1.2, 'yaw_deg': 0.0})
    fsm_center._fresh_payload = lambda _age=0.5: centered
    fsm_center._transition(State.M5_QR_DOCK)
    for _ in range(8):
        fsm_center._state_m5_qr_dock({'depth': 0.0})
    assert abs(fsm_center.cmd.plant._in['surge']) > 10.0

    fsm._fresh_payload = lambda _age=0.5: None
    fsm._m5_last_det_t = m5.time.time() - m5.M5_LOCK_GRACE_T - 0.1
    fsm._state_m5_qr_dock({'depth': 0.0})
    assert fsm._state == State.ABORT


def test_alur_kiri_menahan_kedalaman_di_tiap_state():
    """Tanpa vert, ROV yang sedikit apung hanyut naik selama puluhan detik."""
    det = _yolo_at_area_fraction(m5.LEFT_YOLO_AREA_FRAC * 0.5)   # msh perlu maju
    lemah = dict(det, confidence=0.1)   # stream ada, blm yakin → SEARCH terus maju
    dangkal = {'depth': m5.HOOK_DEPTH - 0.30, 'heading': 0.0}
    for state, source, call in [
        (State.M5_YOLO_SEARCH, lemah, lambda f: f._state_m5_yolo_search(dangkal)),
        (State.M5_GRIP, None, lambda f: f._state_m5_grip(dangkal)),
    ]:
        fsm = _make_fsm()
        fsm._yolo_source = lambda src=source: src
        fsm._state = state
        fsm._state_t = m5.time.time()
        call(fsm)
        assert fsm.cmd.plant._in['vert'] < 0, f"{state.name} tak menahan kedalaman"


# ── SCAN_QR: creep ke wall-hint (belum tervalidasi) vs fallback yaw-sweep ────
def test_scan_qr_creeps_toward_wall_hint_without_setting_target():
    fsm = _make_fsm()
    fsm.vision.wall_hint = {
        'wall': 'B', 'confidence': 0.9,
        'center': (500, 240), 'area': 500.0,   # jauh dari tengah & dari target_area
        'frame_w': 800, 'frame_h': 480,
    }
    fsm._state_scan_qr(telem={}, vis=None)
    axes = fsm.cmd.plant._in
    assert axes['yaw'] == 0, "creep tak boleh yaw — tebakan CNN kasar, bukan align presisi"
    assert axes['surge'] > 0, "area << target_area → harus maju mendekat"
    assert axes['sway'] != 0, "center jauh dari tengah frame → harus geser"
    assert fsm._target_wall is None, "hint TAK tervalidasi, tak boleh mengisi target_wall"


def test_scan_qr_falls_back_to_yaw_sweep_without_hint():
    fsm = _make_fsm()
    fsm.vision.wall_hint = None
    fsm._state_scan_qr(telem={}, vis=None)
    axes = fsm.cmd.plant._in
    assert axes['yaw'] == m5.YAW_SPEED, "tanpa hint sama sekali → perilaku lama (yaw di tempat)"
    assert axes['surge'] == 0


def test_scan_qr_stops_creep_once_aligned_without_decode():
    """Sudah sedekat target engage tapi decode masih gagal → diam, JANGAN terus maju
    berbekal tebakan tak tervalidasi (risiko tabrak dinding)."""
    fsm = _make_fsm()
    fsm.vision.wall_hint = {
        'wall': 'B', 'confidence': 0.9,
        'center': (400, 240), 'area': m5.SERVO_TARGET_AREA,   # persis di tengah & di target
        'frame_w': 800, 'frame_h': 480,
    }
    for _ in range(10):   # servo butuh beberapa tick beruntun in-tolerance agar 'aligned'
        fsm._state_scan_qr(telem={}, vis=None)
    axes = fsm.cmd.plant._in
    assert axes['surge'] == 0 and axes['sway'] == 0 and axes['yaw'] == 0


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


# ── Unit: peredam approach (deadband / slew / gate surge / hysteresis) ───────
def test_pid_deadband_diam_di_dekat_target():
    """Error mikro (riak menggeser centroid) tak boleh menggerakkan thruster."""
    pid = PID(kp=100.0, deadband=0.02)
    assert pid.step(0.01, 0.1) == 0.0
    assert pid.step(0.05, 0.1) != 0.0


def test_pid_slew_membatasi_lonjakan_command():
    """Command 0→penuh dalam satu tick membuat ROV menyentak & miring."""
    pid = PID(kp=1000.0, out_limit=100.0, slew=120.0)
    assert pid.step(1.0, 0.1) == pytest.approx(12.0)     # 120 %/s × 0.1 s
    assert pid.step(1.0, 0.1) == pytest.approx(24.0)     # naik bertahap, bukan melompat
    tanpa_slew = PID(kp=1000.0, out_limit=100.0, slew=0.0)
    assert tanpa_slew.step(1.0, 0.1) == pytest.approx(100.0)


def test_gerbang_approach_tidak_menembus_peredam_laju():
    """Saat error lateral tiba-tiba masuk toleransi, gerbang approach melompat
    0.15 → 1.0. Gerbang itu dikalikan SETELAH PID, jadi tanpa peredam kedua
    surge menyentak dari ~5 % ke 35 % dalam satu tick (297 %/s, terukur di
    tools/hook_thruster_darat.py) — persis saat gripper paling dekat payload."""
    servo = PoseServo(target_dist=0.30, tol_xy=0.05, kp_surge=140.0, slew=120.0)
    for _ in range(6):
        melenceng = servo.step(0.25, 0.0, 0.90, dt=0.1).surge   # jauh & menyerong
    center = servo.step(0.00, 0.0, 0.90, dt=0.1).surge          # mendadak lurus
    assert center - melenceng <= 120.0 * 0.1 + 1e-6
    for _ in range(5):                                          # tetap sampai penuh
        center = servo.step(0.00, 0.0, 0.90, dt=0.1).surge
    assert center == pytest.approx(35.0)


def test_surge_ditahan_selagi_masih_melenceng_lateral():
    """Maju sambil melenceng = gripper datang menyerong & meleset dari payload.
    Surge saat off-center harus jauh lebih kecil drpd surge saat sudah center,
    pada error jarak yang SAMA."""
    kw = dict(target_dist=0.30, tol_xy=0.05, slew=0.0)
    jauh_melenceng = PoseServo(**kw).step(0.25, 0.0, 0.90, dt=0.1).surge
    jauh_center    = PoseServo(**kw).step(0.00, 0.0, 0.90, dt=0.1).surge
    assert jauh_melenceng < jauh_center * 0.5
    assert jauh_melenceng > 0                     # tetap merayap, tak mandek total


def test_aligned_bertahan_dari_satu_frame_berisik():
    """Satu dropout/frame kotor di air keruh tak boleh menghapus seluruh streak
    (dulu bikin ALIGNED tak pernah terkunci → docking jatuh ke fallback timed)."""
    servo = PoseServo(target_dist=0.30, aligned_frames=3)
    for _ in range(3):
        servo.step(0.0, 0.0, 0.30, dt=0.1)
    servo.step(0.5, 0.0, 0.90, dt=0.1)            # satu frame meleset jauh
    assert servo.step(0.0, 0.0, 0.30, dt=0.1).aligned, "streak tak boleh reset ke 0"


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


def test_unhook_mengukur_angkat_dari_depth_sebelum_mundur():
    fsm = _make_fsm()
    sent = []
    fsm.cmd.send = lambda **kw: sent.append(kw)
    fsm._state = State.M5_UNHOOK
    fsm._state_t = m5.time.time()

    fsm._state_m5_unhook({'depth': 0.42})
    assert sent[-1]['vert'] == m5.M5_UNHOOK_VERT
    assert sent[-1]['gripper'] == 1

    fsm._state_m5_unhook({'depth': 0.42 - m5.UNHOOK_LIFT_M - 0.01})
    assert sent[-1]['surge'] == m5.M5_UNHOOK_SURGE
    assert sent[-1]['gripper'] == 1

    fsm._unhook_pull_t = m5.time.time() - m5.UNHOOK_PULL_T - 0.01
    fsm._state_m5_unhook({'depth': 0.42 - m5.UNHOOK_LIFT_M - 0.01})
    assert fsm._state == State.M5_ASCEND


@pytest.mark.parametrize('depth', [None, float('nan'), 0.0])
def test_unhook_depth_invalid_abort(depth):
    fsm = _make_fsm()
    fsm._state = State.M5_UNHOOK
    fsm._state_t = m5.time.time()
    fsm._state_m5_unhook({'depth': depth})
    assert fsm._state == State.ABORT


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


# ── CommandSender: wire format ke rov_link.py ────────────────────────────────
def test_command_sender_emits_heave_scaled_to_1000():
    """rov_link.py::self.sp hanya mengenal key 'heave' (bukan 'vert') dalam skala
    -1000..1000, sedangkan FSM menghitung dalam persen (-100..100). Kunci kontrak
    ini supaya mismatch nama/skala (OPEN-FASE1) tak lolos lagi tanpa terdeteksi."""
    sent = []

    class FakeSock:
        def setsockopt(self, *a):
            pass

        def sendto(self, raw, addr):
            sent.append(json.loads(raw.decode()))

    cmd = m5.CommandSender()
    cmd._sock = FakeSock()

    cmd.send(surge=-50, sway=10, yaw=0, vert=30, gripper=True)

    by_name = {pkt['name']: pkt['value'] for pkt in sent}
    assert by_name['surge'] == -500
    assert by_name['sway'] == 100
    assert by_name['yaw'] == 0
    assert 'vert' not in by_name
    assert by_name['heave'] == 300
    assert by_name['gripper'] == 'close'


# ── Kontrak telemetry FSM ↔ rov_link ─────────────────────────────────────────

def _rov_link_telem_keys():
    """Key telemetry yang BENAR-BENAR dikirim rov_link.py, dibaca dari sumbernya.

    Sengaja parsing teks, bukan `import rov_link`: modul itu membuka socket
    MAVLink saat konstruksi dan butuh pymavlink, sedangkan test ini cuma perlu
    tahu nama field. Cukup dua bentuk yang dipakai file itu — literal
    `self.telem = {...}` di __init__ dan penugasan `self.telem["x"] = ...`.
    """
    with open(os.path.join(_AUTONOMY, 'rov_link.py'), encoding='utf-8') as f:
        src = f.read()
    keys = set(re.findall(r'self\.telem\["([a-z_0-9]+)"\]', src))
    init = re.search(r'self\.telem = \{(.*?)\}', src, re.S)
    assert init, "bentuk `self.telem = {...}` di rov_link.py berubah — perbarui test ini"
    keys |= set(re.findall(r'"([a-z_0-9]+)":', init.group(1)))
    return keys


def test_fsm_hanya_membaca_field_telemetry_yang_dikirim_rov_link():
    """Cerminan test_command_sender_emits_heave_scaled_to_1000, arah sebaliknya.

    Bug 'vert' vs 'heave' (OPEN-FASE1) lolos karena tak ada yang mengunci nama
    field COMMAND. Bug kembarannya di sisi TELEMETRY lolos dengan cara yang sama
    persis: mission5.py membaca telem['mode'] == 'autonomous'/'manual' padahal
    rov_link mengisi 'mode' dgn mode ArduSub ('MANUAL'/'ALT_HOLD') dan menaruh
    gate GUI di 'control_mode'. Keduanya diam — tidak error, tidak warning,
    cek-nya sekadar tak pernah menyala.

    BATAS TEST INI, tegas: ia hanya menangkap nama yang TIDAK DIKENAL. Bug
    'vert'/'heave' bentuknya begitu, jadi tertangkap. Bug 'mode'/'control_mode'
    TIDAK — 'mode' adalah key sah di rov_link, cuma artinya lain. Dimutasi balik
    ke 'mode', test ini tetap hijau (sudah dicek).

    Yang menjaga bug kedua itu adalah test_handoff_kembali_ke_manual_memicu_abort
    di bawah, yang menguji PERILAKU dan memang gagal saat dimutasi. Keduanya
    dipertahankan karena menangkap kelas yang berbeda; jangan buang salah satu
    dengan alasan tumpang tindih.
    """
    with open(os.path.join(_AUTONOMY, 'fsm', 'mission5.py'), encoding='utf-8') as f:
        src = f.read()
    # DUA pola, karena mission5.py memakai keduanya dan bug aslinya ada di
    # pola kedua: `telem.get('x')` pada dict yang sudah diambil, dan
    # `self.telem.get().get('x')` yang mengambil dict-nya lebih dulu.
    dibaca = set(re.findall(r"telem\.get\(\)\.get\('([a-z_0-9]+)'", src))
    dibaca |= set(re.findall(r"(?<!\)\.)telem\.get\('([a-z_0-9]+)'", src))
    assert dibaca, "pola telem.get('...') di mission5.py berubah — perbarui test ini"

    tak_dikenal = dibaca - _rov_link_telem_keys()
    assert not tak_dikenal, (
        f"mission5.py membaca field telemetry yang tak pernah dikirim rov_link.py: "
        f"{sorted(tak_dikenal)}"
    )


def test_handoff_kembali_ke_manual_memicu_abort():
    """Toggle GUI Autonomous→Manual saat FSM jalan → FSM berhenti sendiri.

    Ini lapis KEDUA (rov_link.stop_mission5 adalah yang pertama), dan lapis yang
    sampai 2026-08-21 mati total karena membaca field yang salah.
    """
    fsm = _make_fsm()
    fsm._require_auto = True
    fsm._running = True
    fsm._transition(State.DIVE)

    # Operator memutar toggle balik ke Manual.
    telem_asli = fsm.telem.get
    fsm.telem.get = lambda: {**telem_asli(), 'control_mode': 'manual'}

    fsm._loop()

    assert fsm._state == State.ABORT
    assert not fsm._running


def test_handoff_tetap_jalan_saat_control_mode_autonomous():
    """Kebalikannya — 'autonomous' TIDAK boleh memicu abort.

    Tanpa test ini, membalik perbandingan (abort saat != 'manual') akan lolos
    test di atas sambil mematikan autonomy sepenuhnya.
    """
    fsm = _make_fsm()
    fsm._require_auto = True
    fsm._running = True
    fsm._transition(State.DIVE)

    telem_asli = fsm.telem.get
    fsm.telem.get = lambda: {**telem_asli(), 'control_mode': 'autonomous'}

    # Satu iterasi loop tak boleh membuang FSM ke ABORT.
    telem = fsm.telem.get()
    assert not (fsm._require_auto and telem.get('control_mode') == 'manual')
    assert fsm._state == State.DIVE


def test_abort_kirim_emergency_stop_sebelum_running_false():
    """abort() harus emergency_stop() DULU, baru _running=False.

    Urutan kebalik pernah nyata bikin race: begitu _running jadi False, thread
    FSM sendiri (rov_link.start_mission5 _run finally) langsung cmd.close();
    kalau emergency_stop() belum sempat sendto(), itu race dgn close() dan
    lempar OSError Bad file descriptor — mematikan thread loop_rx_json rov_link
    (semua command GUI berhenti masuk sampai proses direstart)."""
    fsm = _make_fsm()
    fsm._running = True
    running_saat_emit = []
    fsm.cmd.emergency_stop = lambda: running_saat_emit.append(fsm._running)

    fsm.abort()

    assert running_saat_emit == [True]
    assert not fsm._running


def test_commandsender_emit_setelah_close_tidak_crash():
    """Dua thread bisa panggil abort() nyaris bersamaan (rov_link.handle_command
    DAN self-check Mission5FSM._loop() — sengaja rangkap, lihat komentar di
    _loop). Salah satu close() cmd duluan; _emit() dari thread lain sesudahnya
    harus no-op, BUKAN OSError Bad file descriptor yang mematikan thread
    loop_rx_json rov_link."""
    cmd = m5.CommandSender(host='127.0.0.1', port=0)
    cmd.close()
    cmd.emergency_stop()  # tak boleh raise
    cmd.close()  # idempotent, tak boleh raise dobel-close


def test_commandsender_menandai_frame_src_fsm():
    """Kill-switch rov_link membedakan axis operator dari axis FSM lewat field
    'src'. Dulu dia nebak dari alamat pengirim (127.0.0.1 = FSM), yang diam-diam
    mati begitu server.js jalan sehost dgn rov_link (GUI/SITL di satu mesin):
    axis operator ikut ber-IP loopback, dianggap FSM, abort tak pernah nyala.
    Kalau tanda ini hilang, kill-switch balik jadi tak bisa dipicu."""
    terkirim = []
    cmd = m5.CommandSender(host='127.0.0.1', port=0)
    cmd._sock = type("FakeSock", (), {
        "sendto": lambda self, raw, dest: terkirim.append(json.loads(raw.decode())),
        "close": lambda self: None,
    })()

    cmd.send(surge=50)

    assert terkirim, "tak ada frame terkirim"
    assert all(f.get('src') == 'fsm' for f in terkirim), terkirim


# ── Integrasi: jalur M5_FALLBACK (degradasi timed misi 5) ────────────────────
# Drill Fase 4 "tutup lensa kamera saat docking" (PERSIAPAN_FASE2-4.md §4.4):
# QR payload hilang permanen → misi 5 WAJIB degradasi ke M5_FALLBACK dan tetap
# selesai (kredit), BUKAN ABORT. Sebelumnya tak ada test yang menapaki jalur ini —
# yang ada hanya memastikan fallback TIDAK terpakai di jalur normal.
def test_m5_fallback_when_qr_lost_during_dock():
    """QR ter-lock di REDIVE lalu HILANG saat M5_DOCK (lensa tertutup di tengah
    docking) → M5_DOCK timeout → M5_FALLBACK → DONE, misi 5 tetap dapat nilai."""
    rep = run_scenario(start_state=State.M5_REDIVE, provide_pose=True,
                       dropout=lambda c: c > 40)
    visited = [t[2] for t in rep['transitions']]
    assert 'M5_DOCK' in visited, f'REDIVE harus sempat lock QR dulu: {visited}'
    assert 'M5_FALLBACK' in visited, f'harus degradasi ke fallback: {visited}'
    assert rep['state_akhir'] == 'DONE'          # bukan ABORT
    assert rep['nilai_misi_5'] > 0               # tetap dapat kredit
    assert rep['jalur']['used_fallback'] is True
    assert rep['jalur']['used_visual_dock'] is False


def test_m5_aborts_when_qr_never_acquired():
    """QR tak pernah terdeteksi → berhenti aman, bukan grab/unhook timed buta."""
    rep = run_scenario(start_state=State.M5_REDIVE, provide_pose=True,
                       dropout=lambda c: True)
    assert rep['state_akhir'] == 'ABORT'
    assert rep['jalur']['used_fallback'] is False
    assert rep['payload']['unhooked'] is False


# ── Kontrak gripper: rov_link.py (autonomous) vs gripper_controller.py (manual) ──
def test_gripper_pwm_sama_dengan_gripper_controller():
    """Dua implementasi TERPISAH menggerakkan channel fisik yang sama (CH7):
    rov_link.py (jalur FSM/autonomous) dan gripper_controller.py (jalur manual
    rov_agent.py, dipakai via GripperController). 22 Agu 2026: keduanya sempat
    berbeda — rov_link.py 1900/1100, gripper_controller.py 1580/1350 (kalibrasi
    nyata di tepi kolam). Mengirim 1900/1100 ke gripper yang travel amannya cuma
    1580/1350 mendorong servo jauh melewati batas fisiknya.

    gripper_controller.py di root repo, bukan sinkronisasi jauh seperti
    'vert'/'heave' — jadi dites langsung (bukan regex teks) sekalian mengunci
    channel-nya juga sama."""
    root = os.path.dirname(_AUTONOMY)
    if root not in sys.path:
        sys.path.insert(0, root)
    import gripper_controller as gc

    with open(os.path.join(_AUTONOMY, 'rov_link.py'), encoding='utf-8') as f:
        src = f.read()

    def const(name):
        m = re.search(rf"^{name}\s*=\s*(\d+)", src, re.M)
        assert m, f"{name} tak ditemukan di rov_link.py — bentuknya berubah?"
        return int(m.group(1))

    assert const('GRIPPER_SERVO_CH') == gc.GRIPPER_SERVO_CH
    assert const('GRIPPER_PWM_OPEN') == gc.GRIPPER_PWM_OPEN
    assert const('GRIPPER_PWM_CLOSE') == gc.GRIPPER_PWM_CLOSE


# ── MARK gantungan: arah M5_REDIVE tanpa koordinat x/y ──────────────────────
def _fsm_with_mark(heading=None, depth=None):
    from tests.sim_plant import (FakeClock, SimPlant, SimCommandLink, SimTelemetry,
                             SimVision, install_fake_time)
    clock, plant = FakeClock(), SimPlant()
    return m5.Mission5FSM(SimCommandLink(plant), SimTelemetry(plant),
                          SimVision(plant, clock),
                          marked_heading=heading, marked_depth=depth)


def test_mark_heading_menang_atas_wall_heading():
    """MARK adalah heading TERUKUR di gantungan sungguhan; WALL_HEADING masih
    tabel placeholder yang wajib dikalibrasi ulang tiap arena. Jadi mark menang."""
    fsm = _fsm_with_mark(heading=123.0)
    fsm._target_wall = 'C'                      # WALL_HEADING['C'] = 0
    # Menghadap 123° = persis heading yang di-mark → tak perlu berputar.
    assert fsm._heading_toward_wall({'heading': 123.0}) == 0
    # Menghadap 0° (yaitu WALL_HEADING['C']) → HARUS tetap berputar ke 123°,
    # membuktikan tabel tidak dipakai saat mark ada.
    assert fsm._heading_toward_wall({'heading': 0.0}) != 0


def test_tanpa_mark_jatuh_ke_wall_heading():
    """Perilaku lama utuh — jalur SITL/misi 1-5 penuh tak boleh berubah."""
    fsm = _fsm_with_mark()
    fsm._target_wall = 'C'                      # WALL_HEADING['C'] = 0
    assert fsm._heading_toward_wall({'heading': 0.0}) == 0
    assert fsm._heading_toward_wall({'heading': 90.0}) != 0


def test_mark_memberi_arah_walau_target_wall_kosong():
    """INI kegagalan 22 Agu. Misi 1-4 dijalankan MANUAL → FSM tak pernah lewat
    SCAN_QR → _target_wall None. Dulu M5_REDIVE memaksa yaw=YAW_SPEED (putar
    buta) dan mengabaikan mark; log Pi menunjukkan dua run berturut
    'M5_REDIVE timeout — QR tak diperoleh'.

    Dengan mark, arah harus datang dari heading yang direkam, dan begitu ROV
    sudah menghadap ke sana yaw HARUS 0 — bukan terus berputar."""
    fsm = _fsm_with_mark(heading=200.0)
    assert fsm._target_wall is None
    assert fsm._heading_toward_wall({'heading': 200.0}) == 0, "sudah menghadap → berhenti berputar"
    assert fsm._heading_toward_wall({'heading': 100.0}) != 0, "belum menghadap → berputar"


def test_tanpa_mark_dan_tanpa_target_wall_tetap_menyapu():
    """Jaring pengaman: operator lupa MARK → tetap menyapu mencari QR, bukan diam."""
    fsm = _fsm_with_mark()
    assert fsm._target_wall is None
    assert fsm._heading_toward_wall({'heading': 0.0}) != 0


def test_mark_depth_dipakai_sebagai_target_selam():
    """M5_REDIVE menyelam ke kedalaman yang di-MARK, bukan HOOK_DEPTH.

    Penting karena HOOK_DEPTH default diukur DARI PERMUKAAN sedangkan Guidebook
    mengukur hook 0,45 m DARI DASAR — hanya sama bila kolam persis 0,9 m."""
    sent = []
    fsm = _fsm_with_mark(heading=90.0, depth=0.25)
    fsm.cmd.send = lambda **kw: sent.append(kw)
    fsm._fresh_payload = lambda *a, **k: None   # isolasi: uji gerbang KEDALAMAN saja,
                                               # tanpa QR sim yang memicu M5_DOCK
    fsm._state = m5.State.M5_REDIVE
    fsm._state_t = m5.time.time()

    fsm._state_m5_redive({'depth': 0.10, 'heading': 90.0})   # jauh di atas 0.25
    assert sent and sent[-1].get('vert', 0) < 0, "harus menyelam"

    sent.clear()
    fsm._state_m5_redive({'depth': 0.24, 'heading': 90.0})   # sudah di level mark
    # Sampai di level mark → berhenti menyelam & serahkan ke pencarian lateral.
    assert fsm._state == m5.State.M5_SEARCH, (
        f"pada 0.24 m sudah dianggap sampai (mark 0.25), harus lanjut mencari: {fsm._state}")
    assert not any(kw.get('vert', 0) < 0 for kw in sent), (
        f"tak boleh menyelam lagi setelah sampai: {sent}")


# ── M5_SEARCH: pencarian lateral kembali ke gantungan ────────────────────────
# MARK cuma memberi heading+depth (2 dari 3 DOF). Posisi SEPANJANG dinding tak
# diketahui — sapu yaw di tempat tak bisa memperbaikinya. Test di bawah mengunci
# perilaku ladder yang menyisir dimensi ketiga itu.

def _search_fsm(heading=90.0, depth=0.45, lat=0.0, hint=None, wall_heading=90.0):
    """FSM yang SUDAH berada di M5_SEARCH, dgn plant bersimulasi lateral.

    Jam FSM DIVIRTUALKAN (install_fake_time) — tanpa itu `phase_el` memakai waktu
    NYATA sementara test memajukan waktu simulasi, jadi sub-fase ladder tak pernah
    berganti dan testnya jadi bohong."""
    clock = FakeClock()
    plant = SimPlant(sim_lateral=True, wall_heading=wall_heading, lat=lat)
    plant.s.armed = True
    plant.s.depth = depth
    plant.s.heading = heading
    vision = SimVision(plant, clock)
    vision.wall_hint = hint
    fsm = m5.Mission5FSM(SimCommandLink(plant), SimTelemetry(plant), vision,
                         marked_heading=wall_heading, marked_depth=depth)
    install_fake_time(m5, clock)                 # WAJIB: samakan jam FSM & jam sim
    fsm._transition(m5.State.M5_SEARCH)          # memakai blok reset sungguhan
    fsm._state_t = m5.time.time()
    return fsm, plant, clock


def _run_search(fsm, plant, clock, ticks, dt=0.1):
    """Jalankan _state_m5_search N tick, majukan jam & integrasikan plant."""
    for _ in range(ticks):
        if fsm._state != m5.State.M5_SEARCH:
            break
        fsm._state_m5_search(plant.telemetry())
        clock.sleep(dt)
        plant.step(dt)


def test_m5_search_holds_depth():
    """Pencarian berlangsung puluhan detik — kedalaman TIDAK boleh hanyut.

    Cabang sapu lama mengirim send(yaw=…) tanpa vert sama sekali, jadi ROV yang
    sedikit apung naik pelan-pelan dan QR keluar dari FOV justru saat dicari."""
    fsm, plant, clock = _search_fsm(depth=0.45, lat=99.0)     # lat besar = tak akan ketemu
    plant.s.depth = 0.60                                # mulai 0.15 m di bawah target
    _run_search(fsm, plant, clock, 400)
    assert abs(plant.s.depth - 0.45) <= 2 * m5.DEPTH_TOLERANCE, (
        f"kedalaman hanyut ke {plant.s.depth:.2f} (target 0.45)")


def test_m5_search_ladder_widens_and_alternates():
    """Leg harus MEMBESAR & berganti sisi tiap putaran — itu yang membuat cakupan
    melebar mengelilingi titik mark, bukan menyisir satu sisi saja."""
    fsm, plant, clock = _search_fsm(lat=99.0)
    legs, dirs = [], []
    prev = fsm._search_phase
    for _ in range(4000):
        fsm._state_m5_search(plant.telemetry())
        if prev == 'turn_back' and fsm._search_phase == 'look':
            legs.append(fsm._search_leg_t)
            dirs.append(fsm._search_dir)
        prev = fsm._search_phase
        clock.sleep(0.1)
        plant.step(0.1)
        if len(legs) >= 3:
            break
    assert len(legs) >= 3, f"ladder tak menyelesaikan 3 leg: {legs}"
    assert legs[0] < legs[1] < legs[2], f"leg tak membesar: {legs}"
    assert dirs[0] == -dirs[1] and dirs[1] == -dirs[2], f"sisi tak berganti: {dirs}"
    assert legs[-1] <= m5.SEARCH_LEG_T_MAX


def test_m5_search_span_capped():
    """Pagar keras: kolam kecil — begitu menyusur sejauh batas, surge HARUS berhenti
    walau leg belum habis, supaya ROV tak menabrak sudut kolam.

    Ladder ditaruh langsung di ambang (leg terpanjang, span nyaris penuh) karena
    zigzag alami berosilasi di sekitar titik mark & tak pernah menyentuh pagar."""
    fsm, plant, clock = _search_fsm(lat=99.0)
    fsm._search_creep_block = True                 # isolasi: uji ladder saja
    fsm._search_leg_t = m5.SEARCH_LEG_T_MAX
    fsm._search_pos_t = m5.SEARCH_SPAN_MAX_T - 0.5
    fsm._search_dir = 1
    fsm._search_next_phase('traverse')
    surges = []
    for _ in range(int(m5.SEARCH_LEG_T_MAX / 0.1)):
        if fsm._search_phase != 'traverse':
            break
        fsm._state_m5_search(plant.telemetry())
        span = fsm._search_pos_t + fsm._search_dir * (m5.time.time() - fsm._search_phase_t)
        surges.append((span, plant._in.get('surge', 0.0)))
        clock.sleep(0.1)
        plant.step(0.1)
    beyond = [s for span, s in surges if span >= m5.SEARCH_SPAN_MAX_T]
    assert beyond, "prasyarat: uji harus benar-benar melewati ambang span"
    assert all(s == 0 for s in beyond), f"surge tak dihentikan di pagar span: {beyond[:5]}"


def test_m5_search_reacquire_via_hint_without_decode():
    """QR bisa DILOKALISASI jauh sebelum bisa DIBACA — ROV harus merayap mendekat
    ke quad itu, bukan mengabaikannya sampai timeout."""
    hint = {'center': (420, 240), 'area': 400.0, 'frame_w': 640, 'frame_h': 480,
            'wall': 'C', 'confidence': 0.9, 'validated': False}
    fsm, plant, clock = _search_fsm(lat=99.0, hint=hint)
    for _ in range(30):
        fsm._state_m5_search(plant.telemetry())
        clock.sleep(0.1)
        plant.step(0.1)
    assert fsm._search_creep_t is not None, "hint terlihat tapi tak merayap mendekat"
    assert plant._in.get('surge', 0) > 0, "merayap harus MAJU ke kandidat"


def test_m5_search_hint_leads_to_dock_once_decoded():
    """Setelah cukup dekat, decode berhasil → docking normal."""
    hint = {'center': (330, 245), 'area': 2900.0, 'frame_w': 640, 'frame_h': 480,
            'wall': 'C', 'confidence': 0.9, 'validated': False}
    fsm, plant, clock = _search_fsm(lat=99.0, hint=hint)
    for _ in range(20):
        fsm._state_m5_search(plant.telemetry())
        clock.sleep(0.1)
        plant.step(0.1)
    plant.s.lat = 0.0                        # QR akhirnya masuk kerucut → decode jadi
    for _ in range(10):
        if fsm._state != m5.State.M5_SEARCH:
            break
        fsm._state_m5_search(plant.telemetry())
        clock.sleep(0.1)
        plant.step(0.1)
    assert fsm._state == m5.State.M5_DOCK


def test_m5_search_creep_gives_up_and_resumes_ladder():
    """Kandidat palsu (tangga/pipa) tak boleh menahan ROV menempel dinding sampai
    timeout — menyerah setelah SEARCH_CREEP_MAX_T lalu lanjut menyisir."""
    hint = {'center': (330, 240), 'area': 2900.0, 'frame_w': 640, 'frame_h': 480,
            'wall': 'C', 'confidence': 0.9, 'validated': False}
    fsm, plant, clock = _search_fsm(lat=99.0, hint=hint)     # decode tak akan pernah jadi
    for _ in range(int((m5.SEARCH_CREEP_MAX_T + 2.0) / 0.1)):
        fsm._state_m5_search(plant.telemetry())
        clock.sleep(0.1)
        plant.step(0.1)
    assert fsm._state == m5.State.M5_SEARCH, "tak boleh nyangkut di M5_DOCK"
    assert fsm._search_creep_t is None, "harus berhenti merayap setelah batas waktu"


def test_m5_search_timeout_aborts():
    """Gagal total → netral/disarm, bukan grab timed tanpa target."""
    fsm, plant, clock = _search_fsm(lat=99.0)
    fsm._state_t = m5.time.time() - (m5.TIMEOUT_SEARCH + 1)
    fsm._state_m5_search(plant.telemetry())
    assert fsm._state == m5.State.ABORT


# ── Time-budget dinamis: jam TOTAL heat di ATAS timeout per state ────────────
def test_m5_search_budget_low_aborts_early():
    """Waktu heat hampir habis → berhenti aman sebelum gerak buta."""
    fsm, plant, clock = _search_fsm(lat=99.0)
    min_needed = fsm._min_time_needed_from(m5.State.M5_SEARCH)
    fsm._mission_t0 = m5.time.time() - (m5.TIME_BUDGET_TOTAL - min_needed + 1)
    fsm._state_m5_search(plant.telemetry())
    assert fsm._state == m5.State.ABORT


def test_m5_search_budget_ample_no_early_degrade():
    """Waktu heat masih banyak → perilaku LAMA utuh, tak ada degradasi dini."""
    fsm, plant, clock = _search_fsm(lat=99.0)
    fsm._mission_t0 = m5.time.time()   # baru mulai — budget penuh
    fsm._state_m5_search(plant.telemetry())
    assert fsm._state == m5.State.M5_SEARCH


def test_m5_redive_hands_over_to_search_not_blind_spin():
    """Sampai di kedalaman mark tanpa QR → menyisir lateral, BUKAN berputar di tempat."""
    fsm, plant, clock = _search_fsm(depth=0.45, lat=99.0)
    fsm._transition(m5.State.M5_REDIVE)
    fsm._state_t = m5.time.time()
    fsm._state_m5_redive({'depth': 0.45, 'heading': 90.0})
    assert fsm._state == m5.State.M5_SEARCH


def test_m5_redive_tracks_visible_qr_without_approaching_wall():
    """Saat QR terlihat selama turun, koreksi lateral aktif tetapi surge tetap nol."""
    fsm, plant, clock = _search_fsm(depth=0.10, lat=0.10)
    fsm._marked_depth = 0.45
    fsm._transition(m5.State.M5_REDIVE)
    fsm._state_t = m5.time.time()

    fsm._state_m5_redive(plant.telemetry())

    assert plant._in['vert'] < 0, "ROV harus tetap menyelam ke kedalaman MARK"
    assert plant._in['sway'] != 0, "offset QR harus menghasilkan koreksi lateral"
    assert plant._in['surge'] == 0, "jangan mendekati dinding selama masih turun"


def test_m5_search_finds_laterally_offset_payload_end_to_end():
    """Bukti utuh: payload lenceng 0.8 m ke samping — di luar kerucut kamera dari
    titik start — dan ladder benar-benar menemukannya sampai M5_DOCK."""
    fsm, plant, clock = _search_fsm(depth=0.45, lat=0.8)
    assert not plant.qr_visible(), "prasyarat: payload harus TAK terlihat di awal"
    for _ in range(3000):
        if fsm._state != m5.State.M5_SEARCH:
            break
        fsm._state_m5_search(plant.telemetry())
        clock.sleep(0.1)
        plant.step(0.1)
    assert fsm._state == m5.State.M5_DOCK, (
        f"ladder gagal menemukan payload (lat={plant.s.lat:.2f}, rz={plant.s.rz:.2f})")
