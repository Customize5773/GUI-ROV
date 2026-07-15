"""
tests/underwater_sim.py — Simulasi kondisi bawah air untuk gambar QR
====================================================================
Mendegradasi gambar QR seolah difoto di bawah air, untuk mengukur SAMPAI LEVEL MANA
`vision.qr_detect.decode_qr()` masih membaca payload dengan benar.

Peran modul ini = cermin `tests/sim_plant.py`: pustaka simulasi murni (tanpa CLI) yang
dipakai bersama oleh tes unit (`tests/test_qr_underwater.py`) dan harness evaluasi
(`tests/evaluate_qr_underwater.py`).

Empat efek, masing-masing berdiri sendiri & bisa di-toggle:
  1. apply_color_attenuation() — air menyerap merah lebih cepat dari biru/hijau.
  2. apply_backscatter()       — partikel menurunkan kontras + kabut biru-kehijauan.
  3. apply_ripple_distortion() — riak/caustics; PALING merusak finder pattern QR.
  4. apply_blur()              — pelunakan optik.

Skala kedalaman = float KONTINU 0.0 (permukaan) → 1.0 (dalam). Nama preset
('shallow'/'medium'/'deep') hanyalah anchor pada skala ini (lihat DEPTH_ANCHORS),
sehingga ambang kegagalan bisa dilokalisasi lebih presisi daripada 3 bucket diskrit.

Semua fungsi menerima & mengembalikan citra BGR uint8, dan TIDAK mengubah input.

Butuh: opencv-python, numpy. Untuk render QR sintetis juga butuh segno.
"""

import os
import sys

import numpy as np
import cv2

# autonomy/ harus di sys.path agar `vision`, `tests` ter-import (pola sama spt tes lain)
_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)


# ── Skala kedalaman ───────────────────────────────────────────────────────────
# Anchor bernama pada skala kontinu 0..1. Dipakai untuk penamaan folder dataset;
# mesin simulasi sendiri selalu bekerja pada float.
DEPTH_ANCHORS = {'shallow': 0.25, 'medium': 0.55, 'deep': 0.85}

# Nama efek yang bisa di-toggle lewat argumen `effects`.
ALL_EFFECTS = ('color', 'backscatter', 'ripple', 'blur')

# Rentang parameter (nilai di depth=0.0, nilai di depth=1.0) — diinterpolasi linier.
# CATATAN TUNING: angka ini titik awal; kalibrasi ulang dari hasil --sweep bila
# `deep` masih terbaca 100% (kurang agresif) atau `shallow` sudah gagal (kelewat agresif).
_RANGES = {
    # 1. Atenuasi warna — merah hilang duluan; biru/hijau justru menguat relatif.
    'red_gain':    (0.85, 0.20),
    'green_gain':  (1.00, 1.05),
    'blue_gain':   (1.00, 1.18),
    # 2. Backscatter — hitam tak pekat (black point naik), putih tak bersih (white point turun).
    'black_point': (8.0, 92.0),
    'white_point': (248.0, 162.0),
    'haze_alpha':  (0.04, 0.36),
    # 3. Riak — amplitudo (px) & frekuensi (siklus per frame), beda untuk X/Y.
    # Batas atas dikalibrasi empiris: decode putus saat perpindahan riak ≈0.4 lebar
    # modul QR (terukur konsisten di module_px 4/6/8). Rentang 0.3→3.2 px menempatkan
    # tebing itu di sekitar depth 0.5-0.85 utk QR ukuran realistis → gradasi antar level
    # terjaga (bukan cliff yang membuat medium & deep sama-sama gagal total).
    'amp_x':       (0.3, 3.2),
    'amp_y':       (0.2, 2.5),
    # Frekuensi rendah = riak berskala besar (fisik: caustics jauh lebih lebar dari QR
    # 4x4cm). Frekuensi tinggi merusak jauh lebih parah karena melengkungkan modul DI
    # DALAM QR, bukan sekadar menggeser QR — jadi rentangnya sengaja ditahan.
    'freq_x':      (1.0, 3.0),
    'freq_y':      (1.2, 3.5),
    # 4. Blur optik — radius ~1..3 px.
    'sigma':       (0.5, 2.6),
}

# Warna kabut biru-kehijauan (BGR): biru & hijau tinggi, merah rendah.
HAZE_BGR = (150, 120, 15)

# Parameter yang di-jitter (fraksi ±). Fase riak selalu diacak penuh bila ada rng.
_JITTERABLE = ('red_gain', 'green_gain', 'blue_gain', 'black_point', 'white_point',
               'haze_alpha', 'amp_x', 'amp_y', 'freq_x', 'freq_y', 'sigma')


def resolve_depth(depth_level) -> float:
    """Nama preset ('shallow'|'medium'|'deep') atau angka → float depth 0..1."""
    if isinstance(depth_level, str):
        key = depth_level.strip().lower()
        if key not in DEPTH_ANCHORS:
            raise ValueError(
                f"depth_level '{depth_level}' tidak dikenal; "
                f"pilih {sorted(DEPTH_ANCHORS)} atau float 0..1")
        return DEPTH_ANCHORS[key]
    d = float(depth_level)
    if not 0.0 <= d <= 1.0:
        raise ValueError(f"depth_level harus di [0,1], dapat {d}")
    return d


def params_for_depth(depth_level, rng=None, jitter=0.0) -> dict:
    """Turunkan parameter SEMUA efek dari satu skala kedalaman 0..1.

    rng    : np.random.Generator opsional. Bila ada → fase riak diacak & parameter
             di-jitter, sehingga tiap sample per level berbeda TAPI reproducible
             lewat seed (penting: dataset harus bisa dibuat ulang persis).
    jitter : fraksi variasi acak (0.08 = ±8%). 0 → parameter deterministik per depth.
    """
    d = resolve_depth(depth_level)
    p = {name: lo + (hi - lo) * d for name, (lo, hi) in _RANGES.items()}

    if rng is not None and jitter > 0:
        for name in _JITTERABLE:
            p[name] *= 1.0 + float(rng.uniform(-jitter, jitter))

    # Fase riak: acak bila ada rng (riak nyata tak pernah sefase), else tetap.
    if rng is not None:
        p['phase_x'] = float(rng.uniform(0, 2 * np.pi))
        p['phase_y'] = float(rng.uniform(0, 2 * np.pi))
    else:
        p['phase_x'] = 0.0
        p['phase_y'] = float(np.pi / 2)      # beda fase X/Y (diminta spek)

    # Klem ke rentang yang masuk akal secara fisik setelah jitter.
    p['red_gain']    = float(np.clip(p['red_gain'], 0.05, 1.5))
    p['green_gain']  = float(np.clip(p['green_gain'], 0.05, 1.5))
    p['blue_gain']   = float(np.clip(p['blue_gain'], 0.05, 1.5))
    p['black_point'] = float(np.clip(p['black_point'], 0, 120))
    p['white_point'] = float(np.clip(p['white_point'], p['black_point'] + 20, 255))
    p['haze_alpha']  = float(np.clip(p['haze_alpha'], 0.0, 0.9))
    for k in ('amp_x', 'amp_y', 'freq_x', 'freq_y'):
        p[k] = float(max(0.0, p[k]))
    p['sigma'] = float(np.clip(p['sigma'], 0.0, 6.0))
    # ksize Gauss: 3 utk dangkal, 5 utk dalam (spek: 3x3..5x5).
    p['ksize'] = 3 if d < 0.5 else 5
    p['depth_value'] = d
    p['haze_bgr'] = HAZE_BGR
    return p


# ── Efek 1: Atenuasi warna ────────────────────────────────────────────────────
def apply_color_attenuation(img, red_gain=0.35, green_gain=1.02, blue_gain=1.10):
    """Air menyerap gelombang panjang (merah) jauh lebih cepat dari biru/hijau.

    Kalikan channel Red dengan faktor kecil (makin dalam makin kecil), naikkan
    sedikit Blue & Green. img BGR uint8 → BGR uint8.
    """
    b, g, r = cv2.split(img.astype(np.float32))
    b *= float(blue_gain)
    g *= float(green_gain)
    r *= float(red_gain)
    return cv2.merge([b, g, r]).clip(0, 255).astype(np.uint8)


# ── Efek 2: Kabut / backscatter ───────────────────────────────────────────────
def apply_backscatter(img, black_point=50.0, white_point=200.0,
                      haze_bgr=HAZE_BGR, haze_alpha=0.2):
    """Partikel di air menurunkan kontras: hitam tak pekat, putih tak bersih.

    Dua tahap:
      a) Levels/Curves — remap [0,255] → [black_point, white_point] (kompresi rentang
         dinamis: black point NAIK, white point TURUN).
      b) Blending layer warna solid biru-kehijauan via cv2.addWeighted (opasitas rendah).
    """
    f = img.astype(np.float32)
    f = float(black_point) + f * (float(white_point) - float(black_point)) / 255.0
    out = f.clip(0, 255).astype(np.uint8)

    if haze_alpha > 0:
        haze = np.full_like(out, np.array(haze_bgr, dtype=np.uint8))
        out = cv2.addWeighted(out, 1.0 - float(haze_alpha), haze, float(haze_alpha), 0.0)
    return out


# ── Efek 3: Distorsi geometris (caustics & riak) ──────────────────────────────
def apply_ripple_distortion(img, amp_x=2.0, amp_y=1.5, freq_x=3.0, freq_y=4.0,
                            phase_x=0.0, phase_y=np.pi / 2):
    """Riak permukaan/caustics — grid perpindahan sinusoidal via cv2.remap.

    Bagian PALING kritis untuk decode: menggeser modul QR secara non-linier dan
    merusak kesikuan finder pattern (yang diandalkan pyzbar untuk lokalisasi).

    Offset X dan Y memakai fase berbeda agar distorsinya tidak sekadar geser seragam:
      map_x = x + amp_x * sin(2π * freq_x * y / H + phase_x)
      map_y = y + amp_y * sin(2π * freq_y * x / W + phase_y)
    amp dalam piksel, freq dalam siklus per tinggi/lebar frame.
    """
    h, w = img.shape[:2]
    xx, yy = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    map_x = xx + float(amp_x) * np.sin(2 * np.pi * float(freq_x) * yy / h + float(phase_x))
    map_y = yy + float(amp_y) * np.sin(2 * np.pi * float(freq_y) * xx / w + float(phase_y))
    return cv2.remap(img, map_x.astype(np.float32), map_y.astype(np.float32),
                     interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


# ── Efek 4: Blur optik ────────────────────────────────────────────────────────
def apply_blur(img, ksize=3, sigma=1.0):
    """Pelunakan optik — GaussianBlur kernel kecil (3×3..5×5, radius ~1-3 px)."""
    k = int(ksize)
    if k % 2 == 0:          # cv2 menolak kernel genap
        k += 1
    k = max(3, min(5, k))   # spek: 3x3 sampai 5x5
    if sigma <= 0:
        return img.copy()
    return cv2.GaussianBlur(img, (k, k), float(sigma))


# ── Gabungan ──────────────────────────────────────────────────────────────────
def simulate_underwater(img, depth_level='medium', rng=None, jitter=0.0, effects=None):
    """Terapkan simulasi bawah air pada `img` (BGR) untuk `depth_level`.

    Parameters
    ----------
    depth_level : 'shallow'|'medium'|'deep' atau float 0..1 (kontinu).
    rng         : np.random.Generator — untuk jitter & fase riak yang reproducible.
    jitter      : fraksi variasi acak antar-sample (butuh rng).
    effects     : iterable subset dari ALL_EFFECTS; None → semua aktif.
                  Contoh: effects={'ripple'} untuk mengisolasi satu efek.

    Returns
    -------
    (out_img, params) — `params` berisi parameter PERSIS yang dipakai (termasuk
    hasil jitter), sehingga baris metadata dataset merekam angka sebenarnya,
    bukan sekadar nama level.

    Urutan efek meniru fisika: atenuasi di kolom air → partikel → permukaan
    bergelombang → optik kamera.
    """
    active = set(ALL_EFFECTS if effects is None else effects)
    unknown = active - set(ALL_EFFECTS)
    if unknown:
        raise ValueError(f"efek tak dikenal: {sorted(unknown)}; pilih dari {ALL_EFFECTS}")

    p = params_for_depth(depth_level, rng=rng, jitter=jitter)
    out = img.copy()

    if 'color' in active:
        out = apply_color_attenuation(out, p['red_gain'], p['green_gain'], p['blue_gain'])
    if 'backscatter' in active:
        out = apply_backscatter(out, p['black_point'], p['white_point'],
                                p['haze_bgr'], p['haze_alpha'])
    if 'ripple' in active:
        out = apply_ripple_distortion(out, p['amp_x'], p['amp_y'],
                                      p['freq_x'], p['freq_y'],
                                      p['phase_x'], p['phase_y'])
    if 'blur' in active:
        out = apply_blur(out, p['ksize'], p['sigma'])

    p['effects'] = sorted(active)
    return out, p


# ── Helper render QR (dipakai bersama test_qr_detect.py & harness) ─────────────
# Dipindah ke sini dari tests/test_qr_detect.py agar tak terduplikasi tiga arah
# (tes lama, tes baru, harness dataset). Perilaku identik dengan versi asal.

def render_qr_bgr(text, module_px=8, border=4, segno=None, error='m'):
    """Render QR → citra BGR; ukuran modul = module_px piksel/modul.

    `segno` boleh dioper (pola lama `pytest.importorskip`) atau dibiarkan None
    untuk diimpor di sini.
    """
    import io
    if segno is None:
        import segno as segno_mod
        segno = segno_mod
    png = io.BytesIO()
    segno.make(text, error=error).save(png, kind='png', scale=module_px, border=border)
    png.seek(0)
    gray = cv2.imdecode(np.frombuffer(png.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def place_on_canvas(qr_bgr, canvas_wh=(640, 480), contrast=1.0, offset=(0, 0)):
    """Tempel QR di kanvas abu-abu; contrast<1 menurunkan kontras (cahaya buruk).

    Kembalikan (frame, (x0, y0, w, h)).
    """
    W, H = canvas_wh
    frame = np.full((H, W, 3), 128, np.uint8)
    h, w = qr_bgr.shape[:2]
    if contrast < 1.0:                       # tekan ke arah abu-abu 128 → kontras rendah
        qr_bgr = (128 + (qr_bgr.astype(np.float32) - 128) * contrast).astype(np.uint8)
    y0 = (H - h) // 2 + offset[1]
    x0 = (W - w) // 2 + offset[0]
    frame[y0:y0 + h, x0:x0 + w] = qr_bgr
    return frame, (x0, y0, w, h)
