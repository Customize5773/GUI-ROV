"""Pemetaan gain PID dari GUI ke parameter ArduSub + batas kedalaman kolam.

Dipisah dari rov_agent.py dengan alasan yang sama seperti rov_axes.py,
rov_modes.py, dan rov_params.py: bisa di-unit-test tanpa pymavlink/socket/
hardware. (rov_agent.py bind UDP 14550 di scope modul, jadi mengimpornya dari
test memang tidak mungkin.)

Dua lapis nama yang sengaja dibedakan
    - Nama GUI    : "yaw"/"depth" x "p"/"i"/"d" — yang dikirim halaman Setup
      lewat command {"name": "pid", "value": {"yaw": {...}, "depth": {...}}}.
    - Nama ArduSub: ATC_RAT_YAW_* dan PSC_ACCZ_* — yang benar-benar ditulis ke FC.

Kenapa depth -> PSC_ACCZ_*
    Depth hold ArduSub adalah KASKADE tiga loop: POSZ (posisi -> kecepatan),
    VELZ (kecepatan -> akselerasi), lalu ACCZ (akselerasi -> throttle). Tidak
    ada satu "PID depth". ACCZ dipilih karena itu knob tuning depth-hold
    standar di panduan ArduPilot DAN satu-satunya dari ketiganya yang punya
    P, I, dan D lengkap — jadi form 3 kolom di halaman Setup cocok apa adanya
    tanpa kolom yang menganggur atau menyesatkan.

Kenapa ada BATAS, dan kenapa MENOLAK bukan MENJEPIT
    Default PID di public/js/config.js bukan dalam satuan ArduSub (yaw.p = 2.0
    padahal ATC_RAT_YAW_P di wahana 0.18). Tanpa batas, sekali klik "Apply PID
    Gains" menaikkan gain rate yaw ~11x dan wahana bisa berosilasi hebat.

    Nilai di luar batas DITOLAK, bukan dijepit ke tepi rentang: menjepit
    diam-diam membuat operator mengira menulis 2.0 padahal yang masuk 1.0 —
    salah paham yang jauh lebih berbahaya daripada perintah yang gagal terang-
    terangan.

    RENTANG DI BAWAH INI DITULIS TANGAN, bukan dibaca dari metadata param
    ArduSub — metadata itu memang tidak tersedia offline (lihat
    Planning/PLAN-QgroundControl.md §6). Angkanya konservatif dan bertujuan
    menyaring kesalahan besaran (salah orde), BUKAN menjamin kestabilan.
    Menyetel gain tetap butuh uji kolam.
"""

# MAV_PARAM_TYPE_REAL32. Disalin, bukan diimpor dari pymavlink, supaya modul
# ini tetap murni; sudah dikonfirmasi dari dump nyata Pixhawk (kolom tipe = 9
# untuk keenam param di bawah).
REAL32 = 9

# (sumbu, gain) -> (nama param ArduSub, tipe, minimum, maksimum)
PID_PARAM_MAP = {
    ("yaw", "p"): ("ATC_RAT_YAW_P", REAL32, 0.0, 1.0),
    ("yaw", "i"): ("ATC_RAT_YAW_I", REAL32, 0.0, 1.0),
    ("yaw", "d"): ("ATC_RAT_YAW_D", REAL32, 0.0, 0.05),
    ("roll", "p"): ("ATC_RAT_RLL_P", REAL32, 0.0, 1.0),
    ("roll", "i"): ("ATC_RAT_RLL_I", REAL32, 0.0, 1.0),
    ("roll", "d"): ("ATC_RAT_RLL_D", REAL32, 0.0, 0.05),
    # Loop LUAR (angle -> target rate), komplemen ATC_RAT_RLL_* (rate -> output)
    # di atas. Default FC (parameters_ardusub.params) = 6.0; 3.0-12.0 ditulis
    # tangan sama seperti gain lain di file ini, longgar di kedua arah tapi
    # menyaring salah orde-besaran.
    ("roll", "ang_p"): ("ATC_ANG_RLL_P", REAL32, 3.0, 12.0),
    ("pitch", "p"): ("ATC_RAT_PIT_P", REAL32, 0.0, 1.0),
    ("pitch", "i"): ("ATC_RAT_PIT_I", REAL32, 0.0, 1.0),
    ("pitch", "d"): ("ATC_RAT_PIT_D", REAL32, 0.0, 0.05),
    ("pitch", "ang_p"): ("ATC_ANG_PIT_P", REAL32, 3.0, 12.0),
    ("depth", "p"): ("PSC_ACCZ_P", REAL32, 0.2, 1.5),
    ("depth", "i"): ("PSC_ACCZ_I", REAL32, 0.0, 3.0),
    ("depth", "d"): ("PSC_ACCZ_D", REAL32, 0.0, 0.4),
}

# Urutan tulis yang stabil: P lalu I lalu D, yaw -> roll -> pitch -> depth. Bukan
# sekadar rapi — urutan yang tetap membuat log agent bisa dibandingkan antar
# percobaan.
PID_WRITE_ORDER = (
    ("yaw", "p"), ("yaw", "i"), ("yaw", "d"),
    ("roll", "p"), ("roll", "i"), ("roll", "d"), ("roll", "ang_p"),
    ("pitch", "p"), ("pitch", "i"), ("pitch", "d"), ("pitch", "ang_p"),
    ("depth", "p"), ("depth", "i"), ("depth", "d"),
)

# Nama param -> (sumbu, gain). Dipakai sisi GUI/agent untuk arah sebaliknya:
# mengisi form dari PARAM_VALUE yang datang dari FC.
PARAM_TO_PID = {name: key for key, (name, _t, _lo, _hi) in PID_PARAM_MAP.items()}


def pid_param_names():
    """Nama keenam param, dalam urutan tulis. Dipakai untuk param_get massal."""
    return [PID_PARAM_MAP[key][0] for key in PID_WRITE_ORDER]


def resolve_pid_writes(payload):
    """Terjemahkan payload command `pid` jadi daftar tulis param.

    Mengembalikan (writes, rejects):
        writes  = [(nama_param, nilai_float, tipe), ...] siap ke set_param()
        rejects = [(label, alasan), ...] untuk dilaporkan balik ke GUI

    Kunci yang TIDAK ADA di payload dilewati diam-diam (bukan reject): halaman
    Setup boleh mengirim sebagian gain saja. Yang ADA tapi tidak valid selalu
    jadi reject — jangan pernah diam-diam mengabaikan angka yang sudah diketik
    operator.
    """
    writes = []
    rejects = []

    if not isinstance(payload, dict):
        return writes, [("pid", "payload bukan objek")]

    # PID_WRITE_ORDER melewati tiap sumbu tiga kali (p/i/d). Sumbu yang cacat
    # dilaporkan SEKALI saja, kalau tidak operator melihat tiga pesan identik
    # untuk satu kesalahan yang sama.
    axis_ditolak = set()

    for axis, gain in PID_WRITE_ORDER:
        section = payload.get(axis)
        if section is None:
            continue
        if not isinstance(section, dict):
            if axis not in axis_ditolak:
                axis_ditolak.add(axis)
                rejects.append((axis, "bagian bukan objek"))
            continue
        if gain not in section:
            continue

        name, type_id, lo, hi = PID_PARAM_MAP[(axis, gain)]
        raw = section[gain]

        # bool adalah subclass int di Python; True sebagai gain hampir pasti bug.
        if isinstance(raw, bool):
            rejects.append((name, f"nilai bukan angka: {raw!r}"))
            continue

        try:
            value = float(raw)
        except (TypeError, ValueError):
            rejects.append((name, f"nilai bukan angka: {raw!r}"))
            continue

        if value != value or value in (float("inf"), float("-inf")):
            rejects.append((name, "nilai harus finite"))
            continue

        if not (lo <= value <= hi):
            rejects.append((
                name,
                f"{value:g} di luar rentang aman {lo:g}..{hi:g} — "
                f"periksa satuan (nilai ArduSub, bukan skala GUI lama)",
            ))
            continue

        writes.append((name, value, type_id))

    if not writes and not rejects:
        rejects.append(("pid", "tidak ada gain yang dikenali di payload"))

    return writes, rejects


# Depth hold didelegasikan ke ALT_HOLD ArduSub; depth_target hanya menggeser
# setpoint lewat bias pada throttle. Dibatasi supaya tidak pernah bisa melawan
# operator atau menyelam tak terkendali di kolam dangkal.
#
# ALT_HOLD ArduSub punya DEADZONE throttle (param THR_DZ, saat ini 100 PWM):
# input dalam +-THR_DZ dari netral diartikan "tahan kedalaman sekarang", BUKAN
# perintah naik/turun. MANUAL_CONTROL.z 0..1000 dipetakan ke 1100..1900 PWM,
# jadi 1 unit z = 0.8 PWM dan deadzone 100 PWM = 125 unit z.
#
# Trial kolam 2026-08-08 membuktikan konsekuensinya: dengan LIMIT lama = 80
# (setara HANYA 64 PWM), bias maksimum pun masih tenggelam di dalam deadzone,
# sehingga perintah kedalaman TIDAK PERNAH sampai ke controller ALT_HOLD.
# Di CSV terlihat error -1.74 m (4x titik saturasi) selama >1 detik dengan
# depth rata sempurna di 1.89 m — wahana tidak bergerak sama sekali.
#
# Karena itu bias TIDAK naik proporsional dari nol, melainkan langsung
# di-offset melewati deadzone begitu bias diaktifkan (lihat depth_bias_active
# di bawah). Error nol tetap menghasilkan netral persis.
DEPTH_BIAS_GAIN = 200.0   # unit z per meter error, DI ATAS offset deadzone
DEPTH_BIAS_LIMIT = 200    # |bias| maksimum terhadap Z_NEUTRAL (500) = 160 PWM

# Offset minimum supaya perintah benar-benar keluar dari deadzone ALT_HOLD.
# NAIKKAN kalau THR_DZ di FC dinaikkan — keduanya harus jalan bersama.
#
# 22 Agu 2026 — angkanya DIVERIFIKASI LANGSUNG dari Pixhawk, bukan diasumsikan:
#   THR_DZ=100 PWM, RC3_MIN=1100, RC3_MAX=1900
#   → span 800 PWM / 1000 unit z  ⇒  1 unit z = 0,8 PWM
#   → keluar deadzone butuh > 100/0,8 = 125 unit z
# Pada ambang engage (error 0,05 m) magnitude = DEADZONE + 0,05*200 = DEADZONE+10:
#   130 → 140 unit = 112 PWM  → 12 PWM di atas deadzone, langsung mendorong ✓
#   115 → 125 unit = 100 PWM  → PERSIS di tepi, nol dorongan ✗
DEPTH_BIAS_DEADZONE = 130

# DUA ambang, bukan satu — ini HISTERESIS, dan perbedaannya menentukan apakah
# wahana menahan kedalaman atau berosilasi di sekitarnya.
#
# Bias tidak bisa naik landai dari nol (deadzone THR_DZ menelannya), jadi begitu
# aktif ia langsung melompat ke DEPTH_BIAS_DEADZONE. Dengan SATU ambang, error
# yang bergetar di sekitar ambang itu membuat dorongan penuh menyala-mati
# berulang kali: limit cycle klasik, wahana naik-turun melewati setpoint tanpa
# pernah tenang.
#
# Karena itu ambang MENYALA dibuat lebih besar dari ambang MATI:
#   - diam sampai error benar-benar berarti (>= ENGAGE), lalu
#   - dorong sampai error benar-benar kecil (< RELEASE), baru lepas.
# Selisih 0.03 m di antaranya adalah zona tenang tempat noise baro tidak bisa
# lagi memicu apa pun.
#
# RELEASE 0.02 m kira-kira sebesar noise pembacaan baro di kolam dangkal — di
# bawahnya "error" yang terlihat bukan error sungguhan, dan ALT_HOLD sudah
# menahan kedalaman dengan PID-nya sendiri. ENGAGE 0.05 m adalah toleransi
# akhir depth-set yang dijanjikan ke operator: tekan SET lalu OFF/ON akan
# kembali ke kedalaman yang sama dalam +-nilai itu, bukan lebih presisi.
#
# ENGAGE harus > RELEASE. Menyamakannya = kembali ke perilaku satu-ambang.
DEPTH_BIAS_ENGAGE = 0.05
DEPTH_BIAS_RELEASE = 0.02

# Redaman: kurangi dorongan begitu vehicle sudah bergerak menuju target dengan
# cukup cepat, supaya berhenti SEBELUM overshoot alih-alih terus mendorong
# sampai error kecil. Tanpa ini, DEPTH_BIAS_DEADZONE mendominasi magnitude di
# SELURUH rentang error praktis (untuk err < 0,65 m, GAIN*err < DEADZONE) —
# dorongan nyaris konstan ~140-170 sampai sangat dekat target. Trial 15 Agu
# 2026 (sesi setelah otoritas thruster dipulihkan lewat uji roll + kenaikan
# ATC_RAT_*_P) membuktikan konsekuensinya: depth berosilasi 0,15-0,57 m
# mengelilingi target 0,37 m selama 5 menit tanpa menetap — limit cycle
# klasik, bukan lagi macet di deadzone (masalah lama yang dipecahkan
# DEPTH_BIAS_DEADZONE) tapi overshoot karena tidak ada redaman sama sekali.
#
# Konservatif dulu: LEBIH BAIK redaman kurang (osilasi menyusut pelan)
# daripada lebih (bias jadi lemah mendekati nol, gagal menahan sama sekali).
# Naikkan lewat trial berikutnya kalau ayunan masih lebar (lihat
# tools/analyze_trial.py, kolom "ayunan").
DEPTH_BIAS_DAMPING = 300.0  # unit z per (m/s) laju error mengecil

# Jarak koreksi maksimum yang boleh dicoba bias — di luar ini, bias diam dan
# ArduSub ALT_HOLD asli (native, tanpa campur tangan Python) yang menahan,
# sama seperti operator memakai BlueOS/Cockpit langsung tanpa fitur SET kita.
#
# Trial 15-16 Agu 2026 membuktikan dua hal yang berlawanan sekaligus:
#   - ALT_HOLD native (bias OFF, stik netral) menahan RAPAT — sd 0,007 m
#     selama 130+ detik nyata, persis rasa Cockpit.
#   - Bias kita mencoba mengejar target JAUH (0,15-0,4 m) malah drift arah
#     TERBALIK dan berosilasi lambat tanpa henti (lihat window "BEROSILASI"
#     di tools/analyze_trial.py).
#
# Kesimpulannya: hukum bias ini (deadzone-jump + proporsional + redaman)
# cukup andal untuk KOREKSI KECIL (operator sudah dekat target, cuma perlu
# trim), tapi tidak untuk MENGGANTIKAN navigasi manual ke kedalaman jauh.
# Daripada terus menambal hukum bias untuk kasus yang ArduSub sendiri sudah
# tangani lebih baik secara native, batasi jangkauannya: SET yang sudah lama
# (operator berenang jauh sebelum menekan ON) dibiarkan diam, bukan dipaksa
# mengejar dan berisiko drift.
# 21 Agu 2026: nilai INI (0.35) disalin dari Pi, hasil penyetelan di tepi kolam
# yang tidak pernah kembali ke git.
#
# 22 Agu 2026 — KOREKSI, dan bacalah sebelum menyalin balik dari Pi lagi.
# Catatan 21 Agu menulis "DEPTH_BIAS_DEADZONE juga disalin dari Pi", tapi yang
# benar-benar disalin cuma 0.35; deadzone tetap 130 di repo sementara Pi
# memegang 115. Komentar dan kode di file yang sama saling membantah selama
# sehari.
#
# Yang menyelesaikannya bukan "mana yang lebih baru", melainkan pengukuran:
# THR_DZ=100 PWM & RC3 1100-1900 dibaca langsung dari FC (lihat blok
# DEPTH_BIAS_DEADZONE di atas) membuktikan 115 menaruh bias minimum DI DALAM
# deadzone firmware — depth-set tak menghasilkan dorongan sama sekali sampai
# error lewat ~5 cm. Jadi 115 bukan "nilai lapangan yang terbukti", melainkan
# salah setel yang kebetulan tidak pernah dipertanyakan karena wahana tetap
# bergerak (ALT_HOLD native yang menahannya, bukan bias kita).
#
# Pelajarannya: "yang jalan di Pi" ≠ "yang benar". Ukur, jangan salin.
#
# 0.35 dipertahankan karena datanya mendukung: dari 2.506 sampel depth-hold di
# tiga trial (hydro/ROV6/ROV7), gerbang ini hanya menyentuh 2 sampel, error
# terbesar 0.39 m. Melonggarkannya tidak membeli apa pun.
DEPTH_BIAS_MAX_CORRECTION = 0.35  # meter

# Pulsa sekali-tembak untuk depth_up/down di ALT_HOLD (bukan STABILIZE):
# ArduSub sudah punya cascade PID kedalaman di mode ini, jadi bias
# berkelanjutan (DEPTH_BIAS_* di atas) akan berebut channel z dengannya —
# osilasi yang dilaporkan pilot 2026-08-22. Pulsa pendek meniru operator
# menyentuh-lalu-lepas stik heave: dorong z sesaat, lalu netral, biarkan
# cascade ArduSub menahan sendiri kedalaman baru.
# ponytail: magnitude & durasi tebakan awal (belum divalidasi data kolam
# seperti DEPTH_BIAS_*) — kalibrasi di kolam: kalau step 0.05m terasa
# terlalu kecil/besar per pulsa, sesuaikan DEPTH_PULSE_DURATION_S dulu
# (bukan magnitude, supaya tidak ikut mengubah rasa dorongan STABILIZE).
DEPTH_PULSE_MAGNITUDE = 150   # unit z terhadap Z_NEUTRAL (500), searah DEPTH_BIAS_LIMIT
DEPTH_PULSE_DURATION_S = 0.35

# Pulsa rem sekali-tembak untuk surge/sway saat stick dilepas (rasa "brake"
# ala DJI): begitu |axis| turun dari atas epsilon ke bawahnya, dorong sesaat
# ke ARAH BERLAWANAN dari axis terakhir, lalu netral. Ini HANYA menghentikan
# momentum ROV sendiri lebih cepat — tidak ada DVL/GPS di bawah air, jadi
# TIDAK menahan posisi x/y melawan arus (lihat rov_modes.py baris 34-48).
# ponytail: magnitude & durasi tebakan awal, sama alasannya dengan
# DEPTH_PULSE_* di atas — kalibrasi di kolam.
# Naik dari 150/0.3s (trial 23 Agu 2026): tak terasa karena (a) slew klien
# (AXIS_SLEW_PER_SEC di joystick-state.js) sudah meluncurkan perintah ke 0
# dalam ~0,25s SEBELUM rilis terdeteksi, jadi momentum sudah banyak meluruh
# duluan, dan (b) 150 unit (~15% otoritas) kalah lawan drag air + lag ESC.
BRAKE_PULSE_MAGNITUDE = 450   # unit x/y terhadap netral (0)
BRAKE_PULSE_DURATION_S = 0.45


def depth_bias_active(error, was_active):
    """Apakah bias boleh mengalir untuk `error` ini, mengingat keadaan sebelumnya.

    Fungsi murni supaya histeresisnya bisa diuji tanpa state global; pemanggil
    (apply_depth_hold_bias di rov_agent.py) yang menyimpan `was_active` antar
    tick dan meresetnya saat depth-set dimatikan.
    """
    magnitude = abs(error)
    if was_active:
        return magnitude > DEPTH_BIAS_RELEASE
    return magnitude >= DEPTH_BIAS_ENGAGE


def depth_hold_bias(
    error,
    was_active=False,
    closing_rate=0.0,
    damping=DEPTH_BIAS_DAMPING,
    max_correction=DEPTH_BIAS_MAX_CORRECTION,
):
    """Bias z untuk satu nilai error kedalaman (meter, positif = perlu turun).

    Dipisah dari apply_depth_hold_bias() (rov_agent.py) supaya bisa diuji
    tanpa state global — modul ini tidak bind socket apa pun.

    Bentuknya BUKAN proporsional murni: ALT_HOLD ArduSub mengabaikan input di
    dalam deadzone THR_DZ, jadi bias proporsional dari nol berarti semua error
    kecil hilang tanpa jejak (dan error besar pun hilang kalau limit-nya lebih
    kecil dari deadzone — persis bug trial 2026-08-08). Maka: selama tidak
    aktif bias nol persis, dan begitu aktif ia langsung melompat ke
    DEPTH_BIAS_DEADZONE lalu bertambah proporsional.

    `was_active` adalah keluaran depth_bias_active() dari tick sebelumnya —
    lihat catatan histeresis di atas.

    `closing_rate`: seberapa cepat |error| MENGECIL saat ini (m/s, positif =
    mendekati target — lihat DEPTH_BIAS_DAMPING). Default 0.0 membuat
    perilaku IDENTIK dengan sebelum redaman ditambahkan, untuk caller lama
    yang belum menghitung rate.

    `max_correction`: di atas jarak ini, bias diam (0.0) dan ArduSub ALT_HOLD
    native yang menahan — lihat DEPTH_BIAS_MAX_CORRECTION. Dicek SETELAH
    histeresis (bukan sebelum) supaya begitu error turun kembali ke dalam
    jangkauan, `was_active` tidak perlu "dilupakan" dulu.
    """
    if not depth_bias_active(error, was_active):
        return 0.0

    if abs(error) > max_correction:
        return 0.0

    magnitude = DEPTH_BIAS_DEADZONE + abs(error) * DEPTH_BIAS_GAIN
    # max(0, closing_rate): rate yang MENJAUH dari target (negatif) tidak
    # boleh MENAMBAH dorongan lewat sini — itu tugas GAIN*err di atas.
    # max(0, ...) di luar: redaman hanya boleh MELEMAHKAN, tidak pernah
    # membalik arah dorongan.
    magnitude = max(0.0, magnitude - damping * max(0.0, closing_rate))
    magnitude = min(magnitude, DEPTH_BIAS_LIMIT)
    return magnitude if error > 0 else -magnitude


def smooth_depth(samples, alpha=0.5):
    """Rata-rata berbobot exponential dari daftar sampel kedalaman.

    Dipakai saat tombol SET: sampel baro bergetar ±0.02–0.05 m, dan kedalaman
    yang direkam bisa meleset dari yang sebenarnya oleh seluruh toleransi
    DEPTH_BIAS_RELEASE. Rata-rata 1 detik membersihkan noise itu.

    alpha=0.5: setengah dari nilai terbaru, setengah dari rata-rata historis.
    alpha=1.0: ambil nilai terbaru tanpa smoothing.
    """
    if not samples or len(samples) == 0:
        return 0.0
    if len(samples) == 1:
        return float(samples[0])

    smoothed = float(samples[0])
    for s in samples[1:]:
        smoothed = alpha * float(s) + (1.0 - alpha) * smoothed
    return smoothed


def clamp_depth_target(value, pool_depth=None):
    """Jepit setpoint kedalaman ke rentang yang masuk akal untuk kolam ini.

    Batas atas 0 m (permukaan) selalu berlaku. Batas bawah baru aktif setelah
    operator mengirim `pool_depth` — tanpa itu perilakunya persis seperti
    sebelumnya, jadi lupa mengirimnya tidak pernah membuat keadaan lebih buruk.

    Tanpa batas bawah, satu pembacaan baro yang meleset saat operator menekan
    SET di dekat dasar bisa merekam setpoint jauh di bawah kolam: bias throttle
    memang dibatasi DEPTH_BIAS_LIMIT sehingga bukan runaway, tapi ROV tetap
    ditekan ke dasar tanpa henti sampai depth-set dimatikan.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0

    if v != v:              # NaN
        return 0.0

    v = max(0.0, v)

    if pool_depth is not None:
        try:
            limit = float(pool_depth)
        except (TypeError, ValueError):
            return v
        if limit == limit and limit > 0:
            v = min(v, limit)

    return v


def valid_pool_depth(value):
    """Kembalikan pool depth yang sah (meter, > 0), atau None kalau ditolak."""
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    if v <= 0:
        return None
    return v
