"""
tests/test_rov_link.py — Uji perilaku rov_link.py (jembatan JSON/UDP ↔ MAVLink)
================================================================================
`RovLink.__init__` membuka koneksi MAVLink sungguhan, jadi test di sini memakai
`object.__new__(RovLink)` lalu mengisi HANYA atribut yang dipakai jalur yang
diuji. Yang diuji adalah logika otoritas (kill-switch), bukan I/O.

    cd autonomy && PYTHONPATH= pytest tests/test_rov_link.py -v
"""

import os
import sys
import threading

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

import rov_link
from rov_link import RovLink, KILL_SWITCH_DEADZONE


def _link(control_mode="autonomous", external_fsm=False):
    """RovLink minimal tanpa MAVLink — cukup untuk menguji handle_command."""
    link = object.__new__(RovLink)
    link.external_fsm = external_fsm
    link.sp = {"surge": 0.0, "sway": 0.0, "yaw": 0.0, "heave": 0.0}
    link.control_mode = control_mode
    link.lock = threading.Lock()
    link.jejak = []                       # rekaman efek samping
    link.set_mode = lambda m: link.jejak.append(("set_mode", m))
    link.stop_mission5 = lambda: link.jejak.append(("stop_mission5",))
    return link


# ── Kill-switch: operator merebut kendali dari autonomy ─────────────────────
def test_kill_switch_axis_operator_membatalkan_autonomy():
    """Axis dari operator (from_fsm=False) di atas deadzone saat autonomous
    → mode balik manual DAN FSM dihentikan. Kalau hanya salah satu, ROV bisa
    terus digerakkan FSM sementara operator mengira sudah mengambil alih."""
    link = _link("autonomous")
    link.handle_command("surge", KILL_SWITCH_DEADZONE + 1, from_fsm=False)

    assert link.control_mode == "manual"
    assert ("set_mode", "MANUAL") in link.jejak
    assert ("stop_mission5",) in link.jejak
    assert link.sp["surge"] == KILL_SWITCH_DEADZONE + 1   # perintah tetap diteruskan


def test_kill_switch_tidak_menyala_di_bawah_deadzone():
    """Di bawah/­sama dengan ambang → autonomy JALAN TERUS (ini gunanya deadzone)."""
    link = _link("autonomous")
    link.handle_command("sway", KILL_SWITCH_DEADZONE, from_fsm=False)

    assert link.control_mode == "autonomous"
    assert link.jejak == []


def test_kill_switch_tidak_menyala_untuk_perintah_fsm_sendiri():
    """Perintah FSM sendiri (from_fsm=True) TIDAK boleh membatalkan autonomy —
    kalau tidak, FSM membunuh dirinya sendiri pada langkah servo pertama."""
    link = _link("autonomous")
    link.handle_command("heave", 300, from_fsm=True)      # skala penuh dari FSM

    assert link.control_mode == "autonomous"
    assert link.jejak == []
    assert link.sp["heave"] == 300


def test_kill_switch_diam_saat_mode_manual():
    """Sudah manual → tak ada yang perlu dibatalkan."""
    link = _link("manual")
    link.handle_command("yaw", 900, from_fsm=False)

    assert link.control_mode == "manual"
    assert link.jejak == []


# ── Skala ambang: dikunci agar tak "diperbaiki" ke skala yang salah ─────────
def test_deadzone_masuk_akal_pada_skala_axis_1000():
    """KILL_SWITCH_DEADZONE hidup di skala axis GUI -1000..1000 (clampAxis di
    server.js, AXIS_RANGE di rov_axes.py) — BUKAN -100..100.

    Komentar di rov_link.py pernah menyebut -100..100; angka 15 terbaca seolah
    '15% defleksi' padahal di skala sebenarnya = 1,5%. Yang membuatnya tetap
    aman adalah deadzone sisi-GUI (DEFAULT_DEADZONE=0.12 + expo 1.6 di
    shared/joystick-profile.js): drift stik dibuang sebelum terkirim, dan
    kill-switch efektif menyala di ~20% defleksi stik fisik.

    Dua arah yang dijaga test ini:
      • naik jadi ≥100 → operator harus mendorong stik hampir penuh sebelum
        bisa merebut kendali (takeover jadi lamban — bahaya keselamatan);
      • turun jadi ~0 → tiap noise membatalkan autonomy.

    CATATAN OPERASIONAL: profil joystick mengizinkan deadzone 0
    (`floatInRange(row.deadzone, 0, 0.9, …)`). Dengan deadzone 0, drift stik
    MEMANG bisa memicu abort palsu — jangan setel deadzone 0 di hari lomba.
    """
    assert 5 <= KILL_SWITCH_DEADZONE < 100, (
        "ambang di luar rentang wajar untuk skala -1000..1000; "
        "baca docstring ini sebelum mengubah"
    )


def test_konstanta_dan_axis_memakai_skala_yang_sama():
    """Sanity: semua axis yang dijaga kill-switch memang axis GUI yang di-clamp
    ke -1000..1000 di send_manual_control (bukan sub-himpunan lain)."""
    link = _link()
    assert set(link.sp) == {"surge", "sway", "yaw", "heave"}
    src = open(os.path.join(_AUTONOMY, "rov_link.py"), encoding="utf-8").read()
    assert "clamp(s[\"surge\"], -1000, 1000)" in src, (
        "skala clamp di send_manual_control berubah — tinjau ulang "
        "KILL_SWITCH_DEADZONE bersamaan"
    )


# ── Gerbang frame FSM: internal vs proses terpisah ──────────────────────────
def test_frame_fsm_dibuang_saat_manual():
    """Frame dari thread FSM INTERNAL yang belum mati (join timeout di
    stop_mission5) tak boleh menimpa sp sesudah operator kembali ke manual."""
    link = _link("manual", external_fsm=False)
    link.handle_command("heave", -300, from_fsm=True)
    assert link.sp["heave"] == 0.0, "frame FSM basi lolos padahal mode manual"


def test_frame_fsm_eksternal_diterima_tanpa_toggle():
    """FSM sebagai PROSES TERPISAH (tools/launch_sitl.py --fsm) harus diterima
    walau control_mode masih 'manual'.

    22 Agu 2026: gerbang di atas ditambahkan tanpa pengecualian ini, dan pada
    jalur SITL tak ada satu pun yang mengirim control_mode=autonomous ke
    rov_link (mengirimnya justru akan memanggil start_mission5() dan
    memunculkan FSM KEDUA yang bentrok). Akibatnya SELURUH perintah FSM
    dibuang diam-diam: depth tetap 0.00 dan M5_REDIVE selalu timeout ke
    M5_FALLBACK — skenario B/C berhenti lolos jalur visual tanpa satu pun
    pesan error."""
    link = _link("manual", external_fsm=True)
    link.handle_command("heave", -300, from_fsm=True)
    assert link.sp["heave"] == -300, "perintah FSM eksternal dibuang"


def test_fsm_eksternal_tidak_mematikan_kill_switch():
    """--external-fsm melonggarkan gerbang untuk frame FSM, BUKAN untuk
    operator. Kill-switch harus tetap menyala pada axis manual."""
    link = _link("autonomous", external_fsm=True)
    link.handle_command("surge", KILL_SWITCH_DEADZONE + 1, from_fsm=False)
    assert link.control_mode == "manual"
    assert ("stop_mission5",) in link.jejak
