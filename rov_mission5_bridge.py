"""Menjalankan Mission5FSM lengkap di dalam rov_agent.py.

Bridge ini memakai fungsi command/telemetry langsung dari agent. ArduSub tetap
menangani ALT_HOLD, stabilisasi, mixing, PWM, dan failsafe; FSM hanya memberi
command body-axis bounded kepada agent.
"""

import math
import threading
import time


# ═══ EDIT MOTION CUSTOM DI SINI ═══
# False = Mission5FSM lengkap (return, QR docking, unhook) tetap dipakai.
# True  = jalankan CASE di bawah berurutan.
CUSTOM_MOTION_ENABLED = False

# motion = (surge %, sway %, heading target °, depth target m, gripper)
# gripper: "open", "close", atau "hold". duration_ms harus > 0.
CUSTOM_CASES = [
    {"name": "CASE_M1", "duration_ms": 5000,
     "motion": (30, 0, 0, 0.40, "hold")},
    {"name": "CASE_M2", "duration_ms": 5000,
     "motion": (0, 0, -90, 0.40, "open")},
    {"name": "CASE_M3", "duration_ms": 5000,
     "motion": (0, 0, -90, 0.40, "close")},
    {"name": "CASE_M4", "duration_ms": 5000,
     "motion": (0, 0, -90, 0.40, "hold")},
    {"name": "CASE_M5", "duration_ms": 5000,
     "motion": (0, 0, -90, 0.40, "hold")},
]


DEFAULT_MOTION_CONFIG = {
    "dive_mps": 0.12,
    "ascend_mps": 0.12,
    "surge_mps": 0.21,
    "yaw_dps": 22.5,
}

MOTION_CALIBRATION = {
    "dive_mps_at_100": 0.20,
    "ascend_mps_at_100": 0.20,
    # Uji kolam 25 Agu: command 20% menempuh 4,4 m / 19,86 s = 0,222 m/s.
    # Ekuivalen linear lokalnya 1,11 m/s @100%; dipakai hanya pada rentang UI
    # 0..0,30 m/s, bukan klaim kecepatan maksimum ROV.
    "surge_mps_at_100": 1.11,
    "yaw_dps_at_100": 45.0,
}


class Mission5CommandAdapter:
    def __init__(self, set_axis, set_gripper, arm, emergency_stop,
                 set_alt_hold=None, set_depth_target=None):
        self._set_axis = set_axis
        self._set_gripper = set_gripper
        self._arm = arm
        self._emergency_stop = emergency_stop
        self._set_alt_hold = set_alt_hold
        self._set_depth_target = set_depth_target

    def send(self, surge=0, sway=0, yaw=0, vert=0, gripper=None):
        self._set_axis(surge=int(surge * 10), sway=int(sway * 10),
                       yaw=int(yaw * 10), heave=int(vert * 10))
        if gripper is not None:
            self._set_gripper(bool(gripper))

    def send_motion(self, motion, yaw_command=0):
        """Kirim format CASE lama: (surge, sway, heading, depth, gripper)."""
        surge, sway, _heading, depth, gripper = motion
        self._set_axis(surge=int(surge * 10), sway=int(sway * 10),
                       yaw=int(yaw_command * 10), heave=0)
        if depth is not None:
            if self._set_depth_target is None:
                raise RuntimeError("callback set_depth_target belum dipasang")
            if self._set_depth_target(float(depth)) is False:
                raise RuntimeError(f"depth target {depth} m ditolak")
        if gripper == "open":
            self._set_gripper(False)
        elif gripper == "close":
            self._set_gripper(True)
        elif gripper != "hold":
            raise ValueError(f"gripper tidak valid: {gripper}")

    def arm(self, on=True):
        self._arm(bool(on))

    def stop_all(self):
        self._set_axis(surge=0, sway=0, yaw=0, heave=0)

    def emergency_stop(self):
        self._emergency_stop()

    def set_alt_hold(self):
        return bool(self._set_alt_hold()) if self._set_alt_hold else True

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
    def __init__(self, cmd_adapter, telem_adapter, config=None, log=print):
        self._cmd = cmd_adapter
        self._telem = telem_adapter
        self._cfg = config or {}
        self._log = log
        self._fsm = None
        self._thread = None
        self._lock = threading.Lock()
        self._custom_stop = threading.Event()
        self.custom_enabled = bool(self._cfg.get(
            "custom_motion_enabled", CUSTOM_MOTION_ENABLED))
        self.custom_cases = self._cfg.get("custom_cases", CUSTOM_CASES)
        self._custom_state = "IDLE"
        self._custom_motion = None
        self._custom_elapsed_ms = 0.0
        self.motion_config = dict(DEFAULT_MOTION_CONFIG)
        self.last_error = None

    def _import_autonomy(self):
        from fsm.mission5 import (Mission5FSM, State, QR_SIDE_M,
                                  HOOK_COLOR_HSV_RANGE, HOOK_MIN_AREA,
                                  HOOK_PIPE_DIAM_M)
        from vision.qr_detect import VisionPipeline
        return (Mission5FSM, State, VisionPipeline, QR_SIDE_M,
                HOOK_COLOR_HSV_RANGE, HOOK_MIN_AREA, HOOK_PIPE_DIAM_M)

    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def set_motion_config(self, values):
        if self.is_running():
            raise RuntimeError("motion config ditolak saat Mission 5 berjalan")
        if not isinstance(values, dict):
            raise ValueError("motion config harus object")
        limits = {
            "dive_mps": (0.0, 0.20),
            "ascend_mps": (0.0, 0.20),
            "surge_mps": (0.0, 0.30),
            "yaw_dps": (0.0, 45.0),
        }
        next_config = {}
        for key, (low, high) in limits.items():
            try:
                value = float(values[key])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"{key} tidak valid") from None
            if not low <= value <= high:
                raise ValueError(f"{key} di luar batas {low}..{high}")
            next_config[key] = value
        with self._lock:
            self.motion_config = next_config
        return dict(next_config)

    @staticmethod
    def _heading_control(target, current):
        error = (float(target) - float(current) + 180.0) % 360.0 - 180.0
        return 0 if abs(error) < 2.0 else max(-30, min(30, round(error * 3)))

    @staticmethod
    def _validate_custom_cases(cases):
        if not isinstance(cases, (list, tuple)) or not cases:
            raise ValueError("CUSTOM_CASES harus berisi minimal satu CASE")
        for case in cases:
            if not isinstance(case, dict) or not str(case.get("name", "")).strip():
                raise ValueError("setiap CASE wajib punya name")
            duration = float(case.get("duration_ms", 0))
            motion = case.get("motion")
            if not math.isfinite(duration) or not 0 < duration <= 600_000:
                raise ValueError(f"{case.get('name')}: duration_ms harus 1..600000")
            if not isinstance(motion, (list, tuple)) or len(motion) != 5:
                raise ValueError(f"{case.get('name')}: motion harus 5 nilai")
            surge, sway, heading, depth, gripper = motion
            nums = (float(surge), float(sway), float(heading))
            if not all(math.isfinite(v) for v in nums):
                raise ValueError(f"{case.get('name')}: motion bukan angka valid")
            if not -100 <= nums[0] <= 100 or not -100 <= nums[1] <= 100:
                raise ValueError(f"{case.get('name')}: surge/sway harus -100..100")
            if depth is not None and (not math.isfinite(float(depth)) or not 0 <= float(depth) <= 10):
                raise ValueError(f"{case.get('name')}: depth harus 0..10 m atau None")
            if gripper not in ("open", "close", "hold"):
                raise ValueError(f"{case.get('name')}: gripper tidak valid")

    def _start_custom(self):
        try:
            self._validate_custom_cases(self.custom_cases)
        except (TypeError, ValueError) as exc:
            self.last_error = str(exc)
            self._log(f"[CUSTOM] TIDAK BISA START — {exc}")
            return False
        self._cmd.stop_all()
        if not self._cmd.set_alt_hold():
            self.last_error = "ALT_HOLD gagal"
            self._log("[CUSTOM] ABORT — ALT_HOLD gagal")
            return False
        self._custom_stop.clear()
        self._custom_state = "STARTING"

        def run():
            try:
                for case in self.custom_cases:
                    if self._custom_stop.is_set():
                        break
                    self._custom_state = str(case["name"])
                    self._custom_motion = tuple(case["motion"])
                    started = time.monotonic()
                    duration_ms = float(case["duration_ms"])
                    self._log(f"[CUSTOM] {self._custom_state} -> {self._custom_motion}")
                    while not self._custom_stop.is_set():
                        self._custom_elapsed_ms = (time.monotonic() - started) * 1000.0
                        if self._custom_elapsed_ms >= duration_ms:
                            break
                        heading = self._telem.get().get("heading", 0.0)
                        yaw = self._heading_control(self._custom_motion[2], heading)
                        self._cmd.send_motion(self._custom_motion, yaw_command=yaw)
                        self._custom_stop.wait(0.05)
                self._custom_state = "STOPPED" if self._custom_stop.is_set() else "COMPLETE"
            except Exception as exc:
                self.last_error = str(exc)
                self._custom_state = "ERROR"
                self._log(f"[CUSTOM] ERROR: {exc}")
            finally:
                self._cmd.stop_all()
                self._log(f"[CUSTOM] selesai state={self._custom_state}")

        with self._lock:
            self._thread = threading.Thread(target=run, daemon=True,
                                            name="Mission5CustomMotion")
            self._thread.start()
        self._log("[CUSTOM] Mission START")
        return True

    def _apply_motion_config(self, module):
        def axis(value, calibration):
            if value <= 0:
                return 0
            return max(1, min(100, round(100 * value / calibration)))

        module.DIVE_SPEED = axis(self.motion_config["dive_mps"], MOTION_CALIBRATION["dive_mps_at_100"])
        module.ASCEND_SPEED = axis(self.motion_config["ascend_mps"], MOTION_CALIBRATION["ascend_mps_at_100"])
        module.SURGE_SPEED = axis(self.motion_config["surge_mps"], MOTION_CALIBRATION["surge_mps_at_100"])
        module.YAW_SPEED = axis(self.motion_config["yaw_dps"], MOTION_CALIBRATION["yaw_dps_at_100"])
        # "Maju" juga mengatur gerak menyusur dinding pada M5_SEARCH. Sebelumnya
        # SEARCH_SPEED tetap hardcoded sehingga setting GUI tidak memengaruhinya.
        module.SEARCH_SPEED = module.SURGE_SPEED
        module.SCAN_CREEP_MAX_SPEED = (
            0 if module.SURGE_SPEED == 0
            else max(1, min(module.SURGE_SPEED, round(module.SURGE_SPEED * 0.6)))
        )
        self._log("[M5] physical motion mapped: "
                  f"dive={module.DIVE_SPEED}% ascend={module.ASCEND_SPEED}% "
                  f"surge={module.SURGE_SPEED}% yaw={module.YAW_SPEED}% "
                  f"search={module.SEARCH_SPEED}% scan_creep={module.SCAN_CREEP_MAX_SPEED}%")

    def start(self):
        self.last_error = None
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._log("[M5] start dilewati — thread FSM masih hidup")
                return False

        if self.custom_enabled:
            return self._start_custom()

        try:
            (Mission5FSM, State, VisionPipeline, QR_SIDE_M,
             HOOK_COLOR_HSV_RANGE, HOOK_MIN_AREA,
             HOOK_PIPE_DIAM_M) = self._import_autonomy()
        except Exception as exc:
            self.last_error = f"autonomy/vision gagal: {exc}"
            self._log(f"[M5] TIDAK BISA START — {self.last_error}")
            return False

        cfg = self._cfg
        read_mark = cfg.get("read_mark", lambda: (None, None))
        marked_heading, marked_depth = read_mark()
        start_state = getattr(State, cfg.get("start_state", "M5_REDIVE"))
        if start_state == State.M5_REDIVE and (
                marked_heading is None or marked_depth is None or not marked_depth > 0):
            self.last_error = "MARK gantungan wajib sebelum AUTONOMOUS"
            self._log(f"[M5] TIDAK BISA START — {self.last_error}")
            return False

        import fsm.mission5 as mission5_module
        self._apply_motion_config(mission5_module)

        vision = None
        try:
            vision = VisionPipeline(
                source=cfg.get("vision_source", "usb"),
                device=cfg.get("vision_device", 0),
                # Kamera WALL segaris dengan gripper: satu stream untuk QR
                # docking sekaligus hook, tanpa membuka URL yang sama dua kali.
                qr_url=cfg.get("wall_url"),
                hook_url=None,
                calib_file=cfg.get("calib_wall"),
                qr_length=cfg.get("qr_size", QR_SIDE_M),
                hook_hsv_range=HOOK_COLOR_HSV_RANGE,
                hook_min_area=HOOK_MIN_AREA,
                hook_pipe_diam=HOOK_PIPE_DIAM_M,
                wall_cnn=cfg.get("wall_cnn", True),
            )
            vision.start()
            if not self._cmd.set_alt_hold():
                vision.stop()
                self.last_error = "ALT_HOLD gagal"
                self._log(f"[M5] ABORT — {self.last_error}")
                return False
        except Exception as exc:
            self.last_error = f"vision/ALT_HOLD gagal: {exc}"
            self._log(f"[M5] {self.last_error}")
            if vision is not None:
                try:
                    vision.stop()
                except Exception:
                    pass
            return False

        fsm = Mission5FSM(cmd=self._cmd, telem=self._telem, vision=vision,
                          marked_heading=marked_heading,
                          marked_depth=marked_depth,
                          hook_map_file=cfg.get("hook_map"),
                          hook_calib_file=(cfg.get("calib_wall")
                                           if cfg.get("hook_map") else None))

        def run():
            try:
                fsm.start(start_state=start_state, wait_mode=False)
            except Exception as exc:
                self._log(f"[M5] FSM berhenti karena error: {exc}")
            finally:
                try:
                    vision.stop()
                except Exception:
                    pass
                self._log("[M5] thread FSM selesai")

        with self._lock:
            self._fsm = fsm
            self._thread = threading.Thread(target=run, daemon=True, name="Mission5FSM")
            self._thread.start()
        self._log(f"[M5] Mission5 FSM dimulai (start_state={start_state.name})")
        return True

    def stop(self):
        self._custom_stop.set()
        with self._lock:
            fsm, thread = self._fsm, self._thread
            self._fsm = None
            self._thread = None
        if fsm is not None:
            try:
                fsm.abort()
            except Exception as exc:
                self._log(f"[M5] error saat abort: {exc}")
        if thread:
            thread.join(timeout=2)
        self._cmd.stop_all()
        self._log("[M5] Mission5 FSM dihentikan")

    def state_name(self):
        if self.custom_enabled:
            return self._custom_state
        fsm = self._fsm
        if fsm is None:
            return None
        try:
            return fsm.telemetry_out.get("state")
        except Exception:
            return None

    def telemetry(self):
        fsm = self._fsm
        data = dict(getattr(fsm, "telemetry_out", {}) or {}) if fsm else {}
        data.setdefault("state", self.state_name())
        data["running"] = self.is_running()
        data["hook_map_enabled"] = bool(self._cfg.get("hook_map"))
        data["custom_mode"] = self.custom_enabled
        if self.custom_enabled:
            data["motion"] = self._custom_motion
            data["elapsed_ms"] = round(self._custom_elapsed_ms, 1)
        data["motion_config"] = dict(self.motion_config)
        data["motion_calibration"] = dict(MOTION_CALIBRATION)
        return data
