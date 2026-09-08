#!/usr/bin/env python3
"""
control_main.py
Pusat kontrol ROV:

MANUAL (BUKAN lewat file ini)
    GUI pollGamepad -> WebSocket -> server.js -> Pi
    joystick.py -> UDP 14600 -> control_main hanya sebagai KILL-SWITCH
    autonomous; frame motion manual tidak pernah dikirim dari sini.

AUTONOMOUS
    full-counter FSM -> UDP 14601 -> server.js

VISI (hook)
    worker YOLO di Pi -> rov_agent.py -> telemetry -> server.js
                      -> UDP 14603 -> control_main (telemetri dan helper visi)

Motion:
    surge, sway, yaw, heave = -1000..1000

Non-motion autonomous command:
    gripper = open / close
    depth_target = target meter

Catatan:
- GUI memilih control_mode DAN mengemudikan manual.
- joystick.py membaca F310 khusus untuk abort autonomous.
- server.js tetap menjadi bridge.
"""

import ast
import json
import math
import os
import socket
import sys
import threading
import time

# Peredam dipakai ulang dari stack autonomy. Parameter untuk wahana/kamera
# ini tetap belum tervalidasi di kolam.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "autonomy"),
)

try:
    import yaml
    from control.visual_servo import PID, _slew_limit, _tally
except Exception as _servo_import_error:  # pragma: no cover - lingkungan tanpa venv
    # Servo hook MATI, tapi control_main tetap hidup. Kehilangan kendali MANUAL
    # gara-gara PyYAML tidak terpasang adalah kegagalan yang jauh lebih buruk
    # daripada kehilangan satu CASE autonomous.
    PID = None
    print(f"[SERVO] import gagal ({_servo_import_error}) — servo hook MATI")

# ============================================================
# CONFIG
# ============================================================

JOYSTICK_LISTEN_IP = "127.0.0.1"
JOYSTICK_LISTEN_PORT = 14600

SERVER_IP = "127.0.0.1"
SERVER_PORT = 14601

# Port command mode dari server.js -> control_main.py
MODE_LISTEN_IP = "127.0.0.1"
MODE_LISTEN_PORT = 14602

# Deteksi hook (YOLO) dari server.js. Inferensinya berjalan di Pi; di sini
# hanya hasilnya yang mendarat.
HOOK_LISTEN_IP = "127.0.0.1"
HOOK_LISTEN_PORT = 14603

# Semua angka servo ada di file ini, tidak satu pun di kode — tuning kolam
# dilakukan dengan mengedit yaml lalu menyalakan ulang mode autonomous.
SERVO_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "control_config.yaml")

LOOP_HZ = 20.0
LOOP_DT = 1.0 / LOOP_HZ

JOYSTICK_TIMEOUT = 0.5

MODE_MANUAL = "manual"
MODE_AUTONOMOUS = "autonomous"

# Sama persis dengan KILL_SWITCH_DEADZONE di rov_agent.py dan
# autonomy/rov_link.py. Stik F310 di atas nilai ini saat autonomous =
# operator mengambil alih.
OPERATOR_ABORT_DEADZONE = 15

AXES = ("surge", "sway", "yaw", "heave")

# ============================================================
# STATE
# ============================================================

control_mode = MODE_MANUAL
mode_lock = threading.RLock()

joystick = {
    "surge": 0,
    "sway": 0,
    "yaw": 0,
    "heave": 0,
}
joystick_lock = threading.Lock()
last_joystick_time = 0.0

# Deteksi hook terakhir + kapan diterima (monotonic). Tidak ada sensor posisi
# lateral di ROV ini, jadi deteksi inilah SATU-SATUNYA acuan sway; begitu ia
# basi, tak ada apa pun yang tahu ROV sudah bergeser berapa.
latest_hook = None
last_hook_time = 0.0
latest_qr_metric = None  # (kanal, area_frac); bukan luas lintas kanal
latest_qr_xy_norm = None
servo_counted_receipt = None
last_vision_receipt = 0.0
vehicle_state = {}
last_vehicle_time = 0.0
hook_lock = threading.Lock()

running = True

# ============================================================
# UDP
# ============================================================

out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ============================================================
# HELPERS
# ============================================================

def clamp(value, lo=-1000, hi=1000):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0

    value = max(lo, min(hi, value))
    return int(round(value))


def get_mode():
    with mode_lock:
        return control_mode


def set_mode(mode, depth_dasar=None):
    global control_mode, AUTO_STEPS

    mode = str(mode).lower().strip()

    if mode not in (MODE_MANUAL, MODE_AUTONOMOUS):
        print(f"[MODE] mode tidak valid: {mode}")
        return

    with mode_lock:
        old = control_mode
        # Publish mode setelah reset/config siap. Main loop memakai lock yang
        # sama agar toggle tidak berpotongan dengan tick yang menutup gripper.
        if old != mode and mode == MODE_AUTONOMOUS:
            try:
                steps = read_auto_steps(__file__, depth_dasar)
            except (OSError, SyntaxError, ValueError, TypeError) as exc:
                print(f"[AUTO] tidak dimulai: {exc}")
                send_command("control_mode", MODE_MANUAL)
                return
            AUTO_STEPS = steps
            autonomous_reset()
        control_mode = mode
        if old != mode and mode == MODE_MANUAL:
            send_motion(0, 0, 0, 0)

    if old != mode:
        print(f"[MODE] {old.upper()} -> {mode.upper()}")


def send_packet(packet):
    data = json.dumps(packet, separators=(",", ":")).encode("utf-8")
    out_sock.sendto(data, (SERVER_IP, SERVER_PORT))


def send_motion(surge=0, sway=0, yaw=0, heave=0, src="operator"):
    """Kirim satu frame motion.

    `src` WAJIB ikut: di Pi ia yang memutuskan axis ini masuk `fsm_axes`
    (src="fsm") atau `joystick` (operator). Tanpa tag itu axis autonomous
    mendarat di dict joystick dan kill-switch di joystick_sender()
    membatalkan autonomous di CASE gerak pertama. Jangan pernah menebak asal
    perintah dari alamat IP — lihat catatan di rov_agent.py.
    """
    packet = {
        "type": "control",
        "surge": clamp(surge),
        "sway": clamp(sway),
        "yaw": clamp(yaw),
        "heave": clamp(heave),
        "src": src,
        "timestamp": time.time(),
    }

    send_packet(packet)


def send_command(name, value):
    packet = {
        "type": "command",
        "name": name,
        "value": value,
        "timestamp": time.time(),
    }

    send_packet(packet)


# ============================================================
# MANUAL
# ============================================================

def joystick_listener():
    global last_joystick_time

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((JOYSTICK_LISTEN_IP, JOYSTICK_LISTEN_PORT))
    sock.settimeout(0.2)

    print(
        f"[MANUAL] menunggu joystick.py "
        f"di {JOYSTICK_LISTEN_IP}:{JOYSTICK_LISTEN_PORT}"
    )

    while running:
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            continue

        if msg.get("type") != "joystick":
            continue

        with joystick_lock:
            for axis in AXES:
                joystick[axis] = clamp(msg.get(axis, 0))

            last_joystick_time = time.time()


def joystick_snapshot():
    """(surge, sway, yaw, heave), stale — satu sumber utk manual & abort."""
    with joystick_lock:
        stale = (time.time() - last_joystick_time) > JOYSTICK_TIMEOUT
        axes = (
            joystick["surge"],
            joystick["sway"],
            joystick["yaw"],
            joystick["heave"],
        )

    return axes, stale


# MANUAL tidak dikemudikan dari sini. Axis manual datang dari GUI
# (pollGamepad -> WS -> server.js -> Pi) supaya profil joystick operator,
# kendali keyboard, dan input axis dashboard tetap berlaku. Dua sumber yang
# sama-sama menulis dict joystick di Pi hanya membuat stik saling adu.
# joystick.py tetap dibaca, tapi HANYA sebagai kill-switch autonomous
# (lihat autonomous_control()).


# ============================================================
# VISI HOOK
# ============================================================

def hook_center_from_telemetry(det):
    """hook_xy -> (center_x, frame_w), atau None kalau tak layak pakai.

    Sumbernya adalah field `hook_xy` di telemetry, yaitu keluaran
    _validate_hook_vision() di rov_agent.py — dan validator itu SENGAJA hanya
    meneruskan `bbox`, bukan `center` milik worker. Menyalin kembali skema
    worker ke sini akan menghasilkan servo yang diam selamanya di kolam
    sementara semua test buatan sendiri lulus.

    Deteksi cacat dibuang, TIDAK disimpan: menyimpannya membuat stempel waktu
    ikut segar dan servo mengira punya acuan padahal tidak.
    """
    if not isinstance(det, dict):
        return None

    try:
        x, _y, w, _h = [float(v) for v in det["bbox"]]
        frame_w = float(det["frame_w"])
    except (TypeError, KeyError, ValueError):
        return None

    if not all(math.isfinite(v) for v in (x, w, frame_w)) or frame_w <= 0 or w <= 0:
        return None

    return (x + w / 2.0, frame_w)


def qr_center_from_telemetry(det):
    """qr_vision -> (center_x, frame_w), atau None kalau tak layak pakai.

    Berbeda dgn hook_xy, _validate_qr_vision() MENERUSKAN `center` (sudah
    dipastikan berada di dalam frame), jadi tidak perlu diturunkan dari bbox.
    """
    if not isinstance(det, dict):
        return None

    try:
        center_x = float(det["center"][0])
        frame_w = float(det["frame_w"])
    except (TypeError, KeyError, IndexError, ValueError):
        return None

    if not all(math.isfinite(v) for v in (center_x, frame_w)) or frame_w <= 0 or not 0 <= center_x <= frame_w:
        return None

    return (center_x, frame_w)


# Sumber acuan lateral dipilih lewat config; server.js mengirim ketiganya.
#
# "qr" menerima DUA tipe pesan sekaligus, dan itu disengaja: worker mengirim
# `qr_vision` kalau QR berhasil di-decode, `qr_region` kalau kotaknya terdeteksi
# tapi decode gagal — tak pernah keduanya untuk frame yang sama. Menengahkan
# kotak di frame tidak butuh tahu isi QR-nya, sementara decode adalah bagian
# yang paling sering gagal di air berriak. Menerima hanya `qr_vision` membuat
# servo putus-putus persis saat air paling keruh.
VISION_PARSERS = {
    "qr": ({"qr_vision", "qr_region"}, qr_center_from_telemetry),
    "hook": ({"hook_vision"}, hook_center_from_telemetry),
}


def accept_vision_message(msg):
    """Terima geometri + umur Pi; paket cache/reorder tidak menyegarkan deteksi."""
    global latest_hook, latest_qr_metric, last_hook_time, last_vision_receipt
    global vehicle_state, last_vehicle_time
    global latest_qr_xy_norm
    if not isinstance(msg, dict):
        return
    now = time.monotonic()
    if msg.get("type") == "vehicle_state":
        value = msg.get("value")
        if isinstance(value, dict):
            with hook_lock:
                vehicle_state = dict(value)
                last_vehicle_time = now
        return
    if servo_cfg is None:
        return
    types, parser = VISION_PARSERS[servo_cfg["source"]]
    channel = msg.get("type")
    if channel not in types:
        return
    value = msg.get("value")
    center = parser(value)
    if (servo_cfg.get('frame_size') is not None and
            (not isinstance(value, dict) or
             [value.get('frame_w'), value.get('frame_h')] != servo_cfg['frame_size'])):
        return
    if (channel == 'qr_vision' and servo_cfg.get('target_data') is not None
            and (not isinstance(value, dict) or value.get('data') != servo_cfg['target_data'])):
        return
    try:
        age, receipt = float(msg["age"]), float(msg["received"])
        if (center is None or not math.isfinite(age) or not math.isfinite(receipt)
                or age < 0 or age > servo_cfg["max_age"] or receipt <= 0):
            return
    except (KeyError, TypeError, ValueError):
        return
    metric = None
    xy = None
    if channel in ("qr_vision", "qr_region"):
        try:
            area = float(value["area"])
            height = float(value["frame_h"])
            cy = float(value['center'][1])
            if not math.isfinite(cy) or not 0 <= cy <= height:
                return
            fraction = area / (center[1] * height)
            if not math.isfinite(fraction) or height <= 0 or not 0 < fraction <= 1:
                return
            metric = (channel, fraction)
            xy = (center[0] / center[1], cy / height)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return
    with hook_lock:
        if receipt <= last_vision_receipt:
            return
        latest_hook, latest_qr_metric = center, metric
        latest_qr_xy_norm = xy
        last_hook_time = now - age
        last_vision_receipt = receipt


def vision_listener():
    """Visi dan depth lewat kanal UDP yang sama; tidak menjalankan worker baru."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOOK_LISTEN_IP, HOOK_LISTEN_PORT))
    sock.settimeout(0.2)
    print(f"[VISI] menunggu telemetry di {HOOK_LISTEN_IP}:{HOOK_LISTEN_PORT}")
    while running:
        try:
            data, _ = sock.recvfrom(8192)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            accept_vision_message(json.loads(data.decode("utf-8")))
        except (ValueError, UnicodeError):
            continue


# ============================================================
# SERVO HOOK
# ============================================================
#
# Memetakan posisi hook di frame menjadi koreksi sway:
#
#     ex   = (center_x - frame_w/2) / (frame_w/2)     # + = hook di KANAN
#     sway = deadband -> PID(kp, kd ter-filter) -> slew -> cap
#
# Kamera yang dipakai bernama BOTTOM tapi MENGHADAP DEPAN, jadi pemetaan
# x citra -> sway ini BENAR apa adanya; jangan "diperbaiki" jadi surge.
#
# Otoritas lateral wahana ini lemah: frame 3-2-1 hanya punya SATU thruster
# sway, dan sway menimbulkan roll secara mekanik (asimetri terukur 7,1:1,
# ROV diam saja sudah miring ~-2°). Roll memiringkan kamera, error visual
# ikut melompat, dan servo bisa mengejar bayangannya sendiri — itulah kenapa
# slew di config bukan knob kenyamanan melainkan pemutus umpan balik.

servo_cfg = None          # None = urutan berhenti aman
mission_cfg = None
servo_pid = None
servo_hits = 0
servo_last_t = None
servo_surge_out = 0.0
servo_seen_hook = False


def load_servo_config():
    """Baca ulang tuning dari yaml. Dipanggil tiap autonomous dinyalakan."""
    global servo_cfg
    global servo_pid, mission_cfg

    mission_cfg = None
    servo_cfg = None
    servo_pid = None

    if PID is None:
        return

    try:
        with open(SERVO_CONFIG_PATH, encoding="utf-8") as f:
            document = yaml.safe_load(f)
            cfg = document["servo_hook"]
            mission = document["mission"]
        profile = None
        if cfg.get('calibration_file'):
            path = os.path.join(os.path.dirname(SERVO_CONFIG_PATH), cfg['calibration_file'])
            with open(path, encoding='utf-8') as f:
                profile = json.load(f)
            if profile.get('camera') != 'WALL' or profile.get('frame_size') != [1280,720]:
                raise ValueError('profil harus WALL 1280x720 sesuai pengukuran')
            for key in ('target_x_norm', 'grab_roi_norm', 'close_area_frac_decoded',
                        'close_area_frac_region', 'max_grab_area_frac', 'target_data'):
                cfg[key] = profile[key]
            cfg['grab_hysteresis_px'] = profile.get('grab_hysteresis_px', 0.0)
        hysteresis = float(cfg.get('grab_hysteresis_px', 0.0))
        if not math.isfinite(hysteresis) or not 0 <= hysteresis <= 3:
            raise ValueError('grab_hysteresis_px harus 0..3 piksel')
        max_area = cfg.get('max_grab_area_frac')
        if max_area is not None:
            max_area = float(max_area)
            if not math.isfinite(max_area) or not 0 < max_area <= 1:
                raise ValueError('max_grab_area_frac tidak valid')
        target_data = cfg.get('target_data')
        if target_data is not None and (not isinstance(target_data, str) or not target_data):
            raise ValueError('target_data tidak valid')

        for key in ("settle_s", "depth_m", "depth_wait_s", "depth_tol_m",
                    "search_timeout_s", "search_yaw", "search_surge",
                    "search_sweep_s", "approach_surge", "servo_timeout_s",
                    "lost_timeout_s", "gripper_hold_s", "rise_m", "rise_wait_s",
                    "telemetry_max_age"):
            mission[key] = float(mission[key])
            if not math.isfinite(mission[key]):
                raise ValueError(f"mission.{key} harus finite")
            if key not in ("search_yaw", "search_surge", "approach_surge") and mission[key] <= 0:
                raise ValueError(f"mission.{key} harus positif")
        for key in ("search_yaw", "search_surge", "approach_surge"):
            if abs(mission[key]) > 1000:
                raise ValueError(f"mission.{key} di luar axis range")
        for key in ("kp_sway", "kd_sway", "d_lpf", "deadband_norm", "slew",
                    "max_speed", "center_tol_norm", "max_age"):
            cfg[key] = float(cfg[key])
            if not math.isfinite(cfg[key]) or cfg[key] < 0:
                raise ValueError(f"servo_hook.{key} tidak valid")
        if (not 0 < cfg["max_speed"] <= 100 or cfg["slew"] <= 0
                or not 0 < cfg["center_tol_norm"] <= 1
                or not 0 < cfg["max_age"] < mission["lost_timeout_s"]
                or cfg["d_lpf"] > 1 or cfg["deadband_norm"] > 1
                or type(cfg["invert_sway"]) is not bool
                or type(cfg["centered_ticks"]) is not int or cfg["centered_ticks"] < 1):
            raise ValueError("batas servo tidak valid")
        target_x = float(cfg.get('target_x_norm', 0.5))
        if not math.isfinite(target_x) or not 0 < target_x < 1:
            raise ValueError('target_x_norm harus di antara 0 dan 1')
        grab_roi = cfg.get('grab_roi_norm')
        if grab_roi is not None:
            if not isinstance(grab_roi, (list, tuple)) or len(grab_roi) != 4:
                raise ValueError('grab_roi_norm harus [x1,y1,x2,y2] atau null')
            grab_roi = tuple(float(v) for v in grab_roi)
            if (not all(math.isfinite(v) for v in grab_roi)
                    or not 0 <= grab_roi[0] < grab_roi[2] <= 1
                    or not 0 <= grab_roi[1] < grab_roi[3] <= 1):
                raise ValueError('grab_roi_norm di luar batas frame')
        thresholds = {}
        for key in ("close_area_frac_decoded", "close_area_frac_region"):
            value = cfg[key]
            if value is not None:
                value = float(value)
                if not math.isfinite(value) or not 0 < value <= 1:
                    raise ValueError(f"{key} harus null atau 0 < fraksi <= 1")
            thresholds[key] = value
        if (max_area is not None and thresholds['close_area_frac_decoded'] is not None
                and max_area <= thresholds['close_area_frac_decoded']):
            raise ValueError('batas luas maksimum harus melebihi ambang minimum')

        servo_pid = PID(
            float(cfg["kp_sway"]),
            0.0,
            float(cfg["kd_sway"]),
            out_limit=float(cfg["max_speed"]),
            deadband=float(cfg["deadband_norm"]),
            d_lpf=float(cfg["d_lpf"]),
            slew=float(cfg["slew"]),
        )
        # Dibaca sekali di sini supaya KeyError muncul saat memuat, bukan di
        # tengah CASE saat ROV sudah di air.
        source = str(cfg["source"]).strip().lower()

        if source not in VISION_PARSERS:
            raise ValueError(
                f"source={source!r} tidak dikenal, pilih {sorted(VISION_PARSERS)}")

        cfg = {
            **thresholds,
            "target_x_norm": target_x,
            "grab_roi_norm": grab_roi,
            "max_grab_area_frac": max_area,
            "target_data": target_data,
            "frame_size": profile['frame_size'] if profile else None,
            "grab_hysteresis_px": hysteresis,
            "source": source,
            "invert_sway": bool(cfg["invert_sway"]),
            "max_speed": float(cfg["max_speed"]),
            "slew": float(cfg["slew"]),
            "center_tol_norm": float(cfg["center_tol_norm"]),
            "centered_ticks": int(cfg["centered_ticks"]),
            "max_age": float(cfg["max_age"]),
        }
    except Exception as e:
        print(f"[SERVO] {SERVO_CONFIG_PATH} tidak terpakai ({e}) — "
              f"urutan autonomous dihentikan")
        servo_pid = None
        return

    servo_cfg = cfg
    mission_cfg = mission
    print(f"[SERVO] tuning dimuat: source={cfg['source']} "
          f"invert_sway={cfg['invert_sway']} "
          f"tol={cfg['center_tol_norm']} max_age={cfg['max_age']}s")


def servo_reset():
    global servo_counted_receipt
    servo_counted_receipt = None
    global servo_hits
    global servo_last_t
    global servo_surge_out
    global servo_seen_hook

    servo_hits = 0
    servo_last_t = None
    servo_surge_out = 0.0
    servo_seen_hook = False

    if servo_pid is not None:
        servo_pid.reset()


def servo_step(surge_step):
    """Satu tick servo. Return (surge, sway, boleh_tutup_gripper).

    `surge_step` adalah nilai surge dari AUTO_STEPS — di-gate, bukan dipakai
    langsung: maju sambil masih menyamping membuat ROV melewati hook.
    """
    global servo_hits
    global servo_last_t
    global servo_surge_out
    global servo_seen_hook
    global servo_counted_receipt

    now = time.monotonic()

    if servo_last_t is None:
        dt = LOOP_DT
    else:
        dt = max(0.02, min(0.25, now - servo_last_t))

    servo_last_t = now

    with hook_lock:
        det = latest_hook
        age = now - last_hook_time
        metric = latest_qr_metric
        xy = latest_qr_xy_norm
        receipt = last_vision_receipt

    slew_axis = servo_cfg["slew"] * 10.0     # config dlm %, axis dlm ±1000

    if det is None or age > servo_cfg["max_age"]:
        # Deteksi basi. Sway NOL — bukan mengulang error terakhir: tak ada
        # sensor posisi lateral yang bisa membenarkan tebakan itu. Surge ikut
        # ditutup karena gerbangnya justru dihitung dari error yang hilang.
        servo_pid.reset()
        servo_hits = 0
        servo_surge_out = 0.0

        return 0, 0, False

    servo_seen_hook = True
    center_x, frame_w = det
    ex = (center_x - frame_w * servo_cfg["target_x_norm"]) / (frame_w / 2.0)

    sign = -1.0 if servo_cfg["invert_sway"] else 1.0
    sway = clamp(sign * servo_pid.step(ex, dt) * 10.0)

    di_tengah = abs(ex) < servo_cfg["center_tol_norm"]
    threshold = None
    if metric is not None:
        key = ("close_area_frac_decoded" if metric[0] == "qr_vision"
               else "close_area_frac_region")
        threshold = servo_cfg[key]
    roi = servo_cfg['grab_roi_norm']
    in_grab = (roi is not None and xy is not None
               and roi[0] <= xy[0] <= roi[2] and roi[1] <= xy[1] <= roi[3])
    # Enter only inside the calibrated rectangle. Once collecting frames,
    # tolerate subpixel boundary noise, but close only back inside it.
    keep_grab = in_grab
    size = servo_cfg.get('frame_size')
    if servo_hits > 0 and roi is not None and xy is not None and size:
        hx, hy = (servo_cfg['grab_hysteresis_px'] / side for side in size)
        keep_grab = (roi[0]-hx <= xy[0] <= roi[2]+hx
                     and roi[1]-hy <= xy[1] <= roi[3]+hy)
    close_ready = (di_tengah and keep_grab and threshold is not None
                   and metric[1] > threshold
                   and (servo_cfg.get('max_grab_area_frac') is None
                        or metric[1] <= servo_cfg['max_grab_area_frac']))
    # _tally normal punya peluruhan; capit butuh N tick BERUNTUN.
    if not close_ready:
        servo_hits = 0
    elif receipt != servo_counted_receipt:
        servo_hits = _tally(servo_hits, True)
    servo_counted_receipt = receipt
    if servo_hits == 1 or (int(now * 2) != int((now - dt) * 2)):
        print(f"[SERVO] channel={metric[0] if metric else None} ex={ex:.4f} "
              f"area_frac={metric[1] if metric else None} threshold={threshold} "
              f"xy={xy} in_grab={in_grab} close_frames={servo_hits}/{servo_cfg['centered_ticks']}")

    # Gerbang surge. Transisi 0 -> penuh tetap lewat slew: lompatan command
    # menyentak rangka, dan sentakan itu jatuh persis saat ROV paling dekat hook.
    servo_surge_out = _slew_limit(
        servo_surge_out, float(surge_step) if di_tengah else 0.0, slew_axis, dt)
    # Jangan melewati payload saat menunggu 10 frame baru pada worker 4 FPS.
    if close_ready or (metric is not None and servo_cfg.get('max_grab_area_frac') is not None
                       and metric[1] > servo_cfg['max_grab_area_frac']):
        servo_surge_out = 0.0

    return (clamp(servo_surge_out), sway,
            in_grab and servo_hits >= servo_cfg["centered_ticks"])


# ============================================================
# AUTONOMOUS - FULL COUNTER
# ============================================================

# Dibaca ulang dari file setiap toggle AUTONOMOUS; simpan tanpa restart server.
# Depth non-None menggunakan DEPTH DASAR GUI saat memulai urutan.
AUTO_STEPS = [
    # duration, surge, sway, yaw, heave, gripper, depth
    (3.0, 0, 0, 1000, 0, None, 1.0),
    (2.0, 0, 0, 0, 0, None, None),

    # contoh struktur command non-motion
    (1.0, 0, 0, 0, 0, None, None),
    (2.0, 0, 0, 0, 0, None, None),

    (3.0, -500, 0, 0, 0, None, None),

    (1.0, 0, 0, 0, 0, None, None),
]


def read_auto_steps(path, depth_dasar=None):
    """Baca tuple literal saja; perubahan kode lain tidak dieksekusi ulang."""
    with open(path, encoding="utf-8") as source:
        tree = ast.parse(source.read())
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "AUTO_STEPS" for t in node.targets)]
    if len(values) != 1:
        raise ValueError("AUTO_STEPS harus satu daftar literal")
    steps = ast.literal_eval(values[0])
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    if depth_dasar is not None and (not number(depth_dasar) or depth_dasar < 0):
        raise ValueError("DEPTH DASAR tidak valid")
    if not isinstance(steps, list) or not steps:
        raise ValueError("AUTO_STEPS kosong / bukan list")
    result = []
    for step in steps:
        if not isinstance(step, (tuple, list)) or len(step) != 7:
            raise ValueError("setiap langkah harus tujuh kolom")
        duration, *axes, gripper, depth = step
        if (not number(duration) or duration < 0
                or any(not number(v) or abs(v) > 1000 for v in axes)
                or gripper not in (None, "open", "close")
                or (depth is not None and (not number(depth) or depth < 0))):
            raise ValueError("nilai AUTO_STEPS tidak valid")
        result.append((*step[:6], depth_dasar if depth is not None and depth_dasar is not None else depth))
    return result


auto_index = 0
auto_step_start = 0.0
auto_gripper_sent = False
auto_depth_sent = False
auto_finished = False
auto_depth_target = None


def autonomous_reset():
    global latest_qr_xy_norm
    global auto_index
    global auto_step_start
    global auto_gripper_sent
    global auto_depth_sent
    global auto_finished, auto_depth_target
    global latest_hook, latest_qr_metric, last_hook_time, last_vision_receipt

    with hook_lock:
        latest_hook = latest_qr_metric = None
        latest_qr_xy_norm = None
        last_hook_time = last_vision_receipt = 0.0
    auto_depth_target = None
    auto_index = 0
    auto_step_start = time.monotonic()
    auto_gripper_sent = False
    auto_depth_sent = False
    auto_finished = False

    # Muat ulang konfigurasi telemetri/visi. AUTO_STEPS tetap memakai literal
    # di atas yang dibaca ulang oleh set_mode sebelum reset.
    load_servo_config()
    servo_reset()

    print("[AUTO] FSM reset -> CASE 0")


def current_depth():
    with hook_lock:
        depth = vehicle_state.get("depth")
        fresh = time.monotonic() - last_vehicle_time <= mission_cfg["telemetry_max_age"]
    if isinstance(depth, (int, float)) and not isinstance(depth, bool) and math.isfinite(depth) and depth >= 0 and fresh:
        return float(depth)
    return None


def finish_auto(reason, hold_here=True):
    global auto_finished, auto_depth_target
    send_motion(0, 0, 0, 0, src="fsm")
    if hold_here and mission_cfg is not None:
        depth = current_depth()
        if depth is not None:
            send_command("depth_apply", depth)
            auto_depth_target = depth
    auto_finished = True
    print(f"[AUTO] selesai: {reason}; motion nol, tahan kedalaman")


def enter_case(index):
    global auto_index, auto_step_start, auto_gripper_sent, auto_depth_sent
    send_motion(0, 0, 0, 0, src="fsm")
    auto_index = index
    auto_step_start = time.monotonic()
    auto_gripper_sent = auto_depth_sent = False
    servo_reset()
    print(f"[AUTO] -> CASE {index}")


def autonomous_control():
    global auto_step_start, auto_gripper_sent, auto_depth_sent
    global auto_depth_target

    # ── Kill-switch operator ──────────────────────────────────────────────
    # Di arsitektur ini stik F310 TIDAK lagi sampai ke Pi saat autonomous
    # (server.js menolak axis GUI, dan axis autonomous bertag src="fsm"),
    # jadi abort harus dipicu DI SINI — di satu-satunya tempat input F310
    # masih terbaca. Tanpa ini autonomous berjalan tanpa jalan keluar.
    axes, stale = joystick_snapshot()

    if not stale and any(abs(v) > OPERATOR_ABORT_DEADZONE for v in axes):
        print("[KILL-SWITCH] stik F310 digerakkan saat autonomous "
              "— abort, kembali ke MANUAL")
        send_command("control_mode", MODE_MANUAL)
        set_mode(MODE_MANUAL)
        return

    if auto_finished:
        send_motion(0, 0, 0, 0, src="fsm")
        return

    if servo_cfg is None or mission_cfg is None:
        finish_auto("config tidak valid", hold_here=False)
        return

    depth_now = current_depth()
    with hook_lock:
        armed = vehicle_state.get("armed") is True
    if not armed:
        if auto_index == 0:
            auto_step_start = time.monotonic()  # settle dihitung sesudah ARM
            send_motion(0, 0, 0, 0, src="fsm")
        else:
            finish_auto("DISARM", hold_here=False)
        return
    if depth_now is None:
        finish_auto("telemetry depth hilang; target native terakhir dipertahankan", hold_here=False)
        return

    if auto_index >= len(AUTO_STEPS):
        finish_auto("urutan selesai", hold_here=False)
        return
    duration, surge, sway, yaw, heave, gripper, depth_target = AUTO_STEPS[auto_index]
    elapsed = time.monotonic() - auto_step_start

    if elapsed >= duration:
        if auto_index + 1 >= len(AUTO_STEPS):
            finish_auto("urutan selesai; target kedalaman tetap aktif", hold_here=False)
        else:
            enter_case(auto_index + 1)
        return

    if not auto_depth_sent and depth_target is not None:
        send_command("depth_apply", float(depth_target))
        auto_depth_target = float(depth_target)
        auto_depth_sent = True
    if not auto_gripper_sent and gripper is not None:
        send_motion(0, 0, 0, 0, src="fsm")
        send_command("gripper", gripper)
        auto_gripper_sent = True

    send_motion(surge, sway, yaw, heave, src="fsm")


# ============================================================
# MODE RECEIVER
# ============================================================

def mode_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((MODE_LISTEN_IP, MODE_LISTEN_PORT))
    sock.settimeout(0.2)

    print(
        f"[MODE] menunggu control_mode "
        f"di {MODE_LISTEN_IP}:{MODE_LISTEN_PORT}"
    )

    while running:
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            continue

        if msg.get("type") != "control_mode":
            continue

        set_mode(msg.get("value"), msg.get("depth_dasar"))


# ============================================================
# HEARTBEAT
# ============================================================

def heartbeat_sender():
    """
    Tanda bahwa control_main.py masih hidup.
    Dipakai watchdog di rov_agent.py.
    """
    while running:
        try:
            send_packet({
                "type": "control_main_heartbeat",
                "timestamp": time.time(),
            })
        except Exception as e:
            print(f"[HEARTBEAT] error: {e}")

        time.sleep(0.2)


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    global running

    print("=" * 60)
    print("CONTROL MAIN")
    print("MANUAL + AUTONOMOUS")
    print("=" * 60)
    print(f"[UDP] joystick input : {JOYSTICK_LISTEN_PORT}")
    print(f"[UDP] server output  : {SERVER_IP}:{SERVER_PORT}")
    print(f"[UDP] mode input     : {MODE_LISTEN_PORT}")
    print(f"[UDP] hook input     : {HOOK_LISTEN_PORT}")
    print()

    threading.Thread(
        target=joystick_listener,
        daemon=True,
    ).start()

    threading.Thread(
        target=mode_listener,
        daemon=True,
    ).start()

    threading.Thread(
        target=vision_listener,
        daemon=True,
    ).start()

    threading.Thread(
        target=heartbeat_sender,
        daemon=True,
    ).start()

    autonomous_reset()

    try:
        while True:

            with mode_lock:
                mode = get_mode()
                if mode == MODE_AUTONOMOUS:
                    autonomous_control()

            time.sleep(LOOP_DT)

    except KeyboardInterrupt:
        print("\n[CONTROL MAIN] dihentikan")
    finally:
        running = False

        # Selalu netralkan motion saat program berhenti.
        try:
            send_motion(0, 0, 0, 0)
        except Exception:
            pass

        out_sock.close()


if __name__ == "__main__":
    main()
