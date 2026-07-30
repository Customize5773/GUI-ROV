"""Unit test AttitudeFilter (tanpa hardware/pymavlink).

    python3 -m unittest test_attitude_filter -v
"""

import unittest

from attitude_filter import AttitudeFilter, DT_MAX, DT_MIN


class TestInisialisasi(unittest.TestCase):
    def test_inisialisasi_dari_nilai_mentah(self):
        f = AttitudeFilter()
        roll, pitch, yaw = f.update(10.0, -5.0, 90.0, 0.0, 0.0, 0.0, 0.1)
        self.assertAlmostEqual(roll, 10.0, places=6)
        self.assertAlmostEqual(pitch, -5.0, places=6)
        self.assertAlmostEqual(yaw, 90.0, places=6)


class TestComplementaryFilter(unittest.TestCase):
    def test_integrasi_gyro_dan_koreksi_sudut(self):
        f = AttitudeFilter(complementary_alpha=0.98, ema_alpha_roll=0.0)
        f.update(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1)  # seed

        # Gyro rate + sudut mentah konsisten menuju target 30 derajat/detik.
        roll = None
        for _ in range(200):
            roll, _, _ = f.update(10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.05)
        self.assertAlmostEqual(roll, 10.0, delta=0.5)


class TestYawWrap(unittest.TestCase):
    def test_wrap_yaw_359_ke_1_derajat(self):
        f = AttitudeFilter()
        f.update(0.0, 0.0, 359.0, 0.0, 0.0, 0.0, 0.1)  # seed
        _, _, yaw = f.update(0.0, 0.0, 1.0, 0.0, 0.0, 5.0, 0.1)

        # Selisih 359 -> 1 derajat seharusnya diperlakukan sebagai +2 derajat,
        # bukan lompatan -358 derajat.
        diff = (yaw - 359.0 + 180) % 360 - 180
        self.assertLess(abs(diff), 10.0)


class TestDtClamp(unittest.TestCase):
    def test_dt_diklem_batas_bawah(self):
        f1 = AttitudeFilter()
        f2 = AttitudeFilter()
        f1.update(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1)
        f2.update(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1)

        r1 = f1.update(10.0, 0.0, 0.0, 100.0, 0.0, 0.0, DT_MIN / 100)
        r2 = f2.update(10.0, 0.0, 0.0, 100.0, 0.0, 0.0, DT_MIN)
        self.assertAlmostEqual(r1[0], r2[0], places=6)

    def test_dt_diklem_batas_atas(self):
        f1 = AttitudeFilter()
        f2 = AttitudeFilter()
        f1.update(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1)
        f2.update(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1)

        r1 = f1.update(10.0, 0.0, 0.0, 5.0, 0.0, 0.0, DT_MAX * 100)
        r2 = f2.update(10.0, 0.0, 0.0, 5.0, 0.0, 0.0, DT_MAX)
        self.assertAlmostEqual(r1[0], r2[0], places=6)


class TestRedamNoise(unittest.TestCase):
    def test_redam_noise_frekuensi_tinggi(self):
        f = AttitudeFilter()
        f.update(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1)  # seed

        noisy_inputs = [5.0 + (3.0 if i % 2 == 0 else -3.0) for i in range(100)]
        outputs = [
            f.update(v, 0.0, 0.0, 0.0, 0.0, 0.0, 0.05)[0] for v in noisy_inputs
        ]

        def variance(vals):
            mean = sum(vals) / len(vals)
            return sum((v - mean) ** 2 for v in vals) / len(vals)

        self.assertLess(variance(outputs[10:]), variance(noisy_inputs[10:]) * 0.5)


class TestTanpaDependensiPymavlink(unittest.TestCase):
    def test_tanpa_dependensi_pymavlink(self):
        import attitude_filter

        with open(attitude_filter.__file__) as fh:
            content = fh.read()
        self.assertNotIn("import pymavlink", content)
        self.assertNotIn("import socket", content)


if __name__ == "__main__":
    unittest.main()
