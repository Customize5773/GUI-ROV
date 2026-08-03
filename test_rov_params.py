"""Unit test penanganan parameter ArduSub (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_params -v
"""

import os
import unittest

from rov_params import (
    PARAM_NAME_MAX,
    PARAM_TYPES,
    coerce_param_value,
    decode_param_id,
    is_integer_type,
    param_matches,
    param_type_name,
    parse_param_dump,
)

REAL32 = 9
INT8 = 2
INT16 = 4
INT32 = 6

DUMP_PATH = os.path.join(os.path.dirname(__file__), "parameters_ardusub.params")


class TestTipeParam(unittest.TestCase):
    def test_nama_tipe(self):
        self.assertEqual(param_type_name(INT8), "INT8")
        self.assertEqual(param_type_name(REAL32), "REAL32")
        self.assertIsNone(param_type_name(0))
        self.assertIsNone(param_type_name(99))

    def test_integer_vs_real(self):
        for type_id in (1, 2, 3, 4, 5, 6, 7, 8):
            self.assertTrue(is_integer_type(type_id), msg=type_id)
        self.assertFalse(is_integer_type(REAL32))
        self.assertFalse(is_integer_type(10))

    def test_tipe_tak_dikenal_bukan_integer(self):
        # Penting: default TIDAK membulatkan. Membulatkan nilai bertipe asing
        # diam-diam bisa menulis angka yang bukan yang diminta operator.
        self.assertFalse(is_integer_type(0))
        self.assertFalse(is_integer_type(None))


class TestNormalizeParamName(unittest.TestCase):
    def setUp(self):
        from rov_params import normalize_param_name

        self.norm = normalize_param_name

    def test_nama_valid(self):
        self.assertEqual(self.norm("MOT_1_DIRECTION"), "MOT_1_DIRECTION")
        self.assertEqual(self.norm("FS_GCS_ENABLE"), "FS_GCS_ENABLE")
        self.assertEqual(self.norm("ATC_RAT_YAW_P"), "ATC_RAT_YAW_P")

    def test_huruf_kecil_dan_spasi_dinormalkan(self):
        self.assertEqual(self.norm("  mot_1_direction  "), "MOT_1_DIRECTION")
        self.assertEqual(self.norm("Frame_Config"), "FRAME_CONFIG")

    def test_panjang_maksimum_16(self):
        self.assertEqual(len("A" * PARAM_NAME_MAX), 16)
        self.assertEqual(self.norm("A" * PARAM_NAME_MAX), "A" * PARAM_NAME_MAX)
        self.assertIsNone(self.norm("A" * (PARAM_NAME_MAX + 1)))

    def test_nama_ditolak(self):
        # Semua harus None, bukan tebakan: menulis ke param yang salah di FC
        # jauh lebih mahal daripada menolak perintah.
        for bad in (
            "",
            "   ",
            None,
            42,
            True,
            ["MOT_1_DIRECTION"],
            "MOT-1-DIRECTION",     # tanda hubung bukan underscore
            "MOT 1 DIRECTION",     # spasi di tengah
            "MOT_1_DIR\x00EXTRA",  # NUL diselipkan ke char[16]
            "MÖT_1",               # non-ASCII
            "1MOT",                # diawali angka
        ):
            self.assertIsNone(self.norm(bad), msg=repr(bad))


class TestCoerceParamValue(unittest.TestCase):
    def test_selalu_float(self):
        # Wire MAVLink membawa nilai di satu field float32 apa pun tipenya.
        self.assertIsInstance(coerce_param_value(1, INT8), float)
        self.assertIsInstance(coerce_param_value(1.5, REAL32), float)

    def test_tipe_integer_dibulatkan(self):
        self.assertEqual(coerce_param_value(-1.0, INT8), -1.0)
        self.assertEqual(coerce_param_value(2.4, INT16), 2.0)
        self.assertEqual(coerce_param_value(-2.6, INT32), -3.0)

    def test_real32_tidak_dibulatkan(self):
        self.assertAlmostEqual(coerce_param_value(0.3, REAL32), 0.3)
        self.assertAlmostEqual(coerce_param_value("4.5", REAL32), 4.5)

    def test_string_angka_diterima(self):
        self.assertEqual(coerce_param_value(" -1 ", INT8), -1.0)

    def test_bool_jadi_nol_satu(self):
        self.assertEqual(coerce_param_value(True, INT8), 1.0)
        self.assertEqual(coerce_param_value(False, INT8), 0.0)

    def test_nilai_ditolak(self):
        for bad in ("", "abc", None, [1], {"v": 1}, float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                coerce_param_value(bad, REAL32)


class TestParamMatches(unittest.TestCase):
    def test_integer_dibanding_persis(self):
        self.assertTrue(param_matches(-1.0, -1.0, INT8))
        self.assertFalse(param_matches(-1.0, 1.0, INT8))

    def test_real32_pakai_toleransi(self):
        # ArduPilot menyimpan sebagai float32 lalu mengirim ulang; nilai tidak
        # pernah kembali bit-identik. Tanpa toleransi, param_set yang BERHASIL
        # akan dilaporkan gagal ke operator.
        self.assertTrue(param_matches(0.3, 0.30000001192092896, REAL32))
        self.assertFalse(param_matches(0.3, 0.4, REAL32))

    def test_nilai_tidak_valid_tidak_pernah_cocok(self):
        for sent, echoed in (
            (None, 1.0),
            (1.0, None),
            ("x", 1.0),
            (float("nan"), float("nan")),
            (float("inf"), float("inf")),
        ):
            self.assertFalse(param_matches(sent, echoed, REAL32), msg=(sent, echoed))


class TestDecodeParamId(unittest.TestCase):
    def test_bentuk_bentuk_dari_pymavlink(self):
        self.assertEqual(decode_param_id("MOT_1_DIRECTION"), "MOT_1_DIRECTION")
        self.assertEqual(decode_param_id(b"MOT_1_DIRECTION\x00"), "MOT_1_DIRECTION")
        self.assertEqual(decode_param_id(bytearray(b"FS_GCS_ENABLE\x00\x00")), "FS_GCS_ENABLE")

    def test_pas_16_karakter_tanpa_nul(self):
        name = "A" * 16
        self.assertEqual(decode_param_id(name.encode()), name)

    def test_masukan_aneh(self):
        self.assertEqual(decode_param_id(None), "")
        self.assertEqual(decode_param_id(123), "")


class TestParseParamDump(unittest.TestCase):
    SAMPLE = (
        "# Onboard parameters for Vehicle 1\n"
        "#\n"
        "# Vehicle-Id Component-Id Name Value Type\n"
        "1\t1\tACRO_EXPO\t0.300000011920928955\t9\n"
        "1\t1\tMOT_1_DIRECTION\t-1\t2\n"
        "\n"
        "1\t1\tBARIS_RUSAK\n"                 # kolom kurang -> dilewati
        "1\t1\tPARAM_X\tbukan_angka\t9\n"     # nilai bukan angka -> dilewati
        "1\t1\tPARAM_Y\t1.0\t123\n"           # tipe tak dikenal -> dilewati
        "1\t1\tSTAT_RUNTIME\t246444\t6\n"
    )

    def test_parsing_dasar(self):
        out = parse_param_dump(self.SAMPLE)
        self.assertEqual(set(out), {"ACRO_EXPO", "MOT_1_DIRECTION", "STAT_RUNTIME"})
        self.assertAlmostEqual(out["ACRO_EXPO"][0], 0.3)
        self.assertEqual(out["ACRO_EXPO"][1], 9)
        self.assertEqual(out["MOT_1_DIRECTION"], (-1.0, 2))

    def test_baris_rusak_dilewati_bukan_menggagalkan(self):
        # Dump adalah artefak eksternal (diekspor QGroundControl). Satu baris
        # rusak tidak boleh mematikan server simulasi.
        self.assertNotIn("BARIS_RUSAK", parse_param_dump(self.SAMPLE))
        self.assertNotIn("PARAM_X", parse_param_dump(self.SAMPLE))
        self.assertNotIn("PARAM_Y", parse_param_dump(self.SAMPLE))

    def test_dump_asli_di_repo(self):
        # Menjaga agar dump asli dari Pixhawk tetap bisa dibaca — ini fixture
        # yang dipakai mode simulasi server.js untuk halaman Vehicle.
        with open(DUMP_PATH, "r", encoding="utf-8") as fh:
            params = parse_param_dump(fh.read())

        self.assertGreater(len(params), 900)
        self.assertIn("FRAME_CONFIG", params)
        self.assertIn("MOT_1_DIRECTION", params)
        self.assertIn("FS_GCS_ENABLE", params)

        # Setiap tipe yang muncul harus dikenali, kalau tidak GUI tak bisa
        # menampilkan tipe maupun memutuskan pembulatan.
        for name, (_value, type_id) in params.items():
            self.assertIn(type_id, PARAM_TYPES, msg=f"{name} tipe {type_id}")


if __name__ == "__main__":
    unittest.main()
