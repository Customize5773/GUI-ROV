# Validasi TIMEOUT_SCAN=30s — langkah trial kolam fisik

Blocked sejak 26 Agu 2026 (lihat memory `timeout-scan-validation-blocked`):
tak ada CSV `DetectionCsvLogger` di repo, karena tool yang menghasilkannya
(`tools/servo_webcam_test.py` / `pose_webcam_test.py`) belum pernah dijalankan
saat trial fisik — bukan dijalankan lewat `mission5.py` (`--run-log` FSM itu
logger JSONL beda skema, tidak kompatibel dengan `analyze_detection_log.py`).

Ikuti langkah ini di kolam nyata, dengan hardware sungguhan (kamera DWE, bukan
webcam laptop kalau bisa — kalau situasinya cuma webcam USB sementara, tetap
oke untuk validasi TIMEOUT saja, bukan untuk validasi FOV/kalibrasi).

## 1. Rekam CSV deteksi selama QR payload discan

Dari direktori `autonomy/`:

```
python tools/servo_webcam_test.py \
  --device 0 \
  --calib vision/calibration/dwe_trial2.npz \
  --qr-size 0.04 \
  --csv log-m5/scan_trial_$(date +%Y%m%d_%H%M).csv \
  --cam-width 1280 --cam-height 720
```

Ganti `--device` sesuai indeks kamera pool-rig. Biarkan berjalan cukup lama
untuk menangkap beberapa siklus "cari QR dari jauh → decode berhasil", bukan
cuma satu percobaan — makin banyak sampel makin bisa dipercaya distribusinya.
Tutup dengan Ctrl+C saat selesai (logger cetak ringkasan rate saat `.close()`).

Ulangi untuk beberapa kondisi kalau memungkinkan (siang/sore, air keruh/jernih)
— `TIMEOUT_SCAN` harus aman untuk kondisi terburuk yang realistis, bukan
cuma kondisi terbaik.

## 2. Analisis distribusi waktu-decode

```
python tools/analyze_detection_log.py log-m5/scan_trial_*.csv --bin-size 0.1
```

Yang perlu dilihat: bukan `dist`/`area` histogram itu sendiri, tapi **berapa
lama sejak QR pertama masuk frame sampai decode sukses** (`elapsed_s` di CSV,
cari transisi `detected=False→True` per attempt). Kalau perlu, tulis skrip
kecil terpisah untuk ekstrak durasi ini per attempt — `analyze_detection_log.py`
saat ini hanya meringkas jarak/area, bukan durasi (lihat keterbatasan yang
sudah dicatat di docstring file itu).

## 3. Putuskan nilai TIMEOUT_SCAN

- Kalau P95 durasi decode-sukses jauh di bawah 30s (mis. <15s) → `TIMEOUT_SCAN`
  bisa diturunkan, kurangi waktu misi terbuang saat QR memang tak ada di sana.
- Kalau ada attempt yang butuh mendekati atau melebihi 30s → naikkan lagi, atau
  investigasi kenapa (tier adaptive-threshold kelamaan? posisi approach salah?).
- Update `autonomy/config/rov_tuned.yaml` kunci `timeouts.scan`, commit dengan
  catatan nilai empiris yang dipakai (bukan tebakan lagi).

## 4. Tutup memory

Setelah nilai final didapat, update `timeout-scan-validation-blocked.md` —
ganti status dari "blocked" jadi hasil final, atau hapus file itu dan buat
memory baru `timeout-scan-validated.md` merangkum angka yang dipakai + tanggal
trial.
