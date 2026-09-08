# Deploy Pi dan pemeriksaan latency — 9 September 2026

**Deploy aktif**, bukan hanya staging. `rov_agent.py` dan `vision/qr_gripper.py`
diganti dari paket yang telah diverifikasi. Service `rov-agent` dan
`rov-vision-qr` direstart; `rov-vision-hook` tetap memakai proses dan OpenCV lama.
Sebelum tindakan dan sepanjang pengukuran sesudahnya: DISARM, control_mode manual,
mode Pixhawk ALT_HOLD, semua thruster PWM 1500, fc_link ok.

Backup: `/home/hydroships/rov-agent/backups/qr-recovery-20260908-2350/`.
Hash aktif:

- rov_agent.py: `959600532d44b3a32511fd0ecae4a388ea0a3813877029b670c8183c2e0cfbcd`
- vision/qr_gripper.py: `407bcec6bfb07374b350d08732107465daea39384bc64be8e8144e2422ac6a74`

OpenCV-contrib-headless 5.0.0.93 ARM64 dipasang offline di
`/home/hydroships/rov-agent/vendor/qr-opencv-headless-5.0.0.93`.
Drop-in `/etc/systemd/system/rov-vision-qr.service.d/qr-recovery.conf` menambahkan
lokasi tersebut ke PYTHONPATH **hanya pada worker QR**. Model produksi tetap
best_new_320.onnx / imgsz 320 / target 4 FPS / dua thread / core 2–3.
Hasil video 80/229 sebelumnya memakai imgsz 640, jadi bukan angka terukur untuk
konfigurasi Pi ini. Tidak ada perubahan parameter gerak, ARM, atau tuning gain.

## Hasil live sebelum/sesudah

Dua pengukuran 30 detik, masing-masing 300 paket. RTT memakai 10 ping.

| Metrik | Sebelum | Sesudah |
|---|---:|---:|
| Median interval telemetry | 100.14 ms | 100.20 ms |
| P95 interval telemetry | 102.47 ms | 103.37 ms |
| Maksimum interval telemetry | 108.84 ms | 109.94 ms |
| RTT ping rata-rata | 4.28 ms | 6.35 ms |
| RTT maksimum | 4.51 ms | 9.52 ms |
| Packet loss ping | 0/10 | 0/10 |
| Median interval pembaruan status QR | 877 ms | 942 ms |
| Maksimum interval pembaruan status QR | 1025 ms | 1448 ms |
| Median suhu Pi | 84.2°C | 84.2°C |

Telemetry kontrol tidak menunjukkan stall besar pada sampel ini. **Tidak benar
jika diklaim tidak ada peningkatan lag sama sekali.** Pembaruan QR melambat dan
RTT/jitter berubah. Interval status QR adalah selisih `vision_receipts.received`,
bukan waktu capture-to-decode: status no_detection memang dibatasi frekuensi
pengirimannya. Tidak ada QR decoded pada scene live saat pengukuran.

`vcgencmd get_throttled` sesudah deploy melaporkan `0xe0006`; sebelum juga ada
indikator pembatasan (`0xe0008`). Suhu selama pengukuran mencapai 85.2°C.
Kondisi thermal membatasi interpretasi benchmark dan kesiapan operasi kontinu.
Pengukuran tidak mencakup latency browser, kamera-ke-layar, atau response gerak;
dashboard lokal tidak berjalan pada port 8080. Tidak ada uji ARM/di air.

## Benchmark decode langsung di Pi

Worker QR dihentikan sementara selama benchmark, worker hook dan kontrol tetap
berjalan. Kedua versi diuji dengan OpenCV terisolasi yang sama, core 2–3, nice 10,
dua pengulangan per frame, median; biaya baca PNG tidak dihitung. Empat sampel
ini bukan benchmark statistik untuk seluruh video.

| Frame | Sebelum | Sesudah | Payload sebelum → sesudah |
|---|---:|---:|---|
| Tidak ada QR | 478 ms | 460 ms | — → — |
| QR jelas | 5.1 ms | 4.8 ms | C → C |
| QR sulit | 672 ms | 465 ms | — → C |
| QR berkabut | 435 ms | 794 ms | — → C |

Maksimum percobaan versi baru 812 ms. Ini masih di bawah gate umur 1 detik
untuk sampel offline tersebut, **tetapi umur antrian/capture belum termasuk**.
Frame berkabut membutuhkan tambahan sekitar 359 ms; frame gagal/berkabut juga
lebih lambat dari target periode 250 ms (4 FPS). Karena itu belum dapat disebut
bebas delay atau siap air berdasarkan pengujian ini.

## Bukti dan rollback

- `before.json`, `after.json`: sampel telemetry dan ringkasan.
- `pi-benchmark.json`: hasil kedua decoder.
- `service-after.txt`, `verification.txt`: konfigurasi, proses, hash, dan log.
- `ping-before.txt`, `ping-after.txt`: RTT dan packet loss.

Rollback, hanya setelah kembali memverifikasi DISARM dan PWM netral:

```bash
cd /home/hydroships/rov-agent
cp -p backups/qr-recovery-20260908-2350/rov_agent.py rov_agent.py
cp -p backups/qr-recovery-20260908-2350/qr_gripper.py vision/qr_gripper.py
sudo rm /etc/systemd/system/rov-vision-qr.service.d/qr-recovery.conf
sudo systemctl daemon-reload
sudo systemctl restart rov-agent.service rov-vision-qr.service
```

UI WALL berada pada checkout laptop; perubahan public/js sebelumnya tetap ada.
Tidak ada restart server GUI atau verifikasi browser pada deployment ini.
