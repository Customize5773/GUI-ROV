"""Unit test rov_drift.flow_to_velocity (murni, tanpa cv2/pymavlink).

    python3 -m unittest test_rov_drift -v
"""

import unittest

from rov_drift import flow_to_velocity, integrate_accel


class TestFlowToVelocity(unittest.TestCase):
    def test_nilai_dasar(self):
        # 10 px dalam 0.1 s, altitude 1 m, focal 500 px -> 10/0.1 * 1/500 = 0.2 m/s
        vx, vy = flow_to_velocity(10, 0, 0.1, 1.0, 500.0)
        self.assertAlmostEqual(vx, 0.2)
        self.assertAlmostEqual(vy, 0.0)

    def test_arah_dua_sumbu(self):
        vx, vy = flow_to_velocity(10, -20, 0.1, 1.0, 500.0)
        self.assertAlmostEqual(vx, 0.2)
        self.assertAlmostEqual(vy, -0.4)

    def test_altitude_lebih_tinggi_kecepatan_lebih_besar(self):
        v_low, _ = flow_to_velocity(10, 0, 0.1, 1.0, 500.0)
        v_high, _ = flow_to_velocity(10, 0, 0.1, 2.0, 500.0)
        self.assertAlmostEqual(v_high, v_low * 2)

    def test_dt_tidak_valid_jadi_nol(self):
        for dt in (0, -1, None):
            self.assertEqual(flow_to_velocity(10, 10, dt, 1.0, 500.0), (0.0, 0.0))

    def test_altitude_tidak_valid_jadi_nol(self):
        for alt in (0, -1, None):
            self.assertEqual(flow_to_velocity(10, 10, 0.1, alt, 500.0), (0.0, 0.0))

    def test_focal_tidak_valid_jadi_nol(self):
        for f in (0, -1, None):
            self.assertEqual(flow_to_velocity(10, 10, 0.1, 1.0, f), (0.0, 0.0))

    def test_flow_nol_kecepatan_nol(self):
        self.assertEqual(flow_to_velocity(0, 0, 0.1, 1.0, 500.0), (0.0, 0.0))


class TestIntegrateAccel(unittest.TestCase):
    def test_akselerasi_konstan_menambah_kecepatan(self):
        # a=1 m/s^2 selama 0.1s -> +0.1 m/s
        vx, vy = integrate_accel(1.0, 0.0, 0.1, 0.0, 0.0)
        self.assertAlmostEqual(vx, 0.1)
        self.assertAlmostEqual(vy, 0.0)

    def test_menumpuk_dari_kecepatan_sebelumnya(self):
        vx, vy = integrate_accel(1.0, -2.0, 0.1, 0.5, 0.5)
        self.assertAlmostEqual(vx, 0.6)
        self.assertAlmostEqual(vy, 0.3)

    def test_dt_tidak_valid_kecepatan_tetap(self):
        for dt in (0, -1, None):
            self.assertEqual(integrate_accel(5.0, 5.0, dt, 1.0, 2.0), (1.0, 2.0))

    def test_accel_nol_kecepatan_tetap(self):
        self.assertEqual(integrate_accel(0.0, 0.0, 0.1, 0.3, -0.3), (0.3, -0.3))


if __name__ == "__main__":
    unittest.main()
