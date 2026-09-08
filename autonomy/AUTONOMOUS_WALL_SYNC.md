# Sinkronisasi Autonomous WALL — 8 September 2026

Jalur tombol Autonomous menggunakan server/control_main.py, bukan runner
Mission5 legacy di Pi. Worker QR aktif di Pi telah dipindah ke WALL port
8080 dengan role WALL dan vision/calibration/wall.npz. Model tetap
best_new_320.onnx, conf region 0.6, proposal 0.1, batas FPS 4.
Drop-in yang dipasang: config/qr-wall-service.conf ->
/etc/systemd/system/rov-vision-qr.service.d/wall-camera.conf.
Backup worker sebelumnya: /home/hydroships/rov-agent/backups/qr-wall-0908/.

Kontrol lokal target_x_norm=0.475, sama dengan pusat X pratinjau WALL.
Konfigurasi dimuat ulang saat Autonomous diaktifkan. Y tetap gate grab,
bukan kendali heave. grab_roi_norm dan kedua ambang luas tetap null:
belum ada pengukuran toleransi tangkap/luas yang membenarkan auto-grab.
Visual pratinjau tidak mengaktifkan grab.

Verifikasi: 254 tes + 18 subtest lulus. Lima tes lama diperbarui mengikuti
kepemilikan control_main, konstanta watchdog, serta dependensi validator;
bukan menghidupkan runner kedua agar tes lama lulus. Service QR active,
NRestarts=0 setelah pemasangan. Telemetry diterima 40 paket dalam 4 detik
melalui ws://[::1]:8080; ROV DISARM, MANUAL. IPv4 localhost:8080 di komputer
ini juga dipakai proses VS Code sehingga probe sebelumnya tidak sampai Node.

Journal worker setelah pemasangan masih no_detection. Cache qr_region
berumur sekitar 59 detik, bukan pengamatan baru; gate umur menolaknya.
Jadi penyamaan kamera selesai, tetapi deteksi payload live dan kemampuan
grab fisik belum terbukti. Tidak ada perintah ARM/Autonomous/gerak dikirim.
