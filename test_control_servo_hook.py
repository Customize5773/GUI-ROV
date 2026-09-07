"""Servo hook CASE 4 di server/control_main.py.

Deteksi hook (YOLO, berjalan di Pi) menyetir sway saat CASE 4: sway dihitung
tiap tick 20 Hz dari posisi hook di frame, surge di-gate sampai hook cukup di
tengah, dan durasi 3 detik berlaku sebagai TIMEOUT.

Yang dijaga di sini adalah keputusan-keputusan yang mahal kalau salah di kolam:

  • deadband      — riak menggeser centroid beberapa piksel; tanpa ini thruster
                    sway mematuk-matuk di dekat target
  • gate umur     — deteksi basi = sway NOL. ROV ini tidak punya sensor posisi
                    lateral sama sekali; mengulang error terakhir berarti
                    menggerakkan wahana secara buta
  • gate surge    — maju sambil masih menyamping membuat ROV melewati hook
  • timeout       — hook tak pernah terlihat tidak boleh menggantungkan FSM
  • tanda sway    — kalau terbalik, ROV MENJAUH makin cepat dan gejalanya mirip
                    kp kebesaran; harus bisa dibalik dari config

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
  lost_timeout_s: 2.0
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
        for _ in range(20):
            self.tick()

        surge_awal, _sway, _ = self.tick()
        self.assertLess(surge_awal, 0)

        self.cm.last_hook_time = time.monotonic() - 5.0

        jejak = [self.tick()[0] for _ in range(60)]

        self.assertEqual(jejak[0], 0, "surge wajib nol seketika")
        self.assertEqual(jejak[-1], 0, "surge tidak pernah sampai nol")


class AlurCase(_ServoBase):
    """CASE 4 di dalam FSM: selesai lebih awal, timeout, dan tidak menggantung."""

    def setUp(self):
        super().setUp()
        self.cm.set_mode(self.cm.MODE_AUTONOMOUS)
        # Config uji dipasang ulang: set_mode -> autonomous_reset memuat ulang
        # yaml, dan pada titik itu ia masih menunjuk file produksi.
        self.cm.SERVO_CONFIG_PATH = self.tmp.name
        self.cm.load_servo_config()
        self.cm.servo_reset()

        self.case_servo = next(
            i for i, step in enumerate(self.cm.AUTO_STEPS) if step[7])
        self.cm.auto_index = self.case_servo
        self.cm.auto_step_start = time.monotonic()
        self.sent.clear()

    def test_hook_di_tengah_menyelesaikan_case_sebelum_timeout(self):
        self.lihat_hook(0.0)

        for _ in range(self.ticks + 2):
            self.cm.autonomous_control()
            self.lihat_hook(0.0)

        self.assertEqual(self.cm.auto_index, self.case_servo + 1)

    def test_hook_tak_pernah_terlihat_mengakhiri_urutan(self):
        durasi = self.cm.AUTO_STEPS[self.case_servo][0]
        self.cm.autonomous_control()
        self.assertEqual(self.cm.auto_index, self.case_servo,
                         "CASE tidak boleh selesai sebelum timeout")

        self.cm.auto_step_start = time.monotonic() - (durasi + 0.1)
        self.cm.autonomous_control()

        # Selesai aman, tidak maju ke gripper.
        self.assertEqual(self.cm.auto_index, self.case_servo)
        self.assertTrue(self.cm.auto_finished)
        self.assertFalse(self.cm.servo_seen_hook)

    def test_case_servo_tetap_mengirim_frame_bertag_fsm(self):
        # Perbaikan otoritas axis 7 Sep 2026 tidak boleh ikut hilang di sini.
        self.lihat_hook(0.3)
        self.cm.autonomous_control()

        motion = [p for p in self.sent if p["type"] == "control"]
        self.assertTrue(motion)
        self.assertEqual(motion[-1]["src"], "fsm")

    def test_servo_mati_menghentikan_urutan(self):
        # Config rusak wajib menghentikan autonomous, manual tetap tersedia.
        self.cm.SERVO_CONFIG_PATH = os.path.join(ROOT, "tidak-ada.yaml")
        self.cm.load_servo_config()
        self.assertIsNone(self.cm.servo_cfg)

        self.cm.auto_step_start = time.monotonic()
        self.sent.clear()
        self.cm.autonomous_control()

        motion = [p for p in self.sent if p["type"] == "control"]
        # Tidak boleh jatuh ke surge konstan saat servo mati.
        self.assertEqual(motion[-1]["surge"], 0)
        self.assertTrue(self.cm.auto_finished)
        self.assertEqual(motion[-1]["sway"], 0)


class UrutanMisi(_ServoBase):
    """Clock palsu + packet capture: tidak ARM atau menghubungi Pi."""

    def setUp(self):
        super().setUp()
        self.now = 100.0
        clock = patch.object(self.cm.time, "monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.cm.set_mode(self.cm.MODE_AUTONOMOUS)
        self.state()
        self.sent.clear()

    def state(self, depth=1.0, armed=True):
        self.cm.accept_vision_message({"type": "vehicle_state",
                                      "value": {"depth": depth, "armed": armed}})

    def vision(self, channel="qr_vision", fraction=0.15, ex=0.0, age=0.0, receipt=None):
        self.cm.accept_vision_message({
            "type": channel, "received": self.now if receipt is None else receipt,
            "age": age, "value": {"center": [320 + ex * 320, 240],
                                    "frame_w": 640, "frame_h": 480,
                                    "area": fraction * 640 * 480}})

    def step(self, dt=0.05, depth=1.0):
        self.now += dt
        self.state(depth)
        self.cm.autonomous_control()

    def commands(self, name):
        return [p["value"] for p in self.sent
                if p["type"] == "command" and p["name"] == name]

    def assert_stopped(self):
        self.assertTrue(self.cm.auto_finished)
        frames = [p for p in self.sent if p["type"] == "control"]
        self.assertTrue(frames)
        self.assertEqual([frames[-1][a] for a in self.cm.AXES], [0] * 4)
        self.assertEqual(frames[-1]["src"], "fsm")

    def test_alur_lengkap_close_sekali_qr_hilang_lanjut_naik_relatif(self):
        self.step(3.01)
        self.assertEqual(self.cm.auto_index, 1)
        self.step(depth=0.7)
        self.assertEqual(self.commands("depth_apply"), [1.0])
        self.assertEqual(self.cm.auto_index, 1)
        self.step(depth=0.95)
        self.assertEqual(self.cm.auto_index, 2)
        self.step()
        frame = self.sent[-1]
        self.assertLess(frame["surge"], 0)
        self.assertGreater(frame["yaw"], 0)
        self.assertEqual(frame["sway"], 0)
        self.step(2.01)
        self.assertLess(self.sent[-1]["yaw"], 0)
        self.vision()
        self.step()
        self.assertEqual(self.cm.auto_index, 4)  # CASE 3 dilewati
        for _ in range(self.ticks):
            self.vision()
            self.step()
        self.assertEqual(self.cm.auto_index, 5)
        self.assertEqual(self.commands("gripper"), ["close"])
        self.step(1.4)
        self.assertEqual(self.cm.auto_index, 5)
        self.step(0.11)
        self.assertEqual(self.cm.auto_index, 6)
        self.step(depth=0.9)
        self.assertAlmostEqual(self.commands("depth_apply")[-1], 0.4)
        self.step(3.01, depth=0.45)
        self.assert_stopped()
        # Jangan mengganti target 0.4 dengan depth aktual pada akhir timer.
        self.assertAlmostEqual(self.commands("depth_apply")[-1], 0.4)
        self.assertEqual(self.commands("gripper"), ["close"])

    def test_settle_dimulai_setelah_arm_dan_tidak_mengirim_arm(self):
        self.state(armed=False)
        self.cm.autonomous_control()
        self.now += 10
        self.state(armed=False)
        self.cm.autonomous_control()
        self.step(0.05)
        self.assertEqual(self.cm.auto_index, 0)
        self.step(3.01)
        self.assertEqual(self.cm.auto_index, 1)
        self.assertEqual(self.commands("arm"), [])

    def test_case1_timeout_tetap_memulai_pencarian(self):
        self.cm.enter_case(1)
        self.step(depth=0.3)
        self.step(3.01, depth=0.4)
        self.assertEqual(self.cm.auto_index, 2)
        self.assertEqual(self.commands("depth_apply"), [1.0])

    def test_search_timeout_tidak_pernah_menutup_gripper(self):
        self.cm.enter_case(2)
        self.step(8.01, depth=0.82)
        self.assert_stopped()
        self.assertEqual(self.commands("gripper"), [])
        self.assertEqual(self.commands("depth_apply"), [0.82])
        self.vision()
        self.step()
        self.assert_stopped()  # tidak otomatis mulai ulang
        self.assertEqual(self.commands("gripper"), [])

    def test_servo_timeout_centered_tapi_terlalu_jauh_berhenti(self):
        self.cm.enter_case(4)
        self.vision(fraction=0.01)
        self.step(3.01)
        self.assert_stopped()
        self.assertEqual(self.commands("gripper"), [])

    def test_hilang_sementara_nol_seketika_lebih_dua_detik_abort(self):
        self.cm.enter_case(4)
        self.vision(fraction=0.01)
        self.step()
        self.assertLess(self.sent[-1]["surge"], 0)
        self.step(1.01)
        self.assertFalse(self.cm.auto_finished)
        self.assertEqual([self.sent[-1][a] for a in self.cm.AXES], [0] * 4)
        self.step(1.0)
        self.assert_stopped()
        self.assertEqual(self.commands("gripper"), [])

    def test_threshold_per_kanal_dan_streak_wajib_beruntun(self):
        self.cm.enter_case(4)
        for channel, frac, expected in [
                ("qr_vision", 0.15, 1), ("qr_vision", 0.15, 2),
                ("qr_region", 0.15, 0),  # cukup decoded, BELUM cukup region
                ("qr_region", 0.25, 1), ("qr_vision", 0.15, 2)]:
            self.vision(channel, frac)
            self.step()
            self.assertEqual(self.cm.servo_hits, expected)
            self.assertEqual(self.cm.auto_index, 4)
        self.vision("qr_region", 0.25)
        self.step()
        self.assertEqual(self.cm.auto_index, 5)
        self.assertEqual(self.commands("gripper"), ["close"])

    def test_area_sama_ambang_dan_lateral_meleset_tidak_close(self):
        self.cm.enter_case(4)
        for ex, fraction in [(0, 0.1)] * 5 + [(0.4, 0.9)] * 5:
            self.vision(ex=ex, fraction=fraction)
            self.step()
        self.assertEqual(self.cm.servo_hits, 0)
        self.assertEqual(self.commands("gripper"), [])

    def test_threshold_null_tidak_menutup(self):
        self.cm.servo_cfg["close_area_frac_decoded"] = None
        self.cm.servo_cfg["close_area_frac_region"] = None
        self.cm.enter_case(4)
        for channel in ["qr_vision", "qr_region"] * 5:
            self.vision(channel, 0.9)
            self.step()
        self.assertEqual(self.commands("gripper"), [])

    def test_cache_ulang_kanal_lama_dan_umur_pi_tidak_menyegarkan(self):
        self.vision("qr_region", 0.25, receipt=95, age=0.2)
        accepted_time = self.cm.last_hook_time
        self.now += 0.1
        self.vision("qr_region", 0.25, receipt=95, age=0.3)
        self.vision("qr_vision", 0.15, receipt=94, age=0.1)
        self.assertEqual(self.cm.last_hook_time, accepted_time)
        self.assertEqual(self.cm.latest_qr_metric, ("qr_region", 0.25))
        self.assertAlmostEqual(accepted_time, 99.8)
        self.vision(receipt=96, age=1.1)
        self.assertEqual(self.cm.last_hook_time, accepted_time)

    def test_pi_lama_tanpa_metadata_dan_geometri_cacat_ditolak(self):
        for value in (float("nan"), float("inf"), -0.1, 1.1):
            self.vision(fraction=value)
            self.assertIsNone(self.cm.latest_hook)
        self.cm.accept_vision_message({"type": "qr_region", "value": {
            "center": [320, 240], "frame_w": 640, "frame_h": 480, "area": 10000}})
        self.assertIsNone(self.cm.latest_hook)
        for center in (float("nan"), float("inf"), -1):
            self.assertIsNone(self.cm.qr_center_from_telemetry({
                "center": [center, 240], "frame_w": 640}))

    def test_depth_basi_abort_tanpa_target_tebakan(self):
        self.cm.enter_case(2)
        self.now += 1.1  # jangan refresh vehicle_state
        self.cm.autonomous_control()
        self.assert_stopped()
        self.assertEqual(self.commands("depth_apply"), [])
        self.assertEqual(self.commands("gripper"), [])

    def test_disarm_saat_search_mengakhiri_urutan(self):
        self.cm.enter_case(2)
        self.state(armed=False)
        self.cm.autonomous_control()
        self.assert_stopped()
        self.assertEqual(self.commands("depth_apply"), [])

    def test_reset_mode_membuang_streak_latch_dan_deteksi(self):
        self.vision()
        self.cm.auto_gripped = True
        self.cm.servo_hits = 2
        self.cm.set_mode(self.cm.MODE_MANUAL)
        self.cm.set_mode(self.cm.MODE_AUTONOMOUS)
        self.assertFalse(self.cm.auto_gripped)
        self.assertEqual(self.cm.servo_hits, 0)
        self.assertIsNone(self.cm.latest_hook)
        self.assertEqual(self.cm.auto_index, 0)

    def test_case_close_tidak_bisa_dimasuki_tanpa_pemicu(self):
        self.cm.enter_case(5)
        self.step()
        self.assert_stopped()
        self.assertEqual(self.commands("gripper"), [])

    def test_config_tidak_finite_menghentikan_tanpa_motion(self):
        import yaml
        for section, key, value in [
                ("servo_hook", "close_area_frac_region", float("nan")),
                ("servo_hook", "close_area_frac_decoded", 0),
                ("servo_hook", "centered_ticks", 0),
                ("mission", "search_sweep_s", 0),
                ("mission", "search_yaw", 1001)]:
            with self.subTest(key=key):
                cfg = yaml.safe_load(CONFIG_UJI.format(source="qr", invert="false", ticks=3))
                cfg[section][key] = value
                with open(self.tmp.name, "w") as f:
                    yaml.safe_dump(cfg, f)
                self.cm.autonomous_reset()
                self.cm.autonomous_control()
                self.assertIsNone(self.cm.servo_cfg)
                self.assert_stopped()
                self.assertEqual(self.commands("gripper"), [])


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
