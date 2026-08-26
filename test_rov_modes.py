"""Unit test pemetaan pilot mode (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_modes -v
"""

import unittest

from rov_modes import (
    PILOT_MODE_MAP,
    is_poshold_request,
    poshold_mode_ok,
    resolve_pilot_mode,
)


class TestResolvePilotMode(unittest.TestCase):
    def test_semua_mode_yang_didukung(self):
        self.assertEqual(resolve_pilot_mode("manual"), "MANUAL")
        self.assertEqual(resolve_pilot_mode("depth_hold"), "ALT_HOLD")
        self.assertEqual(resolve_pilot_mode("stabilize"), "STABILIZE")

    def test_peta_lengkap(self):
        for gui_name, ardusub_name in PILOT_MODE_MAP.items():
            self.assertEqual(resolve_pilot_mode(gui_name), ardusub_name)

    def test_case_insensitive_dan_spasi(self):
        self.assertEqual(resolve_pilot_mode("STABILIZE"), "STABILIZE")
        self.assertEqual(resolve_pilot_mode("Stabilize"), "STABILIZE")
        self.assertEqual(resolve_pilot_mode("  depth_hold  "), "ALT_HOLD")

    def test_mode_tidak_dikenal_mengembalikan_none(self):
        # Penting: None, bukan fallback ke MANUAL. Perintah harus DITOLAK,
        # bukan diam-diam mengubah mode ke sesuatu yang tidak diminta.
        for bad in ("", "ALT_HOLD", "surface", "acro", None, 3, True, ["stabilize"]):
            self.assertIsNone(resolve_pilot_mode(bad), msg=repr(bad))


class TestPoshold(unittest.TestCase):
    def test_poshold_berujung_di_alt_hold(self):
        # BUKAN mode POSHOLD firmware: itu butuh estimasi posisi horizontal dari
        # EKF yang tidak tersedia di bawah air. Yang dipakai overlay sisi Pi di
        # atas ALT_HOLD — lihat docstring rov_modes.py.
        self.assertEqual(resolve_pilot_mode("poshold"), "ALT_HOLD")

    def test_gerbang_overlay_poshold(self):
        # Overlay heading-hold hidup di mode dasarnya (ALT_HOLD), dan HANYA
        # di sana.
        self.assertTrue(poshold_mode_ok(resolve_pilot_mode("poshold")))
        self.assertTrue(poshold_mode_ok("ALT_HOLD"))
        for other in ("STABILIZE", "MANUAL", "ACRO", "", None):
            self.assertFalse(poshold_mode_ok(other), msg=repr(other))

    def test_is_poshold_request_membedakan_yang_resolve_tidak_bisa(self):
        # Keduanya berujung di ALT_HOLD, jadi hasil resolve_pilot_mode TIDAK
        # cukup untuk tahu apakah overlay harus hidup.
        self.assertTrue(is_poshold_request("poshold"))
        self.assertTrue(is_poshold_request("  POSHOLD "))
        for other in ("depth_hold", "stabilize", "manual", "", None, 3, ["poshold"]):
            self.assertFalse(is_poshold_request(other), msg=repr(other))


if __name__ == "__main__":
    unittest.main()
