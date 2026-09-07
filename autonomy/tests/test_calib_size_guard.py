"""tests/test_calib_size_guard.py — resolusi kalibrasi HARUS cocok resolusi stream.

Kelas bug yang dijaga (22 Agu 2026): `dwe_underwater.npz` dikalibrasi pada
4080x3072 sementara kamera streaming 1280x720. rms-nya paling bagus dari semua
file sehingga tampak paling unggul — padahal rms cuma mengukur kecocokan model
terhadap gambar kalibrasinya sendiri, ia tak tahu apa-apa soal resolusi pakai.
Akibatnya z ~ fx*W/w_px meleset sebanding rasio resolusi, dan ROV berhenti jauh
di dalam payload alih-alih di depannya. Diam, tanpa satu pun pesan error.

Penjaga lama (`qr_detect.VisionPipeline._verify_calib_size`) hidup di jalur
LAPTOP. Sejak vision pindah ke Pi (7 Sep 2026) kedua worker memuat K/dist lewat
`load_calibration()` lalu memakainya LANGSUNG — VisionPipeline tak pernah
dibangun, jadi penjaga itu tak pernah berjalan sekali pun di produksi. Modul ini
menguji penjaga penggantinya, `hook_localization.verify_calib_size()`, DAN
memaku fakta resolusi yang terukur supaya perubahan diam-diam jadi test merah.
"""
import os
import sys

import pytest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_AUTONOMY = os.path.dirname(_TESTS)
for _path in (_AUTONOMY, os.path.dirname(_AUTONOMY)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

np = pytest.importorskip("numpy")

from vision.hook_localization import load_calibration, verify_calib_size  # noqa: E402

_CALIB = os.path.join(_AUTONOMY, "vision", "calibration")

# Resolusi yang BENAR-BENAR dilayani Pi, dibaca dari unit systemd 7 Sep 2026:
#   ustreamer-cam1 (WALL,   port 8080): -r 1280x720
#   ustreamer-cam2 (BOTTOM, port 8081): -r 1280x720
# Kalau salah satu unit diubah, ubah angka di sini DAN kalibrasinya — kegagalan
# test ini adalah pengingat bahwa keduanya harus bergerak bersama.
STREAM_SIZE = (1280, 720)
DEPLOYED = {"bottom.npz": STREAM_SIZE, "wall.npz": STREAM_SIZE}


def _frame(width, height):
    return np.zeros((height, width, 3), np.uint8)


@pytest.mark.parametrize("nama,size", sorted(DEPLOYED.items()))
def test_kalibrasi_terpasang_cocok_resolusi_stream(nama, size):
    """Pengaman regresi: file yang dipakai worker Pi harus 1280x720."""
    path = os.path.join(_CALIB, nama)
    if not os.path.exists(path):
        pytest.fail(f"{nama} hilang — rov_agent.py menunjuk file ini sebagai default")
    calibration = load_calibration(path)
    assert calibration["image_size"] == size, (
        f"{nama} dibuat pada {calibration['image_size']}, stream {size}. "
        f"Pose PBVS akan meleset sebanding rasionya — kalibrasi ulang pada "
        f"resolusi stream, atau samakan `-r` di unit ustreamer.")
    assert verify_calib_size(calibration, _frame(*size)) is True


def test_mismatch_ditolak():
    """Inti penjaga: resolusi beda -> False, supaya pemanggil mematikan pose."""
    calibration = load_calibration(os.path.join(_CALIB, "bottom.npz"))
    assert verify_calib_size(calibration, _frame(1920, 1080)) is False


def test_mismatch_dilaporkan_sebagai_error_bukan_diam(caplog):
    """Kegagalan resolusi HARUS berisik. Bug 22 Agu lolos justru karena senyap."""
    calibration = load_calibration(os.path.join(_CALIB, "bottom.npz"))
    with caplog.at_level("ERROR"):
        verify_calib_size(calibration, _frame(1920, 1080))
    assert any(r.levelname == "ERROR" and "KALIBRASI DITOLAK" in r.getMessage()
               for r in caplog.records), "mismatch resolusi tidak dilaporkan sbg ERROR"


def test_kalibrasi_resolusi_foto_ditolak():
    """Kasus 22 Agu yang sebenarnya: kalibrasi dari FOTO 4080x3072."""
    path = os.path.join(_CALIB, "dwe_underwater.npz")
    if not os.path.exists(path):
        pytest.skip("dwe_underwater.npz tidak ada")
    calibration = load_calibration(path)
    assert calibration["image_size"] == (4080, 3072)
    assert verify_calib_size(calibration, _frame(*STREAM_SIZE)) is False, (
        "kalibrasi resolusi foto lolos penjaga — ini persis bug 22 Agu 2026")


def test_tanpa_image_size_tidak_memblokir_tapi_memperingatkan(caplog):
    """Kalibrasi lama tanpa field image_size tak bisa diperiksa. Jangan blokir
    misi karenanya, tapi jangan diam juga — tak terperiksa != terverifikasi."""
    calibration = {"K": np.eye(3), "dist": np.zeros(5),
                   "image_size": None, "name": "tanpa_size.npz"}
    with caplog.at_level("WARNING"):
        assert verify_calib_size(calibration, _frame(*STREAM_SIZE)) is True
    assert any("image_size" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("bad", [None])
def test_input_kosong_tidak_dianggap_valid(bad):
    """Gagal-tutup: tak ada kalibrasi/frame != boleh dipakai untuk pose."""
    calibration = load_calibration(os.path.join(_CALIB, "bottom.npz"))
    assert verify_calib_size(bad, _frame(*STREAM_SIZE)) is False
    assert verify_calib_size(calibration, bad) is False


@pytest.mark.parametrize("worker", ["qr_vision_worker", "hook_vision_worker"])
def test_worker_pi_benar_benar_memanggil_penjaga(worker):
    """Penjaga yang tak dipanggil = penjaga yang tidak ada.

    Ini persis kegagalan versi sebelumnya: _verify_calib_size ADA, lengkap
    dengan docstring panjang, tapi hidup di VisionPipeline yang tak pernah
    dibangun worker Pi. Jadi yang diuji di sini bukan keberadaannya —
    melainkan bahwa jalur produksi memanggilnya.
    """
    path = os.path.join(_AUTONOMY, "tools", worker + ".py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "verify_calib_size" in source, (
        f"{worker}.py tidak memanggil verify_calib_size — kalibrasi resolusi "
        f"salah akan diam-diam dipakai untuk pose")
    assert "calib_mismatch" in source, (
        f"{worker}.py tidak memancarkan status calib_mismatch — operator tak "
        f"akan tahu pose dimatikan")
