"""Unit test bridge Mission5FSM <-> rov_agent (tanpa hardware/pymavlink/opencv).

    python3 -m unittest test_rov_mission5_bridge -v
"""

import ast
import json
import math
import os
import tempfile
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

    def test_format_motion_lama_tetap_didukung(self):
        sink, depths = {}, []
        adapter = Mission5CommandAdapter(
            set_axis=lambda **kw: sink.update(kw),
            set_gripper=lambda c: sink.update(grip=c),
            arm=lambda _o: None,
            emergency_stop=lambda: None,
            set_depth_target=lambda d: depths.append(d) or True,
        )
        adapter.send_motion((30, -10, 90, 0.4, "close"), yaw_command=20)
        self.assertEqual((sink["surge"], sink["sway"], sink["yaw"], sink["heave"]),
                         (300, -100, 200, 0))
        self.assertEqual(depths, [0.4])
        self.assertTrue(sink["grip"])


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
    def test_run_log_otomatis_bernama_tanggal_dan_mencatat_config(self):
        with tempfile.TemporaryDirectory() as log_dir:
            r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                               config={"run_log_dir": log_dir}, log=lambda *_: None)
            runlog = r._new_runlog("M5_REDIVE", 90.0, 0.385)
            self.assertIsNotNone(runlog)
            runlog.close(state_akhir="ABORT")
            self.assertRegex(os.path.basename(runlog.path),
                             r"^run_\d{8}_\d{6}_\d{3}\.jsonl$")
            with open(runlog.path, encoding="utf-8") as f:
                events = [json.loads(line) for line in f]
            self.assertEqual(events[0]["kind"], "config")
            self.assertEqual(events[0]["marked_depth"], 0.385)
            self.assertFalse(events[0]["bench_qr_dock"])
            self.assertEqual(events[-1]["kind"], "end")
            self.assertEqual(r.telemetry()["run_log"], runlog.path)

    def test_start_tanpa_paket_autonomy_tidak_melempar(self):
        """Pi produksi belum tentu punya autonomy/+opencv. Import gagal harus
        berarti 'misi 5 tak tersedia', BUKAN agent mati dan kontrol manual
        ikut hilang."""
        pesan = []
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           config={"custom_motion_enabled": False},
                           log=pesan.append)
        r._import_autonomy = lambda: (_ for _ in ()).throw(
            ImportError("No module named 'cv2'"))

        self.assertFalse(r.start())          # tidak melempar
        self.assertFalse(r.is_running())
        self.assertTrue(any("TIDAK BISA START" in p for p in pesan), pesan)

    def test_stop_saat_belum_pernah_start_aman(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           config={"custom_motion_enabled": False},
                           log=lambda *_: None)
        r.stop()                              # tidak boleh melempar
        self.assertFalse(r.is_running())

    def test_state_name_none_saat_tidak_jalan(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           config={"custom_motion_enabled": False},
                           log=lambda *_: None)
        self.assertIsNone(r.state_name())

    def test_bench_qr_mematikan_custom_mode(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           config={"bench_qr_dock": True,
                                   "custom_motion_enabled": True},
                           log=lambda *_: None)
        self.assertFalse(r.custom_enabled)
        self.assertTrue(r.telemetry()["bench_qr_dock"])

    def test_m5_redive_ditolak_tanpa_mark(self):
        class State:
            M5_REDIVE = object()

        pesan = []
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           config={"custom_motion_enabled": False,
                                   "start_state": "M5_REDIVE"},
                           log=pesan.append)
        r._import_autonomy = lambda: (object, State, object, 0.04, (), 0, 0.025)
        r._apply_motion_config = lambda _module: None

        self.assertFalse(r.start())
        self.assertIn("MARK gantungan wajib", r.last_error)
        self.assertTrue(any("TIDAK BISA START" in p for p in pesan), pesan)


class TestAutonomousMotionConfig(unittest.TestCase):
    VALID = {
        "dive_mps": 0.16,
        "ascend_mps": 0.12,
        "surge_mps": 0.21,
        "yaw_dps": 20,
    }

    def test_tuning_gerak_valid_disimpan(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        cfg = r.set_motion_config(self.VALID)
        self.assertEqual(r.motion_config["dive_mps"], 0.16)
        self.assertEqual(r.motion_config["yaw_dps"], 20.0)
        self.assertEqual(cfg["dive_mps"], 0.16)

    def test_tuning_gerak_di_luar_batas_ditolak(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        bad = dict(self.VALID, dive_mps=0.21)
        with self.assertRaisesRegex(ValueError, "di luar batas"):
            r.set_motion_config(bad)

    def test_tuning_gerak_tidak_boleh_diubah_saat_fsm_jalan(self):
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        r._thread = threading.current_thread()
        with self.assertRaisesRegex(RuntimeError, "Mission 5 berjalan"):
            r.set_motion_config(self.VALID)

    def test_maju_fisik_juga_mengatur_kecepatan_pencarian(self):
        class Module:
            pass

        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        r.set_motion_config(self.VALID)
        module = Module()
        r._apply_motion_config(module)

        self.assertEqual(module.SURGE_SPEED, 19)   # 0,21 / 1,11 × 100
        self.assertEqual(module.SEARCH_SPEED, 19)  # M5_SEARCH mengikuti input Maju

    def test_maju_nol_menonaktifkan_search_dan_scan_creep(self):
        class Module:
            pass

        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           log=lambda *_: None)
        r.set_motion_config(dict(self.VALID, surge_mps=0))
        module = Module()
        r._apply_motion_config(module)

        self.assertEqual(module.SEARCH_SPEED, 0)
        self.assertEqual(module.SCAN_CREEP_MAX_SPEED, 0)


class TestCustomCaseMotion(unittest.TestCase):
    def test_custom_case_bisa_diedit_tanpa_import_fsm_besar(self):
        sink, history = {}, []
        cases = [{"name": "COBA_MAJU", "duration_ms": 30,
                  "motion": (12, 0, 90, 0.4, "hold")}]
        adapter = Mission5CommandAdapter(
            set_axis=lambda **kw: (sink.update(kw), history.append(dict(kw))),
            set_gripper=lambda c: sink.update(grip=c),
            arm=lambda _o: None,
            emergency_stop=lambda: None,
            set_alt_hold=lambda: True,
            set_depth_target=lambda _d: True,
        )
        r = Mission5Runner(adapter, Mission5TelemetryAdapter(lambda: {"heading": 90}),
                           config={"custom_motion_enabled": True,
                                   "custom_cases": cases,
                                   "run_log_dir": tempfile.gettempdir()},
                           log=lambda *_: None)
        r._cfg["read_mark"] = lambda: (90.0, 0.5)
        chained = []
        r._start_fsm = lambda *a, **kw: chained.append((a, kw)) or True

        self.assertTrue(r.start())
        r._thread.join(timeout=1)
        self.assertTrue(any(item["surge"] == 120 for item in history), history)
        self.assertEqual(sink, {"surge": 0, "sway": 0, "yaw": 0, "heave": 0})
        self.assertTrue(r.telemetry()["custom_mode"])
        # CASE tuntas -> serah terima ke FSM untuk langkah 3-8
        self.assertEqual(len(chained), 1, "CASE COMPLETE harus merantai ke FSM")
        self.assertEqual(chained[0][0][0], "M5_YOLO_SEARCH")
        # heading CASE 90 ditulis RELATIF thd MARK 90 -> absolut 180
        self.assertAlmostEqual(chained[0][1]["heading_hold"], 180.0)

    def test_custom_case_wajib_mark(self):
        """heading CASE adalah offset dari MARK — tanpa MARK ia tak punya arti."""
        cases = [{"name": "X", "duration_ms": 20, "motion": (0, 0, 0, 0.4, "hold")}]
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {"heading": 0}),
                           config={"custom_motion_enabled": True, "custom_cases": cases,
                                   "read_mark": lambda: (None, None)},
                           log=lambda *_: None)
        self.assertFalse(r.start())
        self.assertIn("MARK", r.last_error)

    def test_custom_case_dibatalkan_tidak_merantai_ke_fsm(self):
        """Kill-switch yang menyala di batas serah terima tak boleh memulai FSM."""
        cases = [{"name": "X", "duration_ms": 5000, "motion": (0, 0, 0, 0.4, "hold")}]
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {"heading": 0}),
                           config={"custom_motion_enabled": True, "custom_cases": cases,
                                   "read_mark": lambda: (0.0, 0.5)},
                           log=lambda *_: None)
        chained = []
        r._start_fsm = lambda *a, **kw: chained.append(a) or True
        self.assertTrue(r.start())
        r.stop()
        self.assertEqual(chained, [], "dibatalkan -> jangan mulai FSM")

    def test_custom_case_tidak_aman_ditolak(self):
        bad = [{"name": "TERLALU_BESAR", "duration_ms": 100,
                "motion": (101, 0, 0, 0.4, "hold")}]
        r = Mission5Runner(_adapter({}), Mission5TelemetryAdapter(lambda: {}),
                           config={"custom_motion_enabled": True,
                                   "custom_cases": bad}, log=lambda *_: None)
        self.assertFalse(r.start())
        self.assertIn("surge/sway", r.last_error)


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
        self.assertIn("set_motion_config", self.src)
        self.assertIn('"mission5_motion_config"', self.src)

    def test_produksi_mulai_dari_alur_sisi_kiri(self):
        # Langkah 1-2 = CASE MOTION; FSM mengambil alih dari langkah 3.
        self.assertIn('os.environ.get("M5_START_STATE", "M5_YOLO_SEARCH")', self.src)

    def test_hook_map_produksi_opt_in(self):
        self.assertIn('"hook_map": os.environ.get("M5_HOOK_MAP") or None', self.src)
        with open("rov_mission5_bridge.py", encoding="utf-8") as fh:
            bridge = fh.read()
        self.assertIn('hook_map_file=cfg.get("hook_map")', bridge)
        self.assertIn('data["hook_map_enabled"] = bool(self._cfg.get("hook_map"))', bridge)

        with open("public/js/pages/mission.js", encoding="utf-8") as fh:
            mission_ui = fh.read()
        self.assertIn('loc.status === "ok"', mission_ui)
        self.assertIn('newX = this.hookPose.x', mission_ui)
        self.assertIn('else if (this.hookMapEnabled)', mission_ui)

    def test_qr_docking_memakai_kamera_wall_dekat_gripper(self):
        with open("rov_mission5_bridge.py", encoding="utf-8") as fh:
            bridge = fh.read()
        # True = CASE (langkah 1-2) lalu rantai ke FSM (langkah 3-8) — jalur lomba.
        self.assertIn('CUSTOM_MOTION_ENABLED = True', bridge)
        self.assertIn('qr_url=cfg.get("wall_url")', bridge)
        self.assertIn('hook_url=None', bridge)
        self.assertIn('calib_file=cfg.get("calib_wall")', bridge)

    def test_yolo_laptop_diteruskan_ke_pi_dengan_watchdog(self):
        with open("server/server.js", encoding="utf-8") as fh:
            server = fh.read()
        with open("rov_mission5_bridge.py", encoding="utf-8") as fh:
            bridge = fh.read()
        with open("autonomy/tools/hook_vision_worker.py", encoding="utf-8") as fh:
            worker = fh.read()
        self.assertIn('name: "hook_vision"', server)
        self.assertIn('process.platform === "win32" ? "python" : "python3"', server)
        self.assertIn('elif name == "hook_vision"', self.src)
        self.assertIn('time.monotonic() - latest_hook_vision_received <= 1.0', self.src)
        self.assertIn("'frame_w': detection.get('frame_w')", worker)
        self.assertIn('hook_enabled=False', bridge,
                      "Pi tidak boleh menjalankan detector hook lokal saat YOLO berasal dari laptop")
        self.assertIn('wall_cnn=False', bridge,
                      "Pi tidak boleh memuat fallback CNN saat vision berat dipindah ke laptop")

    def test_validasi_yolo_menolak_bbox_di_luar_frame(self):
        node = next(n for n in self.tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "_validate_hook_vision")
        scope = {"math": math}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "rov_agent.py", "exec"), scope)
        validate = scope["_validate_hook_vision"]
        valid = {"status": "relative_only", "method": "yolov8", "confidence": 0.9,
                 "bbox": [10, 20, 100, 80], "frame_w": 640, "frame_h": 480}
        self.assertIsNotNone(validate(valid))
        self.assertIsNone(validate(dict(valid, bbox=[600, 20, 100, 80])))


if __name__ == "__main__":
    unittest.main()
