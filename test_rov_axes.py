"""Unit test pemetaan axis GUI -> MANUAL_CONTROL (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_axes -v
"""

import unittest

from rov_axes import (
    heave_skip_deadzone,
    HEAVE_SKIP_ALT_HOLD,
    HEAVE_SKIP_MANUAL,
    AXIS_NEUTRAL,
    AXIS_SHAPE,
    IDLE_TIMEOUT,
    NEUTRAL,
    Z_NEUTRAL,
    axes_to_manual_control,
    axis_released,
    clamp_axis,
    resolve_manual_packet,
    resolve_shape_updates,
    shape_axes,
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


class TestAxisReleased(unittest.TestCase):
    """Edge-detect rem surge/sway (rasa 'brake' ala DJI, lihat rov_agent.py
    apply_translation_brake): True hanya persis di tick stick baru dilepas."""

    EPS = 20

    def test_lepas_dari_penuh_terdeteksi(self):
        self.assertTrue(axis_released(800, 0, self.EPS))

    def test_lepas_dari_negatif_terdeteksi(self):
        self.assertTrue(axis_released(-800, 5, self.EPS))

    def test_stick_masih_ditahan_tidak_terdeteksi(self):
        self.assertFalse(axis_released(800, 800, self.EPS))

    def test_sudah_netral_sebelumnya_tidak_terdeteksi(self):
        # prev sudah <= epsilon -> bukan event "baru dilepas".
        self.assertFalse(axis_released(0, 0, self.EPS))
        self.assertFalse(axis_released(10, 0, self.EPS))

    def test_tepat_di_ambang_epsilon_belum_release(self):
        # prev == epsilon dianggap belum "dipegang" (butuh > epsilon).
        self.assertFalse(axis_released(self.EPS, 0, self.EPS))

    def test_curr_tepat_di_ambang_epsilon_sudah_release(self):
        self.assertTrue(axis_released(800, self.EPS, self.EPS))


class TestShapeAxes(unittest.TestCase):
    """Rate-limit + skala otoritas axis (lihat docstring rov_axes)."""

    DT = 0.05  # JOYSTICK_SEND_INTERVAL, 20 Hz
    # Shape eksplisit supaya test tidak ikut berubah saat AXIS_SHAPE di-tuning.
    SHAPE = {
        "surge": (1000.0, 2000.0, 1.0),
        "sway": (1000.0, 2000.0, 0.5),
        "yaw": (1000.0, 2000.0, 1.0),
        "heave": (1000.0, 2000.0, 1.0),
    }

    _DEFAULT = object()  # sentinel: dt=None adalah kasus uji yang sah

    def shape(self, current, target, dt=_DEFAULT):
        if dt is self._DEFAULT:
            dt = self.DT
        return shape_axes(current, target, dt, self.SHAPE)

    def test_tidak_melompat_ke_penuh_dalam_satu_tick(self):
        out = self.shape(dict(AXIS_NEUTRAL), {"surge": 1000})
        self.assertEqual(out["surge"], 50)  # 1000/s * 0.05 s

    def test_butuh_banyak_tick_untuk_mencapai_penuh(self):
        axes = dict(AXIS_NEUTRAL)
        for _ in range(20):  # 20 tick * 50 = 1000
            axes = self.shape(axes, {"surge": 1000})
        self.assertEqual(axes["surge"], 1000)

        # Sudah di target -> berhenti di sana, tidak melewatinya.
        self.assertEqual(self.shape(axes, {"surge": 1000})["surge"], 1000)

    def test_turun_lebih_cepat_daripada_naik(self):
        naik = self.shape(dict(AXIS_NEUTRAL), {"surge": 1000})["surge"]
        turun = 1000 - self.shape({"surge": 1000}, AXIS_NEUTRAL)["surge"]
        self.assertGreater(turun, naik)

    def test_skala_mengecilkan_target_akhir(self):
        # sway scale 0.5 -> stik penuh hanya sampai 500.
        axes = dict(AXIS_NEUTRAL)
        for _ in range(50):
            axes = self.shape(axes, {"sway": 1000})
        self.assertEqual(axes["sway"], 500)

    def test_skala_tidak_mengubah_axis_lain(self):
        axes = dict(AXIS_NEUTRAL)
        for _ in range(50):
            axes = self.shape(axes, {"surge": 1000})
        self.assertEqual(axes["surge"], 1000)

    def test_arah_negatif_simetris(self):
        axes = dict(AXIS_NEUTRAL)
        for _ in range(50):
            axes = self.shape(axes, {"yaw": -1000})
        self.assertEqual(axes["yaw"], -1000)

    def test_balik_tanda_melewati_nol(self):
        # +200 -> -1000 harus melandai, bukan melompat.
        out = self.shape({"yaw": 200}, {"yaw": -1000})
        self.assertEqual(out["yaw"], 150)
        self.assertGreater(out["yaw"], -1000)

    def test_dt_atau_rate_tidak_masuk_akal_langsung_ke_target(self):
        # Menahan axis karena jam aneh lebih berbahaya daripada melompat:
        # wahana bisa berhenti merespons stik tanpa sebab yang terlihat.
        for dt in (0, -1, None, "abc", float("nan"), float("inf")):
            out = self.shape(dict(AXIS_NEUTRAL), {"surge": 1000}, dt=dt)
            self.assertEqual(out["surge"], 1000, f"dt={dt!r}")

    def test_input_sampah_tidak_melempar(self):
        out = self.shape({"surge": "abc"}, {"surge": None, "sway": {}})
        self.assertEqual(out["surge"], 0)
        self.assertEqual(out["sway"], 0)

    def test_current_atau_target_none_dianggap_netral(self):
        self.assertEqual(self.shape(None, None), dict(AXIS_NEUTRAL))

    def test_selalu_mengembalikan_keempat_axis(self):
        self.assertEqual(set(self.shape({}, {})), set(AXIS_NEUTRAL))

    def test_tidak_mengubah_dict_masukan(self):
        cur = dict(AXIS_NEUTRAL)
        self.shape(cur, {"surge": 1000})
        self.assertEqual(cur, dict(AXIS_NEUTRAL))


class TestResolveShapeUpdates(unittest.TestCase):
    """Validasi command `axis_shape` — ini masukan dari jaringan."""

    def test_update_parsial_mempertahankan_field_lain(self):
        applied, rejects = resolve_shape_updates({"sway": {"scale": 0.5}})
        self.assertEqual(rejects, [])
        rise, fall, scale = applied["sway"]
        self.assertEqual(scale, 0.5)
        self.assertEqual((rise, fall), AXIS_SHAPE["sway"][:2])

    def test_axis_tak_disebut_tidak_ikut_diubah(self):
        applied, _ = resolve_shape_updates({"sway": {"scale": 0.5}})
        self.assertEqual(list(applied), ["sway"])

    def test_di_luar_rentang_ditolak_bukan_dijepit(self):
        for spec in ({"scale": 0}, {"scale": 5}, {"rise": 1}, {"fall": 99999}):
            applied, rejects = resolve_shape_updates({"surge": spec})
            self.assertEqual(applied, {}, spec)
            self.assertTrue(rejects, spec)

    def test_nilai_tidak_valid_ditolak(self):
        for bad in (None, "abc", True, False, float("nan"), float("inf"), []):
            applied, rejects = resolve_shape_updates({"surge": {"scale": bad}})
            self.assertEqual(applied, {}, repr(bad))
            self.assertTrue(rejects, repr(bad))

    def test_satu_field_gagal_membatalkan_axis_itu_saja(self):
        applied, rejects = resolve_shape_updates(
            {"surge": {"scale": 99}, "sway": {"scale": 0.5}}
        )
        self.assertNotIn("surge", applied)
        self.assertIn("sway", applied)
        self.assertTrue(rejects)

    def test_axis_dan_field_tak_dikenal_ditolak(self):
        _, rejects = resolve_shape_updates({"terbang": {"scale": 0.5}})
        self.assertTrue(rejects)
        _, rejects = resolve_shape_updates({"surge": {"turbo": 2}})
        self.assertTrue(rejects)

    def test_payload_bukan_dict_ditolak(self):
        for bad in (None, 5, "sway", []):
            applied, rejects = resolve_shape_updates(bad)
            self.assertEqual(applied, {})
            self.assertTrue(rejects)

    def test_resolve_tidak_memutasi_AXIS_SHAPE(self):
        sebelum = dict(AXIS_SHAPE)
        resolve_shape_updates({"sway": {"scale": 0.2}})
        self.assertEqual(dict(AXIS_SHAPE), sebelum)


if __name__ == "__main__":
    unittest.main()


class TestHeaveSkipDeadzone(unittest.TestCase):
    """heave_skip_deadzone: lompati dead band FC, pakai penuh travel stik."""

    def test_netral_tetap_nol(self):
        """Netral WAJIB 0 persis: jalur E-Stop/fail-safe bergantung padanya."""
        for v in (0, 5, -5, 20, -20):
            self.assertEqual(heave_skip_deadzone(v, HEAVE_SKIP_ALT_HOLD), 0)

    def test_langsung_lewat_deadzone(self):
        """Sentuhan terkecil di atas epsilon sudah keluar dari THR_DZ (250 unit)."""
        self.assertGreaterEqual(heave_skip_deadzone(21, HEAVE_SKIP_ALT_HOLD), 250)
        self.assertLessEqual(heave_skip_deadzone(-21, HEAVE_SKIP_ALT_HOLD), -250)

    def test_monoton_dan_simetris(self):
        prev = 0
        for v in range(21, 1001, 10):
            out = heave_skip_deadzone(v, HEAVE_SKIP_ALT_HOLD)
            self.assertGreaterEqual(out, prev, f"turun di {v}")
            self.assertEqual(heave_skip_deadzone(-v, HEAVE_SKIP_ALT_HOLD), -out)
            prev = out

    def test_penuh_tetap_penuh(self):
        """Otoritas maksimum tidak boleh berkurang gara-gara remap."""
        self.assertEqual(heave_skip_deadzone(1000, HEAVE_SKIP_ALT_HOLD), 1000)
        self.assertEqual(heave_skip_deadzone(2000, HEAVE_SKIP_ALT_HOLD), 1000)

    def test_manual_lompat_lebih_kecil(self):
        """MANUAL cuma perlu melompati MOT_SPIN_MIN, bukan THR_DZ juga."""
        self.assertLess(
            heave_skip_deadzone(21, HEAVE_SKIP_MANUAL),
            heave_skip_deadzone(21, HEAVE_SKIP_ALT_HOLD),
        )

    def test_linear_di_atas_lompatan(self):
        """Tanpa expo tersembunyi: kenaikan stik = kenaikan output sebanding.

        Kurva rasa stik dimiliki profil joystick (halaman Joystick), bukan
        modul ini. Tes ini yang menjaga agar expo kedua tidak menyelinap balik.
        """
        lo = heave_skip_deadzone(21, HEAVE_SKIP_ALT_HOLD)
        mid = heave_skip_deadzone(510, HEAVE_SKIP_ALT_HOLD)
        self.assertAlmostEqual(mid, (lo + 1000) / 2, delta=3)
