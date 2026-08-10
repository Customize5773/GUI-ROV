"""Unit test pemetaan pilot mode + gate depth-hold (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_modes -v
"""

import unittest

from rov_modes import (
    ACRO_WARNING,
    DEPTH_HOLD_MODES,
    MODE_WARNINGS,
    PILOT_MODE_MAP,
    RISKY_MODES,
    STABILIZE_WARNING,
    depth_bias_engaged,
    depth_hold_allowed,
    is_poshold_request,
    is_risky_mode,
    resolve_pilot_mode,
    warning_for_mode,
)


class TestResolvePilotMode(unittest.TestCase):
    def test_semua_mode_yang_didukung(self):
        self.assertEqual(resolve_pilot_mode("manual"), "MANUAL")
        self.assertEqual(resolve_pilot_mode("depth_hold"), "ALT_HOLD")
        self.assertEqual(resolve_pilot_mode("acro"), "ACRO")

    def test_stabilize_adalah_alias_alt_hold(self):
        # Tab STABILIZE sudah dihapus dari GUI karena ia menstabilkan attitude
        # tapi TIDAK menahan kedalaman. Aliasnya dipertahankan supaya profil
        # joystick tersimpan dengan aksi `mode_stabilize` tetap bekerja — dan
        # kini ujungnya mode yang memberi keduanya.
        self.assertEqual(resolve_pilot_mode("stabilize"), "ALT_HOLD")

    def test_peta_lengkap(self):
        for gui_name, ardusub_name in PILOT_MODE_MAP.items():
            self.assertEqual(resolve_pilot_mode(gui_name), ardusub_name)

    def test_case_insensitive_dan_spasi(self):
        self.assertEqual(resolve_pilot_mode("ACRO"), "ACRO")
        self.assertEqual(resolve_pilot_mode("Acro"), "ACRO")
        self.assertEqual(resolve_pilot_mode("  depth_hold  "), "ALT_HOLD")

    def test_mode_tidak_dikenal_mengembalikan_none(self):
        # Penting: None, bukan fallback ke MANUAL. Perintah harus DITOLAK,
        # bukan diam-diam mengubah mode ke sesuatu yang tidak diminta.
        for bad in ("", "ALT_HOLD", "surface", None, 3, True, ["acro"]):
            self.assertIsNone(resolve_pilot_mode(bad), msg=repr(bad))


class TestPoshold(unittest.TestCase):
    def test_poshold_berujung_di_alt_hold(self):
        # BUKAN mode POSHOLD firmware: itu butuh estimasi posisi horizontal dari
        # EKF yang tidak tersedia di bawah air. Yang dipakai overlay sisi Pi di
        # atas ALT_HOLD — lihat docstring rov_modes.py.
        self.assertEqual(resolve_pilot_mode("poshold"), "ALT_HOLD")

    def test_depth_hold_tetap_aktif_di_poshold(self):
        # POSHOLD menahan kedalaman DAN heading; kalau gate depth-hold mati,
        # tombol gain +/- dan bias throttle ikut mati tanpa alasan.
        self.assertTrue(depth_hold_allowed(resolve_pilot_mode("poshold")))

    def test_poshold_bukan_mode_risky(self):
        # Tidak lebih berbahaya dari ALT_HOLD — tidak perlu gerbang konfirmasi.
        self.assertFalse(is_risky_mode(resolve_pilot_mode("poshold")))

    def test_is_poshold_request_membedakan_yang_resolve_tidak_bisa(self):
        # Keduanya berujung di ALT_HOLD, jadi hasil resolve_pilot_mode TIDAK
        # cukup untuk tahu apakah overlay harus hidup.
        self.assertTrue(is_poshold_request("poshold"))
        self.assertTrue(is_poshold_request("  POSHOLD "))
        for other in ("depth_hold", "stabilize", "manual", "acro", "", None, 3, ["poshold"]):
            self.assertFalse(is_poshold_request(other), msg=repr(other))


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
        # GUI tidak bisa lagi meminta mode ini, tapi wahana masih bisa berada
        # di sana lewat saklar RC / GCS lain, jadi gate-nya tetap harus benar.
        self.assertFalse(depth_hold_allowed("STABILIZE"))
        self.assertNotIn("STABILIZE", DEPTH_HOLD_MODES)

    def test_diizinkan_lewat_alias_stabilize(self):
        # Tombol "stabilize" di profil joystick lama kini masuk ALT_HOLD, jadi
        # depth hold justru AKTIF di sana.
        self.assertTrue(depth_hold_allowed(resolve_pilot_mode("stabilize")))

    def test_ditolak_di_manual_dan_mode_tak_dikenal(self):
        for mode in ("MANUAL", "unknown", "", None, "acro"):
            self.assertFalse(depth_hold_allowed(mode), msg=repr(mode))


class TestRiskyMode(unittest.TestCase):
    def test_acro_dianggap_berisiko(self):
        self.assertTrue(is_risky_mode("ACRO"))

    def test_stabilize_tidak_perlu_gerbang_konfirmasi(self):
        # is_risky_mode menggerbangi konfirmasi SEBELUM mode diminta. STABILIZE
        # tidak bisa lagi diminta dari GUI, jadi tidak ada yang perlu
        # dikonfirmasi — tapi peringatannya tetap ada (lihat TestWarningForMode).
        self.assertFalse(is_risky_mode("STABILIZE"))
        self.assertIsNotNone(warning_for_mode("STABILIZE"))

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

    def test_setiap_mode_berisiko_punya_peringatan(self):
        # MODE_WARNINGS boleh lebih luas dari RISKY_MODES (STABILIZE), tapi
        # tidak boleh ada mode berisiko yang gerbang konfirmasinya menyala
        # tanpa pesan apa pun untuk ditampilkan.
        for mode in RISKY_MODES:
            self.assertIsNotNone(warning_for_mode(mode), msg=mode)


class TestDepthBiasEngaged(unittest.TestCase):
    """Gerbang depth-set: tombol SET + tombol ON/OFF + mode + stik heave."""

    # Nilai default yang "semuanya benar"; tiap test menjatuhkan satu syarat.
    OK = dict(enabled=True, target=0.5, mode="ALT_HOLD", heave=0, heave_epsilon=20)

    def engaged(self, **override):
        kw = dict(self.OK, **override)
        return depth_bias_engaged(
            kw["enabled"], kw["target"], kw["mode"], kw["heave"], kw["heave_epsilon"]
        )

    def test_semua_syarat_terpenuhi(self):
        self.assertTrue(self.engaged())

    def test_belum_pernah_di_set(self):
        # target None = operator belum menekan SET. Ini yang membuat masuk
        # ALT_HOLD tidak lagi menyeret wahana ke setpoint apa pun.
        self.assertFalse(self.engaged(target=None))

    def test_target_nol_tetap_setpoint_yang_sah(self):
        # Kalau None dan 0.0 tercampur, menekan SET tepat di permukaan akan
        # dianggap "belum di-set" dan depth-set diam-diam tidak bekerja.
        self.assertTrue(self.engaged(target=0.0))

    def test_saklar_operator_mati(self):
        self.assertFalse(self.engaged(enabled=False))

    def test_mode_bukan_depth_hold(self):
        # Sudah SET dan sudah ON, tapi wahana di MANUAL/ACRO: tidak ada cascade
        # PID kedalaman yang menerima bias, jadi bias jadi dorongan open-loop.
        for mode in ("MANUAL", "ACRO", "STABILIZE", None):
            self.assertFalse(self.engaged(mode=mode), msg=repr(mode))

    def test_operator_memegang_stik_heave_menang(self):
        self.assertFalse(self.engaged(heave=21))
        self.assertFalse(self.engaged(heave=-500))

    def test_heave_dalam_deadzone_masih_dianggap_netral(self):
        self.assertTrue(self.engaged(heave=20))
        self.assertTrue(self.engaged(heave=-19))


if __name__ == "__main__":
    unittest.main()
