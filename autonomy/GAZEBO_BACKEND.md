# Gazebo (ros2_ws) sebagai backend SITL untuk mission5.py

**Diverifikasi 25 Agu 2026**: misi penuh `mission5.py --vision mock` lewat
runbook di bawah → **100/100** (kelima sub-misi selesai, DIVE→SCAN_QR→GRAB→
NAV_WALL→HANG→SURFACE→DOCK→M5_REDIVE→M5_DOCK→M5_ENGAGE→M5_UNHOOK→M5_ASCEND→
DONE), tanpa kode baru selain fix skala axis (lihat Prasyarat). Pakai
`autonomy/tools/run_gazebo_backend.sh` untuk mengulang setup ini otomatis.

**Diverifikasi 26 Agu 2026 — vision SUNGGUHAN (bukan mock) juga berhasil**:
`mission5.py --vision rtsp` (kamera Gazebo asli via `http_camera_bridge`,
lihat §"Vision sungguhan" di bawah) benar2 men-decode QR nyata di SCAN_QR
(`qr_data='A'`), lanjut GRAB→NAV_WALL→HANG→SURFACE→DOCK dgn QR terus
terbaca. Butuh 2 fix tambahan di ros2_ws yg sudah masuk: node
`http_camera_bridge` (bridge kamera→HTTP MJPEG) dan anchor DetachableJoint
di `payload_spawner.py` (payload dulu tak stabil di orientasi spawn, QR
menghadap samping bukan atas — lihat memory `pbr-rendering-investigation-deferred`
utk detail diagnosis).

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

- **Dashboard GUI-ROV + FSM bersamaan: SUDAH didukung (26 Agu 2026)** —
  `gui_bridge` sekarang punya `telem_extra` (pola sama dgn `--telem-extra`
  di `rov_link.py`), csv `host:port` utk tujuan tambahan. Contoh: dashboard
  di :14551 (default) + FSM di :14552 sekaligus:
  ```bash
  ros2 launch hydroships_bringup hydroships_gui.launch.py headless:=true \
      gui_host:=127.0.0.1 cmd_port:=14550 telem_port:=14551 \
      telem_extra:=127.0.0.1:14552 rov_random_spawn:=false rov_x:=0.0 rov_y:=0.0
  ```
  Jalankan `server.js` (`RPI_ADDR=127.0.0.1 UDP_OUT=14550 UDP_IN=14551`) di
  terminal terpisah utk lihat dashboard hidup bersamaan dgn `mission5.py`.
- `mission_fsm.py` (FSM bawaan ros2_ws) TIDAK dipakai jalur ini — yang jalan
  tetap `mission5.py` (GUI-ROV), Gazebo cuma jadi "vehicle". `gui_bridge`
  mem-bypass `mission_fsm.py`/`stabilizer`/`thruster_allocator` sepenuhnya
  bila `mission5.py` yang mengendalikan (itu justru intinya — ArduSub-mixing
  yang mau diuji, bukan sim FSM).
- Sebelum dipakai utk uji misi penuh: verifikasi command tidak saturasi
  (lihat smoke test di bawah).

## Vision sungguhan (bukan mock)

`--vision mock` di atas memalsukan hasil QR — tak pernah menyentuh gambar
kamera Gazebo asli. Untuk uji closed-loop vision SUNGGUHAN:

```bash
# Terminal 1 (ros2_ws) — sim seperti biasa (lihat Setup manual di atas).

# Terminal 1b (ros2_ws) — bridge kamera → HTTP MJPEG (cv2.VideoCapture GUI-ROV
# menerima URL http:// sama seperti rtsp://, backend FFmpeg yg sama).
ros2 run hydroships_control http_camera_bridge

# Terminal 2 (GUI-ROV) — arm dulu (sama seperti biasa), lalu:
python3 autonomy/fsm/mission5.py --vision rtsp \
    --bottom-url http://127.0.0.1:8090/cam_bottom \
    --wall-url   http://127.0.0.1:8090/cam_front \
    --config autonomy/config/rov_tuned.yaml
```

**Update 26 Agu 2026**: `config/gazebo_sim.yaml` (nudge `depth.target_bottom`
lebih dekat) TIDAK PERLU LAGI — akar masalahnya (FOV kamera sim 80° generik,
jauh lebih lebar drpd kamera DWE asli ~70°) sudah diperbaiki langsung di
`hydroships_description/urdf/hydroships.urdf.xacro` (`horizontal_fov`
disamakan dgn kalibrasi `dwe_trial2.npz`, fx sim jadi 457 @ 640px, cocok
dgn fx=914 @ 1280px kamera asli). Closed-loop QR-decode kini berhasil di
`DEPTH_TARGET_BOTTOM` DEFAULT (0.70m), tanpa config tambahan. File
`config/gazebo_sim.yaml` dibiarkan ada (harmless, tak lagi diperlukan)
kalau-kalau ada alasan lain nanti mau clearance lebih dekat.

**Posisi spawn ROV vs payload (`rov_x/y` vs `payload_x/y`) penting**:
- **JANGAN identik** (mis. `rov_x=0.4 rov_y=0.04` = persis `payload_x/y`)
  — collision lock, ROV macet total, ditemukan 26 Agu.
- **Jangan terlalu dekat secara vertikal saat spawn** (mis. `rov_z=-0.5`
  dgn offset horizontal kecil ~0.05m) — payload skrg terkunci KAKU di
  orientasi rolled-nya (anchor fix), jadi punya profil collision lebih
  besar drpd dulu (yg cepat rebah rata) — ROV bisa nyangkut kalau di-spawn
  terlalu dekat. Beri jarak wajar (≥0.3m horizontal ATAU spawn ROV agak
  jauh lalu DIVE turun, seperti contoh `rov_x:=0.35` di Setup manual).

## Smoke test verifikasi (jalankan sekali sebelum percaya hasil misi)

Kirim axis kecil (mis. surge=20%, jauh dari saturasi) dari `mission5.py` atau
langsung via UDP, lalu cek `ros2 topic echo /hydroships/cmd_vel` — nilai
`linear.x` harus proporsional (~20% dari maksimum), BUKAN langsung jenuh di
nilai penuh. Kalau langsung jenuh di axis kecil, fix skala di atas belum
ter-apply — cek ulang `gui_bridge_logic.py`.
