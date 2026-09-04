"""tests/test_hook_vision_worker.py — Uji LatestFrame di worker YOLO laptop.

Kenapa kelas ini ada: cv2.VideoCapture atas stream MJPEG mem-buffer frame.
Kalau loop deteksi hanya membaca 10x/detik sementara kamera mengirim ~30 fps,
frame menumpuk dan yang dibaca YOLO makin lama makin TUA — latensi observasi
hook tumbuh sepanjang misi. LatestFrame menguras stream di thread sendiri dan
hanya menyimpan frame terakhir.

Test ini tidak butuh cv2: cukup objek capture palsu.
"""
import os
import sys
import time

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

from tools.hook_vision_worker import LatestFrame  # noqa: E402


class FakeCap:
    """Kamera palsu: mengembalikan frame bernomor, opsional gagal setelah n frame."""

    def __init__(self, fail_after=None, delay=0.0):
        self.n = 0
        self.fail_after = fail_after
        self.delay = delay

    def read(self):
        if self.delay:
            time.sleep(self.delay)
        if self.fail_after is not None and self.n >= self.fail_after:
            return False, None
        self.n += 1
        return True, f"frame{self.n}"


def _wait(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_take_mengembalikan_frame_terbaru_bukan_antrean():
    cap = FakeCap(delay=0.001)
    camera = LatestFrame(cap).start()
    try:
        assert _wait(lambda: cap.n > 20), "grabber harus menguras kamera terus-menerus"
        seq, frame, captured_at, failed = camera.take(0)
        assert not failed
        assert frame is not None
        # Frame yang diserahkan adalah yang TERAKHIR dibaca, bukan frame pertama
        # yang mengantre sejak start.
        assert int(frame.removeprefix("frame")) >= 20
        assert seq == int(frame.removeprefix("frame"))
        assert time.time() - captured_at < 0.5
    finally:
        camera.stop()


def test_take_dengan_seq_sama_tidak_mengembalikan_frame_lama():
    cap = FakeCap()
    camera = LatestFrame(cap).start()
    try:
        assert _wait(lambda: camera.take(0)[1] is not None)
        seq, _frame, _at, _failed = camera.take(0)
        camera.stop()
        time.sleep(0.05)   # pastikan grabber benar-benar berhenti
        same_seq, frame, _at, _failed = camera.take(seq)
        # Tidak ada frame BARU sejak seq itu -> None, supaya loop deteksi tidak
        # memproses ulang frame yang sama dan mengaku itu observasi baru.
        assert frame is None
        assert same_seq == seq
    finally:
        camera.stop()


def test_kegagalan_baca_dilaporkan_sebagai_failed():
    camera = LatestFrame(FakeCap(fail_after=0)).start()
    try:
        assert _wait(lambda: camera.take(0)[3] is True), "read() gagal harus terlihat"
        _seq, frame, _at, failed = camera.take(0)
        assert frame is None and failed is True
    finally:
        camera.stop()


def test_stop_menghentikan_thread_grabber():
    cap = FakeCap(delay=0.001)
    camera = LatestFrame(cap).start()
    assert _wait(lambda: cap.n > 5)
    camera.stop()
    time.sleep(0.1)
    settled = cap.n
    time.sleep(0.1)
    assert cap.n == settled, "grabber masih membaca setelah stop()"
