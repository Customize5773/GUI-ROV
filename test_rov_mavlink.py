"""Unit test throttle & sanitasi stream MAVLink (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_mavlink -v
"""

import json
import unittest

from rov_mavlink import (
    DEFAULT_STREAM_HZ,
    STREAM_KEEPALIVE_TIMEOUT,
    RateLimiter,
    sanitize_fields,
    stream_still_wanted,
)


class TestRateLimiter(unittest.TestCase):
    def test_pesan_pertama_selalu_lolos(self):
        rl = RateLimiter(hz=10)
        self.assertTrue(rl.allow("ATTITUDE", 100.0))

    def test_menahan_di_dalam_interval(self):
        rl = RateLimiter(hz=10)  # interval 0.1 s
        self.assertTrue(rl.allow("ATTITUDE", 100.00))
        self.assertFalse(rl.allow("ATTITUDE", 100.05))
        self.assertTrue(rl.allow("ATTITUDE", 100.10))

    def test_per_type_bukan_global(self):
        # Inti fitur: STATUSTEXT yang jarang tidak boleh kalah oleh ATTITUDE
        # yang datang terus-menerus.
        rl = RateLimiter(hz=10)
        self.assertTrue(rl.allow("ATTITUDE", 100.0))
        self.assertTrue(rl.allow("STATUSTEXT", 100.0))
        self.assertTrue(rl.allow("SYS_STATUS", 100.0))
        self.assertFalse(rl.allow("ATTITUDE", 100.01))

    def test_laju_efektif_mendekati_batas(self):
        rl = RateLimiter(hz=10)
        lolos = sum(1 for i in range(100) if rl.allow("ATTITUDE", 100.0 + i * 0.01))
        # 100 pesan dalam 1 detik pada 10 Hz -> ~10 yang lolos.
        self.assertEqual(lolos, 10)

    def test_sumber_periodik_tepat_di_batas_tidak_kehilangan_separuh(self):
        # Regresi: FC mengirim persis 10 Hz dan limiter juga 10 Hz. Tanpa
        # toleransi "boleh sedikit lebih awal", galat float membuat tiap pesan
        # kedua tertahan dan laju efektif jatuh jadi 5 Hz.
        rl = RateLimiter(hz=10)
        t = 100.0
        lolos = 0
        for _ in range(50):
            if rl.allow("ATTITUDE", t):
                lolos += 1
            t += 0.1
        self.assertEqual(lolos, 50)

    def test_reset_melupakan_riwayat(self):
        # Dipanggil saat link putus: pesan pertama setelah sambung ulang tidak
        # boleh tertahan sisa timestamp lama.
        rl = RateLimiter(hz=10)
        self.assertTrue(rl.allow("ATTITUDE", 100.0))
        rl.reset()
        self.assertTrue(rl.allow("ATTITUDE", 100.01))

    def test_hz_tidak_valid_ditolak(self):
        for bad in (0, -1, -0.5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RateLimiter(hz=bad)

    def test_default_10hz(self):
        self.assertEqual(DEFAULT_STREAM_HZ, 10.0)
        self.assertAlmostEqual(RateLimiter().interval, 0.1)


class TestSanitizeFields(unittest.TestCase):
    def test_nilai_biasa_lewat_apa_adanya(self):
        out = sanitize_fields({"roll": 0.5, "time_boot_ms": 1234, "armed": True})
        self.assertEqual(out, {"roll": 0.5, "time_boot_ms": 1234, "armed": True})

    def test_mavpackettype_dibuang(self):
        # Sudah dikirim terpisah sebagai field "msg"; menyertakannya lagi
        # cuma menggandakan data di setiap frame.
        self.assertNotIn("mavpackettype", sanitize_fields({"mavpackettype": "ATTITUDE", "roll": 0}))

    def test_bytearray_jadi_string(self):
        # STATUSTEXT.text datang sebagai bytearray ber-NUL padding.
        out = sanitize_fields({"text": bytearray(b"PreArm: check failed\x00\x00")})
        self.assertEqual(out["text"], "PreArm: check failed")
        out = sanitize_fields({"text": b"EKF3 IMU0 is using GPS\x00"})
        self.assertEqual(out["text"], "EKF3 IMU0 is using GPS")

    def test_list_dipotong(self):
        out = sanitize_fields({"voltages": list(range(40))})
        self.assertEqual(len(out["voltages"]), 16)
        self.assertEqual(out["voltages"][0], 0)

    def test_tuple_jadi_list(self):
        out = sanitize_fields({"voltages": (1, 2, 3)})
        self.assertEqual(out["voltages"], [1, 2, 3])

    def test_non_finite_jadi_none(self):
        # NaN/Infinity BUKAN JSON valid — kalau lolos, JSON.parse di browser
        # melempar dan seluruh frame terbuang, bukan cuma satu field.
        out = sanitize_fields({"a": float("nan"), "b": float("inf"), "c": float("-inf")})
        self.assertIsNone(out["a"])
        self.assertIsNone(out["b"])
        self.assertIsNone(out["c"])

    def test_hasil_selalu_bisa_di_json_dumps(self):
        payload = sanitize_fields({
            "text": bytearray(b"hello\x00"),
            "voltages": (1, 2, float("nan")),
            "roll": float("inf"),
            "id": 7,
            "flag": False,
            "kosong": None,
            "aneh": object(),
        })
        encoded = json.dumps(payload, allow_nan=False)  # melempar kalau ada NaN
        self.assertIn('"hello"', encoded)

    def test_masukan_bukan_dict(self):
        self.assertEqual(sanitize_fields(None), {})
        self.assertEqual(sanitize_fields("ATTITUDE"), {})


class TestStreamStillWanted(unittest.TestCase):
    def test_belum_pernah_diminta(self):
        self.assertFalse(stream_still_wanted(None, 100.0))

    def test_masih_dalam_batas(self):
        self.assertTrue(stream_still_wanted(100.0, 100.0 + STREAM_KEEPALIVE_TIMEOUT - 1))

    def test_kedaluwarsa(self):
        # Tab Analyze yang ditutup mendadak tidak boleh meninggalkan stream
        # jalan terus tanpa ada yang mendengarkan.
        self.assertFalse(stream_still_wanted(100.0, 100.0 + STREAM_KEEPALIVE_TIMEOUT))
        self.assertFalse(stream_still_wanted(100.0, 200.0))

    def test_keepalive_10_detik_punya_ruang_gagal(self):
        # Halaman Analyze mengirim keepalive tiap 10 detik; batas 30 detik
        # memberi ruang beberapa kali gagal sebelum stream ditutup.
        self.assertGreaterEqual(STREAM_KEEPALIVE_TIMEOUT, 30.0)


if __name__ == "__main__":
    unittest.main()
