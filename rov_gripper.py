from pymavlink import mavutil


class GripperController:

    GRIP_CHANNEL = 7
    ROTATE_CHANNEL = 8

    PWM_FORWARD = 1900
    PWM_STOP = 1500
    PWM_REVERSE = 1100

    def __init__(self, master):
        self.master = master

    def _set_pwm(self, channel, pwm):

        print(f"[SERVO] CH{channel} -> {pwm}")

        self.master.mav.command_long_send(
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
            0,
            channel,
            pwm,
            0,
            0,
            0,
            0,
            0,
        )

    # =====================================
    # Grip
    # =====================================

    def open(self):

        print("[GRIPPER] OPEN")

        self._set_pwm(
            self.GRIP_CHANNEL,
            self.PWM_FORWARD
        )

    def close(self):

        print("[GRIPPER] CLOSE")

        self._set_pwm(
            self.GRIP_CHANNEL,
            self.PWM_REVERSE
        )

    def stop(self):

        print("[GRIPPER] STOP")

        self._set_pwm(
            self.GRIP_CHANNEL,
            self.PWM_STOP
        )

    # =====================================
    # Rotate
    # =====================================

    def rotate_left(self):

        print("[ROTATE] LEFT")

        self._set_pwm(
            self.ROTATE_CHANNEL,
            self.PWM_REVERSE
        )

    def rotate_right(self):

        print("[ROTATE] RIGHT")

        self._set_pwm(
            self.ROTATE_CHANNEL,
            self.PWM_FORWARD
        )

    def rotate_stop(self):

        print("[ROTATE] STOP")

        self._set_pwm(
            self.ROTATE_CHANNEL,
            self.PWM_STOP
        )