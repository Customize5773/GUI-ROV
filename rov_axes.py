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

Kenapa ada shaping (skala + rate-limit) di sini
    Trial 15 Agu 2026 (hydroships2.log + server/GUI.log, 8.614 sampel) mengukur
    dua hal yang terpisah:

      - Goyang TRANSIEN roll/pitch sd 7-11 derajat di semua manuver, vs 2,5-5
        derajat saat diam. Penyebabnya perintah pilot berbentuk STEP: histogram
        surge = 6697x nilai 0, 3502x nilai +1000, hanya ~150 nilai di antaranya.
        Stik di-mentok-kan dan tidak ada satu pun rate-limit di sepanjang jalur
        (server.js clampAxis pass-through, modul ini dulu juga pass-through).
        Waktu tenang setelah stik dilepas: p90 5,8 detik.
      - Miring TETAP roll -21,5 derajat saat sway penuh (asimetris terhadap
        +7,9 derajat ke arah sebaliknya). Ini kopling MEKANIS: thruster sway
        tunggal terpasang jauh dari sumbu roll, jadi dorongan lateral = torsi
        roll. Tidak ada gain PID yang bisa menghapusnya; yang bisa hanya
        mengurangi otoritas sway.

    Karena itu shaping punya DUA knob yang berbeda tujuan, bukan satu:
    `rise/fall` melawan yang transien, `scale` melawan yang tetap.

    Shaping ditaruh di sini (sisi Pi), BUKAN di GUI, karena joystick_sender()
    adalah satu-satunya titik cekik yang dilewati semua sumber input (keyboard,
    gamepad, apa pun nanti) dan ia berdetak pada dt tetap 20 Hz. Melakukannya
    di GUI berarti mengulanginya di tiap sumber, dan dt-nya ikut jitter
    jaringan.
"""

import math

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


# ---------------------------------------------------------------------------
# Axis shaping: skala otoritas + rate-limit
# ---------------------------------------------------------------------------

# (naik/detik, turun/detik, skala) dalam unit axis GUI per detik.
#
# naik 1400/s ~= 0,7 detik dari diam ke penuh. `turun` sengaja LEBIH CEPAT dari
# `naik`: melunakkan tarikan gas itu soal kenyamanan, sedangkan berhenti itu
# soal kendali — pilot yang melepas stik harus melihat wahana melambat segera.
#
# sway sengaja paling lambat DAN satu-satunya yang diperkecil (0,6). Ia
# penyumbang miring terbesar: roll mean -21,5 derajat pada sway penuh. Axis
# lain dibiarkan 1,0 dulu karena masalahnya di sana transien (sd tinggi, mean
# kecil), yang sudah ditangani rate-limit — kecilkan hanya kalau trial bilang
# perlu.
AXIS_SHAPE = {
    "surge": (1400.0, 2800.0, 1.0),
    "sway": (850.0, 2800.0, 0.6),
    "yaw": (1400.0, 2800.0, 1.0),
    "heave": (1800.0, 3000.0, 1.0),
}

# Dipakai untuk axis yang entah bagaimana tidak ada di AXIS_SHAPE: lewatkan apa
# adanya, jangan diam-diam menahan axis yang tidak dikenal.
_SHAPE_PASSTHROUGH = (5000.0, 5000.0, 1.0)

# Batas waras untuk tuning live lewat command `axis_shape`. Ini masukan dari
# jaringan, jadi rentangnya dijaga: scale 0 akan mematikan axis diam-diam, dan
# rate sangat kecil membuat wahana seolah tidak merespons.
SHAPE_LIMITS = {
    "rise": (200.0, 5000.0),
    "fall": (200.0, 5000.0),
    "scale": (0.1, 1.0),
}

# Urutan field di tuple AXIS_SHAPE.
_SHAPE_FIELDS = ("rise", "fall", "scale")


def shape_axes(current, target, dt, shape=AXIS_SHAPE):
    """Murni: dict axis baru, di-skala lalu dibatasi lajunya menuju `target`.

    Cermin dari slewToward() di public/js/axis-shaping.js — rumusnya sengaja
    sama persis supaya tidak ada versi ketiga yang berbeda perilaku.

    current : dict axis hasil panggilan sebelumnya (state dipegang PEMANGGIL,
              supaya fungsi ini tetap murni dan bisa diuji tanpa jam dinding).
    target  : dict axis mentah dari GUI.
    dt      : detik sejak panggilan terakhir (JOYSTICK_SEND_INTERVAL).

    `dt` atau rate yang tidak masuk akal -> langsung ke target. Menahan axis
    karena jam yang aneh jauh lebih berbahaya daripada melompat: wahana bisa
    berhenti merespons stik tanpa sebab yang terlihat.
    """
    try:
        step_dt = float(dt)
    except (TypeError, ValueError):
        step_dt = 0.0
    if not math.isfinite(step_dt):
        step_dt = 0.0

    current = current or {}
    target = target or {}

    out = {}
    for name in AXIS_NEUTRAL:
        cur = clamp_axis(name, current.get(name, 0))
        rise, fall, scale = shape.get(name, _SHAPE_PASSTHROUGH)
        tgt = clamp_axis(name, clamp_axis(name, target.get(name, 0)) * scale)

        # Menjauh dari nol pakai `rise`, mendekat pakai `fall`. Saat tanda
        # berbalik (mis. +500 -> -1000) |tgt| >= |cur| sehingga `rise` yang
        # dipakai — laju yang lebih lambat, jadi sisi amannya.
        rate = rise if abs(tgt) >= abs(cur) else fall
        step = rate * step_dt

        if not step > 0:
            out[name] = tgt
            continue

        delta = tgt - cur
        if abs(delta) <= step:
            out[name] = tgt
        else:
            out[name] = clamp_axis(name, cur + math.copysign(step, delta))

    return out


def resolve_shape_updates(payload):
    """Validasi payload command `axis_shape` -> (applied, rejects).

    Bentuk payload: {"sway": {"scale": 0.5, "rise": 700}, ...}

    Pola sengaja sama dengan resolve_pid_writes() di rov_pid.py: field yang
    TIDAK ADA dilewati diam-diam (update parsial itu wajar saat tuning), tapi
    field yang ADA namun tidak valid selalu jadi reject dengan alasan — bukan
    dijepit diam-diam. Operator yang mengetik angka salah harus tahu.

    Mengembalikan (applied, rejects); `applied` berupa
    {axis: (rise, fall, scale)} yang SUDAH tervalidasi. Fungsi ini tidak
    mengubah apa pun — lihat apply_shape_updates().
    """
    applied = {}
    rejects = []

    if not isinstance(payload, dict):
        return applied, [("axis_shape", "payload harus objek {axis: {...}}")]

    for axis, spec in payload.items():
        if axis not in AXIS_SHAPE:
            rejects.append((str(axis), "axis tidak dikenal"))
            continue
        if not isinstance(spec, dict):
            rejects.append((axis, "nilai harus objek {rise/fall/scale}"))
            continue

        nilai = dict(zip(_SHAPE_FIELDS, AXIS_SHAPE[axis]))
        gagal = False

        for field, raw in spec.items():
            if field not in SHAPE_LIMITS:
                rejects.append((f"{axis}.{field}", "field tidak dikenal"))
                gagal = True
                continue

            # bool lolos isinstance(x, int) di Python — tolak eksplisit, sama
            # seperti resolve_pid_writes.
            if isinstance(raw, bool):
                rejects.append((f"{axis}.{field}", "boolean bukan angka"))
                gagal = True
                continue
            try:
                v = float(raw)
            except (TypeError, ValueError):
                rejects.append((f"{axis}.{field}", f"bukan angka: {raw!r}"))
                gagal = True
                continue
            if not math.isfinite(v):
                rejects.append((f"{axis}.{field}", "NaN/inf"))
                gagal = True
                continue

            lo, hi = SHAPE_LIMITS[field]
            if not lo <= v <= hi:
                rejects.append((f"{axis}.{field}", f"di luar {lo}..{hi}: {v}"))
                gagal = True
                continue

            nilai[field] = v

        if not gagal:
            applied[axis] = tuple(nilai[f] for f in _SHAPE_FIELDS)

    return applied, rejects


def apply_shape_updates(payload):
    """Validasi lalu terapkan ke AXIS_SHAPE. Kembalikan (applied, rejects).

    Sengaja MUTASI dict AXIS_SHAPE di tempat (bukan rebind) supaya default arg
    `shape=AXIS_SHAPE` di shape_axes() ikut melihat nilai baru.
    """
    applied, rejects = resolve_shape_updates(payload)
    AXIS_SHAPE.update(applied)
    return applied, rejects

# Tidak ada axis baru dari GUI selama sekian detik = link/GUI dianggap mati.
IDLE_TIMEOUT = 0.5


def resolve_manual_packet(axes, last_update, now, timeout=IDLE_TIMEOUT):
    """Tentukan paket MANUAL_CONTROL yang harus dikirim saat ini.

    Dipisah dari rov_agent.joystick_sender() supaya keputusan fail-safe bisa
    di-unit-test tanpa thread, socket, maupun jam dinding.

    axes        : dict axis GUI terakhir yang diterima (-1000..1000).
    last_update : time.time() saat axis terakhir masuk.
    now         : waktu sekarang.
    timeout     : batas diam sebelum dianggap stale.

    Return (packet, stale). Saat stale -> NEUTRAL (x/y/r = 0, z = 500), yaitu
    diam di tempat; di ALT_HOLD ini berarti menahan kedalaman, bukan tenggelam.
    """
    if not last_update or (now - last_update) > timeout:
        return dict(NEUTRAL), True

    return axes_to_manual_control(
        surge=axes.get("surge", 0),
        sway=axes.get("sway", 0),
        yaw=axes.get("yaw", 0),
        heave=axes.get("heave", 0),
    ), False
