"""Unit test untuk mapping axis joystick -> MANUAL_CONTROL.

Jalankan:
    python3 -m unittest test_manual_control -v
"""
import unittest

from manual_control import (
    NEUTRAL,
    axes_to_manual_control,
    axis_percent_to_manual,
    throttle_percent_to_manual,
)


class TestManualControl(unittest.TestCase):
    def test_neutral(self):
        self.assertEqual(NEUTRAL, {"x": 0, "y": 0, "z": 500, "r": 0, "buttons": 0})

    def test_axis_full_range(self):
        self.assertEqual(axis_percent_to_manual(100), 1000)
        self.assertEqual(axis_percent_to_manual(-100), -1000)
        self.assertEqual(axis_percent_to_manual(0), 0)

    def test_throttle_range(self):
        self.assertEqual(throttle_percent_to_manual(100), 1000)
        self.assertEqual(throttle_percent_to_manual(-100), 0)
        self.assertEqual(throttle_percent_to_manual(0), 500)

    def test_clamp_over_range(self):
        # server harusnya sudah clamp, tapi mapping tetap defensif
        self.assertEqual(axis_percent_to_manual(999), 1000)
        self.assertEqual(axis_percent_to_manual(-999), -1000)
        self.assertEqual(throttle_percent_to_manual(999), 1000)
        self.assertEqual(throttle_percent_to_manual(-999), 0)

    def test_invalid_input_defaults_to_neutral(self):
        self.assertEqual(axis_percent_to_manual(None), 0)
        self.assertEqual(axis_percent_to_manual("abc"), 0)
        self.assertEqual(throttle_percent_to_manual(None), 500)

    def test_axis_assignment(self):
        r = axes_to_manual_control(surge=10, sway=20, yaw=30, heave=40)
        self.assertEqual(r["x"], 100)   # surge -> x
        self.assertEqual(r["y"], 200)   # sway  -> y
        self.assertEqual(r["r"], 300)   # yaw   -> r
        self.assertEqual(r["z"], 700)   # heave -> z (500 + 40*5)

    def test_buttons_masked_to_16bit(self):
        self.assertEqual(axes_to_manual_control(buttons=0x1FFFF)["buttons"], 0xFFFF)


if __name__ == "__main__":
    unittest.main()
