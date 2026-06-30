# Setup ArduSub SITL (WSL2) — uji rantai penuh tanpa hardware

Tujuan: jalankan **ArduSub SITL** (simulator firmware + fisika depth-hold) di WSL2,
sambungkan ke `rov_link.py` (Windows), lalu uji **FSM → rov_link → ArduSub** dan GUI —
serta tutup item `VERIFIKASI_ARDUSUB.md` (arah sumbu, z-neutral, mode, depth).

```
[WSL2 Ubuntu]                         [Windows]
ArduSub SITL ──MAVLink udpout:14555──► rov_link.py ──telem JSON──► GUI (14551) + FSM (14552)
   (fisika)   ◄──MANUAL_CONTROL───────            ◄──cmd JSON 14550──
```

---

## 1. Pasang WSL2 + Ubuntu (sekali, di PowerShell admin)
```powershell
wsl --install -d Ubuntu
```
Restart bila diminta, buat user Ubuntu. **Windows 11**: aktifkan *mirrored networking*
agar `localhost` tembus dua arah (paling mudah) — buat `C:\Users\<kamu>\.wslconfig`:
```
[wsl2]
networkingMode=mirrored
```
lalu `wsl --shutdown` dan buka Ubuntu lagi. (Jika tak pakai mirrored, lihat §4 untuk IP.)

## 2. Build ArduSub SITL (di Ubuntu/WSL, ~20–40 mnt sekali)
```bash
sudo apt update && sudo apt install -y git python3-pip
git clone --recurse-submodules https://github.com/ArduPilot/ardupilot
cd ardupilot
Tools/environment_install/install-prereqs-ubuntu.sh -y
. ~/.profile
./waf configure --board sitl
./waf sub
```

## 3. Jalankan SITL → arahkan ke rov_link (Windows)
Pakai skrip bantu `tools/run_sitl.sh` (salin ke WSL atau akses via `/mnt/r/...`):
```bash
# mirrored networking (Win11): host = 127.0.0.1
HOST=127.0.0.1 bash run_sitl.sh
# tanpa mirrored: cari IP Windows dari WSL → lihat §4
HOST=<IP_WINDOWS> bash run_sitl.sh
```
Skrip menjalankan: `sim_vehicle.py -v ArduSub --out=udpout:$HOST:14555 --console --map`.

Lalu di **Windows**:
```powershell
# 1) jembatan (fan-out GUI + FSM)
python rov_link.py --server 127.0.0.1 --mavlink udpin:0.0.0.0:14555 --telem-extra 127.0.0.1:14552
# 2) GUI:  cd server ; $env:RPI_ADDR="127.0.0.1" ; npm start     → http://localhost:8080
# 3) FSM (opsional, misi 5): python fsm/mission5.py --server 127.0.0.1 --telem-port 14552 --start-state AUTO_RELEASE
```
`rov_link` harus cetak `[MAV] terhubung: system=1 …`.

## 4. (Tanpa mirrored) cari IP Windows dari WSL
```bash
ip route | grep default | awk '{print $3}'    # = IP host Windows
```
Pakai sebagai `HOST`. Pastikan Windows Firewall mengizinkan UDP 14555 inbound
(atau matikan firewall jaringan privat sementara saat uji).

---

## 5. Verifikasi yang BARU bisa dilakukan dengan SITL (tutup VERIFIKASI_ARDUSUB.md)
Di konsol MAVProxy SITL (`--console`) atau QGroundControl yang juga connect:
```
param set ARMING_CHECK 0      # mempermudah arming saat sim
mode MANUAL                   # atau ALT_HOLD utk depth-hold
arm throttle
```
Lalu via GUI/FSM kirim perintah & amati di SITL map/console:
| Item | Cara | Konstanta bila salah |
|---|---|---|
| Arah surge/sway/yaw | tekan W/A/D/Q/E di GUI → cek arah gerak di map | tanda di `rov_link.send_manual_control()` |
| z-neutral & vertikal | mode ALT_HOLD, vert=0 tahan depth; F turun/R naik | `Z_NEUTRAL`, rumus z |
| Nama mode depth-hold | `print(master.mode_mapping())` → `ALT_HOLD`? | `set_mode("ALT_HOLD")` |
| Sumber & skala depth | bandingkan `depth` telemetri vs SITL | handler SCALED_PRESSURE2 / WATER_RHO |
| Servo gripper/lampu | `gripper close/open` → cek `SERVO*_FUNCTION` di QGC | `GRIPPER_SERVO_CH` dll |

> SITL TIDAK menguji visi (kamera nyata) — APPROACH_HOOK akan timeout→degradasi.
> Untuk uji visual servo pakai webcam (`servo_webcam_test.py` / `pose_webcam_test.py`).

## 6. Tips
- ArduSub SITL frame default = vectored 6-DOF (mirip BlueROV2). Untuk konfigurasi
  thruster kalian (4H+2V vs 3H+3V), set `FRAME_CONFIG` & motor params lalu uji ulang.
- Reset cepat: di MAVProxy ketik `reboot`. Ganti lokasi: `sim_vehicle.py ... -L <name>`.
- Simpan param tuning ke file dan `param load` agar konsisten antar sesi.
