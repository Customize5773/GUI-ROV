# Verifikasi deploy counter + QR — 9 September 2026

Counter aktif di laptop melalui service pengguna transient `gui-rov-counter.service`.
Dashboard: http://localhost:8080. Service menjalankan server/server.js dan
server/control_main.py terbaru. Service transient tidak otomatis dibuat kembali
setelah reboot. Untuk menghentikan: `systemctl --user stop gui-rov-counter`.

Enam langkah literal dipertahankan, termasuk depth 1.0 dan surge -500 selama
3 detik. Tidak ada perintah ARM atau AUTONOMOUS dikirim.

QR Pi sudah identik dengan lokal; tidak ditimpa/restart ulang karena versi yang
diminta telah aktif sejak 8 September 23:56 WIB. Target:
`/home/hydroships/rov-agent/vision/qr_gripper.py`.
SHA256: 407bcec6bfb07374b350d08732107465daea39384bc64be8e8144e2422ac6a74.
Service `rov-vision-qr`: active, PID 8437, NRestarts=0.
Backup tersedia di `/home/hydroships/rov-agent/backups/qr-recovery-20260908-2350`.

Environment service memakai PYTHONPATH
`/home/hydroships/rov-agent/vendor/qr-opencv-headless-5.0.0.93:/home/hydroships/rov-agent`.
Dengan environment tersebut, cv2 5.0.0 menyediakan WeChatQRCode dan konstruksi
decoder berhasil. Pemeriksaan environment dasar Pi saja menghasilkan False;
itu bukan environment worker aktif. Log kamera terakhir menunjukkan no_detection,
bukan bukti keberhasilan decode atau grab.

Verifikasi lokal: 51 tes counter/otoritas dan 20 tes QR lulus; socket counter
dimock. git diff --check lulus.

Telemetri akhir via WebSocket GUI: armed=false, mode=ALT_HOLD,
control_mode=manual, T1–T6 seluruhnya 1500, pi_temp=84.7 C.
Pembacaan SSH sebelumnya 85.7 C, throttled=0xe0008.
Joystick F310 tidak ditemukan; joystick.py keluar dengan code 0.
Pengaman abort F310 tidak tersedia hingga perangkat terhubung dan proses
joystick dijalankan ulang. Tidak dilakukan uji autonomous/gerak/gripper/air.
