"""
rov_mission5_bridge.py — Menjalankan Mission5FSM DI DALAM rov_agent.py.

Kenapa modul ini ada
--------------------
Ada dua program sisi-ROV di repo ini, dan sampai 22 Agu 2026 hanya satu yang
punya FSM:

  * `rov_agent.py`      — yang BENAR-BENAR jalan di Pi (rov-agent.service).
                          Kontrol manual matang: depth-hold, heading-hold,
                          gripper slew-rate, fail-safe idle, thruster gain.
                          TIDAK pernah mengimpor Mission5FSM sama sekali —
                          toggle `control_mode=autonomous` cuma menyetel string
                          dan mencetaknya, tak ada yang menggerakkan wahana.
  * `autonomy/rov_link.py` — punya FSM lengkap (start_mission5/stop_mission5),
                          tervalidasi SITL, tapi tak pernah dijalankan di Pi
                          (dependensinya `fsm/`, `vision/` bahkan belum ada
                          di sana).

Akibatnya: menekan toggle Autonomous di GUI tidak melakukan apa pun di kolam.
Log trial 22 Agu (`log-m5/`) menunjukkan kedalaman rata 0.08–0.14 m selama 57
detik — wahana memang tak pernah diperintah menyelam.

Modul ini menutup celah itu dengan menumpang infrastruktur `rov_agent.py` yang
sudah teruji, BUKAN menjalankan `rov_link.py` sebagai proses kedua (dua program
berebut `/dev/ttyACM0` = frame MAVLink rusak).

Perbedaan penting dari rov_link.start_mission5
----------------------------------------------
`rov_link.py` menyambungkan FSM ke dirinya sendiri lewat **loopback UDP**, jadi
ia perlu menandai frame `src='fsm'` supaya kill-switch bisa membedakan perintah
FSM dari perintah operator. Di sini FSM memanggil **fungsi Python langsung**
(in-process), tidak menyentuh socket command sama sekali — sehingga:

  * kill-switch operator bekerja OTOMATIS: perintah joystick tetap masuk lewat
    `command_listener` seperti biasa, dan gate `control_mode` di caller yang
    memutuskan siapa yang menang. Tak ada tag `src` yang bisa lupa dipasang.
  * tak ada port UDP tambahan (:14552) yang harus dijaga tidak bentrok.
  * FSM ikut mati bersama proses; tak ada thread yatim yang masih menulis
    setpoint setelah agent di-restart.
"""

import threading
import time


class Mission5CommandAdapter:
    """Antarmuka CommandSender yang dipakai Mission5FSM, tapi in-process.

    Mission5FSM memakai skala PERSEN (-100..100) di API-nya dan mengalikan ×10
    saat menulis ke wire (lihat CommandSender.send di fsm/mission5.py). Di sini
    kita mengisi dict axis rov_agent yang memang berskala -1000..1000, jadi
    konversi ×10 yang sama HARUS tetap dilakukan — kalau tidak, seluruh gerakan
    FSM cuma 10% kekuatan dan wahana nyaris diam (persis bug 'vert'/'heave'
    12 Agu, kelas yang sama).
    """

    def __init__(self, set_axis, set_gripper, arm, emergency_stop):
        self._set_axis = set_axis
        self._set_gripper = set_gripper
        self._arm = arm
        self._emergency_stop = emergency_stop

    def send(self, surge=0, sway=0, yaw=0, vert=0, gripper=None):
        # vert (nama FSM) -> heave (nama axis rov_agent/GUI). Nama berbeda untuk
        # sumbu yang sama adalah sumber bug berulang di proyek ini; dipetakan
        # sekali di sini, di satu tempat.
        self._set_axis(surge=surge * 10, sway=sway * 10,
                       yaw=yaw * 10, heave=vert * 10)
        if gripper is not None:
            self._set_gripper(bool(gripper))

    def arm(self, on=True):
        self._arm(bool(on))

    def stop_all(self):
        """Netralkan axis TAPI tetap armed (dipakai antar-state)."""
        self._set_axis(surge=0, sway=0, yaw=0, heave=0)

    def emergency_stop(self):
        """Failsafe: netral + DISARM. Hanya untuk abort."""
        self._emergency_stop()

    def close(self):
        pass


class Mission5TelemetryAdapter:
    """Antarmuka TelemetryReceiver, dibaca langsung dari dict `state` rov_agent.

    Mission5FSM memanggil .get() tiap iterasi loop dan membaca 'depth',
    'heading', 'roll', 'pitch', serta 'control_mode' (gate autonomous).
    Semuanya sudah ada di `state` — tak perlu socket, tak perlu port kedua.
    """

    def __init__(self, read_state):
        self._read_state = read_state

    def start(self):
        pass

    def stop(self):
        pass

    def get(self):
        return self._read_state()


class Mission5Runner:
    """Kelola siklus hidup FSM: start saat toggle autonomous, stop saat manual.

    Dibuat malas (lazy import) supaya `rov_agent.py` tetap bisa jalan di Pi yang
    belum punya opencv/pyzbar atau paket `autonomy/` — impor gagal berarti misi
    5 otonom tidak tersedia, BUKAN agent mati total dan seluruh kontrol manual
    ikut hilang.
    """

    def __init__(self, cmd_adapter, telem_adapter, config=None, log=print):
        self._cmd = cmd_adapter
        self._telem = telem_adapter
        self._cfg = config or {}
        self._log = log
        self._fsm = None
        self._vision = None
        self._thread = None
        self._lock = threading.Lock()

    def available(self):
        """True kalau paket autonomy + dependensinya bisa diimpor di sini."""
        try:
            self._import_autonomy()
            return True
        except Exception:
            return False

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

    def start(self):
        """Nyalakan FSM. Aman dipanggil berulang — start kedua diabaikan.

        Guard 'thread lama masih hidup' pernah menolak toggle-on TANPA jejak di
        rov_link.py: badge bilang autonomous tapi tak ada FSM yang jalan. Di
        sini ia selalu bicara.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._log("[M5] start dilewati — thread FSM sebelumnya masih hidup")
                return False

        try:
            (Mission5FSM, State, VisionPipeline, QR_SIDE_M,
             HOOK_COLOR_HSV_RANGE, HOOK_MIN_AREA,
             HOOK_PIPE_DIAM_M) = self._import_autonomy()
        except Exception as e:
            # Sengaja TIDAK melempar: kontrol manual harus tetap hidup.
            self._log(f"[M5] TIDAK BISA START — paket autonomy/vision gagal diimpor: {e}")
            self._log("[M5] (cek: folder autonomy/ ada di Pi? opencv-python & pyzbar terpasang di venv?)")
            return False

        cfg = self._cfg
        vision = VisionPipeline(
            source=cfg.get("vision_source", "usb"),
            device=cfg.get("vision_device", 0),
            qr_url=cfg.get("bottom_url"),
            hook_url=cfg.get("wall_url"),
            calib_file=cfg.get("calib_file"),
            calib_file_qr=cfg.get("calib_bottom"),
            calib_file_hook=cfg.get("calib_wall"),
            qr_length=cfg.get("qr_size", QR_SIDE_M),
            hook_hsv_range=HOOK_COLOR_HSV_RANGE,
            hook_min_area=HOOK_MIN_AREA,
            hook_pipe_diam=HOOK_PIPE_DIAM_M,
            wall_cnn=cfg.get("wall_cnn", True),
        )
        vision.start()

        fsm = Mission5FSM(cmd=self._cmd, telem=self._telem, vision=vision)
        start_state = getattr(State, cfg.get("start_state", "M5_REDIVE"))

        def _run():
            try:
                # wait_mode=False: gate autonomous SUDAH diputuskan oleh caller
                # (toggle control_mode di rov_agent). Menyuruh FSM menunggu
                # lagi berarti dua gerbang untuk satu keputusan.
                fsm.start(start_state=start_state, wait_mode=False)
            except Exception as e:
                self._log(f"[M5] FSM berhenti karena error: {e}")
            finally:
                try:
                    vision.stop()
                except Exception:
                    pass
                self._log("[M5] thread FSM selesai")

        with self._lock:
            self._fsm = fsm
            self._vision = vision
            self._thread = threading.Thread(target=_run, daemon=True,
                                            name="Mission5FSM")
            self._thread.start()
        self._log(f"[M5] Mission5 FSM dimulai (start_state={start_state.name})")
        return True

    def stop(self):
        """Hentikan FSM. abort() melakukan failsafe + disarm."""
        with self._lock:
            fsm, thread = self._fsm, self._thread
            self._fsm = None
            self._thread = None
        if fsm is None:
            return
        try:
            fsm.abort()
        except Exception as e:
            self._log(f"[M5] error saat abort: {e}")
        if thread:
            thread.join(timeout=2)
            if thread.is_alive():
                self._log("[M5] PERINGATAN: thread FSM belum berhenti dalam 2s")
        self._log("[M5] Mission5 FSM dihentikan")

    def state_name(self):
        """Nama state FSM saat ini untuk telemetri GUI, None kalau tak jalan."""
        fsm = self._fsm
        if fsm is None:
            return None
        try:
            return fsm.telemetry_out.get("state")
        except Exception:
            return None
