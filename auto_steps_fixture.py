"""Tabel AUTO_STEPS uji. Test membaca file ini, BUKAN tabel misi asli di
server/control_main.py, supaya angka misi boleh diubah tanpa merusak test."""

AUTO_STEPS = [
    (3.0, 0, 0, 0, 0, None, None),
    (2.0, 0, 0, 0, 0, None, None),
    (1.0, 0, 0, 0, 0, None, None),
    (2.0, 0, 0, 0, 0, None, 1.0),
    (3.0, -500, 0, 0, 0, None, None),
    (1.0, 0, 0, 0, 0, None, None),
]
