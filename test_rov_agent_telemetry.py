"""Regresi: telemetry ke GUI tidak boleh terikat pada loop RX MAVLink.

Gejala di lapangan: badge dashboard bergantian "Telemetri terputus" /
"Telemetri pulih" berulang kali, padahal umbilical dan laptop sehat.

Sebabnya struktural. `send_telemetry()` dulu adalah pernyataan TERAKHIR di
dalam `while True` loop RX MAVLink di main(), sementara di atasnya ada empat
`continue`:

    while True:
        if master is None:
            connect_pixhawk()        # wait_heartbeat(timeout=30) -> BLOKIR 30 dtk
            ...
            continue                 # <- telemetry dilewati
        msg = master.recv_match(blocking=True, timeout=1)
        ...
        if msg is None:
            continue                 # <- telemetry dilewati
        ...
        send_telemetry()             # hanya tercapai kalau ADA pesan MAVLink

Akibatnya laju telemetry mengikuti kedatangan pesan MAVLink, bukan timer
(terukur 9,02 Hz pada rekaman trial, bukan 10), dan satu gangguan kabel FC
memadamkan dashboard sampai 30 detik.

Diuji di tingkat AST karena rov_agent.py butuh pymavlink/socket untuk diimpor
— konvensi yang sama dengan test_rov_agent_autonomous.py.

    python3 -m unittest test_rov_agent_telemetry -v
"""

import ast
import os
import unittest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rov_agent.py")


def _tree():
    with open(SRC, encoding="utf-8") as f:
        return ast.parse(f.read())


def _fungsi(tree, nama):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == nama:
            return node
    raise AssertionError(f"fungsi {nama}() tak ditemukan di rov_agent.py")


def _panggilan(node):
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


class TestTelemetryTerpisahDariLoopMavlink(unittest.TestCase):
    def setUp(self):
        self.tree = _tree()
        self.main = _fungsi(self.tree, "main")

    def test_send_telemetry_tidak_dipanggil_di_loop_rx(self):
        loops = [n for n in ast.walk(self.main) if isinstance(n, ast.While)]
        self.assertTrue(loops, "loop RX di main() tak ditemukan")
        for loop in loops:
            self.assertNotIn(
                "send_telemetry", _panggilan(loop),
                "send_telemetry() kembali dipanggil dari loop RX MAVLink — "
                "setiap `continue` di atasnya akan membatalkan telemetry, dan "
                "sambung-ulang FC memadamkan dashboard sampai 30 detik")

    def test_ada_thread_telemetry_sendiri(self):
        self.assertTrue(
            any(isinstance(n, ast.FunctionDef) and n.name == "telemetry_sender"
                for n in self.tree.body),
            "telemetry_sender() hilang — telemetry butuh thread berlaju timer")

    def test_thread_telemetry_dijalankan_di_main(self):
        target = [
            kw.value.id
            for n in ast.walk(self.main) if isinstance(n, ast.Call)
            for kw in n.keywords
            if kw.arg == "target" and isinstance(kw.value, ast.Name)
        ]
        self.assertIn("telemetry_sender", target,
                      "thread telemetry tidak pernah di-start di main()")

    def test_telemetry_sender_mengirim_dalam_loop_tak_berujung(self):
        fn = _fungsi(self.tree, "telemetry_sender")
        loops = [n for n in ast.walk(fn) if isinstance(n, ast.While)]
        self.assertTrue(loops, "telemetry_sender harus berupa loop")
        self.assertIn("send_telemetry", _panggilan(loops[0]))

    def test_kegagalan_satu_paket_tidak_mematikan_thread(self):
        fn = _fungsi(self.tree, "telemetry_sender")
        handlers = [h for n in ast.walk(fn) if isinstance(n, ast.Try)
                    for h in n.handlers]
        self.assertTrue(
            any(isinstance(h.type, ast.Name) and h.type.id == "Exception"
                for h in handlers),
            "send_telemetry() tidak dibungkus try/except — satu kegagalan "
            "mematikan thread dan GUI kehilangan telemetry untuk selamanya")


class TestPenandaLinkPixhawk(unittest.TestCase):
    """Telemetry yang terus mengalir saat FC putus berisi angka BEKU.

    Tanpa penanda, dashboard menampilkannya seolah bacaan hidup — itu regresi
    keselamatan, bukan perbaikan. fc_link adalah penandanya.
    """

    def setUp(self):
        self.tree = _tree()
        with open(SRC, encoding="utf-8") as f:
            self.src = f.read()

    def test_fc_link_ada_di_state_awal(self):
        self.assertIn('"fc_link"', self.src,
                      "state tidak punya fc_link — GUI tak bisa membedakan "
                      "link FC putus dari telemetry putus")

    def test_drop_link_menandai_down(self):
        fn = _fungsi(self.tree, "drop_link")
        src = ast.unparse(fn)
        self.assertIn("state['fc_link'] = 'down'", src.replace('"', "'"),
                      "drop_link tidak menandai fc_link=down")

    def test_connect_pixhawk_menandai_ok(self):
        fn = _fungsi(self.tree, "connect_pixhawk")
        src = ast.unparse(fn).replace('"', "'")
        self.assertIn("state['fc_link'] = 'ok'", src,
                      "connect_pixhawk tidak memulihkan fc_link=ok")

    def test_fc_link_ok_hanya_sesudah_heartbeat(self):
        # Menandai "ok" sebelum wait_heartbeat berarti GUI percaya angka beku
        # selama 30 detik pertama sambung-ulang.
        fn = _fungsi(self.tree, "connect_pixhawk")
        baris = [ast.unparse(n).replace('"', "'") for n in fn.body]
        idx_ok = next(i for i, b in enumerate(baris) if "state['fc_link'] = 'ok'" in b)
        idx_hb = next(i for i, b in enumerate(baris) if "wait_heartbeat" in b)
        self.assertGreater(idx_ok, idx_hb,
                           "fc_link diset 'ok' sebelum heartbeat dipastikan")


if __name__ == "__main__":
    unittest.main()
