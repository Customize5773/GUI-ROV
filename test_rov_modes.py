"""Unit test pemetaan pilot mode + gate depth-hold (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_modes -v
"""

import unittest

from rov_modes import (
    DEPTH_HOLD_MODES,
    PILOT_MODE_MAP,
    depth_bias_engaged,
    depth_hold_allowed,
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

    def test_depth_hold_tidak_lagi_aktif_di_poshold(self):
        # Bias depth-set sekarang dipasangkan ke STABILIZE (lihat
        # DEPTH_HOLD_MODES), bukan ALT_HOLD/POSHOLD lagi — kedalaman di
        # Alt Hold/Pos Hold sudah ditahan cascade PID ArduSub sendiri.
        #
        # CATATAN: ini TIDAK berarti overlay POSHOLD ikut mati di ALT_HOLD —
        # gerbangnya poshold_mode_ok(), predikat yang berbeda. Menyatukan
        # keduanya persis itulah yang dulu mematikan POSHOLD diam-diam.
        self.assertFalse(depth_hold_allowed(resolve_pilot_mode("poshold")))

    def test_gerbang_overlay_poshold_lepas_dari_depth_hold_modes(self):
        # Overlay hidup di mode dasarnya (ALT_HOLD), dan HANYA di sana.
        self.assertTrue(poshold_mode_ok(resolve_pilot_mode("poshold")))
        self.assertTrue(poshold_mode_ok("ALT_HOLD"))
        for other in ("STABILIZE", "MANUAL", "ACRO", "", None):
            self.assertFalse(poshold_mode_ok(other), msg=repr(other))

        # Regresi yang sebenarnya terjadi: DEPTH_HOLD_MODES dipersempit ke
        # STABILIZE dan gerbang overlay ikut mati karena memakai predikat itu.
        self.assertNotIn(resolve_pilot_mode("poshold"), DEPTH_HOLD_MODES)

    def test_is_poshold_request_membedakan_yang_resolve_tidak_bisa(self):
        # Keduanya berujung di ALT_HOLD, jadi hasil resolve_pilot_mode TIDAK
        # cukup untuk tahu apakah overlay harus hidup.
        self.assertTrue(is_poshold_request("poshold"))
        self.assertTrue(is_poshold_request("  POSHOLD "))
        for other in ("depth_hold", "stabilize", "manual", "", None, 3, ["poshold"]):
            self.assertFalse(is_poshold_request(other), msg=repr(other))


class TestDepthHoldGate(unittest.TestCase):
    def test_diizinkan_di_stabilize(self):
        # STABILIZE tidak punya cascade PID kedalaman ArduSub, jadi bias
        # depth-set di sini jadi satu-satunya yang mendorong wahana ke
        # setpoint (open-loop) — bukan koreksi di atas PID firmware.
        self.assertTrue(depth_hold_allowed("STABILIZE"))
        self.assertIn("STABILIZE", DEPTH_HOLD_MODES)

    def test_ditolak_di_alt_hold(self):
        # Kedalaman di ALT_HOLD/POSHOLD sudah ditahan cascade PID ArduSub
        # sendiri; bias depth-set tidak lagi jadi syarat mode ini.
        self.assertFalse(depth_hold_allowed("ALT_HOLD"))
        self.assertNotIn("ALT_HOLD", DEPTH_HOLD_MODES)

    def test_ditolak_di_manual_dan_mode_tak_dikenal(self):
        for mode in ("MANUAL", "unknown", "", None, "ACRO"):
            self.assertFalse(depth_hold_allowed(mode), msg=repr(mode))


class TestDepthBiasEngaged(unittest.TestCase):
    """Gerbang depth-set: tombol SET + tombol ON/OFF + mode + stik heave."""

    # Nilai default yang "semuanya benar"; tiap test menjatuhkan satu syarat.
    OK = dict(enabled=True, target=0.5, mode="STABILIZE", heave=0, heave_epsilon=20)

    def engaged(self, **override):
        kw = dict(self.OK, **override)
        return depth_bias_engaged(
            kw["enabled"], kw["target"], kw["mode"], kw["heave"], kw["heave_epsilon"]
        )

    def test_semua_syarat_terpenuhi(self):
        self.assertTrue(self.engaged())

    def test_belum_pernah_di_set(self):
        # target None = operator belum menekan SET. Ini yang membuat masuk
        # STABILIZE tidak lagi menyeret wahana ke setpoint apa pun.
        self.assertFalse(self.engaged(target=None))

    def test_target_nol_tetap_setpoint_yang_sah(self):
        # Kalau None dan 0.0 tercampur, menekan SET tepat di permukaan akan
        # dianggap "belum di-set" dan depth-set diam-diam tidak bekerja.
        self.assertTrue(self.engaged(target=0.0))

    def test_saklar_operator_mati(self):
        self.assertFalse(self.engaged(enabled=False))

    def test_mode_bukan_stabilize(self):
        # Sudah SET dan sudah ON, tapi wahana di mode lain: DEPTH_HOLD_MODES
        # kini hanya STABILIZE — ALT_HOLD/POSHOLD/MANUAL semua ditolak.
        for mode in ("MANUAL", "ALT_HOLD", None):
            self.assertFalse(self.engaged(mode=mode), msg=repr(mode))

    def test_operator_memegang_stik_heave_menang(self):
        self.assertFalse(self.engaged(heave=21))
        self.assertFalse(self.engaged(heave=-500))

    def test_heave_dalam_deadzone_masih_dianggap_netral(self):
        self.assertTrue(self.engaged(heave=20))
        self.assertTrue(self.engaged(heave=-19))


if __name__ == "__main__":
    unittest.main()
