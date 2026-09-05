"""tests/test_qr_vision_worker.py — Uji rantai crop->decode worker QR laptop.

best_new.pt memberi bbox region QR, worker meng-crop ROI dari bbox itu lalu
men-decode DI DALAM crop. Yang paling berbahaya di rantai ini bukan kegagalan
decode (itu kelihatan), melainkan titik hasil yang lupa dikembalikan ke
koordinat frame ASAL: ROV tetap bergerak, hanya ke arah yang salah, tanpa satu
pun error. Karena itu tes menaruh QR jauh dari tengah — kalau offset crop tidak
dikembalikan, pusatnya akan jatuh di sekitar tengah crop dan assert gagal.

Tidak butuh ultralytics maupun file model: bbox detektor di-stub.
"""
import json
import os
import sys

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)


def test_qr_roi_crop_decode_koordinat_frame_asal():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    segno = pytest.importorskip("segno")
    pytest.importorskip("pyzbar")
    from tests.underwater_sim import render_qr_bgr, place_on_canvas
    from vision.qr_detect import _decode_tracked_roi
    from tools.qr_vision_worker import _quad_from_bbox

    text = json.dumps({"mission": 5, "team": "HYDROSHIP", "type": "payload", "id": "C"})
    qr = render_qr_bgr(text, module_px=5, border=4, segno=segno)
    # QR harus berjarak LEBIH dari pad crop (0.9 x sisi) dari tepi kiri/atas.
    # Kalau tidak, crop ter-clamp di 0 dan koordinat crop kebetulan sama dengan
    # koordinat frame — assert di bawah jadi lolos walau offset tidak
    # dikembalikan, yaitu tepat bug yang ingin ditangkap tes ini.
    frame, (x0, y0, w, h) = place_on_canvas(qr, canvas_wh=(1280, 720),
                                            offset=(300, 150))
    assert x0 > 0.9 * w and y0 > 0.9 * h, "penempatan QR tak menguji offset crop"

    # Stub "detektor": bbox region QR persis seperti yang dikirim YOLOHookDetector.
    bbox = (float(x0), float(y0), float(w), float(h))
    out = _decode_tracked_roi(frame, _quad_from_bbox(bbox), full_cascade=True)

    assert out, "decode lewat ROI crop gagal"
    assert out[0]["data"] == text

    pts = np.asarray(out[0]["pts"], dtype=np.float32).reshape(-1, 2)
    cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
    assert abs(cx - (x0 + w / 2.0)) <= 8.0, f"x bukan koordinat frame asal: {cx}"
    assert abs(cy - (y0 + h / 2.0)) <= 8.0, f"y bukan koordinat frame asal: {cy}"


def test_quad_from_bbox_urutan_sudut():
    from tools.qr_vision_worker import _quad_from_bbox

    assert _quad_from_bbox((10, 20, 30, 40)) == [[10, 20], [40, 20], [40, 60], [10, 60]]
