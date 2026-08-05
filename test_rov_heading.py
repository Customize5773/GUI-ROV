"""Unit test hukum kendali heading-hold POSHOLD (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_heading -v
"""

import unittest

from rov_heading import (
    HEADING_DEADBAND_DEG,
    HEADING_LIMIT,
    HEADING_P,
    heading_bias,
    heading_error,
    operator_holding_yaw,
)


class TestHeadingError(unittest.TestCase):
    def test_kasus_sederhana(self):
        self.assertAlmostEqual(heading_error(100, 90), 10.0)
        self.assertAlmostEqual(heading_error(90, 100), -10.0)
        self.assertAlmostEqual(heading_error(90, 90), 0.0)

    def test_wrap_melewati_utara(self):
        # Inti dari modul ini. Target 350°, heading 10° -> jalan terpendek
        # adalah 20° BERLAWANAN jarum jam (-20), bukan 340° searah jarum jam.
        # Tanpa wrap, wahana akan berputar hampir satu putaran penuh.
        self.assertAlmostEqual(heading_error(350, 10), -20.0)
        self.assertAlmostEqual(heading_error(10, 350), 20.0)
        self.assertAlmostEqual(heading_error(0, 359), 1.0)
        self.assertAlmostEqual(heading_error(359, 0), -1.0)

    def test_selalu_di_dalam_rentang(self):
        for target in range(0, 360, 7):
            for actual in range(0, 360, 11):
                err = heading_error(target, actual)
                self.assertGreater(err, -180.0 - 1e-9)
                self.assertLessEqual(err, 180.0 + 1e-9)

    def test_input_tidak_valid_tidak_menghasilkan_koreksi(self):
        # heading_target yang belum pernah ter-seed tidak boleh menggerakkan
        # thruster yaw.
        for bad in (None, "abc", [1]):
            self.assertEqual(heading_error(bad, 90), 0.0)
            self.assertEqual(heading_error(90, bad), 0.0)


class TestHeadingBias(unittest.TestCase):
    def test_nol_di_dalam_deadband(self):
        self.assertEqual(heading_bias(90, 90), 0)
        self.assertEqual(heading_bias(90, 90 + HEADING_DEADBAND_DEG / 2), 0)

    def test_tanda_koreksi(self):
        # Wahana kurang ke kanan dari target (heading < target) -> koreksi
        # positif supaya r memutar wahana searah jarum jam.
        self.assertGreater(heading_bias(100, 80), 0)
        self.assertLess(heading_bias(80, 100), 0)

    def test_besaran_proporsional(self):
        self.assertEqual(heading_bias(100, 90), int(round(10 * HEADING_P)))

    def test_di_clamp_ke_limit(self):
        # Error besar (mis. wahana ditabrak/diputar 170°) tidak boleh meminta
        # otoritas yaw penuh — operator harus selalu bisa mengalahkannya.
        self.assertEqual(heading_bias(0, 170), -int(HEADING_LIMIT))
        self.assertEqual(heading_bias(170, 0), int(HEADING_LIMIT))
        for target in range(0, 360, 13):
            self.assertLessEqual(abs(heading_bias(target, 0)), HEADING_LIMIT)

    def test_wrap_ikut_terpakai(self):
        # Konsekuensi langsung dari heading_error: koreksi harus mengambil arah
        # terpendek melewati utara.
        self.assertLess(heading_bias(350, 10), 0)
        self.assertGreater(heading_bias(10, 350), 0)


class TestOperatorHoldingYaw(unittest.TestCase):
    def test_stik_netral_bukan_dipegang(self):
        self.assertFalse(operator_holding_yaw({"yaw": 0}))
        self.assertFalse(operator_holding_yaw({"yaw": 5}))
        self.assertFalse(operator_holding_yaw({}))

    def test_stik_disentuh(self):
        self.assertTrue(operator_holding_yaw({"yaw": 300}))
        self.assertTrue(operator_holding_yaw({"yaw": -300}))

    def test_nilai_rusak_dianggap_tidak_dipegang(self):
        # Kalau axis rusak dianggap "dipegang", overlay diam-diam mati dan
        # operator tidak pernah tahu kenapa heading tidak ditahan.
        self.assertFalse(operator_holding_yaw({"yaw": None}))
        self.assertFalse(operator_holding_yaw({"yaw": "x"}))


if __name__ == "__main__":
    unittest.main()
