# Uji Vision Hook di Darat

## Tujuan

Memastikan GUI menampilkan CAM WALL, worker YOLO laptop aktif, dan bbox + 6
keypoint hook tampil di live camera.

## Konfigurasi otomatis

- Kamera Control awal: `http://192.168.2.2:8080/stream` (CAM WALL)
- Model: `autonomy/vision/best_pose.pt`
- Pemrosesan: laptop
- Overlay: bbox + keypoint `0=open_0` sampai `5=hook_tip`
- Control mode: `MANUAL`

## Prasyarat yang mudah terlewat

- **Raspberry Pi harus mengirim telemetri**, bukan sekadar menyalakan kamera.
  Overlay hook ikut menumpang paket telemetry (`hook_xy`); sejak overlay juga
  mendengarkan kanal `hook_vision` sendiri, kamera + worker saja sudah cukup —
  tapi panel Mission 5 dan HUD tetap kosong tanpa telemetri Pi.
- **Jangan tekan "Terapkan" di kartu Camera halaman Setup kecuali memang mengubah
  URL.** Tombol itu dulu selalu melempar Control kembali ke CAM 1 (BOTTOM) dan
  overlay hilang tanpa pesan; sekarang ia mempertahankan kamera yang sedang
  tampil.
- Worker YOLO dijalankan `npm start` memakai `python3` di Linux / `python` di
  Windows. Interpreter itu yang harus punya `ultralytics` (`pip install -r
  autonomy/requirements-laptop.txt`), bukan Python lain.

## Prosedur

1. Letakkan ROV di dudukan yang stabil dan jauhkan tangan dari propeller.
2. Nyalakan kamera, Raspberry Pi, dan GUI.
3. Pastikan live camera menampilkan CAM WALL.
4. Arahkan hook ke kamera pada beberapa jarak dan sudut.
5. Periksa kotak deteksi, enam titik keypoint, dan confidence.
6. Rekam video/screenshot serta telemetri untuk evaluasi.

## Uji rantai YOLO → thruster (tanpa ROV)

Menonton overlay hanya membuktikan YOLO *melihat*. Untuk membuktikan YOLO
*menyetir dengan halus* sebelum masuk air:

```
python3 tools/hook_thruster_darat.py --camera http://192.168.2.2:8080/stream
```

Alat itu menjalankan rantai produksi apa adanya — YOLO laptop → validator batas
Pi (`rov_agent._validate_hook_vision`) → `Mission5FSM` M5_YOLO_SEARCH/
M5_HOOK_ALIGN — lalu MENCATAT perintah thruster alih-alih mengirimnya. Vonis:
tiap sumbu ≤ `SERVO_MAX_SPEED` (35 %), laju perubahan ≤ `SERVO_SLEW` (120 %/s),
dan surge tergerbang selagi belum center. Aman dijalankan dengan ROV mati.

ARM tidak diperlukan untuk uji vision. Jika pengujian ARM/telemetry tetap
dilakukan, lepaskan propeller atau gunakan rig mekanis yang benar-benar aman,
dan siapkan tombol `STOP`.

Hasil uji ini memvalidasi kamera, deteksi, dan HALUSNYA perintah thruster yang
dihasilkan YOLO. Tidak memvalidasi depth-hold, arah/tanda sumbu terhadap air,
maupun X/Y global — ketiganya baru tertutup loop-nya di kolam.
