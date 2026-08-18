Berikut adalah isi dokumen **Panduan Kontes Kapal Indonesia (KKI) 2026** bagian **Sub-Kategori Remotely Operated Underwater Vehicle (ROV)** yang disajikan dalam format Markdown:

---

# PANDUAN KONTES KAPAL INDONESIA (KKI) 2026

**Direktorat Pembelajaran dan Kemahasiswaan**

**Direktorat Jenderal Pendidikan Tinggi**

**Kementerian Pendidikan Tinggi, Sains, dan Teknologi Republik Indonesia**

---

## 4.7 Sub-Kategori Remotely Operated Underwater Vehicle (ROV)

> **Sub-Kategori Baru KKI 2026**
> Sub-kategori *Remotely Operated Underwater Vehicle* (ROV) merupakan sub-kategori **BARU** yang diperkenalkan pertama kali pada KKI 2026. ROV hadir sebagai representasi dimensi bawah air dari operasi kapal *coast guard* modern, mencakup inspeksi lambung, pencarian korban, surveilans bawah air, dan pemantauan infrastruktur bawah laut. Petunjuk Teknis ROV yang lebih rinci akan diterbitkan terpisah oleh panitia KKI 2026.
> 
> 

### 4.7.1 Deskripsi dan Misi

Sub-kategori ROV menantang tim mahasiswa untuk merancang, membangun, dan mengoperasikan prototipe robot bawah air (*Remotely Operated Underwater Vehicle* / ROV) yang mampu melaksanakan misi bawah air yang mencerminkan operasi nyata *coast guard*, *floating repair*, ataupun aplikasi lainnya. ROV dioperasikan dari permukaan menggunakan tali *umbilical* dan sistem kendali.

**Misi utama ROV dalam konteks operasi kapal coast guard:**

* **Navigasi bawah air:** Menavigasi lintasan bawah air yang ditandai dengan rintangan warna melewati *gate* bawah air secara akurat.


* **Inspeksi lambung:** Mendekati dan merekam/mengidentifikasi objek di dasar kolam sebagai simulasi inspeksi lambung kapal atau infrastruktur bawah laut.


* **Pengambilan objek:** Mengambil objek spesifik dari dasar kolam sebagai simulasi operasi SAR (pencarian dan penyelamatan) bawah air.


* **Navigasi presisi:** Bermanuver melalui rintangan dengan akurasi tinggi dalam kondisi visibilitas terbatas.



#### Layout & Konsep GUI ROV

* **Dimensi Kolam:** $5\text{ m} \times 5\text{ m}$, Kedalaman air $0{,}7 - 0{,}9\text{ m}$.


* **Gantungan / Hook:** Dipasang di dinding kolam (sisi A, B, C, D yang diacak) pada ketinggian $0{,}45\text{ m}$ dari dasar kolam.


* **Payload:** Ukuran $5\text{ cm} \times 3\text{ cm} \times 10\text{ cm}$ dilengkapi QR Code ($4\text{ cm} \times 4\text{ cm}$) dan *hook* penggantung.


* **Operator:** 2 ROV Operators (*Man #1 and #2*) dan 1 GCS Operator (*Man #3*).


* **Kebutuhan GUI (Graphical User Interface) Minimal:**
1. Display 2 kamera (Front Camera & Side/Wall Camera).


2. Hasil pengolahan pembacaan QR Code (membaca lokasi gantungan A, B, C, atau D).


3. Menampilkan pengukuran posisi ketinggian ROV (titik tengah) terhadap dasar kolam.


4. Informasi tim: Nama tim, perguruan tinggi, hari, tanggal, dan waktu.


5. Gambar desain ROV (2D/3D).


6. *Trajectory map* yang ditempuh dari titik awal hingga akhir.

* **Nilai Tambah (Advanced Feature - Opsional):** *Screenshot & data logging* otomatis, *replay camera & trajectory*, *alarm audio* (kedalaman berbahaya), serta *toggle mode manual/autonomous*.

---

### 4.7.2 Ketentuan Teknis Prototipe ROV

| Parameter | Ketentuan |
| --- | --- |
| **Dimensi Prototipe** | Panjang maks. 35 cm, lebar maks. 35 cm, tinggi maks. 35 cm. Desain kompak untuk manuver di perairan rintangan.

 |
| **Sistem Propulsi** | Motor listrik (*thruster*) *waterproof*; minimal 3 dan/atau maksimal 6 *thruster* untuk gerakan horizontal dan vertikal (*surge, sway, heave*). Konfigurasi *thruster* bebas.

 |
| **Sistem Kendali** | Dioperasikan melalui tali *umbilical* dari permukaan. Tidak diperkenankan menggunakan sistem *wireless* (tanpa kabel) selama misi berlangsung.

 |
| **Catu Daya** | Baterai *onboard* atau suplai daya melalui *umbilical*; kapasitas tidak dibatasi, namun harus dicantumkan dalam proposal.

 |
| **Sistem Kamera** | Minimal 2 kamera bawah air (*waterproof*) dengan output tampilan *real-time* ke monitor operator di permukaan.

 |
| **Material Lambung** | Bebas, namun harus tahan tekanan air dan kedap air (*waterproof*) minimal hingga kedalaman 3 meter.

 |
| **Umbilical/Kabel** | Panjang *umbilical* minimal 15 meter. Kabel harus terlindungi dan tidak mengganggu manuver ROV selama misi.

 |
| **Emergency Stop** | **Wajib:** tombol darurat di panel kontrol permukaan yang dapat menghentikan seluruh *thruster* secara instan.

 |
| **Berat** | Tidak dibatasi; wajib dicantumkan saat inspeksi teknis.

 |

---

### 4.7.3 Sistem Kontes dan Lintasan ROV

Kontes performa ROV diselenggarakan di **Waduk PDAM Kota Bengkalis** dengan arena bawah air yang telah dipersiapkan panitia.

**Setiap tim peserta mempersiapkan:**

1. ROV lengkap dengan *Gripper* dan *Camera*.


2. GUI untuk mengoperasikan ROV lengkap dengan Deteksi QR Code serta display Camera 1 (*bottom*) dan Camera 2 (*wall*).


3. *Gripper* menempel pada ROV.



*Catatan Ukuran:* Ukuran ROV maks. $35 \times 35 \times 35\text{ cm}$ (tidak termasuk *gripper*). *Gripper* boleh masuk dalam dimensi ROV atau berada di luar dimensi ROV sebagai tambahan.

**Tahapan Misi Kontes ROV:**

1. ROV menyelam secara *remotely* ke dasar untuk melakukan *scan* QR code yang diberikan (15%).


2. Setelah *scan*, ROV secara *remotely* mengambil *payload* dengan *gripper*-nya (15%).


3. Selanjutnya ROV menuju sisi dinding kolam yang sesuai QR Code *Payload* untuk memindahkan *payload* dari dasar ke gantungan dinding (15%).


4. Setelah berhasil memindahkan *Payload*, ROV mengapung ke permukaan dan bersandar di sisi dinding *payload* (15%).


5. Memprogram ROV selanjutnya untuk melakukan misi pelepasan *payload* secara *autonomous*, untuk kemudian dibawa ke permukaan. Jika dilakukan secara *remotely*, nilai hanya 25% dari *autonomous* (40% *autonomous* / 10% *remotely*).



**Durasi Misi Kontes:** Maksimal 20 menit per *run* per tim (5 menit persiapan, 10 menit pelaksanaan misi, 5 menit evakuasi/meninggalkan lokasi).

---

### 4.7.4 Penilaian dan Penentuan Pemenang ROV

| Item | Jenis Misi | % Nilai Misi | Keterangan Misi | Rasio Bobot | Pendetailan Misi |
| --- | --- | --- | --- | --- | --- |
| **Misi** | 1. Object Identification and Navigation | 15% | ROV menyelam *remotely* ke dasar untuk melakukan *scan* QR code yang diberikan | 5 | *Diving performance*<br> |
|  |  |  |  | 5 | *Steady positioning attached to QR Code*<br> |
|  |  |  |  | 5 | *Scanning QR code*<br> |
|  | 2. Grapping object (payload) | 15% | Setelah *scan*, ROV secara *remotely* mengambil *payload* dengan *gripper*-nya | 5 | Bernilai 5 jika upaya memegang *payload* > 2 *trial*<br> |
|  |  |  |  | 10 | Bernilai 10 jika upaya memegang *payload* = 2 *trial*<br> |
|  |  |  |  | 15 | Bernilai 15 jika upaya memegang *payload* = 1 *trial*<br> |
|  | 3. Payload placement completion | 15% | ROV menuju sisi dinding kolam yang sesuai QR Code *Payload* untuk memindahkan *payload* dari dasar ke gantungan dinding | 5 | Bernilai 5 jika upaya menggantungkan *payload* > 2 *trial*<br> |
|  |  |  |  | 10 | Bernilai 10 jika upaya menggantungkan *payload* = 2 *trial*<br> |
|  |  |  |  | 15 | Bernilai 15 jika upaya menggantungkan *payload* = 1 *trial*<br> |
|  | 4. Surface docking | 15% | ROV mengapung ke permukaan dan bersandar di sisi dinding *payload* | 0 | Tidak dapat mengapung di permukaan, *docking* di manapun

 |
|  |  |  |  | 5 | Mampu mengapung tetapi *docking* di sisi yang tidak seharusnya

 |
|  |  |  |  | 15 | Mampu mengapung dan *docking* di sisi yang seharusnya

 |
|  | 5. Autonomous / Remotely payload release | 40% (Auto) / 10% (Remote) | Memprogram ROV selanjutnya untuk melakukan misi pelepasan *payload*, lalu dibawa ke permukaan | 40 | Jika dilakukan secara *full-autonomous*<br> |
|  |  |  |  | 10 | Jika dilakukan secara *remotely* (atau *partly autonomous*)

 |
| **Waktu** | Waktu tempuh melakukan misi | 20 min | Diurutkan dari waktu tercepat hingga terlama | 5 min | *Preparation*<br> |
|  |  |  | **Waktu tercepat DAN nilai misi tertinggi** | 10 min | *Running mission*<br> |
|  |  |  |  | 5 min | *Evacuation*<br> |

---

### 4.7.5 Penilaian Proposal ROV

| No. | Indikator Penilaian | Bobot (%) |
| --- | --- | --- |
| 1 | Halaman Sampul | Wajib

 |
| 2 | Lembar Pengesahan | Wajib

 |
| 3 | **Bab 1 Pendahuluan:** latar belakang, relevansi ROV untuk operasi *coast guard*, tujuan, dan misi bawah air yang disimulasikan | 10

 |
| 4 | **Bab 2 Desain dan Spesifikasi ROV:** |  |
|  | • Desain Teknis: *Operational Requirement* dan Dimensi Utama ROV | 10

 |
|  | • Desain Teknis: Konfigurasi *Thruster* dan Sistem Propulsi Bawah Air | 10

 |
|  | • Desain Teknis: Sistem Kendali, *Umbilical*, dan Panel Operator | 10

 |
|  | • Desain Teknis: Sistem Kamera dan Transmisi Video *Real-Time* | 10

 |
|  | • Desain Teknis: Sistem Manipulator/Lengan (jika ada) untuk Pengambilan Objek | 10

 |
|  | • Desain GUI dan Tahapan Pengerjaan, Metode Fabrikasi, dan Rencana Pengujian | 10

 |
| 5 | **Bab 3 Rancangan Biaya dan Jadwal Pengerjaan:** |  |
|  | • Anggaran Biaya | 5

 |
|  | • Jadwal Pelaksanaan | 5

 |
| 6 | **Bab 4 Penutup** | 5

 |
| 7 | **Daftar Pustaka** | 5

 |
| 8 | **Lampiran:** Biodata Anggota Tim dan Job-desk masing-masing | 5

 |
| **TOTAL** |  | **100**<br> |

*Nilai Akhir. Skor: 1-Buruk, 2-Kurang, 3-Cukup, 4-Baik, 5-Sangat Baik. Nilai = Bobot × Skor.*

---

### 4.7.6 Penilaian Laporan Kemajuan ROV

| No. | Indikator Penilaian | Bobot (%) |
| --- | --- | --- |
| 1 | Perkenalan anggota tim, *job-desk* masing-masing, dan penjelasan GUI. | 10

 |
| 2 | Uraian misi ROV dalam konteks operasi kapal *coast guard* (inspeksi lambung, SAR bawah air, surveilans, dll.) | 10

 |
| 3 | Proses fabrikasi rangka/lambung ROV dan perakitan sistem *waterproof* | 10

 |
| 4 | Pemasangan *thruster*, sistem propulsi, dan konfigurasi *umbilical* | 10

 |
| 5 | Pemasangan sistem kendali, kamera, dan sistem elektronik | 10

 |
| 6 | Pengukuran berat, uji trim, dan uji kekedapan air (*waterproof test*) | 10

 |
| 7 | Uji coba komponen di darat: *thruster*, kemudi, kamera, sistem kendali | 10

 |
| 8 | Uji gerak horizontal di dalam air: maju, mundur, dan manuver lateral | 10

 |
| 9 | Uji gerak vertikal: menyelam dan naik ke permukaan secara terkendali | 10

 |
| 10 | Uji coba ROV di kolam sesuai misi dan tertampil di GUI. | 10

 |
| **TOTAL** |  | **100**<br> |

*Nilai Akhir. Skor: 1-Buruk, 2-Kurang, 3-Cukup, 4-Baik, 5-Sangat Baik. Nilai = Bobot × Skor.*

---

## 4.8 Sistem Penilaian Kategori Prototipe

Penilaian proposal menentukan peserta lolos penilaian Tahap 1. Seleksi tahap 2 dilakukan melalui penilaian video kemajuan yang dikirim oleh peserta dan unjuk kerja *prototype* yang dilakukan secara daring. Jika dinyatakan lolos pada seleksi tahap 2, maka peserta akan maju ke babak final yang akan dilaksanakan di venue final KKI 2026. Nilai Akhir Performa ditentukan murni dari hasil kontes pada hari H di Waduk PDAM Kota Bengkalis sesuai dengan ketentuan setiap sub-kategori di kategori *Prototype*.

Setiap sub-kategori memperebutkan:

* **1 Emas**

* **1 Perak**

* **1 Perunggu**

* **1 Harapan 1**

* **1 Harapan 2**


Sehingga total penghargaan Kategori Prototipe adalah **20 penghargaan** dari 4 sub-kategori.