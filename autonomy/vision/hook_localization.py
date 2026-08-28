"""
vision/hook_localization.py — Lokalisasi ROV memakai hook pipa sbg LANDMARK visual
===================================================================================
Modul **OPSIONAL**. Mengubah satu deteksi hook (`vision/hook_detect.detect_hook`)
menjadi:

  1. pose hook relatif KAMERA  (selalu, bila gate lolos)
  2. pose hook relatif BASE_LINK (komposisi mount kamera)
  3. pose ROV pada frame MAP arena — **hanya** bila identitas hook diketahui,
     koordinatnya ada di map, kalibrasi cocok, dan SEMUA quality gate lolos.

Kalau salah satu syarat itu tak terpenuhi, modul mengembalikan status
`relative_only` / `ambiguous` / `rejected` — **tidak pernah** memaksakan pose map.
Wahana ini tak punya sensor posisi lateral apa pun (tak ada GPS/DVL/optical-flow),
jadi pose map palsu jauh lebih berbahaya daripada tak ada pose sama sekali.

╔═ BATASAN YANG DIPEGANG ═══════════════════════════════════════════════════════╗
║ • `vision/hook_detect.py` TIDAK disentuh — ekstraktor keypoint ada DI SINI.    ║
║ • Jalur M5 QR tak berubah; QR payload tetap target primer M5_DOCK.             ║
║ • Modul ini tak pernah mengirim command; ArduSub tetap pegang kendali wahana.  ║
║ • Tanpa `--hook-map` di fsm/mission5.py, modul ini tak pernah dipanggil.       ║
╚═══════════════════════════════════════════════════════════════════════════════╝

KONVENSI FRAME (dipakai konsisten di seluruh modul)
---------------------------------------------------
  camera    : OpenCV — +x KANAN, +y BAWAH, +z KE DEPAN (keluar lensa).
              Sama persis `qr_detect._estimate_pose_pts()` & `detect_hook()['pose']`.
  base_link : body ROV FRD — +x DEPAN (surge), +y KANAN (sway), +z BAWAH (heave).
              Konvensi MAVLink/ArduSub.
  map       : arena — +x arah PANJANG kolam, +y arah LEBAR, +z KE ATAS, dasar = 0.
              Bearing kompas sumbu +x map = `map.x_axis_heading_deg` (α) di config.
              ⚠ GOTCHA saat mengisi koordinat hook: frame ini right-handed dgn z
              KE ATAS, jadi +y map ada di bearing **α − 90°** — 90° BERLAWANAN
              jarum jam dari +x (ke KIRI bila berdiri menghadap +x). Bearing kompas
              justru bertambah searah jarum jam. Salah tanda di sini menaruh ROV di
              sisi kolam yang berseberangan, tanpa error apa pun.
  hook      : landmark — origin di **PUSAT LENGKUNG-U** (bukan center bbox!),
              +x kanan sepanjang bukaan U, +y ke atas sepanjang kaki, +z keluar dinding.

`z` ROV pada frame map SELALU diambil dari sensor depth (`pool.depth − depth`),
tak pernah dari vision: depth adalah kanal terpercaya, vision cuma memberi
bearing + jarak.

DUA MODE ESTIMASI
-----------------
  PnP (6-DOF)      : butuh >=4 korespondensi 3D-2D (keypoint). Titik siluet hook
                     semuanya SEBIDANG, jadi solvePnP planar punya ambiguitas
                     dua-solusi — bila dua solusi sama-sama bagus, hasilnya
                     `ambiguous`, BUKAN pose yang dipaksakan.
  constrained 2.5D : jalur default runtime. Orientasi dari IMU, z dari depth,
                     vision cuma memberi bearing (center) + jarak proxy
                     (`z = fx·d_pipa / width_px`). Covariance orientasi sengaja
                     BESAR — modul ini TIDAK mengklaim akurasi 6-DOF.

KENAPA 2.5D YANG JADI JALUR RUNTIME, BUKAN PnP
-----------------------------------------------
Ambiguitas planar bersifat RELATIF TERHADAP DERAU: pada data sintetis tanpa derau
solvePnP selalu bisa memisahkan kedua solusi, tapi begitu ada derau piksel ia tak
bisa. Diukur di tests/test_hook_localization.py (proyeksi sintetis, 40 realisasi
derau per titik, hook pada jarak 0,9 m):

    derau     sudut pandang     pose PnP TERPAKAI
    0,5 px    miring 35°        100 %
    2   px    frontal            12 %
    4   px    frontal / 35°     < 25 %   (sisanya ambigu / ditolak reprojection)

Dua konsekuensinya:
  1. Docking mendekati dinding TEGAK LURUS — persis geometri near-frontal yang
     paling ambigu. Jadi justru saat pose paling dibutuhkan, PnP paling tak bisa.
  2. Riak/kekeruhan kolam memberi derau centroid jauh di atas 2 px (lihat catatan
     riak di qr_detect._decode_stacked), sehingga di air PnP akan gagal/ambigu di
     mayoritas frame.
Gate-nya sendiri bekerja benar — kasus-kasus itu DISARING, bukan diloloskan diam-
diam. Tapi artinya PnP layak dianggap bonus, bukan tulang punggung.

Semua timestamp memakai wall/steady time (`time.time()` / `time.monotonic()`),
tak pernah sim-time.

Pemakaian:
    from vision.hook_localization import load_hook_map, load_calibration, \
                                         localize_hook, HookTracker
    hmap  = load_hook_map('config/hook_map.local.yaml')
    calib = load_calibration('vision/calibration/wall.npz')
    trk   = HookTracker()
    res   = localize_hook(det, calib, hmap['hook_geometry'], hmap,
                          telem, hmap['camera_to_base'], tracker=trk)

Self-check tanpa hardware:
    PYTHONPATH= python3 -m vision.hook_localization --self-check
"""

import logging
import math
import os
import time
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

try:
    import cv2
    CV2_OK = True
except ImportError:                                     # pragma: no cover
    CV2_OK = False
    log.warning("[hookloc] opencv-python tak tersedia — jalur PnP dinonaktifkan")


# ── Model geometri hook (semua meter; override lewat `hook_geometry:` di config) ──
# Origin frame hook = PUSAT LENGKUNG-U (titik referensi yang jelas & bisa dihitung
# solvePnP langsung dari tvec). Center bounding-box SENGAJA tidak dipakai sbg pose
# final: ia bergeser mengikuti bagian hook mana yang kebetulan terlihat.
HOOK_MODEL = {
    'pipe_diameter_m':      0.025,   # pipa PVC ¾" = 25 mm
    'leg_length_m':         0.090,   # panjang kaki DI ATAS pusat-U
    'u_radius_m':           0.035,   # radius garis-tengah lengkung U
    'wall_mount_offset_m':  0.060,   # jarak titik sambungan dinding di BELAKANG bidang U
}

# Titik model yang benar-benar OBSERVABLE pada siluet hook dari kamera.
# CATATAN PENTING: kelimanya SEBIDANG (z=0) — itu bukan kelalaian model, memang
# begitu bentuk fisiknya. Konsekuensinya PnP planar punya ambiguitas dua-solusi
# yang harus dideteksi, bukan disembunyikan (lihat _solve_pnp).
PNP_POINTS = ('leg_left_tip', 'leg_right_tip', 'u_bottom', 'u_left', 'u_right')

# Titik sambungan ke dinding — MEMECAH kesebidangan sehingga pose jadi unik.
# Opsional & default MATI: di kolam titik ini biasanya terhalang dinding/keruh.
PNP_POINTS_WITH_MOUNT = PNP_POINTS + ('wall_mount',)


# ── Quality gate (override lewat `gates:` di config hook map) ─────────────────
DEFAULT_GATES = {
    'min_confidence':    0.35,   # confidence detect_hook minimum
    'max_area_frac':     0.25,   # tolak contour yang mencakup >25% frame (kelas bug HOOK-02)
    'min_width_px':      2.0,    # lebar pipa di bawah ini = derau, bukan pipa
    'max_width_px':      400.0,  # di atas ini bukan pipa 25 mm, ada yang salah
    'min_range_m':       0.15,   # lebih dekat dari ini kamera tak fokus / hook tak utuh
    'max_range_m':       5.0,    # lebih jauh dari diagonal kolam = tak masuk akal
    'max_reproj_px':     3.0,    # RMSE reprojection maksimum utk menerima pose PnP
    'ambiguity_ratio':   0.60,   # err_terbaik/err_kedua di ATAS ini → dua solusi sama bagus → ambigu
    'wall_tol_deg':      35.0,   # |heading − wall_heading_deg hook| maksimum
    'max_jump_m':        0.80,   # lompatan posisi map maksimum antar frame (kontinuitas)
    'sigma_width_px':    2.0,    # ketidakpastian pengukuran lebar pipa (utk σ jarak)
    'sigma_center_px':   3.0,    # ketidakpastian centroid (riak/glare menggeser beberapa px)
    'sigma_depth_m':     0.02,   # ketidakpastian sensor depth
    'sigma_att_deg':     2.0,    # ketidakpastian roll/pitch IMU
    'sigma_yaw_2p5d_deg': 30.0,  # yaw TIDAK diamati vision di mode 2.5D — sengaja besar
    'sigma_yaw_pnp_deg':  5.0,   # yaw dari PnP (masih perlu validasi air)
}

# Default mount kamera: menghadap DEPAN, tanpa offset. Kamera WALL ROV ini
# menghadap dinding/payload (lihat catatan orientasi di control/visual_servo.py).
DEFAULT_CAMERA_TO_BASE = {
    'mount_roll_deg':  0.0,
    'mount_pitch_deg': 0.0,   # POSITIF = kamera menunduk (tilt ke BAWAH)
    'mount_yaw_deg':   0.0,
    'offset_m':        [0.0, 0.0, 0.0],   # posisi kamera di base_link (FRD, meter)
}

_HOOK_IDS = ('A', 'B', 'C', 'D')
_STATUS_OK, _STATUS_REL, _STATUS_AMB, _STATUS_REJ = 'ok', 'relative_only', 'ambiguous', 'rejected'


# ══ Rotasi & frame ════════════════════════════════════════════════════════════

def _rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def _rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)


def _rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


# Permutasi tetap camera(OpenCV) → base_link(FRD), tanpa rotasi mount:
#   cam +z (depan) → base +x ; cam +x (kanan) → base +y ; cam +y (bawah) → base +z
R_BASE_CAM = np.array([[0., 0., 1.],
                       [1., 0., 0.],
                       [0., 1., 0.]])

# NED(x=Utara, y=Timur, z=Bawah) → ENU(x=Timur, y=Utara, z=Atas)
_M_ENU_NED = np.array([[0., 1., 0.],
                       [1., 0., 0.],
                       [0., 0., -1.]])


def rot_base_cam(camera_to_base=None) -> np.ndarray:
    """Rotasi camera → base_link, termasuk rotasi mount dari config.

    `mount_pitch_deg` POSITIF = kamera MENUNDUK. Di FRD sumbu +y menunjuk ke kanan
    dan +z ke bawah, jadi menunduk = rotasi NEGATIF terhadap +y (lihat tanda minus)."""
    c = _merge(DEFAULT_CAMERA_TO_BASE, camera_to_base)
    r_mount = (_rot_z(math.radians(float(c['mount_yaw_deg'])))
               @ _rot_y(-math.radians(float(c['mount_pitch_deg'])))
               @ _rot_x(math.radians(float(c['mount_roll_deg']))))
    return r_mount @ R_BASE_CAM


def rot_map_base(roll_deg, pitch_deg, heading_deg, x_axis_heading_deg) -> np.ndarray:
    """Rotasi base_link(FRD) → map, dari attitude wahana + bearing sumbu +x map.

    heading = kompas (0 = Utara, SEARAH jarum jam). Rantainya:
        base --(3-2-1 aerospace)--> NED --(tukar sumbu)--> ENU --(putar α)--> map
    Sumbu map dinyatakan di ENU:  x̂=(sinα, cosα, 0), ŷ=ẑ×x̂=(−cosα, sinα, 0), ẑ=(0,0,1).
    """
    r_ned_base = (_rot_z(math.radians(float(heading_deg)))
                  @ _rot_y(math.radians(float(pitch_deg)))
                  @ _rot_x(math.radians(float(roll_deg))))
    a = math.radians(float(x_axis_heading_deg))
    sa, ca = math.sin(a), math.cos(a)
    r_map_enu = np.array([[sa,  ca, 0.],
                          [-ca, sa, 0.],
                          [0.,  0., 1.]])
    return r_map_enu @ _M_ENU_NED @ r_ned_base


def hook_model_points(model=None, points=PNP_POINTS):
    """Titik 3D model hook pada frame hook (origin = PUSAT LENGKUNG-U).

    +x kanan sepanjang bukaan U, +y ke atas sepanjang kaki, +z keluar dari dinding.
    Kembalikan (names, ndarray (N,3) float32) — urutannya WAJIB sama dengan urutan
    keypoint citra yang dipasangkan ke solvePnP."""
    m = _merge(HOOK_MODEL, model)
    r = float(m['u_radius_m'])
    leg = float(m['leg_length_m'])
    back = float(m['wall_mount_offset_m'])
    table = {
        'u_center':      (0.0,  0.0, 0.0),      # = origin, titik referensi pose hook
        'leg_left_tip':  (-r,   leg, 0.0),
        'leg_right_tip': (r,    leg, 0.0),
        'u_bottom':      (0.0,  -r,  0.0),
        'u_left':        (-r,   0.0, 0.0),
        'u_right':       (r,    0.0, 0.0),
        'wall_mount':    (-r,   leg, -back),    # SATU-SATUNYA titik di luar bidang z=0
    }
    missing = [p for p in points if p not in table]
    if missing:
        raise ValueError(f"titik model hook tak dikenal: {missing}")
    return tuple(points), np.array([table[p] for p in points], dtype=np.float32)


# ══ Config map arena ══════════════════════════════════════════════════════════

def load_hook_map(path: str) -> dict:
    """Muat + VALIDASI map arena (.yaml/.yml/.json) → dict siap pakai.

    Reuse `config.loader.read_file()` (sudah menangani yaml/json + pesan error).
    Menolak keras (ValueError) bila ada koordinat hook yang masih `null` atau di
    luar jejak kolam — pose map tak boleh terbit dari angka yang belum diukur."""
    from config.loader import read_file
    raw = read_file(path)

    pool = raw.get('pool') or {}
    for key in ('length_x', 'width_y', 'depth'):
        if pool.get(key) is None:
            raise ValueError(f"hook map {path}: pool.{key} wajib diisi (dapat null/kosong)")
    lx, wy, dz = float(pool['length_x']), float(pool['width_y']), float(pool['depth'])
    if lx <= 0 or wy <= 0 or dz <= 0:
        raise ValueError(f"hook map {path}: pool.length_x/width_y/depth harus > 0")

    mp = raw.get('map') or {}
    if mp.get('x_axis_heading_deg') is None:
        raise ValueError(
            f"hook map {path}: map.x_axis_heading_deg wajib diisi — bearing kompas sumbu "
            "+x map (arah PANJANG kolam). Tanpa ini pose map tak punya acuan arah.")

    hooks_raw = raw.get('hooks') or {}
    hooks = {}
    for hid, h in hooks_raw.items():
        hid = str(hid).upper()
        h = h or {}
        vals = {k: h.get(k) for k in ('x', 'y', 'z', 'wall_heading_deg')}
        if any(v is None for v in vals.values()):
            kosong = sorted(k for k, v in vals.items() if v is None)
            raise ValueError(
                f"hook map {path}: hook '{hid}' masih null pada {kosong} — ukur/MARK di venue "
                "dulu, atau hapus hook itu dari config bila memang tak dipakai.")
        x, y, z = float(vals['x']), float(vals['y']), float(vals['z'])
        if not (0.0 <= x <= lx and 0.0 <= y <= wy):
            raise ValueError(
                f"hook map {path}: hook '{hid}' di ({x}, {y}) DI LUAR jejak kolam "
                f"{lx} x {wy} m")
        if not (0.0 <= z <= dz):
            raise ValueError(
                f"hook map {path}: hook '{hid}' z={z} di luar rentang 0..{dz} m "
                "(z diukur dari DASAR ke atas)")
        hooks[hid] = {'x': x, 'y': y, 'z': z,
                      'wall_heading_deg': float(vals['wall_heading_deg'])}

    # Identitas A/B/C/D bisa DIACAK panitia — pemetaan payload→hook murni dari config,
    # tak ada sisi yang di-hardcode ke huruf mana pun.
    assign = ((raw.get('trial_assignment') or {}).get('payload_id_to_hook_id') or {})
    assign = {str(k): str(v).upper() for k, v in assign.items()}
    tak_dikenal = sorted(set(assign.values()) - set(hooks))
    if tak_dikenal:
        raise ValueError(f"hook map {path}: trial_assignment menunjuk hook {tak_dikenal} "
                         f"yang tak ada di blok hooks: {sorted(hooks)}")

    return {
        'pool': {'length_x': lx, 'width_y': wy, 'depth': dz},
        'map': {'x_axis_heading_deg': float(mp['x_axis_heading_deg'])},
        'hooks': hooks,
        'trial_assignment': assign,
        'hook_geometry': _merge(HOOK_MODEL, raw.get('hook_geometry')),
        'camera_to_base': _merge(DEFAULT_CAMERA_TO_BASE, raw.get('camera_to_base')),
        'gates': _merge(DEFAULT_GATES, raw.get('gates')),
        'source': os.path.abspath(path),
    }


def load_calibration(path: str) -> dict:
    """Muat .npz kalibrasi → {'K','dist','image_size'}. Format sama `tools/calibrate_camera.py`
    dan yang dibaca `qr_detect.VisionPipeline` — tak ada format baru."""
    d = np.load(path)
    size = tuple(int(v) for v in d['image_size']) if 'image_size' in d else None
    return {'K': np.asarray(d['K'], dtype=float),
            'dist': np.asarray(d['dist'], dtype=float),
            'image_size': size, 'name': path}


# ══ Filter temporal ═══════════════════════════════════════════════════════════

class HookTracker:
    """Alpha-beta filter ringan pada posisi ROV di frame map + hold/expire dropout.

    Kenapa alpha-beta dan bukan Kalman penuh: yang dibutuhkan cuma meredam derau
    centroid & menolak lompatan; tak ada model proses yang benar-benar diketahui
    untuk dibayar dengan matriks kovarians penuh.

    hold_s   — dropout lebih pendek dari ini: pakai state terakhir (pola sama
               HOOK_LOCK_GRACE_T di fsm/mission5.py).
    expire_s — lebih lama dari ini: RESET total. Pose basi tanpa batas waktu
               adalah cara paling halus untuk menabrak dinding.
    """

    def __init__(self, alpha=0.5, beta=0.1, hold_s=0.5, expire_s=2.0, max_jump_m=0.8):
        self.alpha, self.beta = float(alpha), float(beta)
        self.hold_s, self.expire_s = float(hold_s), float(expire_s)
        self.max_jump_m = float(max_jump_m)
        self.reset()

    def reset(self):
        self._x = None            # posisi terfilter (3,)
        self._v = np.zeros(3)     # kecepatan (m/s)
        self._t = None            # monotonic time update terakhir

    @property
    def initialized(self) -> bool:
        return self._x is not None

    def update(self, pos, now=None):
        """Masukkan satu pengukuran posisi map (3,). → (pos_terfilter|None, ok, alasan)."""
        pos = np.asarray(pos, dtype=float).reshape(3)
        now = time.monotonic() if now is None else float(now)

        if self._x is not None and self._t is not None and (now - self._t) > self.expire_s:
            self.reset()                      # terlalu lama hilang — jangan lanjut dari state basi
        if self._x is None:
            self._x, self._v, self._t = pos.copy(), np.zeros(3), now
            return self._x.copy(), True, 'tracker diinisialisasi'

        dt = max(1e-3, now - self._t)
        x_pred = self._x + self._v * dt
        resid = pos - x_pred
        jump = float(np.linalg.norm(resid))
        if jump > self.max_jump_m:
            # JANGAN update — sekali lompatan diterima, ia jadi state baru dan
            # gate kontinuitas berikutnya mengukur dari tempat yang salah.
            return None, False, (f'lompatan {jump:.2f} m > max_jump_m '
                                 f'{self.max_jump_m:.2f} m — deteksi ditolak')
        self._x = x_pred + self.alpha * resid
        self._v = self._v + (self.beta / dt) * resid
        self._t = now
        return self._x.copy(), True, 'ok'

    def hold(self, now=None):
        """Posisi terakhir bila dropout masih di dalam hold_s, else None."""
        if self._x is None or self._t is None:
            return None
        now = time.monotonic() if now is None else float(now)
        age = now - self._t
        if age > self.hold_s:
            return None
        return self._x.copy()

    def age(self, now=None):
        if self._t is None:
            return None
        return (time.monotonic() if now is None else float(now)) - self._t


# ══ Ekstraksi keypoint (hook_detect.py TIDAK disentuh) ════════════════════════

def keypoints_from_hook(detection, frame=None, model=None, margin_px=12):
    """Ekstrak keypoint siluet hook dari FRAME, urut sesuai `PNP_POINTS`.

    Sengaja tinggal DI SINI dan bukan di `hook_detect.py`: file itu dipakai jalur
    HANG/DOCK yang sudah tervalidasi kolam (HOOK-02), dan localization tak boleh
    menaruh risiko regresi di sana.

    Kembalikan ndarray (5,2) float32 atau None bila bentuknya tak cocok model.

    ⚠ BELUM DIVALIDASI pada frame bawah air sungguhan — itu isi Tahap 2 (replay
    recorded frame). Sampai terbukti, jalur runtime yang realistis adalah 2.5D."""
    if not CV2_OK or frame is None or not detection:
        return None
    bbox = detection.get('bbox')
    if not bbox:
        return None
    from vision.hook_detect import _to_gray_clahe

    fh, fw = frame.shape[0], frame.shape[1]
    x, y, w, h = (int(v) for v in bbox)
    x0, y0 = max(0, x - margin_px), max(0, y - margin_px)
    x1, y1 = min(fw, x + w + margin_px), min(fh, y + h + margin_px)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    crop = frame[y0:y1, x0:x1]

    edges = cv2.Canny(_to_gray_clahe(crop), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    pts = max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
    if len(pts) < 5:
        return None

    px, py = pts[:, 0], pts[:, 1]
    x_mid = 0.5 * (px.min() + px.max())
    left, right = pts[px < x_mid], pts[px >= x_mid]
    if len(left) < 2 or len(right) < 2:
        return None

    kp = np.array([
        left[np.argmin(left[:, 1])],      # leg_left_tip  — ujung kaki kiri (paling ATAS di sisi kiri)
        right[np.argmin(right[:, 1])],    # leg_right_tip — ujung kaki kanan
        pts[np.argmax(py)],               # u_bottom      — dasar lengkung U
        pts[np.argmin(px)],               # u_left        — sisi terluar kiri lengkung
        pts[np.argmax(px)],               # u_right       — sisi terluar kanan lengkung
    ], dtype=np.float32)
    kp += np.array([x0, y0], dtype=np.float32)      # crop → koordinat frame penuh

    # Sanity bentuk: kaki harus DI ATAS dasar-U, dan lebar bukaan tak boleh nol.
    if not (kp[0][1] < kp[2][1] and kp[1][1] < kp[2][1]):
        return None
    if abs(kp[4][0] - kp[3][0]) < 4.0:
        return None
    return kp


# ══ PnP ═══════════════════════════════════════════════════════════════════════

def _reproj_rmse(obj, img, rvec, tvec, K, dist):
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    d = proj.reshape(-1, 2) - img.reshape(-1, 2)
    return float(np.sqrt((d ** 2).sum(axis=1).mean()))


def _solve_pnp(obj, img, K, dist, gates):
    """solvePnP + deteksi ambiguitas planar. → (ok, R, t, rmse, ambigu, alasan).

    Titik siluet hook sebidang (lihat PNP_POINTS): IPPE mengembalikan DUA solusi
    yang sama-sama menjelaskan citra. Kalau error keduanya berdekatan, pose memang
    tak bisa ditentukan dari satu frame — itu dilaporkan sbg `ambiguous`, bukan
    ditutupi dengan mengambil yang kebetulan lebih kecil."""
    planar = bool(np.allclose(obj[:, 2], obj[0, 2]))
    if planar:
        flags = getattr(cv2, 'SOLVEPNP_IPPE', cv2.SOLVEPNP_ITERATIVE)
    else:
        flags = getattr(cv2, 'SOLVEPNP_SQPNP', cv2.SOLVEPNP_ITERATIVE)
    try:
        n, rvecs, tvecs, _ = cv2.solvePnPGeneric(obj, img, K, dist, flags=flags)
    except cv2.error as e:
        return False, None, None, None, False, f'solvePnP gagal: {e}'
    if not n:
        return False, None, None, None, False, 'solvePnP tak menghasilkan solusi'

    errs = [_reproj_rmse(obj, img, rvecs[i], tvecs[i], K, dist) for i in range(n)]
    order = int(np.argsort(errs)[0])
    rvec, tvec, rmse = rvecs[order], tvecs[order], errs[order]

    ambigu = False
    alasan = 'ok'
    if n > 1:
        kedua = sorted(errs)[1]
        rasio = rmse / kedua if kedua > 1e-9 else 1.0
        if rasio > float(gates['ambiguity_ratio']):
            ambigu = True
            alasan = (f'ambiguitas planar: dua solusi PnP sama bagus '
                      f'(rmse {rmse:.2f} vs {kedua:.2f} px, rasio {rasio:.2f} > '
                      f"{gates['ambiguity_ratio']:.2f})")

    refine = getattr(cv2, 'solvePnPRefineVVS', None)
    if refine is not None and not ambigu:
        try:
            rvec, tvec = refine(obj, img, K, dist, rvec, tvec)
            rmse = _reproj_rmse(obj, img, rvec, tvec, K, dist)
        except cv2.error:
            pass                                  # refinement opsional — pose kasar tetap dipakai

    R, _ = cv2.Rodrigues(rvec)
    return True, R, np.asarray(tvec, dtype=float).ravel(), rmse, ambigu, alasan


# ══ Hasil ═════════════════════════════════════════════════════════════════════

def _blank(status, reason, timestamp=None, **kw):
    """Skema hasil TETAP — semua field selalu ada, apa pun statusnya."""
    out = {
        'valid': status == _STATUS_OK,
        'status': status,
        'hook_id': 'unknown',
        'relative_pose_camera': None,
        'relative_pose_base': None,
        'pose_map_base': None,
        'confidence': 0.0,
        'reprojection_error_px': None,
        'covariance': None,
        'timestamp': float(timestamp) if timestamp is not None else time.time(),
        'reason': reason,
    }
    out.update(kw)
    return out


def _cov6(sx, sy, sz, sroll, spitch, syaw):
    """Covariance 6x6 diagonal, row-major 36 elemen (konvensi
    geometry_msgs/PoseWithCovarianceStamped) supaya drop-in kalau nanti
    dipublikasikan dari ROS 2 (~/ros2_ws). Sudut dalam RADIAN."""
    c = np.zeros((6, 6))
    for i, s in enumerate((sx, sy, sz, sroll, spitch, syaw)):
        c[i, i] = float(s) ** 2
    return [float(v) for v in c.reshape(-1)]


def _resolve_hook_id(detection, hook_map, vehicle_state, gates):
    """Tentukan hook mana yang sedang dilihat. → (hook_id|None, dict|None, alasan).

    Urutan: `hook_id` eksplisit di deteksi → `trial_assignment` dari payload id →
    kecocokan heading dengan `wall_heading_deg`. Identitas A/B/C/D DIACAK panitia,
    jadi tak ada sisi yang di-hardcode ke huruf mana pun."""
    hooks = hook_map.get('hooks') or {}
    if not hooks:
        return None, None, 'map tak punya hook satu pun'

    hid = detection.get('hook_id') or detection.get('wall')
    if hid and str(hid).upper() in hooks:
        hid = str(hid).upper()
        return hid, hooks[hid], f'hook_id eksplisit dari deteksi: {hid}'

    pid = detection.get('payload_id')
    if pid is not None:
        hid = (hook_map.get('trial_assignment') or {}).get(str(pid))
        if hid in hooks:
            return hid, hooks[hid], f'hook_id dari trial_assignment payload {pid} → {hid}'

    hdg = vehicle_state.get('heading')
    if hdg is None:
        return None, None, 'hook_id tak diketahui & heading tak tersedia utk mencocokkan'
    tol = float(gates['wall_tol_deg'])
    cocok = [(h, v) for h, v in hooks.items()
             if abs(_wrap180(float(hdg) - v['wall_heading_deg'])) <= tol]
    if len(cocok) == 1:
        h, v = cocok[0]
        return h, v, f'hook_id dari kecocokan heading ({hdg:.0f}° ≈ {h})'
    if not cocok:
        return None, None, (f'tak ada hook yang cocok heading {float(hdg):.0f}° '
                            f'dalam ±{tol:.0f}°')
    return None, None, (f'{len(cocok)} hook sama-sama cocok heading {float(hdg):.0f}° '
                        f"({', '.join(sorted(h for h, _ in cocok))}) — ambigu")


def _wrap180(a):
    return (float(a) + 180.0) % 360.0 - 180.0


def _merge(*dicts):
    """Gabung beberapa dict, yang belakangan menang (pola sama --config bertumpuk).

    BUKAN `dict(a, **b, **c)`: dua unpacking `**` dengan kunci yang sama melempar
    TypeError "got multiple values for keyword argument". Overlap di sini bukan
    kasus langka melainkan NORMAL — `load_hook_map()` sudah menggabungkan default
    ke `camera_to_base`/`gates`/`hook_geometry`, jadi begitu pemanggil juga mengoper
    salah satunya, semua kuncinya bertabrakan."""
    out = {}
    for d in dicts:
        if d:
            out.update(d)
    return out


# ══ API utama ═════════════════════════════════════════════════════════════════

def localize_hook(detection,
                  camera_calibration,
                  hook_model=None,
                  hook_map=None,
                  vehicle_state=None,
                  camera_to_base=None,
                  tracker=None,
                  keypoints=None,
                  frame=None,
                  gates=None) -> dict:
    """Satu deteksi hook → pose relatif, dan (bila layak) pose ROV di frame map.

    Parameters
    ----------
    detection          : dict keluaran `vision.hook_detect.detect_hook()`.
    camera_calibration : {'K','dist','image_size'} — lihat `load_calibration()`.
    hook_model         : override `HOOK_MODEL` (dict), atau None.
    hook_map           : hasil `load_hook_map()`, atau None → maksimal `relative_only`.
    vehicle_state      : telemetri {'depth','heading','roll','pitch'} (sama persis
                         key `TelemetryReceiver._data` di fsm/mission5.py).
    camera_to_base     : override `DEFAULT_CAMERA_TO_BASE`.
    tracker            : `HookTracker` opsional (filter + gate kontinuitas).
    keypoints          : (N,2) korespondensi citra utk PnP. None → coba diekstrak
                         dari `frame`; tetap None → jalur constrained 2.5D.
    frame              : frame BGR asal deteksi (hanya utk ekstraksi keypoint).
    gates              : override `DEFAULT_GATES`.

    Returns
    -------
    dict — skema tetap (lihat `_blank`). `pose_map_base` HANYA terisi bila
    status == 'ok'. Fungsi ini tak pernah melempar exception untuk input jelek;
    kegagalan dilaporkan lewat `status` + `reason`.
    """
    g = _merge(DEFAULT_GATES, (hook_map or {}).get('gates'), gates)
    vehicle_state = vehicle_state or {}
    ts = (detection or {}).get('timestamp', time.time())

    if not detection:
        return _blank(_STATUS_REJ, 'tak ada deteksi', ts)
    if camera_calibration is None or camera_calibration.get('K') is None:
        return _blank(_STATUS_REJ, 'kalibrasi kamera tak tersedia — modul ini butuh K '
                                   '(IBVS boleh tanpa kalibrasi, localization tidak)', ts)

    K = np.asarray(camera_calibration['K'], dtype=float)
    dist = np.asarray(camera_calibration.get('dist')
                      if camera_calibration.get('dist') is not None
                      else np.zeros(5), dtype=float)
    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx_k, cy_k = float(K[0, 2]), float(K[1, 2])
    conf = float(detection.get('confidence') or 0.0)
    fw = int(detection.get('frame_w') or 0)
    fh = int(detection.get('frame_h') or 0)

    # ── Gate 1: resolusi kalibrasi vs frame nyata ────────────────────────────
    # Kelas bug 22 Agu 2026 (lihat qr_detect._verify_calib_size): K dari resolusi
    # lain membuat z ~ fx·W/w_px meleset berlipat, DIAM-DIAM tanpa error. Jalur
    # dual-camera `_run_hook_camera` TIDAK lewat guard itu, jadi diulang di sini.
    size = camera_calibration.get('image_size')
    if size and fw and fh and tuple(size) != (fw, fh):
        return _blank(_STATUS_REJ,
                      f"kalibrasi {camera_calibration.get('name', '?')} dibuat pada "
                      f"{size[0]}x{size[1]}, frame nyata {fw}x{fh} — K/dist tak valid", ts)

    # ── Gate 2: kualitas deteksi ─────────────────────────────────────────────
    if conf < float(g['min_confidence']):
        return _blank(_STATUS_REJ, f"confidence {conf:.2f} < min {g['min_confidence']:.2f}",
                      ts, confidence=conf)
    area = float(detection.get('area') or 0.0)
    if fw and fh:
        frac = area / float(fw * fh)
        if frac > float(g['max_area_frac']):
            return _blank(_STATUS_REJ,
                          f"contour mencakup {100 * frac:.1f}% frame > "
                          f"{100 * float(g['max_area_frac']):.0f}% — bukan hook "
                          "(kelas bug HOOK-02, air keruh)", ts, confidence=conf)
    width_px = float(detection.get('width_px') or 0.0)
    if not (float(g['min_width_px']) <= width_px <= float(g['max_width_px'])):
        return _blank(_STATUS_REJ,
                      f"lebar pipa {width_px:.1f} px di luar rentang "
                      f"[{g['min_width_px']}, {g['max_width_px']}]", ts, confidence=conf)

    model = _merge(HOOK_MODEL, (hook_map or {}).get('hook_geometry'), hook_model)
    cam2base = _merge(DEFAULT_CAMERA_TO_BASE,
                      (hook_map or {}).get('camera_to_base'), camera_to_base)

    # ── Estimasi pose hook thd KAMERA ────────────────────────────────────────
    if keypoints is None and frame is not None:
        keypoints = keypoints_from_hook(detection, frame, model)

    reproj = None
    mode = '2.5d'
    R_cam_hook = None
    ambigu_pnp = False
    alasan_pnp = ''

    if keypoints is not None and CV2_OK and len(np.asarray(keypoints).reshape(-1, 2)) >= 4:
        img = np.asarray(keypoints, dtype=np.float32).reshape(-1, 2)
        _, obj = hook_model_points(model, PNP_POINTS[:len(img)])
        ok, R_cam_hook, t_cam, reproj, ambigu_pnp, alasan_pnp = _solve_pnp(obj, img, K, dist, g)
        if not ok:
            return _blank(_STATUS_REJ, alasan_pnp, ts, confidence=conf)
        if reproj is not None and reproj > float(g['max_reproj_px']):
            return _blank(_STATUS_REJ,
                          f"reprojection error {reproj:.2f} px > max "
                          f"{float(g['max_reproj_px']):.2f} px", ts,
                          confidence=conf, reprojection_error_px=reproj)
        p_cam = np.asarray(t_cam, dtype=float).reshape(3)
        mode = 'pnp'
    else:
        # Constrained 2.5D — bearing dari centroid, jarak proxy dari lebar pipa.
        # Titik referensinya CENTROID deteksi, bukan pusat-U: pergeseran itu masuk
        # ke covariance, tak dipura-purakan nol.
        z = fx * float(model['pipe_diameter_m']) / width_px
        u, v = detection.get('center') or (fw / 2.0, fh / 2.0)
        p_cam = np.array([(float(u) - cx_k) * z / fx,
                          (float(v) - cy_k) * z / fy,
                          z], dtype=float)

    rng = float(np.linalg.norm(p_cam))
    if p_cam[2] <= 0 or not (float(g['min_range_m']) <= rng <= float(g['max_range_m'])):
        return _blank(_STATUS_REJ,
                      f"jarak {rng:.2f} m di luar rentang masuk akal "
                      f"[{g['min_range_m']}, {g['max_range_m']}] m", ts,
                      confidence=conf, reprojection_error_px=reproj)

    rel_cam = {'x': float(p_cam[0]), 'y': float(p_cam[1]), 'z': float(p_cam[2]),
               'dist': rng, 'mode': mode}
    if R_cam_hook is not None:
        rel_cam['yaw_deg'] = math.degrees(math.atan2(R_cam_hook[0, 2], R_cam_hook[2, 2]))

    # ── camera → base_link ───────────────────────────────────────────────────
    R_bc = rot_base_cam(cam2base)
    t_bc = np.asarray(cam2base['offset_m'], dtype=float).reshape(3)
    p_base = R_bc @ p_cam + t_bc
    rel_base = {'x': float(p_base[0]), 'y': float(p_base[1]), 'z': float(p_base[2]),
                'dist': float(np.linalg.norm(p_base)), 'mode': mode}

    # σ jarak & lateral — dipakai baik utk covariance maupun laporan kejujuran.
    if mode == 'pnp':
        s_pos = max(0.01, float(reproj or 1.0) * rng / fx)
        s_yaw = math.radians(float(g['sigma_yaw_pnp_deg']))
    else:
        s_range = rng * float(g['sigma_width_px']) / width_px
        s_lat = rng * float(g['sigma_center_px']) / fx
        # ponytail: σ horizontal isotropik (maks dari range & lateral) — bukan
        # elips yang dirotasi ke arah pandang. Naikkan ke covariance penuh
        # kalau nanti benar-benar difusikan ke EKF.
        s_pos = max(s_range, s_lat)
        s_yaw = math.radians(float(g['sigma_yaw_2p5d_deg']))
    s_att = math.radians(float(g['sigma_att_deg']))
    s_depth = float(g['sigma_depth_m'])

    if ambigu_pnp:
        return _blank(_STATUS_AMB, alasan_pnp, ts, confidence=conf,
                      relative_pose_camera=rel_cam, relative_pose_base=rel_base,
                      reprojection_error_px=reproj,
                      covariance=_cov6(s_pos, s_pos, s_depth, s_att, s_att, s_yaw))

    common = dict(confidence=conf, relative_pose_camera=rel_cam,
                  relative_pose_base=rel_base, reprojection_error_px=reproj,
                  covariance=_cov6(s_pos, s_pos, s_depth, s_att, s_att, s_yaw))

    # ── base_link → map (butuh map lengkap + identitas hook + attitude) ──────
    if not hook_map:
        return _blank(_STATUS_REL, 'hook map tak diberikan — pose relatif saja', ts, **common)

    hid, hook, alasan_id = _resolve_hook_id(detection, hook_map, vehicle_state, g)
    if hook is None:
        return _blank(_STATUS_AMB, alasan_id, ts, **common)

    hdg = vehicle_state.get('heading')
    depth = vehicle_state.get('depth')
    if hdg is None or depth is None:
        return _blank(_STATUS_REL,
                      'heading/depth tak tersedia — pose map butuh keduanya '
                      '(orientasi dari IMU, z dari depth)', ts, hook_id=hid, **common)

    # Gate konsistensi dinding: hook yang benar HARUS berada di arah yang cocok
    # dengan heading wahana, kalau tidak yang terlihat itu benda lain.
    d_wall = _wrap180(float(hdg) - float(hook['wall_heading_deg']))
    if abs(d_wall) > float(g['wall_tol_deg']):
        return _blank(_STATUS_AMB,
                      f"heading {float(hdg):.0f}° menyimpang {abs(d_wall):.0f}° dari "
                      f"wall_heading hook {hid} ({hook['wall_heading_deg']:.0f}°) > "
                      f"tol {float(g['wall_tol_deg']):.0f}°", ts, hook_id=hid, **common)

    alpha = float(hook_map['map']['x_axis_heading_deg'])
    R_mb = rot_map_base(vehicle_state.get('roll', 0.0) or 0.0,
                        vehicle_state.get('pitch', 0.0) or 0.0,
                        float(hdg), alpha)
    p_map_hook = np.array([hook['x'], hook['y'], hook['z']], dtype=float)
    p_map_base = p_map_hook - R_mb @ p_base
    # z SELALU dari depth: sensor terpercaya, sementara vision cuma bearing+range.
    p_map_base[2] = float(hook_map['pool']['depth']) - float(depth)

    if tracker is not None:
        filt, ok, alasan_trk = tracker.update(p_map_base)
        if not ok:
            return _blank(_STATUS_REJ, alasan_trk, ts, hook_id=hid, **common)
        p_map_base = filt

    pose_map = {
        'x': float(p_map_base[0]), 'y': float(p_map_base[1]), 'z': float(p_map_base[2]),
        'roll_deg': float(vehicle_state.get('roll', 0.0) or 0.0),
        'pitch_deg': float(vehicle_state.get('pitch', 0.0) or 0.0),
        'yaw_deg': _wrap180(float(hdg) - alpha),      # yaw relatif sumbu +x map
        'mode': mode,
    }
    return _blank(_STATUS_OK, alasan_id, ts, hook_id=hid, pose_map_base=pose_map, **common)


# ══ Self-check (tanpa hardware) ═══════════════════════════════════════════════

def _self_check():
    """Bukti minimal bahwa rantai transform + PnP + gate benar-benar jalan.
    Bukan pengganti tests/test_hook_localization.py — ini jaring cepat CLI."""
    assert CV2_OK, "butuh opencv utk self-check"

    # 1. Transform: heading = bearing sumbu x map, hook lurus di depan → +x map.
    R = rot_map_base(0, 0, 47.0, 47.0)
    v = R @ np.array([1.0, 0.0, 0.0])
    assert np.allclose(v, [1, 0, 0], atol=1e-9), v
    # Map right-handed z-ATAS → +y map ada di bearing α−90° (lihat rot_map_base).
    v = rot_map_base(0, 0, 47.0 - 90.0, 47.0) @ np.array([1.0, 0.0, 0.0])
    assert np.allclose(v, [0, 1, 0], atol=1e-9), v
    v = rot_map_base(0, 0, 47.0 + 90.0, 47.0) @ np.array([1.0, 0.0, 0.0])
    assert np.allclose(v, [0, -1, 0], atol=1e-9), v

    # 2. Proyeksi sintetis: model 3D → citra → PnP → pulihkan posisi.
    K = np.array([[864.0, 0, 640.0], [0, 864.0, 360.0], [0, 0, 1.0]])
    _, obj = hook_model_points()
    rvec = np.array([[0.25], [0.35], [0.05]])          # miring, supaya tak degenerate
    tvec = np.array([[0.03], [-0.02], [0.85]])
    img, _ = cv2.projectPoints(obj, rvec, tvec, K, np.zeros(5))
    det = {'type': 'hook', 'center': tuple(img.reshape(-1, 2).mean(axis=0)),
           'bbox': (0, 0, 50, 90), 'area': 4000.0, 'width_px': 25.0,
           'confidence': 0.9, 'frame_w': 1280, 'frame_h': 720, 'timestamp': time.time()}
    calib = {'K': K, 'dist': np.zeros(5), 'image_size': (1280, 720), 'name': 'synth'}
    res = localize_hook(det, calib, keypoints=img.reshape(-1, 2))
    assert res['status'] == 'relative_only', res
    rc = res['relative_pose_camera']
    err = np.linalg.norm(np.array([rc['x'], rc['y'], rc['z']]) - tvec.ravel())
    assert err < 0.02, f"error posisi PnP {err:.4f} m"
    assert res['reprojection_error_px'] < 1.0, res['reprojection_error_px']

    # 3. Gate contour se-frame.
    bad = dict(det, area=0.9 * 1280 * 720)
    assert localize_hook(bad, calib)['status'] == 'rejected'

    # 4. Gate resolusi kalibrasi.
    salah = dict(calib, image_size=(1920, 1080))
    assert localize_hook(det, salah)['status'] == 'rejected'

    print(f"self-check OK — PnP err {1000 * err:.1f} mm, "
          f"reproj {res['reprojection_error_px']:.3f} px, semua gate menyala")


if __name__ == '__main__':                              # pragma: no cover
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    ap = argparse.ArgumentParser(description='hook localization — utilitas offline')
    ap.add_argument('--self-check', action='store_true', help='uji cepat transform+PnP+gate')
    ap.add_argument('--map', default=None, help='validasi file hook map lalu cetak ringkasannya')
    args = ap.parse_args()
    if args.map:
        m = load_hook_map(args.map)
        print(f"map OK: pool {m['pool']['length_x']}x{m['pool']['width_y']}x{m['pool']['depth']} m, "
              f"x_axis {m['map']['x_axis_heading_deg']}°, hooks={sorted(m['hooks'])}")
    if args.self_check or not args.map:
        _self_check()
