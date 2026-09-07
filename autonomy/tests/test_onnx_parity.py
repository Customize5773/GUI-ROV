"""tests/test_onnx_parity.py — OnnxHookDetector (Pi) HARUS sama dengan Ultralytics (laptop).

Kenapa modul ini ada: runtime YOLO pindah dari laptop ke Raspberry Pi 4, dan di
Pi inferensi berjalan lewat cv2.dnn atas .onnx — TANPA torch. Artinya decode
keluaran mentah YOLOv8 (layout kanal, argmax, unmap letterbox, confidence
keypoint) ditulis tangan di vision/yolo_hook.py, bukan lagi dikerjakan
Ultralytics. Keypoint 2..5 dari model pose itulah yang membidik servo docking
(_hook_skeleton/_hook_tip di fsm/mission5.py). Decode pose meleset = ROV
bergerak ke titik yang salah di kolam. Jadi paritasnya diuji, bukan diasumsikan.

YANG DIUJI: MATEMATIKA DECODE, dengan preprocessing DISAMAKAN.
Kedua backend diberi kanvas letterbox 640x640 yang sama persis, lalu keluarannya
di-unmap dengan skala/padding yang sama. Semua selisih yang tersisa murni milik
decoder — dan harus ~0.

YANG SENGAJA TIDAK DIUJI: kesamaan .pt-apa-adanya vs .onnx-apa-adanya.
Keduanya memang BEDA dan itu bukan bug: Ultralytics menjalankan .pt dengan
inferensi REKTANGULAR (frame 1280x720 -> 640x384), sedangkan graf ONNX yang
diekspor berukuran tetap 640x640. Geometri masukan beda -> deteksi beda.
Terukur 7 Sep 2026 pada fixture hook nyata: .pt rect conf=0.46 vs onnx square
conf=0.79, keypoint 1 selisih ~40 px. Square justru lebih dekat ke kondisi
TRAINING (Ultralytics melatih dengan letterbox persegi), jadi jalur Pi bukan
versi yang lebih buruk — hanya bukan angka yang identik. Konsekuensi praktis:
ambang confidence (QR_VISION_CONF, LEFT_YOLO_CONF) perlu dicek ulang di kolam
setelah pindah ke Pi, jangan diasumsikan tetap.

Butuh ultralytics + torch, jadi ini test LAPTOP (di Pi otomatis skip).
"""
import glob
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_AUTONOMY = os.path.dirname(_HERE)
_FIXTURES = os.path.join(_HERE, "fixtures", "real_hard_cases")
# Korpus KEDUA, terpisah dengan sengaja: real_hard_cases/ punya makna
# spesifik milik test QR (QR TERLIHAT tapi decode_qr GAGAL), jadi
# menaruh frame hook di sana mengubah arti hasil test itu. live_hook/
# berisi frame CAM WALL sungguhan (192.168.2.2:8080, 7 Sep 2026) dengan
# hook di dalam frame — inilah yang membuat paritas pose benar-benar
# terbandingkan, bukan ter-skip.
_LIVE_HOOK = os.path.join(_HERE, "fixtures", "live_hook")
_VISION = os.path.join(_AUTONOMY, "vision")

if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

cv2 = pytest.importorskip("cv2", reason="paritas butuh OpenCV")
pytest.importorskip("ultralytics", reason="paritas hanya jalan di laptop (butuh ultralytics)")

from vision.yolo_hook import OnnxHookDetector, YOLOHookDetector, _pack  # noqa: E402

# (stem .pt, stem .onnx, conf, jumlah keypoint) — SEMUA varian ukuran diuji,
# karena imgsz graf ONNX itu tetap dan dipilih saat export: varian 320/416
# adalah kandidat nyata untuk dipasang di Pi (lihat tools/bench_pi_yolo.py),
# jadi decode-nya harus terbukti benar juga, bukan hanya varian 640.
MODELS = [
    ("best_pose", "best_pose", 0.10, 6),        # CAM WALL — pose, 6 kp, membidik servo
    ("best_pose", "best_pose_416", 0.10, 6),
    ("best_pose", "best_pose_320", 0.10, 6),
    ("best_new", "best_new", 0.10, 0),          # CAM BOTTOM — detect-only, region QR
    ("best_new", "best_new_416", 0.10, 0),
    ("best_new", "best_new_320", 0.10, 0),
]

# Ambang: dengan preprocessing identik ini seharusnya nyaris nol. Pengukuran
# 7 Sep 2026 pada 4 fixture: bbox <=1 px, conf <=1e-4, keypoint <=0.02 px.
# Angka di bawah memberi kelonggaran ~25x terhadap variasi BLAS/versi OpenCV
# antar mesin, tapi tetap jauh lebih ketat daripada apa pun yang bisa
# menggeser keputusan FSM.
MAX_BBOX_PX = 2.0
MAX_CONF = 0.005
MAX_KP_PX = 0.5


def _fixtures():
    return (sorted(glob.glob(os.path.join(_FIXTURES, "*.png")))
            + sorted(glob.glob(os.path.join(_LIVE_HOOK, "*.png"))))


def _pair(pt_stem, onnx_stem, conf):
    pt = os.path.join(_VISION, pt_stem + ".pt")
    onnx = os.path.join(_VISION, onnx_stem + ".onnx")
    if not os.path.exists(pt):
        pytest.skip(f"{pt} tidak ada")
    if not os.path.exists(onnx):
        pytest.skip(f"{onnx} tidak ada — export dulu: "
                    f"yolo export model={pt} format=onnx imgsz=640 simplify=True")
    return YOLOHookDetector(pt, conf=conf), OnnxHookDetector(onnx, conf=conf)


def _reference(pt_detector, onnx_detector, frame):
    """Jalankan model .pt pada kanvas letterbox yang SAMA dengan jalur ONNX.

    Kanvas sudah 640x640 sehingga letterbox internal Ultralytics jadi no-op —
    inilah yang menyamakan preprocessing kedua sisi. Hasilnya di-unmap balik ke
    piksel frame asli memakai skala/padding yang sama dengan OnnxHookDetector.
    """
    canvas, scale, pad_x, pad_y = onnx_detector._letterbox(frame)
    result = pt_detector.model.predict(source=canvas, conf=pt_detector.conf,
                                       imgsz=onnx_detector.imgsz, verbose=False)[0]
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return None

    def unmap(x, y):
        return (float(x) - pad_x) / scale, (float(y) - pad_y) / scale

    index = int(boxes.conf.argmax())
    x1, y1, x2, y2 = boxes.xyxy[index].tolist()
    ux1, uy1 = unmap(x1, y1)
    ux2, uy2 = unmap(x2, y2)
    keypoints = None
    pose = getattr(result, "keypoints", None)
    if pose is not None and getattr(pose, "xy", None) is not None and len(pose.xy) > index:
        confidences = (pose.conf[index].tolist()
                       if getattr(pose, "conf", None) is not None else None)
        keypoints = []
        for kp_index, (kx, ky) in enumerate(pose.xy[index].tolist()):
            px, py = unmap(kx, ky)
            keypoints.append({
                "id": kp_index, "x": px, "y": py,
                "confidence": confidences[kp_index] if confidences else None,
            })
    # bbox dilewatkan _pack() YANG SAMA dengan jalur ONNX, bukan dihitung
    # sendiri: _pack meng-clamp kotak ke dalam frame, jadi menghitungnya manual
    # di sini membandingkan kotak ter-clamp lawan kotak mentah dan melaporkan
    # "beda 3,07 px" untuk perbedaan yang sebenarnya cuma clamping.
    h, w = frame.shape[:2]
    packed = _pack(ux1, uy1, ux2, uy2, float(boxes.conf[index]), keypoints, w, h)
    return {
        "bbox": packed["bbox"],
        "confidence": packed["confidence"],
        "keypoints": keypoints,
    }


@pytest.mark.parametrize("pt_stem,onnx_stem,conf,n_keypoints", MODELS)
def test_onnx_decode_matches_ultralytics(pt_stem, onnx_stem, conf, n_keypoints):
    """Decode ONNX tangan == decode Ultralytics, pada preprocessing identik."""
    pt_detector, onnx_detector = _pair(pt_stem, onnx_stem, conf)
    assert onnx_detector.n_keypoints == n_keypoints, (
        f"{onnx_stem}.onnx melaporkan {onnx_detector.n_keypoints} keypoint, "
        f"harusnya {n_keypoints} — ekspor ulang dari bobot yang benar")

    compared = 0
    for path in _fixtures():
        frame = cv2.imread(path)
        assert frame is not None, f"fixture tidak terbaca: {path}"
        expected = _reference(pt_detector, onnx_detector, frame)
        actual = onnx_detector.detect(frame)
        name = os.path.basename(path)

        if expected is None:
            # Tak ada kandidat di atas conf: kedua sisi harus sama-sama diam.
            assert actual is None, f"{name}: ONNX mendeteksi, Ultralytics tidak"
            continue
        assert actual is not None, f"{name}: Ultralytics mendeteksi, ONNX tidak"
        compared += 1

        for axis, want, got in zip("xywh", expected["bbox"], actual["bbox"]):
            assert abs(want - got) <= MAX_BBOX_PX, (
                f"{name}: bbox.{axis} beda {abs(want - got):.3f} px "
                f"(ultralytics={want:.2f} onnx={got:.2f})")
        assert abs(expected["confidence"] - actual["confidence"]) <= MAX_CONF, (
            f"{name}: confidence beda "
            f"{abs(expected['confidence'] - actual['confidence']):.5f}")

        if n_keypoints == 0:
            assert actual["keypoints"] is None, (
                f"{name}: model detect-only tidak boleh memancarkan keypoint — "
                f"FSM memakai keberadaan keypoint untuk membedakan hook dari region QR")
            continue

        assert actual["keypoints"] is not None, f"{name}: keypoint hilang"
        assert len(actual["keypoints"]) == n_keypoints
        for want, got in zip(expected["keypoints"], actual["keypoints"]):
            assert want["id"] == got["id"]
            delta = max(abs(want["x"] - got["x"]), abs(want["y"] - got["y"]))
            assert delta <= MAX_KP_PX, (
                f"{name}: keypoint {want['id']} meleset {delta:.3f} px "
                f"(ultralytics=({want['x']:.2f},{want['y']:.2f}) "
                f"onnx=({got['x']:.2f},{got['y']:.2f}))")
            assert abs(want["confidence"] - got["confidence"]) <= MAX_CONF, (
                f"{name}: confidence keypoint {want['id']} beda")

    if compared == 0:
        # BUKAN kegagalan decode: model ini memang tidak melihat apa pun di
        # korpus fixture. Nyata terjadi pada best_new_320 (7 Sep 2026) — QR 4 cm
        # sudah hanya ~3 px per modul di 640, dan di 320 hilang sama sekali.
        # Itu ONGKOS AKURASI dari mengecilkan imgsz, dan harus TERLIHAT saat
        # memilih model untuk Pi, bukan lewat begitu saja sebagai test hijau.
        pytest.skip(f"{onnx_stem}: tak ada deteksi pada korpus fixture — "
                    f"paritas decode tak teruji di sini (lihat "
                    f"test_full_size_models_detect_on_fixtures)")


def test_full_size_models_detect_on_fixtures():
    """Jaring pengaman anti "hijau palsu".

    Test paritas di atas boleh skip kalau sebuah varian tidak mendeteksi apa pun.
    Tanpa test ini, SEMUA varian bisa diam-diam skip dan suite tetap hijau
    padahal decode tak pernah benar-benar dibandingkan. Varian 640 adalah
    baseline dan WAJIB mendeteksi hook pada korpus fixture.
    """
    pt_detector, onnx_detector = _pair("best_pose", "best_pose", 0.10)
    hits = sum(1 for path in _fixtures()
               if _reference(pt_detector, onnx_detector, cv2.imread(path)) is not None)
    assert hits > 0, ("best_pose.onnx tidak mendeteksi apa pun pada fixture — "
                      "paritas decode tidak pernah teruji; periksa bobot/export")


@pytest.mark.parametrize("pt_stem,onnx_stem,conf,_n", MODELS)
def test_onnx_detect_returns_fsm_schema(pt_stem, onnx_stem, conf, _n):
    """Kontrak dict identik dengan jalur .pt — inilah alasan rov_agent.py dan
    fsm/mission5.py tidak perlu tahu backend mana yang dipakai."""
    _pt, onnx_detector = _pair(pt_stem, onnx_stem, conf)
    for path in _fixtures():
        detection = onnx_detector.detect(cv2.imread(path))
        if detection is None:
            continue
        assert detection["method"] == "yolov8"      # dicek _validate_hook_vision
        assert detection["width_px"] is None
        assert detection["pose"] is None
        x, y, w, h = detection["bbox"]
        assert w > 0 and h > 0
        assert 0 <= x < detection["frame_w"] and 0 <= y < detection["frame_h"]
        assert x + w <= detection["frame_w"] and y + h <= detection["frame_h"]
        assert 0.0 <= detection["confidence"] <= 1.0
        return
    pytest.skip("tak ada deteksi pada fixture")


def test_make_detector_picks_backend_by_extension():
    """Worker memanggil jalur konstruksi ini; salah pilih backend di Pi =
    ImportError torch saat runtime, bukan saat start."""
    from vision.yolo_hook import make_detector
    onnx = os.path.join(_VISION, "best_new.onnx")
    if not os.path.exists(onnx):
        pytest.skip("best_new.onnx belum diekspor")
    assert isinstance(make_detector(onnx, conf=0.5), OnnxHookDetector)
    assert isinstance(make_detector(onnx.upper(), conf=0.5), OnnxHookDetector)


# ── Paritas DECODER MURNI: graf yang sama, dua pembaca ────────────────────────
# Test di atas membandingkan .pt lawan .onnx dengan preprocessing disamakan —
# berguna, tapi masih membandingkan DUA graf berbeda, jadi setiap selisih selalu
# bisa dijelaskan sebagai "ya memang beda model". Blok di bawah menutup celah
# itu: Ultralytics disuruh memuat FILE .onnx YANG SAMA yang dibaca
# OnnxHookDetector. Bobot sama, graf sama, preprocessing sama-sama letterbox
# sendiri. Yang tersisa HANYA kode decode kita. Karena itu toleransinya nyaris
# nol, bukan "cukup dekat".
#
# Terukur 7 Sep 2026 pada frame hook LIVE (CAM WALL 192.168.2.2:8080):
# best_pose.onnx dan best_pose_320.onnx keduanya cocok 0.00 px di SEMUA
# keypoint dan 0.0000 di confidence. Decode terbukti benar — bukan diasumsikan.
DECODER_KP_PX = 0.05
DECODER_CONF = 1e-4

POSE_ONNX = ["best_pose", "best_pose_416", "best_pose_320"]


def _ultralytics_on_onnx(weights, frame, imgsz, conf):
    """Ultralytics membaca .onnx yang sama. CPU dipaksa: provider CUDA di mesin
    ini gagal dibuat lalu melempar error binding, dan itu tak ada hubungannya
    dengan yang sedang diuji."""
    from ultralytics import YOLO
    result = YOLO(weights, task="pose").predict(source=frame, imgsz=imgsz, conf=conf,
                                                device="cpu", verbose=False)[0]
    if result.boxes is None or len(result.boxes) == 0:
        return None
    index = int(result.boxes.conf.argmax())
    pose = getattr(result, "keypoints", None)
    return {
        "confidence": float(result.boxes.conf[index]),
        "keypoints": (pose.xy[index].tolist()
                      if pose is not None and getattr(pose, "xy", None) is not None
                      else None),
    }


@pytest.mark.parametrize("onnx_stem", POSE_ONNX)
def test_decoder_matches_ultralytics_on_same_onnx(onnx_stem):
    """Decode tangan kita == decode Ultralytics, pada graf yang SAMA PERSIS."""
    from vision.yolo_hook import _enhance
    weights = os.path.join(_VISION, onnx_stem + ".onnx")
    if not os.path.exists(weights):
        pytest.skip(f"{weights} belum diekspor")
    detector = OnnxHookDetector(weights, conf=0.01, enhance_underwater=True)

    compared = 0
    for path in _fixtures():
        frame = cv2.imread(path)
        assert frame is not None, path
        mine = detector.detect(frame)
        theirs = _ultralytics_on_onnx(weights, _enhance(frame), detector.imgsz, 0.01)
        name = os.path.basename(path)
        if mine is None or theirs is None:
            assert (mine is None) == (theirs is None), (
                f"{name}: satu sisi mendeteksi, sisi lain tidak "
                f"(mine={mine is not None}, ultralytics={theirs is not None})")
            continue
        compared += 1
        assert abs(mine["confidence"] - theirs["confidence"]) <= DECODER_CONF, (
            f"{name}: confidence beda {abs(mine['confidence'] - theirs['confidence']):.6f}")
        assert theirs["keypoints"] is not None, f"{name}: ultralytics tanpa keypoint"
        height, width = frame.shape[:2]
        for kp in mine["keypoints"]:
            ux, uy = theirs["keypoints"][kp["id"]]
            # Ultralytics MENG-CLAMP keypoint ke tepi gambar; kita sengaja
            # tidak (lihat catatan di vision/yolo_hook.py). Itu beda KEBIJAKAN,
            # bukan beda decode, jadi dikeluarkan dari perbandingan dengan
            # menerapkan clamp yang sama ke sisi kita — bukan dengan
            # melonggarkan toleransi, yang justru akan menyembunyikan bug asli.
            mx = min(max(kp["x"], 0.0), float(width))
            my = min(max(kp["y"], 0.0), float(height))
            delta = max(abs(mx - ux), abs(my - uy))
            assert delta <= DECODER_KP_PX, (
                f"{name}: keypoint {kp['id']} meleset {delta:.4f} px — "
                f"BUG DECODE NYATA (graf & bobot identik, jadi tak bisa "
                f"dijelaskan sebagai beda model). "
                f"mine=({kp['x']:.2f},{kp['y']:.2f}) ultra=({ux:.2f},{uy:.2f})")

    # TIDAK BOLEH lolos diam-diam: keypoint 2..5 menggerakkan servo docking ROV
    # nyata, jadi "tak ada yang dibandingkan" adalah kegagalan, bukan skip.
    assert compared > 0, (
        f"{onnx_stem}.onnx: tak satu pun frame fixture dibandingkan — paritas "
        f"decode TIDAK teruji. Tambahkan fixture yang model ini memang deteksi.")
