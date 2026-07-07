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

from control.visual_servo import PID, VisualServo, PoseServo
from vision.aruco_qr import wall_from_qr
from fsm.mission5 import Mission5FSM, State
from tests.evaluate_mission5 import run_scenario


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


# ── Unit: wall_from_qr ───────────────────────────────────────────────────────
@pytest.mark.parametrize('data,wall', [
    ('A', 'A'), ('SIDE_B', 'B'), ('WALL-C', 'C'), ('HYDROSHIP-M5-D', 'D'),
    ('AREA', None), ('12345', None),
])
def test_wall_from_qr(data, wall):
    assert wall_from_qr(data) == wall


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


# ── Integrasi: skenario semua sisi kolam A/B/C/D ─────────────────────────────
@pytest.mark.parametrize('wall', ['A', 'B', 'C', 'D'])
def test_full_mission_each_wall(wall):
    rep = run_scenario(start_state=State.DIVE, provide_pose=True, target_wall=wall)
    assert rep['state_akhir'] == 'DONE'
    assert rep['nilai_total'] == 100
    assert rep['skenario']['target_wall'] == wall
