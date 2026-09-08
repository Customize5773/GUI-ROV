# Pemulihan QR lanjutan

Video, ROI, model YOLO, imgsz, dan 229 timestamp sama dengan evaluasi pertama.
Perubahan sebelumnya (upscale ZXing) dipertahankan. Tambahan terbaru adalah
fallback **WeChatQRCode** setelah decode kandidat YOLO dengan ZXing gagal.

Empat percobaan: grayscale asli, grayscale 2×, hijau 3×, dan contrast-stretch
3×, pada crop bbox dengan margin 25%, maksimum sisi hasil 640 piksel. Semua
hasil berasal dari frame saat ini; tidak ada payload hardcoded atau hasil
tracking lama yang dihitung sebagai decode baru. Geometri hasil diperiksa dan
skalanya dikembalikan sebelum gate bbox/ROI yang sudah ada. Dua hasil QR dalam
kandidat tidak dipilih sembarangan.

OpenCV-contrib sudah tercantum di `autonomy/requirements.txt`. Instalasi lokal
5.0.0.93 sebelumnya tertimpa opencv-python; contrib versi yang sama dipasang
ulang dari cache. Kedua paket menulis modul `cv2` yang sama: periksa
`hasattr(cv2, 'wechat_qrcode_WeChatQRCode')` setelah instalasi environment.
Jika modul tidak tersedia, jalur lama tetap bekerja dan warning recovery
unavailable tercatat. Tidak ada unduhan model saat runtime.

Sumber API: [OpenCV WeChatQRCode](https://docs.opencv.org/4.10.0/d5/d04/classcv_1_1wechat__qrcode_1_1WeChatQRCode.html).

## Hasil

- Baseline awal: **67/229 = 29.26%**.
- Setelah upscale ZXing: **70/229 = 30.57%**.
- Setelah fallback WeChat: **80/229 = 34.93%**, seluruh payload `C`.
- Tidak ada keberhasilan sebelumnya yang hilang.
- Benchmark berpasangan, median proses/frame: **78.58 → 81.08 ms**.
- P95 proses/frame: **95.33 → 166.79 ms**. Tambahan pass lebih mahal pada
  frame sulit; angka CPU laptop ini bukan hasil Raspberry Pi.
- Detail berpasangan tersedia di `comparison.csv`.
- Tambahan sampel indeks: 18, 27, 67, 68, 76, 77, 79, 89, 131, 132.

**Persentase tinggi untuk seluruh video belum tercapai.** Denominator tetap
mencakup gambar QR hilang, terpotong, dan kabur; hasil ini tidak diganti dengan
persentase subset yang lebih menguntungkan. Inspeksi contact sheet seluruh
video menunjukkan segmen panjang tanpa QR setelah sekitar sampel 134.
Belum ada ground-truth anotasi per-frame yang diaudit operator untuk menyatakan
recall khusus QR yang terlihat.

Eksplorasi offline mencakup cascade decode lama, penajaman, adaptive threshold,
kanal warna/binarizer, variasi margin crop, super-resolution resmi OpenCV, dan
registrasi/penggabungan beberapa frame. Super-resolution tidak memberi tambahan
atas kombinasi terpilih pada probe ini. Temporal fusion menghasilkan beberapa
kandidat tambahan, tetapi belum memenuhi validasi geometri untuk kendali; tidak
dimasukkan ke runtime. Probe tidak dihitung sebagai hasil produksi.

## Reproduksi

```bash
.venv/bin/python autonomy/tools/replay_gripper_qr.py \
  /home/rasya/Music/H8/hydroship_record_1788880841762.webm \
  --model autonomy/vision/best_new.onnx --imgsz 640 --live \
  --samples-from artifacts/qr-wall-20260908/comparison.csv \
  --output /tmp/qr-wall-recovery-replay
```

Opsi `--samples-from` membaca berurutan mengikuti timestamp yang sama, tanpa
seeking WebM dan tanpa mengandalkan metadata FPS. Script `evaluate_paired.py`
mengukur baseline sebelumnya versus versi terbaru, tiga pengulangan per frame,
OpenCV dua thread, median pengulangan, tanpa biaya membaca PNG. Baseline hanya
menonaktifkan fallback baru; semua jalur lain identik.

Validasi: **80 tes Python lulus**, termasuk fixture video berkabut, pemulihan
koordinat/skala, kegagalan decode setelah sukses (tidak menahan payload), batas
ukuran/jumlah percobaan, dan penolakan dua payload. Kompilasi Python dan
`git diff --check` lulus. Belum deploy Pi atau diuji kamera live/di air.
