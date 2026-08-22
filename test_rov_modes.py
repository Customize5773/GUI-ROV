"""Unit test pemetaan pilot mode + gate depth-hold (tanpa hardware/pymavlink).

    python3 -m unittest test_rov_modes -v
"""

import ast
import inspect
import os
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

    def test_depth_hold_aktif_di_poshold(self):
        # Permintaan pilot 2026-08-22: depth up/down harus aktif di semua
        # mode kecuali Manual — termasuk Pos Hold (ALT_HOLD). Bias di sini
        # mendorong MANUAL_CONTROL.z sama seperti operator menyentuh stik
        # heave, jadi bekerja berdampingan dengan cascade PID ArduSub.
        self.assertTrue(depth_hold_allowed(resolve_pilot_mode("poshold")))

    def test_gerbang_overlay_poshold_independen_dari_depth_hold_modes(self):
        # Overlay heading-hold hidup di mode dasarnya (ALT_HOLD), dan HANYA
        # di sana — predikat terpisah dari depth_hold_allowed(), keduanya
        # sengaja tidak disatukan (lihat POSHOLD_BASE_MODE di rov_modes.py).
        self.assertTrue(poshold_mode_ok(resolve_pilot_mode("poshold")))
        self.assertTrue(poshold_mode_ok("ALT_HOLD"))
        for other in ("STABILIZE", "MANUAL", "ACRO", "", None):
            self.assertFalse(poshold_mode_ok(other), msg=repr(other))

        # ALT_HOLD kini termasuk DEPTH_HOLD_MODES juga — cek eksplisit
        # supaya perubahan sengaja ini tidak diam-diam ter-revert.
        self.assertIn(resolve_pilot_mode("poshold"), DEPTH_HOLD_MODES)

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

    def test_diizinkan_di_alt_hold(self):
        # ALT_HOLD (menaungi GUI "depth_hold" & "poshold") ikut diizinkan
        # sejak 2026-08-22: pilot minta depth up/down aktif di semua mode
        # kecuali Manual.
        self.assertTrue(depth_hold_allowed("ALT_HOLD"))
        self.assertIn("ALT_HOLD", DEPTH_HOLD_MODES)

    def test_ditolak_di_manual_dan_mode_tak_dikenal(self):
        for mode in ("MANUAL", "unknown", "", None, "ACRO"):
            self.assertFalse(depth_hold_allowed(mode), msg=repr(mode))


class TestDepthBiasEngaged(unittest.TestCase):
    """Gerbang depth-set: target + mode + stik heave. Tanpa saklar ON/OFF
    operator terpisah (desain 21 Agu 2026) — target sendiri yang jadi penanda
    aktif, diisi otomatis begitu operator memegang-lalu-lepas heave."""

    # Nilai default yang "semuanya benar"; tiap test menjatuhkan satu syarat.
    OK = dict(target=0.5, mode="STABILIZE", heave=0, heave_epsilon=20)

    def engaged(self, **override):
        kw = dict(self.OK, **override)
        return depth_bias_engaged(
            kw["target"], kw["mode"], kw["heave"], kw["heave_epsilon"]
        )

    def test_semua_syarat_terpenuhi(self):
        self.assertTrue(self.engaged())

    def test_belum_pernah_di_set(self):
        # target None = belum pernah menyentuh heave/masuk mode depth-hold.
        # Ini yang membuat masuk STABILIZE tidak menyeret wahana ke setpoint apa pun
        # sebelum target pertama terbentuk.
        self.assertFalse(self.engaged(target=None))

    def test_target_nol_tetap_setpoint_yang_sah(self):
        # Kalau None dan 0.0 tercampur, target tepat di permukaan akan
        # dianggap "belum di-set" dan depth-set diam-diam tidak bekerja.
        self.assertTrue(self.engaged(target=0.0))

    def test_mode_di_luar_depth_hold_modes(self):
        # DEPTH_HOLD_MODES = {STABILIZE, ALT_HOLD}: MANUAL dan mode tak
        # dikenal (None) tetap ditolak; ALT_HOLD sekarang lolos.
        for mode in ("MANUAL", None):
            self.assertFalse(self.engaged(mode=mode), msg=repr(mode))
        self.assertTrue(self.engaged(mode="ALT_HOLD"))

    def test_operator_memegang_stik_heave_menang(self):
        self.assertFalse(self.engaged(heave=21))
        self.assertFalse(self.engaged(heave=-500))

    def test_heave_dalam_deadzone_masih_dianggap_netral(self):
        self.assertTrue(self.engaged(heave=20))
        self.assertTrue(self.engaged(heave=-19))


class TestCallSiteCocokDenganDefinisi(unittest.TestCase):
    """Menyeberangi batas file: rov_agent.py MEMANGGIL, rov_modes.py MENDEFINISIKAN.

    21 Agu 2026: commit "kkkkkkk" mendesain ulang depth-hold jadi auto-follow
    (tanpa saklar ON/OFF operator terpisah — lihat apply_depth_hold_bias) dan
    menghapus handler command depth_set/depth_hold, tapi TIDAK ikut membuang
    parameter `enabled` dari depth_bias_engaged() maupun dari pemanggilnya
    secara konsisten — sempat 4 argumen dikirim ke fungsi yang masih 5
    parameter (TypeError setiap evaluasi). 22 Agu: parameter `enabled` dibuang
    dari KEDUANYA (menyelesaikan redesain yang ditinggal setengah jalan),
    kembali ke 4 parameter murni target/mode/heave/epsilon — bukan
    menghidupkan kembali saklar yang sudah diniatkan mati (lihat commit
    d25194b: "Tidak ada saklar manual lagi").

    73 test lolos semua saat bug lama ada, karena tiap test memanggil fungsi
    ini LANGSUNG dengan argumen yang benar — tak ada satu pun yang melewati
    call site sungguhan. Test ini menjaga supaya arity call site & definisi
    tak pernah menyimpang lagi, di kedua arah. Kelas bug yang sama dengan
    `vert`/`heave` dan `mode`/`control_mode`: dua file tak sepakat, dan tak
    ada test yang menyeberang di antaranya.
    """

    def test_arity_pemanggilan_di_rov_agent_cocok(self):
        src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "rov_agent.py")
        with open(src_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        panggilan = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "depth_bias_engaged"
        ]
        self.assertTrue(
            panggilan,
            "tak ada pemanggilan depth_bias_engaged di rov_agent.py — "
            "kalau memang dipindah, perbarui test ini, jangan hapus")

        wajib = [
            p for p in inspect.signature(depth_bias_engaged).parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        for call in panggilan:
            diberi = len(call.args) + len(call.keywords)
            self.assertEqual(
                diberi, len(wajib),
                f"rov_agent.py baris {call.lineno} mengirim {diberi} argumen ke "
                f"depth_bias_engaged(), padahal definisinya butuh {len(wajib)}: "
                f"{[p.name for p in wajib]}")

    def test_cabang_kontinu_dipagari_depth_bias_is_continuous(self):
        """22 Agu 2026: cabang bias KONTINU di apply_depth_hold_bias sempat
        cuma dipagari depth_bias_engaged(), yang meloloskan ALT_HOLD (lihat
        DEPTH_HOLD_MODES). Akibatnya bias Python terus mendorong z di
        ALT_HOLD, berebut channel dengan cascade PSC_ACCZ_*/VELZ_* ArduSub
        sendiri — dikonfirmasi log trial (bias=+140 z=360 saat mode=ALT_HOLD).
        depth_bias_is_continuous() (STABILIZE saja) sudah ada & dipakai benar
        buat menyalakan pulsa ALT_HOLD (baris ~1162), tapi tak pernah dipakai
        memagari cabang kontinu itu sendiri. Test ini menjaga supaya `if` yang
        memanggil depth_bias_engaged() di rov_agent.py SELALU ikut memanggil
        depth_bias_is_continuous() di kondisi yang sama.
        """
        src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "rov_agent.py")
        with open(src_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        def calls_in(node):
            return {
                n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }

        gerbang = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and "depth_bias_engaged" in calls_in(node.test)
        ]
        self.assertTrue(
            gerbang,
            "tak ada if yang memanggil depth_bias_engaged di rov_agent.py — "
            "kalau memang dipindah, perbarui test ini, jangan hapus")

        for node in gerbang:
            self.assertIn(
                "depth_bias_is_continuous", calls_in(node.test),
                f"rov_agent.py baris {node.lineno}: if depth_bias_engaged(...) "
                "tanpa depth_bias_is_continuous(...) di kondisi yang sama -> "
                "cabang bias kontinu akan ikut menyala di ALT_HOLD lagi")

    def test_auto_follow_heave_dipagari_depth_bias_is_continuous(self):
        """Riwayat berulang: `update_depth_target_from_manual_heave()` (nama
        lama) sudah pernah dihapus (commit 13c1d4a, 10 Agu 2026) karena diam-
        diam menimpa depth_target ke depth aktual tiap stik heave disentuh --
        bertentangan dengan model "hanya SET (depth_up/down) yang boleh ubah
        depth_target". Tapi ditulis ulang tanpa sengaja di redesign 21 Agu
        (8a23060), bikin depth-set kelihatan "dekorasi" di ALT_HOLD: apa pun
        yang diatur lewat depth_up/down langsung tertimpa begitu stik heave
        disentuh. 22 Agu: dipersempit lagi ke STABILIZE saja (di sana
        depth_target satu-satunya yang menahan kedalaman, jadi harus ikut
        stik supaya tak "menyelam balik" begitu stik dilepas). Di ALT_HOLD,
        cascade ArduSub sendiri yang menahan -- depth_target cuma dipakai
        sebagai arah pulsa depth_up/down, jadi harus diam kecuali tombol itu
        ditekan. Test ini menjaga assignment auto-follow selalu dipagari
        depth_bias_is_continuous(), supaya tak tertulis ulang polos lagi.
        """
        src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "rov_agent.py")
        with open(src_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        func = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)
             and n.name == "apply_depth_hold_bias"),
            None)
        self.assertIsNotNone(
            func, "tak ada apply_depth_hold_bias di rov_agent.py -- "
            "kalau dipindah/di-rename, perbarui test ini")

        def calls_in(node):
            return {
                n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }

        def is_follow_assign(node):
            if not (isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "depth_target"
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "clamp_depth_target"
                    and node.value.args):
                return False
            arg0 = node.value.args[0]
            return (isinstance(arg0, ast.Subscript)
                    and isinstance(arg0.value, ast.Name)
                    and arg0.value.id == "state")

        assigns = [n for n in ast.walk(func) if is_follow_assign(n)]
        self.assertTrue(
            assigns,
            "tak ada assignment depth_target = clamp_depth_target(state[...], ...) "
            "di apply_depth_hold_bias -- kalau memang dihapus/dipindah, "
            "perbarui test ini, jangan hapus")

        dipagari = [
            node for node in ast.walk(func)
            if isinstance(node, ast.If)
            and "depth_bias_is_continuous" in calls_in(node.test)
            and any(a in list(ast.walk(node)) for a in assigns)
        ]
        self.assertTrue(
            dipagari,
            "assignment auto-follow depth_target di apply_depth_hold_bias "
            "tidak dipagari depth_bias_is_continuous(...) -- ALT_HOLD akan "
            "ikut menimpa depth_target tiap stik heave disentuh lagi")


if __name__ == "__main__":
    unittest.main()
