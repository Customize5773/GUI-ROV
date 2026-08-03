"""Unit test validasi payload motor_test (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_motor_test -v
"""

import unittest

from rov_motor_test import validate_motor_test


class TestValidateMotorTest(unittest.TestCase):
    def test_default_forward(self):
        motor, throttle, duration, direction, signed = validate_motor_test({"motor": 3})
        self.assertEqual(motor, 3)
        self.assertEqual(throttle, 15)
        self.assertEqual(duration, 1.0)
        self.assertEqual(direction, "forward")
        self.assertEqual(signed, 15)

    def test_reverse_negates_throttle(self):
        _, throttle, _, direction, signed = validate_motor_test(
            {"motor": 1, "throttle": 10, "direction": "reverse"}
        )
        self.assertEqual(direction, "reverse")
        self.assertEqual(signed, -10)
        self.assertEqual(throttle, 10)

    def test_throttle_diklem_ke_batas_aman(self):
        _, throttle, _, _, signed = validate_motor_test({"motor": 1, "throttle": 999})
        self.assertEqual(throttle, 20)
        self.assertEqual(signed, 20)

    def test_throttle_minimal_1(self):
        _, throttle, _, _, _ = validate_motor_test({"motor": 1, "throttle": 0})
        self.assertEqual(throttle, 1)

    def test_duration_diklem_ke_batas_aman(self):
        _, _, duration, _, _ = validate_motor_test({"motor": 1, "duration": 999})
        self.assertEqual(duration, 2.0)

    def test_duration_minimal(self):
        _, _, duration, _, _ = validate_motor_test({"motor": 1, "duration": 0})
        self.assertEqual(duration, 0.2)

    def test_motor_di_luar_jangkauan_ditolak(self):
        for bad in (0, 7, -1, 100):
            with self.assertRaises(ValueError, msg=bad):
                validate_motor_test({"motor": bad})

    def test_motor_tidak_valid_ditolak(self):
        for bad in (None, "abc", [1]):
            with self.assertRaises((ValueError, TypeError), msg=bad):
                validate_motor_test({"motor": bad})


if __name__ == "__main__":
    unittest.main()
