#!/usr/bin/env python3
"""
tests/evaluate_qr_underwater.py — Dataset + evaluasi robustness QR bawah air
============================================================================
Membuat dataset gambar QR yang disimulasikan difoto di bawah air (tests/underwater_sim.py),
lalu MENGUKUR sampai level degradasi berapa `vision.qr_detect.decode_qr()` masih membaca
payload JSON dengan BENAR:

  • TES     : render/muat QR bersih → degradasi per depth_level → simpan PNG.
  • NILAI   : decode ulang tiap varian, bandingkan dgn payload groundtruth (harus SAMA PERSIS).
  • EVALUASI: tabel depth × (raw vs enhance) × jenjang mana yang menyelamatkan,
              + ringkasan ambang: di depth berapa jalur cepat putus, di depth berapa
              eskalasi CLAHE/upscale ikut menyerah.

Dua sumbu degradasi (KEDUANYA penting — lihat catatan di bawah):
  - depth_level : 0.0 (permukaan) → 1.0 (dalam); preset shallow/medium/deep = anchor.
  - module_px   : piksel per modul QR = proxy JARAK (kecil = QR jauh/kecil di frame).
    Tanpa sumbu ini laporan menyesatkan: pada QR besar, efek fotometrik hampir tak
    pernah mematahkan pyzbar, sehingga CLAHE/upscale tampak tak berguna.

Pakai:
    # dataset 3 level x 5 sample, QR dirender segno dari payload
    python tests/evaluate_qr_underwater.py \
        --payload '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"A"}' \
        --outdir dataset --samples 5 --seed 0 --zip

    # pakai PNG bersih milik sendiri (segno tak perlu); --payload = groundtruth
    python tests/evaluate_qr_underwater.py --input qr_A.png \
        --payload '{"mission":5,...}' --outdir dataset

    # lokalisasi ambang (mode ukur; tak menulis gambar)
    python tests/evaluate_qr_underwater.py --payload '{...}' --sweep 0:1:0.05 --samples 3

    # isolasi efek — mana yang paling merusak?
    python tests/evaluate_qr_underwater.py --payload '{...}' --sweep 0:1:0.05 --no-ripple

Dataset siap-pakai di Colab: folder per depth_level, penamaan konsisten, plus
metadata.csv (flat, utk pd.read_csv) & metadata.json (nested params + ringkasan).

Butuh: opencv-python, pyzbar, numpy (+ segno bila memakai --payload).
"""

import os
import sys
import csv
import json
import zipfile
import argparse

import numpy as np

# autonomy/ harus di sys.path agar `vision`, `tests` ter-import
_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

import cv2

import vision.qr_detect as qd
from vision.qr_detect import decode_qr
from tests.underwater_sim import (
    ALL_EFFECTS, DEPTH_ANCHORS, place_on_canvas, render_qr_bgr, resolve_depth,
    simulate_underwater,
)

# Kolom metadata.csv — flat & stabil (Colab: pd.read_csv langsung pakai).
CSV_FIELDS = [
    'path', 'depth_level', 'depth_value', 'module_px', 'qr_px', 'sample_idx',
    'qr_id', 'payload_groundtruth', 'effects',
    'red_gain', 'green_gain', 'blue_gain',
    'black_point', 'white_point', 'haze_alpha',
    'amp_x', 'amp_y', 'freq_x', 'freq_y', 'phase_x', 'phase_y',
    'ksize', 'sigma',
    'decode_raw_ok', 'decode_enhance_ok', 'decode_stage', 'decoded_data',
]

# Jenjang decode_qr() sesuai urutan panggilan pyzbar di dalamnya.
_STAGE_BY_CALL = {1: 'raw', 2: 'clahe', 3: 'upscale'}


def decode_with_stage(frame, expected):
    """Jalankan decode_qr() ASLI, lalu laporkan JENJANG mana yang berhasil.

    decode_qr() tak mengembalikan provenance jenjang, dan kode produksi sengaja TIDAK
    diubah. Jadi jenjang disimpulkan dgn membungkus `qd.pyzbar.decode` memakai
    penghitung: panggilan ke-1 = frame mentah, ke-2 = gray+CLAHE, ke-3 = CLAHE+upscale.
    Bila semua panggilan pyzbar kosong tapi decode_qr tetap mengembalikan hasil →
    fallback cv2.QRCodeDetector yang menyelamatkan. (Pola pembungkus ini sama dengan
    yang dipakai tests/test_qr_detect.py.)

    Returns {'ok', 'data', 'stage'}; ok = payload SAMA PERSIS dgn groundtruth.
    """
    hits = []          # per panggilan pyzbar: apakah mengembalikan simbol?
    original = getattr(qd, 'pyzbar', None)

    if original is None or not getattr(qd, 'PYZBAR_OK', False):
        res = decode_qr(frame, enhance=True)
        data = res[0]['data'] if res else ''
        return {'ok': bool(res) and data == expected, 'data': data,
                'stage': 'cv2_fallback' if res else 'fail'}

    real_decode = original.decode

    def counting_decode(img, *a, **kw):
        out = real_decode(img, *a, **kw)
        hits.append(bool(out))
        return out

    try:
        original.decode = counting_decode
        res = decode_qr(frame, enhance=True)
    finally:
        original.decode = real_decode      # selalu pulihkan, walau decode_qr melempar

    if not res:
        return {'ok': False, 'data': '', 'stage': 'fail'}

    data = res[0]['data']
    if True in hits:
        stage = _STAGE_BY_CALL.get(hits.index(True) + 1, 'upscale')
    else:
        stage = 'cv2_fallback'
    return {'ok': data == expected, 'data': data, 'stage': stage}


def evaluate_frame(frame, expected):
    """Ukur satu frame: jalur cepat (raw) vs jalur lengkap (enhance) + jenjang."""
    raw = decode_qr(frame, enhance=False)
    raw_ok = bool(raw) and raw[0]['data'] == expected
    enh = decode_with_stage(frame, expected)
    return {'decode_raw_ok': raw_ok, 'decode_enhance_ok': enh['ok'],
            'decode_stage': enh['stage'], 'decoded_data': enh['data']}


def _clean_frame(args, payload, module_px):
    """Frame QR bersih: dari --input (PNG) atau dirender segno dari --payload."""
    if args.input:
        img = cv2.imread(args.input, cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"gagal membaca gambar: {args.input}")
        return place_on_canvas(img, canvas_wh=tuple(args.canvas))
    try:
        import segno  # noqa: F401
    except ImportError:
        raise SystemExit(
            "segno tidak terinstal — `pip install segno`, atau pakai --input PNG bersih.")
    qr = render_qr_bgr(payload, module_px=module_px)
    return place_on_canvas(qr, canvas_wh=tuple(args.canvas))


def _row(params, level_name, module_px, qr_px, idx, qr_id, payload, path, verdict):
    row = {
        'path': path, 'depth_level': level_name,
        'depth_value': round(params['depth_value'], 4),
        'module_px': module_px, 'qr_px': qr_px, 'sample_idx': idx,
        'qr_id': qr_id, 'payload_groundtruth': payload,
        'effects': '|'.join(params['effects']),
    }
    for k in ('red_gain', 'green_gain', 'blue_gain', 'black_point', 'white_point',
              'haze_alpha', 'amp_x', 'amp_y', 'freq_x', 'freq_y', 'phase_x',
              'phase_y', 'sigma'):
        row[k] = round(float(params[k]), 4)
    row['ksize'] = int(params['ksize'])
    row.update(verdict)
    return row


def run_dataset(args, levels, targets, effects, write_images=True):
    """Bangun (opsional tulis) dataset & verifikasi tiap varian. → list baris metadata.

    targets : [(qr_id, payload), ...] — tiap sisi kolam (A/B/C/D) jadi kelas tersendiri;
              `qr_id` di metadata = label training.
    """
    rows = []
    for qr_id, payload in targets:
        rows.extend(_run_one_target(args, levels, payload, qr_id, effects, write_images))
    return rows


def _run_one_target(args, levels, payload, qr_id, effects, write_images=True):
    rows = []
    for module_px in args.module_px:
        clean, (x0, y0, qr_w, qr_h) = _clean_frame(args, payload, module_px)
        # Dari --input, module/px tak diketahui (PNG apa adanya) → jangan mengarang
        # angka di metadata; qr_px (lebar QR terukur) tetap jujur & berguna.
        mod_tag = 'src' if args.input else str(module_px)
        mod_meta = '' if args.input else module_px

        if write_images:
            clean_dir = os.path.join(args.outdir, 'clean')
            os.makedirs(clean_dir, exist_ok=True)
            cv2.imwrite(os.path.join(clean_dir, f'qr_{qr_id}_m{mod_tag}_clean.png'), clean)

        for level_name, depth in levels:
            if write_images:
                os.makedirs(os.path.join(args.outdir, level_name), exist_ok=True)
            for idx in range(args.samples):
                # Seed per-varian → dataset reproducible PERSIS dari --seed, dan tiap
                # sample tetap berbeda (fase riak + jitter).
                # qr_id ikut di-seed → tiap sisi kolam dapat realisasi riak/jitter
                # sendiri (variasi utk training), tapi tetap reproducible dari --seed.
                rng = np.random.default_rng(
                    [args.seed, sum(ord(c) for c in qr_id), module_px,
                     int(round(depth * 1000)), idx])
                out, params = simulate_underwater(clean, depth, rng=rng,
                                                 jitter=args.jitter, effects=effects)
                rel = os.path.join(level_name,
                                   f'qr_{qr_id}_m{mod_tag}_{level_name}_{idx:03d}.png')
                if write_images:
                    cv2.imwrite(os.path.join(args.outdir, rel), out)
                verdict = evaluate_frame(out, payload)
                rows.append(_row(params, level_name, mod_meta, qr_w, idx,
                                 qr_id, payload, rel, verdict))
    return rows


def summarize(rows):
    """Agregasi per (depth_level, module_px) + ambang putus raw & enhance."""
    cells = {}
    for r in rows:
        key = (r['depth_level'], r['depth_value'], r['module_px'])
        c = cells.setdefault(key, {'n': 0, 'raw': 0, 'enh': 0, 'stages': {}})
        c['n'] += 1
        c['raw'] += bool(r['decode_raw_ok'])
        c['enh'] += bool(r['decode_enhance_ok'])
        c['stages'][r['decode_stage']] = c['stages'].get(r['decode_stage'], 0) + 1

    by_depth = {}
    for r in rows:
        d = by_depth.setdefault(round(r['depth_value'], 4), {'n': 0, 'raw': 0, 'enh': 0})
        d['n'] += 1
        d['raw'] += bool(r['decode_raw_ok'])
        d['enh'] += bool(r['decode_enhance_ok'])

    # Ambang = depth TERKECIL yang rate-nya < 100% (mulai goyah).
    raw_break = next((d for d in sorted(by_depth) if by_depth[d]['raw'] < by_depth[d]['n']), None)
    enh_break = next((d for d in sorted(by_depth) if by_depth[d]['enh'] < by_depth[d]['n']), None)
    # Titik mati total = depth terkecil yang enhance-nya 0%.
    enh_dead = next((d for d in sorted(by_depth) if by_depth[d]['enh'] == 0), None)
    rescued = sum(1 for r in rows
                  if r['decode_enhance_ok'] and not r['decode_raw_ok'])
    return {'cells': cells, 'by_depth': by_depth, 'raw_break': raw_break,
            'enh_break': enh_break, 'enh_dead': enh_dead, 'rescued': rescued,
            'total': len(rows)}


def _print_report(rows, summ, args, effects):
    print('=' * 78)
    print(' EVALUASI ROBUSTNESS QR BAWAH AIR — decode_qr() vs degradasi tersimulasi')
    print('=' * 78)
    # Kolom diambil dari baris yang BENAR-BENAR ada (utk --input, module_px kosong).
    mods = sorted({r['module_px'] for r in rows}, key=lambda m: (m == '', m))
    print(f"  Efek aktif : {'+'.join(sorted(effects)) or '(tak ada)'}")
    if mods == ['']:
        print(f"  Sumber QR  : {args.input}  (module_px mengikuti PNG; "
              f"lebar QR={rows[0]['qr_px']}px)")
    else:
        print(f"  module_px  : {mods}   (piksel/modul = proxy jarak; kecil = jauh)")
    print(f"  Sample     : {args.samples}/level  seed={args.seed}  jitter=±{args.jitter:.0%}")
    print('-' * 78)
    print('  TABEL: kedalaman × jarak × berhasil-decode (raw = pyzbar mentah,')
    print('         enh = decode_qr penuh: CLAHE→upscale→cv2). Nilai = sukses/total.')
    print('-' * 78)
    print(f"  {'depth':<10}{'d':>5}  "
          + '  '.join(f"m={(m if m != '' else 'src'):<3} raw/enh" for m in mods))
    last_level = None
    for (level, depth, _m) in sorted(summ['cells'], key=lambda k: (k[1], k[2])):
        if (level, depth) == last_level:
            continue
        last_level = (level, depth)
        parts = []
        for m in mods:
            c = summ['cells'].get((level, depth, m))
            if not c:
                parts.append('       -   ')
                continue
            parts.append(f"    {c['raw']}/{c['n']}  {c['enh']}/{c['n']}")
        print(f"  {level:<10}{depth:>5.2f}  " + ''.join(parts))

    print('-' * 78)
    print('  JENJANG yang menyelamatkan (hanya varian yang berhasil via enhance):')
    stage_tot = {}
    for c in summ['cells'].values():
        for s, n in c['stages'].items():
            stage_tot[s] = stage_tot.get(s, 0) + n
    for s in ('raw', 'clahe', 'upscale', 'cv2_fallback', 'fail'):
        if s in stage_tot:
            print(f"    {s:<14}: {stage_tot[s]:>4} varian")
    print(f"    → {summ['rescued']} varian DISELAMATKAN eskalasi (raw gagal, enhance berhasil)")

    ids = sorted({r['qr_id'] for r in rows})
    if len(ids) > 1:
        print('-' * 78)
        print('  PER SISI KOLAM (label kelas dataset) — harusnya setara; timpang = curiga:')
        for i in ids:
            sub = [r for r in rows if r['qr_id'] == i]
            enh = sum(bool(r['decode_enhance_ok']) for r in sub)
            print(f"    id={i}: {enh}/{len(sub)} terbaca   payload={sub[0]['payload_groundtruth']!r}")

    print('-' * 78)
    print('  AMBANG:')
    rb, eb, ed = summ['raw_break'], summ['enh_break'], summ['enh_dead']
    print(f"    Jalur cepat (pyzbar mentah) mulai gagal di depth : "
          f"{rb if rb is not None else 'tak pernah gagal'}")
    print(f"    Eskalasi CLAHE/upscale mulai gagal di depth      : "
          f"{eb if eb is not None else 'tak pernah gagal'}")
    print(f"    Eskalasi GAGAL TOTAL (0%) sejak depth            : "
          f"{ed if ed is not None else 'tak pernah 0%'}")
    print('=' * 78)


def _write_metadata(args, rows, summ, targets, effects):
    csv_path = os.path.join(args.outdir, 'metadata.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in CSV_FIELDS})

    json_path = os.path.join(args.outdir, 'metadata.json')
    meta = {
        'payload_template': args.payload,
        'targets': {qid: pl for qid, pl in targets},   # label kelas → payload groundtruth
        'effects': sorted(effects),
        'module_px': args.module_px,
        'samples_per_level': args.samples,
        'seed': args.seed,
        'jitter': args.jitter,
        'depth_anchors': DEPTH_ANCHORS,
        'summary': {
            'total': summ['total'],
            'rescued_by_enhance': summ['rescued'],
            'raw_break_depth': summ['raw_break'],
            'enhance_break_depth': summ['enh_break'],
            'enhance_dead_depth': summ['enh_dead'],
        },
        'images': rows,
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    return csv_path, json_path


def _zip_dataset(outdir):
    """Bungkus dataset → .zip siap unggah manual ke Google Drive (stdlib, tanpa dep)."""
    zip_path = os.path.normpath(outdir.rstrip('/\\')) + '.zip'
    base = os.path.basename(os.path.normpath(outdir))
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(outdir):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                z.write(full, os.path.join(base, os.path.relpath(full, outdir)))
    return zip_path


def _parse_sweep(spec):
    """'0:1:0.05' → [(nama_level, depth), ...] dgn nama 'd0.05' dst."""
    try:
        lo, hi, step = (float(x) for x in spec.split(':'))
    except ValueError:
        raise SystemExit("--sweep harus berformat MULAI:AKHIR:LANGKAH, mis. 0:1:0.05")
    if step <= 0 or hi < lo:
        raise SystemExit("--sweep tak valid: langkah harus >0 dan AKHIR >= MULAI")
    n = int(round((hi - lo) / step)) + 1
    return [(f'd{lo + i * step:.2f}', round(min(lo + i * step, 1.0), 4)) for i in range(n)]


def main():
    ap = argparse.ArgumentParser(
        description='Dataset + evaluasi robustness decode_qr() pada simulasi bawah air')
    src = ap.add_argument_group('sumber QR')
    src.add_argument('--payload', default='HYDROSHIP-M5-{id}',
                     help='isi QR = groundtruth; "{id}" diganti tiap --ids. Default = '
                          'format yang BENAR-BENAR tercetak di PDF payload tim '
                          '(terverifikasi via decode). Pakai JSON penuh bila QR dicetak ulang.')
    src.add_argument('--ids', default='A,B,C,D',
                     help='daftar sisi kolam yang digenerate (default A,B,C,D). '
                          'Jadi kolom `qr_id` di metadata = label kelas utk training.')
    src.add_argument('--input', help='PNG QR bersih milik sendiri (segno tak diperlukan)')
    src.add_argument('--canvas', type=int, nargs=2, default=[640, 480],
                     metavar=('W', 'H'), help='ukuran kanvas frame (default 640 480)')
    src.add_argument('--module-px', type=int, nargs='+', default=[4, 6, 8],
                     help='piksel per modul QR = proxy jarak (kecil = QR jauh)')

    ds = ap.add_argument_group('dataset')
    ds.add_argument('--outdir', default='dataset', help='folder keluaran (default: dataset)')
    ds.add_argument('--levels', default='shallow,medium,deep',
                    help='daftar preset depth (default: shallow,medium,deep)')
    ds.add_argument('--samples', type=int, default=5, help='varian per level (default 5)')
    ds.add_argument('--seed', type=int, default=0, help='seed RNG → dataset reproducible')
    ds.add_argument('--jitter', type=float, default=0.08,
                    help='variasi acak parameter antar-sample (default 0.08 = ±8%%)')
    ds.add_argument('--zip', action='store_true', help='bungkus dataset jadi .zip utk Drive')

    ev = ap.add_argument_group('evaluasi')
    ev.add_argument('--sweep', metavar='LO:HI:STEP',
                    help='mode ukur: sapu depth kontinu utk lokalisasi ambang '
                         '(tak menulis gambar kecuali --write-sweep)')
    ev.add_argument('--write-sweep', action='store_true',
                    help='tulis juga gambar saat --sweep')
    ev.add_argument('--json', action='store_true', help='keluaran JSON ringkasan (utk CI)')

    tg = ap.add_argument_group('toggle efek (default: semua aktif)')
    for eff in ALL_EFFECTS:
        tg.add_argument(f'--no-{eff}', dest=f'no_{eff}', action='store_true',
                        help=f'matikan efek {eff}')
    args = ap.parse_args()

    effects = {e for e in ALL_EFFECTS if not getattr(args, f'no_{e}')}

    # --input = PNG berukuran tetap → module_px (proxy jarak) tak bisa dikendalikan.
    # Menyapu beberapa module_px hanya akan menghasilkan varian rangkap dgn kolom
    # module_px yang menyesatkan. Jadi runtuhkan ke satu lintasan & beri tahu user.
    if args.input and len(args.module_px) > 1:
        print(f"[catatan] --input dipakai → --module-px {args.module_px} diabaikan "
              f"(ukuran QR mengikuti PNG). Pakai --payload bila ingin menyapu jarak.")
        args.module_px = args.module_px[:1]

    # Bangun target: satu per sisi kolam. '{id}' di --payload diganti tiap ids →
    # kolom `qr_id` (A/B/C/D) jadi label kelas dataset.
    ids = [s.strip() for s in args.ids.split(',') if s.strip()]
    if '{id}' in args.payload:
        targets = [(i, args.payload.replace('{id}', i)) for i in ids]
    else:
        # payload literal (tanpa placeholder) → satu target; id diambil dari JSON bila ada.
        qr_id = 'X'
        try:
            parsed = json.loads(args.payload)
            if isinstance(parsed, dict) and isinstance(parsed.get('id'), str):
                qr_id = parsed['id']
        except ValueError:
            pass   # payload non-JSON tetap boleh (groundtruth = string apa adanya)
        targets = [(qr_id, args.payload)]

    if args.input and len(targets) > 1:
        raise SystemExit(
            "--input hanya memuat SATU gambar QR, tapi --ids meminta beberapa "
            f"({', '.join(ids)}). Pakai --payload agar tiap id dirender, atau --ids <satu>.")

    if args.sweep:
        levels = _parse_sweep(args.sweep)
        write = args.write_sweep
    else:
        levels = []
        for name in [s.strip() for s in args.levels.split(',') if s.strip()]:
            levels.append((name, resolve_depth(name)))
        write = True

    if write:
        os.makedirs(args.outdir, exist_ok=True)

    rows = run_dataset(args, levels, targets, effects, write_images=write)
    summ = summarize(rows)

    if write:
        csv_path, json_path = _write_metadata(args, rows, summ, targets, effects)

    if args.json:
        print(json.dumps({
            'total': summ['total'], 'rescued_by_enhance': summ['rescued'],
            'raw_break_depth': summ['raw_break'],
            'enhance_break_depth': summ['enh_break'],
            'enhance_dead_depth': summ['enh_dead'],
            'by_depth': {str(k): v for k, v in summ['by_depth'].items()},
        }, indent=2, ensure_ascii=False))
    else:
        _print_report(rows, summ, args, effects)
        if write:
            print(f"  Dataset : {os.path.abspath(args.outdir)}  ({len(rows)} gambar)")
            print(f"  Anotasi : {os.path.basename(csv_path)} + {os.path.basename(json_path)}")

    if args.zip and write:
        zp = _zip_dataset(args.outdir)
        print(f"  ZIP     : {os.path.abspath(zp)}  (siap unggah ke Google Drive)")

    # Exit non-nol bila level paling dangkal pun sudah tak terbaca → regresi nyata.
    shallow_depth = min((d for _n, d in levels), default=None)
    if shallow_depth is not None:
        s = summ['by_depth'].get(round(shallow_depth, 4))
        if s and s['enh'] == 0:
            print("\n  ✗ REGRESI: level paling dangkal pun gagal total — cek pipeline/param.")
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
