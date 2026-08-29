"""
rov_mission5_bridge.py

Autonomous Mission FSM sederhana untuk ROV.

Format motion:
    motion = (surge, sway, heading_set, depth_set)

Setiap CASE memiliki motion sendiri.
Tidak ada AUTO_SURGE / AUTO_DEPTH global.

ArduSub ALT_HOLD tetap controller depth.
depth_set dikirim ke native ArduSub melalui callback set_depth_target.
"""

import threading
import time


class Mission5CommandAdapter:
    def __init__(
        self,
        set_axis,
        set_gripper,
        arm,
        emergency_stop,
        set_alt_hold=None,
        set_depth_target=None,
    ):
        self._set_axis = set_axis
        self._set_gripper = set_gripper
        self._arm = arm
        self._emergency_stop = emergency_stop
        self._set_alt_hold = set_alt_hold
        self._set_depth_target = set_depth_target

    def send_motion(self, motion):
        """motion = (surge, sway, heading_set, depth_set)."""
        if len(motion) != 4:
            raise ValueError("motion harus (surge, sway, heading_set, depth_set)")

        surge, sway, yaw, depth_set = motion

        # Axis autonomous: skala FSM -100..100 -> rov_agent -1000..1000.
        self._set_axis(
            surge=int(surge * 10),
            sway=int(sway * 10),
            yaw=int(yaw * 10),
            heave=0,
        )

        # depth_set adalah TARGET, bukan heave.
        if depth_set is not None and self._set_depth_target is not None:
            self._set_depth_target(depth_set)

    def send(self, surge=0, sway=0, yaw=0, vert=0, gripper=None):
        """Kompatibilitas API lama; gunakan send_motion() untuk mission baru."""
        self._set_axis(
            surge=int(surge * 10),
            sway=int(sway * 10),
            yaw=int(yaw * 10),
            heave=int(vert * 10),
        )
        if gripper is not None:
            self._set_gripper(bool(gripper))

    def arm(self, on=True):
        self._arm(bool(on))

    def set_alt_hold(self):
        if self._set_alt_hold is None:
            return False
        return bool(self._set_alt_hold())

    def stop_all(self):
        self._set_axis(surge=0, sway=0, yaw=0, heave=0)

    def emergency_stop(self):
        self._emergency_stop()

    def close(self):
        pass


class Mission5TelemetryAdapter:
    def __init__(self, read_state):
        self._read_state = read_state

    def start(self):
        pass

    def stop(self):
        pass

    def get(self):
        return self._read_state()


class Mission5Runner:
    """
    FSM non-blocking.

    CASE_FORWARD:
        motion = (30, 0, 0, 1.5)
        selama 3000 ms

    CASE_REVERSE:
        motion = (-30, 0, 0, 1.5)
        selama 2000 ms

    CASE_STOP:
        motion = (0, 0, 0, 1.5)
        selesai

    counter:
        0 = start
        1 = forward selesai
        2 = reverse selesai
        3 = complete
    """

    def __init__(self, cmd_adapter, telem_adapter, config=None, log=print):
        self._cmd = cmd_adapter
        self._telem = telem_adapter
        self._cfg = config or {}
        self._log = log

        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.state = "IDLE"
        self.counter = 0
        self.motion = (0, 0, 0, None)

        self._case_started = None
        self._state_elapsed_ms = 0.0

    def _heading_control(self, target_heading, current_heading):
        error = target_heading - current_heading

        # Normalisasi error ke -180 ... +180
        if error > 180:
            error -= 360
        elif error < -180:
            error += 360

        # Untuk awal kita gunakan proportional sederhana.
        yaw = int(error * 3)

        # Limit command yaw
        yaw = max(-30, min(30, yaw))

        # Deadband
        if abs(error) < 2:
            yaw = 0

        return yaw

    def available(self):
        return True

    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _transition(self, new_state):
        with self._lock:
            self.state = new_state
            self._case_started = time.monotonic()
            self._state_elapsed_ms = 0.0

        self._log(f"[AUTO] STATE -> {new_state}")

    def _apply_motion(self, motion):
        surge, sway, heading_set, depth_set = motion

        current_heading = self._telem.get().get("heading", 0)

        yaw = self._heading_control(
            heading_set,
            current_heading
        )

        command_motion = (
            surge,
            sway,
            yaw,
            depth_set,
        )

        self.motion = motion
        self._cmd.send_motion(command_motion)

    def _case_forward(self):
        # CASE sendiri: motion langsung ditulis di sini.
        motion = (0, 0, 90, 0.4)

        self._apply_motion(motion)

        elapsed_ms = (time.monotonic() - self._case_started) * 1000.0
        self._state_elapsed_ms = elapsed_ms

        if elapsed_ms >= 5000:
            self._cmd.stop_all()
            self.counter += 1
            self._log(
                f"[AUTO] FORWARD selesai "
                f"elapsed={elapsed_ms:.1f} ms counter={self.counter}"
            )
            self._transition("CASE_REVERSE")

    def _case_reverse(self):
        # Reverse = negatif langsung dari surge forward.
        motion = (0, 0, -90, 0.4)

        self._apply_motion(motion)

        elapsed_ms = (time.monotonic() - self._case_started) * 1000.0
        self._state_elapsed_ms = elapsed_ms

        if elapsed_ms >= 5000:
            self._cmd.stop_all()
            self.counter += 1
            self._log(
                f"[AUTO] REVERSE selesai "
                f"elapsed={elapsed_ms:.1f} ms counter={self.counter}"
            )
            self._transition("CASE_STOP")

    def _case_stop(self):
        # Tetap di depth target, semua gerakan lain netral.
        motion = (0, 0, 0, 0)

        self._apply_motion(motion)

        self.counter += 1
        self._log(
            f"[AUTO] STOP — motion={motion} counter={self.counter}"
        )
        self._transition("COMPLETE")

    def _update(self):
        if self.state == "CASE_FORWARD":
            self._case_forward()

        elif self.state == "CASE_REVERSE":
            self._case_reverse()

        elif self.state == "CASE_STOP":
            self._case_stop()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._log("[AUTO] start diabaikan — mission masih berjalan")
                return False

        self._stop_event.clear()
        self.counter = 0
        self.motion = (0, 0, 0, None)

        # Pastikan output autonomous lama tidak terbawa.
        self._cmd.stop_all()

        # ALT_HOLD harus berhasil sebelum case pertama.
        if not self._cmd.set_alt_hold():
            self._cmd.stop_all()
            self.state = "ABORTED"
            self._log("[AUTO] ABORT — ALT_HOLD gagal")
            return False

        # Set depth awal mission melalui motion pertama.
        self._transition("CASE_FORWARD")

        with self._lock:
            self._thread = threading.Thread(
                target=self._run,
                name="AutonomousMission",
                daemon=True,
            )
            self._thread.start()

        self._log("[AUTO] Mission START")
        return True

    def _run(self):
        try:
            while not self._stop_event.is_set():
                if self.state == "COMPLETE":
                    break

                self._update()
                time.sleep(0.02)  # 50 Hz FSM
        except Exception as exc:
            self._log(f"[AUTO] ERROR: {exc}")
            self._cmd.stop_all()
            self.state = "ERROR"
        finally:
            self._cmd.stop_all()
            self._log(
                f"[AUTO] thread selesai state={self.state} "
                f"counter={self.counter}"
            )

    def stop(self):
        self._stop_event.set()
        self._cmd.stop_all()

        with self._lock:
            thread = self._thread
            self._thread = None

        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

        self.state = "STOPPED"
        self.motion = (0, 0, 0, None)
        self._log("[AUTO] Mission STOPPED")

    def telemetry(self):
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()

        return {
            "state": self.state,
            "running": running,
            "counter": self.counter,
            "motion": self.motion,
            "elapsed_ms": self._state_elapsed_ms,
        }
