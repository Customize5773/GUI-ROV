"""Pemetaan MURNI axis GUI -> field MANUAL_CONTROL MAVLink.

Dipisah dari rov_agent.py supaya logika clamp/konversi bisa di-unit-test tanpa
bergantung pada pymavlink, socket, atau hardware.

Kenapa MANUAL_CONTROL (bukan RC_CHANNELS_OVERRIDE)?
    MANUAL_CONTROL adalah cara standar ArduSub menerima kontrol manual dari
    ground station: empat sumbu (x=maju/mundur, y=lateral, z=throttle, r=yaw)
    plus bitmask tombol. Ini tidak menimpa channel RC fisik dan lebih aman
    dipakai berdampingan dengan konfigurasi channel/servo di sisi Pixhawk.

Konvensi:
    GUI  : keempat axis -1000..1000, 0 = diam (lihat clampAxis di server.js).
    Wire : x, y, r -> -1000..1000 (netral 0); z -> 0..1000 (netral 500).
"""

# Rentang valid per axis di sisi GUI/UDP.
AXIS_RANGE = {
    "surge": (-1000, 1000),
    "sway": (-1000, 1000),
    "yaw": (-1000, 1000),
    "heave": (-1000, 1000),
}

# Nilai "diam" per axis dalam konvensi GUI.
AXIS_NEUTRAL = {"surge": 0, "sway": 0, "yaw": 0, "heave": 0}

# Netral throttle MANUAL_CONTROL.z di ArduSub.
Z_NEUTRAL = 500


def clamp_axis(name, value):
    """Clamp nilai axis ke rentang valid. Nilai tidak valid -> 0."""
    lo, hi = AXIS_RANGE.get(name, (-1000, 1000))
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(lo, min(hi, v))


def to_mavlink_z(heave):
    """Ubah heave GUI (-1000..1000, 0 = diam) ke MANUAL_CONTROL.z ArduSub
    (0..1000, 500 = diam)."""
    return max(0, min(1000, int(round(Z_NEUTRAL + clamp_axis("heave", heave) / 2.0))))


def axes_to_manual_control(surge=0, sway=0, yaw=0, heave=0, buttons=0):
    """Terjemahkan axis GUI ke field MANUAL_CONTROL.

    surge -> x (maju/mundur)
    sway  -> y (lateral kiri/kanan)
    heave -> z (throttle naik/turun)
    yaw   -> r (rotasi)
    buttons: bitmask tombol (placeholder; belum dipetakan ke tombol GUI).
    """
    return {
        "x": clamp_axis("surge", surge),
        "y": clamp_axis("sway", sway),
        "z": to_mavlink_z(heave),
        "r": clamp_axis("yaw", yaw),
        "buttons": int(buttons) & 0xFFFF,
    }


# Perintah netral / fail-safe: diam di tempat, throttle di tengah.
NEUTRAL = axes_to_manual_control(**AXIS_NEUTRAL)
