"""Unit test pemetaan pilot mode + gate depth-hold (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_modes -v
"""

import unittest

from rov_modes import (
    ACRO_WARNING,
    DEPTH_HOLD_MODES,
    MODE_WARNINGS,
    PILOT_MODE_MAP,
    STABILIZE_WARNING,
    depth_hold_allowed,
    is_risky_mode,
    resolve_pilot_mode,
    warning_for_mode,
)


class TestResolvePilotMode(unittest.TestCase):
    def test_semua_mode_yang_didukung(self):
        self.assertEqual(resolve_pilot_mode("manual"), "MANUAL")
        self.assertEqual(resolve_pilot_mode("stabilize"), "STABILIZE")
        self.assertEqual(resolve_pilot_mode("depth_hold"), "ALT_HOLD")
        self.assertEqual(resolve_pilot_mode("acro"), "ACRO")

    def test_peta_lengkap_dan_tanpa_duplikat(self):
        # Menjaga agar penambahan mode baru tidak menimpa mode lama diam-diam.
        self.assertEqual(len(set(PILOT_MODE_MAP.values())), len(PILOT_MODE_MAP))
        for gui_name, ardusub_name in PILOT_MODE_MAP.items():
            self.assertEqual(resolve_pilot_mode(gui_name), ardusub_name)

    def test_case_insensitive_dan_spasi(self):
        self.assertEqual(resolve_pilot_mode("ACRO"), "ACRO")
        self.assertEqual(resolve_pilot_mode("Acro"), "ACRO")
        self.assertEqual(resolve_pilot_mode("  depth_hold  "), "ALT_HOLD")

    def test_mode_tidak_dikenal_mengembalikan_none(self):
        # Penting: None, bukan fallback ke MANUAL. Perintah harus DITOLAK,
        # bukan diam-diam mengubah mode ke sesuatu yang tidak diminta.
        for bad in ("poshold", "", "ALT_HOLD", "surface", None, 3, True, ["acro"]):
            self.assertIsNone(resolve_pilot_mode(bad), msg=repr(bad))


class TestDepthHoldGate(unittest.TestCase):
    def test_diizinkan_di_alt_hold(self):
        self.assertTrue(depth_hold_allowed("ALT_HOLD"))

    def test_ditolak_di_acro(self):
        # Inti pengaman ACRO: throttle netral tidak menahan kedalaman, jadi
        # bias depth-hold tidak boleh ikut campur.
        self.assertFalse(depth_hold_allowed("ACRO"))
        self.assertNotIn("ACRO", DEPTH_HOLD_MODES)

    def test_ditolak_di_stabilize(self):
        # STABILIZE menstabilkan attitude, tapi tidak menjalankan cascade PID
        # kedalaman ArduSub (itu hanya jalan di ALT_HOLD) — throttle netral
        # cuma berarti "dorongan vertikal nol", bukan "tahan kedalaman".
        self.assertFalse(depth_hold_allowed("STABILIZE"))
        self.assertNotIn("STABILIZE", DEPTH_HOLD_MODES)

    def test_ditolak_di_manual_dan_mode_tak_dikenal(self):
        for mode in ("MANUAL", "unknown", "", None, "acro"):
            self.assertFalse(depth_hold_allowed(mode), msg=repr(mode))


class TestRiskyMode(unittest.TestCase):
    def test_acro_dianggap_berisiko(self):
        self.assertTrue(is_risky_mode("ACRO"))

    def test_stabilize_dianggap_berisiko(self):
        # STABILIZE tidak menahan kedalaman (lihat TestDepthHoldGate), jadi
        # operator perlu peringatan yang sama seperti ACRO.
        self.assertTrue(is_risky_mode("STABILIZE"))

    def test_mode_lain_tidak(self):
        for mode in ("MANUAL", "ALT_HOLD", None):
            self.assertFalse(is_risky_mode(mode), msg=repr(mode))

    def test_peringatan_menyebut_depth_hold(self):
        self.assertIn("ACRO", ACRO_WARNING)
        self.assertIn("kedalaman", ACRO_WARNING)
        self.assertIn("STABILIZE", STABILIZE_WARNING)
        self.assertIn("kedalaman", STABILIZE_WARNING)


class TestWarningForMode(unittest.TestCase):
    def test_pesan_per_mode_berisiko(self):
        self.assertEqual(warning_for_mode("ACRO"), ACRO_WARNING)
        self.assertEqual(warning_for_mode("STABILIZE"), STABILIZE_WARNING)

    def test_none_untuk_mode_aman_atau_tak_dikenal(self):
        for mode in ("MANUAL", "ALT_HOLD", "unknown", None):
            self.assertIsNone(warning_for_mode(mode), msg=repr(mode))

    def test_konsisten_dengan_mode_warnings(self):
        for mode, msg in MODE_WARNINGS.items():
            self.assertEqual(warning_for_mode(mode), msg)


if __name__ == "__main__":
    unittest.main()
