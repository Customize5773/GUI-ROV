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
TEL = re.compile(r"^\[TELEM\].*roll=(-?[\d.]+) pitch=(-?[\d.]+)")
SEND = re.compile(r"\[SEND\].*?(\{.*\})")

# Ambang "axis ini sedang didorong". Setengah skala: cukup tinggi untuk
# menyaring koreksi kecil, cukup rendah untuk menangkap manuver sungguhan.
AKTIF = 500
TENANG_DEG = 3.0  # |roll| & |pitch| di bawah ini = sudah tenang


def baca_gui(path):
    """[(axes, roll, pitch)] dalam urutan waktu."""
    cur = {a: 0 for a in AXES}
    rows = []
    with open(path, errors="ignore") as f:
        for line in f:
            m = CMD.match(line)
            if m:
                cur[m.group(1)] = int(m.group(2))
                continue
            m = TEL.match(line)
            if m:
                rows.append((dict(cur), float(m.group(1)), float(m.group(2))))
    return rows


def ringkas(tag, data):
    if len(data) < 30:
        return
    r = [d[0] for d in data]
    p = [d[1] for d in data]
    print(f"{tag:10s} {len(data):5d}   {st.mean(r):+7.2f} ± {st.pstdev(r):5.2f}"
          f"   {st.mean(p):+7.2f} ± {st.pstdev(p):5.2f}")


def waktu_tenang(rows):
    """Detik dari stik dilepas sampai roll & pitch kembali di bawah ambang."""
    bergerak = [any(v != 0 for v in c.values()) for c, _, _ in rows]
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
    with open(path, errors="ignore") as f:
        for line in f:
            m = SEND.search(line)
            if not m:
                continue
            try:
                out.append(eval(m.group(1)))  # log kami sendiri, dict Python
            except Exception:
                continue
    return out


def pwm_vertikal(rows):
    vals = [r["thruster_vertical_pwm"] for r in rows
            if isinstance(r.get("thruster_vertical_pwm"), (int, float))]
    hold = [r["thruster_vertical_pwm"] for r in rows
            if r.get("depth_hold") and isinstance(r.get("thruster_vertical_pwm"), (int, float))]
    return vals, hold


def depth_hold_windows(rows):
    """Kelompokkan sampel depth_hold=True berurutan jadi window, dengan
    err_awal/err_akhir (target - depth) dan rata-rata |roll|/|pitch|.

    Dipakai pertama kali secara manual untuk mendiagnosis trial 15 Agu 2026:
    error yang TIDAK mengecil sepanjang window (bahkan membesar) adalah
    tanda depth-hold "tidak respons", biasanya karena thruster vertikal
    sedang dipakai FC untuk melawan roll/pitch dari manuver surge/yaw
    bersamaan, bukan bug di perhitungan bias.
    """
    windows, cur = [], []
    for r in rows:
        if r.get("depth_hold"):
            cur.append(r)
        elif cur:
            windows.append(cur)
            cur = []
    if cur:
        windows.append(cur)

    out = []
    for w in windows:
        if len(w) < 10 or w[0].get("depth_target") is None:
            continue
        tgt = w[0]["depth_target"]
        out.append({
            "n": len(w),
            "durasi": len(w) * 0.1,
            "target": tgt,
            "err_awal": w[0]["depth"] - tgt,
            "err_akhir": w[-1]["depth"] - tgt,
            "roll": st.mean(abs(r["roll"]) for r in w),
            "pitch": st.mean(abs(r["pitch"]) for r in w),
        })
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 1

    rows = baca_gui(argv[0])
    if not rows:
        print(f"tidak ada telemetry di {argv[0]}")
        return 1

    print(f"\n{len(rows)} sampel telemetry dari {argv[0]}\n")
    print("kondisi        n     roll mean ± sd     pitch mean ± sd")
    ringkas("IDLE", [(r, p) for c, r, p in rows if all(v == 0 for v in c.values())])
    ringkas("MANUVER", [(r, p) for c, r, p in rows
                        if any(abs(v) > AKTIF for v in c.values())])
    for ax in AXES:
        for tanda, label in ((1, "+"), (-1, "-")):
            ringkas(ax + label, [
                (r, p) for c, r, p in rows
                if (c[ax] > AKTIF if tanda > 0 else c[ax] < -AKTIF)
                and all(abs(c[k]) <= AKTIF for k in AXES if k != ax)
            ])

    tenang, gagal = waktu_tenang(rows)
    if tenang:
        print(f"\nwaktu tenang (|roll|,|pitch| < {TENANG_DEG:.0f}°), "
              f"{len(tenang)} pelepasan stik, gagal dalam 15 s: {gagal}")
        print(f"  median {tenang[len(tenang) // 2]:.1f} s"
              f"   p90 {tenang[int(len(tenang) * 0.9)]:.1f} s"
              f"   maks {tenang[-1]:.1f} s")

    if len(argv) > 1:
        send_rows = baca_send(argv[1])

        vals, hold = pwm_vertikal(send_rows)
        if vals:
            bawah = sum(1 for v in vals if v < 1500)
            print(f"\nPWM thruster vertikal dari {argv[1]}")
            print(f"  di bawah netral 1500: {bawah}/{len(vals)} sampel"
                  "   (trim apung: makin mendekati separuh, makin netral)")
            if hold:
                print(f"  saat depth-hold ON: mean {st.mean(hold):.1f}"
                      f" sd {st.pstdev(hold):.1f}   (target 1490-1500)")

        windows = depth_hold_windows(send_rows)
        if windows:
            print(f"\n{len(windows)} window depth-hold ON dari {argv[1]}")
            print("  durasi  target  err_awal  err_akhir  |roll|  |pitch|")
            for w in windows:
                membaik = "OK" if abs(w["err_akhir"]) < abs(w["err_awal"]) else "MACET/MELEBAR"
                print(f"  {w['durasi']:5.1f}s  {w['target']:5.2f}   "
                      f"{w['err_awal']:+.3f}    {w['err_akhir']:+.3f}    "
                      f"{w['roll']:5.2f}   {w['pitch']:5.2f}   {membaik}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
