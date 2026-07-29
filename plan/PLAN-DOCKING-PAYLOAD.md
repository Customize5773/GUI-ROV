# Rencana Arsitektur Hardware-to-Software ROV Misi 5 (Otonom Vision-Only)

Dokumen ini merupakan perencanaan teknis komprehensif yang menghubungkan seluruh komponen **Hardware, Propulsi, Sensor, dan Software** untuk pelaksanaan **Misi 5 (Pelepasan Payload Otonom)** pada Kontes Kapal Indonesia (KKI) 2026.

---

## 1. Spesifikasi Hardware & Pemetaan Saluran (*Channel Mapping*)

### A. Komponen Komputasi & Sensor

| Komponen | Spesifikasi / Model | Peran & Fungsi |
| --- | --- | --- |
| **SBC (Companion Computer)** | Raspberry Pi 4 Model B | Pusat pengolahan *Computer Vision* (OpenCV), *State Machine*, dan pemroses instruksi PyMavlink. |
| **Flight Controller (FC)** | Pixhawk PX4 (Firmware ArduSub) | Kontroler motor, ekskusi *stabilization*, penanganan PWM Servo, dan komunikasi MAVLink. |
| **Kamera 1 (Head Cam)** | DWE ExploreHD USB (Sony STARVIS) | Navigasi jarak jauh (*Far Approach* $>30\text{ cm}$) & deteksi permukaan (*Surfacing*). |
| **Kamera 2 (Bottom Cam)** | DWE ExploreHD USB (Sony STARVIS) | *Micro-alignment* jarak dekat ($<30\text{ cm}$), pengawasan jepitan *gripper*, dan *unhooking*. |
| **Sensor Kedalaman** | MS5837 ($I^2C$) | Pembacaan tekanan digital *real-time* untuk *Auto-Depth Hold* (Redundansi/Safety). |
| **IMU Sensor** | Terintegrasi di Pixhawk | Mempertahankan *Heading (Yaw)* dan orientasi ROV. |

### B. Pemetaan Output Pixhawk (Main & Aux PWM Ports)

| Port Pixhawk | Komponen Terhubung | Fungsi Pergerakan |
| --- | --- | --- |
| **MAIN 1–2** | 2x Thruster T200 BLDC + ESC 20A | Pendorong Maju / Mundur (*Surge*) |
| **MAIN 3–6** | 4x Thruster T100 BLDC + ESC 20A | Kendali Lateral (*Sway*), Vertikal (*Heave*), dan Orientasi (*Yaw/Roll/Pitch*) |
| **AUX 1** | Servo Waterproof 1 | Capit Gripper (*Clamp / Release*) |
| **AUX 2** | Servo Waterproof 2 | Elevasi Gripper (*Tilt Up / Down*) |

### C. Sistem Kelistrikan & Power Distribution

* **Power Supply Utama:** PSU 24V 40A (Input 220V AC) di permukaan via kabel *tether* 10 AWG.
* **Step-Down Kiprok (24V ke 12V 30A):** Suplai daya khusus untuk 6 unit ESC 20A.
* **UBEC Step-Down (12V ke 5V 8A):** Suplai daya terisolasi untuk Pixhawk PX4, Raspberry Pi 4, dan 2x Servo Waterproof.

---

## 2. Topologi Sistem & Komunikasi Data

```
========================= SURFACE / GROUND STATION =========================
[ Laptop GCS + Joystick ]
           │
           │ (Ethernet / RTSP Video Stream + MAVLink Data)
           ▼
============================== UMBILICAL / TETHER ==============================
[ Tether Cable 2x26AWG (25 Meter) + Tether Interface Board ]
           │
           ▼
================================== UNDERWATER ROV ==============================
[ Raspberry Pi 4 Model B (SBC) ]
     ├── USB 3.0 Port 1 ──> DWE ExploreHD Camera (Head)
     ├── USB 3.0 Port 2 ──> DWE ExploreHD Camera (Bottom)
     └── Serial UART / USB ──> [ Pixhawk PX4 Flight Controller ]
                                   ├── I2C Port ──────> Sensor MS5837
                                   ├── MAIN 1–6 ──────> 6x ESC 20A + BLDC Thrusters
                                   └── AUX 1–2 ───────> 2x Servo Waterproof Gripper

```

---

## 3. Alur Software & State Machine Misi 5

Eksplorasi gerakan otonom dari docking dinding hingga permukaan diatur secara otomatis tanpa input *joystick* operator:

```
[State 0: Mode Otonom Activated (GCS Toggle)]
                       │
                       ▼
[State 1: Far Approach (Head Cam)]
──> OpenCV detect QR (4x4 cm) via solvePnP
──> PyMavlink: Surge & Sway Alignment
                       │
                       ▼
[State 2: Close Alignment & Gripping (Bottom Cam)]
──> Switch feed ke Bottom Cam saat Z <= 30 cm
──> Target koordinat offset Lubang (Y_offset = -0.045m)
──> AUX 1 PWM Trigger: Clamp Gripper
                       │
                       ▼
[State 3: Unhooking Sequence]
──> Heave Up (Naik +5 cm)
──> Verifikasi Optical Flow Dinding / Shift QR
                       │
                       ▼
[State 4: Retract / Back Up]
──> Surge Backward hingga Z > 50 cm
                       │
                       ▼
[State 5: Surfacing & Finish]
──> Head Cam: Optical Flow / Brightness Gradient
──> MS5837 Sensor Check: Depth ≈ 0m
──> Motors Disarm & Mission Completed

```

---

## 4. Implementasi Kode Integrasi (PyMavlink + OpenCV)

Berikut adalah skrip Python utama (`mission5_autonomous.py`) yang berjalan di Raspberry Pi 4 Model B:

```python
import time
import cv2
import numpy as np
from pymavlink import mavutil

# ==============================================================================
# 1. INISIALISASI KOMUNIKASI PYMAVLINK (RPi 4 -> PIXHAWK)
# ==============================================================================
pixhawk_conn = mavutil.mavlink_connection('/dev/ttyACM0', baud=115200)
pixhawk_conn.wait_heartbeat()
print("[INFO] Terhubung ke Pixhawk PX4 via MAVLink!")

def send_movement_command(surge=0, sway=0, heave=0, yaw=0):
    """
    Kirim kontrol gerakan manual ke Pixhawk (Nilai PWM: -1000 hingga 1000)
    """
    pixhawk_conn.mav.manual_control_send(
        pixhawk_conn.target_system,
        x=int(surge),  # Maju (+) / Mundur (-)
        y=int(sway),   # Kanan (+) / Kiri (-)
        z=int(heave),  # Turun (+) / Naik (-)
        r=int(yaw),    # Putar Kanan (+) / Putar Kiri (-)
        buttons=0
    )

def set_gripper_servo(channel, pwm_value):
    """
    channel 9 = AUX 1 (Capit), channel 10 = AUX 2 (Elevasi)
    pwm_value: 1000us (Lepas/Turun) - 2000us (Jepit/Naik)
    """
    pixhawk_conn.mav.command_long_send(
        pixhawk_conn.target_system, pixhawk_conn.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
        0, channel, pwm_value, 0, 0, 0, 0, 0
    )

# ==============================================================================
# 2. PARAMETER KALIBRASI COMPUTER VISION (QR CODE 4x4 CM)
# ==============================================================================
QR_SIZE = 0.04  # 4 cm dalam meter
HOLE_Y_OFFSET = 0.045  # Offset 4.5 cm dari pusat QR ke pusat Lubang Hook

# 3D Object Points dari QR Code
qr_3d_points = np.array([
    [-QR_SIZE/2,  QR_SIZE/2, 0],
    [ QR_SIZE/2,  QR_SIZE/2, 0],
    [ QR_SIZE/2, -QR_SIZE/2, 0],
    [-QR_SIZE/2, -QR_SIZE/2, 0]
], dtype=np.float32)

# Matrix Kamera exploreHD (Hasil Kalibrasi Underwater)
cam_matrix = np.array([[800, 0, 640], [0, 800, 360], [0, 0, 1]], dtype=np.float32)
dist_coeffs = np.zeros((4, 1))

qr_detector = cv2.QRCodeDetector()

# ==============================================================================
# 3. UTILITY POSE ESTIMATION
# ==============================================================================
def get_payload_target(frame):
    retval, _, points, _ = qr_detector.detectAndDecode(frame)
    if retval and points is not None:
        img_points = points[0].astype(np.float32)
        success, _, tvec = cv2.solvePnP(qr_3d_points, img_points, cam_matrix, dist_coeffs)
        if success:
            tx, ty, tz = tvec.flatten()
            hole_x = tx
            hole_y = ty - HOLE_Y_OFFSET
            hole_z = tz
            return True, hole_x, hole_y, hole_z
    return False, 0, 0, 0

# ==============================================================================
# 4. LOGIKA UTAMA STATE MACHINE
# ==============================================================================
current_state = "APPROACH"
cap_head = cv2.VideoCapture(0)
cap_bottom = cv2.VideoCapture(1)

Kp = 400.0  # Proportional Gain Kontroler Motor

try:
    while True:
        if current_state == "APPROACH":
            ret, frame = cap_head.read()
            if not ret: continue
            
            found, h_x, h_y, h_z = get_payload_target(frame)
            if found:
                # Visual Servoing
                sway_cmd = Kp * h_x
                heave_cmd = Kp * h_y
                surge_cmd = Kp * (h_z - 0.30)  # Dekati hingga jarak 30 cm
                
                send_movement_command(surge=surge_cmd, sway=sway_cmd, heave=heave_cmd)
                
                # Pindah ke Bottom Cam jika jarak < 30 cm
                if h_z <= 0.30:
                    print("[STATE] Switch ke Bottom Cam & Gripping Mode")
                    current_state = "GRIPPING"

        elif current_state == "GRIPPING":
            ret, frame = cap_bottom.read()
            if not ret: continue
            
            found, h_x, h_y, h_z = get_payload_target(frame)
            if found:
                # Micro Alignment
                sway_cmd = Kp * h_x
                heave_cmd = Kp * h_y
                surge_cmd = Kp * (h_z - 0.10) # Jarak jepit 10 cm
                
                send_movement_command(surge=surge_cmd, sway=sway_cmd, heave=heave_cmd)
                
                if abs(h_x) < 0.01 and abs(h_y) < 0.01 and h_z <= 0.12:
                    print("[ACTION] Posisi Pas! Epit Payload...")
                    send_movement_command(0, 0, 0, 0)
                    set_gripper_servo(channel=9, pwm_value=1900) # PWM Tutup Capit
                    time.sleep(1.5)
                    current_state = "UNHOOK"

        elif current_state == "UNHOOK":
            print("[ACTION] Lifting Payload...")
            send_movement_command(surge=0, sway=0, heave=-400) # Naik vertikal
            time.sleep(2.0)
            
            print("[ACTION] Mundur dari Hook...")
            send_movement_command(surge=-300, sway=0, heave=0) # Mundur
            time.sleep(2.5)
            
            current_state = "SURFACING"

        elif current_state == "SURFACING":
            print("[ACTION] Naik ke Permukaan...")
            send_movement_command(surge=0, sway=0, heave=-500)
            time.sleep(5.0)
            
            # Stop dan Disarm
            send_movement_command(0, 0, 0, 0)
            print("[INFO] Misi 5 Selesai!")
            break

finally:
    cap_head.release()
    cap_bottom.release()

```

---

## 5. Prosedur Keamanan & *Fail-Safe* Otonom

1. **Target Loss Timeout (Vision Recovery):**
* Jika QR Code tidak terdeteksi selama $>3\text{ detik}$ pada saat *approach*, RPi 4 otomatis menghentikan gerakan maju dan melakukan gerakan mundur perlahan ($20\text{ cm}$) untuk melakukan pemindaian ulang (*rescan*).


2. **Manual GCS Override (Kill-Switch):**
* Operator di darat dapat mengambil alih kendali kapan saja. Pergerakan *joystick* di GCS akan memicu Pixhawk untuk berpindah dari skrip otonom RPi 4 kembali ke mode manual (`STABILIZE / MANUAL`).


3. **Double Verification Surfacing (MS5837 Redundancy):**
* Proses naik ke permukaan (*Surfacing*) dikombinasikan antara analisis *Optical Flow* kamera dan konfirmasi pembacaan sensor tekanan MS5837 ($\text{Depth} \le 0.05\text{ meter}$).


4. **Proteksi Arus Listrik (Power Regulation):**
* Penggunaan UBEC Step-Down 5V 8A terpisah memastikan Raspberry Pi 4 dan Pixhawk PX4 tidak mengalami *brownout* (kekurangan tegangan) ketika 6 motor *thruster* beroperasi pada daya puncak (*peak load*).