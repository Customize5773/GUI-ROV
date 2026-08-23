"""Unit test rov_position (murni, tanpa cv2/pymavlink).

    python3 -m unittest test_rov_position -v
"""

import unittest

from rov_position import (
    position_correction,
    operator_holding_translation,
    POSITION_DEADBAND_M,
)


class TestPositionCorrection(unittest.TestCase):
    def test_dalam_deadband_nol(self):
        self.assertEqual(position_correction(0.01, -0.02), (0, 0))

    def test_arah_koreksi_melawan_pergeseran(self):
        # error POSITIF = wahana sudah bergeser ke arah positif -> koreksi
        # HARUS negatif (melawan), bukan mengikuti. Ini bug tanda yang
        # sempat lolos ke draf pertama (lihat docstring position_correction).
        cx, cy = position_correction(0.5, -0.5)
        self.assertLess(cx, 0)
        self.assertGreater(cy, 0)

    def test_besaran_proporsional(self):
        cx1, _ = position_correction(0.2, 0)
        cx2, _ = position_correction(0.4, 0)
        self.assertAlmostEqual(cx2, cx1 * 2, delta=1)

    def test_diclamp_ke_limit(self):
        cx, cy = position_correction(100.0, -100.0, limit=300.0)
        self.assertEqual(cx, -300)
        self.assertEqual(cy, 300)

    def test_axis_independen(self):
        cx, cy = position_correction(0.5, 0.0)
        self.assertNotEqual(cx, 0)
        self.assertEqual(cy, 0)

    def test_tepat_di_ambang_deadband_bukan_nol(self):
        # abs(e) < deadband, jadi PERSIS di ambang tidak ikut ternolkan —
        # sama konvensi dengan heading_bias() di rov_heading.py.
        cx, _ = position_correction(POSITION_DEADBAND_M, 0)
        self.assertNotEqual(cx, 0)

    def test_sedikit_di_bawah_ambang_nol(self):
        cx, _ = position_correction(POSITION_DEADBAND_M - 0.001, 0)
        self.assertEqual(cx, 0)

    def test_nilai_tidak_valid_jadi_nol(self):
        self.assertEqual(position_correction(None, "abc"), (0, 0))


class TestOperatorHoldingTranslation(unittest.TestCase):
    EPS = 20

    def test_stik_netral_bukan_dipegang(self):
        self.assertFalse(operator_holding_translation({"surge": 0, "sway": 0}, self.EPS))

    def test_surge_dipegang(self):
        self.assertTrue(operator_holding_translation({"surge": 500, "sway": 0}, self.EPS))

    def test_sway_dipegang(self):
        self.assertTrue(operator_holding_translation({"surge": 0, "sway": -500}, self.EPS))

    def test_di_bawah_epsilon_bukan_dipegang(self):
        self.assertFalse(operator_holding_translation({"surge": 5, "sway": -5}, self.EPS))

    def test_axis_hilang_dianggap_nol(self):
        self.assertFalse(operator_holding_translation({}, self.EPS))

    def test_nilai_rusak_dianggap_tidak_dipegang(self):
        self.assertFalse(operator_holding_translation({"surge": "abc"}, self.EPS))


if __name__ == "__main__":
    unittest.main()
