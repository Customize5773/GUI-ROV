"""Unit test bridge Mission5FSM <-> rov_agent (tanpa hardware/pymavlink/opencv).

    python3 -m unittest test_rov_mission5_bridge -v
"""

import ast
import os
import threading
import unittest

from rov_mission5_bridge import (
    Mission5CommandAdapter,
    Mission5Runner,
    Mission5TelemetryAdapter,
)


def _adapter(sink):
    return Mission5CommandAdapter(
        set_axis=lambda **kw: sink.update(kw),
        set_gripper=lambda c: sink.update(grip=c),
        arm=lambda o: sink.update(arm=o),
        emergency_stop=lambda: sink.update(estop=True),
    )


class TestSkalaDanNamaAxis(unittest.TestCase):
    """Dua bug yang SUDAH pernah terjadi di proyek ini, dikunci sekaligus.

    1. Skala: Mission5FSM memakai PERSEN (-100..100) di API-nya; rov_agent dan
       seluruh pipeline GUI memakai -1000..1000. Lupa mengalikan ×10 membuat
       seluruh gerakan FSM cuma ~10% kekuatan dan wahana nyaris diam.
    2. Nama: FSM menyebut sumbu vertikal `vert`, rov_agent/GUI menyebutnya
       `heave`. Mismatch persis ini membuat perintah menyelam hilang tanpa
       jejak (OPEN-FASE1, 12 Agu 2026).
    """

    def test_persen_dikali_sepuluh(self):
        sink = {}
        _adapter(sink).send(surge=30, sway=-10, yaw=25, vert=-40)
        self.assertEqual(sink["surge"], 300)
        self.assertEqual(sink["sway"], -100)
        self.assertEqual(sink["yaw"], 250)

    def test_vert_dipetakan_ke_heave(self):
        sink = {}
        _adapter(sink).send(vert=-30)
        self.assertNotIn("vert", sink, "axis harus bernama 'heave', bukan 'vert'")
        self.assertEqual(sink["heave"], -300)

    def test_skala_penuh_tidak_terpotong(self):
        # 100% FSM harus jadi 1000 (skala penuh GUI), bukan 100.
        sink = {}
        _adapter(sink).send(surge=100)
        self.assertEqual(sink["surge"], 1000)


class TestGripperDanFailsafe(unittest.TestCase):
    def test_gripper_hanya_dikirim_saat_diminta(self):
        sink = {}
        _adapter(sink).send(surge=10)          # gripper=None
        self.assertNotIn("grip", sink,
                         "gripper=None berarti 'jangan sentuh', bukan 'buka'")

    def test_gripper_truthy_menutup(self):
        sink = {}
        _adapter(sink).send(gripper=1)
        self.assertIs(sink["grip"], True)
        sink.clear()
        _adapter(sink).send(gripper=0)
        self.assertIs(sink["grip"], False)

    def test_stop_all_menetralkan_semua_axis(self):
        sink = {}
        cmd = _adapter(sink)
        cmd.send(surge=50, sway=50, yaw=50, vert=50)
        cmd.stop_all()
        for ax in ("surge", "sway", "yaw", "heave"):
            self.assertEqual(sink[ax], 0, msg=ax)

    def test_stop_all_tidak_disarm(self):
        # stop_all dipakai ANTAR-STATE; kalau ia disarm, FSM mati di tengah misi.
        sink = {}
        _adapter(sink).stop_all()
        self.assertNotIn("estop", sink)
        self.assertNotIn("arm", sink)

    def test_emergency_stop_memanggil_failsafe(self):
        sink = {}
        _adapter(sink).emergency_stop()
        self.assertTrue(sink.get("estop"))


class TestTelemetryAdapter(unittest.TestCase):
    def test_meneruskan_state_apa_adanya(self):
        t = Mission5TelemetryAdapter(read_state=lambda: {"depth": 0.7,
                                                         "control_mode": "autonomous"})
        self.assertEqual(t.get()["depth"], 0.7)
        self.assertEqual(t.get()["control_mode"], "autonomous")

    def test_get_membaca_ulang_tiap_panggilan(self):
        # FSM memanggil .get() tiap iterasi loop; kalau di-cache, ia akan
        # mengambil keputusan berdasar kedalaman basi.
        seq = iter([{"depth": 0.1}, {"depth": 0.9}])
        t = Mission5TelemetryAdapter(read_state=lambda: next(seq))
        self.assertEqual(t.get()["depth"], 0.1)
        self.assertEqual(t.get()["depth"], 0.9)


class TestRunnerGagalLunak(unittest.TestCase):
    def test_start_tanpa_paket_autonomy_tidak_melempar(self):
        """Pi produksi belum tentu punya autonomy/+opencv. Import gagal harus
        berarti 'misi 5 tak tersedia', BUKAN agent mati dan kontrol manual
        ikut hilang."""
        pesan = []
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=pesan.append)
        r._import_autonomy = lambda: (_ for _ in ()).throw(
            ImportError("No module named 'cv2'"))

        self.assertFalse(r.start())          # tidak melempar
        self.assertFalse(r.is_running())
        self.assertTrue(any("TIDAK BISA START" in p for p in pesan), pesan)

    def test_stop_saat_belum_pernah_start_aman(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        r.stop()                              # tidak boleh melempar
        self.assertFalse(r.is_running())

    def test_state_name_none_saat_tidak_jalan(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        self.assertIsNone(r.state_name())


class TestAutonomousMotionConfig(unittest.TestCase):
    def test_tuning_gerak_valid_disimpan(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        ok, cfg = r.update_motion_config({"dive": 0.16, "yaw": 20})
        self.assertTrue(ok)
        self.assertEqual(r._cfg["runtime_motion"]["dive"], 0.16)
        self.assertEqual(r._cfg["runtime_motion"]["yaw"], 20.0)
        self.assertEqual(cfg["dive"], 0.16)

    def test_tuning_gerak_di_luar_batas_ditolak(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        ok, reason = r.update_motion_config({"dive": 0.21})
        self.assertFalse(ok)
        self.assertIn("di luar batas", reason)
        self.assertNotIn("runtime_motion", r._cfg)

    def test_tuning_gerak_tidak_boleh_diubah_saat_fsm_jalan(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        r._thread = threading.current_thread()
        ok, reason = r.update_motion_config({"surge": 10})
        self.assertFalse(ok)
        self.assertIn("sedang berjalan", reason)


class TestWiringDiRovAgent(unittest.TestCase):
    """Menyeberangi batas file, seperti TestCallSiteCocokDenganDefinisi.

    Bug aslinya (22 Agu 2026): handler `control_mode` di rov_agent.py hanya
    menyetel string dan mencetaknya — tak ada FSM yang pernah dijalankan, jadi
    toggle Autonomous di GUI tidak menggerakkan wahana sama sekali. Tak ada
    test yang gagal karenanya, karena tak ada test yang memeriksa handler itu
    BERBUAT sesuatu.
    """

    def setUp(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "rov_agent.py")
        with open(path, encoding="utf-8") as f:
            self.src = f.read()
        self.tree = ast.parse(self.src)

    def _nama_yang_dipanggil(self):
        nama = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    nama.add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    nama.add(fn.attr)
        return nama

    def test_toggle_autonomous_menjalankan_dan_menghentikan_fsm(self):
        dipanggil = self._nama_yang_dipanggil()
        self.assertIn("setup_mission5_runner", dipanggil,
                      "runner FSM tak pernah di-setup di rov_agent.py")
        self.assertIn("start", dipanggil)
        self.assertIn("stop", dipanggil)
        self.assertIn("mission5_runner", self.src,
                      "handler control_mode tidak menyentuh mission5_runner — "
                      "toggle Autonomous tidak akan menggerakkan apa pun")

    def test_kill_switch_ada_di_jalur_pengiriman(self):
        # Operator HARUS selalu bisa merebut kendali dari FSM.
        self.assertIn("KILL_SWITCH_DEADZONE", self.src)
        self.assertIn("KILL-SWITCH", self.src)

    def test_axis_fsm_terpisah_dari_axis_operator(self):
        # Kalau FSM menulis ke dict `joystick` yang sama, perintahnya akan
        # tertimpa fail-safe idle / input operator secara acak.
        self.assertIn("fsm_axes", self.src)
        self.assertIn("fsm_axes_lock", self.src)

    def test_tuning_gerak_masuk_ke_runner(self):
        self.assertIn('name == "mission5_motion"', self.src)
        self.assertIn("update_motion_config", self.src)
        self.assertIn('"runtime_motion"', self.src)


if __name__ == "__main__":
    unittest.main()
