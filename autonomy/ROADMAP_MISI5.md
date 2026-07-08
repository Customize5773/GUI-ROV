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

- [ ] Cara cepat (satu perintah): `python tools/launch_sitl.py --fsm --vision mock
      --no-wait-autonomous` — start vehicle mock + `rov_link.py` + GUI + FSM sekaligus.
      Atau manual ikuti `SITL_SETUP.md`: ArduSub SITL (WSL2) ATAU pakai `sitl_mock.py`
      (lebih ringan, tanpa WSL) sbg pengganti sementara.
- [ ] Jalankan `rov_link.py` menjembatani MAVLink ↔ UDP JSON (`:14550`/`:14551`).
- [ ] Buka GUI (`npm start` mode LIVE, `RPI_ADDR=127.0.0.1`) — pastikan
      telemetri (depth/heading/attitude) masuk & ROV 3D bergerak.
- [ ] Jalankan `fsm/mission5.py --vision usb --start-state DIVE
      --no-wait-autonomous` (atau `--vision mock` dulu bila kamera SITL belum
      siap) — rantai misi 1→5 tuntas sampai `DONE`.
- [ ] Uji handoff GUI: toggle Manual→Autonomous di tengah proses, FSM merespons
      `mode=='autonomous'`; toggle balik ke Manual → FSM `abort()` bersih.
- [ ] Tombol **STOP** di GUI menetralkan thruster walau FSM sedang jalan.

**DoD Fase 1:** rantai misi 5 tuntas via SITL dgn skor rubrik penuh di log FSM,
GUI menampilkan gerak 3D sinkron, handoff manual/autonomous & STOP terverifikasi.

**Catatan:**

---

## Fase 2 — Bring-up hardware kering (ROV di darat / ember, TANPA kolam penuh)

Tujuan: pastikan tiap axis & aktuator fisik benar SEBELUM ROV masuk air.

- [ ] Pixhawk/ArduSub asli menyala, `rov_link.py` tersambung (ganti target
      SITL → hardware nyata), heartbeat masuk di GUI.
- [ ] Verifikasi arah **tiap dari 8 (atau sesuai desain) thruster** — surge/
      sway/vert/yaw masing-masing gerak sesuai command, bukan terbalik.
- [ ] Channel servo **gripper** — `gripper=1` (tutup) & `gripper=0` (buka)
      terverifikasi fisik, tanpa macet/nyangkut.
- [ ] Sensor depth (pressure) terbaca akurat vs referensi (mis. penggaris di
      ember/kolam dangkal).
- [ ] Feed kamera BOTTOM & WALL tampil bersamaan di GUI (`§4.7.3`).
- [ ] Ulangi checklist `VERIFIKASI_ARDUSUB.md` poin per poin.

**DoD Fase 2:** semua thruster arah benar, gripper open/close andal, depth
akurat, dual-cam tampil, tombol STOP menetralkan SEMUA aktuator instan.

**Catatan:**

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

**DoD Fase 3:** docking QR closed-loop berhasil ambil & lepas payload dari hook
berulang (≥5 dari percobaan), radius align konsisten kecil, tak ada tabrakan.

**Catatan:**

---

## Fase 4 — Latihan penuh & strategi lomba

Tujuan: simulasikan kondisi hari-H seakurat mungkin, termasuk jalur degradasi.

- [ ] Konfirmasi strategi: **misi 1–4 manual via GUI, misi 5 autonomous**
      (`fsm/mission5.py --start-state M5_REDIVE`) — operator toggle header
      Manual→Autonomous setelah docking misi 4 selesai.
- [ ] Full run end-to-end dgn **auto screenshot & logging** GUI aktif (§4.7.3).
- [ ] Uji sengaja jalur **M5_FALLBACK** (mis. tutup lensa kamera saat docking)
      — pastikan degradasi timed tetap dapat kredit parsial, bukan ABORT total.
- [ ] Siapkan checklist hari-H: baterai, kabel umbilical, cek arm/disarm,
      urutan boot (Pixhawk → rov_link → server → GUI → FSM).
- [ ] Rehearsal 3× run berturut-turut TANPA intervensi manual di luar toggle
      mode — catat skor & waktu tiap run.
- [ ] Siapkan rencana cadangan bila WiFi/venue melarang wireless (aturan KKI:
      umbilical kabel wajib, satu subnet).

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
- [ ] **Mode logging visi** di `servo_webcam_test.py` / `pose_webcam_test.py`
      — rekam laju deteksi + error pose ke CSV, berguna saat tuning Fase 3.
- [ ] Checklist verifikasi tanda/arah yang bisa dicentang (perluasan
      `VERIFIKASI_ARDUSUB.md`) — dipakai ulang tiap kali mounting kamera/ROV
      berubah.

---

## Referensi silang

| Fase | Dokumen terkait |
|---|---|
| 0 | `VERIFIKASI_ARDUSUB.md`, `tests/test_mission5.py` |
| 1 | `SITL_SETUP.md`, `README_SETUP_C.md` |
| 2, 3 | `VERIFIKASI_ARDUSUB.md` |
| Semua | `../README.md` §"Autonomy (Python, opsional)", `tests/evaluate_mission5.py` |
