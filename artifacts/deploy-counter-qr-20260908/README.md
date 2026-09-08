# Persiapan deploy counter + QR

Status: belum diterapkan ke runtime. Pengguna meminta keduanya.

- Counter: `server/control_main.py` berjalan di laptop melalui `server/server.js`; Pi tidak memiliki proses/file server ini. GUI lokal port 8080 tidak aktif pada pemeriksaan.
- QR: `autonomy/vision/qr_gripper.py` dipetakan ke `/home/hydroships/rov-agent/vision/qr_gripper.py`; service `rov-vision-qr`.
- SHA256 target QR sebelum deploy: `7f7106840d9b1c88df4a447cf5783e16bf755aebac8e22b399aa1e35c2248415`.
- Backup yang direncanakan: `/home/hydroships/rov-agent/vision/qr_gripper.py.before-counter-qr-20260908` (belum dibuat).
- Pi cv2 5.0.0 belum menyediakan `wechat_qrcode_WeChatQRCode`; fallback baru membutuhkan contrib yang kompatibel, dengan backup environment sebelum perubahan dependensi.
- Telemetri pra-deploy: armed=true, mode=ALT_HOLD, control_mode=manual, PWM T1-T6=[1498,1501,1404,1591,1423,1498], depth=0.36 m.
- Suhu 84.7–85.2 C; throttled=0xe0008. Tidak ada perintah ARM, DISARM, mode, atau gerak dikirim.

Lanjutan: dapatkan kondisi DISARM dan netral; siapkan dependensi QR kompatibel, backup dan deploy sempit, verifikasi hash, restart worker QR saja, periksa log/service/telemetri. Aktifkan counter pada laptop GUI yang digunakan operator. Belum ada validasi hardware.
