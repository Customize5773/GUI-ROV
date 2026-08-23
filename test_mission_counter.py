"""Unit test counter trial Misi 2/3 di rov_agent.py (Guidebook KKI 2026 §4.7.4).

rov_agent.py butuh pymavlink/socket untuk diimpor langsung (lihat
test_rov_agent_autonomous.py), jadi `_tier_score` diekstrak & dijalankan
terisolasi lewat AST, dan handler command `mission_counter` diperiksa di
tingkat AST juga.

    python3 -m unittest test_mission_counter -v
"""

import ast
import os
import unittest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rov_agent.py")

with open(SRC, encoding="utf-8") as f:
    _TREE = ast.parse(f.read())


def _tier_score_func():
    for node in ast.walk(_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == "_tier_score":
            return node
    raise AssertionError("_tier_score tak ditemukan di rov_agent.py")


def _mission_counter_branch():
    for node in ast.walk(_TREE):
        if (isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "name"
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "mission_counter"):
            return node
    raise AssertionError("cabang command mission_counter tak ditemukan di rov_agent.py")


class TestTierScore(unittest.TestCase):
    def setUp(self):
        ns = {}
        exec(compile(ast.Module(body=[_tier_score_func()], type_ignores=[]), SRC, "exec"), ns)
        self.tier_score = ns["_tier_score"]

    def test_trial_1_15_poin(self):
        self.assertEqual(self.tier_score(0), 15)

    def test_trial_2_10_poin(self):
        self.assertEqual(self.tier_score(1), 10)

    def test_trial_lebih_dari_2_5_poin(self):
        self.assertEqual(self.tier_score(2), 5)
        self.assertEqual(self.tier_score(9), 5)


class TestMissionCounterHandler(unittest.TestCase):
    def setUp(self):
        self.src = ast.unparse(_mission_counter_branch())

    def test_reset_menolkan_kedua_misi(self):
        self.assertIn("mission_counter_fails['m2'] = 0", self.src)
        self.assertIn("mission_counter_fails['m3'] = 0", self.src)

    def test_fail_menambah_hanya_misi_terkait(self):
        self.assertIn('mission_counter_fails[mission] += 1', self.src)

    def test_hasil_ditulis_ke_state_untuk_telemetri(self):
        self.assertIn("state['mission_counter']", self.src)


if __name__ == "__main__":
    unittest.main()
