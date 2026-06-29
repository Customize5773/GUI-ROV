# Point (c) — Jalur MANUAL_CONTROL Python → ArduSub + integrasi GUI

Tujuan: buktikan **end-to-end** GUI HYDROSHIP menggerakkan vehicle MAVLink dan telemetri
balik menggerakkan 3D-nya — **tanpa hardware**, pakai mock dulu, lalu ArduSub SITL.

```
Browser ──WS:8080── server.js ──cmd JSON :14550──► rov_link.py ──MANUAL_CONTROL──► mock / SITL / Pixhawk
  (3D)             (LIVE)      ◄─telem JSON :14551─             ◄─ATTITUDE/PRESSURE─   (MAVLink :14555)
```

> Tidak ada perubahan pada `server.js` atau GUI. `rov_link.py` adalah pengganti
> `raspi_rov_example.py` yang berbicara MAVLink.

---

## 1. Pasang Python 3.12 + venv (sekali saja)

Sistemmu hanya punya Python 3.14 (pymavlink/OpenCV sering belum ada wheel-nya). Pasang 3.12:

```powershell
winget install -e --id Python.Python.3.12
```
Tutup & buka lagi terminal, lalu buat venv di folder ini:
```powershell
cd "R:\GUI ROV\autonomy"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```
> Jika `Activate.ps1` diblokir: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (sekali).

---

## 2. Uji dengan MOCK (3 terminal) — bisa malam ini

**Terminal A — vehicle palsu (MAVLink):**
```powershell
cd "R:\GUI ROV\autonomy"; .\.venv\Scripts\Activate.ps1
python sitl_mock.py --mavlink udpout:127.0.0.1:14555
```

**Terminal B — jembatan:**
```powershell
cd "R:\GUI ROV\autonomy"; .\.venv\Scripts\Activate.ps1
python rov_link.py --server 127.0.0.1 --mavlink udpin:0.0.0.0:14555
```
Harus muncul: `[MAV] terhubung: system=1 …` dan `[OK] rov_link berjalan`.

**Terminal C — GUI dalam mode LIVE (bukan --sim), arahkan command ke localhost:**
```powershell
cd "R:\GUI ROV\server"
$env:RPI_ADDR="127.0.0.1"; npm start
```
Buka `http://localhost:8080`.

### Yang harus terlihat (kriteria sukses c1–c3)
- GUI status **ONLINE**, telemetri masuk (tak ada "timeout").
- Tekan **ARM** → Terminal A cetak `[MOCK] ARMED`.
- Halaman **Control**, tekan **Q/E** (yaw) → heading di strip & **kompas 3D berputar**.
- Tahan **F / R** (vertikal) → **DEPTH** berubah (turun/naik).
- Tekan **STOP / Spasi** → Terminal A cetak `[MOCK] DISARMED`, gerak berhenti.
- Toggle **AUTONOMOUS** → Terminal B cetak `[MODE] -> ALT_HOLD` (di mock hanya log).

---

## 3. Naik ke ArduSub SITL (langkah berikutnya, fisika nyata)

Di WSL2 (Ubuntu):
```bash
git clone --recurse-submodules https://github.com/ArduPilot/ardupilot
cd ardupilot && Tools/environment_install/install-prereqs-ubuntu.sh -y
./waf configure --board sitl && ./waf sub
sim_vehicle.py -v ArduSub --out=udpout:<IP_WINDOWS>:14555 --console --map
```
Lalu jalankan `rov_link.py` (Terminal B) & GUI (Terminal C) seperti di atas.
`<IP_WINDOWS>` = IP host Windows dari sisi WSL (`cat /etc/resolv.conf` → nameserver,
atau pakai IP adapter vEthernet).

> SITL memberi depth-hold & dinamika sungguhan → kita bisa mulai tuning dan, di
> tahap berikut, menempelkan **state-machine autonomy** (yang juga mengirim
> command JSON yang sama: `surge/sway/yaw/vert/gripper`).

---

## 4. Hal yang WAJIB diverifikasi saat naik ke ArduSub asli/SITL
Tandai `VERIFIKASI` di `rov_link.py`:
- **Arah & tanda** sumbu `MANUAL_CONTROL` (surge/sway/yaw) dan netral `z` (500?) untuk depth-hold.
- **Channel servo** lampu & gripper (`SERVOn_FUNCTION` di QGroundControl) + PWM open/close.
- **Nama mode** depth-hold ArduSub (`ALT_HOLD` vs lain) lewat `master.mode_mapping()`.
- **Sumber depth**: `SCALED_PRESSURE2` (MS5837) vs `GLOBAL_POSITION_INT`; kalibrasi `surface_hpa`.
- **Densitas air** kolam (tawar 997).

## 5. Setelah (c) hijau
Lanjut ke visi (point a/b): modul `vision/aruco_qr.py` + state-machine `fsm/mission5.py`
yang menyuntik command JSON yang sama ke `:14550` saat mode autonomous.
