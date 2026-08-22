"""Regresi toggle autonomous di rov_agent.py — menyeberangi batas file.

Trial 22 Agu 2026, run 3 & 4: ROV tidak bergerak sedetik pun sesudah AUTONOMOUS
ditekan. Journal Pi menunjukkan urutannya:

    11:48:05  [CMD] control_mode = autonomous
    11:48:05  [KILL-SWITCH] stik operator digerakkan saat autonomous
    11:48:06  [M5] Mission5 FSM dimulai

Dua cacat, keduanya di handler `control_mode`:

  1. dict `joystick` menyimpan nilai TERAKHIR dari GUI dan tak pernah pulang ke
     nol sendiri. Sisa >KILL_SWITCH_DEADZONE dari sesi manual sebelumnya dibaca
     joystick_sender sebagai "operator nyetir" pada iterasi pertama.
  2. `current_control_mode` diset SEBELUM runner.start(); selama ~1 detik
     VisionPipeline membuka kamera, stop() dari kill-switch menemukan _fsm masih
     None dan diam — meninggalkan thread FSM yatim.

Diuji di tingkat AST karena rov_agent.py butuh pymavlink/socket untuk diimpor.
Kelas bug yang sama dengan vert/heave dan arity depth_bias_engaged: dua bagian
tak sepakat, dan tak ada test yang menyeberang di antaranya.

    python3 -m unittest test_rov_agent_autonomous -v
"""

import ast
import os
import unittest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rov_agent.py")


def _cabang_autonomous():
    """Node `if requested == "autonomous":` di handler control_mode."""
    with open(SRC, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if (isinstance(t, ast.Compare)
                and isinstance(t.left, ast.Name) and t.left.id == "requested"
                and isinstance(t.comparators[0], ast.Constant)
                and t.comparators[0].value == "autonomous"):
            return node
    raise AssertionError("cabang toggle autonomous tak ditemukan di rov_agent.py")


class TestToggleAutonomous(unittest.TestCase):
    def setUp(self):
        # HANYA body cabang autonomous — orelse-nya adalah jalur manual, yang
        # juga menetapkan current_control_mode dan akan mengacaukan hitungan.
        self.cabang = _cabang_autonomous().body
        self.src = "\n".join(ast.unparse(n) for n in self.cabang)

    def test_axis_operator_dinolkan_saat_masuk_autonomous(self):
        # Tanpa ini, kill-switch memicu pada nilai BASI, bukan gerakan baru.
        self.assertIn("joystick.update", self.src,
                      "axis operator tidak dinolkan saat masuk autonomous — "
                      "kill-switch akan abort pada sisa nilai sesi sebelumnya")
        self.assertIn("fsm_axes.update", self.src)

    def test_mode_dipindah_sesudah_start_bukan_sebelum(self):
        assign = [n for b in self.cabang for n in ast.walk(b)
                  if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name)
                          and t.id == "current_control_mode" for t in n.targets)]
        self.assertEqual(len(assign), 1, "harus tepat satu penetapan mode di cabang ini")

        start = [n for b in self.cabang for n in ast.walk(b)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "start"]
        self.assertTrue(start, "runner.start() tak dipanggil di cabang autonomous")
        self.assertGreater(
            assign[0].lineno, max(c.lineno for c in start),
            "current_control_mode diset SEBELUM start() — jendela balapan "
            "kill-switch vs FSM yatim (journal 22 Agu 11:48:05)")


if __name__ == "__main__":
    unittest.main()
