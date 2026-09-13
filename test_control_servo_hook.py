"""Tes helper servo visual dan urutan AUTO_STEPS berdasarkan durasi.

Helper visi tetap diuji terpisah; counter aktif memakai enam tuple literal
serta pengaman ARM, telemetri, dan otoritas operator.

    python3 -m unittest test_control_servo_hook -v
"""

import ast
import importlib.util
import os
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.abspath(__file__))

# Tuning uji: sengaja BUKAN nilai produksi di server/control_config.yaml —
# test tidak boleh ikut gagal hanya karena angka kolam berubah.
CONFIG_UJI = """
servo_hook:
  source: {source}
  grab_roi_norm: [0.4, 0.4, 0.6, 0.6]
  invert_sway: {invert}
  kp_sway: 45.0
  kd_sway: 0.0
  d_lpf: 0.4
  deadband_norm: 0.02
  slew: 120.0
  max_speed: 35.0
  center_tol_norm: 0.08
  centered_ticks: {ticks}
  max_age: 1.0
  close_area_frac_decoded: 0.10
  close_area_frac_region: 0.20
mission:
  settle_s: 3.0
  depth_m: 1.0
  depth_wait_s: 3.0
  depth_tol_m: 0.1
  search_timeout_s: 8.0
  search_yaw: 100
  search_surge: -100
  search_sweep_s: 2.0
  approach_surge: -100
  servo_timeout_s: 3.0
  lost_timeout_s: 10.0
  gripper_hold_s: 1.5
  rise_m: 0.5
  rise_wait_s: 3.0
  telemetry_max_age: 1.0
"""

FRAME_W = 640.0


def _load_control_main():
    path = os.path.join(ROOT, "server", "control_main.py")
    spec = importlib.util.spec_from_file_location("control_main_servo_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # set_mode membaca AUTO_STEPS dari __file__; arahkan ke tabel uji.
    module.__file__ = os.path.join(ROOT, "auto_steps_fixture.py")
    return module


class _ServoBase(unittest.TestCase):

    invert = "false"
    ticks = 3
    source = "qr"

    def setUp(self):
        self.cm = _load_control_main()
        self.addCleanup(self.cm.out_sock.close)
        self.sent = []
        self.cm.send_packet = self.sent.append

        self.tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8")
        self.tmp.write(CONFIG_UJI.format(
            invert=self.invert, ticks=self.ticks, source=self.source))
        self.tmp.close()
        self.addCleanup(os.unlink, self.tmp.name)

        self.cm.SERVO_CONFIG_PATH = self.tmp.name
        self.cm.load_servo_config()
        self.cm.servo_reset()
        self.cm.vehicle_state = {"depth": 1.0, "armed": True}
        self.cm.last_vehicle_time = time.monotonic()
        self.assertIsNotNone(self.cm.servo_cfg, "config uji gagal dimuat")

    def lihat_hook(self, ex, umur=0.0):
        """Taruh deteksi dgn error ternormalisasi `ex` (+ = hook di kanan)."""
        center_x = FRAME_W / 2.0 + ex * (FRAME_W / 2.0)
        self.cm.latest_hook = (center_x, FRAME_W)
        self.cm.last_hook_time = time.monotonic() - umur
        self.cm.latest_qr_metric = ("qr_vision", 0.15)
        self.cm.latest_qr_xy_norm = (center_x / FRAME_W, 0.5)
        self.cm.last_vision_receipt += 1.0

    def tick(self, surge_step=-500):
        return self.cm.servo_step(surge_step)


class BentukHookXy(_ServoBase):
    """Servo membaca hook_xy dengan bentuk yang BENAR-BENAR sampai dari Pi.

    _validate_hook_vision() di rov_agent.py sengaja hanya meneruskan `bbox` dan
    MEMBUANG `center` milik worker. Versi pertama listener ini membaca `center`
    — lolos semua test buatan sendiri, dan akan diam selamanya di kolam. Test di
    bawah mengambil daftar field langsung dari rov_agent.py supaya kalau salah
    satu sisi berubah, yang gagal adalah test, bukan trial.
    """

    def _field_hook_xy(self):
        """Kunci dict yang dikembalikan _validate_hook_vision(), dari sumbernya."""
        with open(os.path.join(ROOT, "rov_agent.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())

        fungsi = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_validate_hook_vision")

        # Beberapa `return {...}`: yang paling lengkap adalah jalur sukses,
        # sisanya penolakan / kabar sehat worker ({"status": ...}).
        dicts = [{k.value for k in n.value.keys if isinstance(k, ast.Constant)}
                 for n in ast.walk(fungsi)
                 if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
        self.assertTrue(dicts, "_validate_hook_vision tidak lagi mengembalikan dict")

        return max(dicts, key=len)

    def test_center_memang_tidak_ada_di_telemetry(self):
        field = self._field_hook_xy()

        self.assertIn("bbox", field)
        self.assertNotIn("center", field,
                         "hook_xy kini membawa center — parser boleh disederhanakan")

    def test_parser_menerima_record_hook_xy_asli(self):
        # Bentuk persis yang diteruskan validator, tanpa satu field tambahan.
        record = {k: None for k in self._field_hook_xy()}
        record.update(bbox=[679.0, 373.0, 23.0, 49.0], frame_w=1280, frame_h=720,
                      confidence=0.69, method="yolov8", keypoints=None)

        acuan = self.cm.hook_center_from_telemetry(record)

        self.assertIsNotNone(acuan, "listener menolak hook_xy yang sah")
        self.assertAlmostEqual(acuan[0], 690.5)      # x + w/2
        self.assertEqual(acuan[1], 1280)

    def test_keypoints_kosong_tetap_diterima(self):
        # best_new.pt adalah model DETECT-ONLY: keypoints selalu None. Kalau
        # servo menuntut keypoint, bobot itu tak akan pernah bisa dipakai.
        acuan = self.cm.hook_center_from_telemetry(
            {"bbox": [100, 10, 40, 40], "frame_w": 640, "keypoints": None})

        self.assertIsNotNone(acuan)

    def test_record_cacat_dibuang_bukan_disimpan(self):
        for cacat in ({}, None, {"bbox": [1, 2, 3]}, {"bbox": [1, 2, 0, 4], "frame_w": 640},
                      {"bbox": [1, 2, 3, 4], "frame_w": 0},
                      {"bbox": [1, 2, 3, 4]}, {"frame_w": 640}):
            with self.subTest(cacat=cacat):
                self.assertIsNone(self.cm.hook_center_from_telemetry(cacat))


class SumberAcuan(_ServoBase):
    """CASE 4 menyamakan diri ke KOTAK QR (best_new @ CAM BOTTOM), bukan hook.

    Dua sumber diteruskan server.js ke port yang sama. Menyimpan keduanya ke
    satu slot berarti servo diam-diam mengikuti kamera yang salah — dan itu
    tidak akan terlihat sebagai error, cuma sebagai koreksi yang aneh.
    """

    def test_qr_vision_membawa_center_langsung(self):
        # _validate_qr_vision MENERUSKAN center (beda dgn hook_xy yg cuma bbox).
        acuan = self.cm.qr_center_from_telemetry(
            {"center": [400.0, 300.0], "frame_w": 640, "frame_h": 480})

        self.assertEqual(acuan, (400.0, 640.0))

    def test_qr_record_cacat_dibuang(self):
        for cacat in (None, {}, {"center": [1]}, {"center": [1, 2]},
                      {"frame_w": 640}, {"center": [1, 2], "frame_w": 0}):
            with self.subTest(cacat=cacat):
                self.assertIsNone(self.cm.qr_center_from_telemetry(cacat))

    def test_default_config_produksi_memakai_qr(self):
        # Yang dikirim ke kolam, bukan config uji.
        import yaml
        with open(os.path.join(ROOT, "server", "control_config.yaml"),
                  encoding="utf-8") as f:
            produksi = yaml.safe_load(f)["servo_hook"]

        self.assertEqual(produksi["source"], "qr")

    def test_source_tak_dikenal_mematikan_servo(self):
        # Salah ketik di yaml tidak boleh diam-diam jatuh ke sumber lain.
        with open(self.tmp.name, "w", encoding="utf-8") as f:
            f.write(CONFIG_UJI.format(invert="false", ticks=3, source="hokk"))

        self.cm.load_servo_config()

        self.assertIsNone(self.cm.servo_cfg)


class RegionTanpaDecode(_ServoBase):
    """Kotak QR yang terdeteksi tapi GAGAL di-decode tetap menyetir sway.

    Sebelum 8 Sep worker membuang region tanpa decode ("hasilnya sama saja
    dengan tidak ada deteksi"). Benar untuk Mission 5 — tanpa teks QR ia tak
    boleh bergerak — tapi mematikan servo lateral justru saat air paling keruh,
    karena decode adalah bagian yang paling sering gagal. Region kini dilaporkan
    di kanal terpisah `qr_region`; kontrak `qr_vision` tidak berubah.
    """

    def test_source_qr_menerima_kedua_kanal(self):
        tipe, _parser = self.cm.VISION_PARSERS["qr"]

        self.assertEqual(tipe, {"qr_vision", "qr_region"})

    def test_region_menghasilkan_sway(self):
        # Amplop qr_region: center + frame_w, TANPA data/payload/pose.
        acuan = self.cm.qr_center_from_telemetry(
            {"method": "yolo_qr_region", "status": "region",
             "center": [480.0, 300.0], "area": 1127.0, "confidence": 0.68,
             "frame_w": 640, "frame_h": 480})

        self.assertIsNotNone(acuan)
        self.cm.latest_hook, self.cm.last_hook_time = acuan, time.monotonic()
        _surge, sway, _ = self.tick()

        self.assertNotEqual(sway, 0, "region tanpa decode tidak menggerakkan sway")

    def test_worker_mengirim_region_di_kanal_terpisah(self):
        # Kanal salah = record mendarat di validator qr_vision dan dibuang
        # diam-diam; tidak ada error, cuma servo yang tidak pernah bergerak.
        with open(os.path.join(ROOT, "autonomy", "tools", "qr_vision_worker.py"),
                  encoding="utf-8") as f:
            worker = ast.parse(f.read())

        panggilan = [
            n for n in ast.walk(worker)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "emit"
            and any(kw.arg == "channel" for kw in n.keywords)]

        self.assertEqual(len(panggilan), 1, "emit(channel=) region hilang/ganda")
        kanal = next(kw.value.value for kw in panggilan[0].keywords
                     if kw.arg == "channel")
        self.assertEqual(kanal, "qr_region")

    def test_kontrak_qr_vision_tidak_ikut_berubah(self):
        # Mission 5 bergantung pada qr_vision MEWAJIBKAN teks QR. Kalau syarat
        # itu longgar, FSM bisa docking ke kotak yang isinya tak pernah dibaca.
        with open(os.path.join(ROOT, "rov_agent.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())

        fungsi = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == "_validate_qr_vision")
        sumber = ast.dump(fungsi)

        self.assertIn("data_empty_or_too_long", sumber,
                      "_validate_qr_vision tidak lagi mewajibkan teks QR")
        self.assertIn("'yolo_qr'", sumber)


class Sway(_ServoBase):

    def test_gripper_offset_changes_servo_reference(self):
        self.cm.servo_cfg['target_x_norm'] = 0.65
        self.lihat_hook(0.30)
        surge, sway, centered = self.tick()
        self.assertEqual(sway, 0)
        self.assertNotEqual(surge, 0)
        self.cm.servo_reset()
        self.lihat_hook(0.0)
        surge, sway, centered = self.tick()
        self.assertLess(sway, 0)
        self.assertEqual(surge, 0)

    def test_invalid_gripper_offset_stops_config(self):
        import yaml
        for value in (float('nan'), -0.1, 1.1):
            cfg = yaml.safe_load(CONFIG_UJI.format(source='qr', invert='false', ticks=3))
            cfg['servo_hook']['target_x_norm'] = value
            with open(self.tmp.name, 'w') as f:
                yaml.safe_dump(cfg, f)
            self.cm.load_servo_config()
            self.assertIsNone(self.cm.servo_cfg)

    def test_hook_di_kanan_menghasilkan_sway_positif(self):
        self.lihat_hook(0.5)
        _surge, sway, _ = self.tick()

        self.assertGreater(sway, 0)

    def test_deadband_meredam_geser_centroid_kecil(self):
        # 0.01 < deadband 0.02 — riak, bukan perintah.
        self.lihat_hook(0.01)
        _surge, sway, _ = self.tick()

        self.assertEqual(sway, 0)

    def test_slew_membatasi_lonjakan_command(self):
        # Error maksimum sekalipun tidak boleh melompat ke cap dalam satu tick:
        # sentakan sway memiringkan ROV, dan kemiringan menggeser kamera.
        self.lihat_hook(1.0)
        _surge, sway_pertama, _ = self.tick()

        self.assertLess(abs(sway_pertama), self.cm.servo_cfg["max_speed"] * 10)

    def test_tanda_bisa_dibalik_dari_config(self):
        # Arah sway belum diverifikasi di hardware (bdk. surge yg sengaja
        # dibalik di joystick.py). Kalau terbalik, ROV menjauh makin cepat.
        self.lihat_hook(0.5)
        _s, sway_normal, _ = self.tick()

        self.invert = "true"
        self.setUp()
        self.lihat_hook(0.5)
        _s, sway_terbalik, _ = self.tick()

        self.assertEqual(sway_normal, -sway_terbalik)


class GateUmurDeteksi(_ServoBase):

    def test_deteksi_basi_menolkan_sway(self):
        self.lihat_hook(0.5, umur=self.cm.servo_cfg["max_age"] + 0.1)
        _surge, sway, _ = self.tick()

        self.assertEqual(sway, 0)

    def test_deteksi_hilang_tidak_mengulang_error_terakhir(self):
        self.lihat_hook(0.5)
        _s, sway_hidup, _ = self.tick()
        self.assertNotEqual(sway_hidup, 0)

        # Kamera mati: nilai terakhir membeku di dict, tapi tak ada satu pun
        # sensor yang tahu ROV sudah bergeser berapa sejak itu.
        self.cm.last_hook_time = time.monotonic() - 5.0
        _s, sway_basi, _ = self.tick()

        self.assertEqual(sway_basi, 0)

    def test_tanpa_deteksi_sama_sekali_sway_nol(self):
        _surge, sway, di_tengah = self.tick()

        self.assertEqual(sway, 0)
        self.assertFalse(di_tengah)


class GateSurge(_ServoBase):

    def test_surge_tertutup_selagi_menyamping(self):
        self.lihat_hook(0.5)          # jauh di luar tol 0.08

        for _ in range(20):
            surge, _sway, _ = self.tick()

        self.assertEqual(surge, 0, "ROV maju sambil masih menyamping")

    def test_surge_terbuka_setelah_di_tengah(self):
        self.lihat_hook(0.0)
        self.cm.latest_qr_metric = ('qr_vision', 0.05)  # centered but still far

        for _ in range(20):
            surge, _sway, _ = self.tick()

        self.assertLess(surge, 0, "surge tidak pernah jalan walau sudah center")

    def test_surge_naik_bertahap_bukan_menyentak(self):
        self.lihat_hook(0.0)
        surge_pertama, _sway, _ = self.tick()

        self.assertGreater(surge_pertama, -500)

    def test_kehilangan_deteksi_menutup_surge_lagi(self):
        """Acuan basi menghentikan seluruh gerak seketika."""
        self.lihat_hook(0.0)
        self.cm.latest_qr_metric = ('qr_vision', 0.05)
        for _ in range(20):
            self.tick()

        surge_awal, _sway, _ = self.tick()
        self.assertLess(surge_awal, 0)

        self.cm.last_hook_time = time.monotonic() - 5.0

        jejak = [self.tick()[0] for _ in range(60)]

        self.assertEqual(jejak[0], 0, "surge wajib nol seketika")
        self.assertEqual(jejak[-1], 0, "surge tidak pernah sampai nol")


class UrutanCounter(_ServoBase):
    def setUp(self):
        super().setUp()
        self.now = 100.0
        clock = patch.object(self.cm.time, "monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.cm.set_mode("autonomous")
        self.sent.clear()

    def tick(self, dt=0.0, depth=0.0, armed=True):
        self.now += dt
        self.cm.accept_vision_message({"type": "vehicle_state",
                                      "value": {"depth": depth, "armed": armed, "heading": 0.0, "control_mode": "autonomous"}})
        self.cm.autonomous_control()

    def commands(self, name):
        return [p["value"] for p in self.sent
                if p["type"] == "command" and p["name"] == name]

    def assert_neutral(self):
        self.assertEqual([self.sent[-1][a] for a in self.cm.AXES], [0] * 4)

    def test_tabel_misi_asli_valid(self):
        """Tabel di control_main.py sendiri harus lolos validator, atau
        autonomous menolak mulai di kolam."""
        self.cm.read_auto_steps(
            os.path.join(ROOT, "server", "control_main.py"), .5)

    def test_reload_literal_dan_depth_gui(self):
        # File ditulis lalu ditutup tiap kali: Windows tak bisa membuka ulang
        # NamedTemporaryFile yang masih terbuka.
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "steps.py")

            def read(literal, depth):
                with open(path, "w", encoding="utf-8") as source:
                    source.write("AUTO_STEPS = " + literal)
                return self.cm.read_auto_steps(path, depth)

            self.assertEqual(read("[(2, -100, 0, 0, 0, None, 1.0)]", .5)[0],
                             (2, -100, 0, 0, 0, None, 1.0))
            self.assertEqual(read("[(4, -300, 0, 0, 0, None, None)]", .7)[0],
                             (4, -300, 0, 0, 0, None, .7))
            self.assertEqual(read("[(2, 0, 0, 0, 0, None, 0.0)]", .7)[0][6], 0.0)
            for invalid in ["[(1, 2000, 0, 0, 0, None, None)]", "[]", "make_steps()"]:
                with self.assertRaises(ValueError):
                    read(invalid, .5)

    def test_reload_depth_auto(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "steps.py")
            for value in ["1.0", "0.7", "-1", "True", "float('nan')"]:
                with open(path, "w", encoding="utf-8") as source:
                    source.write("depth_auto = " + value + "\n"
                                 "AUTO_STEPS = [(1, 0, 0, 0, 0, None, depth_auto)]\n")
                if value in ["1.0", "0.7"]:
                    self.assertEqual(self.cm.read_auto_steps(path, .5)[0][6], float(value))
                else:
                    with self.assertRaises(ValueError):
                        self.cm.read_auto_steps(path, .5)

    def test_depth_kode_prioritas_dan_none_kembali_ke_gui(self):
        self.cm.set_mode("manual")
        self.cm.set_mode("autonomous", .5)
        self.sent.clear()
        self.assertEqual([step[6] for step in self.cm.AUTO_STEPS],
                         [.5, .5, .5, 1.0, .5, .5])
        for duration in [3, 2, 1, 2, 3, 1]:
            self.tick()
            self.tick(duration + .01)
        self.assertEqual(self.commands("depth_apply"), [.5, 1.0, .5])

    def test_yaw_target_derajat_dan_wrap(self):
        self.cm.AUTO_STEPS[0] = (3, 0, 0, 10, 0, None, None)
        self.tick()
        self.assertEqual(self.sent[-1]["yaw"], 60)
        self.cm.vehicle_state["heading"] = 350
        self.cm.autonomous_control()
        self.assertEqual(self.sent[-1]["yaw"], 120)
        self.cm.vehicle_state["heading"] = 10
        self.cm.autonomous_control()
        self.assertEqual(self.sent[-1]["yaw"], 0)
        self.cm.vehicle_state["heading"] = float("nan")
        self.cm.autonomous_control()
        self.assertTrue(self.cm.auto_finished)
        self.assert_neutral()

    def test_menunggu_mode_pi_sebelum_koreksi_heading(self):
        self.cm.vehicle_state = {"armed": True, "depth": .5,
                                 "heading": 180, "control_mode": "manual"}
        self.cm.last_vehicle_time = self.now
        self.cm.autonomous_control()
        self.assertFalse(self.cm.auto_finished)
        self.assert_neutral()
        self.assertEqual(self.commands("depth_apply"), [])

    def test_enam_langkah_persis_dan_tidak_ditimpa_reset(self):
        expected = [(3.0, 0, 0, 0, 0, None, None),
                    (2.0, 0, 0, 0, 0, None, None),
                    (1.0, 0, 0, 0, 0, None, None),
                    (2.0, 0, 0, 0, 0, None, 1.0),
                    (3.0, -500, 0, 0, 0, None, None),
                    (1.0, 0, 0, 0, 0, None, None)]
        self.assertEqual(self.cm.AUTO_STEPS, expected)
        self.cm.autonomous_reset()
        self.assertEqual(self.cm.AUTO_STEPS, expected)

    def test_urutan_durasi_depth_sekali_surge_tanpa_qr_lalu_netral(self):
        for index, duration in enumerate([3, 2, 1, 2, 3, 1]):
            self.assertEqual(self.cm.auto_index, index)
            self.tick()
            frame = self.sent[-1]
            self.assertEqual(frame["src"], "fsm")
            self.assertEqual(frame["surge"], -500 if index == 4 else 0)
            self.tick(duration - 0.01)
            self.assertEqual(self.cm.auto_index, index)
            self.assertFalse(self.cm.auto_finished)
            self.tick(0.02)
            self.assert_neutral()
        self.assertTrue(self.cm.auto_finished)
        self.assertEqual(self.commands("depth_apply"), [1.0])
        self.assertEqual(self.commands("gripper"), [])
        self.assertEqual(self.commands("arm"), [])
        self.tick()
        self.assert_neutral()

    def test_disarm_tanpa_depth_menunggu_lalu_settle_setelah_arm(self):
        self.tick(10, depth=None, armed=False)
        self.assertFalse(self.cm.auto_finished)
        self.assert_neutral()
        self.tick(0.01)
        self.assertEqual(self.cm.auto_index, 0)
        self.tick(3)
        self.assertEqual(self.cm.auto_index, 1)

    def test_arm_tanpa_depth_berhenti(self):
        self.tick(depth=None)
        self.assertTrue(self.cm.auto_finished)
        self.assert_neutral()

    def test_telemetry_basi_saat_gerak_berhenti(self):
        self.cm.enter_case(4)
        self.tick()
        self.now += 2
        self.cm.autonomous_control()
        self.assertTrue(self.cm.auto_finished)
        self.assert_neutral()

    def test_disarm_saat_gerak_berhenti(self):
        self.cm.enter_case(4)
        self.tick(armed=False)
        self.assertTrue(self.cm.auto_finished)
        self.assert_neutral()

    def test_gripper_non_motion_hanya_sekali(self):
        self.cm.AUTO_STEPS = [(1.0, 0, 0, 0, 0, 1580, None)]
        self.tick()
        self.tick(0.5)
        self.assertEqual(self.commands("gripper_pwm"), [1580])


class GrabAreaXY(_ServoBase):
    def test_stop_surge_while_collecting_grab_frames(self):
        self.frame()
        self.assertEqual(self.tick()[0], 0)

    def test_too_close_never_grabs_or_advances(self):
        self.cm.servo_cfg['max_grab_area_frac'] = .12
        for _ in range(5):
            self.assertFalse(self.frame())
            self.assertEqual(self.tick()[0], 0)

    def frame(self, x=.5, y=.5):
        self.lihat_hook(2*x-1)
        self.cm.latest_qr_xy_norm = (x, y)
        return self.tick()[2]

    def test_centered_x_but_wrong_y_never_grabs(self):
        for _ in range(10):
            self.assertFalse(self.frame(y=.2))
        self.assertEqual(self.cm.servo_hits, 0)

    def test_new_frames_inside_area_grab(self):
        self.assertFalse(self.frame())
        self.assertFalse(self.frame())
        self.assertTrue(self.frame())

    def test_cached_frame_does_not_build_streak(self):
        self.frame()
        for _ in range(10):
            self.assertFalse(self.tick()[2])
        self.assertEqual(self.cm.servo_hits, 1)

    def test_exit_and_stale_detection_reset_streak(self):
        self.frame()
        self.frame()
        self.assertFalse(self.frame(y=.8))
        self.assertEqual(self.cm.servo_hits, 0)
        self.frame()
        self.cm.last_hook_time -= 2
        self.assertFalse(self.tick()[2])
        self.assertEqual(self.cm.servo_hits, 0)

    def test_uncalibrated_or_missing_xy_blocks_grab(self):
        self.cm.servo_cfg['grab_roi_norm'] = None
        for _ in range(5):
            self.assertFalse(self.frame())
        self.cm.servo_cfg['grab_roi_norm'] = (.4,.4,.6,.6)
        self.cm.latest_qr_xy_norm = None
        self.assertFalse(self.tick()[2])

    def test_invalid_roi_disables_config(self):
        import yaml
        for roi in ([0,0,1], [0,0,float('nan'),1], [0,.8,1,.2], [-1,0,1,1]):
            cfg = yaml.safe_load(CONFIG_UJI.format(source='qr', invert='false', ticks=3))
            cfg['servo_hook']['grab_roi_norm'] = roi
            with open(self.tmp.name, 'w') as f:
                yaml.safe_dump(cfg, f)
            self.cm.load_servo_config()
            self.assertIsNone(self.cm.servo_cfg)


class MetadataPi(unittest.TestCase):
    def test_cache_telem_tidak_memperbarui_waktu_penerimaan_vision(self):
        # Jalankan blok overlay/metadata asli tanpa mengimpor koneksi MAVLink.
        from types import SimpleNamespace
        with open(os.path.join(ROOT, "rov_agent.py")) as f:
            tree = ast.parse(f.read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "send_telemetry")
        start = next(i for i, n in enumerate(fn.body)
                     if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Subscript)
                     and isinstance(n.targets[0].slice, ast.Constant)
                     and n.targets[0].slice.value == "hook_xy")
        end = next(i for i, n in enumerate(fn.body) if isinstance(n, ast.Expr)
                   and isinstance(n.value, ast.Call)
                   and getattr(n.value.func, "id", None) == "send_to_gui")
        code = compile(ast.Module(body=fn.body[start:end], type_ignores=[]), "overlay", "exec")
        now = [100.0]
        env = {"state": {}, "time": SimpleNamespace(monotonic=lambda: now[0]),
               "latest_hook_vision": {"bbox": [1, 2, 3, 4]},
               "latest_qr_vision": {"data": "QR1"}, "latest_qr_region": {"area": 100},
               "latest_hook_vision_received": 98.0,
               "latest_qr_vision_received": 99.0, "latest_qr_region_received": 99.5}
        exec(code, env)
        self.assertEqual(env["state"]["vision_receipts"]["qr_region"],
                         {"received": 99.5, "age": 0.5})
        now[0] = 103.0
        exec(code, env)
        self.assertEqual(env["state"]["vision_receipts"]["qr_region"],
                         {"received": 99.5, "age": 3.5})
        self.assertEqual(env["state"]["qr_vision"], {"data": "QR1"})


if __name__ == "__main__":
    unittest.main()
