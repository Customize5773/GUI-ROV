
#!/usr/bin/env python3
import json
import math
import socket
import time

import pygame


# ============================================================
# JOYSTICK -> CONTROL MAIN
# ============================================================

CONTROL_MAIN_IP = "127.0.0.1"
CONTROL_MAIN_PORT = 14600

SEND_HZ = 20
SEND_PERIOD = 1.0 / SEND_HZ


# ============================================================
# F310
# HANYA 4 AXIS
# ============================================================

# Sesuai hasil pembacaan F310 Anda
AXIS_YAW = 0
AXIS_HEAVE = 1
AXIS_SWAY = 2
AXIS_SURGE = 3


# ============================================================
# MAPPING PROFIL
# ============================================================

# Mengikuti hydroship-joystick-2026-09-07.json
#
# Axis 0 -> Axis R -> YAW
# Axis 1 -> Axis Z -> HEAVE
# Axis 2 -> Axis Y -> SWAY
# Axis 3 -> Axis X -> SURGE

YAW_MIN = -600
YAW_MAX = 600

HEAVE_MIN = 1000
HEAVE_MAX = -1000

SWAY_MIN = -1000
SWAY_MAX = 1000

SURGE_MIN = 1000
SURGE_MAX = -1000


# Profil Anda
DEADZONE = 0.0
EXPO = 4.0


# ============================================================
# AXIS MAPPING
# ============================================================

def clamp(value, minimum=-1.0, maximum=1.0):
    return max(minimum, min(maximum, float(value)))


def apply_deadzone_expo(value):
    value = clamp(value)

    magnitude = abs(value)

    if magnitude <= DEADZONE:
        return 0.0

    scaled = (magnitude - DEADZONE) / (1.0 - DEADZONE)

    return math.copysign(
        scaled ** EXPO,
        value
    )


def map_axis(raw, minimum, maximum):
    """
    Mapping sama seperti mapAxisValue()
    pada joystick-profile.js.
    """

    value = apply_deadzone_expo(raw)

    # Reverse jika min > max
    if minimum > maximum:
        value *= -1.0

    low = min(minimum, maximum)
    high = max(minimum, maximum)

    result = low + (
        ((value + 1.0) / 2.0)
        * (high - low)
    )

    return round(result)


# ============================================================
# UDP
# ============================================================

sock = socket.socket(
    socket.AF_INET,
    socket.SOCK_DGRAM
)


def send_joystick(
    surge,
    sway,
    heave,
    yaw
):

    packet = {
        "type": "joystick",

        "surge": int(surge),
        "sway": int(sway),
        "heave": int(heave),
        "yaw": int(yaw),

        "timestamp": time.time()
    }

    data = json.dumps(
        packet,
        separators=(",", ":")
    ).encode("utf-8")

    sock.sendto(
        data,
        (
            CONTROL_MAIN_IP,
            CONTROL_MAIN_PORT
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:

        print("Joystick tidak ditemukan.")

        return

    joystick = pygame.joystick.Joystick(0)
    joystick.init()

    print("=" * 60)
    print("HYDROSHIPS - JOYSTICK INPUT")
    print("=" * 60)

    print(
        "Joystick :",
        joystick.get_name()
    )

    print(
        "Axes     :",
        joystick.get_numaxes()
    )

    print(
        "Buttons  :",
        joystick.get_numbuttons()
    )

    print()

    print("MAPPING AXIS:")

    print("Axis 0 -> YAW")
    print("Axis 1 -> HEAVE")
    print("Axis 2 -> SWAY")
    print("Axis 3 -> SURGE")

    print()

    print("Axis 4 dan Axis 5 DIABAIKAN.")

    print()

    print(
        "Output   :",
        f"{CONTROL_MAIN_IP}:{CONTROL_MAIN_PORT}"
    )

    print("=" * 60)

    try:

        while True:

            pygame.event.pump()

            # ------------------------------------------------
            # BACA AXIS
            # ------------------------------------------------

            raw_yaw = joystick.get_axis(
                AXIS_YAW
            )

            raw_heave = joystick.get_axis(
                AXIS_HEAVE
            )

            raw_sway = joystick.get_axis(
                AXIS_SWAY
            )

            raw_surge = joystick.get_axis(
                AXIS_SURGE
            )

            # ------------------------------------------------
            # MAP AXIS
            # ------------------------------------------------

            yaw = map_axis(
                raw_yaw,
                YAW_MIN,
                YAW_MAX
            )

            heave = map_axis(
                raw_heave,
                HEAVE_MIN,
                HEAVE_MAX
            )

            sway = map_axis(
                raw_sway,
                SWAY_MIN,
                SWAY_MAX
            )

            surge = map_axis(
                raw_surge,
                SURGE_MIN,
                SURGE_MAX
            )

            # ------------------------------------------------
            # KIRIM KE CONTROL_MAIN.PY
            # ------------------------------------------------

            send_joystick(
                surge,
                sway,
                heave,
                yaw
            )

            # ------------------------------------------------
            # MONITOR
            # ------------------------------------------------

            print(
                f"\r"
                f"SURGE:{surge:5d}  "
                f"SWAY:{sway:5d}  "
                f"HEAVE:{heave:5d}  "
                f"YAW:{yaw:5d}",
                end="",
                flush=True
            )

            time.sleep(
                SEND_PERIOD
            )

    except KeyboardInterrupt:

        print(
            "\nJoystick dihentikan."
        )

    finally:

        # Netral saat program berhenti
        try:

            send_joystick(
                0,
                0,
                0,
                0
            )

        except Exception:
            pass

        joystick.quit()

        pygame.joystick.quit()

        pygame.quit()

        sock.close()


if __name__ == "__main__":

    main()