#!/usr/bin/env python3
"""
tools/analyze_run.py — Ringkas run JSONL misi autonomous jadi laporan trial.

Dipakai setelah tiap trial di kolam:
    python tools/analyze_run.py logs/run_20260817_101500.jsonl      # satu run, detail
    python tools/analyze_run.py logs/*.jsonl                        # tabel antar-trial
    python tools/analyze_run.py logs/run_....jsonl --json           # utk panel GUI

Mode tabel adalah yang menjawab "analisis tiap trial/error": satu baris per run,
sehingga tren skor dan detection-rate antar percobaan kelihatan, dan bisa dikaitkan
ke nilai tuning yang dipakai (event `config` tiap file).

Catatan: ini BUKAN pengganti tests/evaluate_mission5.py. Evaluator itu memakai
ground-truth simulator (posisi sejati, status hook) yang tak ada di hardware;
file ini hanya memakai apa yang benar-benar terukur saat trial nyata.
"""
import argparse
import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.run_log import read_run   # noqa: E402

DOCK_TAIL_S = 2.0     # detik terakhir di M5_DOCK yang dipakai menilai konvergensi


def summarize(path: str) -> dict:
    ev = read_run(path)
    cfg = next((e for e in ev if e['kind'] == 'config'), {})
    end = next((e for e in ev if e['kind'] == 'end'), {})
    trans = [e for e in ev if e['kind'] == 'transition']
    samp = [e for e in ev if e['kind'] == 'sample']
    rej = [e for e in ev if e['kind'] == 'reject']

    s = {
        'file': os.path.basename(path),
        'config_files': cfg.get('files', []),
        'start_state': cfg.get('start_state'),
        'nilai_config': cfg.get('nilai', {}),
        'state_akhir': end.get('state_akhir'),
        'alasan': end.get('alasan'),
        'durasi_s': end.get('durasi_s'),
        'skor': end.get('skor', {}),
        'target_wall': end.get('target_wall'),
        'hang_used_fallback': end.get('hang_used_fallback'),
        'dock_used_fallback': end.get('dock_used_fallback'),
        'n_sample': len(samp),
        'transitions': [{'t': e['t'], 'frm': e.get('frm'), 'to': e.get('to'),
                         'lama_state_s': e.get('lama_state_s')} for e in trans],
        'terpotong': not end,   # run dihentikan sebelum sempat menulis event `end`
    }

    # ── Deteksi QR: rate + dropout terpanjang ────────────────────────────────
    # Dropout adalah penyebab paling sering gagalnya M5_DOCK, jadi bukan cuma
    # rata-rata yang dilaporkan tapi juga jeda buta terpanjang.
    if samp:
        det = [bool(e.get('qr_data')) for e in samp]
        s['qr_rate_pct'] = round(100.0 * sum(det) / len(det), 1)
        worst = run_len = 0
        t_start = None
        for e, d in zip(samp, det):
            if d:
                run_len = 0
                t_start = None
            else:
                if t_start is None:
                    t_start = e['t']
                run_len = e['t'] - t_start
                worst = max(worst, run_len)
        s['qr_dropout_max_s'] = round(worst, 2)

        # ── Profil kedalaman ─────────────────────────────────────────────────
        depths = [e['depth'] for e in samp if e.get('depth') is not None]
        if depths:
            s['depth_min'] = round(min(depths), 3)
            s['depth_max'] = round(max(depths), 3)
            s['depth_akhir'] = round(depths[-1], 3)
            batas = s['nilai_config'].get('DEPTH_TARGET_BOTTOM')
            if batas:
                # Menembus target dasar = risiko gripper/ROV menyentuh dasar kolam.
                s['depth_tembus_target'] = max(depths) > batas + 0.05

    # ── Konvergensi docking: detik-detik terakhir sebelum keluar M5_DOCK ─────
    t_exit = next((e['t'] for e in trans if e.get('frm') == 'M5_DOCK'), None)
    if t_exit is not None:
        tail = [e for e in samp
                if e.get('state') == 'M5_DOCK' and t_exit - DOCK_TAIL_S <= e['t'] <= t_exit]
        vals = {}
        for k in ('offset_x', 'offset_y', 'distance_z'):
            xs = [abs(e[k]) if k != 'distance_z' else e[k] for e in tail if e.get(k) is not None]
            if xs:
                vals[k] = round(sum(xs) / len(xs), 4)
        if vals:
            s['dock_konvergensi'] = vals
            s['dock_keluar_ke'] = next((e.get('to') for e in trans if e.get('frm') == 'M5_DOCK'), None)

    # ── Kenapa FSM menolak deteksi ───────────────────────────────────────────
    # Event `reject` edge-triggered, jadi yang dihitung adalah LAMA BERTAHAN tiap
    # alasan, bukan berapa kali muncul: satu alasan yang bertahan 90 detik jauh
    # lebih penting daripada sepuluh alasan yang berkedip sedetik.
    if rej:
        t_end = end.get('durasi_s') or (samp[-1]['t'] if samp else rej[-1]['t'])
        lama = collections.Counter()
        for e, nxt in zip(rej, rej[1:] + [{'t': t_end}]):
            if e.get('reason'):
                # Keluarga alasan, tanpa angka di belakang ':' pertama —
                # "conf_below_gate:0.28<0.35" dan "…:0.31<0.35" satu penyebab.
                lama[str(e['reason']).split(':')[0]] += max(0.0, nxt['t'] - e['t'])
        if lama:
            s['reject_lama_s'] = {k: round(v, 1) for k, v in lama.most_common()}
            s['reject_dominan'] = lama.most_common(1)[0][0]
            s['reject_contoh'] = next(e['reason'] for e in rej
                                      if str(e.get('reason', '')).split(':')[0]
                                      == s['reject_dominan'])
        locks = [e['lock_progress'] for e in rej if e.get('lock_progress')]
        if locks:
            # Pembilang tertinggi yang pernah dicapai latch. Mentok di bawah
            # penyebut = deteksi berkedip, bukan deteksi tak ada.
            s['lock_maks'] = max(locks, key=lambda v: int(str(v).split('/')[0]))
    return s


def print_detail(s: dict):
    print(f"\n=== {s['file']} ===")
    if s['terpotong']:
        print("  ⚠ run terpotong (tak ada event `end`) — dihentikan paksa / crash")
    print(f"  config      : {', '.join(s['config_files']) or '(default kode)'}")
    print(f"  mulai dari  : {s['start_state']}   target wall: {s['target_wall']}")
    print(f"  state akhir : {s['state_akhir']} ({s['alasan']})   durasi: {s['durasi_s']} s")

    sc = s['skor']
    if sc:
        print(f"  SKOR        : m1={sc.get('m1')} m2={sc.get('m2')} m3={sc.get('m3')} "
              f"m4={sc.get('m4')} m5={sc.get('m5')}  →  TOTAL {sc.get('total')}/100")

    print("\n  Transisi:")
    for t in s['transitions']:
        print(f"    {t['t']:7.2f}s  {t['frm']:<12} → {t['to']:<12} "
              f"(state sebelumnya {t['lama_state_s']}s)")

    if 'qr_rate_pct' in s:
        print(f"\n  QR terdeteksi   : {s['qr_rate_pct']}% dari {s['n_sample']} sample"
              f"   dropout terpanjang: {s['qr_dropout_max_s']}s")
    if 'depth_min' in s:
        line = f"  Kedalaman       : min {s['depth_min']} / max {s['depth_max']} / akhir {s['depth_akhir']} m"
        if s.get('depth_tembus_target'):
            line += "   ⚠ MENEMBUS target dasar"
        print(line)
    if 'dock_konvergensi' in s:
        v = s['dock_konvergensi']
        print(f"  Konvergensi dock ({DOCK_TAIL_S}s terakhir M5_DOCK → {s['dock_keluar_ke']}): "
              + "  ".join(f"{k}={val}" for k, val in v.items()))
    if 'reject_dominan' in s:
        print(f"\n  Gate vision menolak (total detik per penyebab):")
        for reason, detik in s['reject_lama_s'].items():
            tanda = " ←DOMINAN" if reason == s['reject_dominan'] else ""
            print(f"    {detik:7.1f}s  {reason}{tanda}")
        print(f"    contoh alasan lengkap: {s['reject_contoh']}")
    if 'lock_maks' in s:
        print(f"  Latch YOLO tertinggi : {s['lock_maks']}")
    for k, label in (('hang_used_fallback', 'HANG'), ('dock_used_fallback', 'DOCK')):
        if s.get(k):
            print(f"  ⚠ {label} jatuh ke jalur fallback timed (visual gagal)")


def print_table(rows: list):
    hdr = (f"{'run':<28} {'akhir':<12} {'skor':>5} {'durasi':>7} {'QR%':>6} "
           f"{'drop':>6} {'lock':>6}  {'tolak-dominan':<24} fallback")
    print(hdr)
    print("-" * len(hdr))
    for s in rows:
        fb = ",".join(l for k, l in (('hang_used_fallback', 'hang'),
                                     ('dock_used_fallback', 'dock')) if s.get(k)) or "-"
        print(f"{s['file']:<28} {str(s['state_akhir']):<12} "
              f"{s['skor'].get('total', '-'):>5} {str(s['durasi_s']):>7} "
              f"{s.get('qr_rate_pct', '-'):>6} {s.get('qr_dropout_max_s', '-'):>6} "
              f"{s.get('lock_maks', '-'):>6}  {s.get('reject_dominan', '-'):<24} {fb}")
    print(f"\n{len(rows)} run. Kaitkan perubahan skor ke nilai config tiap run "
          f"(`--json` menampilkan `nilai_config`).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('logs', nargs='+', help='file .jsonl (boleh banyak / pakai glob)')
    ap.add_argument('--json', action='store_true', help='keluarkan JSON (dipakai panel GUI)')
    args = ap.parse_args()

    paths = []
    for p in args.logs:
        paths.extend(sorted(glob.glob(p)) if any(c in p for c in '*?[') else [p])
    if not paths:
        ap.error("tak ada file cocok")

    rows = [summarize(p) for p in paths]
    if args.json:
        print(json.dumps(rows[0] if len(rows) == 1 else rows, indent=2, default=str))
    elif len(rows) == 1:
        print_detail(rows[0])
    else:
        print_table(rows)


if __name__ == '__main__':
    main()
