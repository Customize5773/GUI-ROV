"""
tests/test_launch_sitl.py — Uji `tools/launch_sitl.py` TANPA menjalankan proses nyata.

`build_plan()` dipisah dari eksekusi (subprocess.Popen) justru supaya logika
penyusunan command bisa diuji murni: benar jumlah proses, urutan, flag, & env
utk tiap kombinasi --vehicle/--no-gui/--fsm — tanpa butuh npm/node/pymavlink jalan.
"""
import importlib.util
import os
import sys

import pytest

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTONOMY not in sys.path:
    sys.path.insert(0, _AUTONOMY)

_SPEC = importlib.util.spec_from_file_location(
    "launch_sitl", os.path.join(_AUTONOMY, "tools", "launch_sitl.py"))
launch_sitl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(launch_sitl)


def _parse(argv):
    return launch_sitl.build_arg_parser().parse_args(argv)


def _names(plan):
    return [p[0] for p in plan]


def test_default_plan_is_vehicle_rovlink_gui_in_order():
    plan = launch_sitl.build_plan(_parse([]))
    assert _names(plan) == ["VEHICLE", "ROV_LINK", "GUI"]


def test_vehicle_mock_command_targets_loopback_and_default_port():
    plan = launch_sitl.build_plan(_parse([]))
    vehicle_cmd = dict((n, c) for n, c, _, _ in plan)["VEHICLE"]
    assert "--mavlink" in vehicle_cmd
    assert vehicle_cmd[vehicle_cmd.index("--mavlink") + 1] == "udpout:127.0.0.1:14555"


def test_vehicle_sitl_skips_vehicle_process():
    plan = launch_sitl.build_plan(_parse(["--vehicle", "sitl"]))
    assert _names(plan) == ["ROV_LINK", "GUI"]


def test_vehicle_none_skips_vehicle_process():
    plan = launch_sitl.build_plan(_parse(["--vehicle", "none"]))
    assert _names(plan) == ["ROV_LINK", "GUI"]


def test_no_gui_skips_gui_process():
    plan = launch_sitl.build_plan(_parse(["--no-gui"]))
    assert _names(plan) == ["VEHICLE", "ROV_LINK"]


def test_no_rov_link_skips_rov_link_process():
    plan = launch_sitl.build_plan(_parse(["--no-rov-link"]))
    assert _names(plan) == ["VEHICLE", "GUI"]


def test_all_skipped_without_fsm_yields_empty_plan():
    """Kombinasi tanpa proses sama sekali — main() harus deteksi & keluar bersih (exit 1),
    bukan masuk wait_any_exit() dgn daftar proses kosong (loop tanpa akhir)."""
    plan = launch_sitl.build_plan(
        _parse(["--vehicle", "none", "--no-gui", "--no-rov-link"]))
    assert plan == []


def test_fsm_flag_appends_fsm_process_with_expected_flags():
    plan = launch_sitl.build_plan(_parse(
        ["--fsm", "--start-state", "M5_REDIVE", "--vision", "usb",
         "--config", "config/mission5.local.yaml", "--no-wait-autonomous"]))
    assert _names(plan) == ["VEHICLE", "ROV_LINK", "GUI", "FSM"]
    fsm_cmd = dict((n, c) for n, c, _, _ in plan)["FSM"]
    assert "--start-state" in fsm_cmd and "M5_REDIVE" in fsm_cmd
    assert "--vision" in fsm_cmd and "usb" in fsm_cmd
    assert "--config" in fsm_cmd and "config/mission5.local.yaml" in fsm_cmd
    assert "--no-wait-autonomous" in fsm_cmd


def test_fsm_flag_adds_telem_extra_to_rov_link_for_fanout():
    plan = launch_sitl.build_plan(_parse(["--fsm"]))
    rov_link_cmd = dict((n, c) for n, c, _, _ in plan)["ROV_LINK"]
    assert "--telem-extra" in rov_link_cmd
    assert rov_link_cmd[rov_link_cmd.index("--telem-extra") + 1] == "127.0.0.1:14552"


def test_no_fsm_omits_telem_extra_on_rov_link():
    plan = launch_sitl.build_plan(_parse([]))
    rov_link_cmd = dict((n, c) for n, c, _, _ in plan)["ROV_LINK"]
    assert "--telem-extra" not in rov_link_cmd


def test_gui_env_carries_server_ip_as_rpi_addr():
    plan = launch_sitl.build_plan(_parse(["--server-ip", "192.168.2.1"]))
    gui_env = dict((n, e) for n, _, _, e in plan)["GUI"]
    assert gui_env == {"RPI_ADDR": "192.168.2.1"}


def test_rov_link_command_uses_custom_ports_and_mavlink_endpoint():
    plan = launch_sitl.build_plan(_parse(
        ["--cmd-port", "24550", "--telem-port", "24551", "--mavlink", "udpin:0.0.0.0:24555"]))
    rov_link_cmd = dict((n, c) for n, c, _, _ in plan)["ROV_LINK"]
    assert rov_link_cmd[rov_link_cmd.index("--json-rx-port") + 1] == "24550"
    assert rov_link_cmd[rov_link_cmd.index("--telem-port") + 1] == "24551"
    assert rov_link_cmd[rov_link_cmd.index("--mavlink") + 1] == "udpin:0.0.0.0:24555"


def test_all_process_cwds_are_valid_existing_directories():
    plan = launch_sitl.build_plan(_parse(["--fsm"]))
    for name, _, cwd, _ in plan:
        assert cwd is not None and os.path.isdir(cwd), f"{name}: cwd tak ada -> {cwd}"
