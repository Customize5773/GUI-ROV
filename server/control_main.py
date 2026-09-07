#!/usr/bin/env python3
"""
control_main.py
Pusat kontrol ROV:

MANUAL
    joystick.py -> UDP 14600 -> control_main -> UDP 14601 -> server.js

AUTONOMOUS
    full-counter FSM -> UDP 14601 -> server.js

VISI (hook)
    worker YOLO di Pi -> rov_agent.py -> telemetry -> server.js
                      -> UDP 14603 -> control_main (CASE bertanda servo)

Motion:
    surge, sway, yaw, heave = -1000..1000

Non-motion autonomous command:
    gripper = open / close
    depth_target = target meter

Catatan:
- GUI hanya memilih control_mode.
- joystick.py tetap menjadi pembaca F310.
- server.js tetap menjadi bridge.
"""

import json
import os
import socket
import sys
import threading
import time

# Peredam servo dipakai ULANG dari stack autonomy — deadband, D ter-filter,
# slew, dan gerbang approach di sana sudah dibayar dengan trial kolam
# (lihat docstring PID di control/visual_servo.py). Menyalinnya ke sini berarti
# dua tuning yang perlahan menyimpang.
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
mode_lock = threading.Lock()

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


def set_mode(mode):
    global control_mode

    mode = str(mode).lower().strip()

    if mode not in (MODE_MANUAL, MODE_AUTONOMOUS):
        print(f"[MODE] mode tidak valid: {mode}")
        return

    with mode_lock:
        old = control_mode
        control_mode = mode

    if old != mode:
        print(f"[MODE] {old.upper()} -> {mode.upper()}")

        # Saat pindah mode, autonomous dimulai dari state 0.
        if mode == MODE_AUTONOMOUS:
            autonomous_reset()

        # Saat kembali MANUAL, autonomous langsung dihentikan.
        if mode == MODE_MANUAL:
            send_motion(0, 0, 0, 0)


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


def manual_control():
    axes, stale = joystick_snapshot()

    send_motion(*((0, 0, 0, 0) if stale else axes), src="operator")


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

    if frame_w <= 0 or w <= 0:
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

    if frame_w <= 0:
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


def vision_listener():
    """Terima deteksi visi dari server.js. Pola sama dgn joystick/mode listener."""
    global latest_hook
    global last_hook_time

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOOK_LISTEN_IP, HOOK_LISTEN_PORT))
    sock.settimeout(0.2)

    print(
        f"[VISI] menunggu deteksi YOLO "
        f"di {HOOK_LISTEN_IP}:{HOOK_LISTEN_PORT}"
    )

    while running:
        try:
            data, _ = sock.recvfrom(8192)
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            continue

        # Sumber yang tidak dipilih DIBUANG, bukan disimpan: dua sumber
        # mengisi satu slot berarti servo diam-diam mengikuti kamera yang salah.
        # servo_cfg None (config gagal / servo mati) -> tak ada yang disimpan.
        if servo_cfg is None:
            continue

        tipe, parser = VISION_PARSERS[servo_cfg["source"]]

        if msg.get("type") not in tipe:
            continue

        acuan = parser(msg.get("value"))

        if acuan is None:
            continue

        with hook_lock:
            latest_hook = acuan
            last_hook_time = time.monotonic()


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

servo_cfg = None          # None = servo mati; CASE servo jadi langkah waktu biasa
servo_pid = None
servo_hits = 0
servo_last_t = None
servo_surge_out = 0.0
servo_seen_hook = False


def load_servo_config():
    """Baca ulang tuning dari yaml. Dipanggil tiap autonomous dinyalakan."""
    global servo_cfg
    global servo_pid

    servo_cfg = None
    servo_pid = None

    if PID is None:
        return

    try:
        with open(SERVO_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)["servo_hook"]

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
              f"servo hook MATI, CASE servo jadi langkah waktu biasa")
        servo_pid = None
        return

    servo_cfg = cfg
    print(f"[SERVO] tuning dimuat: source={cfg['source']} "
          f"invert_sway={cfg['invert_sway']} "
          f"tol={cfg['center_tol_norm']} max_age={cfg['max_age']}s")


def servo_reset():
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
    """Satu tick servo. Return (surge, sway, sudah_di_tengah).

    `surge_step` adalah nilai surge dari AUTO_STEPS — di-gate, bukan dipakai
    langsung: maju sambil masih menyamping membuat ROV melewati hook.
    """
    global servo_hits
    global servo_last_t
    global servo_surge_out
    global servo_seen_hook

    now = time.monotonic()

    if servo_last_t is None:
        dt = LOOP_DT
    else:
        dt = max(0.02, min(0.25, now - servo_last_t))

    servo_last_t = now

    with hook_lock:
        det = latest_hook
        age = now - last_hook_time

    slew_axis = servo_cfg["slew"] * 10.0     # config dlm %, axis dlm ±1000

    if det is None or age > servo_cfg["max_age"]:
        # Deteksi basi. Sway NOL — bukan mengulang error terakhir: tak ada
        # sensor posisi lateral yang bisa membenarkan tebakan itu. Surge ikut
        # ditutup karena gerbangnya justru dihitung dari error yang hilang.
        servo_pid.reset()
        servo_hits = _tally(servo_hits, False)
        servo_surge_out = _slew_limit(servo_surge_out, 0.0, slew_axis, dt)

        return clamp(servo_surge_out), 0, False

    servo_seen_hook = True
    center_x, frame_w = det
    ex = (center_x - frame_w / 2.0) / (frame_w / 2.0)

    sign = -1.0 if servo_cfg["invert_sway"] else 1.0
    sway = clamp(sign * servo_pid.step(ex, dt) * 10.0)

    di_tengah = abs(ex) < servo_cfg["center_tol_norm"]
    servo_hits = _tally(servo_hits, di_tengah)

    # Gerbang surge. Transisi 0 -> penuh tetap lewat slew: lompatan command
    # menyentak rangka, dan sentakan itu jatuh persis saat ROV paling dekat hook.
    servo_surge_out = _slew_limit(
        servo_surge_out, float(surge_step) if di_tengah else 0.0, slew_axis, dt)

    return (clamp(servo_surge_out), sway,
            servo_hits >= servo_cfg["centered_ticks"])


# ============================================================
# AUTONOMOUS - FULL COUNTER
# ============================================================

# Kolom `servo` menandai CASE yang menyetir sway dari deteksi hook. Ditandai
# per-langkah, bukan lewat nomor CASE, supaya urutan boleh diubah tanpa ada
# indeks ajaib yang diam-diam ikut bergeser.
AUTO_STEPS = [
    # duration, surge, sway, yaw, heave, gripper, depth, servo
    (3.0, 0, 0, 0, 0, None, None, False),
    (2.0, 0, 0, 0, 0, None, None, False),

    # contoh struktur command non-motion
    (1.0, 0, 0, 0, 0, None, None, False),
    (2.0, 0, 0, 0, 0, None, 1.0, False),

    # CASE 4 — satu-satunya yang bergerak. sway dihitung tiap tick dari hook,
    # surge di-gate sampai hook cukup di tengah, dan 3 detik adalah TIMEOUT:
    # CASE selesai lebih awal begitu hook terkunci di tengah.
    (3.0, -500, 0, 0, 0, None, None, True),

    (1.0, 0, 0, 0, 0, None, None, False),
]


auto_index = 0
auto_step_start = 0.0
auto_gripper_sent = False
auto_depth_sent = False
auto_finished = False


def autonomous_reset():
    global auto_index
    global auto_step_start
    global auto_gripper_sent
    global auto_depth_sent
    global auto_finished

    auto_index = 0
    auto_step_start = time.time()
    auto_gripper_sent = False
    auto_depth_sent = False
    auto_finished = False

    # Tuning dibaca ulang di sini: operator kolam cukup mengedit yaml lalu
    # menyalakan ulang mode autonomous, tanpa restart server.
    load_servo_config()
    servo_reset()

    print("[AUTO] FSM reset -> CASE 0")


def autonomous_control():
    global auto_index
    global auto_step_start
    global auto_gripper_sent
    global auto_depth_sent
    global auto_finished

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

    if auto_index >= len(AUTO_STEPS):
        send_motion(0, 0, 0, 0, src="fsm")
        auto_finished = True
        print("[AUTO] FSM selesai")
        return

    (duration, surge, sway, yaw, heave,
     gripper, depth_target, servo) = AUTO_STEPS[auto_index]

    elapsed = time.time() - auto_step_start

    # Command sekali saat memasuki CASE.
    if not auto_gripper_sent and gripper is not None:
        send_command("gripper", gripper)
        auto_gripper_sent = True
        print(f"[AUTO] CASE {auto_index} -> gripper={gripper}")

    if not auto_depth_sent and depth_target is not None:
        send_command("depth_apply", float(depth_target))
        auto_depth_sent = True
        print(
            f"[AUTO] CASE {auto_index} -> "
            f"depth_target={float(depth_target):.2f} m"
        )

    # CASE servo menimpa surge & sway dari deteksi hook; sisanya (yaw, heave)
    # tetap dari tabel. Kalau servo mati (config/ import gagal), CASE ini
    # berjalan sebagai langkah waktu biasa seperti sebelum YOLO disambungkan.
    selesai = elapsed >= duration
    alasan = "timeout"

    if servo and servo_cfg is not None:
        surge, sway, di_tengah = servo_step(surge)

        if di_tengah:
            selesai = True
            alasan = "hook di tengah"

    # Motion dikirim terus selama CASE aktif.
    send_motion(surge, sway, yaw, heave, src="fsm")

    if selesai:
        print(
            f"[AUTO] CASE {auto_index} selesai ({alasan}) | "
            f"motion=({surge},{sway},{yaw},{heave})"
        )

        # Hook tak pernah terlihat sepanjang CASE: lanjut ke CASE berikutnya,
        # jangan menggantung. Dicatat supaya trial yang "jalan tapi tidak
        # mengoreksi apa pun" bisa dibedakan dari trial yang servonya bekerja.
        if servo and servo_cfg is not None and not servo_seen_hook:
            print(f"[SERVO] CASE {auto_index}: hook TIDAK PERNAH terdeteksi "
                  f"— tidak ada koreksi sway sama sekali, lanjut ke CASE "
                  f"{auto_index + 1}")

        auto_index += 1
        auto_step_start = time.time()
        auto_gripper_sent = False
        auto_depth_sent = False
        servo_reset()


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

        set_mode(msg.get("value"))


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

            mode = get_mode()

            if mode == MODE_MANUAL:
                manual_control()

            elif mode == MODE_AUTONOMOUS:
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
