#!/usr/bin/env python3
"""
tools/decode_latency.py — Ekstrak durasi per-attempt "cari QR -> decode sukses"
dari CSV DetectionCsvLogger (tools/detection_log.py), untuk validasi TIMEOUT_SCAN.

analyze_detection_log.py meringkas distribusi jarak/area, BUKAN durasi (lihat
docstringnya). Script ini melengkapi: definisi 1 "attempt" = 1 runtutan frame
detected=False yang diakhiri satu frame detected=True (decode sukses). Durasi
= elapsed_s(frame sukses) - elapsed_s(frame False pertama di runtutan itu).
Runtutan False di akhir file (tak pernah sukses) dilaporkan terpisah sebagai
"timeout candidate" dgn durasi = elapsed_s(frame False terakhir) - elapsed_s(awal).

PEMAKAIAN
  python tools/decode_latency.py log-m5/scan_trial_*.csv
"""
import argparse
import csv
import sys

ap = argparse.ArgumentParser()
ap.add_argument("csv_files", nargs="+")
args = ap.parse_args()

durations = []
timeouts = []

for path in args.csv_files:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    run_start = None
    for r in rows:
        detected = r.get("detected") in ("1", "True", "true")
        t = float(r["elapsed_s"])
        if not detected:
            if run_start is None:
                run_start = t
        else:
            if run_start is not None:
                durations.append(t - run_start)
                run_start = None
    if run_start is not None and rows:
        timeouts.append(float(rows[-1]["elapsed_s"]) - run_start)

if not durations and not timeouts:
    sys.exit("Tak ada attempt ditemukan di CSV manapun")

durations.sort()


def pct(p):
    if not durations:
        return None
    i = min(len(durations) - 1, int(p / 100 * len(durations)))
    return durations[i]


print(f"Attempt sukses: {len(durations)}")
if durations:
    print(f"  min={min(durations):.2f}s  median={pct(50):.2f}s  "
          f"p95={pct(95):.2f}s  max={max(durations):.2f}s")
if timeouts:
    print(f"Runtutan tak-sukses di akhir file (calon timeout, bukan attempt lengkap): "
          f"{len(timeouts)}, durasi {min(timeouts):.2f}-{max(timeouts):.2f}s")
