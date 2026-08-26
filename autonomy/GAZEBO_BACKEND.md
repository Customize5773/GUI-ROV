# Gazebo (ros2_ws) sebagai backend SITL untuk mission5.py

Selain `sitl_mock.py`/ArduSub SITL, `mission5.py` juga bisa dijalankan langsung
di atas dunia Gazebo dari `~/ros2_ws` (`Customize5773/ros2_ws`, ROS2 Humble +
Gazebo Fortress). Node `gui_bridge` di repo itu **sudah bicara protokol
UDP-JSON yang sama persis** yang `mission5.py` harapkan dari `rov_link.py`
(diverifikasi end-to-end thd dashboard GUI-ROV asli 2026-08-13, retest
2026-08-16 — lihat `~/ros2_ws/docs/GUI-INTEGRATION.md`). Jadi Gazebo bisa
langsung dianggap "ROV" oleh `mission5.py`, **tanpa `rov_link.py` sama sekali**
dan tanpa kode baru di kedua sisi.

## Prasyarat

Fix wajib sebelum dipakai untuk apa pun selain smoke test: `gui_bridge_logic.py`
(ros2_ws) sebelumnya salah baca skala axis GUI-ROV (mengira −100..100, padahal
`server.js`/`mission5.py` benar-benar kirim −1000..1000) — sudah diperbaiki
25 Agu 2026 (`on_command()` kini bagi /10 di titik masuk). Kalau workspace
ros2_ws-mu belum punya fix ini, command apa pun >±10% akan tersaturasi ke
gain 100% sebelum sempat proporsional.

## Setup 2 terminal

```bash
# Terminal 1 (ros2_ws) — sim + thruster_allocator + gui_bridge.
# telem_port DIARAHKAN ke port yang didengar mission5.py (14552), BUKAN 14551
# (14551 = punya server.js/dashboard — biar tidak rebutan port UDP kalau
# dashboard tak ikut dijalankan bersamaan).
cd ~/ros2_ws
ros2 launch hydroships_bringup hydroships_gui.launch.py \
    gui_host:=127.0.0.1 cmd_port:=14550 telem_port:=14552

# Terminal 2 (GUI-ROV) — jalankan FSM langsung, tanpa rov_link.py.
# Default --server 127.0.0.1 --cmd-port 14550 --telem-port 14552 SUDAH pas
# dgn argumen launch di atas — nol flag tambahan diperlukan.
cd ~/GUI-ROV
python autonomy/fsm/mission5.py --vision mock --config autonomy/config/rov_tuned.yaml
```

## Batasan yang perlu diketahui

- Kalau kamu mau dashboard GUI-ROV (`server.js`) hidup BERSAMAAN dengan FSM
  ini, itu belum didukung — `gui_bridge` hanya kirim telemetri ke SATU
  `telem_port`. Butuh fanout kecil (pola `--telem-extra` di `rov_link.py`)
  sebelum itu bisa jalan; belum dibangun, baru kerjakan kalau kebutuhannya
  nyata.
- `mission_fsm.py` (FSM bawaan ros2_ws) TIDAK dipakai jalur ini — yang jalan
  tetap `mission5.py` (GUI-ROV), Gazebo cuma jadi "vehicle". `gui_bridge`
  mem-bypass `mission_fsm.py`/`stabilizer`/`thruster_allocator` sepenuhnya
  bila `mission5.py` yang mengendalikan (itu justru intinya — ArduSub-mixing
  yang mau diuji, bukan sim FSM).
- Sebelum dipakai utk uji misi penuh: verifikasi command tidak saturasi
  (lihat smoke test di bawah).

## Smoke test verifikasi (jalankan sekali sebelum percaya hasil misi)

Kirim axis kecil (mis. surge=20%, jauh dari saturasi) dari `mission5.py` atau
langsung via UDP, lalu cek `ros2 topic echo /hydroships/cmd_vel` — nilai
`linear.x` harus proporsional (~20% dari maksimum), BUKAN langsung jenuh di
nilai penuh. Kalau langsung jenuh di axis kecil, fix skala di atas belum
ter-apply — cek ulang `gui_bridge_logic.py`.
