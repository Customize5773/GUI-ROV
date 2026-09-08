# QR gripper: perbaikan deteksi dan validasi kalibrasi

## Status terbaru: profil diterapkan

### Koreksi setelah trial tidak close

Log trial menunjukkan streak 7/10 reset ketika Y=378.75 px, hanya 0.25 px
melewati batas 378.5 px, kemudian timeout 3 detik. Tidak ada perintah close
yang dikirim dalam trial itu. Profil kini memiliki `grab_hysteresis_px=1`:
entry tetap di rectangle asli; saat streak sudah berjalan, noise tepi <=1 px
tidak menghapus progres. Close tetap wajib di dalam rectangle asli. Keluar
lebih dari band, deteksi basi, luas tidak valid atau lateral meleset tetap
mereset streak. Tidak ada perluasan area entry/close fisik.

Timeout servo sekarang 6 detik, agar 10 pengamatan pada 4 FPS mendapat waktu
akuisisi dan retry. Surge tetap nol selama menunggu kesiapan dalam band.
270 tes + 18 subtest lulus, termasuk regresi trial 0.25 px, menolak entry
dan close di luar rectangle, reset saat benar-benar keluar, dan akuisisi
yang lebih lambat dari timeout lama. Ini menggantikan angka timeout 3 detik
dan jumlah tes pada catatan tahap sebelumnya di bawah.

Profil bersama `shared/grab-calibration.json` kini dibaca control_main dan
visual CONTROL. Nilai JSON menimpa nilai cadangan null di YAML. Profil meja
ini berlaku untuk WALL 1280x720, QR `C`, dengan label pada posisi yang telah
diukur. Target X=0.477; ROI memakai envelope pengamatan terkonfirmasi di
bawah; ambang decoded >0.006488444010416667 dan batas maksimum
<=0.011424696180555555. Region tanpa decode tetap tidak dapat memicu grab.
Resolusi lain dan teks QR selain C ditolak oleh kontrol profil ini.

Kontrol menghentikan surge selama menunggu frame siap grab, serta ketika
luas melebihi batas terdekat yang diukur. Koreksi sway tetap menggunakan
toleransi lateral; batas kiri yang terukur masih harus dikoreksi menuju
tengah sebelum close. `centered_ticks=10` menghitung pengamatan baru, bukan
paket cache. Pada worker 4 FPS, dibutuhkan sekitar 2.5 detik pengamatan.

266 tes + 18 subtest lulus. `test_grab_profile.py` memasukkan rekaman hasil
deteksi ke CASE 4/5 asli dengan jam virtual dan penampung paket tanpa IO
kendaraan. Far/near/right/middle/middle_recheck menghasilkan tepat satu
close; left/outside tidak. Skenario terlalu dekat, salah teks, dan Y di
luar area juga tidak close. Ini uji software pada data kalibrasi, bukan
uji tangkap fisik atau data independen; jangan menganggap seluruh kombinasi
posisi dalam rectangle telah diuji.

Server lokal dimuat ulang; log menunjukkan config dimuat tanpa error.
Endpoint `/shared/grab-calibration.json` mengembalikan 200 dan profil yang
sama. Telemetry sesudah restart: 20 paket/2 detik, DISARM, MANUAL, QR C.
Autonomous tidak diaktifkan dan tidak ada perintah grab uji dikirim.
GUI perlu dimuat ulang untuk mendapatkan profil baru. Batas misi tetap
target depth 1 m dan servo timeout 3 detik; penerapan profil bukan validasi
parameter gerak atau jaminan docking di air.

Bagian di bawah adalah hasil tahap pengukuran sebelum penerapan profil.

8 September 2026. Kamera WALL 1280x720; posisi ditentukan pengguna saat
DISARM. Label QR telah dipindah agar terlihat. Ini kalibrasi meja, belum
validasi gerak/tangkapan fisik di air.

## Jalur yang dipasang

Satu pass ZXing pada ROI gripper dijalankan lebih dahulu. Hasil wajib lolos
checksum dan quiet-zone decoder. Jika gagal, jalur YOLO + decode crop tetap
berjalan; bila masih gagal, decoder mencoba ROI gripper penuh. Hasil decode
mandiri memakai method `qr_decode`, bukan `yolo_qr`. Confidence 0 berarti
tidak ada skor YOLO, bukan skor probabilitas decoder. Validator tetap
mewajibkan teks, geometri finite, dan batas frame. Fallback dengan beberapa
QR ditolak agar tidak memilih target sembarang. Tidak ada prediksi posisi
lama yang dijadikan deteksi baru.

Worker dan validator Pi diperbarui; bridge Mission5 menerima method baru,
tetapi gate isi payload tidak dilonggarkan. Teks `C` bukan bukti bahwa gate
payload Mission5 terpisah akan menerima target. Jalur Autonomous CONTROL
tetap memakai control_main.py.

## Replay data yang sudah dikumpulkan

| Posisi | Jalur lama decode | Jalur baru decode | Gate geometri kandidat |
| --- | ---: | ---: | ---: |
| Terjauh masih terjangkau | 15/15 | 15/15 | 15/15 |
| Terdekat | 15/15 | 15/15 | 15/15 |
| Batas kiri | 6/15 | 15/15 | 15/15 |
| Batas kanan | 15/15 | 15/15 | 15/15 |
| Tengah | 9/15 | 15/15 | 15/15 |
| Tengah ulang, dikonfirmasi bisa dijepit | 0/15 | 15/15 | 15/15 |
| Di luar jangkauan | 7/15 | 15/15 | 0/15 |

Seluruh hasil decode adalah `C`. Gate geometri di tabel hanya memeriksa
area X–Y dan ambang luas; bukan seluruh FSM, syarat lateral, streak,
kedalaman, ARM, atau bukti penutupan gripper. Sampel ini dipakai juga untuk
menyusun kandidat, sehingga angka bukan akurasi pada data uji independen.

Envelope posisi terkonfirmasi (bukan toleransi seluruh kombinasi X/Y):
`[0.4205078125, 0.49375, 0.510546875, 0.5256944444444445]`.
Rentang Y lama hanya mencakup sampel awal; posisi tengah terkonfirmasi
memperluas envelope. Tidak perlu memaksa payload mengikuti rentang Y awal.
Kandidat ambang luas decoded: `0.006488444010416667` (0.6488% frame).
Ambang region tetap null karena luas bbox terjangkau/tidak terjangkau
tumpang tindih. Konfigurasi produksi grab_roi_norm dan ambang luas tetap
null; tidak ada grab otomatis yang diaktifkan dalam pekerjaan ini.

## Verifikasi Pi

Probe satu sampel tengah ulang: fallback awal 790–845 ms setelah pemanasan;
pass cepat mengurangi menjadi 7.8–8.6 ms pada gambar yang sama (pemanasan
151 ms). Ini bukan benchmark seluruh kondisi. Service QR tetap dibatasi
4 FPS. Setelah pemasangan, 40 paket telemetry/4 detik membawa qr_decode C;
ini jumlah paket telemetry, bukan 40 frame kamera independen. Empat hasil
journal terbaru menunjukkan capture age 12.9–48.6 ms. Kedua service aktif,
NRestarts=0; ROV DISARM, MANUAL. Backup sebelum pemasangan di Pi:
`/home/hydroships/rov-agent/backups/qr-fallback-0908/`.

257 tes dan 18 subtest lulus. Tambahan tes mencakup offset ROI, provenance
fallback, QR ganda, serta validator menolak teks kosong dan koordinat NaN.

Data dan hasil lengkap: `logs/grab-calibration/fallback-validation.json`.
Jalankan ulang dari root proyek:

```powershell
.venv/Scripts/python.exe autonomy/tools/validate_grab_calibration.py
```
# Pembaruan jalur live Autonomous — 8 September 2026

Jalur live QR memakai ZXing pada ROI, proposal YOLO, maksimal enam pass ZXing
pada crop kandidat (grayscale, hijau, LAB L; skala 1/2), lalu contrast stretch
ROI. Cascade exhaustive tetap tersedia untuk offline, tetapi tidak dipanggil
worker live. Geometri dengan umur capture >1 detik dibuang. Ini membatasi
beban decode, bukan hard real-time deadline untuk inferensi native OpenCV.

Replay jalur live: 104/105 frame terbaca; posisi kanan 14/15, posisi lain 15/15.
Satu frame kanan (13.jpg) gagal dan diperlakukan sebagai no_detection; bukan
target grab valid. Semua 15 sampel luar area tetap ditolak oleh gate geometri.
Uji replay CASE 4/5 dengan kegagalan tersebut tetap lulus. Sampel merupakan data
kalibrasi yang sama, bukan validasi akurasi independen atau bukti grab fisik.

Benchmark Pi dengan worker lama masih berjalan: delapan frame live tanpa QR
409,7–444,5 ms; blank 349,7–540,0 ms termasuk warm-up; sampel kanan terbaca 8/8,
273,0–752,5 ms termasuk warm-up. Setelah deployment, jurnal no_detection
berjarak sekitar 0,75–0,95 detik (publikasi status juga di-throttle 0,5 detik).

Telemetry `autonomous_input` berisi axis FSM yang diterima Pi dan umurnya.
`control_output` berisi hasil penulisan MANUAL_CONTROL ke transport Pixhawk,
sumber, status stale, serta umur kiriman; bukan konfirmasi gerakan fisik.
CONTROL menampilkan keduanya. MANUAL_CONTROL z=500 berarti heave netral.

Deployment dilakukan saat DISARM/manual, tanpa ARM atau gerakan uji. Backup
Pi: `/home/hydroships/rov-agent/backups/auto-live-20260908-045434`.
Kedalaman kolam 0,9 m dipulihkan sesudah restart agent. Verifikasi: 273 tes
Python + 18 subtes lulus, tiga berkas tes Node lulus, kedua service aktif.
