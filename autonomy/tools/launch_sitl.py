#!/usr/bin/env python3
"""
tools/launch_sitl.py — Peluncur SATU-PERINTAH: vehicle (mock/SITL) -> rov_link -> GUI (-> FSM).

Menggantikan alur manual 3-4 terminal di README_SETUP_C.md / SITL_SETUP.md dengan
SATU perintah (ROADMAP_MISI5.md, Fase 1):

    [vehicle: sitl_mock.py ATAU ArduSub SITL eksternal via --vehicle sitl]
        --MAVLink udpout:14555-->  rov_link.py  --cmd JSON :14550-->  server.js (GUI LIVE)
                                                 <--telem JSON :14551--
                                                 --telem JSON :14552--> fsm/mission5.py (opsional, --fsm)

Pakai:
    python tools/launch_sitl.py                          # mock + rov_link + GUI (default, tercepat)
    python tools/launch_sitl.py --vehicle sitl            # ArduSub SITL WSL2 sudah jalan terpisah
                                                           # (lihat SITL_SETUP.md / tools/run_sitl.sh)
    python tools/launch_sitl.py --fsm --start-state M5_REDIVE --vision mock
    python tools/launch_sitl.py --no-gui                  # tanpa server.js (mis. GUI sudah jalan)
    python tools/launch_sitl.py --vehicle none --mavlink /dev/ttyACM0 --no-gui  # Pixhawk asli via USB
    python tools/launch_sitl.py --vehicle none --mavlink /dev/ttyACM0 --fsm --vision usb --calib vision/calibration/dwe.npz --no-gui  # pool trial

Keluaran tiap proses anak diberi label warna [VEHICLE]/[ROV_LINK]/[GUI]/[FSM] di satu
terminal. Tekan Ctrl+C SEKALI untuk menghentikan SEMUA proses dengan rapi. Bila salah
satu proses berhenti sendiri (crash), semua proses lain ikut dihentikan — supaya tak
ada yang "nyangkut" setengah jalan tanpa disadari.

CATATAN: jeda antar tahap (--delay) adalah jeda TETAP, bukan sinkronisasi ketat
(tak menunggu pesan "terhubung" secara aktif). Naikkan --delay bila komputer lambat.
"""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time

AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AUTONOMY)
SERVER_DIR = os.path.join(REPO_ROOT, "server")

_COLORS = {"VEHICLE": "\033[36m", "ROV_LINK": "\033[33m", "GUI": "\033[35m", "FSM": "\033[32m"}
_RESET = "\033[0m"


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Peluncur satu-perintah Fase 1: vehicle -> rov_link -> GUI (-> FSM)")
    ap.add_argument("--vehicle", choices=["mock", "sitl", "none"], default="mock",
                    help="mock=sitl_mock.py (default, TANPA WSL) | sitl=ArduSub SITL sudah "
                         "jalan terpisah -> skrip ini TAK menjalankan apa pun di slot ini | "
                         "none=lewati (mis. hardware Pixhawk asli via --mavlink serial)")
    ap.add_argument("--mavlink", default="udpin:0.0.0.0:14555",
                    help="endpoint MAVLink rov_link (default cocok utk mock & SITL lokal; "
                         "utk hardware asli mis. /dev/ttyACM0 dgn --vehicle none)")
    ap.add_argument("--baud", type=int, default=115200, help="baud jika --mavlink serial")
    ap.add_argument("--server-ip", default="127.0.0.1",
                    help="IP tujuan telemetri rov_link & RPI_ADDR utk GUI")
    ap.add_argument("--telem-port", type=int, default=14551)
    ap.add_argument("--cmd-port", type=int, default=14550)
    ap.add_argument("--fsm-telem-port", type=int, default=14552,
                    help="port fan-out telemetri tambahan utk FSM (dipakai bila --fsm)")
    ap.add_argument("--no-gui", action="store_true", help="lewati start server.js (GUI)")
    ap.add_argument("--no-rov-link", action="store_true",
                    help="lewati start rov_link.py (mis. rov_link sudah jalan terpisah, "
                         "hanya ingin iterasi ulang --fsm lawan-nya)")
    ap.add_argument("--fsm", action="store_true", help="sekalian jalankan fsm/mission5.py")
    ap.add_argument("--start-state", default="DIVE", choices=["DIVE", "M5_REDIVE", "M5_DOCK"],
                    help="dipakai bila --fsm")
    ap.add_argument("--vision", default="mock", choices=["mock", "usb", "rtsp"],
                    help="dipakai bila --fsm")
    ap.add_argument("--config", default=None,
                    help="dipakai bila --fsm: path config tuning .yaml/.yml/.json")
    ap.add_argument("--calib", default=None,
                    help="dipakai bila --fsm: path kalibrasi kamera .npz utk mode PBVS (solvePnP)")
    ap.add_argument("--no-wait-autonomous", action="store_true",
                    help="dipakai bila --fsm: langsung jalan tanpa menunggu toggle GUI")
    ap.add_argument("--delay", type=float, default=1.5,
                    help="jeda tetap (detik) antar tahap start (vehicle->rov_link->GUI/FSM)")
    return ap


def build_plan(args):
    """Bangun daftar (nama, cmd, cwd, extra_env) — TANPA menjalankan apa pun.
    Dipisah dari eksekusi supaya bisa diuji murni (lihat tests/test_launch_sitl.py)."""
    py = sys.executable
    plan = []

    if args.vehicle == "mock":
        mock_cmd = [py, "sitl_mock.py", "--mavlink", "udpout:127.0.0.1:14555"]
        if args.fsm:
            mock_cmd += ["--telem-extra", f"127.0.0.1:{args.fsm_telem_port}"]
        plan.append(("VEHICLE", mock_cmd, AUTONOMY, None))
    elif args.vehicle == "sitl":
        pass  # ArduSub SITL diasumsikan sudah jalan terpisah (tools/run_sitl.sh)

    if not args.no_rov_link:
        rov_link_cmd = [py, "rov_link.py",
                        "--server", args.server_ip,
                        "--telem-port", str(args.telem_port),
                        "--json-rx-port", str(args.cmd_port),
                        "--mavlink", args.mavlink,
                        "--baud", str(args.baud),
                        # --vision berlaku untuk KEDUA jalur FSM, bukan cuma proses
                        # terpisah di bawah. rov_link punya jalurnya sendiri:
                        # toggle GUI Manual->Autonomous memanggil start_mission5()
                        # yang menjalankan FSM in-process. Tanpa baris ini
                        # start_mission5 jatuh ke default "usb" (rov_link.py:263),
                        # jadi `--vision mock` diam-diam berarti "mock untuk proses
                        # FSM, webcam untuk FSM hasil toggle" — dua arti berbeda
                        # untuk satu flag, dan yang kedua menggantung di mesin
                        # tanpa kamera.
                        "--fsm-vision-source", args.vision]
        if args.fsm:
            rov_link_cmd += ["--telem-extra", f"127.0.0.1:{args.fsm_telem_port}"]
            # FSM di bawah adalah proses TERPISAH, jadi tak ada yang mengirim
            # control_mode=autonomous ke rov_link. Tanpa flag ini gerbang
            # anti-frame-basi di handle_command() membuang seluruh perintahnya.
            rov_link_cmd += ["--external-fsm"]
        plan.append(("ROV_LINK", rov_link_cmd, AUTONOMY, None))

    if not args.no_gui:
        npm_path = shutil.which("npm") or ("npm.cmd" if os.name == "nt" else "npm")
        plan.append(("GUI", [npm_path, "start"], SERVER_DIR, {"RPI_ADDR": args.server_ip}))

    if args.fsm:
        fsm_cmd = [py, os.path.join("fsm", "mission5.py"),
                  "--server", "127.0.0.1",
                  "--cmd-port", str(args.cmd_port),
                  "--telem-port", str(args.fsm_telem_port),
                  "--vision", args.vision,
                  "--start-state", args.start_state]
        if args.config:
            fsm_cmd += ["--config", args.config]
        if args.calib:
            fsm_cmd += ["--calib", args.calib]
        if args.no_wait_autonomous:
            fsm_cmd += ["--no-wait-autonomous"]
        plan.append(("FSM", fsm_cmd, AUTONOMY, None))

    return plan


def _stream(name, proc):
    color = _COLORS.get(name, "")
    prefix = f"{color}[{name}]{_RESET} "
    for line in iter(proc.stdout.readline, b""):
        text = line.decode(errors="replace").rstrip()
        print(prefix + text, flush=True)


class Launcher:
    def __init__(self):
        self.procs = []  # [(name, Popen)]

    def spawn(self, name, cmd, cwd=None, extra_env=None):
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")   # log Python child langsung tampil (pipe = block-buffered)
        if extra_env:
            env.update(extra_env)
        print(f"{_COLORS.get(name, '')}[{name}]{_RESET} $ {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.procs.append((name, proc))
        threading.Thread(target=_stream, args=(name, proc), daemon=True).start()
        return proc

    def wait_any_exit(self):
        """Blok sampai satu proses berhenti SENDIRI (indikasi crash) atau Ctrl+C."""
        try:
            while True:
                for name, proc in self.procs:
                    code = proc.poll()
                    if code is not None:
                        print(f"\n[launch_sitl] proses '{name}' berhenti (exit={code}) "
                              "— mematikan proses lain...")
                        return
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[launch_sitl] Ctrl+C — mematikan semua proses...")

    def shutdown(self):
        for name, proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + 5
        for name, proc in self.procs:
            try:
                proc.wait(timeout=max(0, deadline - time.time()))
            except subprocess.TimeoutExpired:
                print(f"[launch_sitl] '{name}' tak berhenti, paksa kill...")
                proc.kill()
        print("[launch_sitl] Semua proses dihentikan.")


def main():
    args = build_arg_parser().parse_args()
    plan = build_plan(args)

    if not plan:
        print("[launch_sitl] Tak ada proses yang perlu dijalankan (cek "
              "--vehicle/--no-gui/--fsm) — tidak ada yang dilakukan.", file=sys.stderr)
        return 1

    if not args.no_gui and not os.path.isdir(os.path.join(SERVER_DIR, "node_modules")):
        print(f"[launch_sitl] PERINGATAN: {SERVER_DIR}/node_modules tak ada — "
              f"jalankan `npm install` di folder server/ dulu.", file=sys.stderr)

    L = Launcher()
    prev_name = None
    for name, cmd, cwd, extra_env in plan:
        if prev_name in ("VEHICLE", "ROV_LINK"):
            time.sleep(args.delay)
        L.spawn(name, cmd, cwd=cwd, extra_env=extra_env)
        prev_name = name

    print(f"\n[launch_sitl] {len(plan)} proses jalan. Ctrl+C SEKALI utk berhenti semua.\n")
    L.wait_any_exit()
    L.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
