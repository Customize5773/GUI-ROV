# Gazebo (ros2_ws) sebagai backend SITL untuk mission5.py

**Diverifikasi 25 Agu 2026**: misi penuh `mission5.py --vision mock` lewat
runbook di bawah → **100/100** (kelima sub-misi selesai, DIVE→SCAN_QR→GRAB→
NAV_WALL→HANG→SURFACE→DOCK→M5_REDIVE→M5_DOCK→M5_ENGAGE→M5_UNHOOK→M5_ASCEND→
DONE), tanpa kode baru selain fix skala axis (lihat Prasyarat). Pakai
`autonomy/tools/run_gazebo_backend.sh` untuk mengulang setup ini otomatis.

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

## Setup (otomatis)

`autonomy/tools/run_gazebo_backend.sh` menjalankan seluruh urutan di bawah
(bersihkan sisa proses lama → launch sim headless → arm gui_bridge → jalankan
`mission5.py`). Lihat isi skrip utk parameter yang bisa diubah (start-state,
config, qr_letter/payload posisi).

## Setup manual (2 terminal)

```bash
# Terminal 1 (ros2_ws) — sim + thruster_allocator + gui_bridge.
# PENTING: PATH TIDAK BOLEH memuat GUI-ROV/.venv/bin di depan PATH sistem —
# beberapa node ros2_ws (mis. payload_spawner) pakai shebang `#!/usr/bin/env
# python3` yg resolve ke python3 manapun duluan di PATH; kalau itu python3.14
# GUI-ROV, rclpy C-extension (dikompilasi utk python3.10 ROS) gagal load dan
# node itu mati diam-diam (ditemukan 25 Agu saat percobaan pertama).
# telem_port DIARAHKAN ke port yang didengar mission5.py (14552), BUKAN 14551
# (14551 = punya server.js/dashboard — biar tidak rebutan port UDP kalau
# dashboard tak ikut dijalankan bersamaan).
cd ~/ros2_ws
source /opt/ros/humble/setup.bash && source install/setup.bash
ros2 launch hydroships_bringup hydroships_gui.launch.py headless:=true \
    gui_host:=127.0.0.1 cmd_port:=14550 telem_port:=14552 \
    rov_random_spawn:=false rov_x:=0.0 rov_y:=0.0

# Terminal 2 (GUI-ROV) — ARM gui_bridge dulu. Di produksi operator dashboard
# yang menekan tombol arm sebelum toggle autonomous; di sini tak ada dashboard,
# jadi mission5.py TIDAK PERNAH mengirim {"name":"arm"} sendiri (memang bukan
# tanggung jawabnya) — tanpa langkah ini gui_bridge.wrench() selalu nol dan
# DIVE akan timeout diam-diam (ditemukan 25 Agu, awalnya disangka bug fisika).
python3 -c "
import socket, json
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(json.dumps({'name':'arm','value':True,'src':'fsm'}).encode(), ('127.0.0.1',14550))
"

# Terminal 2 lanjut — jalankan FSM langsung, tanpa rov_link.py.
# Default --server 127.0.0.1 --cmd-port 14550 --telem-port 14552 SUDAH pas
# dgn argumen launch di atas — nol flag tambahan diperlukan.
cd ~/GUI-ROV
python3 autonomy/fsm/mission5.py --vision mock --config autonomy/config/rov_tuned.yaml
```

## Batasan yang perlu diketahui

- **Timeout FSM (`TIMEOUT_DIVE` dkk) berbasis wall-clock, sim tidak selalu
  real-time**: di mesin yg lagi terbebani (banyak aplikasi lain jalan),
  `ign gazebo` bisa butuh beberapa detik ekstra utk mencapai steady-state
  fisika tepat setelah launch — DIVE bisa timeout palsu pada percobaan
  pertama padahal berhasil mulus begitu diulang tanpa perubahan apa pun
  (diamati 25 Agu: 1 dari 3 percobaan). Kalau timeout terjadi tepat di
  percobaan PERTAMA setelah launch, coba ulang sebelum menyimpulkan ada bug.
- **Cek proses ganda sebelum menyalahkan bug**: kalau `kill`/`Ctrl+C` pada
  `ros2 launch` sebelumnya tidak bersih (mis. `pkill` yg pola-nya cuma cocok
  parent process), child node (`gui_bridge` dkk) bisa jadi orphan dan TERUS
  jalan, dobel dengan instance baru — keduanya rebutan UDP :14550/:14552 dan
  hasilnya acak/flaky (field `armed` di telemetri "berkedip" antara run yg
  beda). Cek `ps aux | grep -E "gui_bridge|ign gazebo"` sebelum debug lebih
  jauh kalau hasil tak konsisten antar percobaan.

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
