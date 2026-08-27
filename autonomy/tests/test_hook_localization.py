"""
tests/test_hook_localization.py — Uji vision/hook_localization.py (lokalisasi ROV
memakai hook pipa sbg landmark).

Pola sama `test_hook_detect.py`: pytest + `importorskip`, sys.path ke dir autonomy,
geometri/pose SINTETIS (tak butuh kamera/kolam). Menutup 10 poin "Testing wajib":
model geometri, transform frame, proyeksi sintetis, ambiguitas planar, contour
palsu se-frame, dropout & expiration, map A/B/C/D + trial_assignment, resolusi
kalibrasi tak cocok, penolakan null di config, dan regresi jalur M5/QR.

Yang TIDAK diuji di sini (dan memang tak bisa): akurasi pada frame bawah air
sungguhan. Itu Tahap 2 (replay recorded frame) — lihat PR-AUTONOMY.md HOOK-03.
"""
import json
import math
import os
import sys

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

np = pytest.importorskip("numpy")

from vision.hook_localization import (            # noqa: E402
    DEFAULT_GATES, HOOK_MODEL, PNP_POINTS, PNP_POINTS_WITH_MOUNT,
    HookTracker, hook_model_points, load_hook_map, localize_hook,
    rot_base_cam, rot_map_base,
)


# ── Helper ────────────────────────────────────────────────────────────────────

K_WALL = np.array([[864.0, 0.0, 640.0],      # ~vision/calibration/wall.npz @1280x720
                   [0.0, 864.0, 360.0],
                   [0.0, 0.0, 1.0]])


def _calib(size=(1280, 720), K=None):
    return {'K': K_WALL if K is None else K, 'dist': np.zeros(5),
            'image_size': size, 'name': 'synth'}


def _det(**kw):
    """Deteksi hook sintetis yang LOLOS semua gate kualitas (baseline sehat)."""
    d = {'type': 'hook', 'center': (640, 360), 'bbox': (600, 300, 80, 120),
         'area': 4000.0, 'width_px': 25.0, 'confidence': 0.9,
         'method': 'contour', 'frame_w': 1280, 'frame_h': 720,
         'pose': None, 'timestamp': 1.0}
    d.update(kw)
    return d


# Geometri acuan seluruh test: x_axis_heading_deg = 0 → sumbu +x map menunjuk
# bearing 0 (Utara), dan sumbu +y map menunjuk bearing −90 = 270 (lihat
# rot_map_base: map right-handed z-ATAS, jadi +y ada di α−90, BUKAN α+90).
# Karena itu heading ROV saat MENGHADAP TEGAK LURUS ke tiap dinding:
#   dinding y=0    → ROV menghadap −y map → bearing  90
#   dinding y=2.2  → ROV menghadap +y map → bearing 270
#   dinding x=4.4  → ROV menghadap +x map → bearing   0
#   dinding x=0    → ROV menghadap −x map → bearing 180
def _map(tmp_path, hooks=None, x_axis=0.0, **extra):
    """Tulis hook map valid ke tmp_path → dict hasil load_hook_map()."""
    doc = {
        'pool': {'length_x': 4.4, 'width_y': 2.2, 'depth': 0.8},
        'map': {'x_axis_heading_deg': x_axis},
        'hooks': hooks if hooks is not None else {
            'A': {'x': 2.2, 'y': 0.0, 'z': 0.385, 'wall_heading_deg': 90},
        },
    }
    doc.update(extra)
    p = tmp_path / 'hook_map.json'          # .json → tak butuh PyYAML
    p.write_text(json.dumps(doc), encoding='utf-8')
    return load_hook_map(str(p))


def _vehicle(**kw):
    v = {'depth': 0.4, 'heading': 90.0, 'roll': 0.0, 'pitch': 0.0}
    v.update(kw)
    return v


def _project(rvec, tvec, points=PNP_POINTS, K=None, model=None, noise_px=0.0, seed=0):
    """Model hook 3D → keypoint citra, dgn pose kamera yang DIKETAHUI.

    `noise_px` penting untuk uji ambiguitas: ambiguitas planar adalah fenomena
    RELATIF TERHADAP DERAU — pada data sintetis tanpa derau, solvePnP selalu bisa
    memisahkan kedua solusi secara matematis, jadi menguji ambiguitas tanpa derau
    berarti menguji idealisasi yang tak ada di kamera mana pun."""
    cv2 = pytest.importorskip("cv2")
    _, obj = hook_model_points(model, points)
    img, _ = cv2.projectPoints(obj, np.asarray(rvec, float), np.asarray(tvec, float),
                               K_WALL if K is None else K, np.zeros(5))
    img = img.reshape(-1, 2)
    if noise_px:
        img = img + np.random.default_rng(seed).normal(0.0, noise_px, img.shape)
    return img


# ── 1. Model geometri hook ────────────────────────────────────────────────────

def test_model_origin_di_pusat_u_bukan_bbox():
    """Titik referensi pose = PUSAT LENGKUNG-U (origin), bukan center bounding box."""
    names, pts = hook_model_points(points=('u_center',) + PNP_POINTS)
    assert names[0] == 'u_center'
    assert np.allclose(pts[0], [0, 0, 0]), "u_center harus persis di origin frame hook"


def test_model_jarak_cocok_parameter_geometri():
    r, leg = HOOK_MODEL['u_radius_m'], HOOK_MODEL['leg_length_m']
    names, pts = hook_model_points()
    p = dict(zip(names, pts))
    # bukaan U = 2 x radius
    assert abs(np.linalg.norm(p['u_right'] - p['u_left']) - 2 * r) < 1e-6
    # dasar U tepat satu radius di bawah pusat
    assert abs(np.linalg.norm(p['u_bottom']) - r) < 1e-6
    # ujung kaki tepat `leg` di atas garis pusat-U
    assert abs(p['leg_left_tip'][1] - leg) < 1e-6
    assert abs(p['leg_right_tip'][1] - leg) < 1e-6


def test_model_bisa_dikonfigurasi():
    _, a = hook_model_points()
    _, b = hook_model_points({'u_radius_m': 0.10})
    assert not np.allclose(a, b), "u_radius_m tak mengalir ke titik model"


def test_model_pnp_points_sebidang_wall_mount_memecahnya():
    """Kesebidangan itu FISIK, bukan kelalaian model — dan justru sumber ambiguitas."""
    _, planar = hook_model_points()
    assert np.allclose(planar[:, 2], 0.0), "siluet hook memang sebidang"
    _, dgn_mount = hook_model_points(points=PNP_POINTS_WITH_MOUNT)
    assert not np.allclose(dgn_mount[:, 2], 0.0), "wall_mount harus memecah bidang"


def test_model_titik_tak_dikenal_ditolak():
    with pytest.raises(ValueError, match='tak dikenal'):
        hook_model_points(points=('ngawur',))


# ── 2. Transform camera → base_link → map ────────────────────────────────────

def test_rot_base_cam_permutasi_sumbu():
    """cam +z(depan)→base +x, cam +x(kanan)→base +y, cam +y(bawah)→base +z."""
    R = rot_base_cam()
    assert np.allclose(R @ np.array([0, 0, 1.]), [1, 0, 0])   # depan
    assert np.allclose(R @ np.array([1., 0, 0]), [0, 1, 0])   # kanan
    assert np.allclose(R @ np.array([0, 1., 0]), [0, 0, 1])   # bawah


def test_rot_base_cam_pitch_positif_menunduk():
    """mount_pitch_deg POSITIF = kamera MENUNDUK → arah pandang punya komponen +z (bawah)."""
    v = rot_base_cam({'mount_pitch_deg': 30.0}) @ np.array([0, 0, 1.])
    assert v[2] > 0.4, f"pitch positif harus menunduk (z FRD ke bawah), dapat {v}"
    assert v[0] > 0.8, "masih dominan menghadap depan"


def test_rot_map_base_heading_sejajar_sumbu_x():
    R = rot_map_base(0, 0, 47.0, 47.0)
    assert np.allclose(R @ np.array([1., 0, 0]), [1, 0, 0], atol=1e-9)


def test_rot_map_base_tanda_sumbu_y():
    """Map right-handed z-ATAS → +y map ada di bearing (α−90°), BUKAN (α+90°).
    Gotcha tanda ini yang paling gampang menaruh ROV di sisi kolam berseberangan."""
    kiri = rot_map_base(0, 0, 47.0 - 90.0, 47.0) @ np.array([1., 0, 0])
    kanan = rot_map_base(0, 0, 47.0 + 90.0, 47.0) @ np.array([1., 0, 0])
    assert np.allclose(kiri, [0, 1, 0], atol=1e-9)
    assert np.allclose(kanan, [0, -1, 0], atol=1e-9)


def test_rot_map_base_ortonormal_dan_tangan_kanan():
    for hdg in (0, 33, 91, 180, 271, 359):
        R = rot_map_base(4.0, -3.0, hdg, 17.0)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-9)
        assert abs(np.linalg.det(R) - 1.0) < 1e-9      # rotasi murni, bukan pencerminan


def test_pose_map_base_geometri_ujung_ke_ujung(tmp_path):
    """Rantai penuh dgn angka yang bisa dihitung tangan: hook di tengah dinding y=0,
    ROV menghadap dinding itu (heading 90 = wall_heading A) dari jarak 0,8 m → ROV
    harus berada 0,8 m ke arah +y dari hook (yaitu DI DALAM kolam), x sama."""
    hmap = _map(tmp_path, x_axis=0.0,
                hooks={'A': {'x': 2.2, 'y': 0.0, 'z': 0.385, 'wall_heading_deg': 90}})
    # hook lurus di depan kamera, 0,8 m — width_px yang menghasilkan z=0.8:
    # z = fx*d/w → w = 864*0.025/0.8 = 27.0 px
    det = _det(center=(640, 360), width_px=864 * 0.025 / 0.8)
    res = localize_hook(det, _calib(), hook_map=hmap, vehicle_state=_vehicle(depth=0.4))
    assert res['status'] == 'ok', res['reason']
    p = res['pose_map_base']
    assert abs(p['x'] - 2.2) < 0.02, p
    assert abs(p['y'] - 0.8) < 0.02, p          # 0,8 m DI DALAM kolam dari dinding y=0
    assert 0.0 <= p['y'] <= 2.2, "ROV di luar kolam — kemungkinan tanda sumbu y terbalik"
    assert abs(p['z'] - (0.8 - 0.4)) < 1e-9, "z wajib dari depth, bukan dari vision"


def test_pose_map_z_selalu_dari_depth(tmp_path):
    """Vision boleh salah tinggi; z map tetap mengikuti sensor depth."""
    hmap = _map(tmp_path)
    for depth in (0.10, 0.40, 0.75):
        res = localize_hook(_det(center=(640, 120)), _calib(), hook_map=hmap,
                            vehicle_state=_vehicle(depth=depth))
        assert res['status'] == 'ok', res['reason']
        assert abs(res['pose_map_base']['z'] - (0.8 - depth)) < 1e-9


# ── 3. Proyeksi sintetis: pose diketahui → project → estimasi ulang ──────────

@pytest.mark.parametrize('tvec', [
    (0.00, 0.00, 0.60),
    (0.05, -0.03, 0.85),
    (-0.08, 0.06, 1.20),
])
def test_synthetic_projection_memulihkan_pose(tvec):
    pytest.importorskip("cv2")
    rvec = (0.25, 0.35, 0.05)                     # miring — hindari degenerate
    img = _project(rvec, tvec)
    res = localize_hook(_det(), _calib(), keypoints=img)
    assert res['status'] == 'relative_only', res['reason']
    rc = res['relative_pose_camera']
    err = np.linalg.norm(np.array([rc['x'], rc['y'], rc['z']]) - np.array(tvec))
    assert err < 0.02, f"error posisi PnP {err * 1000:.1f} mm"
    assert res['reprojection_error_px'] < 1.0
    assert rc['mode'] == 'pnp'


def test_synthetic_projection_lapor_reprojection_error():
    """Keypoint diganggu → reprojection error naik & akhirnya ditolak gate."""
    pytest.importorskip("cv2")
    img = _project((0.25, 0.35, 0.05), (0.0, 0.0, 0.8))
    bersih = localize_hook(_det(), _calib(), keypoints=img)
    rusak = img.copy()
    rusak[0] += 40.0                              # satu keypoint meleset jauh
    kotor = localize_hook(_det(), _calib(), keypoints=rusak)
    assert bersih['reprojection_error_px'] < 1.0
    assert kotor['status'] == 'rejected'
    assert 'reprojection' in kotor['reason']


def test_pnp_dipakai_hanya_bila_keypoint_ada():
    """Tanpa keypoint → jalur constrained 2.5D, dan itu dilaporkan jujur."""
    res = localize_hook(_det(), _calib())
    assert res['relative_pose_camera']['mode'] == '2.5d'
    assert res['reprojection_error_px'] is None


def test_mode_2p5d_jarak_dari_lebar_pipa():
    """z = fx · d_pipa / width_px — proxy, tapi harus monoton & benar secara angka."""
    dekat = localize_hook(_det(width_px=50.0), _calib())
    jauh = localize_hook(_det(width_px=10.0), _calib())
    assert dekat['relative_pose_camera']['z'] < jauh['relative_pose_camera']['z']
    assert abs(dekat['relative_pose_camera']['z'] - 864 * 0.025 / 50.0) < 1e-6


def test_mode_2p5d_covariance_yaw_besar():
    """Mode 2.5D TIDAK mengamati yaw dari vision → variansnya harus jujur-besar."""
    res = localize_hook(_det(), _calib())
    cov = np.array(res['covariance']).reshape(6, 6)
    assert abs(math.sqrt(cov[5, 5]) - math.radians(DEFAULT_GATES['sigma_yaw_2p5d_deg'])) < 1e-9
    assert cov[5, 5] > cov[0, 0], "yaw harus jauh lebih tak pasti drpd posisi"


def test_covariance_bentuk_6x6_row_major():
    res = localize_hook(_det(), _calib())
    assert len(res['covariance']) == 36            # konvensi PoseWithCovarianceStamped
    cov = np.array(res['covariance']).reshape(6, 6)
    assert np.allclose(cov, cov.T)
    assert (np.diag(cov) > 0).all()


# ── 4. Ambiguitas / geometri degenerate ──────────────────────────────────────

def _frac_ambiguous(tilt_deg, noise_px, n=40):
    """Fraksi realisasi derau yang dilaporkan ambigu. Ambiguitas planar bersifat
    STATISTIK (relatif thd derau), jadi diuji sbg populasi — bukan satu draw yang
    kebetulan menyala/tak menyala."""
    hit = 0
    for seed in range(n):
        img = _project((math.radians(tilt_deg), 0.0, 0.0), (0.0, 0.0, 0.9),
                       noise_px=noise_px, seed=seed)
        if localize_hook(_det(), _calib(), keypoints=img)['status'] == 'ambiguous':
            hit += 1
    return hit / float(n)


def _frac_usable(tilt_deg, noise_px, n=40):
    """Fraksi realisasi yang menghasilkan pose PnP TERPAKAI (bukan ambiguous /
    ditolak gate reprojection). Inilah angka yang menentukan apakah PnP layak
    jadi jalur runtime."""
    ok = 0
    for seed in range(n):
        img = _project((math.radians(tilt_deg), 0.0, 0.0), (0.0, 0.0, 0.9),
                       noise_px=noise_px, seed=seed)
        if localize_hook(_det(), _calib(), keypoints=img)['status'] not in (
                'ambiguous', 'rejected'):
            ok += 1
    return ok / float(n)


def _keypoints_ambiguous(tilt_deg=0.0, noise_px=2.0, n=40):
    """Keypoint yang PASTI memicu gate ambiguitas (seed pertama yang menyala)."""
    for seed in range(n):
        img = _project((math.radians(tilt_deg), 0.0, 0.0), (0.0, 0.0, 0.9),
                       noise_px=noise_px, seed=seed)
        if localize_hook(_det(), _calib(), keypoints=img)['status'] == 'ambiguous':
            return img
    pytest.fail(f"tak ada realisasi ambigu pada tilt {tilt_deg}° / derau {noise_px} px")


@pytest.mark.parametrize('tilt', [0.0, 5.0])
def test_hampir_frontal_dgn_derau_sering_ambiguous(tilt):
    """Hook sebidang dilihat NYARIS TEGAK LURUS + derau piksel realistis → dua
    solusi PnP sama-sama menjelaskan citra. Yang benar adalah MELAPORKANNYA,
    bukan memilih salah satu diam-diam.

    ⚠ Geometri inilah yang justru terjadi saat docking (ROV mendekat tegak lurus
    ke dinding), jadi di air jalur PnP memang sering berakhir `ambiguous` — itu
    sebabnya constrained 2.5D adalah jalur runtime yang realistis, bukan PnP."""
    pytest.importorskip("cv2")
    assert _frac_ambiguous(tilt, 2.0) > 0.70


def test_miring_cukup_derau_kecil_tidak_ambiguous():
    """Sisi lain gate yang sama: sudut pandang cukup miring + derau sub-piksel →
    kedua solusi TERPISAH jelas. Gate tak boleh menolak pose yang sehat."""
    pytest.importorskip("cv2")
    assert _frac_ambiguous(35.0, 0.5) < 0.10


@pytest.mark.parametrize('tilt', [0.0, 35.0])
def test_derau_besar_pnp_nyaris_tak_terpakai(tilt):
    """TEMUAN YANG MENENTUKAN PILIHAN ARSITEKTUR: pada derau ~4 px (seordo riak
    air keruh), PnP menghasilkan pose terpakai di <25% frame — sisanya ambigu
    atau ditolak gate reprojection — dan itu berlaku BAIK pada sudut miring 35°
    maupun frontal. Jadi pose 6-DOF dari satu pipa monokuler memang tak bisa
    diandalkan di kolam; constrained 2.5D-lah jalur runtime yang jujur.

    Gate-nya sendiri bekerja benar: yang gagal disaring, bukan diloloskan."""
    pytest.importorskip("cv2")
    assert _frac_usable(tilt, 4.0) < 0.25


def test_derau_kecil_miring_pnp_terpakai_penuh():
    """Kontras dgn test di atas: pada kondisi bersih (derau sub-piksel + sudut
    miring), PnP terpakai di SEMUA frame — gate tak asal menolak."""
    pytest.importorskip("cv2")
    assert _frac_usable(35.0, 0.5) == 1.0


def test_data_tanpa_derau_tak_pernah_ambiguous():
    """Dokumentasi perilaku: ambiguitas planar itu RELATIF TERHADAP DERAU. Tanpa
    derau solvePnP selalu bisa memisahkan kedua solusi, jadi gate memang tak
    menyala — itu benar, bukan gate yang bocor."""
    pytest.importorskip("cv2")
    res = localize_hook(_det(), _calib(),
                        keypoints=_project((0.0, 0.0, 0.0), (0.0, 0.0, 0.9)))
    assert res['status'] == 'relative_only'


def test_ambiguous_tetap_memberi_pose_relatif():
    """Ambigu untuk pose MAP, tapi jarak/bearing relatif tetap berguna utk servo."""
    pytest.importorskip("cv2")
    res = localize_hook(_det(), _calib(), keypoints=_keypoints_ambiguous())
    assert res['status'] == 'ambiguous'
    assert 'ambiguitas planar' in res['reason']
    assert res['relative_pose_camera'] is not None
    assert res['relative_pose_base'] is not None
    assert res['valid'] is False


def test_ambiguous_tak_pernah_terbitkan_pose_map(tmp_path):
    pytest.importorskip("cv2")
    res = localize_hook(_det(), _calib(), hook_map=_map(tmp_path),
                        vehicle_state=_vehicle(), keypoints=_keypoints_ambiguous())
    assert res['status'] == 'ambiguous'
    assert res['pose_map_base'] is None


# ── 5. Contour palsu / se-frame ──────────────────────────────────────────────

def test_contour_se_frame_ditolak():
    """Regresi HOOK-02 (uji kolam 22 Agu): air keruh bikin Canny menghasilkan satu
    contour sebesar frame dgn confidence 1,00. Localization tak boleh ikut termakan."""
    res = localize_hook(_det(area=0.9 * 1280 * 720, confidence=1.0), _calib())
    assert res['status'] == 'rejected'
    assert '% frame' in res['reason']


def test_confidence_rendah_ditolak():
    res = localize_hook(_det(confidence=0.10), _calib())
    assert res['status'] == 'rejected' and 'confidence' in res['reason']


@pytest.mark.parametrize('width_px', [0.5, 1000.0])
def test_lebar_pipa_di_luar_rentang_ditolak(width_px):
    res = localize_hook(_det(width_px=width_px), _calib())
    assert res['status'] == 'rejected' and 'lebar pipa' in res['reason']


def test_jarak_tak_masuk_akal_ditolak():
    """width_px sangat kecil → z raksasa (jauh di luar kolam) → tolak."""
    res = localize_hook(_det(width_px=2.5), _calib())
    assert res['status'] == 'rejected' and 'jarak' in res['reason']


def test_tanpa_kalibrasi_ditolak():
    """IBVS boleh tanpa kalibrasi; localization tidak — dan itu harus eksplisit."""
    assert localize_hook(_det(), None)['status'] == 'rejected'
    assert localize_hook(_det(), {'K': None})['status'] == 'rejected'


def test_deteksi_kosong_tak_melempar():
    for bad in (None, {}, ):
        res = localize_hook(bad, _calib())
        assert res['status'] == 'rejected' and res['valid'] is False


# ── 6. Dropout, hold, expiration ─────────────────────────────────────────────

def test_tracker_inisialisasi_lalu_memfilter():
    trk = HookTracker(alpha=0.5, beta=0.1)
    p0, ok, _ = trk.update([1.0, 1.0, 0.4], now=0.0)
    assert ok and np.allclose(p0, [1, 1, 0.4])
    p1, ok, _ = trk.update([1.2, 1.0, 0.4], now=0.1)
    assert ok and 1.0 < p1[0] < 1.2, "alpha-beta harus meredam, bukan menyalin"


def test_tracker_menolak_lompatan():
    trk = HookTracker(max_jump_m=0.5)
    trk.update([0.0, 0.0, 0.0], now=0.0)
    p, ok, alasan = trk.update([9.0, 0.0, 0.0], now=0.1)
    assert not ok and p is None and 'lompatan' in alasan


def test_tracker_lompatan_ditolak_tak_merusak_state():
    """Sekali lompatan diterima, ia jadi state baru & gate berikutnya salah acuan."""
    trk = HookTracker(max_jump_m=0.5)
    trk.update([0.0, 0.0, 0.0], now=0.0)
    trk.update([9.0, 0.0, 0.0], now=0.1)                 # ditolak
    p, ok, _ = trk.update([0.05, 0.0, 0.0], now=0.2)     # dekat state ASLI
    assert ok and abs(p[0]) < 0.2, "state tercemar oleh pengukuran yang ditolak"


def test_tracker_hold_lalu_expire():
    trk = HookTracker(hold_s=0.5, expire_s=2.0)
    trk.update([1.0, 1.0, 0.4], now=100.0)
    assert trk.hold(now=100.3) is not None, "dropout singkat → boleh hold"
    assert trk.hold(now=100.9) is None, "lewat hold_s → jangan pakai pose basi"


def test_tracker_expire_reset_penuh():
    """Lewat expire_s: state di-reset, pengukuran berikutnya jadi inisialisasi baru —
    BUKAN dilanjutkan dari posisi lama (yang sudah tak berarti)."""
    trk = HookTracker(hold_s=0.5, expire_s=2.0, max_jump_m=0.5)
    trk.update([0.0, 0.0, 0.0], now=0.0)
    p, ok, alasan = trk.update([9.0, 0.0, 0.0], now=10.0)   # jauh, tapi setelah expire
    assert ok and 'diinisialisasi' in alasan
    assert np.allclose(p, [9.0, 0.0, 0.0])


def test_tracker_reset_manual():
    trk = HookTracker()
    trk.update([1.0, 2.0, 3.0], now=0.0)
    assert trk.initialized
    trk.reset()
    assert not trk.initialized and trk.hold(now=0.0) is None


def test_gate_kontinuitas_menolak_pose_melompat(tmp_path):
    """Tracker terpasang di localize_hook → lompatan posisi map ditolak."""
    hmap = _map(tmp_path)
    trk = HookTracker(max_jump_m=0.2)
    v = _vehicle()
    r1 = localize_hook(_det(width_px=27.0), _calib(), hook_map=hmap,
                       vehicle_state=v, tracker=trk)
    assert r1['status'] == 'ok', r1['reason']
    r2 = localize_hook(_det(width_px=60.0), _calib(), hook_map=hmap,
                       vehicle_state=v, tracker=trk)     # tiba-tiba jauh lebih dekat
    assert r2['status'] == 'rejected' and 'lompatan' in r2['reason']


# ── 7. Map A/B/C/D + trial_assignment ────────────────────────────────────────

def _empat_hook():
    """Empat hook, satu per dinding, heading konsisten dgn α=0 (lihat catatan
    geometri di atas). Huruf SENGAJA tak punya pola terhadap sisi — identitas
    A/B/C/D diacak panitia, kode tak boleh mengandaikan apa pun."""
    return {
        'A': {'x': 2.2, 'y': 0.0, 'z': 0.385, 'wall_heading_deg': 90},
        'B': {'x': 2.2, 'y': 2.2, 'z': 0.385, 'wall_heading_deg': 270},
        'C': {'x': 4.4, 'y': 1.1, 'z': 0.385, 'wall_heading_deg': 0},
        'D': {'x': 0.0, 'y': 1.1, 'z': 0.385, 'wall_heading_deg': 180},
    }


@pytest.mark.parametrize('hid,hdg', [('A', 90), ('B', 270), ('C', 0), ('D', 180)])
def test_identitas_hook_dari_heading(tmp_path, hid, hdg):
    hmap = _map(tmp_path, hooks=_empat_hook())
    res = localize_hook(_det(width_px=27.0), _calib(), hook_map=hmap,
                        vehicle_state=_vehicle(heading=hdg))
    assert res['status'] == 'ok', res['reason']
    assert res['hook_id'] == hid


def test_identitas_hook_tak_hardcode_ke_sisi(tmp_path):
    """Huruf ditukar di config → hasil ikut bertukar. Kalau ada sisi yang
    di-hardcode ke huruf, test ini yang jatuh."""
    ditukar = _empat_hook()
    ditukar['A']['wall_heading_deg'], ditukar['C']['wall_heading_deg'] = 0, 270
    hmap = _map(tmp_path, hooks=ditukar)
    res = localize_hook(_det(width_px=27.0), _calib(), hook_map=hmap,
                        vehicle_state=_vehicle(heading=0))
    assert res['hook_id'] == 'A'


def test_trial_assignment_payload_ke_hook(tmp_path):
    hmap = _map(tmp_path, hooks=_empat_hook(),
                trial_assignment={'payload_id_to_hook_id': {'7': 'C'}})
    res = localize_hook(_det(width_px=27.0, payload_id=7), _calib(), hook_map=hmap,
                        vehicle_state=_vehicle(heading=0))
    assert res['hook_id'] == 'C' and 'trial_assignment' in res['reason']


def test_trial_assignment_hook_tak_dikenal_ditolak(tmp_path):
    with pytest.raises(ValueError, match='trial_assignment'):
        _map(tmp_path, hooks=_empat_hook(),
             trial_assignment={'payload_id_to_hook_id': {'7': 'Z'}})


def test_hook_id_eksplisit_menang(tmp_path):
    hmap = _map(tmp_path, hooks=_empat_hook())
    res = localize_hook(_det(width_px=27.0, hook_id='B'), _calib(), hook_map=hmap,
                        vehicle_state=_vehicle(heading=270))
    assert res['hook_id'] == 'B' and 'eksplisit' in res['reason']


def test_heading_tak_cocok_hook_mana_pun_jadi_ambiguous(tmp_path):
    hmap = _map(tmp_path, hooks=_empat_hook())
    res = localize_hook(_det(width_px=27.0), _calib(), hook_map=hmap,
                        vehicle_state=_vehicle(heading=45))    # persis di antara C & B
    assert res['status'] == 'ambiguous'
    assert res['pose_map_base'] is None


def test_lebih_dari_satu_hook_cocok_jadi_ambiguous(tmp_path):
    """Dua hook berdempetan heading → jangan menebak, laporkan ambigu."""
    hmap = _map(tmp_path, hooks={
        'A': {'x': 2.2, 'y': 0.0, 'z': 0.385, 'wall_heading_deg': 270},
        'B': {'x': 1.0, 'y': 0.0, 'z': 0.385, 'wall_heading_deg': 275},
    })
    res = localize_hook(_det(width_px=27.0), _calib(), hook_map=hmap,
                        vehicle_state=_vehicle(heading=272))
    assert res['status'] == 'ambiguous' and 'ambigu' in res['reason']


def test_heading_menyimpang_dari_wall_heading_jadi_ambiguous(tmp_path):
    """hook_id sudah pasti (eksplisit) tapi ROV menghadap arah lain → yang terlihat
    kemungkinan besar benda lain, bukan hook itu."""
    hmap = _map(tmp_path, hooks=_empat_hook())     # hook A menghadap heading 90
    res = localize_hook(_det(width_px=27.0, hook_id='A'), _calib(), hook_map=hmap,
                        vehicle_state=_vehicle(heading=200))
    assert res['status'] == 'ambiguous' and 'menyimpang' in res['reason']


# ── 8. Resolusi kamera tak cocok kalibrasi ───────────────────────────────────

def test_resolusi_kalibrasi_tak_cocok_ditolak():
    """Kelas bug 22 Agu (qr_detect._verify_calib_size): K dari resolusi lain bikin
    z ~ fx·W/w_px meleset berlipat DIAM-DIAM. Jalur dual-camera _run_hook_camera
    tak lewat guard itu, jadi diulang di modul ini."""
    res = localize_hook(_det(), _calib(size=(1920, 1080)))
    assert res['status'] == 'rejected'
    assert '1920x1080' in res['reason'] and '1280x720' in res['reason']


def test_resolusi_cocok_diterima():
    assert localize_hook(_det(), _calib(size=(1280, 720)))['status'] != 'rejected'


def test_kalibrasi_tanpa_image_size_tak_diblokir():
    """File kalibrasi lama tanpa `image_size` tetap jalan (gate ini tak bisa menilai)."""
    assert localize_hook(_det(), _calib(size=None))['status'] != 'rejected'


# ── 9. Validasi config map ───────────────────────────────────────────────────

def _tulis(tmp_path, doc, nama='m.json'):
    p = tmp_path / nama
    p.write_text(json.dumps(doc), encoding='utf-8')
    return str(p)


def test_config_menolak_koordinat_hook_null(tmp_path):
    doc = {'pool': {'length_x': 4.4, 'width_y': 2.2, 'depth': 0.8},
           'map': {'x_axis_heading_deg': 0},
           'hooks': {'A': {'x': None, 'y': 0.0, 'z': 0.385, 'wall_heading_deg': 270}}}
    with pytest.raises(ValueError, match="hook 'A' masih null"):
        load_hook_map(_tulis(tmp_path, doc))


def test_config_menolak_x_axis_heading_null(tmp_path):
    doc = {'pool': {'length_x': 4.4, 'width_y': 2.2, 'depth': 0.8},
           'map': {'x_axis_heading_deg': None}, 'hooks': {}}
    with pytest.raises(ValueError, match='x_axis_heading_deg'):
        load_hook_map(_tulis(tmp_path, doc))


def test_config_menolak_pool_null(tmp_path):
    doc = {'pool': {'length_x': None, 'width_y': 2.2, 'depth': 0.8},
           'map': {'x_axis_heading_deg': 0}, 'hooks': {}}
    with pytest.raises(ValueError, match='pool.length_x'):
        load_hook_map(_tulis(tmp_path, doc))


def test_config_menolak_hook_di_luar_kolam(tmp_path):
    doc = {'pool': {'length_x': 4.4, 'width_y': 2.2, 'depth': 0.8},
           'map': {'x_axis_heading_deg': 0},
           'hooks': {'A': {'x': 99.0, 'y': 0.0, 'z': 0.385, 'wall_heading_deg': 270}}}
    with pytest.raises(ValueError, match='DI LUAR jejak kolam'):
        load_hook_map(_tulis(tmp_path, doc))


def test_config_menolak_z_hook_di_luar_kedalaman(tmp_path):
    doc = {'pool': {'length_x': 4.4, 'width_y': 2.2, 'depth': 0.8},
           'map': {'x_axis_heading_deg': 0},
           'hooks': {'A': {'x': 2.2, 'y': 0.0, 'z': 5.0, 'wall_heading_deg': 270}}}
    with pytest.raises(ValueError, match='di luar rentang'):
        load_hook_map(_tulis(tmp_path, doc))


def test_config_contoh_repo_menolak_dirinya_sendiri():
    """config/hook_map.example.yaml SENGAJA berisi null — memaksa ukur di venue.
    Kalau suatu saat file itu bisa dimuat apa adanya, ada yang salah."""
    pytest.importorskip("yaml")
    path = os.path.join(_AUTONOMY, 'config', 'hook_map.example.yaml')
    assert os.path.isfile(path)
    with pytest.raises(ValueError):
        load_hook_map(path)


def test_config_geometri_default_kolam_latihan(tmp_path):
    """Nilai z contoh = 0.385 (kolam latihan terukur), BUKAN 0.45 (spek arena lomba)."""
    pytest.importorskip("yaml")
    path = os.path.join(_AUTONOMY, 'config', 'hook_map.example.yaml')
    with open(path, encoding='utf-8') as f:
        import yaml
        doc = yaml.safe_load(f)
    assert doc['pool'] == {'length_x': 4.4, 'width_y': 2.2, 'depth': 0.8}
    for h in doc['hooks'].values():
        assert h['z'] == 0.385


def test_override_bertumpuk_tak_bentrok(tmp_path):
    """AKAR BUG: `dict(default, **dari_map, **dari_argumen)` melempar TypeError
    "got multiple values for keyword argument" begitu kuncinya beririsan — dan
    beririsan itu NORMAL, karena load_hook_map() sudah menggabungkan default ke
    camera_to_base/gates/hook_geometry. Persis kombinasi yang dipakai FSM."""
    hmap = _map(tmp_path)
    res = localize_hook(_det(width_px=27.0), _calib(), hook_map=hmap,
                        vehicle_state=_vehicle(),
                        camera_to_base=hmap['camera_to_base'],   # kunci sama persis
                        hook_model=hmap['hook_geometry'],
                        gates=hmap['gates'])
    assert res['status'] == 'ok', res['reason']


def test_override_argumen_menang_atas_map(tmp_path):
    """Presedens tumpukan: argumen eksplisit > nilai dari map > default."""
    hmap = _map(tmp_path)
    longgar = localize_hook(_det(confidence=0.20), _calib(), hook_map=hmap,
                            vehicle_state=_vehicle(), gates={'min_confidence': 0.10})
    ketat = localize_hook(_det(confidence=0.20), _calib(), hook_map=hmap,
                          vehicle_state=_vehicle(), gates={'min_confidence': 0.50})
    assert longgar['status'] != 'rejected'
    assert ketat['status'] == 'rejected' and 'confidence' in ketat['reason']


def test_tanpa_map_hanya_pose_relatif():
    res = localize_hook(_det(), _calib(), vehicle_state=_vehicle())
    assert res['status'] == 'relative_only'
    assert res['pose_map_base'] is None
    assert res['relative_pose_base'] is not None


def test_tanpa_heading_atau_depth_hanya_pose_relatif(tmp_path):
    hmap = _map(tmp_path)
    for hilang in ('heading', 'depth'):
        v = _vehicle()
        v[hilang] = None
        res = localize_hook(_det(width_px=27.0, hook_id='A'), _calib(),
                            hook_map=hmap, vehicle_state=v)
        assert res['status'] == 'relative_only', f"{hilang}: {res['reason']}"
        assert res['pose_map_base'] is None


# ── 10. Regresi: skema hasil & jalur M5/QR tak terganggu ─────────────────────

_FIELD_WAJIB = ('valid', 'status', 'hook_id', 'relative_pose_camera',
                'relative_pose_base', 'pose_map_base', 'confidence',
                'reprojection_error_px', 'covariance', 'timestamp', 'reason')


@pytest.mark.parametrize('kw', [
    {},                                              # relative_only
    {'confidence': 0.0},                             # rejected
])
def test_skema_hasil_selalu_lengkap(kw):
    res = localize_hook(_det(**kw), _calib())
    for f in _FIELD_WAJIB:
        assert f in res, f"field '{f}' hilang dari hasil"
    assert isinstance(res['reason'], str) and res['reason']
    assert res['valid'] is (res['status'] == 'ok')


def test_valid_hanya_saat_status_ok(tmp_path):
    hmap = _map(tmp_path)
    ok = localize_hook(_det(width_px=27.0), _calib(), hook_map=hmap, vehicle_state=_vehicle())
    assert ok['status'] == 'ok' and ok['valid'] is True and ok['pose_map_base'] is not None


def test_timestamp_ikut_deteksi_bukan_waktu_panggil():
    """Semua timestamp vision = wall time deteksi, supaya usia frame bisa dihitung."""
    res = localize_hook(_det(timestamp=1234.5), _calib())
    assert res['timestamp'] == 1234.5


def test_menerima_output_detect_hook_asli():
    """Regresi antar-modul: keluaran detect_hook() yang SEBENARNYA harus bisa
    langsung dimasukkan ke localize_hook() tanpa adaptor."""
    cv2 = pytest.importorskip("cv2")
    from vision.hook_detect import detect_hook
    from tests.test_hook_detect import _make_hook

    frame = _make_hook(cv2, np)
    det = detect_hook(frame, focal_px=500.0)
    assert det is not None
    K = np.array([[500.0, 0, 320.0], [0, 500.0, 240.0], [0, 0, 1.0]])
    res = localize_hook(det, _calib(size=(640, 480), K=K), frame=frame)
    assert res['status'] in ('ok', 'relative_only', 'ambiguous', 'rejected')
    for f in _FIELD_WAJIB:
        assert f in res


def test_ekstraksi_keypoint_dari_frame_sintetis():
    """Ekstraktor keypoint tinggal di modul ini (hook_detect.py nol diff).
    ⚠ Lolos pada hook sintetis TIDAK berarti lolos pada frame bawah air — itu Tahap 2."""
    cv2 = pytest.importorskip("cv2")
    from vision.hook_detect import detect_hook
    from vision.hook_localization import keypoints_from_hook
    from tests.test_hook_detect import _make_hook

    frame = _make_hook(cv2, np)
    det = detect_hook(frame)
    kp = keypoints_from_hook(det, frame)
    assert kp is not None and kp.shape == (len(PNP_POINTS), 2)
    x, y, w, h = det['bbox']
    assert (kp[:, 0] >= x - 20).all() and (kp[:, 0] <= x + w + 20).all()
    assert kp[0][1] < kp[2][1] and kp[1][1] < kp[2][1]   # kaki di ATAS dasar-U


def test_ekstraksi_keypoint_frame_kosong_none():
    from vision.hook_localization import keypoints_from_hook
    assert keypoints_from_hook(_det(), None) is None
    assert keypoints_from_hook(None, None) is None


def test_hook_detect_tak_diubah():
    """Kontrak eksplisit: hook_detect.py nol diff. detect_hook() TIDAK boleh
    tiba-tiba mengeluarkan field baru yang dipakai jalur HANG/DOCK."""
    cv2 = pytest.importorskip("cv2")
    from vision.hook_detect import detect_hook
    from tests.test_hook_detect import _make_hook
    det = detect_hook(_make_hook(cv2, np), focal_px=500.0)
    assert set(det) == {'type', 'center', 'bbox', 'area', 'width_px', 'confidence',
                        'method', 'frame_w', 'frame_h', 'pose', 'timestamp'}


def test_fsm_pinjam_kalibrasi_dari_vision_pipeline(tmp_path):
    """Mode satu-kamera: tak ada file kalibrasi terpisah → K dipinjam dari
    VisionPipeline. K/dist itu ndarray, jadi `a or b` di jalur ini akan melempar
    ValueError 'truth value is ambiguous' yang TERTELAN except dan mematikan
    fitur diam-diam. Test ini yang menjaganya."""
    import fsm.mission5 as m5

    class _Vision:                      # pengganti VisionPipeline seadanya
        _K, _dist = K_WALL, np.zeros(5)
        _K_hook = _dist_hook = None

        def latest_hook(self, max_age=1.0):
            return _det(width_px=27.0)

    hmap = tmp_path / 'm.json'
    hmap.write_text(json.dumps({
        'pool': {'length_x': 4.4, 'width_y': 2.2, 'depth': 0.8},
        'map': {'x_axis_heading_deg': 0},
        'hooks': {'A': {'x': 2.2, 'y': 0.0, 'z': 0.385, 'wall_heading_deg': 90}},
    }), encoding='utf-8')

    fsm = m5.Mission5FSM.__new__(m5.Mission5FSM)
    m5.Mission5FSM.__init__(fsm, cmd=None, telem=None, vision=_Vision(),
                            hook_map_file=str(hmap))
    assert fsm.hook_loc is not None, "map valid harus mengaktifkan fitur"
    fsm._hook_loc_t = 0.0
    fsm._hook_localize({'depth': 0.4, 'heading': 90.0, 'roll': 0.0, 'pitch': 0.0})
    hl = fsm.telemetry_out['hook_loc']
    assert hl is not None, "K dari VisionPipeline tak terpakai (jalur except tertelan?)"
    assert hl['status'] == 'ok', hl['reason']
    assert hl['pose_map'] is not None and hl['sigma_xy_m'] > 0


def test_fsm_tanpa_hook_map_tidak_melokalisasi():
    """Fitur benar-benar OPSIONAL: FSM default tak punya localizer & telemetry
    hook_loc tetap None — jalur M5 QR tak terpengaruh sama sekali."""
    import fsm.mission5 as m5
    fsm = m5.Mission5FSM.__new__(m5.Mission5FSM)     # tanpa socket/thread
    m5.Mission5FSM.__init__(fsm, cmd=None, telem=None, vision=None)
    assert fsm.hook_loc is None
    assert fsm.telemetry_out['hook_loc'] is None
    # field telemetri lama harus tetap ada (GUI membacanya)
    for f in ('state', 'active_cam', 'distance_z', 'offset_x', 'offset_y',
              'bbox', 'confidence', 'qr_data', 'qr_wall', 'time_left'):
        assert f in fsm.telemetry_out
