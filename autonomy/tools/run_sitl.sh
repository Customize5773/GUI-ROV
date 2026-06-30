#!/usr/bin/env bash
# run_sitl.sh — Jalankan ArduSub SITL & arahkan MAVLink ke rov_link.py (Windows).
# Pakai: HOST=127.0.0.1 bash run_sitl.sh      (Win11 mirrored networking)
#        HOST=<IP_WINDOWS> bash run_sitl.sh   (cari: ip route | grep default)
#
# Prasyarat: ArduPilot sudah di-build (lihat SITL_SETUP.md §2) dan sim_vehicle.py di PATH
# (biasanya ~/ardupilot/Tools/autotest/sim_vehicle.py).
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-14555}"
ARDUPILOT="${ARDUPILOT:-$HOME/ardupilot}"

SIMV="$ARDUPILOT/Tools/autotest/sim_vehicle.py"
if [ ! -f "$SIMV" ]; then
  echo "sim_vehicle.py tak ditemukan di $SIMV — set ARDUPILOT=/path/ardupilot" >&2
  exit 1
fi

echo "ArduSub SITL → MAVLink udpout:$HOST:$PORT (rov_link.py harus udpin:0.0.0.0:$PORT)"
exec python3 "$SIMV" -v ArduSub \
  --out="udpout:$HOST:$PORT" \
  --console --map \
  "$@"
