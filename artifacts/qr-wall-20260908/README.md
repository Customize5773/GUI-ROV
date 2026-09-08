# Evaluasi QR WALL — 8 September 2026

Video: `/home/rasya/Music/H8/hydroship_record_1788880841762.webm` (1280×720).
Replay offline menggunakan `detect_gripper_qr(..., live=True)`, model
`autonomy/vision/best_new.onnx`, imgsz 640, proposal confidence 0.1,
region confidence 0.6, ROI `(0.25, 0.10, 0.75, 0.80)`.

229 sampel sepanjang video sampai timestamp 245.516 detik. Target sampling
1 detik berdasarkan timestamp video (bukan metadata FPS WebM yang terbaca 1000).
Evaluator membaca berurutan dan mengambil frame pertama setelah target, lalu
menambah target 1 detik; bila timestamp melompat, sampel berikutnya dapat lebih
rapat. Timestamp setiap sampel tersedia di `comparison.csv`. Baseline menggunakan
frame asli; pengujian sesudah perubahan menggunakan salinan PNG lossless dari
frame yang sama. JPEG tidak digunakan untuk angka perbandingan akhir.

| Hasil | Sebelum | Sesudah |
|---|---:|---:|
| Payload berhasil dibaca | 67/229 (29.26%) | 70/229 (30.57%) |
| Payload | C | C |
| Median proses/frame, CPU lokal | 78.31 ms | 77.47 ms |
| P95 proses/frame, CPU lokal | 101.96 ms | 118.65 ms |

Tambahan sukses pada 17.000, 36.001, dan 124.002 detik; tidak ada sampel sukses
sebelumnya yang menjadi gagal. Waktu adalah satu pengukuran per frame,
OpenCV 2 thread, tanpa waktu membaca video; bukan benchmark Raspberry Pi.
Tambahan pass meningkatkan P95. Angka mencakup bagian video ketika QR tidak
terlihat; bukan tingkat keberhasilan khusus frame dengan QR terlihat.

Perubahan: satu percobaan pembesaran sampai 2× (maksimum sisi 1280) setelah
jalur live sebelumnya gagal. Decode tetap melalui validator ZXing yang ada;
koordinat dikembalikan ke ROI asli. Area kendali tidak diperluas.

Visual control WALL kini menerima bbox dan identitas kamera dari validator
telemetry, menampilkan confidence YOLO, payload, dan pembeda region/decode.
Umur telemetry Pi ditambah waktu lokal membatasi overlay sampai 1 detik;
hasil BOTTOM tidak digambar di WALL. Decoder fallback diberi label QR tanpa
mengklaim confidence YOLO.

Replay produksi untuk inspeksi lanjutan (sampling tool ini minimal 1 detik
antarframe, sehingga jumlah sampelnya dapat berbeda dari evaluasi di atas):

```bash
.venv/bin/python autonomy/tools/replay_gripper_qr.py \
  /home/rasya/Music/H8/hydroship_record_1788880841762.webm \
  --model autonomy/vision/best_new.onnx --imgsz 640 --live \
  --step 1 --output /tmp/qr-wall-replay
```

Validasi: 76 tes Python QR/worker/pipeline/validator lulus; tes Node decoder QR,
overlay, dan forwarding telemetry lulus; kompilasi Python, sintaks app.js, dan
`git diff --check` lulus. Tes UDP awal terblokir sandbox dan berhasil saat
pengujian lokal dijalankan dengan izin socket. Belum deploy Raspberry Pi,
belum inspeksi browser live, dan belum validasi kamera/gerak di air.


Evaluasi lanjutan: [pemulihan WeChat, 80/229](recovery/README.md).
