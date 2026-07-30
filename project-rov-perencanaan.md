# ROV HYDROSHIPS Roadmap menuju KKI 2026

## Tujuan Utama

Mengembangkan sistem ROV berbasis Raspberry Pi 4B + Pixhawk 2.4.8 yang
stabil, modular, dan siap digunakan pada kompetisi KKI 2026.

------------------------------------------------------------------------

# Progress

## ✅ Selesai

### Sistem Komunikasi

-   [x] Telemetri Raspberry Pi ↔ Pixhawk (MAVLink)
-   [x] Dashboard ↔ Raspberry Pi (UDP)

### Sistem Kendali

-   [x] Kendali 6 DOF Thruster
-   [x] Pengaturan RPM minimum dan maksimum thruster
-   [x] Reverse arah putaran motor

### Sistem Visual

-   [x] Dual Camera Streaming

------------------------------------------------------------------------

# Tahap Berikutnya

## 1. Manipulator (Prioritas Tinggi)

### Goal

Mengendalikan gripper menggunakan servo melalui Pixhawk.

Target: - \[ \] Open gripper - \[ \] Close gripper - \[ \] Stop/Hold
position - \[ \] Mapping tombol pada joystick - \[ \] Pengujian mekanik

------------------------------------------------------------------------

## 2. Flight Mode (Prioritas Tinggi)

### Goal

Mendukung mode operasi Pixhawk.

Target: - \[ \] Stabilize - \[ \] Manual - \[ \] Depth Hold - \[ \] Alt
Hold (jika digunakan) - \[ \] Pergantian mode melalui dashboard

------------------------------------------------------------------------

## 3. Optimasi Kamera (Prioritas Tinggi)

### Goal

Mengurangi patah-patah (frame drop) pada streaming.

Target: - \[ \] Optimasi resolusi - \[ \] Optimasi FPS - \[ \] Optimasi
bitrate MJPEG - \[ \] Pengujian bandwidth Ethernet - \[ \] Sinkronisasi
dual camera

------------------------------------------------------------------------

# Tahap Pengembangan Selanjutnya

## 4. Dashboard Monitoring

Target: - \[ \] Status Pixhawk - \[ \] Status Camera 1 - \[ \] Status
Camera 2 - \[ \] Status UDP - \[ \] Ping - \[ \] CPU Raspberry Pi - \[
\] RAM - \[ \] Temperatur Raspberry Pi

------------------------------------------------------------------------

## 5. Fail Safe

Target: - \[ \] Thruster otomatis netral saat koneksi terputus - \[ \]
Gripper berhenti saat komunikasi hilang - \[ \] Heartbeat monitoring -
\[ \] Auto reconnect MAVLink

------------------------------------------------------------------------

## 6. Logging

Target: - \[ \] Log telemetri - \[ \] Log command thruster - \[ \] Log
command gripper - \[ \] Log perubahan mode - \[ \] Log error

------------------------------------------------------------------------

## 7. Konfigurasi Sistem

Target: - \[ \] File konfigurasi (JSON) - \[ \] Konfigurasi port
MAVLink - \[ \] Konfigurasi kamera - \[ \] Konfigurasi UDP - \[ \]
Konfigurasi RPM thruster

------------------------------------------------------------------------

## 8. Struktur Project

Target: - \[ \] Modularisasi source code - \[ \] Folder modules - \[ \]
Folder config - \[ \] Folder logs - \[ \] requirements.txt

------------------------------------------------------------------------

## 9. Pengujian

### Functional Test

-   [ ] Thruster
-   [ ] Gripper
-   [ ] Kamera
-   [ ] Telemetri
-   [ ] Flight Mode

### Stress Test

-   [ ] Boot otomatis
-   [ ] Restart service
-   [ ] Putus-sambung Ethernet
-   [ ] Putus-sambung Pixhawk
-   [ ] Pengujian durasi operasi

------------------------------------------------------------------------

# Target Akhir KKI 2026

## Sistem Minimum

-   [x] Telemetri
-   [x] Kendali 6 DOF
-   [x] Pengaturan RPM
-   [x] Reverse Motor
-   [x] Dual Camera
-   [ ] Gripper
-   [ ] Flight Mode
-   [ ] Kamera stabil
-   [ ] Fail Safe
-   [ ] Dashboard Monitoring
-   [ ] Logging
-   [ ] Auto Reconnect
-   [ ] Dokumentasi

------------------------------------------------------------------------

## Catatan

Prioritas pengembangan:

1.  Gripper
2.  Flight Mode
3.  Optimasi Kamera
4.  Fail Safe
5.  Logging
6.  Dashboard Monitoring
7.  Konfigurasi Sistem
8.  Pengujian Menyeluruh
9.  Finalisasi untuk KKI 2026
