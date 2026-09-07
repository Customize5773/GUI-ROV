# QR servo area gripper

Worker QR sekarang mencari kandidat YOLO pada crop area gripper. Default ROI
`0.25,0.10,0.75,0.80` berarti x=25–75% dan y=10–80% frame asli.
Ini profil awal dari rekaman meja, bukan kalibrasi kamera bawah air.
`--gripper-roi` atau `QR_GRIPPER_ROI` mengubah area tersebut; `0,0,1,1`
memakai seluruh frame. CLI menang atas environment.

Kandidat YOLO mulai confidence 0.1 (`--proposal-conf` / `QR_PROPOSAL_CONF`)
hanya diteruskan jika QR di dalam bounding box berhasil di-decode.
Region tanpa decode tetap membutuhkan `--conf` (default 0.6).
Decode dari QR tetangga di luar bbox tidak boleh meloloskan kandidat lemah.
Semua bbox, pusat, luas dan sudut dikembalikan ke koordinat frame asli
sebelum telemetry dan estimasi pose. Ukuran kalibrasi tetap ukuran frame asli.

`servo_hook.target_x_norm` di `server/control_config.yaml` menentukan pusat
mulut gripper dibagi lebar frame. Default 0.5 dipertahankan karena rekaman
belum mengukur pusat tangkapan secara pasti. Misalnya hasil pengukuran x=704
pada frame 1280 berarti 0.55. Konfigurasi dibaca ulang saat autonomous aktif.
Sway dan gate surge memakai titik ini. Nilai tidak valid menghentikan config.
Ambang luas untuk menutup gripper tidak diubah oleh crop.

## Hasil lokal 8 September 2026

Video: `hydroship_record_1788810021708.webm`, 1280x720.
Sampling berdasarkan timestamp minimal 1 detik antarsampel: 36 sampel.
Timestamp meloncat dari sekitar 27 ke 66 detik; bukan 74 frame sampel.

| Jalur pada sampel yang sama | Region diterima | Decode |
| --- | ---: | ---: |
| Lama: full frame, best_new_320.onnx, conf 0.6 | 0/36 | 0/36 |
| Baru: crop gripper + kandidat tervalidasi decode | 16/36 | 16/36 |

Seluruh decode menghasilkan `C`. Ini bukan bukti bahwa gate payload Mission 5
menerima teks tersebut; kontrak payload Mission 5 tetap berlaku.
20 sampel belum menghasilkan deteksi. Angka ini adalah tingkat deteksi sampel,
bukan recall teranotasi atau jaminan tangkapan fisik. Belum ada validasi Pi,
latensi runtime di Pi, arah thruster fisik, atau penutupan gripper di kolam.

185 tes dan 18 subtest lulus (servo, crop gripper, worker QR, environment,
dan Mission 5). Hasil replay tersimpan di `logs/qr-gripper-replay/`:
CSV per sampel, summary JSON, dan frame dengan kotak deteksi/ROI.

## Analisis lanjutan dan perbandingan resolusi

Benchmark memakai 36 frame asli yang sama, ROI dan confidence sama, OpenCV
2 thread, satu warm-up per model, dan tiga pengulangan per frame. Waktu
mencakup YOLO + decode/gate, tidak mencakup pembacaan video. Ini waktu CPU
laptop, bukan hasil Raspberry Pi. Tidak ada perubahan model produksi.

| Model ONNX | Decode | Tanpa proposal YOLO | Gagal decode/gate bbox | Median ms | P95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 320 | 16/36 | 19 | 1 | 21.34 | 42.29 |
| 416 | 14/36 | 21 | 1 | 34.39 | 49.73 |
| 640 | 13/36 | 4 | 19 | 98.64 | 114.82 |

Tidak ada region tanpa decode yang diterima. P95 dihitung atas median tiga
pengulangan tiap frame. Model 640 menghasilkan lebih banyak proposal, tetapi
proposal itu tidak otomatis menjadi QR valid. Model 320 paling baik pada
profil crop dan rekaman ini; hasil ini tidak membandingkan full-frame 640.

Koreksi interpretasi terhadap 20 sampel tanpa deteksi pada model 320:

- Enam frame pada 3.018, 8.090, 15.186, 18.250, 20.286, dan 22.289 detik:
  decoder langsung di ROI membaca `C`, tetapi YOLO tidak memberi proposal.
  Ini bukti kehilangan deteksi di tahap YOLO, bukan QR tidak terbaca.
- Dua frame pada 16.218 dan 17.220 detik: payload tampak diputar/miring;
  decoder langsung juga gagal. Ini observasi visual, bukan pengukuran blur.
- Empat frame pada 24.296–27.335 detik: payload turun mendekati gripper,
  QR kecil/miring atau sebagian terhalang; decoder langsung juga gagal.
- Delapan frame pada 66.399–73.485 detik: payload besar yang sedang diuji
  tidak lagi terlihat. Tidak mendeteksi pada frame ini bukan bukti kegagalan.

Decoder langsung membaca 22/36 sampel, seluruhnya `C`. Karena belum ada
anotasi ground truth tiap bbox, angka di atas bukan precision/recall.
Hasil menunjukkan prioritas berikutnya adalah fallback decode ROI yang
tervalidasi saat YOLO tidak menemukan kandidat, serta data latihan sudut
dan posisi dekat gripper. Jangan menurunkan confidence region tanpa decode.
Fallback tersebut belum dihubungkan ke kendali: kontrak telemetry saat ini
mewajibkan method `yolo_qr`, sehingga hasil decoder langsung perlu provenance
yang benar dan pengujian gate sebelum menjadi acuan gerak.

Artefak: `logs/qr-gripper-benchmark/comparison.csv`, `summary.json`, dan
`failed_320.jpg` (contact sheet untuk memeriksa 20 sampel tanpa deteksi).
Reproduksi:

```powershell
.venv/Scripts/python.exe autonomy/tools/benchmark_gripper_qr.py 'C:/Users/ivand/Downloads/hydroship_record_1788810021708.webm' --output logs/qr-gripper-benchmark
```

Jalankan ulang dari root proyek (offline, tanpa mengirim perintah ROV):

```powershell
.venv/Scripts/python.exe autonomy/tools/replay_gripper_qr.py 'C:/Users/ivand/Downloads/hydroship_record_1788810021708.webm' --output logs/qr-gripper-replay
.venv/Scripts/python.exe -m pytest test_control_servo_hook.py autonomy/tests/test_qr_gripper.py autonomy/tests/test_qr_vision_worker.py autonomy/tests/test_worker_env_knobs.py autonomy/tests/test_mission5.py -q -p no:cacheprovider
```
