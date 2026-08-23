"""
tests/test_wall_cnn.py — Uji fallback klasifikasi sisi kolam (vision/wall_cnn.py).

Menjaga tiga hal yang kalau putus akan gagal DIAM-DIAM di kolam:
  1. Inferensi numpy SETARA torch (bobot .npz hasil ekspor benar).
  2. CROP_MARGIN cocok dgn preprocessing training — salah skala = akurasi runtuh
     tanpa error (terukur: margin 1.15 → 0.7%, margin benar → 100%).
  3. Tebakan fallback TIDAK bocor ke latest_qr() — kalau bocor, FSM akan men-servo/GRAB
     dari tebakan tak tervalidasi (_is_target_payload menerima payload=None apa adanya).

Butuh cv2 + numpy + bobot vision/wall_cnn.npz; di-skip bila absen. Torch TIDAK diperlukan
(uji kesetaraan torch di-skip bila torch tak ada — memang tak dipasang di ROV).
"""
import os
import sys

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

PAYLOAD_TMPL = 'HYDROSHIP-M5-{id}'


def _weights_or_skip():
    from vision.wall_cnn import DEFAULT_WEIGHTS
    if not os.path.exists(DEFAULT_WEIGHTS):
        pytest.skip("vision/wall_cnn.npz belum diekspor "
                    "(python tools/export_wall_cnn.py --ckpt wall_cnn_fixed.pt)")
    return DEFAULT_WEIGHTS


def _clean_frame(cv2, np, segno, wall, module_px=8):
    from tests.underwater_sim import place_on_canvas, render_qr_bgr
    qr = render_qr_bgr(PAYLOAD_TMPL.format(id=wall), module_px=module_px, segno=segno)
    frame, _ = place_on_canvas(qr)
    return frame


# ── Operasi numpy ─────────────────────────────────────────────────────────────

def test_maxpool_and_conv_shapes():
    np = pytest.importorskip("numpy")
    from vision.wall_cnn import _conv2d_3x3, _maxpool2
    x = np.random.default_rng(0).normal(size=(3, 8, 8)).astype(np.float32)
    w = np.random.default_rng(1).normal(size=(5, 3, 3, 3)).astype(np.float32)
    b = np.zeros(5, np.float32)
    y = _conv2d_3x3(x, w, b)
    assert y.shape == (5, 8, 8), "conv 3x3 pad=1 harus menjaga H,W"
    assert _maxpool2(y).shape == (5, 4, 4)


def test_conv2d_matches_naive_reference():
    """im2col + stride_tricks gampang salah indeks — bandingkan dgn loop lugu."""
    np = pytest.importorskip("numpy")
    from vision.wall_cnn import _conv2d_3x3
    rng = np.random.default_rng(0)
    x = rng.normal(size=(2, 5, 5)).astype(np.float32)
    w = rng.normal(size=(3, 2, 3, 3)).astype(np.float32)
    b = rng.normal(size=3).astype(np.float32)
    got = _conv2d_3x3(x, w, b)

    xp = np.pad(x, ((0, 0), (1, 1), (1, 1)))
    exp = np.zeros((3, 5, 5), np.float32)
    for o in range(3):
        for i in range(5):
            for j in range(5):
                exp[o, i, j] = (w[o] * xp[:, i:i + 3, j:j + 3]).sum() + b[o]
    assert np.allclose(got, exp, atol=1e-4), "conv numpy tak cocok dgn referensi lugu"


def test_numpy_inference_matches_torch():
    """Bobot .npz + forward numpy harus setara torch (ROV tak memasang torch, jadi
    kesetaraan inilah satu-satunya jaminan hasilnya sama dgn yang dilatih)."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    torch = pytest.importorskip("torch")
    _weights_or_skip()
    ckpt = os.path.join(_AUTONOMY, 'wall_cnn_fixed.pt')
    if not os.path.exists(ckpt):
        pytest.skip("wall_cnn_fixed.pt tak ada — uji kesetaraan butuh checkpoint asal")
    import torch.nn as nn
    from vision.wall_cnn import WallClassifier

    class WallCNN(nn.Module):
        def __init__(self, n=4):
            super().__init__()
            def blk(i, o):
                return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o),
                                     nn.ReLU(), nn.MaxPool2d(2))
            self.f = nn.Sequential(blk(1, 16), blk(16, 32), blk(32, 64), blk(64, 64))
            self.head = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Flatten(),
                                      nn.Dropout(0.3), nn.Linear(64, n))

        def forward(self, x):
            return self.head(self.f(x))

    t = WallCNN()
    t.load_state_dict(torch.load(ckpt, map_location='cpu', weights_only=False)['state_dict'])
    t.eval()
    clf = WallClassifier()

    rng = np.random.default_rng(0)
    for _ in range(5):
        c = rng.normal(size=(96, 96)).astype(np.float32)
        with torch.no_grad():
            exp = torch.softmax(t(torch.from_numpy(c)[None, None].float()), 1)[0].numpy()
        got = clf.forward(c)
        assert np.abs(exp - got).max() < 1e-4, "numpy != torch → ekspor bobot rusak"


# ── Klasifikasi ───────────────────────────────────────────────────────────────

def test_classifier_correct_on_clean_qr():
    """Regression guard paling dasar: QR BERSIH keempat sisi harus benar. Ini juga yang
    menangkap checkpoint kolaps (pernah terjadi: model tak pernah menebak A/B)."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    _weights_or_skip()
    from vision.wall_cnn import WallClassifier
    clf = WallClassifier()
    for wall in 'ABCD':
        out = clf.predict_frame(_clean_frame(cv2, np, segno, wall))
        assert out is not None, f"QR bersih sisi {wall} gagal dilokalisasi"
        assert out[0] == wall, f"sisi {wall} salah diklasifikasi jadi {out[0]}"


def test_classifier_uses_all_four_classes():
    """Kolaps kelas = kegagalan senyap. Pastikan keempat kelas benar-benar terpakai."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    _weights_or_skip()
    from vision.wall_cnn import WallClassifier
    clf = WallClassifier()
    preds = {clf.predict_frame(_clean_frame(cv2, np, segno, w))[0] for w in 'ABCD'}
    assert preds == set('ABCD'), f"model hanya memakai {sorted(preds)} → kolaps"


def test_classifier_survives_underwater_degradation():
    """Nilai sesungguhnya: benar di frame yang decode_qr() sudah menyerah."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    pytest.importorskip("pyzbar")
    _weights_or_skip()
    from tests.underwater_sim import simulate_underwater
    from vision.qr_detect import decode_qr
    from vision.wall_cnn import WallClassifier
    clf = WallClassifier()

    checked = 0
    for wall in 'ABCD':
        frame = _clean_frame(cv2, np, segno, wall, module_px=6)
        out, _p = simulate_underwater(frame, 'deep', rng=np.random.default_rng(0), jitter=0.0)
        if decode_qr(out):
            continue                      # decode masih jalan → bukan kasus fallback
        got = clf.predict_frame(out)
        if got is None:
            continue                      # QR tak terlokalisasi — batas yang diketahui
        checked += 1
        assert got[0] == wall, f"fallback salah: {wall} → {got[0]}"
    if checked == 0:
        pytest.skip("tak ada frame 'deep' yang gagal decode + terlokalisasi di env ini")


def test_crop_margin_matches_training_scale():
    """CROP_MARGIN salah = akurasi runtuh TANPA error (terukur 1.15 → 0.7%).
    Nilai benar berasal dari qr_px(termasuk quiet zone)/sisi_detect() ≈ 1.295 × 1.15."""
    from vision.wall_cnn import CROP_MARGIN
    assert 1.35 <= CROP_MARGIN <= 1.8, (
        f"CROP_MARGIN={CROP_MARGIN} di luar dataran aman terukur (1.4-1.8); "
        "cek ulang vs preprocessing training")


def test_missing_weights_raises_clear_error(tmp_path):
    pytest.importorskip("numpy")
    from vision.wall_cnn import WallClassifier
    with pytest.raises(FileNotFoundError, match="export_wall_cnn"):
        WallClassifier(str(tmp_path / 'tak_ada.npz'))


# ── WallVoter ─────────────────────────────────────────────────────────────────

def test_voter_requires_consecutive_agreement():
    from vision.wall_cnn import WallVoter
    v = WallVoter(need=3, min_conf=0.8)
    assert v.push('A', 0.9) is None
    assert v.push('A', 0.9) is None
    assert v.push('A', 0.9) == 'A', "3 frame sepakat → percaya"


def test_voter_resets_on_disagreement_and_low_conf():
    from vision.wall_cnn import WallVoter
    v = WallVoter(need=3, min_conf=0.8)
    v.push('A', 0.9); v.push('A', 0.9)
    assert v.push('B', 0.9) is None, "ganti tebakan harus mengulang hitungan"
    v = WallVoter(need=2, min_conf=0.8)
    v.push('A', 0.9)
    assert v.push('A', 0.5) is None, "confidence rendah harus membatalkan"
    assert v.push('A', 0.9) is None, "…dan mengulang dari awal"


# ── Integrasi VisionPipeline ──────────────────────────────────────────────────

def test_wall_hint_never_leaks_into_latest_qr():
    """PALING PENTING: fallback tak boleh menyamar jadi deteksi QR. Mission5FSM
    ._is_target_payload() menerima apa saja yang payload-nya None, jadi kebocoran =
    FSM men-servo/GRAB dari tebakan tak tervalidasi."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    _weights_or_skip()
    from vision.qr_detect import VisionPipeline

    cam = VisionPipeline(source='mock', wall_cnn=True, wall_cnn_votes=1)
    frame = np.full((480, 640, 3), 128, np.uint8)
    _fake_pts = np.array([[280, 200], [360, 200], [360, 280], [280, 280]], dtype=np.float32)
    cam._wall_clf = type('F', (), {
        'locate': staticmethod(lambda g: _fake_pts),
        'predict_frame': staticmethod(lambda f, pts=None: ('C', 0.99)),
    })()
    cam._try_wall_fallback(frame)

    hint = cam.latest_wall_hint()
    assert hint is not None and hint['wall'] == 'C'
    assert hint['validated'] is False, "hint fallback WAJIB ditandai tak tervalidasi"
    assert hint['center'] == (320, 240), "center dari pts QUAD locate(), utk creep SCAN_QR"
    assert hint['area'] == 80.0 * 80.0, "area dari pts QUAD locate() (80x80 di _fake_pts)"
    assert cam.latest_qr() is None, "fallback BOCOR ke latest_qr() — bahaya!"
    assert cam.last_result() is None, "fallback tak boleh jadi hasil deteksi QR"


def test_wall_hint_expires_like_other_detections():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    _weights_or_skip()
    from vision.qr_detect import VisionPipeline
    cam = VisionPipeline(source='mock', wall_cnn=True, wall_cnn_votes=1)
    _fake_pts = np.array([[280, 200], [360, 200], [360, 280], [280, 280]], dtype=np.float32)
    cam._wall_clf = type('F', (), {
        'locate': staticmethod(lambda g: _fake_pts),
        'predict_frame': staticmethod(lambda f, pts=None: ('B', 0.95)),
    })()
    cam._try_wall_fallback(np.full((480, 640, 3), 128, np.uint8))
    assert cam.latest_wall_hint(max_age=10) is not None
    assert cam.latest_wall_hint(max_age=-1) is None, "hint basi harus ditolak"


def test_pipeline_works_without_weights(tmp_path):
    """Bobot belum diekspor TIDAK boleh mematikan pipeline QR normal."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from vision.qr_detect import VisionPipeline
    cam = VisionPipeline(source='mock', wall_cnn=str(tmp_path / 'tak_ada.npz'))
    assert cam._wall_clf is None, "harus gagal-lunak, bukan melempar"
    assert cam.latest_wall_hint() is None


def test_wall_cnn_disabled_by_default():
    """Fitur baru tak boleh diam-diam aktif — default harus perilaku lama."""
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("numpy")
    from vision.qr_detect import VisionPipeline
    cam = VisionPipeline(source='mock')
    assert cam._wall_clf is None and cam._wall_voter is None
