#!/usr/bin/env python3
"""Ukur ketenangan wahana dari log trial. Murni baca, tidak menyentuh ROV.

    python3 tools/analyze_trial.py server/GUI.log [hydroships.log]

Kenapa alat ini ada
    "ROV terasa lebih tenang" itu kesan, dan kesan tidak bisa dibandingkan
    antar trial. Angka yang sama, dihitung dengan cara yang sama, bisa.
    Skrip inilah yang dipakai mendiagnosis trial 15 Agu 2026 dan yang harus
    dipakai lagi sesudahnya supaya perbandingannya adil.

Yang dibaca
    GUI.log  : perintah axis + telemetry berselang-seling. Urutan baris jadi
               pengganti timestamp (telemetry ~10 Hz), cukup untuk memasangkan
               "sedang diperintah apa" dengan "miringnya berapa".
    log Pi   : opsional, untuk PWM thruster vertikal (indikator trim apung).

Angka kuncinya
    mean = miring TETAP (kopling mekanis, tidak bisa dihapus gain PID)
    sd   = goyang TRANSIEN (inilah yang dilawan rate-limit axis)
"""

import re
import statistics as st
import sys

AXES = ("surge", "sway", "yaw", "heave")
CMD = re.compile(r"^\[CMD\] (surge|sway|yaw|heave) = (-?\d+)")
TEL = re.compile(
    r"^\[TELEM\].*roll=(-?[\d.]+) pitch=(-?[\d.]+).*armed=(\w+) mode=(\w+)")
# Timestamp journald di log Pi ("Aug 15 12:32:41"). Tanpa tahun — lihat
# rentang_pi().
STAMP = re.compile(r"^([A-Z][a-z]{2}) ([ \d]\d) (\d\d):(\d\d):(\d\d)")
SEND = re.compile(r"\[SEND\].*?(\{.*\})")

# Ambang "axis ini sedang didorong". Setengah skala: cukup tinggi untuk
# menyaring koreksi kecil, cukup rendah untuk menangkap manuver sungguhan.
AKTIF = 500
TENANG_DEG = 3.0  # |roll| & |pitch| di bawah ini = sudah tenang
TOLERANSI_M = 0.05  # |err| akhir di bawah ini = sampai di target
OSC_THRESHOLD = 0.10  # ayunan depth (err_max - err_min) di atas ini = berosilasi


def baris(path):
    """Baris file log, apa pun encoding-nya.

    PowerShell/npm di Windows menulis redirect sebagai UTF-16 ber-BOM. Dibaca
    sebagai UTF-8 dengan errors="ignore", setiap NUL hilang tapi barisnya
    tetap "terbaca" — regex berhenti cocok dan alat ini melapor "tidak ada
    telemetry" pada log yang isinya lengkap (ROV5.log, 21 Agu 2026).
    """
    b = open(path, "rb").read()
    enc = "utf-16" if b[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    return b.decode(enc, "ignore").splitlines()


def baca_gui(path):
    """([(axes, roll, pitch, armed, mode)], asimetri) dalam urutan waktu.

    `armed`/`mode` dulu dibuang di regex, dan itu menyembunyikan sinyal
    terpenting di seluruh log: sampel DISARMED-mengambang adalah satu-satunya
    saat sikap wahana terukur TANPA dorongan apa pun. Tercampur ke bucket IDLE,
    ia menarik rata-rata roll ke arah yang menghapus buktinya (trial 19 Agu
    2026: disarmed -2,00 + ALT_HOLD +0,26 terbaca jadi IDLE +0,10).

    `asimetri` menghitung perintah per axis saat ARMED: {axis: [pos, neg, nol]}.
    Lihat cetak_asimetri() untuk kenapa itu perlu.
    """
    cur = {a: 0 for a in AXES}
    rows = []
    asim = {a: [0, 0, 0] for a in AXES}
    armed, mode = False, "?"
    for line in baris(path):
            m = CMD.match(line)
            if m:
                v = int(m.group(2))
                cur[m.group(1)] = v
                if armed:
                    asim[m.group(1)][0 if v > 0 else 1 if v < 0 else 2] += 1
                continue
            m = TEL.match(line)
            if m:
                armed = m.group(3) == "true"
                mode = m.group(4)
                rows.append(
                    (dict(cur), float(m.group(1)), float(m.group(2)), armed, mode))
    return rows, asim


def ringkas(tag, data):
    if len(data) < 30:
        return
    r = [d[0] for d in data]
    p = [d[1] for d in data]
    print(f"{tag:10s} {len(data):5d}   {st.mean(r):+7.2f} ± {st.pstdev(r):5.2f}"
          f"   {st.mean(p):+7.2f} ± {st.pstdev(p):5.2f}")


def waktu_tenang(rows):
    """Detik dari stik dilepas sampai roll & pitch kembali di bawah ambang."""
    bergerak = [any(v != 0 for v in c.values()) for c, *_ in rows]
    hasil, gagal = [], 0
    for i in range(1, len(rows)):
        if not (bergerak[i - 1] and not bergerak[i]):
            continue
        for j in range(i, min(i + 150, len(rows))):
            if bergerak[j]:
                break  # digerakkan lagi sebelum sempat tenang
            if abs(rows[j][1]) < TENANG_DEG and abs(rows[j][2]) < TENANG_DEG:
                hasil.append((j - i) * 0.1)
                break
        else:
            gagal += 1
    return sorted(hasil), gagal


def baca_send(path):
    """Semua baris [SEND] dari log Pi, sebagai list dict state (10 Hz)."""
    out = []
    for line in baris(path):
            m = SEND.search(line)
            if not m:
                continue
            try:
                out.append(eval(m.group(1)))  # log kami sendiri, dict Python
            except Exception:
                continue
    return out


def rentang_pi(path):
    """(stamp_pertama, stamp_terakhir) dari timestamp journald, atau None.

    journald tidak mencetak tahun, jadi ini string "Aug 07 19:50:13" yang
    dibandingkan apa adanya — cukup untuk mendeteksi pasangan log yang salah,
    tidak dipakai untuk aritmatika waktu.
    """
    first = last = None
    for line in baris(path):
            m = STAMP.match(line)
            if m:
                last = m.group(0)
                if first is None:
                    first = last
    return (first, last) if first else None


def peringatan_sesi(gui_path, pi_path):
    """Peringatkan kalau log GUI dan log Pi bukan dari sesi yang sama.

    Log GUI sama sekali tidak bertimestamp (urutan baris = jam-nya), jadi satu-
    satunya penanda waktunya adalah mtime file. Itu rapuh — menyalin file tanpa
    `cp -p` menyetel ulang mtime — tapi rapuh masih jauh lebih baik daripada
    perilaku lama, yang diam-diam melaporkan angka depth-hold dari run yang
    BERBEDA dari tabel attitude-nya (19 Agu 2026: GUI15.log 19 Agu dipasangkan
    dengan ROV1.log yang berakhir 15 Agu, dan tidak ada satu pun tanda di
    output). Peringatan yang kadang salah alarm masih bisa diabaikan manusia;
    angka gabungan yang salah diam-diam tidak bisa.
    """
    rng = rentang_pi(pi_path)
    if not rng:
        return
    import datetime
    import os
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(gui_path))
    tag = mtime.strftime("%b %d")
    awal, akhir = rng[0][:6], rng[1][:6]
    if not awal <= tag <= akhir:
        print(f"\n  PERINGATAN: {pi_path} mencakup {awal} .. {akhir},"
              f" sedangkan {gui_path} terakhir ditulis {tag}.")
        print("  Kemungkinan besar BUKAN sesi yang sama — angka dari log Pi di"
              " bawah ini\n  tidak boleh dibaca sebagai penjelasan tabel di atas.")


def cetak_asimetri(asim):
    """Rasio perintah pos:neg per axis, saat ARMED.

    Wahana ini tidak punya sensor posisi lateral apa pun, jadi drift menyamping
    tidak pernah muncul langsung di log mana pun. Yang bisa dilihat adalah
    KOREKSI pilot: kalau ROV terus hanyut ke satu arah, pilot terus mendorong
    stik ke arah lawannya, dan hitungan perintahnya jadi berat sebelah.

    Axis lain adalah kontrolnya. Trial 19 Agu 2026: surge 0,9:1 dan yaw 1,3:1
    (seimbang, seperti seharusnya) sementara sway 7,1:1 — itulah yang mengubah
    "rasanya ngedrift" jadi angka, dan yang harus turun mendekati 1:1 kalau
    perbaikannya benar-benar bekerja.
    """
    print("\nasimetri perintah pilot saat ARMED"
          "  (proxy drift: axis lain = kontrol)")
    print("axis        pos    neg    nol   pos:neg")
    for ax in AXES:
        pos, neg, nol = asim[ax]
        if pos + neg < 25:
            continue
        rasio = f"{pos / neg:.1f}:1" if neg else "inf"
        print(f"{ax:8s} {pos:6d} {neg:6d} {nol:6d}   {rasio:>7s}")


def pwm_lateral(rows):
    """PWM T6 (thruster sway) dari log Pi, dipisah per mode ArduSub.

    T6 sendirian membawa lateral 1.0 DAN roll -0,25 di frame BlueROV1, jadi
    ArduSub ikut memakainya untuk mengoreksi roll. Kalau T6 duduk jauh dari
    netral saat stik netral, dorongan menyamping itu diperintahkan FC — bukan
    tarikan tether dan bukan arus. Dipisah per mode karena itulah bisect-nya:
    MANUAL tidak menstabilkan attitude, ALT_HOLD ya. Menyimpang hanya di
    ALT_HOLD = kebocoran koreksi roll; menyimpang di keduanya = netral ESC
    (SERVO6_TRIM).
    """
    per_mode = {}
    for r in rows:
        v = r.get("thruster_lateral_pwm")
        if isinstance(v, (int, float)) and v:
            per_mode.setdefault(r.get("mode", "?"), []).append(v)
    return per_mode


def pwm_vertikal(rows):
    vals = [r["thruster_vertical_pwm"] for r in rows
            if isinstance(r.get("thruster_vertical_pwm"), (int, float))]
    hold = [r["thruster_vertical_pwm"] for r in rows
            if r.get("depth_hold") and isinstance(r.get("thruster_vertical_pwm"), (int, float))]
    return vals, hold


def depth_hold_windows(rows):
    """Kelompokkan sampel depth_hold=True berurutan jadi window, dengan
    err_awal/err_akhir (target - depth) dan rata-rata |roll|/|pitch|.

    Dipakai pertama kali secara manual untuk mendiagnosis trial 15 Agu 2026.
    PENTING: baris dari sini (`rows`, hasil baca_send()) datang dari
    hydroships*.log, yang di-throttle 1 Hz oleh rov_agent.py
    (_last_telem_log) — BEDA dari server/GUI.log yang mencetak tiap paket
    UDP tanpa throttle (10 Hz). Durasi window karena itu `len(w) * 1.0`,
    BUKAN `* 0.1` — sempat salah 10x di versi awal alat ini dan membuat
    window 5 menit yang berosilasi terbaca sebagai "29,8 detik, OK".
    Endpoint (err_awal/err_akhir) saja TIDAK CUKUP untuk menilai window:
    ayunan lebar bisa kebetulan berakhir dekat titik awal. err_min/err_max
    itulah yang mengungkap osilasi yang endpoint-nya sembunyikan.
    """
    # Window PECAH juga saat depth_target berubah, bukan cuma saat depth_hold
    # mati. Tombol SET boleh ditekan berkali-kali tanpa mematikan hold, dan
    # trial 21 Agu 2026 memang begitu: SATU window 33 menit berisi 162 setpoint
    # berbeda. Err terhadap target PERTAMA lalu jadi omong kosong — ayunan
    # terbaca 0,46 m ("BEROSILASI") padahal tiap hold sesungguhnya menetap
    # dengan ayunan <=0,15 m. Metrik yang salah lebih buruk dari tidak ada.
    windows, cur = [], []
    for r in rows:
        if not r.get("depth_hold"):
            if cur:
                windows.append(cur)
                cur = []
            continue
        if cur and r.get("depth_target") != cur[-1].get("depth_target"):
            windows.append(cur)
            cur = []
        cur.append(r)
    if cur:
        windows.append(cur)

    out = []
    for w in windows:
        if len(w) < 10 or w[0].get("depth_target") is None:
            continue
        tgt = w[0]["depth_target"]
        errs = [r["depth"] - tgt for r in w]
        out.append({
            "n": len(w),
            "durasi": len(w) * 1.0,
            "target": tgt,
            "err_awal": errs[0],
            "err_akhir": errs[-1],
            "err_min": min(errs),
            "err_max": max(errs),
            # Ayunan diukur dari EKOR window (10 detik terakhir), bukan seluruh
            # window: hold yang sehat pun berangkat dari error awal besar, dan
            # rentang seluruh window karena itu mengukur jarak tempuh
            # pendekatan, bukan ketenangan. Yang ditanya "sudah tenang belum
            # di akhir", dan itu cuma bisa dijawab ekornya.
            "ayunan": max(errs[-10:]) - min(errs[-10:]),
            "roll": st.mean(abs(r["roll"]) for r in w),
            "pitch": st.mean(abs(r["pitch"]) for r in w),
        })
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 1

    rows, asim = baca_gui(argv[0])
    if not rows:
        print(f"tidak ada telemetry di {argv[0]}")
        return 1

    print(f"\n{len(rows)} sampel telemetry dari {argv[0]}\n")
    print("kondisi        n     roll mean ± sd     pitch mean ± sd")

    # IDLE dipecah per (mode, armed), TIDAK digabung. Menggabungkannya berarti
    # merata-ratakan wahana yang mengambang mati bersama wahana yang sedang
    # aktif menahan sikapnya — dua hal fisik yang berbeda. Baris DISARMED
    # adalah sikap ISTIRAHAT sejati wahana; simpangannya dari nol mengukur
    # ketidakseimbangan ballast, dan itu yang harus dilawan controller (lewat
    # T6, lihat pwm_lateral) sepanjang sisa trial.
    idle = [(r, p, md, a) for c, r, p, a, md in rows
            if all(v == 0 for v in c.values())]
    for md, a in sorted({(d[2], d[3]) for d in idle},
                        key=lambda k: -sum(1 for d in idle
                                           if (d[2], d[3]) == k)):
        tag = f"{md}/{'armed' if a else 'mati'}"
        ringkas(tag, [(d[0], d[1]) for d in idle if (d[2], d[3]) == (md, a)])

    ringkas("MANUVER", [(r, p) for c, r, p, a, md in rows
                        if any(abs(v) > AKTIF for v in c.values())])
    for ax in AXES:
        for tanda, label in ((1, "+"), (-1, "-")):
            ringkas(ax + label, [
                (r, p) for c, r, p, a, md in rows
                if (c[ax] > AKTIF if tanda > 0 else c[ax] < -AKTIF)
                and all(abs(c[k]) <= AKTIF for k in AXES if k != ax)
            ])

    cetak_asimetri(asim)

    tenang, gagal = waktu_tenang(rows)
    if tenang:
        print(f"\nwaktu tenang (|roll|,|pitch| < {TENANG_DEG:.0f}°), "
              f"{len(tenang)} pelepasan stik, gagal dalam 15 s: {gagal}")
        print(f"  median {tenang[len(tenang) // 2]:.1f} s"
              f"   p90 {tenang[int(len(tenang) * 0.9)]:.1f} s"
              f"   maks {tenang[-1]:.1f} s")

    if len(argv) > 1:
        peringatan_sesi(argv[0], argv[1])
        send_rows = baca_send(argv[1])

        lat = pwm_lateral(send_rows)
        if lat:
            print(f"\nPWM T6 (thruster sway) dari {argv[1]}"
                  f"   netral = SERVO6_TRIM 1500")
            print("  mode          n     mean   simpangan   sd")
            for md, v in sorted(lat.items(), key=lambda kv: -len(kv[1])):
                print(f"  {md:10s} {len(v):5d}   {st.mean(v):6.0f}"
                      f"   {st.mean(v) - 1500:+8.0f}   {st.pstdev(v):5.1f}")

        vals, hold = pwm_vertikal(send_rows)
        if vals:
            # Trim apung diukur dari BESAR simpangan rata-rata, bukan dari
            # jumlah sampel di bawah 1500.
            #
            # Trial 16 Agu 2026 menunjukkan kenapa: setelah PSC_ACCZ_I dinaikkan,
            # "di bawah netral" MEMBURUK (77% -> 88%) sementara simpangan
            # rata-rata MEMBAIK (75 -> 50 PWM). Integrator yang lebih cepat
            # membuat PWM duduk rapat di satu pita tepat di bawah netral alih-
            # alih mengayun lebar sampai kadang melewati 1500 — dan hitungan
            # sampel justru menghukum pita rapat itu. Metrik yang bisa bergerak
            # berlawanan dengan perbaikan nyata lebih buruk daripada tidak ada.
            offset = st.mean(vals) - 1500
            bawah = sum(1 for v in vals if v < 1500)
            print(f"\nPWM thruster vertikal dari {argv[1]}")
            print(f"  trim apung: mean {st.mean(vals):.0f}"
                  f" = {offset:+.0f} PWM dari netral"
                  f"   (target ±10; makin kecil |simpangan|, makin netral apung)")
            print(f"  sebaran: sd {st.pstdev(vals):.1f},"
                  f" {bawah}/{len(vals)} sampel di bawah 1500")
            if hold:
                print(f"  saat depth-hold ON: mean {st.mean(hold):.1f}"
                      f" sd {st.pstdev(hold):.1f}   (target 1490-1500)")

        windows = depth_hold_windows(send_rows)
        if windows:
            print(f"\n{len(windows)} window depth-hold ON dari {argv[1]}")
            print("  durasi   target  err_awal  err_akhir  ayunan  |roll|  |pitch|")
            for w in windows:
                ayunan = w["ayunan"]
                # Rentang ayunan itu sendiri yang menentukan tenang/tidak —
                # endpoint saja BISA kebetulan berdekatan padahal di antaranya
                # berosilasi lebar (lihat docstring depth_hold_windows).
                # Urutannya penting: tenang DULU, baru dekat. Membandingkan
                # err_akhir dengan err_awal (versi lama) menghukum window yang
                # sudah tepat sejak awal — err 0,00 -> -0,01 terbaca "MELEBAR"
                # padahal itu hold terbaik di seluruh trial.
                verdict = ("BEROSILASI" if ayunan >= OSC_THRESHOLD
                           else "OK" if abs(w["err_akhir"]) <= TOLERANSI_M
                           else "MELESET")
                menit = f"{w['durasi']/60:4.1f}m" if w["durasi"] >= 60 else f"{w['durasi']:4.0f}s"
                print(f"  {menit:>7s}  {w['target']:5.2f}   "
                      f"{w['err_awal']:+.3f}    {w['err_akhir']:+.3f}    "
                      f"{ayunan:.3f}   {w['roll']:5.2f}   {w['pitch']:5.2f}   {verdict}")
    print()
    return 0


def selftest():
    """python3 tools/analyze_trial.py --selftest

    Menjaga satu hal yang gampang rusak diam-diam: regex TEL harus tetap
    menangkap armed/mode dari baris [TELEM] SUNGGUHAN, dan hitungan asimetri
    harus mengabaikan perintah saat DISARMED. Kalau format log berubah, regex
    berhenti cocok dan seluruh tabel jadi kosong tanpa error — itu kegagalan
    yang harus berisik.
    """
    import tempfile
    import os

    contoh = (
        "[TELEM] from 192.168.2.2:55211 | heading=221.6 roll=-2.19 pitch=-4.48"
        " volt=0.657 armed=false mode=MANUAL\n"
        "[CMD] sway = -1000 -> 192.168.2.2:14550\n"   # disarmed: tidak dihitung
        "[TELEM] from 192.168.2.2:55211 | heading=221.5 roll=-2.16 pitch=-4.30"
        " volt=0.653 armed=true mode=ALT_HOLD\n"
        "[CMD] sway = 1000 -> 192.168.2.2:14550\n"
        "[CMD] surge = 0 -> 192.168.2.2:14550\n"
        "[TELEM] from 192.168.2.2:55211 | heading=221.4 roll=0.26 pitch=-1.35"
        " volt=0.650 armed=true mode=ALT_HOLD\n"
    )
    fd, path = tempfile.mkstemp(suffix=".log")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(contoh)
        rows, asim = baca_gui(path)
    finally:
        os.unlink(path)

    assert len(rows) == 3, rows
    assert rows[0][3] is False and rows[0][4] == "MANUAL", rows[0]
    assert rows[1][3] is True and rows[1][4] == "ALT_HOLD", rows[1]
    # sway -1000 datang saat disarmed -> tidak boleh masuk hitungan
    assert asim["sway"] == [1, 0, 0], asim["sway"]
    assert asim["surge"] == [0, 0, 1], asim["surge"]
    # sample-and-hold: baris terakhir membawa sway=1000 dari [CMD] sebelumnya
    assert rows[2][0]["sway"] == 1000, rows[2][0]

    # depth_hold_windows harus PECAH saat depth_target berubah walau hold
    # tidak pernah mati (tombol SET ditekan ulang) — kalau tidak, err diukur
    # terhadap target yang sudah basi. Lihat komentar di depth_hold_windows.
    def sampel(tgt, depth):
        return {"depth_hold": True, "depth_target": tgt, "depth": depth,
                "roll": 0.0, "pitch": 0.0}
    w = depth_hold_windows([sampel(0.1, 0.1)] * 12 + [sampel(0.5, 0.5)] * 12)
    assert len(w) == 2, w
    assert [x["target"] for x in w] == [0.1, 0.5], w
    assert all(abs(x["err_akhir"]) < 1e-9 for x in w), w

    print("selftest ok")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"]:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))
