#!/usr/bin/env python3
"""
control_main.py
Pusat kontrol ROV:

MANUAL
    joystick.py -> UDP 14600 -> control_main -> UDP 14601 -> server.js

AUTONOMOUS
    full-counter FSM -> UDP 14601 -> server.js

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
import socket
import threading
import time

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

LOOP_HZ = 20.0
LOOP_DT = 1.0 / LOOP_HZ

JOYSTICK_TIMEOUT = 0.5

MODE_MANUAL = "manual"
MODE_AUTONOMOUS = "autonomous"

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


def send_motion(surge=0, sway=0, yaw=0, heave=0):
    packet = {
        "type": "control",
        "surge": clamp(surge),
        "sway": clamp(sway),
        "yaw": clamp(yaw),
        "heave": clamp(heave),
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


def manual_control():
    now = time.time()

    with joystick_lock:
        stale = (now - last_joystick_time) > JOYSTICK_TIMEOUT

        if stale:
            motion = (0, 0, 0, 0)
        else:
            motion = (
                joystick["surge"],
                joystick["sway"],
                joystick["yaw"],
                joystick["heave"],
            )

    send_motion(*motion)


# ============================================================
# AUTONOMOUS - FULL COUNTER
# ============================================================

AUTO_STEPS = [
    # duration, surge, sway, yaw, heave, gripper, depth
    (3.0, 0, 0, 0, 0, None, None),
    (2.0, 0, 0, 0, 0, None, None),

    # contoh struktur command non-motion
    (1.0, 0, 0, 0, 0, None, None),
    (2.0, 0, 0, 0, 0, None, 1.0),

    (3.0, -500, 0, 0, 0, None, None),

    (1.0, 0, 0, 0, 0, None, None),
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

    print("[AUTO] FSM reset -> CASE 0")


def autonomous_control():
    global auto_index
    global auto_step_start
    global auto_gripper_sent
    global auto_depth_sent
    global auto_finished

    if auto_finished:
        send_motion(0, 0, 0, 0)
        return

    if auto_index >= len(AUTO_STEPS):
        send_motion(0, 0, 0, 0)
        auto_finished = True
        print("[AUTO] FSM selesai")
        return

    duration, surge, sway, yaw, heave, gripper, depth_target = AUTO_STEPS[auto_index]

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

    # Motion dikirim terus selama CASE aktif.
    send_motion(surge, sway, yaw, heave)

    if elapsed >= duration:
        print(
            f"[AUTO] CASE {auto_index} selesai | "
            f"motion=({surge},{sway},{yaw},{heave})"
        )

        auto_index += 1
        auto_step_start = time.time()
        auto_gripper_sent = False
        auto_depth_sent = False


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
