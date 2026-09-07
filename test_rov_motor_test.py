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


class TestMotorTestWire(unittest.TestCase):
    def test_board_index_dan_pwm_reversible(self):
        import ast
        import threading
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock
        tree = ast.parse(Path(__file__).with_name('rov_agent.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == 'run_motor_test')
        mav = Mock()
        constants = SimpleNamespace(MAV_CMD_DO_MOTOR_TEST=209,
                                    MOTOR_TEST_THROTTLE_PWM=1,
                                    MOTOR_TEST_ORDER_BOARD=2)
        env = {'validate_motor_test': validate_motor_test,
               'master_lock': threading.Lock(),
               'master': SimpleNamespace(target_system=1,target_component=1,mav=mav),
               'mavutil': SimpleNamespace(mavlink=constants), 'send_to_gui': Mock()}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'motor_test','exec'),env)
        for motor, direction, throttle, pwm in [(6,'forward',10,1550),
                                               (1,'reverse',10,1450),
                                               (6,'forward',999,1600),
                                               (6,'reverse',999,1400)]:
            with self.subTest(motor=motor,direction=direction,throttle=throttle):
                env['run_motor_test']({'motor':motor,'direction':direction,
                                       'throttle':throttle,'duration':0.5})
                self.assertEqual(mav.command_long_send.call_args.args,
                                 (1,1,209,0,motor-1,1,pwm,0.5,0,2,0))


if __name__ == "__main__":
    unittest.main()
