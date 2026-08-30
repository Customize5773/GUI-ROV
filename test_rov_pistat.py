"""Unit test status hardware Pi (tanpa hardware/Pi sungguhan).

    python3 -m unittest test_rov_pistat -v

Yang dijaga di sini adalah LOGIKA delta dan kontrak nilai-kosong, bukan I/O:
read_cpu_percent() sengaja stateless supaya snapshot bisa disuntik langsung.
"""

import json
import unittest

import rov_pistat
from rov_pistat import parse_cpu_snapshot, read_cpu_percent, read_soc_temp


class TestParseCpuSnapshot(unittest.TestCase):
    def test_baris_proc_stat_asli(self):
        # user nice system idle iowait irq softirq ...
        line = "cpu  100 0 50 800 50 0 0 0 0 0"
        self.assertEqual(parse_cpu_snapshot(line), (1000, 850))  # idle+iowait

    def test_hanya_baris_pertama_yang_dipakai(self):
        """/proc/stat berlanjut dengan cpu0/cpu1/intr — token "cpu0" pernah
        membuat int() gagal dan seluruh pembacaan mengembalikan None."""
        line = "cpu  100 0 50 800 50 0 0\ncpu0 50 0 25 400 25 0 0\nintr 12345"
        self.assertEqual(parse_cpu_snapshot(line), (1000, 850))

    def test_input_rusak_jadi_none(self):
        for bad in (None, "", "intr 123", "cpu", "cpu a b c d e", "cpu 1 2 3"):
            self.assertIsNone(parse_cpu_snapshot(bad), bad)


class TestReadCpuPercent(unittest.TestCase):
    def _patch(self, stat_text):
        rov_pistat._read_text = lambda path: stat_text

    def setUp(self):
        self._orig = rov_pistat._read_text

    def tearDown(self):
        rov_pistat._read_text = self._orig

    def test_sampel_pertama_none_bukan_nol(self):
        """Counter kumulatif: satu titik tidak bisa jadi persen. 0 akan terbaca
        operator sebagai "Pi idle", padahal artinya "belum tahu"."""
        self._patch("cpu  100 0 50 800 50 0 0")
        pct, snap = read_cpu_percent(None)
        self.assertIsNone(pct)
        self.assertEqual(snap, (1000, 850))

    def test_delta_setengah_sibuk(self):
        self._patch("cpu  100 0 100 800 0 0 0")   # total 1000, idle 800
        pct, _ = read_cpu_percent((500, 700))     # d_total=500, d_idle=100
        self.assertAlmostEqual(pct, 80.0)

    def test_idle_penuh_nol_persen(self):
        self._patch("cpu  0 0 0 1000 0 0 0")
        pct, _ = read_cpu_percent((0, 0))
        self.assertEqual(pct, 0.0)

    def test_sibuk_penuh_seratus_persen(self):
        self._patch("cpu  1000 0 0 0 0 0 0")
        pct, _ = read_cpu_percent((0, 0))
        self.assertEqual(pct, 100.0)

    def test_dua_sampel_identik_none_bukan_zerodivision(self):
        """Dipanggil dua kali terlalu cepat -> d_total 0."""
        self._patch("cpu  100 0 50 800 50 0 0")
        pct, _ = read_cpu_percent((1000, 850))
        self.assertIsNone(pct)

    def test_counter_mundur_setelah_reboot_none(self):
        self._patch("cpu  10 0 5 80 5 0 0")       # total 100 < prev 1000
        pct, _ = read_cpu_percent((1000, 850))
        self.assertIsNone(pct)

    def test_selalu_dalam_0_sampai_100(self):
        # d_idle > d_total (hotplug CPU) tidak boleh jadi persen negatif
        self._patch("cpu  0 0 0 5000 0 0 0")
        pct, _ = read_cpu_percent((0, 0))
        self.assertGreaterEqual(pct, 0.0)
        self.assertLessEqual(pct, 100.0)

    def test_proc_stat_hilang_pertahankan_snapshot_lama(self):
        self._patch(None)
        pct, snap = read_cpu_percent((1000, 850))
        self.assertIsNone(pct)
        self.assertEqual(snap, (1000, 850))


class TestSocTemp(unittest.TestCase):
    def test_mesin_tanpa_cpu_thermal_none(self):
        """Laptop dev: zone0 = INT3400 (chipset), bukan cpu-thermal. None di
        sini BENAR — fallback ke zone pertama = mengirim 20 °C palsu."""
        rov_pistat.THERMAL_ROOT = "/nonexistent-thermal"
        try:
            self.assertIsNone(read_soc_temp())
        finally:
            rov_pistat.THERMAL_ROOT = "/sys/class/thermal"

    def test_mesin_ini_none_atau_suhu_wajar(self):
        t = read_soc_temp()
        if t is not None:
            self.assertGreater(t, -20.0)
            self.assertLess(t, 150.0)


class TestJsonAman(unittest.TestCase):
    def test_tidak_pernah_nan(self):
        """send_to_gui() memakai allow_nan=False: satu NaN mematikan SELURUH
        paket telemetri, bukan cuma field pi_cpu/pi_temp."""
        payload = {"pi_temp": read_soc_temp(), "pi_cpu": read_cpu_percent(None)[0]}
        json.dumps(payload, allow_nan=False)   # tidak boleh raise


if __name__ == "__main__":
    unittest.main()
