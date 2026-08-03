# PLAN: Fitur QGroundControl-lite untuk GUI ROV

Status: **Terimplementasi** (Fungsi A + Fungsi B). Dokumentasi pemakaian ada di
[README-WORK.md](../README-WORK.md) §9; dokumen ini disimpan sebagai catatan rancangan
dan keputusan scope.

Mengambil 2 fungsi dari QGroundControl dan mengadaptasinya ke arsitektur GUI ROV
yang sudah ada (Browser ↔ WebSocket ↔ server.js ↔ UDP ↔ rov_agent.py ↔ MAVLink/ArduSub):

1. **Vehicle Configuration** — konfigurasi & setup dasar kendaraan (parameter ArduSub,
   frame, kalibrasi sensor dasar, mapping RC/joystick sudah ada sebagian).
2. **Analyze View** — analisis & diagnostik penerbangan (MAVLink Inspector, log review,
   grafik parameter historis).

Referensi arsitektur saat ini ada di [README-WORK.md](README-WORK.md) §1–3 dan implementasi
command existing (`thruster_config`, `pid`, `pool_depth`) di [rov_agent.py](rov_agent.py)
`apply_thruster_config()` dan [server/server.js](server/server.js).

---

## 0. Prinsip desain

- **Tidak reinvent** apa yang sudah ada: thruster mixer, PID gain, pool depth sudah punya
  jalur command (`thruster_config`, `pid`, `pool_depth`) di halaman **Setup** — fitur baru
  ini melengkapi dengan akses **parameter ArduSub mentah** (seperti QGC Parameters tab) dan
  **grafik/log diagnostik** (seperti QGC Analyze/MAVLink Inspector), bukan menduplikasi UI
  yang sudah ada.
- **Tanpa dependency baru** bila memungkinkan — pola yang sama dipakai fitur Replay
  (frame-store, nol dependency baru) diikuti di sini: parameter cache in-memory + file JSON,
  grafik pakai Canvas/SVG manual atau reuse chart util yang sudah dipakai di halaman Telemetry.
- **Kabel umbilical only** — tidak ada asumsi radio-link/telemetry-radio seperti QGC asli;
  semua lewat WebSocket :8080 yang sudah ada.
- **Fail-safe**: menulis parameter ke FC adalah operasi sensitif (bisa membuat ROV
  un-armable atau mixer salah) → perlu gerbang konfirmasi (pola yang sama dipakai Mode ACRO,
  lihat [CONTROL-MAPPING.md](CONTROL-MAPPING.md)) sebelum `param_set_send`.

---

## 1. Fungsi A — Vehicle Configuration (halaman baru: **Vehicle**)

### 1.1 Cakupan (diambil dari QGC, dipangkas ke yang relevan BlueROV1/ArduSub)

| Sub-tab QGC | Diadaptasi? | Catatan |
|---|---|---|
| Firmware / Frame setup | Sebagian (tampilkan info saja) | Frame BlueROV1 3-2-1 sudah fixed, lihat memori `rov-frame-3-2-1-layout` — tidak perlu re-flash firmware dari GUI ini |
| **Full Parameter List** (search/edit param ArduSub) | **Ya — inti fitur** | Tabel semua param FC: nama, nilai, tipe, deskripsi (dari metadata ArduSub jika tersedia offline) |
| Sensor calibration (accel/compass/level) | Ya, ringkas | Trigger `MAV_CMD_PREFLIGHT_CALIBRATION` via command baru, tampilkan progress dari `COMMAND_ACK`/`STATUSTEXT` |
| Motor/ESC setup | **Tidak** — sudah ada di Setup → Thruster (`thruster_config`) | Cukup tautkan (link) ke halaman itu |
| PID tuning | **Tidak** — sudah ada di Setup → PID | Tautkan |
| Radio/Joystick setup | **Tidak** — sudah ada (Joystick page + `joystick-profile.js`) | Tautkan |
| Safety (failsafe, geofence) | Opsional/nice-to-have | Param ArduSub `FS_*`, `BATT_*` — bisa cukup lewat Full Parameter List, tak perlu UI khusus di v1 |

### 1.2 Alur data param (baru)

```
Browser (Vehicle page)                server.js                  rov_agent.py (MAVLink)
  request "param_list"      ---ws-->  forward UDP "param_list"  --> master.mav.param_request_list_send()
                                                                       loop terima PARAM_VALUE
  cache tabel param di GUI  <--ws--   forward UDP "param_value"  <-- kirim tiap PARAM_VALUE diterima (atau batch)
  edit 1 nilai + confirm     ---ws--> "param_set" {name,value}   --> master.mav.param_set_send(...)
  toast ACK/NACK             <--ws--  "param_ack" {name,ok}      <-- verifikasi PARAM_VALUE balik cocok
```

- **server.js**: tambah 3 command pass-through baru (`param_list`, `param_set`, `param_get`)
  mengikuti pola command existing (lihat `switch (name)` command handler ~baris 441) — server
  hanya meneruskan JSON via UDP, tidak menyimpan state param (source of truth = FC).
- **rov_agent.py**: tambah handler di `command_listener()` mirip `apply_thruster_config()`:
  - `param_list` → `param_request_list_send()`, lalu di loop utama setiap `PARAM_VALUE` diterima,
    kirim balik ke GUI via UDP `send_telemetry`-style channel (bukan polling homebrew).
  - `param_set` → `param_set_send()` satu param, tunggu `PARAM_VALUE` echo untuk konfirmasi
    (pola sudah ada persis di `apply_thruster_config`, tinggal generalisasi jadi 1 fungsi
    `set_param(name, value, type)` dipakai ulang oleh thruster_config juga — **hindari duplikasi kode**).
- Param metadata (deskripsi/range/unit) ArduSub: cek apakah `pymavlink` sudah bundel
  `ardupilotmega.xml`/param metadata JSON secara offline; kalau tidak ada, v1 cukup tampilkan
  nama+nilai+tipe mentah tanpa deskripsi (parity minimum, bukan blocker).

### 1.3 UI (public/js/pages/vehicle.js — baru)

- Tabel param: search box (filter nama, mis. ketik "MOT" → semua `MOT_*`), grouping per
  prefix (`FRAME`, `MOT`, `PID` sudah dicover, `BATT`, `FS`, `RC`, dll).
- Klik nilai → input inline → **gerbang konfirmasi** (modal "Ubah `MOT_1_DIRECTION` dari `1`
  ke `-1`? Ini bisa mengubah arah thruster." + tombol Confirm/Cancel) sebelum kirim `param_set`.
- Badge status tiap baris: synced (hijau) / pending (kuning, menunggu ACK) / gagal (merah).
- Tombol "Refresh All" → kirim ulang `param_list`.
- Tombol kalibrasi (Level/Accel/Compass) dengan progress bar dari `STATUSTEXT`.

### 1.4 File yang disentuh

- `public/index.html` — tambah sidebar entry "Vehicle" + container halaman.
- `public/js/pages/vehicle.js` — **baru**, render tabel + modal konfirmasi.
- `public/js/app.js` — daftarkan page baru (pola sama seperti page lain).
- `server/server.js` — 3 command baru (`param_list`, `param_set`, `param_get`) di command switch.
- `rov_agent.py` — handler param di `command_listener()` + refactor `apply_thruster_config`
  agar reuse fungsi `set_param()` baru.
- `test_rov_agent_param.py` (baru, ikuti pola `test_rov_axes.py`) — unit test `set_param()`.

---

## 2. Fungsi B — Analyze View (halaman baru: **Analyze**)

### 2.1 Cakupan (dipangkas dari QGC Analyze)

| Sub-fitur QGC | Diadaptasi? | Catatan |
|---|---|---|
| MAVLink Inspector (live message tree) | Ya — inti fitur | Tabel semua MAVLink message type + field terakhir diterima, update live |
| Log download & GeoTag/plot | **Tidak** — tidak ada dataflash/SD card log di alur ini | Di luar scope umbilical-only |
| Plot custom (pilih field → grafik time-series) | Ya | Reuse chart engine dari halaman **Telemetry** (`public/js/pages/telemetry.js`, sudah ada grafik Yaw/Depth/Pitch/Roll) — generalisasi jadi "pilih field apa saja", bukan tulis chart baru |
| CSV/log export | **Sudah ada** (Telemetry → rekam CSV) | Tautkan, tak perlu duplikasi |

### 2.2 Alur data

```
rov_agent.py: setiap MAVLink message masuk (bukan cuma yang sudah dipakai)
   → serialize {type, fields, timestamp} → UDP ke server → WS broadcast "mavlink_msg"
Browser (Analyze page): terima stream, update tree MAVLink Inspector + buffer ring
   untuk field yang dipilih user → render ke chart (reuse telemetry chart util)
```

- **Volume data**: MAVLink Inspector QGC asli menstream SEMUA message — di link umbilical
  serial (bukan radio lossy) ini aman, tapi perlu **throttle per message-type** (mis. maks
  10 Hz per type) di `rov_agent.py` sebelum kirim ke GUI, supaya WS tidak banjir. Sudah ada
  pola throttling di `send_telemetry()` — ikuti pola itu, jangan bikin throttler baru.
- Field yang dipilih untuk plot disimpan di buffer ring browser-side (mis. 60 detik terakhir),
  tidak perlu persist ke server — konsisten dengan sifat "live diagnostic", beda dari Replay
  yang memang didesain untuk playback (lihat memori `replay-feature-implemented`).

### 2.3 UI (public/js/pages/analyze.js — baru)

- Panel kiri: tree/list semua message type aktif (mis. `ATTITUDE`, `VFR_HUD`, `SYS_STATUS`,
  `BATTERY_STATUS`) dengan nilai field ter-update live, mirip tabel key-value QGC.
- Klik field numerik → "+ Add to plot" → muncul di panel kanan sebagai chart line, bisa
  multi-field overlay (opsional v1: 1 chart per field, overlay jadi v2 kalau dibutuhkan).
- Tombol pause/resume stream (freeze untuk baca nilai tanpa gangguan update).

### 2.4 File yang disentuh

- `public/index.html` — sidebar entry "Analyze" + container.
- `public/js/pages/analyze.js` — **baru**.
- `public/js/app.js` — daftarkan page.
- `rov_agent.py` — tambah broadcast generik MAVLink message (throttled) di loop penerima MAVLink
  (dekat `send_telemetry()`), command baru `mavlink_stream` {on/off} untuk toggle dari GUI
  (hemat bandwidth saat halaman Analyze tidak dibuka).
- `server/server.js` — pass-through `mavlink_msg` (server→GUI) dan `mavlink_stream` (GUI→server).

---

## 3. Urutan implementasi yang disarankan

1. **rov_agent.py**: refactor `apply_thruster_config` → fungsi `set_param()` reusable +
   handler `param_list`/`param_set`. Ini fondasi Fungsi A, kecil, testable via unit test murni
   (mock `master.mav`), tanpa hardware.
2. **server.js**: tambah pass-through command param_* (mekanis, low-risk, pola sudah ada).
3. **Vehicle page (frontend)**: tabel param + gerbang konfirmasi. Testable manual pakai
   `npm run sim` dulu (server sim tidak punya FC asli — perlu cek apakai mode --sim server.js
   support mock param, atau uji lewat SITL ArduSub yang sudah didokumentasikan di
   `autonomy/SITL_SETUP.md`).
4. **rov_agent.py**: broadcast MAVLink generik (throttled) + toggle `mavlink_stream`.
5. **Analyze page (frontend)**: MAVLink Inspector tree + reuse chart util dari Telemetry.
6. Dokumentasi: update [README-WORK.md](README-WORK.md) §2 (tabel halaman) dan §3 (tabel command)
   dengan entri baru, sama seperti pola dokumentasi Mode ACRO di [CONTROL-MAPPING.md](CONTROL-MAPPING.md).

## 4. Yang eksplisit di luar scope

- Firmware flashing / bootloader (QGC "Firmware" tab) — berisiko brick FC, tak relevan untuk
  kompetisi terjadwal.
- Geofence & mission planner (waypoint) — ROV bawah air tanpa GPS, tak applicable.
- Radio-link telemetry stats (RSSI dsb.) — tidak ada radio, kabel umbilical.
- Log dataflash download — tidak ada storage SD card di alur saat ini.

## 5. Pertanyaan terbuka (perlu keputusan sebelum coding)

- Apakah param metadata (deskripsi/range per param ArduSub) tersedia offline untuk dibundel,
  atau v1 tabel param tanpa deskripsi dulu?
- Apakah `server.js --sim` perlu mock param list (untuk dev tanpa FC), atau development fitur
  ini wajib pakai SITL ArduSub (`autonomy/SITL_SETUP.md`) / hardware asli?
- Prioritas: Fungsi A (Vehicle Config) dan Fungsi B (Analyze) independen satu sama lain —
  bisa dikerjakan sebagai 2 PR terpisah. Mana duluan?

---

## 6. Catatan hasil implementasi (koreksi terhadap rencana di atas)

Empat asumsi di dokumen ini ternyata tidak sesuai kode yang ada. Dicatat di sini supaya
rencana lama tidak menyesatkan pembaca berikutnya:

1. **§1.2 "`switch (name)` command handler ~baris 441" salah alamat.** Dispatch command
   di `server/server.js` adalah **if-chain** (`if (msg.type === "cmd")`), bukan `switch`;
   `switch (name)` di baris ~452 itu `applySimCommand`, khusus mode SIM. Lebih penting:
   **jalur GUI→ROV tidak perlu diubah sama sekali** — tidak ada allowlist nama command,
   dan objek yang diteruskan sudah membawa `name` + `value`, jadi `param_set` cukup
   menaruh payload-nya di dalam `value` sebagai objek.
2. **Yang wajib diubah justru jalur balik ROV→GUI.** `udp.on("message")` membungkus
   **setiap** datagram sebagai `{type:"telemetry"}` tanpa pengecekan, dan `broadcast()`
   men-tap yang bertipe telemetry ke perekam Replay. Tanpa diskriminator envelope,
   `param_batch`/`mavlink_msg` akan tertulis ke `trajectory.jsonl`.
3. **§1.2 keliru menyebut `apply_thruster_config` sudah punya pola tunggu-echo.**
   Fungsi itu hanya `param_set_send` + `time.sleep(0.1)`; **tidak ada handler
   `PARAM_VALUE` sama sekali** di `rov_agent.py` sebelum perubahan ini. Verifikasi echo
   adalah perilaku baru — dan refactor `set_param()` sekalian memperbaiki penulisan
   thruster yang selama ini tidak pernah diverifikasi.
4. **§5 pertanyaan metadata param: terjawab — tidak tersedia offline.** pymavlink tidak
   membundel metadata param, dan repo hanya punya dump **nilai**
   (`parameters_ardusub.params`, tanpa deskripsi/rentang/satuan). v1 karena itu
   menampilkan nama + nilai + tipe saja. Kolom tipe pada dump memakai enum
   `MAV_PARAM_TYPE` yang sama, sehingga dump tetap berguna sebagai fixture mode SIM.

### Jawaban pertanyaan §5 lainnya
- **Mode dev:** `server.js --sim` diberi mock param (`server/sim-params.js`) dari dump
  nyata Pixhawk, jadi kedua halaman bisa dikembangkan & didemokan tanpa FC. SITL tetap
  dipakai untuk verifikasi akhir.
- **Urutan:** Fungsi A dan B dikerjakan berurutan di atas fondasi envelope yang sama.
- **Kalibrasi sensor (§1.1):** **ditunda**, tidak masuk v1 — butuh wahana kering/diam dan
  alur progres tersendiri; salah picu menjelang lomba merusak kalibrasi yang sudah benar.

### Dump param yang dipakai
`parameters_ardusub.params` di root repo **diganti** dengan dump QGroundControl yang
diambil langsung dari Pixhawk wahana (ArduSub 4.5.7, git `b09fafe2`, 975 param). Dump
lama (968 param, git `30257f01`) berasal dari wahana lain: `BATT_*` tidak lengkap dan
masih memuat `INS_ACC3*` (3 IMU) padahal wahana ini 2 IMU.

## 7. Yang ditemukan tapi TIDAK diperbaiki di sini

- ~~**`pid`, `pool_depth`, dan `viewer_access` tidak punya cabang di
  `command_listener()`**~~ — **SUDAH DIPERBAIKI** menyusul, lihat
  [README-WORK.md](../README-WORK.md) §10. `pid` kini menulis
  `ATC_RAT_YAW_*` + `PSC_ACCZ_*` lewat `set_param()` dengan rentang aman
  (`rov_pid.py`), `pool_depth` membatasi `depth_target`, dan `viewer_access` masuk
  `GUI_ONLY_COMMANDS`.
- **`manipulator` masih belum tertangani.** GUI mengirimkannya dan `server/server.js:350`
  meneruskannya utuh, tapi `command_listener()` tidak punya cabangnya sehingga jatuh ke
  `unknown command` — dan `handle_manipulator()` (`rov_agent.py`) tidak pernah dipanggil
  dari mana pun. Menyangkut protokol manipulator & servo channel, perlu PR tersendiri.
- **Dashboard butuh internet untuk dimuat.** `public/index.html` mengambil three.js &
  Chart.js dari CDN (unpkg/jsdelivr) lewat importmap. Di venue tanpa internet — kondisi
  yang justru diwajibkan aturan "tanpa wireless" — **seluruh dashboard gagal dimuat**,
  bukan cuma grafiknya. Perbaikannya: vendor keduanya ke `public/vendor/` seperti yang
  sudah dilakukan untuk `jsqr.min.js`, lalu arahkan importmap ke file lokal.
