"""Otoritas axis: siapa yang menggerakkan ROV saat autonomous.

Trial-bug 7 Sep 2026: control_main.py mengirim axis autonomous sebagai perintah
`surge/sway/yaw/heave` biasa. Di Pi semuanya mendarat di dict `joystick`, dan
joystick_sender() membaca dict itu sebagai stik operator — sehingga CASE gerak
pertama (surge=-500) langsung memicu kill-switch dan membatalkan autonomous.

Perbaikannya melintasi TIGA file, jadi test ini juga melintasinya:

    control_main.py  menandai frame  src="fsm" / "operator"
    server.js        meneruskan tag itu apa adanya ke Pi
    rov_agent.py     memilih fsm_axes vs joystick DARI TAG, bukan dari IP

Kelas bug yang sama dengan killswitch-src-tag-not-ip: dua bagian tak sepakat
soal asal perintah, dan tak ada test yang menyeberang di antaranya.

    python3 -m unittest test_control_authority -v
"""

import ast
import importlib.util
import os
import re
import time
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_control_main():
    """control_main.py murni stdlib dan tidak memulai thread saat diimpor."""
    path = os.path.join(ROOT, "server", "control_main.py")
    spec = importlib.util.spec_from_file_location("control_main_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TagAsalFrame(unittest.TestCase):
    """control_main.py harus menandai setiap frame motion dengan asalnya."""

    def setUp(self):
        self.cm = _load_control_main()
        self.sent = []
        self.cm.send_packet = self.sent.append

    def _stik(self, surge=0, sway=0, yaw=0, heave=0, umur=0.0):
        self.cm.joystick.update(
            {"surge": surge, "sway": sway, "yaw": yaw, "heave": heave})
        self.cm.last_joystick_time = time.time() - umur

    def _motion(self):
        return [p for p in self.sent if p["type"] == "control"]

    def test_manual_tidak_dikirim_control_main(self):
        """MANUAL milik GUI, bukan control_main.

        Axis manual datang dari pollGamepad -> WS -> server.js -> Pi supaya
        profil joystick operator tetap berlaku. Kalau control_main ikut
        mengirim, dua sumber menulis dict joystick yang sama di Pi dan stik
        terasa aneh (nilai adu, deadzone/expo ganda).
        """
        self.assertFalse(hasattr(self.cm, "manual_control"))

        self._stik(surge=300)
        self.sent.clear()

        # Satu tick MANUAL: tidak boleh ada frame motion sama sekali.
        self.assertEqual(self.cm.get_mode(), self.cm.MODE_MANUAL)
        self.assertEqual(self._motion(), [])

    def test_stik_tetap_dibaca_untuk_abort(self):
        """joystick.py tetap perlu: satu-satunya kill-switch saat autonomous."""
        self.cm.set_mode(self.cm.MODE_AUTONOMOUS)
        self._stik(surge=400)
        self.sent.clear()

        self.cm.autonomous_control()

        self.assertEqual(self.cm.get_mode(), self.cm.MODE_MANUAL)

    def test_frame_autonomous_bertag_fsm(self):
        self.cm.set_mode(self.cm.MODE_AUTONOMOUS)
        self.sent.clear()
        self.cm.autonomous_control()

        # Tanpa tag ini axis autonomous mendarat di dict joystick milik Pi.
        self.assertEqual(self._motion()[-1]["src"], "fsm")

    def test_frame_autonomous_bergerak_tetap_bertag_fsm(self):
        """CASE 4 mengirim surge literal melalui jalur FSM."""
        self.cm.set_mode(self.cm.MODE_AUTONOMOUS)
        self.cm.auto_index = 4
        self.cm.vehicle_state = {"depth": 1.0, "armed": True}
        self.cm.last_vehicle_time = time.monotonic()
        self.cm.auto_step_start = time.monotonic()

        self.sent.clear()
        self.cm.autonomous_control()

        frame = self._motion()[-1]
        self.assertEqual(frame["src"], "fsm")
        self.assertTrue(
            any(frame[axis] for axis in ("surge", "sway", "yaw", "heave")),
            "CASE gerak tidak menghasilkan axis non-netral sama sekali")


    def test_mode_autonomous_dipublikasikan_setelah_reset_siap(self):
        from unittest.mock import patch
        modes_during_reset = []
        reset = self.cm.autonomous_reset

        def check_reset():
            modes_during_reset.append(self.cm.get_mode())
            reset()

        with patch.object(self.cm, "autonomous_reset", side_effect=check_reset):
            self.cm.set_mode(self.cm.MODE_AUTONOMOUS)
        self.assertEqual(modes_during_reset, [self.cm.MODE_MANUAL])
        self.assertIsNotNone(self.cm.mission_cfg)
        self.assertEqual(self.cm.get_mode(), self.cm.MODE_AUTONOMOUS)


class KillSwitchOperator(unittest.TestCase):
    """Stik F310 harus tetap bisa membatalkan autonomous — dari control_main.

    Sesudah axis autonomous dipisahkan ke fsm_axes, kill-switch di Pi tak akan
    pernah melihat stik operator lagi (server.js menolak axis GUI). Abort karena
    itu WAJIB dipicu di control_main, satu-satunya tempat F310 masih terbaca.
    """

    def setUp(self):
        self.cm = _load_control_main()
        self.sent = []
        self.cm.send_packet = self.sent.append
        self.cm.set_mode(self.cm.MODE_AUTONOMOUS)
        self.sent.clear()

    def _dorong(self, surge, umur=0.0):
        self.cm.joystick.update({"surge": surge, "sway": 0, "yaw": 0, "heave": 0})
        self.cm.last_joystick_time = time.time() - umur
        self.cm.autonomous_control()

    def test_dorongan_nyata_membatalkan_autonomous(self):
        self._dorong(500)

        self.assertEqual(self.cm.get_mode(), self.cm.MODE_MANUAL)
        abort = [p for p in self.sent
                 if p["type"] == "command" and p["name"] == "control_mode"]
        # Pi harus ikut pindah; kalau tidak, ia tetap mengira dirinya autonomous.
        self.assertEqual([p["value"] for p in abort], ["manual"])

    def test_jitter_di_bawah_deadzone_tidak_membatalkan(self):
        self._dorong(self.cm.OPERATOR_ABORT_DEADZONE)

        self.assertEqual(self.cm.get_mode(), self.cm.MODE_AUTONOMOUS)

    def test_stik_basi_tidak_membatalkan(self):
        # joystick.py mati sambil stik terdorong: nilainya beku, bukan perintah.
        self._dorong(500, umur=self.cm.JOYSTICK_TIMEOUT + 0.1)

        self.assertEqual(self.cm.get_mode(), self.cm.MODE_AUTONOMOUS)


class PenerusTag(unittest.TestCase):
    """Tag tidak boleh hilang di dua hop berikutnya."""

    def test_rov_agent_memilih_tujuan_axis_dari_tag(self):
        with open(os.path.join(ROOT, "rov_agent.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())

        # Cabang `elif name in AXIS_RANGE:` di command_listener.
        cabang = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.ops[0], ast.In)
            and getattr(node.test.comparators[0], "id", None) == "AXIS_RANGE"
        ]
        self.assertEqual(len(cabang), 1, "cabang axis di command_listener pindah")

        src = ast.dump(cabang[0])
        self.assertIn("'fsm'", src, "axis tidak lagi dirutekan lewat tag src")
        self.assertIn("fsm_axes", src, "axis src='fsm' tidak masuk fsm_axes")
        self.assertIn("joystick", src, "axis operator tidak masuk dict joystick")

    def test_server_js_meneruskan_src_ke_pi(self):
        with open(os.path.join(ROOT, "server", "server.js"), encoding="utf-8") as f:
            js = f.read()

        # Satu hop pass-through, tapi kalau argumen ini hilang seluruh
        # pemisahan otoritas ikut mati tanpa satu pun error.
        self.assertTrue(
            re.search(r"sendToRpi\(\s*name\s*,\s*value\s*,\s*msg\.src\s*\)", js),
            "relay motion tidak lagi meneruskan msg.src")
        self.assertIn("command.src = src", js)


if __name__ == "__main__":
    unittest.main()
