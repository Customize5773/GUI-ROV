"""
config/loader.py — Muat file konfigurasi tuning (YAML/JSON) utk fsm/mission5.py.

Memindahkan konstanta tuning (gain PID, target docking, depth, timing, arah servo,
WALL_HEADING) KELUAR dari kode Python, supaya tim bisa tuning parameter di kolam
cukup edit file config — TANPA menyentuh mission5.py.

Pemakaian (lihat fsm/mission5.py --config):
    python fsm/mission5.py --config config/mission5.local.yaml ...

Format didukung:
  - .yaml / .yml  (butuh `pip install pyyaml`)
  - .json         (bawaan Python, tak perlu instal apa pun)

Alur kerja: salin config/mission5.example.yaml -> config/mission5.local.yaml, ubah
nilainya sesuai hasil tuning kolam. File *.local.* diabaikan git (.gitignore) agar
nilai tuning tim tak numpuk sbg noise di riwayat commit.

Semua field OPSIONAL — kunci yang tak ada di file config tetap memakai default
bawaan mission5.py (konstanta di kode adalah fallback, bukan digantikan total).
"""
import json
import logging
import os

log = logging.getLogger(__name__)

# Peta: (path YAML/JSON bertingkat) → nama konstanta modul mission5.py.
# Tambah baris di sini bila ada konstanta baru yang perlu di-expose ke config.
_SCALAR_MAP = {
    ('depth', 'target_bottom'):   'DEPTH_TARGET_BOTTOM',
    ('depth', 'target_surface'):  'DEPTH_TARGET_SURFACE',
    ('depth', 'tolerance'):       'DEPTH_TOLERANCE',
    ('depth', 'hook_depth'):      'HOOK_DEPTH',

    ('speed', 'dive'):    'DIVE_SPEED',
    ('speed', 'ascend'):  'ASCEND_SPEED',
    ('speed', 'surge'):   'SURGE_SPEED',
    ('speed', 'yaw'):     'YAW_SPEED',

    ('timeouts', 'dive'):      'TIMEOUT_DIVE',
    ('timeouts', 'scan'):      'TIMEOUT_SCAN',
    ('timeouts', 'grab'):      'TIMEOUT_GRAB',
    ('timeouts', 'nav'):       'TIMEOUT_NAV',
    ('timeouts', 'hang'):      'TIMEOUT_HANG',
    ('timeouts', 'surface'):   'TIMEOUT_SURFACE',
    ('timeouts', 'dock'):      'TIMEOUT_DOCK',
    ('timeouts', 'redive'):    'TIMEOUT_REDIVE',
    ('timeouts', 'm5_dock'):   'TIMEOUT_M5_DOCK',
    ('timeouts', 'm5_engage'): 'TIMEOUT_M5_ENGAGE',
    ('timeouts', 'unhook'):    'TIMEOUT_UNHOOK',
    ('timeouts', 'm5_ascend'): 'TIMEOUT_M5_ASCEND',
    ('timeouts', 'fallback'):  'TIMEOUT_FALLBACK',

    ('docking', 'qr_side_m'):         'QR_SIDE_M',
    ('docking', 'servo_target_area'): 'SERVO_TARGET_AREA',
    ('docking', 'servo_target_dist'): 'SERVO_TARGET_DIST',
    ('docking', 'servo_kp_yaw'):      'SERVO_KP_YAW',
    ('docking', 'lock_grace_t'):      'M5_LOCK_GRACE_T',

    ('pid_ibvs', 'kp_sway'):  'IBVS_KP_SWAY',
    ('pid_ibvs', 'kp_surge'): 'IBVS_KP_SURGE',
    ('pid_ibvs', 'kp_vert'):  'IBVS_KP_VERT',

    ('pid_pbvs', 'kp_sway'):  'PBVS_KP_SWAY',
    ('pid_pbvs', 'kp_surge'): 'PBVS_KP_SURGE',
    ('pid_pbvs', 'kp_vert'):  'PBVS_KP_VERT',

    ('m5_mechanics', 'engage_surge'):  'M5_ENGAGE_SURGE',
    ('m5_mechanics', 'unhook_vert'):   'M5_UNHOOK_VERT',
    ('m5_mechanics', 'unhook_surge'):  'M5_UNHOOK_SURGE',
    ('m5_mechanics', 'unhook_lift_t'): 'UNHOOK_LIFT_T',
    ('m5_mechanics', 'unhook_pull_t'): 'UNHOOK_PULL_T',

    ('payload', 'mission'): 'PAYLOAD_MISSION',
    ('payload', 'type'):    'PAYLOAD_TYPE',
}

_WALLS = ('A', 'B', 'C', 'D')
_INVERT_AXES = ('sway', 'vert', 'surge', 'yaw')


def read_file(path: str) -> dict:
    """Baca file .yaml/.yml/.json → dict mentah (struktur nested apa adanya)."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File config tidak ditemukan: {path}")
    ext = os.path.splitext(path)[1].lower()
    with open(path, 'r', encoding='utf-8') as f:
        if ext in ('.yaml', '.yml'):
            try:
                import yaml
            except ImportError:
                raise RuntimeError(
                    "Butuh PyYAML utk file .yaml/.yml (`pip install pyyaml`), "
                    "atau pakai file .json sbg alternatif (tak perlu instal apa pun).")
            data = yaml.safe_load(f)
        elif ext == '.json':
            data = json.load(f)
        else:
            raise ValueError(f"Ekstensi config tak dikenal: '{ext}' (pakai .yaml/.yml/.json)")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Isi config harus berupa object/dict di root, dapat: {type(data)}")
    return data


def _get(d: dict, path: tuple):
    """Ambil nilai bertingkat dari dict via tuple path; (None, False) bila tak ada."""
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None, False
        cur = cur[key]
    return cur, True


def flatten(raw: dict) -> dict:
    """Petakan dict nested (dari YAML/JSON) → {nama_konstanta_modul: nilai}.

    Hanya kunci yang ADA di file yang disertakan — kunci lain tetap memakai default
    di kode (mission5.py). WALL_HEADING & SERVO_INVERT (dict) ditandai khusus dgn
    prefix '_' agar apply_config() tahu harus MERGE (bukan timpa penuh dict lama)."""
    out = {}
    for path, attr in _SCALAR_MAP.items():
        val, found = _get(raw, path)
        if found and val is not None:
            out[attr] = val

    wh, found = _get(raw, ('wall_heading',))
    if found and isinstance(wh, dict):
        merged = {w: int(wh[w]) for w in _WALLS if w in wh}
        if merged:
            out['_WALL_HEADING_MERGE'] = merged

    inv, found = _get(raw, ('invert',))
    if found and isinstance(inv, dict):
        merged = {f'invert_{ax}': bool(inv[ax]) for ax in _INVERT_AXES if ax in inv}
        if merged:
            out['_SERVO_INVERT_MERGE'] = merged

    return out


def load_config(path: str) -> dict:
    """Baca + ratakan file config → dict {nama_konstanta: nilai} siap diterapkan."""
    raw = read_file(path)
    cfg = flatten(raw)
    log.info("[config] %d nilai akan dioverride dari %s", len(cfg), path)
    return cfg


def apply_config(ns: dict, cfg: dict) -> list:
    """Terapkan hasil load_config() ke namespace modul (dict `globals()` mission5.py)
    — HARUS dipanggil SEBELUM CommandSender/VisionPipeline/Mission5FSM dibuat, karena
    state handler & Mission5FSM.__init__ membaca nama konstanta ini sbg global module
    saat dipanggil (late-binding Python — nilai baru otomatis terpakai).

    Kembalikan list (nama, nilai_lama, nilai_baru) utk logging/audit."""
    cfg = dict(cfg)   # jangan mutasi dict pemanggil
    wall_merge = cfg.pop('_WALL_HEADING_MERGE', None)
    invert_merge = cfg.pop('_SERVO_INVERT_MERGE', None)

    applied = []
    for attr, val in cfg.items():
        old = ns.get(attr)
        ns[attr] = val
        applied.append((attr, old, val))

    if wall_merge:
        wh = dict(ns.get('WALL_HEADING', {}))
        old = dict(wh)
        wh.update(wall_merge)
        ns['WALL_HEADING'] = wh
        applied.append(('WALL_HEADING', old, wh))

    if invert_merge:
        si = dict(ns.get('SERVO_INVERT', {}))
        old = dict(si)
        si.update(invert_merge)
        ns['SERVO_INVERT'] = si
        applied.append(('SERVO_INVERT', old, si))

    for attr, old, new in applied:
        log.info("[config] %s: %s -> %s", attr, old, new)
    return applied
