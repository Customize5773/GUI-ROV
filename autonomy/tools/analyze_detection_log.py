#!/usr/bin/env python3
"""
tools/analyze_detection_log.py — Ringkas CSV dari DetectionCsvLogger (servo/pose_webcam_test.py)
menjadi tabel distribusi deteksi per-jarak (atau per-area bila tanpa kalibrasi/PBVS).

DIPAKAI UTK VALIDASI EMPIRIS: membandingkan hasil uji kolam NYATA dgn angka simulasi
di tests/evaluate_qr_underwater.py (yang murni sintetis, belum pernah diverifikasi
di air sungguhan — lihat memory qr-underwater-robustness-limits.md).

KETERBATASAN PENTING: DetectionCsvLogger hanya mencatat `dist`/`area` pada frame
YANG TERDETEKSI (lihat tools/detection_log.py — kolom kosong saat detected=False).
Jadi tool ini TIDAK bisa menghitung "rate = terdeteksi/total PADA jarak X" yang
sebenarnya (jarak sebenarnya saat gagal tidak diketahui). Yang bisa dihitung:
  - rate keseluruhan (terdeteksi/total frame di seluruh run)
  - distribusi JARAK/AREA di antara frame yang BERHASIL terdeteksi (menunjukkan di
    rentang mana keberhasilan berkumpul — mis. "kebanyakan sukses di bawah 0.5 m"
    mengindikasikan kegagalan di jarak jauh, walau tak dikuantifikasi persis).
Untuk rate-vs-jarak yang presisi, uji manual per-jarak tetap (gerakkan ROV/QR ke
jarak tertentu, diamkan beberapa detik, baca rate segmen itu dari log run terpisah).

PEMAKAIAN
  python tools/analyze_detection_log.py run1.csv run2.csv --bin-size 0.1
"""
import argparse
import csv
import sys
from collections import defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("csv_files", nargs="+", help="file CSV dari DetectionCsvLogger")
ap.add_argument("--bin-size", type=float, default=0.1, help="lebar bin jarak (m, default 0.1)")
ap.add_argument("--area-bin-size", type=float, default=500.0,
                help="lebar bin area (px^2, dipakai bila tak ada data jarak/PBVS)")
args = ap.parse_args()


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bucket(value, bin_size):
    return round((value // bin_size) * bin_size, 3)


def print_hist(title, unit, counts, bin_size):
    print(f"\n{title}")
    if not counts:
        print("  (tak ada data)")
        return
    total = sum(counts.values())
    for edge in sorted(counts):
        n = counts[edge]
        bar = "#" * max(1, int(40 * n / total))
        print(f"  [{edge:6.2f}, {edge + bin_size:6.2f}) {unit}: {n:4d} frame  {bar}")


total_frames = 0
total_detected = 0
total_hint = 0
dist_counts = defaultdict(int)
area_counts = defaultdict(int)
hint_dist_counts = defaultdict(int)   # M9c: jarak quad TERLIHAT tapi decode GAGAL
hint_area_counts = defaultdict(int)

for path in args.csv_files:
    rows = load_rows(path)
    frames = len(rows)
    detected = sum(1 for r in rows if r.get("detected") in ("1", "True", "true"))
    hint = sum(1 for r in rows if r.get("hint_detected") in ("1", "True", "true"))
    total_frames += frames
    total_detected += detected
    total_hint += hint
    print(f"[{path}] {detected}/{frames} frame terdeteksi "
          f"({100.0 * detected / frames if frames else 0:.1f}%), "
          f"+{hint} frame hint-tanpa-decode ({100.0 * hint / frames if frames else 0:.1f}%)")
    for r in rows:
        is_hint = r.get("hint_detected") in ("1", "True", "true")
        if r.get("detected") not in ("1", "True", "true") and not is_hint:
            continue
        d = to_float(r.get("dist"))
        a = to_float(r.get("area"))
        if is_hint:
            if d is not None:
                hint_dist_counts[bucket(d, args.bin_size)] += 1
            elif a is not None:
                hint_area_counts[bucket(a, args.area_bin_size)] += 1
        else:
            if d is not None:
                dist_counts[bucket(d, args.bin_size)] += 1
            elif a is not None:
                area_counts[bucket(a, args.area_bin_size)] += 1

if not args.csv_files:
    sys.exit("Tak ada file CSV diberikan")

print(f"\n=== TOTAL: {total_detected}/{total_frames} frame terdeteksi "
      f"({100.0 * total_detected / total_frames if total_frames else 0:.1f}%) ===")

if dist_counts:
    print_hist("Distribusi JARAK (m) pada frame yang BERHASIL terdeteksi (PBVS)",
               "m", dist_counts, args.bin_size)
if area_counts:
    print_hist("Distribusi AREA (px^2) pada frame yang BERHASIL terdeteksi (IBVS, tanpa kalibrasi)",
               "px^2", area_counts, args.area_bin_size)
if hint_dist_counts:
    print_hist("M9c — Distribusi JARAK (m) pada frame HINT (quad terlihat, decode GAGAL)",
               "m", hint_dist_counts, args.bin_size)
if hint_area_counts:
    print_hist("M9c — Distribusi AREA (px^2) pada frame HINT (quad terlihat, decode GAGAL)",
               "px^2", hint_area_counts, args.area_bin_size)
if dist_counts and hint_dist_counts:
    decode_max = max(dist_counts)
    hint_max = max(hint_dist_counts)
    print(f"\nM9c ringkas: decode berhasil sampai ~{decode_max:.2f} m; quad masih "
          f"terlihat (belum tentu decode) sampai ~{hint_max:.2f} m. Selisih "
          f"~{hint_max - decode_max:.2f} m adalah jarak tambahan yang bisa dipakai "
          f"SEARCH_BACKOFF_T untuk 'melihat lebih lebar sebelum mendekat merayap'.")
if not dist_counts and not area_counts:
    print("\n(tak ada frame terdeteksi dgn dist/area di CSV manapun)")

print("\nCatatan: ini distribusi KEBERHASILAN, bukan rate per-jarak presisi — lihat "
      "docstring modul ini utk keterbatasan logging saat ini.")
