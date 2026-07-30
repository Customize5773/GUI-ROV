"""Unit test pemetaan axis GUI -> MANUAL_CONTROL (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_axes -v
"""

import unittest

from rov_axes import (
    IDLE_TIMEOUT,
    NEUTRAL,
    Z_NEUTRAL,
    axes_to_manual_control,
    clamp_axis,
    resolve_manual_packet,
    to_mavlink_z,
)


class TestClampAxis(unittest.TestCase):
    def test_passthrough_dalam_rentang(self):
        for name in ("surge", "sway", "yaw", "heave"):
            self.assertEqual(clamp_axis(name, 0), 0)
            self.assertEqual(clamp_axis(name, 250), 250)
            self.assertEqual(clamp_axis(name, -250), -250)

    def test_clamp_di_luar_rentang(self):
        self.assertEqual(clamp_axis("surge", 5000), 1000)
        self.assertEqual(clamp_axis("surge", -5000), -1000)
        self.assertEqual(clamp_axis("heave", 99999), 1000)
        self.assertEqual(clamp_axis("heave", -99999), -1000)

    def test_nilai_tidak_valid_jadi_nol(self):
        for bad in (None, "abc", float("nan"), {}, []):
            self.assertEqual(clamp_axis("surge", bad), 0)

    def test_pembulatan(self):
        self.assertEqual(clamp_axis("yaw", 10.6), 11)
        self.assertEqual(clamp_axis("yaw", "-10.4"), -10)

    def test_axis_tak_dikenal_pakai_rentang_default(self):
        self.assertEqual(clamp_axis("entah", 5000), 1000)


class TestToMavlinkZ(unittest.TestCase):
    def test_netral_di_tengah(self):
        self.assertEqual(to_mavlink_z(0), Z_NEUTRAL)

    def test_batas(self):
        self.assertEqual(to_mavlink_z(1000), 1000)
        self.assertEqual(to_mavlink_z(-1000), 0)

    def test_monoton_dan_terkurung(self):
        prev = -1
        for h in range(-2000, 2001, 25):
            z = to_mavlink_z(h)
            self.assertGreaterEqual(z, 0)
            self.assertLessEqual(z, 1000)
            self.assertGreaterEqual(z, prev)
            prev = z

    def test_input_di_luar_rentang_ikut_di_clamp(self):
        self.assertEqual(to_mavlink_z(9999), 1000)
        self.assertEqual(to_mavlink_z(-9999), 0)


class TestAxesToManualControl(unittest.TestCase):
    def test_pemetaan_sumbu(self):
        r = axes_to_manual_control(surge=100, sway=200, yaw=300, heave=400)
        self.assertEqual(r["x"], 100)
        self.assertEqual(r["y"], 200)
        self.assertEqual(r["r"], 300)
        self.assertEqual(r["z"], to_mavlink_z(400))

    def test_buttons_dipotong_16_bit(self):
        self.assertEqual(axes_to_manual_control(buttons=0x1FFFF)["buttons"], 0xFFFF)

    def test_neutral_adalah_perintah_diam(self):
        self.assertEqual(NEUTRAL, {"x": 0, "y": 0, "z": Z_NEUTRAL, "r": 0, "buttons": 0})


class TestResolveManualPacket(unittest.TestCase):
    """Fail-safe idle: axis berhenti mengalir -> kirim netral, jangan tahan
    thrust terakhir."""

    AXES = {"surge": 400, "sway": -300, "yaw": 200, "heave": 600}

    def test_axis_masih_segar_dipakai_apa_adanya(self):
        packet, stale = resolve_manual_packet(self.AXES, last_update=100.0, now=100.2)
        self.assertFalse(stale)
        self.assertEqual(packet, axes_to_manual_control(**self.AXES))

    def test_lewat_timeout_jadi_netral(self):
        packet, stale = resolve_manual_packet(
            self.AXES, last_update=100.0, now=100.0 + IDLE_TIMEOUT + 0.01
        )
        self.assertTrue(stale)
        self.assertEqual(packet, NEUTRAL)

    def test_tepat_di_batas_belum_stale(self):
        packet, stale = resolve_manual_packet(
            self.AXES, last_update=100.0, now=100.0 + IDLE_TIMEOUT
        )
        self.assertFalse(stale)
        self.assertEqual(packet, axes_to_manual_control(**self.AXES))

    def test_belum_pernah_ada_axis_langsung_netral(self):
        # last_update = 0.0 -> GUI belum pernah mengirim apa pun sejak boot.
        packet, stale = resolve_manual_packet(self.AXES, last_update=0.0, now=100.0)
        self.assertTrue(stale)
        self.assertEqual(packet, NEUTRAL)

    def test_netral_berarti_throttle_tengah_bukan_nol(self):
        # Inti bug yang diperbaiki: z = 0 pada MANUAL_CONTROL = turun penuh.
        packet, _ = resolve_manual_packet({}, last_update=0.0, now=1.0)
        self.assertEqual(packet["z"], Z_NEUTRAL)
        self.assertEqual((packet["x"], packet["y"], packet["r"]), (0, 0, 0))

    def test_hasil_bisa_dimodifikasi_tanpa_merusak_NEUTRAL(self):
        packet, _ = resolve_manual_packet({}, last_update=0.0, now=1.0)
        packet["z"] = 999
        self.assertEqual(NEUTRAL["z"], Z_NEUTRAL)

    def test_axis_hilang_dianggap_diam(self):
        packet, stale = resolve_manual_packet(
            {"surge": 500}, last_update=100.0, now=100.1
        )
        self.assertFalse(stale)
        self.assertEqual(packet["x"], 500)
        self.assertEqual(packet["y"], 0)
        self.assertEqual(packet["r"], 0)
        self.assertEqual(packet["z"], Z_NEUTRAL)


if __name__ == "__main__":
    unittest.main()
