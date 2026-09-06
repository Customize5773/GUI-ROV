#!/usr/bin/env python3
"""
rov_link.py — Jembatan sisi-ROV: protokol JSON/UDP GUI HYDROSHIP  <->  MAVLink (ArduSub).

Ini adalah upgrade nyata dari `raspi_rov_example.py`. Di produksi ia jalan di
Raspberry Pi (terhubung ke Pixhawk/ArduSub). Saat pengembangan, ia jalan di laptop
dan menyambung ke MOCK atau ArduSub SITL.

Topologi (tiga port UDP, TIDAK boleh bentrok):

    GUI browser ──WS:8080── server.js ──cmd JSON :14550──►  rov_link  ──MANUAL_CONTROL──►  ArduSub
                                       ◄──telem JSON :14551──         ◄──ATTITUDE/PRESSURE──   (SITL/mock/HW)
                                                                MAVLink di port terpisah :14555

  - server.js mengirim command JSON ke  :14550   → rov_link DENGARKAN di sini.
  - rov_link mengirim telemetri JSON ke  server:14551.
  - MAVLink ke vehicle lewat port lain (default udpin :14555) agar tidak bentrok 14550/14551.

Jalankan (uji dengan mock):
    python rov_link.py --server 127.0.0.1 --mavlink udpin:0.0.0.0:14555
Jalankan (ArduSub SITL):
    # sim_vehicle.py -v ArduSub --out=udpout:127.0.0.1:14555
    python rov_link.py --server 127.0.0.1 --mavlink udpin:0.0.0.0:14555
Jalankan (di Raspberry Pi, Pixhawk via USB):
    python rov_link.py --server 192.168.2.1 --mavlink /dev/ttyACM0 --baud 115200

Kontrak JSON (sesuai server.js + README-WORK §3):
  Command masuk  : {"name": "...", "value": ..., "t": ...}
  Telemetri keluar: {heading, roll, pitch, depth, temp, voltage, armed, light, mode, ts}
"""

import argparse
import json
import math
import socket
import threading
import time

from pymavlink import mavutil

from fsm.mission5 import (Mission5FSM, State, CommandSender, TelemetryReceiver,
                          QR_SIDE_M, HOOK_COLOR_HSV_RANGE, HOOK_MIN_AREA, HOOK_PIPE_DIAM_M)
from vision.qr_detect import VisionPipeline

# 22 Agu 2026: dwe_underwater.npz -> dwe_trial2.npz. Dipilih dgn DUA syarat,
# bukan rms saja (rms hanya mengukur kecocokan model thd gambar kalibrasinya
# SENDIRI — ia tak tahu resolusi stream, dan bisa rendah justru karena overfit):
#
#   1. Resolusi WAJIB sama dgn stream (1280x720). dwe_underwater.npz dibuat pada
#      4080x3072 (resolusi FOTO): fx jadi 3,2x terlalu besar -> PBVS mengira QR
#      3,2x lebih jauh -> dgn SERVO_TARGET_DIST=0.30 m ROV baru berhenti saat
#      jarak ASLI ~9 cm (menabrak payload).
#   2. Geometri harus masuk akal. Menskala dwe_underwater (kalibrasi paling
#      sehat: fx/fy=1.000, principal point -0,1%/-2,0%) ke lebar 1280 memberi
#      fx ~= 950 sbg pembanding independen:
#        dwe_trial2.npz  fx=914  (-3,8%)  <- dipakai
#        dwe_v1.npz      fx=992  (+4,3%)  tapi cy=892,6 JATUH DI LUAR frame 720
#                                          (mustahil fisik, khas overfit; rms-nya
#                                          justru paling rendah 0,68)
#        dwe.npz         fx=609  (-36%)   outlier, tak sepakat dgn keduanya
#
# Sisa kelemahan dwe_trial2: cy meleset +29,5% dari tengah. Perbaikan sebenarnya
# = KALIBRASI ULANG DI AIR pada 1280x720; yang ini membuatnya tidak berbahaya,
# belum membuatnya akurat. Dijaga _verify_calib_size() di vision/qr_detect.py:
# resolusi tak cocok -> PBVS dimatikan + ERROR di log (bukan gagal diam-diam).
CALIB_BOTTOM_DEFAULT = "vision/calibration/dwe_trial2.npz"
CALIB_WALL_DEFAULT   = "vision/calibration/dwe_trial2.npz"

# ───────────────────────── Konfigurasi yang perlu DIVERIFIKASI ke setup ArduSub kalian ──
WATER_RHO = 997.0          # kg/m³ air tawar (kolam). Air laut ≈ 1025.
G = 9.80665
SURFACE_HPA_DEFAULT = 1013.25

# Channel servo (SERVOn_FUNCTION di ArduSub). VERIFIKASI dgn QGroundControl.
LIGHT_SERVO_CH = 9         # contoh — sesuaikan
GRIPPER_SERVO_CH = 7       # SERVO7, sesuai konfigurasi ArduSub aktual

# HARUS sama dengan gripper_controller.py (dipakai rov_agent.py, jalur manual)
# — dua implementasi TERPISAH menggerakkan channel fisik yang sama, dan sampai
# 22 Agu 2026 nilainya berbeda: di sini 1900/1100, di gripper_controller.py
# 1580/1350 (kalibrasi nyata di tepi kolam, 22 Agu). Kirim 1900/1100 ke gripper
# yang travel amannya cuma sampai 1580/1350 mendorong servo ~2x lebih jauh dari
# batas fisiknya — stall/tarik arus berlebih/rusak gear, bukan cuma salah bunyi.
# Sengaja DIDUPLIKASI, bukan di-import (lihat komentar PILOT_MODE_MAP di atas:
# rov_link.py harus tetap jalan standalone tanpa root repo di sys.path).
# Dikunci tests/test_mission5.py::test_gripper_pwm_sama_dengan_gripper_controller.
GRIPPER_PWM_OPEN = 1580
GRIPPER_PWM_CLOSE = 1350
LIGHT_PWM_ON = 1900
LIGHT_PWM_OFF = 1100

# MANUAL_CONTROL: sumbu vertikal (z). Di ArduSub umumnya 0..1000 dgn 500 = netral.
# Surge/sway/yaw: -1000..1000 dgn 0 = netral. VERIFIKASI arah/tanda saat uji SITL.
Z_NEUTRAL = 500

# Nama mode GUI -> nama mode ArduSub. Sengaja diduplikasi (bukan import) supaya
# rov_link.py tetap bisa dijalankan langsung dari dalam autonomy/ tanpa root repo
# di sys.path — tapi HARUS tetap sinkron dengan rov_modes.PILOT_MODE_MAP.
PILOT_MODE_MAP = {
    "manual": "MANUAL",
    "stabilize": "STABILIZE",
    "depth_hold": "ALT_HOLD",
    "poshold": "ALT_HOLD",  # overlay heading-hold sisi Python, lihat rov_modes.py
}

# Kill-switch: axis operator di atas ambang ini membatalkan autonomy. Skalanya
# SAMA dengan axis GUI, yaitu -1000..1000 (clampAxis di server.js, AXIS_RANGE di
# rov_axes.py) — jadi 15 ≈ 1,5% skala penuh, BUKAN 15%.
#
# Yang menyaring drift stik bukan angka ini, melainkan deadzone sisi-GUI
# (DEFAULT_DEADZONE=0.12 + expo 1.6 di shared/joystick-profile.js): nilai di
# bawah deadzone dikirim sebagai 0. Efek gabungannya, kill-switch menyala di
# ~20% defleksi stik fisik — cukup peka untuk merebut kendali, cukup tuli
# terhadap noise.
#
# JANGAN "perbaiki" jadi 150 dengan anggapan skalanya -100..100: itu membuat
# operator harus mendorong stik hampir penuh sebelum bisa mengambil alih.
# Profil joystick mengizinkan deadzone 0 — dengan setelan itu drift MEMANG bisa
# memicu abort palsu, jadi jangan pakai deadzone 0 saat lomba.
# Dikunci tests/test_rov_link.py.
KILL_SWITCH_DEADZONE = 15

# Port lokal (loopback) tempat Mission5FSM menerima telemetri fan-out dari rov_link
# selama misi otonom berjalan (lihat start_mission5 / extra_dests).
FSM_TELEM_PORT_DEFAULT = 14552


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class RovLink:
    def __init__(self, args):
        self.args = args
        # setpoint manual dari GUI (-1000..1000, sama seperti clampAxis di server.js)
        self.sp = {"surge": 0.0, "sway": 0.0, "yaw": 0.0, "heave": 0.0}
        self.light_on = False
        self.control_mode = "manual"
        # FSM dijalankan sebagai PROSES TERPISAH (tools/launch_sitl.py --fsm),
        # bukan lewat start_mission5() internal. Gerbang di handle_command()
        # ada untuk membuang frame dari thread FSM internal yang belum benar-
        # benar mati sesudah operator kembali ke manual — FSM eksternal bukan
        # kasus itu, dan tak ada satu pun yang mengirim control_mode=autonomous
        # ke sini pada jalur SITL, sehingga tanpa flag ini SELURUH perintahnya
        # dibuang diam-diam (gejala: depth tetap 0.00, M5_REDIVE selalu timeout).
        self.external_fsm = bool(getattr(args, "external_fsm", False))
        self.surface_hpa = SURFACE_HPA_DEFAULT
        self.last_press_abs = SURFACE_HPA_DEFAULT   # tekanan absolut terbaru (utk set_surface)
        self.lock = threading.Lock()

        # Mission5 FSM (misi 5 autonomous) — lihat start_mission5/stop_mission5.
        self.mission5_fsm = None
        self.mission5_thread = None
        self.fsm_telem_port = getattr(args, "fsm_telem_port", FSM_TELEM_PORT_DEFAULT)

        # telemetri terbaru hasil parsing MAVLink
        self.telem = {
            "heading": None, "roll": None, "pitch": None, "depth": None,
            "temp": None, "voltage": None, "armed": False,
            "light": False, "mode": "manual", "poshold": False,
        }
        self.pilot_mode_name = "manual"   # nama GUI terakhir diminta lewat "pilot_mode"

        # MAVLink
        print(f"[MAV] connecting: {args.mavlink}")
        if args.mavlink.startswith(("udp", "tcp")):
            self.master = mavutil.mavlink_connection(args.mavlink, source_system=255, source_component=190)
        else:
            self.master = mavutil.mavlink_connection(args.mavlink, baud=args.baud, source_system=255, source_component=190)
        hb_timeout = getattr(args, "hb_timeout", 10)
        print(f"[MAV] menunggu heartbeat dari vehicle… (timeout {hb_timeout}s)")
        if self.master.wait_heartbeat(timeout=hb_timeout) is None:
            raise RuntimeError(
                f"[MAV] tidak ada heartbeat dari {args.mavlink} dalam {hb_timeout}s — "
                "cek vehicle/SITL/mock hidup & endpoint benar")
        print(f"[MAV] terhubung: system={self.master.target_system} component={self.master.target_component}")
        self._request_streams()

        # UDP sockets ke server.js
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # telemetri keluar
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # command masuk
        self.rx.bind(("0.0.0.0", args.json_rx_port))

        # tujuan telemetri tambahan (mis. FSM autonomy di port lain) — agar GUI & FSM
        # bisa terima telemetri bersamaan tanpa rebut port 14551
        self.extra_dests = []
        for item in (getattr(args, "telem_extra", "") or "").split(","):
            item = item.strip()
            if item:
                host, port = item.rsplit(":", 1)
                self.extra_dests.append((host, int(port)))

    # ───────────────────────── MAVLink helpers ─────────────────────────
    def _request_streams(self):
        self.master.mav.request_data_stream_send(
            self.master.target_system, self.master.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)  # 10 Hz

    def arm(self, on):
        self.master.mav.command_long_send(
            self.master.target_system, self.master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
            1 if on else 0, 0, 0, 0, 0, 0, 0)
        print(f"[CMD] {'ARM' if on else 'DISARM'}")

    def set_servo(self, ch, pwm):
        self.master.mav.command_long_send(
            self.master.target_system, self.master.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0,
            ch, pwm, 0, 0, 0, 0, 0)

    def set_mode(self, ardusub_mode):
        """ardusub_mode mis. 'MANUAL', 'STABILIZE', 'ALT_HOLD' (=Depth Hold)."""
        mapping = self.master.mode_mapping() or {}
        if ardusub_mode not in mapping:
            print(f"[MODE] '{ardusub_mode}' tidak ada di mode_mapping {list(mapping)} — dilewati")
            return
        self.master.set_mode(mapping[ardusub_mode])
        print(f"[MODE] -> {ardusub_mode}")

    def send_manual_control(self):
        with self.lock:
            s = dict(self.sp)
        # s sudah dalam konvensi GUI -1000..1000 (lihat clampAxis di server.js) —
        # x/y/r dikirim apa adanya, z digeser+dibagi 2 ke rentang 0..1000 ArduSub.
        x = int(clamp(s["surge"], -1000, 1000))
        y = int(clamp(s["sway"], -1000, 1000))
        r = int(clamp(s["yaw"], -1000, 1000))
        z = int(clamp(Z_NEUTRAL + s["heave"] / 2.0, 0, 1000))
        self.master.mav.manual_control_send(self.master.target_system, x, y, z, r, 0)

    def send_gcs_heartbeat(self):
        self.master.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)

    # ───────────────────────── Command dari GUI ─────────────────────────
    def handle_command(self, name, value, addr=None, from_fsm=False):
        if name in self.sp:                      # surge/sway/yaw/heave
            # Frame nyasar dari thread FSM yang belum benar-benar mati (join()
            # timeout di stop_mission5) tidak boleh lolos begitu operator sudah
            # kembali ke manual — kalau tidak, axis autonomous lama terus
            # menimpa self.sp diam-diam.
            if (from_fsm and not self.external_fsm
                    and self.control_mode != "autonomous"):
                return
            # Kill-switch: axis nyata dari operator (bukan CommandSender milik FSM
            # sendiri, yang menandai frame-nya src='fsm') di atas deadzone, saat
            # autonomous berjalan → override manual.
            if (self.control_mode == "autonomous" and not from_fsm
                    and abs(float(value)) > KILL_SWITCH_DEADZONE):
                print(f"[KILL-SWITCH] axis manual {name}={value} dari {addr} — abort autonomy")
                self.control_mode = "manual"
                self.set_mode("MANUAL")
                self.stop_mission5()
            with self.lock:
                self.sp[name] = float(value)
            return
        if name == "stop":                       # FAILSAFE
            # Hentikan FSM DULU, baru netral+disarm.
            #
            # Urutannya penting: fsm.abort() sendiri memanggil emergency_stop()
            # yang menulis setpoint lewat loopback. Kalau dinolkan lebih dulu,
            # tulisan itu datang SESUDAHNYA dan kita kirim manual_control dari
            # nilai yang sudah basi.
            #
            # Tanpa baris ini (perilaku sampai 2026-08-21), STOP hanya
            # menetralkan + disarm sementara thread FSM TERUS jalan dan terus
            # menulis self.sp. Wahana diam karena disarm — tapi begitu operator
            # menekan ARM (refleks wajar setelah STOP tak sengaja), gerakan
            # langsung lanjut dari state FSM terakhir tanpa peringatan apa pun.
            # E-Stop yang bisa hidup lagi sendiri bukan E-Stop.
            #
            # stop_mission5() idempotent (return cepat bila fsm None), jadi aman
            # dipanggil saat autonomy tidak jalan. Pola yang sama sudah dipakai
            # cabang KILL-SWITCH di atas dan cabang control_mode→manual di bawah.
            self.stop_mission5()
            with self.lock:
                for k in self.sp:
                    self.sp[k] = 0.0
            self.send_manual_control()
            self.arm(False)
            print("[CMD] STOP — netral + disarm")
        elif name == "arm":
            self.arm(bool(value))
        elif name == "light":
            self.light_on = bool(value)
            self.set_servo(LIGHT_SERVO_CH, LIGHT_PWM_ON if self.light_on else LIGHT_PWM_OFF)
        elif name == "gripper":                  # "open"/"close" atau true(=close)/false(=open)
            close = (value == "close") or (value is True)
            self.set_servo(GRIPPER_SERVO_CH, GRIPPER_PWM_CLOSE if close else GRIPPER_PWM_OPEN)
            print(f"[CMD] gripper {'CLOSE' if close else 'OPEN'}")
        elif name == "pilot_mode":
            ardusub_mode = PILOT_MODE_MAP.get(str(value).strip().lower())
            if ardusub_mode is None:
                print(f"[MODE] pilot_mode tidak dikenal: {value}")
            else:
                self.pilot_mode_name = str(value).strip().lower()
                self.set_mode(ardusub_mode)
        elif name == "control_mode":
            self.control_mode = str(value)
            self.set_mode("ALT_HOLD" if self.control_mode == "autonomous" else "MANUAL")
            if self.control_mode == "autonomous":
                with self.lock:
                    for k in self.sp:
                        self.sp[k] = 0.0         # hold; FSM autonomy akan ambil alih nanti
                self.start_mission5()
            else:
                self.stop_mission5()
        elif name == "set_surface":
            # tangkap tekanan absolut terbaru sebagai referensi depth = 0
            self.surface_hpa = self.last_press_abs
            print(f"[CMD] set_surface — surface_hpa = {self.surface_hpa:.2f}")
        else:
            # mode/controller/thruster_config/pid/pool_depth/viewer_access → urusan GUI
            print(f"[CMD] (diabaikan di link) {name} = {value}")

    # ───────────────────────── Mission5 FSM (misi 5 autonomous) ─────────────────────────
    def start_mission5(self, vision_source=None, device=None):
        """Spawn Mission5FSM sbg thread daemon in-process, bicara ke diri sendiri via
        loopback UDP (protokol sama persis dgn tools/launch_sitl.py --fsm, hanya
        diorkestrasi otomatis oleh toggle GUI, bukan proses terpisah)."""
        # Guard ini pernah diam-diam menolak toggle-on: thread FSM lama masih
        # sekarat (stop_mission5 tak join sampai tuntas) saat operator menyalakan
        # lagi, jadi start dilewati TANPA jejak — badge & mode bilang autonomous,
        # tapi tak ada FSM yang jalan. Kebalikan persis dari bug STOP 2026-08-21,
        # dan sama tak kelihatannya. Sekarang minimal dia teriak.
        if self.mission5_thread and self.mission5_thread.is_alive():
            print("[M5] start dilewati — thread FSM sebelumnya masih hidup")
            return
        vision_source = vision_source or getattr(self.args, "fsm_vision_source", "usb")
        device = self.args.fsm_vision_device if device is None else device
        cmd = CommandSender(host="127.0.0.1", port=self.args.json_rx_port)
        telem = TelemetryReceiver(host="0.0.0.0", port=self.fsm_telem_port)
        # Dual-camera (BOTTOM=QR docking, WALL=hook) bila kedua URL diisi; jika tidak,
        # jatuh ke jalur lama (satu kamera dari --fsm-vision-source/--fsm-vision-device).
        bottom_url = getattr(self.args, "fsm_bottom_url", None)
        wall_url = getattr(self.args, "fsm_wall_url", None)
        wall_cnn_on = getattr(self.args, "fsm_wall_cnn", True)
        vision = VisionPipeline(
            source=vision_source, device=device,
            rtsp_url=getattr(self.args, "fsm_rtsp_url", None) or "rtsp://hydroship:8554/cam",
            qr_url=bottom_url, hook_url=wall_url,
            calib_file_qr=getattr(self.args, "fsm_calib_bottom", None),
            calib_file_hook=getattr(self.args, "fsm_calib_wall", None),
            qr_length=getattr(self.args, "fsm_qr_size", QR_SIDE_M),
            hook_hsv_range=HOOK_COLOR_HSV_RANGE,
            hook_min_area=HOOK_MIN_AREA, hook_pipe_diam=HOOK_PIPE_DIAM_M,
            hook_model=getattr(self.args, "fsm_hook_model", None),
            wall_cnn=True if wall_cnn_on else None,
        )
        telem.start()
        vision.start()
        fsm = Mission5FSM(
            cmd=cmd, telem=telem, vision=vision,
            hook_map_file=getattr(self.args, "fsm_hook_map", None),
            hook_calib_file=getattr(self.args, "fsm_calib_wall", None),
        )
        self.mission5_fsm = fsm
        dest = ("127.0.0.1", self.fsm_telem_port)
        if dest not in self.extra_dests:
            self.extra_dests.append(dest)

        def _run():
            try:
                fsm.start(start_state=State.M5_REDIVE, wait_mode=False)
            finally:
                vision.stop()
                telem.stop()
                cmd.close()

        self.mission5_thread = threading.Thread(target=_run, daemon=True)
        self.mission5_thread.start()
        print("[M5] Mission5 FSM dimulai (thread)")

    def stop_mission5(self):
        fsm, thread = self.mission5_fsm, self.mission5_thread
        if fsm is None:
            return
        fsm.abort()   # failsafe + disarm; TIDAK menyentuh gripper (lihat PLAN §1)
        if thread:
            thread.join(timeout=2)
            if thread.is_alive():
                print("[M5] PERINGATAN: thread FSM belum berhenti dalam 2s — masih hidup "
                      "di background, tapi axis-nya sudah diblokir gate control_mode")
        dest = ("127.0.0.1", self.fsm_telem_port)
        if dest in self.extra_dests:
            self.extra_dests.remove(dest)
        self.mission5_fsm = None
        self.mission5_thread = None
        print("[M5] Mission5 FSM dihentikan")

    # ───────────────────────── Loop-loop ─────────────────────────
    def loop_rx_json(self):
        print(f"[JSON] dengar command di :{self.args.json_rx_port}")
        while True:
            data, addr = self.rx.recvfrom(2048)
            try:
                msg = json.loads(data.decode())
            except ValueError:
                continue
            self.handle_command(msg.get("name"), msg.get("value"), addr,
                                from_fsm=(msg.get("src") == "fsm"))

    def loop_mavlink_rx(self):
        while True:
            msg = self.master.recv_match(blocking=True, timeout=1)
            if msg is None:
                continue
            t = msg.get_type()
            if t == "ATTITUDE":
                self.telem["roll"] = round(math.degrees(msg.roll), 1)
                self.telem["pitch"] = round(math.degrees(msg.pitch), 1)
                self.telem["heading"] = round((math.degrees(msg.yaw) + 360) % 360, 1)
            elif t == "SCALED_PRESSURE2":
                self.last_press_abs = msg.press_abs
                depth = (msg.press_abs - self.surface_hpa) * 100.0 / (WATER_RHO * G)
                self.telem["depth"] = round(max(0.0, depth), 2)
                self.telem["temp"] = round(msg.temperature / 100.0, 1)
            elif t == "GLOBAL_POSITION_INT" and self.telem["depth"] is None:
                self.telem["depth"] = round(max(0.0, -msg.relative_alt / 1000.0), 2)
            elif t == "SYS_STATUS":
                if msg.voltage_battery not in (0, 65535):
                    self.telem["voltage"] = round(msg.voltage_battery / 1000.0, 1)
            elif t == "HEARTBEAT":
                self.telem["armed"] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                # Mode ArduSub yang BENAR-BENAR aktif. GUI memakai field ini
                # untuk menyorot tab mode, jadi ia harus datang dari wahana —
                # bukan dari control_mode (gate otoritas GUI, lihat loop_telem_tx).
                try:
                    self.telem["mode"] = mavutil.mode_string_v10(msg)
                except Exception:
                    pass
                # ALT_HOLD melayani dua tab GUI ("depth_hold" & "poshold");
                # bedakan pakai permintaan pilot_mode terakhir, lihat rov_modes.py.
                self.telem["poshold"] = (self.telem["mode"] == "ALT_HOLD"
                                          and self.pilot_mode_name == "poshold")

    def loop_manual_tx(self):
        while True:
            self.send_manual_control()
            time.sleep(0.05)   # 20 Hz

    def loop_telem_tx(self):
        while True:
            with self.lock:
                self.telem["light"] = self.light_on
                # `mode` diisi dari HEARTBEAT (lihat loop_rx). control_mode
                # adalah hal berbeda — gate otoritas GUI — jadi dikirim
                # sebagai field-nya sendiri.
                self.telem["control_mode"] = self.control_mode
            out = dict(self.telem)
            out["ts"] = time.time()
            if self.mission5_fsm is not None:
                m5 = dict(self.mission5_fsm.telemetry_out)
                out["mission5"] = m5
                loc = m5.get("hook_loc") or {}
                pose = loc.get("pose_map") or {}
                out["hook_xy"] = {
                    "status": loc.get("status"),
                    "hook_id": loc.get("hook_id"),
                    "x": pose.get("x"), "y": pose.get("y"), "z": pose.get("z"),
                    "sigma_xy_m": loc.get("sigma_xy_m"),
                    "reproj_px": loc.get("reproj_px"),
                    "reason": loc.get("reason"),
                    "confidence": m5.get("confidence"),
                    "bbox": m5.get("bbox"),
                    "offset_x": m5.get("offset_x"),
                    "offset_y": m5.get("offset_y"),
                }
            else:
                out["hook_xy"] = None
            payload = json.dumps(out).encode()
            self.tx.sendto(payload, (self.args.server, self.args.telem_port))
            for host, port in self.extra_dests:
                self.tx.sendto(payload, (host, port))
            time.sleep(0.1)    # 10 Hz

    def loop_gcs_hb(self):
        while True:
            self.send_gcs_heartbeat()
            time.sleep(1.0)

    def run(self):
        for fn in (self.loop_rx_json, self.loop_mavlink_rx, self.loop_manual_tx,
                   self.loop_telem_tx, self.loop_gcs_hb):
            threading.Thread(target=fn, daemon=True).start()
        print("[OK] rov_link berjalan. Ctrl+C untuk berhenti.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[EXIT] berhenti.")


def _load_fsm_config(paths):
    """Terapkan config tuning + geometri kolam ke globals fsm.mission5.

    Tanpa ini toggle Autonomous di GUI menjalankan FSM dengan default modul
    (dive 30%, HOOK_DEPTH 0.45, timeout arena 5x5) sementara jalur CLI
    `python fsm/mission5.py --config ...` memakai angka hasil trial — dua
    perilaku berbeda dari kode yang sama, dan yang lewat GUI itulah yang
    dipakai saat lomba. Urutan & semantik dijaga sama persis dgn mission5.main().
    """
    import fsm.mission5 as m5
    from config.loader import load_config, apply_config
    keys = set()
    for path in paths:
        applied = apply_config(vars(m5), load_config(path))
        keys.update(name for name, _old, _new in applied)
        print(f"[CFG] {path}: {len(applied)} nilai dioverride")
    m5._derive_depths(keys)
    # Nama-nama ini disalin ke modul ini saat `from fsm.mission5 import ...`, jadi
    # TIDAK ikut berubah saat globals mission5 di-override (beda dgn state handler
    # FSM yang late-binding). Tanpa penyegaran ini VisionPipeline dibangun dgn
    # nilai lama sementara FSM memakai nilai config — diam-diam tidak konsisten.
    global QR_SIDE_M, HOOK_COLOR_HSV_RANGE, HOOK_MIN_AREA, HOOK_PIPE_DIAM_M
    QR_SIDE_M = m5.QR_SIDE_M
    HOOK_COLOR_HSV_RANGE = m5.HOOK_COLOR_HSV_RANGE
    HOOK_MIN_AREA = m5.HOOK_MIN_AREA
    HOOK_PIPE_DIAM_M = m5.HOOK_PIPE_DIAM_M
    print(f"[CFG] HOOK_DEPTH={m5.HOOK_DEPTH} DEPTH_TARGET_BOTTOM={m5.DEPTH_TARGET_BOTTOM} "
          f"DIVE_SPEED={m5.DIVE_SPEED} WALL_HEADING={m5.WALL_HEADING}")


def main():
    # Pra-parse --fsm-config LEBIH DULU (pola mission5.main()): default flag di
    # bawah (mis. --fsm-qr-size) membaca konstanta yang baru saja dioverride.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--fsm-config", action="append", default=None, metavar="FILE",
                     help="config tuning/geometri .yaml utk FSM misi 5 yang dinyalakan "
                          "toggle Autonomous di GUI. BOLEH DIULANG, file belakangan menang: "
                          "--fsm-config config/rov_tuned.yaml --fsm-config config/pool_kki_running.yaml")
    pre_args, _ = pre.parse_known_args()
    if pre_args.fsm_config:
        _load_fsm_config(pre_args.fsm_config)

    ap = argparse.ArgumentParser(description="Jembatan JSON/UDP GUI <-> MAVLink ArduSub",
                                 parents=[pre])
    ap.add_argument("--server", default="127.0.0.1", help="IP komputer yang menjalankan server.js (telemetri dikirim ke sini)")
    ap.add_argument("--telem-port", type=int, default=14551, help="port telemetri di server.js")
    ap.add_argument("--telem-extra", default="", help="tujuan telemetri tambahan, csv host:port (mis. 127.0.0.1:14552 untuk FSM autonomy)")
    ap.add_argument("--json-rx-port", type=int, default=14550, help="port command JSON dari server.js")
    ap.add_argument("--mavlink", default="udpin:0.0.0.0:14555", help="endpoint MAVLink ke vehicle/SITL/mock")
    ap.add_argument("--baud", type=int, default=115200, help="baud (jika serial, mis. /dev/ttyACM0)")
    ap.add_argument("--hb-timeout", type=int, default=10, help="detik menunggu heartbeat vehicle sebelum menyerah")
    ap.add_argument("--external-fsm", action="store_true",
                    help="FSM misi 5 dijalankan sbg proses terpisah (tools/launch_sitl.py "
                         "--fsm), bukan start_mission5() internal. Menerima frame src='fsm' "
                         "tanpa menunggu toggle control_mode dari GUI.")
    ap.add_argument("--fsm-telem-port", type=int, default=FSM_TELEM_PORT_DEFAULT,
                     help="port loopback tempat Mission5FSM in-process menerima telemetri (auto-dikelola oleh toggle GUI)")
    ap.add_argument("--fsm-vision-source", default="usb", choices=["mock", "usb", "rtsp"],
                     help="sumber video Mission5FSM saat autonomy dimulai dari GUI")
    ap.add_argument("--fsm-vision-device", type=int, default=0, help="index USB webcam utk Mission5FSM (jika --fsm-vision-source usb, jalur satu-kamera lama)")
    ap.add_argument("--fsm-rtsp-url", default=None,
                     help="URL RTSP/HTTP eksplisit jalur satu-kamera lama (jika --fsm-vision-source rtsp). "
                          "Tanpa ini, default hardcode rtsp://hydroship:8554/cam BUKAN kamera ROV asli.")
    ap.add_argument("--fsm-bottom-url", default=None,
                     help="URL stream kamera BOTTOM (QR docking), mis. http://192.168.2.2:8080/stream. "
                          "Isi bersama --fsm-wall-url utk aktifkan mode dual-camera.")
    ap.add_argument("--fsm-wall-url", default=None,
                     help="URL stream kamera WALL (hook), mis. http://192.168.2.2:8081/stream. "
                          "Isi bersama --fsm-bottom-url utk aktifkan mode dual-camera.")
    ap.add_argument("--fsm-calib-bottom", default=CALIB_BOTTOM_DEFAULT,
                     help="kalibrasi .npz kamera BOTTOM (mode dual-camera)")
    ap.add_argument("--fsm-calib-wall", default=CALIB_WALL_DEFAULT,
                     help="kalibrasi .npz kamera WALL (mode dual-camera)")
    ap.add_argument("--fsm-qr-size", type=float, default=QR_SIDE_M,
                     help="sisi fisik QR payload (m) utk solvePnP PBVS")
    ap.add_argument("--fsm-hook-model", default=None, metavar="BEST.PT",
                    help="opsional bobot YOLOv8 Hook di laptop, mis. autonomy/vision/best.pt")
    ap.add_argument("--fsm-hook-map", default=None, metavar="MAP.YAML",
                    help="opsional map hook untuk menerbitkan pose X/Y arena, mis. autonomy/config/hook_map.pool.yaml")
    ap.add_argument("--fsm-wall-cnn", type=lambda s: s.lower() not in ("0", "false", "no", "off"),
                     default=True, metavar="BOOL",
                     help="aktifkan fallback wall-CNN saat decode_qr() gagal (default aktif)")
    args = ap.parse_args()
    RovLink(args).run()


if __name__ == "__main__":
    main()
