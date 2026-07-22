"""Pemetaan MURNI axis joystick (persen -100..100) -> MANUAL_CONTROL MAVLink.

Dipisah dari rov_agent.py supaya logika mapping bisa di-unit-test tanpa
bergantung pada pymavlink / hardware / Gamepad API.

Kenapa MANUAL_CONTROL (bukan RC_CHANNELS_OVERRIDE)?
    MANUAL_CONTROL adalah cara standar ArduSub menerima kontrol manual dari
    ground station: empat sumbu (x=maju/mundur, y=lateral, z=throttle, r=yaw)
    plus bitmask tombol. Ini tidak menimpa channel RC fisik dan lebih aman
    dipakai berdampingan dengan konfigurasi channel/servo di sisi Pixhawk.

Rentang MANUAL_CONTROL:
    x, y, r : -1000 .. 1000   (netral 0)
    z       :     0 .. 1000   (netral 500)
"""


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def axis_percent_to_manual(p):
    """Sumbu bipolar: persen -100..100 -> -1000..1000 (netral 0)."""
    return int(round(_clamp(_to_float(p), -100.0, 100.0) * 10.0))


def throttle_percent_to_manual(p):
    """Throttle: persen -100..100 -> 0..1000 (netral 500)."""
    return int(round(_clamp(500.0 + _clamp(_to_float(p), -100.0, 100.0) * 5.0, 0.0, 1000.0)))


def axes_to_manual_control(surge=0, sway=0, yaw=0, heave=0, buttons=0):
    """Terjemahkan axis GUI (persen) ke field MANUAL_CONTROL.

    surge -> x (maju/mundur)
    sway  -> y (lateral kiri/kanan)
    heave -> z (throttle naik/turun)
    yaw   -> r (rotasi)
    buttons: bitmask tombol (placeholder/TODO; belum dipetakan ke tombol GUI).
    """
    return {
        "x": axis_percent_to_manual(surge),
        "y": axis_percent_to_manual(sway),
        "z": throttle_percent_to_manual(heave),
        "r": axis_percent_to_manual(yaw),
        "buttons": int(buttons) & 0xFFFF,
    }


# Perintah netral / fail-safe: diam di tempat, throttle di tengah.
NEUTRAL = axes_to_manual_control(0, 0, 0, 0)
