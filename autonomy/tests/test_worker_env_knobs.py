"""tests/test_worker_env_knobs.py — HOOK_VISION_*/QR_VISION_* harus benar-benar
membaca os.environ, bukan cuma disebut di komentar.

Ditemukan 7 Sep 2026 (A5): QR_VISION_CONF sudah disebut di komentar
qr_vision_worker.py sejak awal ("setel lewat QR_VISION_CONF"), tapi
`grep -r QR_VISION_CONF` di seluruh repo cuma menemukan komentar itu sendiri —
tak ada satu `os.environ.get(...)` pun. Unit systemd menghardcode `--conf 0.6`
langsung di ExecStart, jadi menuning ambang ini di lapangan berarti mengedit
file unit di Pi, bukan mengedit .env seperti aturan repo ini. Test ini
memastikan knob-nya benar-benar ada, dan bahwa CLI flag tetap menang atas env
(supaya unit systemd yang SUDAH menulis --conf/--imgsz/--fps eksplisit tidak
diam-diam berubah perilaku saat file ini disentuh).
"""
import os
import subprocess
import sys

import pytest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_AUTONOMY = os.path.dirname(_TESTS)

WORKERS = {
    "hook_vision_worker": {"HOOK_VISION_IMGSZ": ("--imgsz", "imgsz", int),
                           "HOOK_VISION_FPS": ("--fps", "fps", float)},
    "qr_vision_worker": {"QR_VISION_CONF": ("--conf", "conf", float),
                        "QR_VISION_IMGSZ": ("--imgsz", "imgsz", int),
                        "QR_VISION_FPS": ("--fps", "fps", float)},
}

# Argumen WAJIB tiap worker, diisi nilai bohong sekadar supaya parser tak
# menolak sebelum sempat memeriksa default --imgsz/--fps/--conf yang diuji.
REQUIRED_ARGS = {
    "hook_vision_worker": ["--camera", "x", "--model", "x", "--map", "x", "--calib", "x"],
    "qr_vision_worker": ["--camera", "x", "--model", "x"],
}


def _parse_args(worker, argv, env):
    """Jalankan build_arg_parser() modul worker di SUBPROCESS dgn env dipaksa.

    Subprocess, bukan import-in-process: argparse default dievaluasi SEKALI
    saat modul di-exec, jadi memaksa os.environ setelah import tak berpengaruh
    — harus proses baru per skenario env, persis seperti systemd start ulang.
    """
    code = (
        "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "from tools import %s as w\n"
        "ns = w.build_arg_parser().parse_args(%r)\n"
        "for name in %r:\n"
        "    print(name, getattr(ns, name))\n"
    ) % (_AUTONOMY, os.path.dirname(_AUTONOMY), worker,
        REQUIRED_ARGS[worker] + list(argv),
        [dest for _, dest, _ in WORKERS[worker].values()])
    full_env = dict(os.environ)
    full_env.update(env)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, env=full_env, timeout=30)
    return result


@pytest.mark.parametrize("worker,env_var", [
    (w, ev) for w, knobs in WORKERS.items() for ev in knobs
])
def test_env_var_is_actually_read(worker, env_var):
    """Bukan grep komentar — jalankan parsernya dan buktikan env berpengaruh."""
    flag, dest, cast = WORKERS[worker][env_var]
    probe_value = "0.3702" if cast is float else "111"
    result = _parse_args(worker, [], {env_var: probe_value})
    assert result.returncode == 0, result.stderr
    assert f"{dest} {cast(probe_value)}" in result.stdout, (
        f"{env_var} diset tapi {dest} tidak ikut berubah: {result.stdout!r}")


@pytest.mark.parametrize("worker,env_var", [
    (w, ev) for w, knobs in WORKERS.items() for ev in knobs
])
def test_cli_flag_still_wins_over_env(worker, env_var):
    """Unit systemd yang SUDAH menulis --conf/--imgsz/--fps eksplisit tidak
    boleh diam-diam berubah perilaku begitu env plumbing ini ditambahkan."""
    flag, dest, cast = WORKERS[worker][env_var]
    cli_value = "0.9999" if cast is float else "999"
    env_decoy = "0.0001" if cast is float else "1"
    result = _parse_args(worker, [flag, cli_value], {env_var: env_decoy})
    assert result.returncode == 0, result.stderr
    assert f"{dest} {cast(cli_value)}" in result.stdout
