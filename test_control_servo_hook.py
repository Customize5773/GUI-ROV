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
        self.assertIsNotNone(self.cm.servo_cfg, "config uji gagal dimuat")

    def lihat_hook(self, ex, umur=0.0):
        """Taruh deteksi dgn error ternormalisasi `ex` (+ = hook di kanan)."""
        center_x = FRAME_W / 2.0 + ex * (FRAME_W / 2.0)
        self.cm.latest_hook = (center_x, FRAME_W)
        self.cm.last_hook_time = time.monotonic() - umur

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
        """Surge turun ke nol — TAPI lewat slew, tidak seperti sway.

        Sway wajib nol seketika karena acuannya hilang. Surge tidak: menghentikan
        dorongan maju secara mendadak justru sentakan yang diredam slew (sentak
        -> ROV miring -> kamera ikut miring). Yang dijaga di sini adalah ia
        benar-benar SAMPAI nol dan tidak membeku di nilai terakhir.
        """
        self.lihat_hook(0.0)
        for _ in range(20):
            self.tick()

        surge_awal, _sway, _ = self.tick()
        self.assertLess(surge_awal, 0)

        self.cm.last_hook_time = time.monotonic() - 5.0

        jejak = [self.tick()[0] for _ in range(60)]

        self.assertGreater(jejak[0], surge_awal, "surge membeku, tidak melandai")
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
        self.cm.auto_step_start = time.time()
        self.sent.clear()

    def test_hook_di_tengah_menyelesaikan_case_sebelum_timeout(self):
        self.lihat_hook(0.0)

        for _ in range(self.ticks + 2):
            self.cm.autonomous_control()
            self.lihat_hook(0.0)

        self.assertEqual(self.cm.auto_index, self.case_servo + 1)

    def test_hook_tak_pernah_terlihat_tetap_lanjut_saat_timeout(self):
        durasi = self.cm.AUTO_STEPS[self.case_servo][0]
        self.cm.autonomous_control()
        self.assertEqual(self.cm.auto_index, self.case_servo,
                         "CASE tidak boleh selesai sebelum timeout")

        self.cm.auto_step_start = time.time() - (durasi + 0.1)
        self.cm.autonomous_control()

        # Lanjut, bukan menggantung — walau tak satu pun hook terdeteksi.
        self.assertEqual(self.cm.auto_index, self.case_servo + 1)
        self.assertFalse(self.cm.servo_seen_hook)

    def test_case_servo_tetap_mengirim_frame_bertag_fsm(self):
        # Perbaikan otoritas axis 7 Sep 2026 tidak boleh ikut hilang di sini.
        self.lihat_hook(0.3)
        self.cm.autonomous_control()

        motion = [p for p in self.sent if p["type"] == "control"]
        self.assertTrue(motion)
        self.assertEqual(motion[-1]["src"], "fsm")

    def test_servo_mati_membuat_case_jadi_langkah_waktu_biasa(self):
        # PyYAML hilang / config rusak tidak boleh mematikan autonomous.
        self.cm.SERVO_CONFIG_PATH = os.path.join(ROOT, "tidak-ada.yaml")
        self.cm.load_servo_config()
        self.assertIsNone(self.cm.servo_cfg)

        self.cm.auto_step_start = time.time()
        self.sent.clear()
        self.cm.autonomous_control()

        motion = [p for p in self.sent if p["type"] == "control"]
        # surge konstan dari tabel, sway 0 — persis perilaku sebelum YOLO.
        self.assertEqual(motion[-1]["surge"],
                         self.cm.AUTO_STEPS[self.case_servo][1])
        self.assertEqual(motion[-1]["sway"], 0)


if __name__ == "__main__":
    unittest.main()
