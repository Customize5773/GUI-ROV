# Optimasi latency QR — 9 September 2026

Sudah dideploy ke `/home/hydroships/rov-agent/vision/qr_gripper.py`.
SHA256: `bc2ad43e8825ab7cb49bd09479dc8e2547823888f6f101e5b0c2cd9104cd4340`.
Hanya `rov-vision-qr.service` direstart. Backup versi sebelumnya:
`/home/hydroships/rov-agent/backups/qr-latency-20260909/before.py`.
Environment OpenCV headless terisolasi dan konfigurasi worker tetap seperti
deployment sebelumnya: model 320, target 4 FPS, core 2–3, dua thread.

## Perubahan

Pass awal ZXing seluruh ROI tetap diutamakan untuk QR yang sudah jelas.
Setelah YOLO memberi kandidat, crop kecil WeChat dicoba sebelum enam pass
ZXing yang lebih lebar. Untuk bbox dengan sisi maksimum kurang dari 80 piksel,
kanal hijau 3× dicoba lebih dahulu. Seluruh fallback yang lama tetap tersedia;
tidak ada payload cache, pelonggaran gate usia, perubahan ROI, ataupun
perubahan kendali wahana.

## Bukti offline

Replay sequential pada 229 timestamp video yang sama, menggunakan model 640:
**80/229 sebelum dan sesudah**, tanpa kehilangan keberhasilan sebelumnya,
semua hasil payload C. P95 CPU laptop **174.74 → 165.42 ms**, median
**87.23 → 84.57 ms**. Ini satu pengukuran per frame tiap versi; bukan statistik
hardware Pi. Detail: `video-comparison.json`.

Benchmark Pi memakai empat frame dan model produksi 320. Worker QR berhenti
sementara untuk benchmark dan otomatis kembali berjalan sesudahnya; worker
hook dan kontrol tetap aktif. Tiga pengulangan berpasangan, urutan versi
dibalik pada pengulangan kedua, median proses tanpa pembacaan PNG:

| Frame | Sebelum | Sesudah | Payload |
|---|---:|---:|---|
| Tanpa QR | 508.6 ms | 429.2 ms | keduanya kosong |
| QR jelas | 11.5 ms | 10.9 ms | C → C |
| QR sulit | 478.8 ms | 379.6 ms | C → C |
| QR berkabut | 840.9 ms | 618.1 ms | C → C |

Frame berkabut sekitar **26.5% lebih cepat**, maksimum sesudah 630.1 ms.
Sampel kecil dan thermal/penjadwalan dapat memengaruhi selisih; jalur QR jelas
secara logika tidak berubah. Detail pengulangan: `pi-benchmark.json`.

## Validasi dan batas

82 tes QR/worker/pipeline/validator lulus; kompilasi Python dan diff-check lulus.
Pemeriksaan deployment memakai lima paket live DISARM, PWM T1–T6 1500, dan
fc_link ok sebelum mengganti file. Tidak ada perintah ARM/gerak atau perubahan
kode kontrol lain.

Pi masih panas sekitar 84–85°C dengan indikator throttling. **618 ms masih
lebih lama dari periode target 250 ms (4 FPS)**. Waktu offline belum mencakup
antrean capture; bukan jaminan frame live selalu lolos gate umur 1 detik.
Tidak ada uji underwater atau pengukuran latency kamera-ke-layar.

Pengukuran dashboard: 299 paket per sesi, median interval **100.17 → 100.14 ms**,
P95 **103.64 → 102.39 ms**, maksimum **113.59 → 108.09 ms**. Semua sampel
DISARM, PWM 1500 dan fc_link ok; tidak tampak peningkatan jitter telemetry
pada dua sesi pendek ini. Tidak ada restart otomatis (NRestarts=0).

`before.json` dan `after.json` merekam pembaruan telemetry WebSocket selama
30 detik pada dashboard yang berjalan. Itu interval kedatangan paket, bukan
latency perintah atau waktu render browser. `service.txt` merekam status/hash
service aktif dan thermal. Jalankan `node .../measure-ws.cjs output.json` dari
root repo untuk pengukuran pasif yang sama; tidak mengirim command kendaraan.

Rollback setelah verifikasi DISARM/netral:

```bash
cd /home/hydroships/rov-agent
cp -p backups/qr-latency-20260909/before.py vision/qr_gripper.py
sudo systemctl restart rov-vision-qr.service
```
