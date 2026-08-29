"""
tests/test_qr_underwater.py — Uji simulasi bawah air + robustness decode_qr().

Dua lapis:
  1. Tiap efek di tests/underwater_sim.py melakukan PERSIS yang dijanjikan (dan hanya
     itu — bisa di-toggle terpisah), serta deterministik terhadap seed → dataset
     yang dihasilkan bisa direproduksi.
  2. decode_qr() masih membaca payload di kedalaman dangkal (regression guard), dan
     eskalasi CLAHE/upscale memang yang menyelamatkan saat QR kecil + air keruh.

Butuh cv2 + numpy (tes efek) dan tambahan pyzbar + segno (tes decode); di-skip bila absen.
"""
import os
import sys

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

# Payload realistis — sama formatnya dgn yang dicetak tim (MOCK_QR_DATA di qr_detect).
# Payload pendek spt '{"id":"A"}' bikin QR versi-1 yang pyzbar sering gagal decode.
PAYLOAD = '{"mission":5,"team":"HYDROSHIP","type":"payload","id":"A"}'


def _gradient_bgr(np, cv2):
    """Kanvas uji dgn rentang penuh 0..255 + tekstur — bukan warna rata, supaya
    perubahan kontras/geometri benar-benar terukur."""
    g = np.tile(np.linspace(0, 255, 128, dtype=np.uint8), (128, 1))
    g[::8, :] = 0            # garis tipis → energi frekuensi tinggi utk uji blur
    g[:, ::8] = 255
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


# ── Lapis 1: efek ─────────────────────────────────────────────────────────────

def test_color_attenuation_reduces_red_channel():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import apply_color_attenuation
    img = _gradient_bgr(np, cv2)
    out = apply_color_attenuation(img, red_gain=0.3, green_gain=1.0, blue_gain=1.1)
    # channel BGR = index 0,1,2
    assert out[:, :, 2].mean() < img[:, :, 2].mean() * 0.5, "merah harus meredup tajam"
    assert out[:, :, 0].mean() >= img[:, :, 0].mean(), "biru tak boleh ikut meredup"
    assert out.dtype == np.uint8 and out.shape == img.shape


def test_backscatter_compresses_dynamic_range():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import apply_backscatter
    img = _gradient_bgr(np, cv2)
    assert img.min() == 0 and img.max() == 255      # prasyarat: rentang penuh
    out = apply_backscatter(img, black_point=60, white_point=180, haze_alpha=0.0)
    assert out.min() > img.min(), "hitam harus jadi tak pekat (black point naik)"
    assert out.max() < img.max(), "putih harus jadi tak bersih (white point turun)"


def test_backscatter_haze_pushes_toward_blue_green():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import apply_backscatter
    img = _gradient_bgr(np, cv2)                     # abu-abu netral: B == R
    out = apply_backscatter(img, black_point=0, white_point=255, haze_alpha=0.4)
    assert out[:, :, 0].mean() > out[:, :, 2].mean(), "kabut harus biru-kehijauan (B > R)"


def test_ripple_changes_geometry_but_preserves_shape():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import apply_ripple_distortion
    img = _gradient_bgr(np, cv2)
    out = apply_ripple_distortion(img, amp_x=3.0, amp_y=2.0, freq_x=3.0, freq_y=4.0)
    assert out.shape == img.shape, "remap tak boleh mengubah ukuran frame"
    assert not np.array_equal(out, img), "riak harus benar-benar menggeser piksel"


def test_ripple_zero_amplitude_is_noop():
    """amp=0 → identitas. Menjamin depth rendah tidak diam-diam merusak geometri."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import apply_ripple_distortion
    img = _gradient_bgr(np, cv2)
    out = apply_ripple_distortion(img, amp_x=0.0, amp_y=0.0, freq_x=3.0, freq_y=4.0)
    assert np.array_equal(out, img), "amplitudo 0 harus jadi no-op"


def test_blur_reduces_high_frequency_energy():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import apply_blur
    img = _gradient_bgr(np, cv2)
    sharp = cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    out = apply_blur(img, ksize=5, sigma=2.0)
    soft = cv2.Laplacian(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    assert soft < sharp, "blur harus menurunkan energi frekuensi tinggi"


def test_blur_forces_odd_kernel_in_range():
    """ksize genap/di luar 3..5 harus dikoreksi, bukan melempar cv2.error."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import apply_blur
    img = _gradient_bgr(np, cv2)
    for k in (2, 4, 9):
        assert apply_blur(img, ksize=k, sigma=1.0).shape == img.shape


def test_effects_are_independently_toggleable():
    """effects={'blur'} tak boleh mengubah warna; effects={'color'} tak boleh mem-blur."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import simulate_underwater
    img = _gradient_bgr(np, cv2)

    only_blur, _ = simulate_underwater(img, 'deep', effects={'blur'})
    assert abs(float(only_blur[:, :, 2].mean()) - float(img[:, :, 2].mean())) < 2.0, \
        "hanya blur → statistik merah harus ~tak berubah"

    only_color, _ = simulate_underwater(img, 'deep', effects={'color'})
    lap_src = cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    lap_out = cv2.Laplacian(cv2.cvtColor(only_color, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    assert lap_out > lap_src * 0.5, "hanya color → ketajaman tak boleh runtuh"


def test_simulate_underwater_no_effects_is_noop():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import simulate_underwater
    img = _gradient_bgr(np, cv2)
    out, _p = simulate_underwater(img, 'deep', effects=set())
    assert np.array_equal(out, img), "tanpa efek aktif → gambar harus utuh"


def test_simulate_underwater_does_not_mutate_input():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import simulate_underwater
    img = _gradient_bgr(np, cv2)
    before = img.copy()
    simulate_underwater(img, 'deep', rng=np.random.default_rng(0), jitter=0.1)
    assert np.array_equal(img, before), "input tak boleh diubah di tempat"


def test_simulate_underwater_is_deterministic_with_seed():
    """Jaminan dataset reproducible: seed sama → byte-identik; seed beda → berbeda."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import simulate_underwater
    img = _gradient_bgr(np, cv2)
    a, pa = simulate_underwater(img, 'deep', rng=np.random.default_rng(42), jitter=0.1)
    b, pb = simulate_underwater(img, 'deep', rng=np.random.default_rng(42), jitter=0.1)
    c, _pc = simulate_underwater(img, 'deep', rng=np.random.default_rng(7), jitter=0.1)
    assert np.array_equal(a, b), "seed sama harus menghasilkan gambar identik"
    assert pa['amp_x'] == pb['amp_x']
    assert not np.array_equal(a, c), "seed beda harus menghasilkan varian berbeda"


def test_deeper_is_monotonically_more_degraded():
    """Makin dalam = makin parah (merah makin redup, riak makin besar). Menjaga
    'depth_level' tetap bermakna sebagai sumbu tunggal."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import params_for_depth
    p0, p1 = params_for_depth(0.1), params_for_depth(0.9)
    assert p1['red_gain'] < p0['red_gain'], "merah harus makin redup"
    assert p1['amp_x'] > p0['amp_x'], "riak harus makin kuat"
    assert p1['black_point'] > p0['black_point'], "hitam makin tak pekat"
    assert p1['white_point'] < p0['white_point'], "putih makin tak bersih"


def test_params_for_depth_accepts_preset_names_and_floats():
    pytest.importorskip("numpy")
    from tests.underwater_sim import DEPTH_ANCHORS, params_for_depth, resolve_depth
    assert resolve_depth('deep') == DEPTH_ANCHORS['deep']
    assert params_for_depth('medium')['depth_value'] == DEPTH_ANCHORS['medium']
    assert params_for_depth(0.4)['depth_value'] == 0.4
    with pytest.raises(ValueError):
        resolve_depth('abyss')                 # preset tak dikenal
    with pytest.raises(ValueError):
        resolve_depth(1.5)                     # di luar [0,1]


def test_simulate_underwater_rejects_unknown_effect():
    pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from tests.underwater_sim import simulate_underwater
    img = np.full((32, 32, 3), 128, np.uint8)
    with pytest.raises(ValueError):
        simulate_underwater(img, 'deep', effects={'kraken'})


# ── Lapis 2: robustness decode_qr() ───────────────────────────────────────────

def test_render_qr_never_emits_micro_qr():
    """Regresi: segno memilih Micro QR (M1-M4) utk teks pendek, dan pyzbar TIDAK BISA
    membacanya sama sekali → QR bersih pun gagal decode & seluruh dataset jadi 0%.
    Payload sungguhan tim ('HYDROSHIP-M5-A') cukup pendek untuk memicu ini."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    pytest.importorskip("pyzbar")
    from tests.underwater_sim import place_on_canvas, render_qr_bgr
    from vision.qr_detect import decode_qr

    for text in ('HYDROSHIP-M5-A', 'A', PAYLOAD):
        qr = render_qr_bgr(text, module_px=8, segno=segno)
        frame, _ = place_on_canvas(qr)
        res = decode_qr(frame)
        assert res, f"QR BERSIH {text!r} harus terbaca (Micro QR bocor ke render?)"
        assert res[0]['data'] == text, f"isi QR {text!r} harus utuh"


def test_printed_payload_format_maps_to_wall():
    """QR yang tim CETAK berisi string biasa 'HYDROSHIP-M5-A' (terverifikasi decode dari
    PDF), BUKAN JSON. Pastikan format itu tetap memetakan ke sisi kolam A/B/C/D lewat
    jalur regex legacy — kalau ini putus, misi 1 (SCAN_QR) gagal di kolam."""
    from vision.qr_detect import parse_payload, wall_from_qr
    for wall in ('A', 'B', 'C', 'D'):
        data = f'HYDROSHIP-M5-{wall}'
        assert parse_payload(data) is None, "payload cetak memang bukan JSON"
        assert wall_from_qr(data) == wall, f"{data} harus memetakan ke sisi {wall}"



def test_shallow_still_decodes():
    """Regression guard: air dangkal TIDAK boleh mematahkan pipeline."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    pytest.importorskip("pyzbar")
    from tests.underwater_sim import place_on_canvas, render_qr_bgr, simulate_underwater
    from vision.qr_detect import decode_qr

    qr = render_qr_bgr(PAYLOAD, module_px=6, segno=segno)
    frame, _ = place_on_canvas(qr)
    out, _p = simulate_underwater(frame, 'shallow', rng=np.random.default_rng(0), jitter=0.0)
    res = decode_qr(out)
    assert res, "QR di air dangkal harus tetap terbaca"
    assert res[0]['data'] == PAYLOAD, "payload JSON harus utuh (tak berubah isi)"


def test_deep_breaks_decoding():
    """Sisi lain regression guard: 'deep' memang harus di luar kemampuan sistem,
    kalau tidak, preset-nya terlalu lembut & dataset jadi tak informatif."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    pytest.importorskip("pyzbar")
    from tests.underwater_sim import place_on_canvas, render_qr_bgr, simulate_underwater
    from vision.qr_detect import decode_qr

    qr = render_qr_bgr(PAYLOAD, module_px=6, segno=segno)
    frame, _ = place_on_canvas(qr)
    out, _p = simulate_underwater(frame, 'deep', rng=np.random.default_rng(0), jitter=0.0)
    assert not decode_qr(out), "'deep' seharusnya mematahkan decode (preset terlalu lembut?)"


def test_enhanced_decoder_recovers_ripple_case():
    """Riak tetap mematahkan jalur mentah, tetapi fallback enhanced harus boleh
    memulihkannya. Ini menjaga tes selaras dengan decoder ZXing 4x terbaru."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    pytest.importorskip("pyzbar")
    from tests.underwater_sim import (
        ALL_EFFECTS, place_on_canvas, render_qr_bgr, simulate_underwater)
    from vision.qr_detect import decode_qr

    qr = render_qr_bgr(PAYLOAD, module_px=6, segno=segno)
    frame, _ = place_on_canvas(qr)

    photometric = set(ALL_EFFECTS) - {'ripple'}
    out_photo, _ = simulate_underwater(frame, 'deep', rng=np.random.default_rng(0),
                                       jitter=0.0, effects=photometric)
    out_ripple, _ = simulate_underwater(frame, 'deep', rng=np.random.default_rng(0),
                                        jitter=0.0, effects={'ripple'})
    assert decode_qr(out_photo), "tanpa riak, degradasi fotometrik 'deep' masih terbaca"
    assert not decode_qr(out_ripple, enhance=False), "riak harus mematahkan jalur mentah"
    assert decode_qr(out_ripple), "fallback enhanced harus memulihkan riak sintetis"


def test_enhancement_rescues_small_qr_in_hazy_water():
    """Zona di mana eskalasi CLAHE/upscale MENGHASILKAN NILAI: QR kecil (jauh) +
    air keruh fotometrik. Pola sama dgn test_decode_qr_enhance_false_is_raw_only."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    pytest.importorskip("pyzbar")
    from tests.underwater_sim import (
        ALL_EFFECTS, place_on_canvas, render_qr_bgr, simulate_underwater)
    from vision.qr_detect import decode_qr

    qr = render_qr_bgr(PAYLOAD, module_px=2, segno=segno)       # kecil = jauh
    frame, _ = place_on_canvas(qr)
    photometric = set(ALL_EFFECTS) - {'ripple'}                 # riak = tebing, bukan gradasi
    out, _p = simulate_underwater(frame, 'shallow', rng=np.random.default_rng(0),
                                  jitter=0.0, effects=photometric)

    if decode_qr(out, enhance=False):
        pytest.skip("pyzbar mentah kebetulan berhasil di env ini — A/B tak diskriminatif")
    assert decode_qr(out, enhance=True), "CLAHE/upscale seharusnya memulihkan deteksi"


# ── Harness evaluasi (tests/evaluate_qr_underwater.py) ────────────────────────

def test_decode_with_stage_reports_winning_stage(monkeypatch):
    """decode_with_stage() harus melaporkan jenjang yang benar & MEMULIHKAN
    pyzbar.decode asli (tak boleh membocorkan monkeypatch ke tes lain)."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    pytest.importorskip("pyzbar")
    import vision.qr_detect as qd
    from tests.evaluate_qr_underwater import decode_with_stage

    class _Pt:
        def __init__(self, x, y):
            self.x, self.y = x, y

    class _Sym:
        type = 'QRCODE'

        def __init__(self, data, poly):
            self.data = data
            self.polygon = [_Pt(x, y) for x, y in poly]

    calls = {'n': 0}

    def fake_decode(img, *a, **kw):
        calls['n'] += 1
        if calls['n'] < 3:                     # raw & CLAHE gagal, upscale berhasil
            return []
        return [_Sym(PAYLOAD.encode(), [(200, 200), (240, 200), (240, 240), (200, 240)])]

    monkeypatch.setattr(qd, '_quiet_zone_ok', lambda *a, **kw: True)
    monkeypatch.setattr(qd.pyzbar, 'decode', fake_decode)
    sentinel = qd.pyzbar.decode
    frame = np.full((480, 640, 3), 128, np.uint8)

    out = decode_with_stage(frame, PAYLOAD)
    assert out['stage'] == 'upscale', "jenjang ke-3 yang berhasil → 'upscale'"
    assert out['ok'] is True and out['data'] == PAYLOAD
    assert qd.pyzbar.decode is sentinel, "pyzbar.decode asli harus dipulihkan"


def test_decode_with_stage_ok_false_on_payload_mismatch(monkeypatch):
    """'berhasil' = payload SAMA PERSIS groundtruth, bukan sekadar ada deteksi."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    pytest.importorskip("pyzbar")
    import vision.qr_detect as qd
    from tests.evaluate_qr_underwater import decode_with_stage

    class _Pt:
        def __init__(self, x, y):
            self.x, self.y = x, y

    class _Sym:
        type = 'QRCODE'

        def __init__(self, data, poly):
            self.data = data
            self.polygon = [_Pt(x, y) for x, y in poly]

    monkeypatch.setattr(qd, '_quiet_zone_ok', lambda *a, **kw: True)
    monkeypatch.setattr(qd.pyzbar, 'decode', lambda img, *a, **kw: [
        _Sym(b'{"mission":5,"team":"HYDROSHIP","type":"payload","id":"B"}',
             [(10, 10), (20, 10), (20, 20), (10, 20)])])
    frame = np.full((480, 640, 3), 128, np.uint8)

    out = decode_with_stage(frame, PAYLOAD)       # groundtruth id=A, terbaca id=B
    assert out['stage'] == 'raw'
    assert out['ok'] is False, "payload beda harus dihitung GAGAL, walau QR terdeteksi"


def test_decode_with_stage_reports_fail_when_nothing_decodes(monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    pytest.importorskip("pyzbar")
    import vision.qr_detect as qd
    from tests.evaluate_qr_underwater import decode_with_stage

    monkeypatch.setattr(qd.pyzbar, 'decode', lambda img, *a, **kw: [])
    blank = np.full((480, 640, 3), 200, np.uint8)
    out = decode_with_stage(blank, PAYLOAD)
    assert out == {'ok': False, 'data': '', 'stage': 'fail'}


def test_sweep_spec_parsing():
    from tests.evaluate_qr_underwater import _parse_sweep
    levels = _parse_sweep('0:1:0.5')
    assert [d for _n, d in levels] == [0.0, 0.5, 1.0]
    assert levels[0][0] == 'd0.00'
    with pytest.raises(SystemExit):
        _parse_sweep('bukan-angka')
    with pytest.raises(SystemExit):
        _parse_sweep('0:1:0')                   # langkah 0 → tak boleh infinite loop
