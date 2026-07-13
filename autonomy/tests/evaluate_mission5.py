#!/usr/bin/env python3
"""
tests/evaluate_mission5.py — Harness "tes-nilai-evaluasi" misi 5 (KKI 2026)
============================================================================
Menjalankan Mission5FSM end-to-end melawan SimPlant (loop kontrol tertutup, tanpa
hardware) lalu MENILAI & MENGEVALUASI hasilnya secara terukur:

  • TES     : jalankan rantai misi 1→5 (atau hanya misi 5) sampai DONE/ABORT.
  • NILAI   : skor rubrik per-misi (m1..m5, total /100) — sama seperti FSM.
  • EVALUASI: checklist mutu docking (jalur visual vs fallback, akurasi align saat
             engage, payload lepas-hook & sampai permukaan, waktu tempuh).

Pakai:
    python tests/evaluate_mission5.py                 # misi penuh 1-5, mode PBVS
    python tests/evaluate_mission5.py --start M5_REDIVE  # hanya misi 5 autonomous
    python tests/evaluate_mission5.py --ibvs          # tanpa kalibrasi (IBVS piksel)
    python tests/evaluate_mission5.py --json          # keluaran JSON (utk CI)
"""

import os
import sys
import argparse
import logging

# autonomy/ harus di sys.path agar `fsm`, `vision`, `control`, `tests` ter-import
_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

import fsm.mission5 as m5
from fsm.mission5 import Mission5FSM, State
from tests.sim_plant import (
    FakeClock, SimPlant, SimCommandLink, SimTelemetry, SimVision, install_fake_time,
)

# Toleransi mutu docking untuk EVALUASI (bukan sekadar 'aligned' internal servo)
DOCK_TOL_XY   = 0.06   # m — sisa error lateral+vertikal saat engage
DOCK_TOL_DIST = 0.06   # m — sisa error jarak thd SERVO_TARGET_DIST


def run_scenario(start_state=State.DIVE, provide_pose=True, target_wall='C',
                 dropout=None, hook_dropout=None):
    """Jalankan satu skenario misi di simulator, kembalikan dict hasil evaluasi.

    dropout      : predikat(count)->bool utk mensimulasikan QR hilang sesaat (loss-of-lock).
    hook_dropout : idem utk deteksi HOOK (misi 3b HANG / misi 4 DOCK).
    """
    clock  = FakeClock()
    plant  = SimPlant(target_wall=target_wall, target_dist=m5.SERVO_TARGET_DIST,
                      target_area=m5.SERVO_TARGET_AREA)
    clock.on_sleep = plant.step

    cmd    = SimCommandLink(plant)
    telem  = SimTelemetry(plant)
    vision = SimVision(plant, clock, provide_pose=provide_pose, dropout=dropout,
                       hook_dropout=hook_dropout)

    # Pasang jam virtual pada modul FSM (mission5 pakai time.time()/time.sleep()).
    original_time = install_fake_time(m5, clock)
    try:
        fsm = Mission5FSM(cmd=cmd, telem=telem, vision=vision)

        transitions = []
        dock_error = {}          # geometri saat M5_DOCK → M5_ENGAGE (akurasi align)
        original_transition = fsm._transition

        def rec_transition(new_state):
            frm = fsm._state
            transitions.append((round(clock.time(), 2), frm.name, new_state.name))
            if frm == State.M5_DOCK and new_state == State.M5_ENGAGE:
                dock_error.update(rx=plant.s.rx, ry=plant.s.ry,
                                  rz=plant.s.rz, dist=m5.SERVO_TARGET_DIST)
            original_transition(new_state)

        fsm._transition = rec_transition
        fsm.start(start_state=start_state, wait_mode=False)
    finally:
        m5.time = original_time   # kembalikan modul time asli

    return _evaluate(fsm, plant, clock, transitions, dock_error, provide_pose,
                     start_state, target_wall)


def _evaluate(fsm, plant, clock, transitions, dock_error, provide_pose,
              start_state, target_wall):
    score   = fsm.score()
    visited = {t[1] for t in transitions} | {t[2] for t in transitions}
    final   = transitions[-1][2] if transitions else start_state.name

    used_visual   = ('M5_DOCK' in visited and 'M5_ENGAGE' in visited)
    used_fallback = ('M5_FALLBACK' in visited)
    full_run      = (start_state == State.DIVE)
    m5_only       = not full_run

    radial_xy = None
    if dock_error:
        radial_xy = (dock_error['rx'] ** 2 + dock_error['ry'] ** 2) ** 0.5

    # ── Checklist EVALUASI (nilai kualitatif) ─────────────────────────────────
    checks = []

    def chk(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    hang_visual = full_run and not fsm._hang_used_fallback
    dock_visual = full_run and not fsm._dock_used_fallback

    chk('Mencapai DONE (tanpa ABORT)', final == 'DONE', f'state akhir={final}')
    if full_run:
        chk('Misi 1 Scan QR (+15)',  score['m1'] == 15, f"m1={score['m1']}")
        chk('Misi 2 Grab (+15)',     score['m2'] == 15, f"m2={score['m2']}")
        chk('Misi 3 Hang (+15)',     score['m3'] == 15, f"m3={score['m3']}")
        chk('Misi 4 Surface Dock (+15)', score['m4'] == 15, f"m4={score['m4']}")
        chk('Misi 3 Hang via jalur VISUAL (bukan fallback timed)',
            hang_visual, f'hang_fallback={fsm._hang_used_fallback}')
        chk('Misi 4 Dock via jalur VISUAL (bukan fallback timed)',
            dock_visual, f'dock_fallback={fsm._dock_used_fallback}')
    chk('Misi 5 Auto-Release penuh (+40)', score['m5'] == 40, f"m5={score['m5']}")
    chk('Docking via jalur VISUAL (bukan fallback timed)',
        used_visual and not used_fallback,
        f'visual={used_visual} fallback={used_fallback}')
    if radial_xy is not None:
        chk('Akurasi align lateral saat engage',
            radial_xy <= DOCK_TOL_XY, f'radial_xy={radial_xy:.3f}m ≤ {DOCK_TOL_XY}')
        chk('Akurasi jarak engage',
            abs(dock_error['rz'] - dock_error['dist']) <= DOCK_TOL_DIST,
            f"z={dock_error['rz']:.3f}m target={dock_error['dist']}m")
    else:
        chk('Akurasi align saat engage', False, 'tidak pernah mencapai engage visual')
    chk('Payload tercengkeram gripper', plant.grabbed, f'grabbed={plant.grabbed}')
    chk('Payload lepas dari hook', not plant.hooked, f'hooked={plant.hooked}')
    chk('Payload dibawa ke permukaan',
        plant.s.depth <= m5.DEPTH_TARGET_SURFACE,
        f'depth_akhir={plant.s.depth:.3f}m')

    passed = sum(1 for c in checks if c['ok'])
    total_checks = len(checks)

    return {
        'skenario': {
            'start_state': start_state.name,
            'mode_visi': 'PBVS (pose)' if provide_pose else 'IBVS (piksel)',
            'target_wall': target_wall,
        },
        'skor': score,
        'nilai_misi_5': score['m5'],
        'nilai_total': score['total'],
        'state_akhir': final,
        'durasi_virtual_s': round(clock.time(), 2),
        'jalur': {
            'used_visual_dock': used_visual,
            'used_fallback': used_fallback,
            'hang_used_fallback': fsm._hang_used_fallback,
            'dock_used_fallback': fsm._dock_used_fallback,
        },
        'akurasi_docking': (
            {'rx': round(dock_error['rx'], 4), 'ry': round(dock_error['ry'], 4),
             'rz': round(dock_error['rz'], 4), 'radial_xy': round(radial_xy, 4)}
            if dock_error else None
        ),
        'payload': {'grabbed': plant.grabbed, 'unhooked': not plant.hooked,
                    'hung': plant.hung, 'docked': plant.docked,
                    'depth_akhir': round(plant.s.depth, 4)},
        'evaluasi': checks,
        'evaluasi_lulus': passed,
        'evaluasi_total': total_checks,
        'lulus': passed == total_checks,
        'transitions': transitions,
    }


def _print_report(rep):
    sc = rep['skor']
    print('=' * 66)
    print(' EVALUASI MISI 5 — Docking Payload Autonomous (SimPlant closed-loop)')
    print('=' * 66)
    s = rep['skenario']
    print(f"  Skenario   : start={s['start_state']}  visi={s['mode_visi']}  wall={s['target_wall']}")
    print(f"  State akhir: {rep['state_akhir']}   durasi(virtual)={rep['durasi_virtual_s']}s")
    print('-' * 66)
    print('  NILAI (rubrik KKI 2026):')
    print(f"    Misi 1 Scan QR      : {sc['m1']:>3}/15")
    print(f"    Misi 2 Grab Payload : {sc['m2']:>3}/15")
    print(f"    Misi 3 Hang Payload : {sc['m3']:>3}/15")
    print(f"    Misi 4 Surface Dock : {sc['m4']:>3}/15")
    print(f"    Misi 5 Auto-Release : {sc['m5']:>3}/40   ← nilai tertinggi")
    print(f"    TOTAL               : {sc['total']:>3}/100")
    if rep['akurasi_docking']:
        a = rep['akurasi_docking']
        print('-' * 66)
        print(f"  Akurasi docking saat engage: radial_xy={a['radial_xy']}m  "
              f"z={a['rz']}m (rx={a['rx']} ry={a['ry']})")
    print('-' * 66)
    print(f"  EVALUASI ({rep['evaluasi_lulus']}/{rep['evaluasi_total']} lolos):")
    for c in rep['evaluasi']:
        mark = '✓' if c['ok'] else '✗'
        print(f"    [{mark}] {c['name']:<44} {c['detail']}")
    print('=' * 66)
    verdict = 'LULUS ✓' if rep['lulus'] else 'GAGAL ✗'
    print(f"  HASIL: {verdict}   (nilai misi 5 = {rep['nilai_misi_5']}/40, "
          f"total = {rep['nilai_total']}/100)")
    print('=' * 66)


def main():
    ap = argparse.ArgumentParser(description='Evaluasi closed-loop misi 5 (SimPlant)')
    ap.add_argument('--start', default='DIVE',
                    choices=['DIVE', 'M5_REDIVE', 'M5_DOCK'],
                    help='DIVE=misi penuh 1-5; M5_REDIVE=hanya misi 5 autonomous')
    ap.add_argument('--wall', default='C', choices=['A', 'B', 'C', 'D'])
    ap.add_argument('--ibvs', action='store_true',
                    help='mode IBVS (tanpa kalibrasi/pose) — default PBVS')
    ap.add_argument('--json', action='store_true', help='keluaran JSON (utk CI)')
    ap.add_argument('--loglevel', default='WARNING')
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, args.loglevel.upper()),
                        format='%(levelname)s %(message)s')

    rep = run_scenario(start_state=State[args.start],
                       provide_pose=not args.ibvs, target_wall=args.wall)

    if args.json:
        import json
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        _print_report(rep)

    return 0 if rep['lulus'] else 1


if __name__ == '__main__':
    sys.exit(main())
