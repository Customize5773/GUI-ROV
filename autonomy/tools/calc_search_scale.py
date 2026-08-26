#!/usr/bin/env python3
"""Skala search: block (M5_SEARCH) dari kolam latihan ke arena 5m/10m.

search: (backoff_t/leg_t0/leg_t_max/span_max_t/creep_max_t) di
config/pool_trial.yaml merepresentasikan JARAK (detik x SEARCH_SPEED yang
sudah dikalibrasi nyata di kolam latihan, ~0,222 m/s @ lebar 2,2 m) yang
dibatasi supaya ROV tak menabrak dinding. Script ini men-scale linear tiap
nilai detik itu dengan rasio target_width/practice_width, lalu bandingkan
dengan default mission5.py -- BUKAN menulis file config apa pun, cuma
mencetak snippet YAML utk ditempel manual ke pool_kki_trial.yaml /
pool_kki_running.yaml (pola sama seperti bottom_clearance yang disalin
tangan antar config).

Pakai:
    python3 autonomy/tools/calc_search_scale.py --width 5.0
    python3 autonomy/tools/calc_search_scale.py --width 10.0
"""
import argparse

# Nilai saat ini di config/pool_trial.yaml (lebar kolam latihan 2,2 m).
PRACTICE_WIDTH = 2.2
PRACTICE_SEARCH = {
    "backoff_t": 3.0,
    "leg_t0": 2.0,
    "leg_t_max": 4.5,
    "span_max_t": 4.5,
    "creep_max_t": 6.0,
}
# Default mission5.py (asumsi implisit kolam kecil ~3 m), utk pembanding.
MISSION5_DEFAULT = {
    "backoff_t": 6.0,
    "leg_t0": 3.0,
    "leg_t_max": 10.0,
    "span_max_t": 12.0,
    "creep_max_t": 8.0,
}


def scale_search(target_width, practice_width=PRACTICE_WIDTH, practice_search=None):
    practice_search = practice_search or PRACTICE_SEARCH
    ratio = target_width / practice_width
    return {k: round(v * ratio, 2) for k, v in practice_search.items()}


def _demo():
    same = scale_search(PRACTICE_WIDTH)
    assert same == PRACTICE_SEARCH, same
    doubled = scale_search(PRACTICE_WIDTH * 2)
    for k, v in PRACTICE_SEARCH.items():
        assert doubled[k] == round(v * 2, 2), (k, doubled[k])
    print("_demo ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--width", type=float,
                     help="Lebar arena target (m), mis. 5.0 atau 10.0")
    ap.add_argument("--practice-width", type=float, default=PRACTICE_WIDTH)
    for key, default in PRACTICE_SEARCH.items():
        ap.add_argument(f"--{key}", type=float, default=default,
                         help=f"Nilai kalibrasi kolam latihan (default {default})")
    args = ap.parse_args()

    if args.width is None:
        _demo()
        return

    practice_search = {k: getattr(args, k) for k in PRACTICE_SEARCH}
    scaled = scale_search(args.width, args.practice_width, practice_search)

    print(f"# search: hasil scale {args.practice_width}m -> {args.width}m "
          f"(rasio {args.width / args.practice_width:.3f})")
    print("search:")
    for k, v in scaled.items():
        default = MISSION5_DEFAULT[k]
        flag = "LEBIH KETAT" if v < default else ("LEBIH LONGGAR" if v > default else "sama")
        print(f"  {k}: {v}   # default mission5.py {default} -> {flag}")


if __name__ == "__main__":
    main()
