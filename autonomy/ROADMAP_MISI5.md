# Roadmap Misi 5 — Docking Payload Autonomous (KKI 2026)

Peta jalan dari kondisi sekarang (visi & FSM sudah lolos uji closed-loop di
simulator + tes meja pakai webcam) sampai siap tanding di kolam. Diurut dari
risiko tertinggi ke terendah — tiap fase punya **Definition of Done (DoD)**
supaya jelas kapan boleh lanjut ke fase berikutnya.

Status dokumen ini: rencana kerja, bukan laporan hasil. Centang tiap item saat
selesai dan catat tanggal + temuan di kolom Catatan.

---

## Fase 0 — Verifikasi visi di meja (sedang berjalan)

Tujuan: pastikan deteksi QR + arah servo BENAR sebelum disambungkan ke thruster
sungguhan — kalau tanda kebalik di sini, ROV nanti menjauh dari target.

- [ ] `tools/servo_webcam_test.py --device 0` (IBVS, tanpa kalibrasi): QR
      terdeteksi stabil, kotak hijau menempel di QR.
- [ ] Gerakkan QR ke KANAN → `sway` bereaksi ke arah yang benar (lihat
      `VERIFIKASI_ARDUSUB.md` utk konvensi tanda). Balik `invert_sway` di
      `control/visual_servo.py` bila terbalik.
- [ ] Sama untuk QR MENJAUH → `surge`, dan QR ke BAWAH → `vert`.
- [ ] Di tengah & jarak engage → status **ALIGNED** tercapai & stabil (tidak
      "kedip" aligned/tidak).
- [ ] (Opsional, utk PBVS) Cetak papan catur → `tools/make_checkerboard.py` →
      kalibrasi `tools/calibrate_camera.py --auto` → uji ulang dgn
      `tools/pose_webcam_test.py` (x/y/z dalam meter masuk akal).
- [ ] `pytest tests/ -v` tetap hijau (34 test) — regresi logika servo/FSM.

**DoD Fase 0:** QR payload (format JSON `{"mission":5,...,"id":"A"}`)
terdeteksi konsisten, arah semua sumbu terverifikasi benar, ALIGNED tercapai.

**Catatan:**

---

## Fase 1 — SITL (fisika ArduSub, belum ada air)

Tujuan: uji FSM lewat jalur **MAVLink + `rov_link.py` + GUI** yang sesungguhnya
dipakai saat lomba — bukan lagi `sim_plant.py` in-process, tapi proses terpisah
dgn latency & timing nyata.

- [x] Cara cepat (satu perintah): `python3 autonomy/tools/launch_sitl.py --fsm --vision mock
      --no-wait-autonomous --no-gui` — start vehicle mock + `rov_link.py` + FSM sekaligus.
      (`--no-gui` bila sudah ada `node server.js` yang memegang :8080/:14551.)
      Atau manual ikuti `SITL_SETUP.md`.
- [x] Jalankan `rov_link.py` menjembatani MAVLink ↔ UDP JSON (`:14550`/`:14551`).
- [x] Buka GUI mode LIVE, `RPI_ADDR=127.0.0.1` — telemetri masuk.
      **`RPI_ADDR` WAJIB di-set**; default `192.168.2.2` mengirim command ke ROV asli
      sementara telemetry tetap terlihat normal (server BIND :14551, menerima dari
      siapa pun) — perintah hilang tanpa jejak. Gerak 3D visual = checklist browser.
- [x] `fsm/mission5.py --vision mock` — rantai misi 1→5 tuntas sampai `DONE`.
- [x] Uji handoff GUI Manual↔Autonomous — otomatis via `tools/verify_handoff.mjs`.
- [x] Tombol **STOP** menetralkan thruster walau FSM jalan — skenario C skrip yang sama.

**DoD Fase 1:** rantai misi 5 tuntas via SITL dgn skor rubrik penuh di log FSM,
GUI menampilkan gerak 3D sinkron, handoff manual/autonomous & STOP terverifikasi.

**Catatan:**
- **2026-08-21 — VERIFIED.** Rantai FSM: `launch_sitl.py --fsm --vision mock
  --no-wait-autonomous --no-gui` 3× berturut → **3/3 `DONE`, 100/100**, 13 transisi
  state identik, nol WARNING/ERROR. Blocker `vert`/`heave` (2026-08-12) sudah beres.
  Handoff + STOP: `node autonomy/tools/verify_handoff.mjs` → **3/3 skenario LULUS**.
  Unit test: 124 passed, 2 skipped (`cd autonomy && PYTHONPATH= python3 -m pytest tests/ -q`
  — `PYTHONPATH=` wajib, PYTHONPATH ROS Humble membuat pytest gagal collect).

  **Dua bug ditemukan & diperbaiki saat verifikasi ini**, keduanya sekelas dengan
  `vert`/`heave` — field yang dibaca tak pernah berisi nilai yang diharapkan, dan
  tak ada test yang menangkapnya:
  1. `mission5.py` membaca `telem['mode']` untuk gate autonomous, padahal `mode`
     berisi mode ArduSub (`MANUAL`/`ALT_HOLD`) dan gate GUI ada di `control_mode`.
     `_wait_for_autonomous()` menunggu selamanya; abort-saat-kembali-ke-Manual tak
     pernah menyala. Tak terlihat karena kedua jalur yang dipakai memakai
     `wait_mode=False` yang melewati cek itu.
  2. Handler `stop` di `rov_link.py` tidak memanggil `stop_mission5()`. STOP
     men-disarm tapi thread FSM terus jalan — dibuktikan mencapai `M5_ASCEND`
     setelah STOP ditekan. Sekali operator menekan ARM, gerakan lanjut dari state
     terakhir tanpa peringatan.

  Sisa yang belum tercentang: **checklist browser** di `TEST_CHECKLIST.md`
  (gerak 3D, tombol fisik, F12 console) — butuh mata, tak bisa headless.

- **2026-08-21 (lanjutan) — skenario A/B/C + stabilitas DICENTANG.** Ketiga
  skenario SITL di `TEST_CHECKLIST.md` dijalankan & lulus: **A** 100/100 dalam
  54 dtk, **B** 40/40 dalam 15 dtk, **C** konvergensi servo x 0.15→0.00,
  y 0.10→0.00, z 0.72→0.30 (= `SERVO_TARGET_DIST`), `ALIGNED` tepat 1×. Nol
  WARNING/ERROR di ketiganya. Resource saat run: maks 77 MB RSS & 10,5% CPU per
  proses, shutdown tanpa proses tersisa. `verify_handoff.mjs` dijalankan ulang
  → 3/3 LULUS. Unit test 135 passed, 2 skipped.

  Dua celah ditutup di sesi ini:
  1. **`README_SETUP_C.md` yang hilang** (dirujuk README, ROADMAP, PERSIAPAN,
     dan docstring `launch_sitl.py`) kini ada — jalur manual per-terminal untuk
     mendiagnosa saat launcher gagal, plus empat jebakan lingkungan yang selama
     ini cuma tercatat di catatan lepas (`RPI_ADDR`, `npm run sim`, `PYTHONPATH=`,
     port sisa). Langkah-langkahnya dijalankan apa adanya untuk memastikan benar.
  2. **Kill-switch joystick tak punya test sama sekali** — kini `tests/test_rov_link.py`
     (6 test). Sekalian ketahuan komentar `KILL_SWITCH_DEADZONE` menyebut skala
     `-100..100`, padahal axis GUI berskala `-1000..1000`; angkanya sendiri aman
     (menyala di ~20% defleksi stik berkat deadzone GUI 0.12), tapi komentarnya
     mengundang "perbaikan" 15→150 yang justru membuat takeover lamban. Komentar
     diperbaiki, ambangnya TIDAK diubah.

## Fase 2 — Bring-up hardware kering (ROV di darat / ember, TANPA kolam penuh)

Tujuan: pastikan tiap axis & aktuator fisik benar SEBELUM ROV masuk air.

- [x] Pixhawk/ArduSub asli menyala, `rov_agent.py` tersambung, heartbeat masuk
      di GUI. **LULUS 2026-08-25** (di kolam, bukan darat/ember — lihat Catatan).
- [x] Verifikasi arah surge/sway/vert/yaw — masing-masing gerak sesuai command,
      bukan terbalik. **LULUS 2026-08-25.**
- [x] Channel servo **gripper** — `gripper=1` (tutup) & `gripper=0` (buka)
      terverifikasi fisik, tanpa macet/nyangkut. **LULUS 2026-08-25.**
- [x] Sensor depth (pressure) terbaca akurat vs referensi. **LULUS 2026-08-25.**
- [ ] Feed kamera BOTTOM & WALL tampil bersamaan di GUI (`§4.7.3`) — belum
      dikonfirmasi sesi ini.
- [x] Ulangi checklist `VERIFIKASI_ARDUSUB.md` poin per poin — **#1,2,3,5,6,7
      LULUS; #4 (lampu) DILEWATI, belum diimplementasikan.**

**DoD Fase 2:** semua thruster arah benar, gripper open/close andal, depth
akurat, dual-cam tampil, tombol STOP menetralkan SEMUA aktuator instan.

**Catatan:**
- **2026-08-25 — SOFTWARE siap, FISIK belum mulai.** Kelima item di atas dicek
  ulang terhadap kode yang BENAR-BENAR jalan di Pi (`rov_agent.py` via
  `rov-agent.service`, bukan `autonomy/rov_link.py` yang dipakai jalur SITL
  terpisah — dua program berbeda, lihat catatan Fase 1). Tak ada gap kode:
  1. **Koneksi Pixhawk** — `connect_pixhawk()` di `rov_agent.py` sudah
     menyambung ke `PIXHAWK_PORT`/`PIXHAWK_BAUD` (env, default
     `/dev/ttyACM0`/115200) langsung ke serial nyata, bukan SITL. Dikonfirmasi
     hidup di hardware: `[MAV] Heartbeat received!` di journal Pi (lihat
     restart 2026-08-25 02:53).
  2. **Arah tiap thruster** — panel "Motor Test" di halaman Setup GUI
     (`public/js/pages/setup.js`, meniru Motor Test QGroundControl) sudah ada,
     siap dipakai uji satu-satu.
  3. **Gripper PWM** — `gripper_controller.py` (`GRIPPER_PWM_OPEN=1580`/
     `CLOSE=1350`) dipakai langsung oleh `rov_agent.py` lewat `GripperController`,
     satu sumber kebenaran, tak ada mismatch.
  4. **Sensor depth** — `rov_agent.py` membaca `AHRS2.altitude` (bukan
     `SCALED_PRESSURE2` seperti disebut `VERIFIKASI_ARDUSUB.md` item 6, yang
     menggambarkan jalur `autonomy/rov_link.py`). Pendekatan berbeda,
     sama-sama sudah lengkap & sedang berjalan di Pi — item 6 dokumen itu
     perlu dibaca sebagai deskripsi jalur SITL, bukan Pi produksi.
  5. **Dual-cam** — `public/js/pages/camera.js` sudah render CAM1=BOTTOM +
     CAM2=WALL sekaligus dengan PiP.

  Yang menahan Fase 2 murni fisik: Pixhawk & ROV harus di darat/ember untuk
  menjalankan checklist `VERIFIKASI_ARDUSUB.md` #1-7 manual (arah axis, servo
  gripper/lampu, mode ALT_HOLD, sumber depth, arming/failsafe). Tak ada yang
  bisa diverifikasi lebih lanjut dari kode.

- **2026-08-25 (lanjutan) — checklist fisik #1,2,3,5,6,7 LULUS, langsung di
  kolam** (bukan darat/ember dulu seperti rencana semula — operator langsung
  uji di venue sesungguhnya). Dipantau live lewat journal `rov-agent.service`
  selama sesi: `[MAV] ARM`/`DISARM` berulang kali diterima Pixhawk dengan
  `result=0` konsisten, tak ada crash/hang/drop_link. Arah surge/sway/yaw,
  arah vertikal, gripper, mode ALT_HOLD, dan akurasi depth dikonfirmasi
  operator langsung dari pengamatan fisik ROV. #4 (servo lampu) DILEWATI —
  belum terhubung hardware, lihat `VERIFIKASI_ARDUSUB.md`. Item dual-cam
  belum sempat dikonfirmasi eksplisit sesi ini.

  **DoD Fase 2 hampir tercapai** — tinggal konfirmasi dual-cam tampil
  bersamaan dan uji STOP menetralkan SEMUA aktuator (arm/disarm sudah
  terverifikasi, tapi STOP spesifik saat thruster aktif belum eksplisit diuji).

---

## Fase 3 — Uji kolam & tuning (loop tertutup di air)

Tujuan: kalibrasi ulang & tuning parameter dgn kondisi air sungguhan —
refraksi mengubah focal length efektif, jadi kalibrasi udara cuma pendekatan.

- [ ] **Kalibrasi kamera DI DALAM AIR**, di balik housing/dome yang SAMA dgn
      saat misi (`tools/calibrate_camera.py`, papan tahan air atau di balik
      kaca akuarium).
- [ ] Verifikasi ULANG semua `invert_*` di air (arah bisa beda dari uji meja
      karena mounting kamera/orientasi berbeda).
- [ ] Tune `SERVO_TARGET_DIST` / `SERVO_TARGET_AREA` (jarak & area engage QR)
      sampai gripper benar-benar mencapai payload saat ALIGNED.
- [ ] Tune `HOOK_DEPTH`, `DEPTH_TARGET_BOTTOM` sesuai kedalaman kolam
      sesungguhnya venue.
- [ ] Tune timing fase manual (GRAB, HANG, DOCK, M5_ENGAGE, M5_UNHOOK) — durasi
      di kode adalah TEBAKAN awal, ganti dgn hasil ukur di kolam.
- [ ] Kalibrasi `WALL_HEADING` (A/B/C/D) sesuai orientasi kolam venue
      sebenarnya (kompas ROV vs tata letak kolam).
- [ ] Uji **loss-of-lock** sungguhan: gerakkan/halangi QR sesaat saat docking,
      pastikan dead-reckon hold + sapu terarah (lihat commit hardening
      `M5_DOCK`/`M5_ENGAGE`) benar-benar pulih di air, bukan cuma di sim.
- [ ] Ulangi docking closed-loop berturut-turut ≥5× dari kondisi start berbeda.
- [ ] Validasi **tanda & stabilitas yaw squaring** (`SERVO_KP_YAW`, default 0 —
      NONAKTIF) — pakai `python -m autonomy.tests.pool_yaw_validation` (item
      M7, `VERIFIKASI_ARDUSUB.md`). Skrip PASIF, tak kirim command; operator
      putar ROV manual, script cuma log `yaw_deg`. JANGAN naikkan
      `SERVO_KP_YAW` dari 0 sebelum ini lolos.
- [ ] Kalibrasi **pencarian lateral M5_SEARCH** (`SEARCH_SPEED` → m/s, deviasi
      kompas, lebar kolam vs jarak back-off) — item M9, `VERIFIKASI_ARDUSUB.md`.
- [ ] Tune **peredam approach servo** (`servo_smooth`: deadband/slew/`kd`/
      approach-floor di `control/visual_servo.py`) — nilai saat ini tebakan
      awal, bukan hasil ukur di air.

**DoD Fase 3:** docking QR closed-loop berhasil ambil & lepas payload dari hook
berulang (≥5 dari percobaan), radius align konsisten kecil, tak ada tabrakan.

**Catatan:**
- **2026-08-25 — BELUM MULAI secara fisik, tooling sisi software sudah siap.**
  Semua item Fase 3 butuh air sungguhan (refraksi mengubah focal length efektif
  — kalibrasi udara Fase 0/2 cuma pendekatan) sehingga tak satu pun bisa
  dicentang dari kode. Yang SUDAH disiapkan supaya uji kolam tinggal jalan,
  tanpa menulis skrip baru di tempat:
  - **Yaw squaring** — `tests/pool_yaw_validation.py` siap pakai (dibuat &
    diuji-self-check sesi ini). Murni pasif: decode QR + log `yaw_deg` ke CSV,
    tak pernah menyentuh thruster — aman dijalankan kapan pun kamera QR aktif.
  - **M5_SEARCH & peredam servo** — semua konstanta tuning (M9, `servo_smooth`)
    sudah dipetakan ke `config/loader.py`, jadi hasil ukur kolam tinggal ditulis
    ke `config/*.yaml` tanpa edit Python.
  - **Kalibrasi kamera dalam air** — `tools/calibrate_camera.py` sudah ada dari
    sesi sebelumnya (dipakai bikin `dwe_v3.npz`, RMS 0.87px, 24 Agu).

  Tak ada gap alat. Titik mulai Fase 3 murni menunggu akses kolam.

---

## Fase 4 — Latihan penuh & strategi lomba

Tujuan: simulasikan kondisi hari-H seakurat mungkin, termasuk jalur degradasi.

- [ ] Konfirmasi strategi: **misi 1–4 manual via GUI, misi 5 autonomous**
      (`fsm/mission5.py --start-state M5_REDIVE`) — operator toggle header
      Manual→Autonomous setelah docking misi 4 selesai.
- [ ] Full run end-to-end dgn **auto screenshot & logging** GUI aktif (§4.7.3).
- [x] **Logika** jalur **M5_FALLBACK** terverifikasi (2026-08-21, simulator):
      QR hilang → `M5_DOCK`/`M5_REDIVE` timeout → `M5_FALLBACK` → `DONE`, misi 5
      tetap dapat nilai, BUKAN ABORT; payload tetap lepas dari hook lewat urutan
      timed buta. Dikunci 2 pytest (`test_m5_fallback_*`), sudah diuji-mutasi.
- [ ] **Drill fisik** jalur M5_FALLBACK di kolam: tutup lensa kamera saat docking
      sungguhan — yang terbukti di atas logikanya, bukan perilaku ROV di air.
- [x] Checklist hari-H **sudah tertulis** — `PERSIAPAN_FASE2-4.md` §4.1 (urutan
      boot), §4.2 (pra-dive), §4.6 (teardown). Sisa: dieksekusi & dihafal tim.
- [ ] Rehearsal 3× run berturut-turut TANPA intervensi manual di luar toggle
      mode — catat skor & waktu tiap run.
- [x] Rencana kabel/umbilical **sudah tertulis** — `PERSIAPAN_FASE2-4.md` §4.2
      (umbilical wajib, satu subnet). Sisa: uji sambungan fisik di venue.

**DoD Fase 4:** 3× run sukses berturut-turut dgn skor rubrik stabil, tim hafal
checklist hari-H & prosedur pemulihan bila fallback terpicu.

---

## Dukungan yang bisa disiapkan lebih dulu (tanpa hardware/kolam)

Dikerjakan paralel, tidak menunggu fase hardware:

- [x] **File konfigurasi tunable** (`config/mission5.example.yaml` + `config/loader.py`,
      flag `--config` di `fsm/mission5.py`) — pindahkan konstanta tuning (gain PID,
      target dist/area, depth, timing, `invert_*`, `WALL_HEADING`, mekanik unhook,
      validasi payload) keluar dari `mission5.py`. Salin ke `config/mission5.local.yaml`
      (gitignored) lalu tuning di kolam TANPA edit kode Python. Format `.yaml`/`.json`.
      Diuji: 8 test baru (flatten/merge/apply + bukti FSM benar berubah perilaku saat
      config diterapkan) — lihat `README.md` §"Tuning tanpa edit kode".
- [x] **Skrip peluncur SITL satu-perintah** (`tools/launch_sitl.py`) — otomatis start
      vehicle (`sitl_mock.py` default, atau `--vehicle sitl` utk ArduSub SITL WSL2
      yang sudah jalan terpisah) → `rov_link.py` → GUI (`npm start`) → opsional `--fsm`.
      Label warna per proses di satu terminal; Ctrl+C / crash satu proses mematikan
      semua dgn rapi. Diuji: 13 test (`tests/test_launch_sitl.py`, murni argparse+plan
      tanpa spawn nyata) + smoke test nyata (mock terhubung ke rov_link, heartbeat OK).
- [x] **Mode logging visi** di `servo_webcam_test.py` / `pose_webcam_test.py`
      (`--csv` + `tools/detection_log.py`) — rekam detection-rate + pose/servo per
      frame ke CSV + ringkasan saat keluar. Berguna diagnosa isu QR & tuning Fase 3.
      Sekalian: kedua tool kini pakai `decode_qr()` (preprocessing CLAHE+upscale) &
      opsi `--cam-width/height`. (Robustness deteksi QR — perbaikan isu Fase 0.)
- [x] **Checklist verifikasi tanda/arah** — dibuat sbg run-book operasional
      `PERSIAPAN_FASE2-4.md` (bisa dicentang, merujuk `VERIFIKASI_ARDUSUB.md`),
      plus backlog `PR-AUTONOMY.md` (analisis gap Fase 0–4 + isu tertunda).

---

## Referensi silang

| Fase | Dokumen terkait |
|---|---|
| 0 | `VERIFIKASI_ARDUSUB.md`, `tests/test_mission5.py`, `tests/test_qr_detect.py` |
| 1 | `SITL_SETUP.md`, `README_SETUP_C.md`, `tools/launch_sitl.py` |
| 2, 3, 4 | `PERSIAPAN_FASE2-4.md` (run-book operasional), `VERIFIKASI_ARDUSUB.md` |
| Backlog | `PR-AUTONOMY.md` (gap analysis Fase 0–4 + isu tertunda spt QR-01) |
| Semua | `../README.md` §"Autonomy (Python, opsional)", `tests/evaluate_mission5.py` |
