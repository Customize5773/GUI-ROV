"""Unit test pemetaan & slew gripper (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_gripper -v
"""

import unittest

from rov_gripper import (
    GRIPPER_AXIS_DEADZONE,
    GRIPPER_PWM_CLOSE,
    GRIPPER_PWM_NEUTRAL,
    GRIPPER_PWM_OPEN,
    clamp_pwm,
    gripper_value_to_pwm,
    slew_toward,
)


class TestGripperValueToPwm(unittest.TestCase):
    def test_string_open_close(self):
        self.assertEqual(gripper_value_to_pwm("close"), GRIPPER_PWM_CLOSE)
        self.assertEqual(gripper_value_to_pwm("open"), GRIPPER_PWM_OPEN)
        self.assertEqual(gripper_value_to_pwm("CLOSE"), GRIPPER_PWM_CLOSE)
        self.assertEqual(gripper_value_to_pwm(" Open "), GRIPPER_PWM_OPEN)

    def test_bool_sesuai_rov_link(self):
        # rov_link.py: value True == "close"
        self.assertEqual(gripper_value_to_pwm(True), GRIPPER_PWM_CLOSE)
        self.assertEqual(gripper_value_to_pwm(False), GRIPPER_PWM_OPEN)

    def test_string_tak_dikenal_jadi_netral(self):
        for bad in ("", "nonsense", "toggle"):
            self.assertEqual(gripper_value_to_pwm(bad), GRIPPER_PWM_NEUTRAL)

    def test_nilai_tidak_valid_jadi_netral(self):
        for bad in (None, {}, [], float("nan")):
            self.assertEqual(gripper_value_to_pwm(bad), GRIPPER_PWM_NEUTRAL)

    def test_axis_analog_deadzone(self):
        self.assertEqual(gripper_value_to_pwm(0), GRIPPER_PWM_NEUTRAL)
        self.assertEqual(
            gripper_value_to_pwm(GRIPPER_AXIS_DEADZONE - 1), GRIPPER_PWM_NEUTRAL
        )

    def test_axis_analog_proporsional(self):
        # positif = menutup, negatif = membuka
        self.assertEqual(gripper_value_to_pwm(1000), GRIPPER_PWM_CLOSE)
        self.assertEqual(gripper_value_to_pwm(-1000), GRIPPER_PWM_OPEN)
        mid_close = gripper_value_to_pwm(500)
        self.assertTrue(GRIPPER_PWM_CLOSE < mid_close < GRIPPER_PWM_NEUTRAL)

    def test_axis_di_luar_rentang_ikut_diklem(self):
        self.assertEqual(gripper_value_to_pwm(99999), GRIPPER_PWM_CLOSE)
        self.assertEqual(gripper_value_to_pwm(-99999), GRIPPER_PWM_OPEN)


class TestClampPwm(unittest.TestCase):
    def test_tidak_pernah_lewat_batas(self):
        lo = min(GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE)
        hi = max(GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE)
        for v in (-5000, 0, 900, 1100, 1500, 1900, 2500, 99999):
            self.assertGreaterEqual(clamp_pwm(v), lo)
            self.assertLessEqual(clamp_pwm(v), hi)

    def test_nilai_invalid_jadi_netral(self):
        self.assertEqual(clamp_pwm(None), GRIPPER_PWM_NEUTRAL)
        self.assertEqual(clamp_pwm(float("nan")), GRIPPER_PWM_NEUTRAL)


class TestSlewToward(unittest.TestCase):
    def test_dt_nol_tidak_bergerak(self):
        self.assertEqual(
            slew_toward(GRIPPER_PWM_NEUTRAL, GRIPPER_PWM_CLOSE, 0.0),
            GRIPPER_PWM_NEUTRAL,
        )

    def test_dt_negatif_dianggap_nol(self):
        self.assertEqual(
            slew_toward(GRIPPER_PWM_NEUTRAL, GRIPPER_PWM_CLOSE, -5.0),
            GRIPPER_PWM_NEUTRAL,
        )

    def test_rate_limit_membatasi_lompatan(self):
        # max_speed 500 PWM/s, dt 0.1 -> maksimum 50 PWM per langkah
        out = slew_toward(GRIPPER_PWM_NEUTRAL, GRIPPER_PWM_CLOSE, 0.1)
        self.assertLessEqual(abs(out - GRIPPER_PWM_NEUTRAL), 50.0 + 1e-9)
        self.assertLess(out, GRIPPER_PWM_NEUTRAL)  # bergerak ke arah close

    def test_konvergen_ke_target(self):
        pos = float(GRIPPER_PWM_NEUTRAL)
        for _ in range(500):
            pos = slew_toward(pos, GRIPPER_PWM_CLOSE, 0.1)
        self.assertAlmostEqual(pos, GRIPPER_PWM_CLOSE, delta=1.0)

    def test_selalu_dalam_batas_aman(self):
        lo = min(GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE)
        hi = max(GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE)
        pos = float(GRIPPER_PWM_NEUTRAL)
        for target in (99999, -99999, GRIPPER_PWM_OPEN, GRIPPER_PWM_CLOSE):
            for _ in range(50):
                pos = slew_toward(pos, target, 0.1)
                self.assertGreaterEqual(pos, lo)
                self.assertLessEqual(pos, hi)

    def test_tahan_posisi_saat_target_sama(self):
        pos = slew_toward(GRIPPER_PWM_CLOSE, GRIPPER_PWM_CLOSE, 0.1)
        self.assertAlmostEqual(pos, GRIPPER_PWM_CLOSE, places=6)


class TestKonsistensiDenganRovLink(unittest.TestCase):
    def test_pwm_dan_channel_sama_dengan_rov_link(self):
        """Jalur manual & autonomous harus menggerakkan servo yang sama."""
        import re

        with open("autonomy/rov_link.py") as fh:
            src = fh.read()

        def const(name):
            m = re.search(rf"^{name}\s*=\s*(\d+)", src, re.M)
            return int(m.group(1)) if m else None

        import rov_gripper

        self.assertEqual(const("GRIPPER_SERVO_CH"), rov_gripper.GRIPPER_SERVO_CH)
        self.assertEqual(const("GRIPPER_PWM_OPEN"), rov_gripper.GRIPPER_PWM_OPEN)
        self.assertEqual(const("GRIPPER_PWM_CLOSE"), rov_gripper.GRIPPER_PWM_CLOSE)


if __name__ == "__main__":
    unittest.main()
