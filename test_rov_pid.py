"""Unit test pemetaan PID GUI -> param ArduSub + batas kedalaman kolam.

    python3 -m unittest test_rov_pid -v
"""

import unittest

from rov_pid import (
    DEPTH_BIAS_DEADZONE,
    DEPTH_BIAS_EPSILON,
    DEPTH_BIAS_GAIN,
    DEPTH_BIAS_LIMIT,
    PARAM_TO_PID,
    PID_PARAM_MAP,
    PID_WRITE_ORDER,
    REAL32,
    clamp_depth_target,
    depth_hold_bias,
    pid_param_names,
    resolve_pid_writes,
    valid_pool_depth,
)

# Nilai gain yang benar-benar ada di Pixhawk wahana (parameters_ardusub.params).
FC_NYATA = {
    "yaw": {"p": 0.18, "i": 0.018, "d": 0.0},
    "depth": {"p": 0.5, "i": 0.1, "d": 0.0},
}

# Default LAMA di public/js/config.js — bukan satuan ArduSub.
GUI_DEFAULT_LAMA = {
    "yaw": {"p": 2.0, "i": 0.0, "d": 0.5},
    "depth": {"p": 10.0, "i": 0.5, "d": 2.0},
}


class TestPeta(unittest.TestCase):
    def test_enam_gain_terpetakan(self):
        self.assertEqual(len(PID_PARAM_MAP), 6)
        self.assertEqual(len(PID_WRITE_ORDER), 6)
        self.assertEqual(set(PID_WRITE_ORDER), set(PID_PARAM_MAP))

    def test_nama_param_tanpa_duplikat(self):
        # Menjaga agar penambahan gain baru tidak menimpa param lama diam-diam.
        names = pid_param_names()
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(len(PARAM_TO_PID), 6)

    def test_nama_param_sesuai_keputusan(self):
        self.assertEqual(pid_param_names(), [
            "ATC_RAT_YAW_P", "ATC_RAT_YAW_I", "ATC_RAT_YAW_D",
            "PSC_ACCZ_P", "PSC_ACCZ_I", "PSC_ACCZ_D",
        ])

    def test_semua_real32(self):
        # Sudah dikonfirmasi dari dump nyata Pixhawk (kolom tipe = 9).
        for _name, type_id, _lo, _hi in PID_PARAM_MAP.values():
            self.assertEqual(type_id, REAL32)

    def test_rentang_masuk_akal(self):
        for name, (_n, _t, lo, hi) in zip(PID_PARAM_MAP, PID_PARAM_MAP.values()):
            self.assertLess(lo, hi, msg=str(name))
            self.assertGreaterEqual(lo, 0.0, msg=str(name))

    def test_nilai_fc_nyata_ada_di_dalam_rentang(self):
        # Kalau rentang yang ditulis tangan sampai menolak nilai yang SEDANG
        # BERJALAN di wahana, rentangnya yang salah.
        writes, rejects = resolve_pid_writes(FC_NYATA)
        self.assertEqual(rejects, [])
        self.assertEqual(len(writes), 6)


class TestResolvePidWrites(unittest.TestCase):
    def test_payload_lengkap_valid(self):
        writes, rejects = resolve_pid_writes(FC_NYATA)
        self.assertEqual(rejects, [])
        self.assertEqual([w[0] for w in writes], pid_param_names())
        self.assertEqual(writes[0], ("ATC_RAT_YAW_P", 0.18, REAL32))
        for _name, value, _t in writes:
            self.assertIsInstance(value, float)

    def test_default_gui_lama_DITOLAK(self):
        # REGRESI INTI: inilah yang membuat menyambungkan `pid` secara lugas
        # berbahaya. yaw.p 2.0 (FC 0.18), depth.p 10.0 (FC 0.5).
        writes, rejects = resolve_pid_writes(GUI_DEFAULT_LAMA)
        ditolak = {name for name, _alasan in rejects}
        self.assertIn("ATC_RAT_YAW_P", ditolak)
        self.assertIn("ATC_RAT_YAW_D", ditolak)
        self.assertIn("PSC_ACCZ_P", ditolak)
        self.assertIn("PSC_ACCZ_D", ditolak)
        # Yang ditolak TIDAK BOLEH ikut ditulis.
        ditulis = {name for name, _v, _t in writes}
        self.assertTrue(ditulis.isdisjoint(ditolak))

    def test_alasan_penolakan_menyebut_rentang(self):
        _writes, rejects = resolve_pid_writes({"yaw": {"p": 2.0}})
        self.assertEqual(len(rejects), 1)
        name, alasan = rejects[0]
        self.assertEqual(name, "ATC_RAT_YAW_P")
        self.assertIn("2", alasan)
        self.assertIn("rentang aman", alasan)

    def test_batas_rentang_inklusif(self):
        for axis, gain in PID_WRITE_ORDER:
            _n, _t, lo, hi = PID_PARAM_MAP[(axis, gain)]
            for edge in (lo, hi):
                writes, rejects = resolve_pid_writes({axis: {gain: edge}})
                self.assertEqual(rejects, [], msg=f"{axis}.{gain}={edge}")
                self.assertEqual(len(writes), 1)

    def test_payload_sebagian_boleh(self):
        # Halaman Setup boleh mengirim sebagian gain saja.
        writes, rejects = resolve_pid_writes({"yaw": {"p": 0.2}})
        self.assertEqual(rejects, [])
        self.assertEqual(writes, [("ATC_RAT_YAW_P", 0.2, REAL32)])

    def test_nilai_tidak_valid_jadi_reject_bukan_dilewati(self):
        # Angka yang sudah diketik operator tidak boleh hilang diam-diam.
        for bad in ("abc", None, [1], {"v": 1}, True, float("nan"), float("inf")):
            writes, rejects = resolve_pid_writes({"yaw": {"p": bad}})
            self.assertEqual(writes, [], msg=repr(bad))
            self.assertEqual(len(rejects), 1, msg=repr(bad))

    def test_string_angka_diterima(self):
        writes, rejects = resolve_pid_writes({"yaw": {"p": "0.2"}})
        self.assertEqual(rejects, [])
        self.assertEqual(writes[0][1], 0.2)

    def test_payload_cacat(self):
        for bad in (None, "pid", 42, []):
            writes, rejects = resolve_pid_writes(bad)
            self.assertEqual(writes, [], msg=repr(bad))
            self.assertTrue(rejects, msg=repr(bad))

    def test_bagian_bukan_objek_ditolak(self):
        writes, rejects = resolve_pid_writes({"yaw": 0.2})
        self.assertEqual(writes, [])
        self.assertEqual(rejects, [("yaw", "bagian bukan objek")])

    def test_payload_kosong_dilaporkan(self):
        # Jangan diam saja: operator menekan Apply dan tidak terjadi apa-apa.
        writes, rejects = resolve_pid_writes({})
        self.assertEqual(writes, [])
        self.assertTrue(rejects)

    def test_kunci_asing_diabaikan_bukan_error(self):
        writes, rejects = resolve_pid_writes({"yaw": {"p": 0.2, "ff": 9.9}, "roll": {"p": 1}})
        self.assertEqual(rejects, [])
        self.assertEqual(len(writes), 1)


class TestClampDepthTarget(unittest.TestCase):
    def test_tanpa_pool_depth_perilaku_lama(self):
        # Lupa mengirim pool_depth tidak boleh membuat keadaan lebih buruk.
        self.assertEqual(clamp_depth_target(3.0), 3.0)
        self.assertEqual(clamp_depth_target(-1.0), 0.0)

    def test_dijepit_ke_pool_depth(self):
        self.assertEqual(clamp_depth_target(3.0, 0.9), 0.9)
        self.assertEqual(clamp_depth_target(0.5, 0.9), 0.5)
        self.assertEqual(clamp_depth_target(0.9, 0.9), 0.9)

    def test_batas_permukaan_tetap_berlaku(self):
        self.assertEqual(clamp_depth_target(-5.0, 0.9), 0.0)

    def test_set_di_bawah_dasar_kolam_dijepit(self):
        # Tombol SET merekam state["depth"]. Pembacaan baro yang meleset di
        # dekat dasar tidak boleh jadi setpoint di luar kolam.
        self.assertAlmostEqual(clamp_depth_target(1.4, 0.9), 0.9)

    def test_pool_depth_tidak_valid_diabaikan(self):
        for bad in ("abc", None, 0, -1, float("nan")):
            self.assertEqual(clamp_depth_target(3.0, bad), 3.0, msg=repr(bad))

    def test_nilai_tidak_valid_jadi_nol(self):
        for bad in ("abc", None, [1], float("nan")):
            self.assertEqual(clamp_depth_target(bad, 0.9), 0.0, msg=repr(bad))


class TestValidPoolDepth(unittest.TestCase):
    def test_nilai_sah(self):
        self.assertEqual(valid_pool_depth(0.9), 0.9)
        self.assertEqual(valid_pool_depth("1.5"), 1.5)
        self.assertEqual(valid_pool_depth(3), 3.0)

    def test_nilai_ditolak(self):
        for bad in (0, -1, "abc", None, True, False, [1], float("nan"), float("inf")):
            self.assertIsNone(valid_pool_depth(bad), msg=repr(bad))


class TestDepthHoldBias(unittest.TestCase):
    """Bentuk bias throttle depth-set: nol -> lompat deadzone -> ramp -> saturasi."""

    def test_error_nol_bias_nol_persis(self):
        # Netral persis = ALT_HOLD menahan kedalaman dengan PID-nya sendiri.
        self.assertEqual(depth_hold_bias(0.0), 0.0)

    def test_di_bawah_epsilon_diam(self):
        # Sebesar noise baro. Tanpa ambang ini, error sekecil apa pun langsung
        # memicu dorongan penuh DEPTH_BIAS_DEADZONE dan wahana berosilasi.
        self.assertEqual(depth_hold_bias(DEPTH_BIAS_EPSILON * 0.99), 0.0)
        self.assertEqual(depth_hold_bias(-DEPTH_BIAS_EPSILON * 0.99), 0.0)

    def test_tepat_di_atas_epsilon_langsung_lewat_deadzone(self):
        # INTI perbaikan trial 2026-08-08: bias sekecil apa pun yang masih di
        # dalam deadzone THR_DZ (125 unit z) tidak pernah sampai ke controller.
        bias = depth_hold_bias(DEPTH_BIAS_EPSILON)
        self.assertGreater(abs(bias), 125)
        self.assertAlmostEqual(bias, DEPTH_BIAS_DEADZONE + DEPTH_BIAS_EPSILON * DEPTH_BIAS_GAIN)

    def test_arah_error(self):
        # error positif = target lebih dalam dari posisi = perlu TURUN.
        self.assertGreater(depth_hold_bias(0.2), 0)
        self.assertLess(depth_hold_bias(-0.2), 0)
        self.assertEqual(depth_hold_bias(0.2), -depth_hold_bias(-0.2))

    def test_naik_monoton_lalu_saturasi(self):
        self.assertGreater(depth_hold_bias(0.3), depth_hold_bias(0.1))
        for error in (0.5, 2.0, 50.0):
            self.assertEqual(depth_hold_bias(error), DEPTH_BIAS_LIMIT, msg=error)
            self.assertEqual(depth_hold_bias(-error), -DEPTH_BIAS_LIMIT, msg=error)

    def test_limit_lebih_besar_dari_deadzone(self):
        # Regresi bug LIMIT=80: kalau limit <= deadzone THR_DZ, bias maksimum
        # pun tenggelam dan perintah kedalaman TIDAK PERNAH sampai ke FC.
        self.assertGreater(DEPTH_BIAS_LIMIT, DEPTH_BIAS_DEADZONE)
        self.assertGreater(DEPTH_BIAS_DEADZONE, 125)

    def test_tidak_pernah_melawan_operator_penuh(self):
        # Bias digeser dari Z_NEUTRAL=500 pada rentang 0..1000, jadi limit harus
        # menyisakan ruang: operator selalu bisa menang dengan stik heave.
        self.assertLess(DEPTH_BIAS_LIMIT, 500)


if __name__ == "__main__":
    unittest.main()
