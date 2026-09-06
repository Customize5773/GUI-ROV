"""tests/test_pi_vision_pipeline.py — plumbing YOLO setelah pindah ke Raspberry Pi.

Sejak 7 Sep 2026 kedua worker YOLO berjalan DI PI, bukan di laptop. Tiga potong
logika baru menopang perpindahan itu, dan ketiganya diuji di sini:

  1. VISION_WANT / vision_want()  — kamera mana yang dibaca tiap state FSM.
     Salah memetakan = FSM buta di state tsb, di tengah kolam.
  2. inference_wanted()           — gate CPU di sisi worker. HARUS jatuh-aman:
     ragu sedikit pun -> tetap jalan.
  3. emit() + enable_udp_emit()   — amplop UDP worker -> rov_agent. Kalau
     amplopnya meleset, hasil deteksi diam-diam dibuang validator dan ROV
     berhenti bergerak tanpa pesan error.

Uji (3) sengaja diakhiri di `_validate_hook_vision` / `_validate_qr_vision` yang
SEBENARNYA, bukan tiruan: kontrak antara worker dan rov_agent itulah yang
menahan seluruh arsitektur baru.
"""
import json
import os
import socket
import sys
import threading
import time

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_AUTONOMY)
for _path in (_AUTONOMY, _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fsm.mission5 import State, VISION_WANT, vision_want  # noqa: E402
from tools import hook_vision_worker as worker  # noqa: E402


# ── 1. Peta kamera per-state ──────────────────────────────────────────────────

def test_qr_dock_only_needs_bottom():
    assert vision_want(State.M5_QR_DOCK) == ['BOTTOM']


def test_hook_align_only_needs_wall():
    assert vision_want(State.M5_HOOK_ALIGN) == ['WALL']


def test_yolo_search_needs_both_cameras():
    """M5_YOLO_SEARCH mengintip QR tiap tick sebagai jalan pintas ke M5_QR_DOCK
    (lihat _state_m5_yolo_search). Menggating BOTTOM di sini menghapus jalan
    pintas itu dan memakan jatah 10 menit — regresi yang tak terlihat di log."""
    assert sorted(vision_want(State.M5_YOLO_SEARCH)) == ['BOTTOM', 'WALL']


def test_unknown_state_falls_back_to_both():
    """Jatuh-aman: salah menyalakan cuma boros CPU, salah mematikan membutakan FSM."""
    assert sorted(vision_want(State.ABORT)) == ['BOTTOM', 'WALL']
    assert sorted(vision_want(None)) == ['BOTTOM', 'WALL']


def test_every_mapped_state_lists_known_cameras():
    for state, cameras in VISION_WANT.items():
        assert cameras, f"{state} memetakan daftar kosong — itu mematikan vision diam-diam"
        for camera in cameras:
            assert camera in ('WALL', 'BOTTOM'), f"{state}: kamera asing {camera!r}"


# ── 2. Gate CPU di worker ─────────────────────────────────────────────────────

def _state_with(want, age=0.0):
    telemetry = {'_telemetry_ts': time.time() - age}
    if want is not None:
        telemetry['mission5'] = {'vision_want': want}
    return telemetry, threading.Lock()


def test_gate_allows_when_camera_listed():
    state, lock = _state_with(['BOTTOM', 'WALL'])
    assert worker.inference_wanted(state, lock, 'WALL') is True


def test_gate_blocks_when_camera_not_listed():
    state, lock = _state_with(['BOTTOM'])
    assert worker.inference_wanted(state, lock, 'WALL') is False


def test_gate_opens_when_telemetry_is_stale():
    """rov_agent mati / uji darat / FSM idle: worker TETAP jalan supaya overlay
    GUI hidup. Diam di sini berarti kamera mati tanpa sebab yang terlihat."""
    state, lock = _state_with(['BOTTOM'], age=5.0)
    assert worker.inference_wanted(state, lock, 'WALL') is True


def test_gate_opens_when_fsm_does_not_publish_vision_want():
    """Kompatibel dengan FSM versi lama yang belum menerbitkan vision_want."""
    state, lock = _state_with(None)
    assert worker.inference_wanted(state, lock, 'WALL') is True
    state, lock = _state_with([])
    assert worker.inference_wanted(state, lock, 'WALL') is True


# ── 3. Amplop UDP worker -> rov_agent ─────────────────────────────────────────

@pytest.fixture
def udp_inbox():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('127.0.0.1', 0))
    sock.settimeout(2.0)
    yield sock, sock.getsockname()[1]
    sock.close()
    worker._udp_sink = None


def _roundtrip(inbox, channel, record):
    sock, port = inbox
    worker.enable_udp_emit(f'127.0.0.1:{port}', channel)
    worker.emit(record)
    return json.loads(sock.recvfrom(65535)[0])


HOOK_RECORD = {
    'status': 'ok', 'method': 'yolov8', 'confidence': 0.82,
    'bbox': [650.0, 10.0, 105.0, 292.0], 'frame_w': 1280, 'frame_h': 720,
    'keypoints': [{'id': i, 'x': 670.0 + i, 'y': 100.0 + i * 20, 'confidence': 0.9}
                  for i in range(6)],
}

QR_RECORD = {
    'status': 'ok', 'method': 'yolo_qr', 'data': 'MISSION5|TYPE=A',
    'payload': {'mission': '5', 'type': 'A'}, 'wall': 'B',
    'center': [640.0, 360.0], 'area': 900.0, 'confidence': 0.71,
    'frame_w': 1280, 'frame_h': 720,
    'pose': {'x': 0.01, 'y': -0.02, 'z': 0.30, 'dist': 0.30, 'yaw_deg': 3.5},
}


def test_emit_wraps_record_in_rov_agent_envelope(udp_inbox):
    message = _roundtrip(udp_inbox, 'hook_vision', HOOK_RECORD)
    # Amplop ini PERSIS yang dulu dikirim server.js dari laptop — itulah sebabnya
    # handler di rov_agent.py tidak berubah sebaris pun saat YOLO pindah ke Pi.
    assert message['name'] == 'hook_vision'
    assert message['value']['method'] == 'yolov8'


def test_emitted_hook_record_survives_rov_agent_validator(udp_inbox):
    import rov_agent
    message = _roundtrip(udp_inbox, 'hook_vision', HOOK_RECORD)
    clean = rov_agent._validate_hook_vision(message['value'])
    assert clean is not None, "hasil worker ditolak validator Pi — FSM tak akan pernah melihatnya"
    assert len(clean['keypoints']) == 6, "keypoint 2..5 membidik servo docking; tak boleh hilang"


def test_emitted_qr_record_survives_rov_agent_validator(udp_inbox):
    import rov_agent
    message = _roundtrip(udp_inbox, 'qr_vision', QR_RECORD)
    clean = rov_agent._validate_qr_vision(message['value'])
    assert clean is not None, "hasil worker QR ditolak validator Pi"
    assert clean['data'] == 'MISSION5|TYPE=A'
    assert clean['pose']['yaw_deg'] == pytest.approx(3.5)


def test_emit_without_udp_still_writes_stdout(capsys):
    """Jalur laptop tidak boleh ikut berubah: tanpa --emit-udp, stdout saja."""
    worker._udp_sink = None
    worker.emit({'status': 'no_detection'})
    assert json.loads(capsys.readouterr().out.strip())['status'] == 'no_detection'


# ── 4. Keypoint di luar frame ─────────────────────────────────────────────────
# Regresi 7 Sep 2026. Geometri letterbox PERSEGI jalur ONNX menaruh keypoint 0
# di y = -2,9 px pada fixture hook nyata, dan validator lama membuang SELURUH
# deteksi karenanya — termasuk ujung "J" (id 5) yang membidik servo docking.
# Batang hook memang menerus melewati tepi frame justru saat ROV sudah dekat,
# jadi menolak seluruh deteksi di situ berarti buta tepat saat paling penting.

def _hook_record(**overrides):
    record = {k: (list(v) if isinstance(v, list) else v) for k, v in HOOK_RECORD.items()}
    record['keypoints'] = [dict(k) for k in HOOK_RECORD['keypoints']]
    record.update(overrides)
    return record


def test_keypoint_just_outside_frame_is_accepted():
    import rov_agent
    record = _hook_record()
    record['keypoints'][0]['y'] = -2.9          # batang hook menerus ke atas
    clean = rov_agent._validate_hook_vision(record)
    assert clean is not None, (
        "keypoint 2,9 px di luar frame membuang deteksi hook lengkap — "
        "ini membutakan docking justru saat ROV paling dekat")
    assert clean['keypoints'][5]['id'] == 5, "ujung J harus tetap sampai ke FSM"


@pytest.mark.parametrize("bad", [1e9, -99999.0, float('nan'), float('inf')])
def test_absurd_keypoint_still_rejected(bad):
    """Melonggarkan batas BUKAN berarti membuka pintu: batas kewarasan tetap ada."""
    import rov_agent
    record = _hook_record()
    record['keypoints'][0]['y'] = bad
    assert rov_agent._validate_hook_vision(record) is None
