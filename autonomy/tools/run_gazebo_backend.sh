#!/usr/bin/env bash
# run_gazebo_backend.sh — jalankan mission5.py end-to-end melawan Gazebo (ros2_ws)
# sebagai backend SITL. Lihat autonomy/GAZEBO_BACKEND.md utk detail & rationale
# tiap langkah (kenapa PATH harus bersih, kenapa arm manual, dst).
#
# Pakai:
#   autonomy/tools/run_gazebo_backend.sh [start-state] [mission5.py args tambahan...]
#   autonomy/tools/run_gazebo_backend.sh DIVE --run-log /tmp/run.jsonl
#
# ROS2_WS bisa dioverride via env var kalau lokasi ros2_ws bukan ~/ros2_ws.
set -euo pipefail

ROS2_WS="${ROS2_WS:-$HOME/ros2_ws}"
GUI_ROV="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
START_STATE="${1:-DIVE}"
shift || true

# WORLD: nama file .sdf di hydroships_gazebo/worlds/. Default = kolam latihan
# 2,2x4,4x0,8 m (pool_practice_arena.sdf), sama seperti default project-wide
# ros2_ws (lihat hydroships_gazebo/launch/sim.launch.py). POOL_CONFIG: layer
# YAML pool tambahan ditumpuk di atas rov_tuned.yaml, defaultnya dipasangkan
# dgn world di atas. Utk uji arena lomba, override keduanya:
#   WORLD=kki_arena.sdf POOL_CONFIG=config/pool_kki_trial.yaml \
#       autonomy/tools/run_gazebo_backend.sh DIVE
WORLD="${WORLD:-pool_practice_arena.sdf}"
POOL_CONFIG="${POOL_CONFIG:-config/pool_trial.yaml}"
# GAZEBO_CONFIG: layer ke-3, ditumpuk PALING AKHIR (menang atas POOL_CONFIG).
# Default = override wall_heading yg cuma valid lawan Gazebo (world default
# py 4 hook 1/dinding), BUKAN kolam latihan fisik (cuma 1 hook, itu sebabnya
# tak dimasukkan ke pool_trial.yaml sendiri). Kosongkan kalau tak perlu.
GAZEBO_CONFIG="${GAZEBO_CONFIG:-config/gazebo_sim.yaml}"

echo "[1/4] Membersihkan sisa proses sim lama (kalau ada)..."
# ros_gz_bridge/parameter_bridge & robot_state_publisher TIDAK match pola
# "hydroships|ign gazebo" -- nama proses beda -- kalau kelewatan, orphan-nya
# numpuk tiap launch dan lama-lama menghabiskan CPU host (ditemukan 25 Agu:
# load average naik ke ~29 dari parameter_bridge; 26 Agu: 28 proses
# robot_state_publisher orphan numpuk sejak sesi H-1, masing2 cuma 2-5% CPU
# tapi jumlahnya bikin host tersendat terus-menerus tanpa disadari).
(ps aux | grep -E "hydroships|ign gazebo|ros_gz_bridge|parameter_bridge|robot_state_publisher" | grep -v grep | awk '{print $2}' | xargs -r kill -9) || true
sleep 2

echo "[2/4] Meluncurkan sim headless (ros2_ws @ $ROS2_WS)..."
# PATH dibersihkan dari venv GUI-ROV -- lihat GAZEBO_BACKEND.md soal kenapa
# (node ros2_ws dgn shebang `env python3` bisa nyasar ke python3 venv salah).
CLEAN_PATH=$(echo "$PATH" | tr ':' '\n' | grep -v "GUI-ROV/.venv" | paste -sd:)
(
  export PATH="$CLEAN_PATH"
  cd "$ROS2_WS"
  set +u   # ROS2 setup.bash mereferensikan var yg belum di-set -- tak cocok dgn set -u
  source /opt/ros/humble/setup.bash
  source install/setup.bash
  set -u
  exec ros2 launch hydroships_bringup hydroships_gui.launch.py headless:=true \
      gui_host:=127.0.0.1 cmd_port:=14550 telem_port:=14552 \
      rov_random_spawn:=false rov_x:=0.0 rov_y:=0.0 world:="$WORLD"
) > /tmp/gazebo_backend_sim.log 2>&1 < /dev/null &
SIM_PID=$!
disown

echo "    menunggu gui_bridge siap..."
for _ in $(seq 1 30); do
    grep -q "gui_bridge siap" /tmp/gazebo_backend_sim.log 2>/dev/null && break
    sleep 1
done
grep -q "gui_bridge siap" /tmp/gazebo_backend_sim.log || {
    echo "gui_bridge tak siap dalam 30s, cek /tmp/gazebo_backend_sim.log" >&2
    exit 1
}

echo "[3/4] Arm gui_bridge (tak ada dashboard operator di jalur ini)..."
python3 - <<'PYEOF'
import socket, json
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(json.dumps({'name': 'arm', 'value': True, 'src': 'fsm'}).encode(), ('127.0.0.1', 14550))
PYEOF

echo "[4/4] Menjalankan mission5.py (start-state=$START_STATE)..."
cd "$GUI_ROV/autonomy"
python3 fsm/mission5.py --vision mock --config config/rov_tuned.yaml \
    ${POOL_CONFIG:+--config "$POOL_CONFIG"} \
    ${GAZEBO_CONFIG:+--config "$GAZEBO_CONFIG"} \
    --start-state "$START_STATE" --no-wait-autonomous "$@"

echo "Selesai. Sim (PID $SIM_PID) masih jalan di background -- 'kill $SIM_PID' atau" \
     "'pkill -9 -f hydroships' utk stop."
