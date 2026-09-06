"""Menjalankan Mission5FSM lengkap di dalam rov_agent.py.

Bridge ini memakai fungsi command/telemetry langsung dari agent. ArduSub tetap
menangani ALT_HOLD, stabilisasi, mixing, PWM, dan failsafe; FSM hanya memberi
command body-axis bounded kepada agent.
"""

import math
import os
import threading
import time


# ═══ EDIT MOTION CUSTOM DI SINI ═══
# True  = jalankan CASE di bawah (langkah 1-2 misi 5; tambah case bila perlu)
#         lalu SERAHKAN ke Mission5FSM di M5_YOLO_SEARCH untuk langkah 3-8
#         (YOLO → ujung J → QR → grip → unhook → surface). INI JALUR LOMBA.
#         Di M5_YOLO_SEARCH, bila QR payload sudah ter-decode, FSM lompat
#         langsung ke M5_QR_DOCK — M5_HOOK_ALIGN hanya untuk MENEMUKAN
#         gantungan saat QR belum terbaca.
# False = full motion dari file ini saja: SELURUH CUSTOM_CASES dijalankan
#         (motion terjadwal dari Pi), lalu stop + disarm. Mission5FSM TIDAK
#         pernah jalan — rute cadangan open-loop bila vision gagal di hari-H.
# Pengecualian: bench_qr_dock=True melewati CASE sepenuhnya dan masuk
# Mission5FSM langsung di M5_QR_DOCK (rig uji satu-state).
CUSTOM_MOTION_ENABLED = False

# motion = (surge %, sway %, heading target °, depth target m, gripper)
# gripper: "open", "close", atau "hold". duration_ms harus > 0.
# Saat AUTONOMOUS diklik, arah ROV saat itu otomatis menjadi heading 0 derajat.
# Nilai heading dan depth setiap tahap diambil langsung dari tuple motion ini.
CUSTOM_CASES = [
    # Langkah 1: turun ke kedalaman hook sambil maju sedikit + serong kanan.
    {"name": "CASE_M1", "duration_ms": 5000,
     "motion": (25, 0, 0, 0.35, "open")},
    # Langkah 2: putar 180 derajat dari arah saat AUTONOMOUS diklik.
    # PENTING: kolom depth adalah TARGET ABSOLUT (lihat send_motion) — 0.0
    # berarti "naik ke PERMUKAAN", bukan "pertahankan kedalaman". Nilai 0.0 di
    # sini dulu tak pernah terasa karena jalur CASE tidak pernah ARM sehingga
    # ROV tak bergerak sama sekali; setelah ARM diperbaiki, ROV terukur melesat
    # 0,45 -> 0,10 m di tengah putaran (uji 04:58). Tahan 0,40 m selama memutar
    # dan mendekat, lalu FSM langkah 3-8 memakai hook_depth-nya sendiri.
    {"name": "CASE_M2", "duration_ms": 1000,
     "motion": (0, 0, 90, 0.35, "hold")},
    {"name": "CASE_M2", "duration_ms": 5000,
     "motion": (0, 0, 180, 0.35, "hold")},
    {"name": "CASE_M4", "duration_ms": 9700,
     "motion": (0, -100, 180, 0.35, "open")},
    {"name": "CASE_M5", "duration_ms": 7000,
     "motion": (15, 0, 180, 0.35, "hold")},
    {"name": "CASE_M6", "duration_ms": 5000,
     "motion": (10, 0, 180, 0.35, "close")},
    {"name": "CASE_M7", "duration_ms": 7000,
     "motion": (-35, 0, 180, 0.0, "close")},
    {"name": "CASE_M8", "duration_ms": 9000,
     "motion": (20, 0, 180, 0.0, "close")},
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
                 set_alt_hold=None, set_depth_target=None, invert_vert=False,
                 invert_yaw=False):
        self._set_axis = set_axis
        self._set_gripper = set_gripper
        self._arm = arm
        self._emergency_stop = emergency_stop
        self._set_alt_hold = set_alt_hold
        self._set_depth_target = set_depth_target
        # Konvensi FSM: vert positif = naik. Beberapa instalasi ArduSub/wiring
        # memakai tanda heave kebalikan; balikkan hanya di batas FSM agar semua
        # tahap (align, unhook, surface) konsisten tanpa mengubah kontrol manual.
        self._invert_vert = bool(invert_vert)
        # Sama untuk yaw: konvensi FSM positif = target/putaran ke kanan. Pada
        # instalasi kolam ini uji live menunjukkan tanda MANUAL_CONTROL.r fisik
        # terbalik, jadi kalibrasi dilakukan di satu batas dan tidak mengubah
        # arah joystick operator.
        self._invert_yaw = bool(invert_yaw)

    def send(self, surge=0, sway=0, yaw=0, vert=0, gripper=None):
        mapped_vert = -vert if self._invert_vert else vert
        mapped_yaw = -yaw if self._invert_yaw else yaw
        self._set_axis(surge=int(surge * 10), sway=int(sway * 10),
                       yaw=int(mapped_yaw * 10), heave=int(mapped_vert * 10))
        if gripper is not None:
            self._set_gripper(bool(gripper))

    def send_motion(self, motion, yaw_command=0):
        """Kirim format CASE lama: (surge, sway, heading, depth, gripper)."""
        surge, sway, _heading, depth, gripper = motion
        mapped_yaw = -yaw_command if self._invert_yaw else yaw_command
        self._set_axis(surge=int(surge * 10), sway=int(sway * 10),
                       yaw=int(mapped_yaw * 10), heave=0)
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
        self._last_case_heading = None   # heading relatif CASE terakhir → FSM
        self.bench_qr_dock = bool(self._cfg.get("bench_qr_dock", False))
        self.custom_enabled = (not self.bench_qr_dock and bool(self._cfg.get(
            "custom_motion_enabled", CUSTOM_MOTION_ENABLED)))
        self.custom_cases = self._cfg.get("custom_cases", CUSTOM_CASES)
        self._custom_state = "IDLE"
        self._custom_motion = None
        self._custom_elapsed_ms = 0.0
        self.motion_config = dict(DEFAULT_MOTION_CONFIG)
        self.last_error = None
        self._run_log_path = None

    def _new_runlog(self, start_state, marked_heading=None, marked_depth=None):
        self._run_log_path = None
        try:
            try:
                from tools.run_log import RunLogger
            except ImportError:
                from autonomy.tools.run_log import RunLogger
            stamp = time.strftime("%Y%m%d_%H%M%S")
            stamp += f"_{int(time.time() * 1000) % 1000:03d}"
            path = os.path.join(self._cfg.get("run_log_dir", "logs"),
                                f"run_{stamp}.jsonl")
            runlog = RunLogger(path)
            self._run_log_path = path
            runlog.event("config", start_state=getattr(start_state, "name", str(start_state)),
                         marked_heading=marked_heading, marked_depth=marked_depth,
                         bench_qr_dock=self.bench_qr_dock,
                         motion_config=dict(self.motion_config),
                         hook_map=self._cfg.get("hook_map"))
            self._log(f"[M5] Run log: {path}")
            return runlog
        except Exception as exc:
            self._log(f"[M5] WARNING — run log gagal dibuat: {exc}")
            return None

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

        if abs(error) < 2.0:
            return 0

        return max(-60, min(60, round(error * 3)))

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

    def _start_custom(self, handover=True):
        try:
            self._validate_custom_cases(self.custom_cases)
        except (TypeError, ValueError) as exc:
            self.last_error = str(exc)
            self._log(f"[CUSTOM] TIDAK BISA START — {exc}")
            return False
        if self._cfg.get("require_hook_map", False):
            hook_map = self._cfg.get("hook_map")
            if not hook_map or not os.path.isfile(hook_map):
                self.last_error = "hook map wajib dan file harus tersedia"
                self._log("[CUSTOM] TIDAK BISA START - " + self.last_error)
                return False

        self._cmd.stop_all()
        if not self._cmd.set_alt_hold():
            self.last_error = "ALT_HOLD gagal"
            self._log("[CUSTOM] ABORT — ALT_HOLD gagal")
            return False
        # ARM WAJIB di sini. Tanpa ini seluruh langkah 1-2 berjalan disarmed:
        # send_motion() tetap mengirim axis, tapi ArduSub menahan semua thruster
        # di 1500 sehingga ROV DIAM total — terukur pada uji 04:51 (surge 60%,
        # sway 80%, yaw 45% selama 13 detik, heading hanya bergerak 0,0 -> 0,2
        # derajat dan PWM tak pernah lepas dari netral). FSM langkah 3-8 punya
        # arm() sendiri; jalur CASE ini sebelumnya tak pernah memilikinya.
        # Mission5CommandAdapter.arm() tak mengembalikan status (void), jadi
        # keberhasilannya diverifikasi lewat telemetry `armed`, bukan nilai balik.
        self._cmd.arm(True)
        runlog = self._new_runlog("CUSTOM")
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
                    if runlog:
                        runlog.event("custom_case", name=self._custom_state,
                                     duration_ms=duration_ms, motion=self._custom_motion)
                    while not self._custom_stop.is_set():
                        self._custom_elapsed_ms = (time.monotonic() - started) * 1000.0
                        if self._custom_elapsed_ms >= duration_ms:
                            break
                        target_heading = float(self._custom_motion[2]) % 360.0
                        self._last_case_heading = target_heading

                        heading = self._telem.get().get("heading", 0.0)
                        yaw = self._heading_control(target_heading, heading)
                        self._cmd.send_motion(self._custom_motion, yaw_command=yaw)
                        self._custom_stop.wait(0.05)
                self._custom_state = "STOPPED" if self._custom_stop.is_set() else "COMPLETE"
            except Exception as exc:
                self.last_error = str(exc)
                self._custom_state = "ERROR"
                self._log(f"[CUSTOM] ERROR: {exc}")
            finally:
                self._cmd.stop_all()
                # Sukses -> JANGAN disarm: FSM langkah 3-8 mengambil alih dalam
                # hitungan detik dan akan arm sendiri; disarm di sini hanya
                # menciptakan jeda mati di tengah serah terima. Batal/error ->
                # wajib disarm, jangan tinggalkan wahana hidup tanpa pengendali.
                if (not handover or self._custom_state != "COMPLETE"
                        or self._custom_stop.is_set()):
                    self._cmd.arm(False)
                if runlog:
                    alasan = ("dibatalkan" if self._custom_stop.is_set() else
                              "error" if self._custom_state == "ERROR" else "selesai")
                    runlog.close(alasan=alasan,
                                 state_akhir=self._custom_state)
                self._log(f"[CUSTOM] selesai state={self._custom_state}")

            # Langkah 1-2 tuntas → serahkan ke FSM untuk langkah 3-8, membawa
            # heading CASE terakhir sbg acuan yang ditahan selagi mencari hook.
            # Cek _custom_stop LAGI di sini: kill-switch yang menyala tepat di
            # batas serah terima tak boleh malah memulai FSM.
            if handover and self._custom_state == "COMPLETE" and not self._custom_stop.is_set():
                self._log("[CUSTOM] CASE selesai → serah terima ke FSM "
                          f"(M5_YOLO_SEARCH, heading_hold={self._last_case_heading})")
                with self._lock:
                    self._thread = None      # lepaskan slot agar _start_fsm bisa mulai
                self._start_fsm("M5_YOLO_SEARCH", heading_hold=self._last_case_heading)

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

    def _apply_configs(self):
        """Terapkan tuning/geometri arena sebelum membuat servo dan FSM."""
        paths = [p.strip() for p in (self._cfg.get("config_files") or "").split(",")
                 if p.strip()]
        if not paths:
            return
        try:
            from config.loader import apply_config, load_config
            import fsm.mission5 as module
        except Exception as exc:
            self._log(f"[M5] loader config tidak tersedia: {exc} — pakai default")
            return
        changed = set()
        for requested in paths:
            path = requested
            if not os.path.isfile(path) and not os.path.isabs(path):
                packaged = os.path.join(os.path.dirname(__file__), "autonomy", path)
                if os.path.isfile(packaged):
                    path = packaged
            try:
                applied = apply_config(vars(module), load_config(path))
            except Exception as exc:
                self._log(f"[M5] config GAGAL dimuat: {requested}: {exc} — dilewati")
                continue
            changed.update(name for name, _old, _new in applied)
            self._log(f"[M5] config dimuat: {requested} ({len(applied)} nilai)")
        try:
            module._derive_depths(changed)
        except Exception as exc:
            self._log(f"[M5] geometri config tidak valid: {exc} — pakai nilai terakhir")

    def start(self):
        self.last_error = None
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._log("[M5] start dilewati — thread FSM masih hidup")
                return False

        # Bench QR docking adalah rig uji satu-state: ia memang harus masuk
        # Mission5FSM langsung di M5_QR_DOCK, tanpa motion CASE apa pun.
        if self.bench_qr_dock:
            return self._start_fsm()
        # Sisanya SELALU lewat CASE. Yang dibedakan CUSTOM_MOTION_ENABLED cuma
        # apakah FSM mengambil alih setelahnya:
        #   True  -> CASE langkah 1-2, lalu serah terima ke Mission5FSM (jalur lomba)
        #   False -> full motion dari file ini saja, FSM tidak pernah jalan
        return self._start_custom(handover=self.custom_enabled)

    def _start_fsm(self, start_state_name=None, heading_hold=None):
        """Jalankan Mission5FSM. Dipanggil start() (langsung) DAN rantai CASE."""
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
        start_state = (State.M5_QR_DOCK if self.bench_qr_dock else
                       getattr(State, start_state_name
                               or cfg.get("start_state", "M5_REDIVE")))
        if start_state == State.M5_REDIVE and (
                marked_heading is None or marked_depth is None or not marked_depth > 0):
            self.last_error = "MARK gantungan wajib sebelum AUTONOMOUS"
            self._log(f"[M5] TIDAK BISA START — {self.last_error}")
            return False

        import fsm.mission5 as mission5_module
        self._apply_configs()
        self._apply_motion_config(mission5_module)

        vision = None
        try:
            vision = VisionPipeline(
                source=cfg.get("vision_source", "usb"),
                device=cfg.get("vision_device", 0),
                # YOLO hook tetap berasal dari CAM WALL lewat hook_vision laptop.
                # QR docking wajib memakai CAM BOTTOM yang menghadap gripper;
                # sebelumnya bottom_url/calib_bottom sudah dikonfigurasi agent
                # tetapi tidak pernah dipakai oleh bridge.
                qr_url=cfg.get("bottom_url"),
                # QR 4x4 cm (spesifikasi KKI, tak bisa diperbesar) hanya ~3 px
                # per modul dari jarak kerja gripper — satu kamera sering gagal
                # decode di air keruh. VisionPipeline menjalankan decode QR di
                # KEDUA loop kamera, jadi CAM WALL dipasang sebagai sumber QR
                # kedua: sudut & jaraknya berbeda, siapa pun yang lolos duluan
                # dipakai. hook_enabled=False tetap berlaku — CAM WALL di sini
                # murni pembaca QR, deteksi hook tetap milik worker YOLO laptop.
                hook_url=cfg.get("wall_url"),
                calib_file=cfg.get("calib_bottom"),
                qr_length=cfg.get("qr_size", mission5_module.QR_SIDE_M),
                hook_hsv_range=mission5_module.HOOK_COLOR_HSV_RANGE,
                hook_min_area=mission5_module.HOOK_MIN_AREA,
                hook_pipe_diam=mission5_module.HOOK_PIPE_DIAM_M,
                # Hook berasal dari worker YOLO di laptop melalui hook_vision.
                # Jangan jalankan detector hook OpenCV/YOLO kedua di Raspberry Pi.
                hook_enabled=False,
                # Jangan muat fallback CNN lain di Pi. Ini hanya tebakan sisi
                # saat QR gagal, bukan hasil decode yang boleh dipercaya FSM.
                wall_cnn=False,
            )
            vision.start()
            if not self.bench_qr_dock and not self._cmd.set_alt_hold():
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
                          heading_hold=heading_hold,
                          bench_qr_dock=self.bench_qr_dock,
                          yolo_source=lambda: self._telem.get().get("hook_vision"),
                          # Worker QR laptop; VisionPipeline di atas TETAP jalan
                          # sebagai fallback bila link laptop putus.
                          qr_source=lambda: self._telem.get().get("qr_vision"),
                          hook_map_file=cfg.get("hook_map"),
                          hook_calib_file=(cfg.get("calib_wall")
                                           if cfg.get("hook_map") else None))
        runlog = self._new_runlog(start_state, marked_heading, marked_depth)
        if runlog and start_state == State.M5_YOLO_SEARCH:
            names = (
                "LEFT_ADVANCE_MAX_T", "LEFT_TIMEOUT_ALIGN", "LEFT_YOLO_CONF",
                "LEFT_YOLO_AREA_FRAC", "HOOK_TIP_X_FRAC", "HOOK_TIP_Y_FRAC",
                "LEFT_HOOK_YAW_KP", "LEFT_HOOK_MAX_YAW",
                "LEFT_QR_YAW_KP", "LEFT_QR_YAW_KP_DEG", "LEFT_QR_YAW_TOL_DEG",
                "LEFT_QR_MAX_YAW", "LEFT_GRIP_T", "LEFT_VISUAL_MAX_SURGE",
                "LEFT_VISUAL_MAX_SWAY", "LEFT_VISUAL_MAX_YAW",
                "LEFT_VISUAL_MAX_VERT", "LEFT_VISUAL_SLEW",
            )
            runlog.event("left_flow_config", heading_hold=heading_hold,
                         values={name: getattr(mission5_module, name) for name in names})
        fsm.runlog = runlog

        def run():
            alasan = "selesai"
            try:
                fsm.start(start_state=start_state, wait_mode=False)
            except Exception as exc:
                alasan = f"error: {exc}"
                self._log(f"[M5] FSM berhenti karena error: {exc}")
            finally:
                try:
                    vision.stop()
                except Exception:
                    pass
                if runlog:
                    state_akhir = getattr(fsm._state, "name", str(fsm._state))
                    runlog.close(alasan=("dibatalkan" if state_akhir == "ABORT" else alasan),
                                 state_akhir=state_akhir, skor=fsm.score(),
                                 target_wall=fsm._target_wall,
                                 hang_used_fallback=fsm._hang_used_fallback,
                                 dock_used_fallback=fsm._dock_used_fallback)
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
        # Setelah rantai CASE→FSM, yang berlaku adalah state FSM. Tanpa cek _fsm
        # lebih dulu, badge GUI membeku di "COMPLETE" sepanjang fase YOLO.
        fsm = self._fsm
        if fsm is None:
            return self._custom_state if self.custom_enabled else None
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
        data["bench_qr_dock"] = self.bench_qr_dock
        if self.custom_enabled:
            data["motion"] = self._custom_motion
            data["elapsed_ms"] = round(self._custom_elapsed_ms, 1)
        data["motion_config"] = dict(self.motion_config)
        data["motion_calibration"] = dict(MOTION_CALIBRATION)
        data["run_log"] = self._run_log_path
        return data