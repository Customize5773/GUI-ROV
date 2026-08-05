# CONTROL-MAPPING.md

GUI-ROV HYDROSHIP — Input Mapping Reference
Version: 3.0 | Last Updated: 2026-08-05 | Wahana: **BlueROV1 frame, 6 thruster 6-DoF** (`FRAME_CONFIG = 0`)

> Sumber kebenaran mapping default: [`shared/joystick-profile.js`](shared/joystick-profile.js).
> Modul ES ini diimpor langsung oleh browser (`joystick-state.js`) dan server (`joystick-config.js` via `await import()`),
> sehingga tidak ada tabel default yang disalin ganda.
>
> Profil aktif operator tersimpan di OS config dir (`~/.config/hydroship/` atau `%APPDATA%\hydroship\`),
> overridable via `HYDROSHIP_CONFIG_DIR`. Salinan factory (`server/config/joystick-profile.json`, versi 2)
> dipakai sebagai seed dan dimigrasi ke v3 saat pertama kali dimuat.
>
> `public/js/joystick-defaults.json` adalah **file stale** — hanya dirujuk dalam satu komentar di `config.js` dan
> tidak lagi mencerminkan default aktual. Jangan acuan file ini.

---

## 1. Logitech Gamepad F310 — WAJIB mode X-Input

Geser switch di belakang controller ke posisi **`X`**. Browser lalu melaporkannya
sebagai *standard mapping* Xbox-style.

### 1.1 Fakta penting standard mapping

| | |
|---|---|
| Jumlah axis | **4 saja** — `0`=Left X, `1`=Left Y, `2`=Right X, `3`=Right Y |
| Trigger LT/RT | **BUKAN axis.** Keduanya adalah *button analog* nomor **6** dan **7** |
| Jumlah button | **17** (0–16), termasuk D-Pad di **12–15** dan Guide di **16** |

Inilah sebabnya grip analog memakai sumber virtual bernama **`triggers`**
(nilai = `RT − LT`) dan bukan "axis 4" — axis 4 tidak pernah ada di F310 X-Input.
Perhitungan `triggers` terjadi di `joystick-state.js` (`updateJoystickStateFromGamepad`),
membaca nilai tombol LT/RT langsung dari `rawButtons`, bukan dari `axisConfig`.

### 1.2 Axis (gerak wahana)

| Input | Assigned | Stik fisik | Arah |
|---|---|---|---|
| `axis 0` | `Axis R` → **yaw** | Kiri ↔ | Kanan = putar CW |
| `axis 1` | `Axis Z` → **heave** | Kiri ↕ | Dorong ke atas = naik |
| `axis 2` | `Axis Y` → **sway** | Kanan ↔ | Kanan = geser kanan |
| `axis 3` | `Axis X` → **surge** | Kanan ↕ | Dorong ke atas = maju |
| `triggers` | `Grip` | LT / RT | RT = menutup, LT = membuka (proporsional) |

Konvensi `min`/`max`: bila **min > max**, axis dibalik. Sumbu Y gamepad bernilai
negatif saat didorong ke atas, jadi `heave` dan `surge` memang dibalik.

### 1.3 Button — layer Regular

| Btn | F310 | Aksi |
|---|---|---|
| 0 | A | `grip_close` |
| 1 | B | `grip_open` |
| 2 | X | `grip_neutral` |
| 3 | Y | — |
| 4 | LB | `gain_dec` |
| 5 | RB | `gain_inc` |
| 6 | LT | *(grip analog)* |
| 7 | RT | *(grip analog)* |
| 8 | Back | **`e_stop`** |
| 9 | Start | `arm` |
| 10 | LS klik | **shift modifier** |
| 11 | RS klik | — |
| 12 | D-Pad ↑ | `mode_depth_hold` |
| 13 | D-Pad ↓ | `mode_manual` |
| 14 | D-Pad ← | `mode_stabilize` |
| 15 | D-Pad → | `light_toggle` |
| 16 | Guide | — |

### 1.4 Button — layer Shift (tahan **LS klik**)

| Btn | F310 | Aksi |
|---|---|---|
| 8 | Back | **`e_stop`** |
| 9 | Start | `disarm` |
| sisanya | | — |

> **`e_stop` sengaja ada di KEDUA layer.** Penekanan shift yang tidak disengaja
> tidak boleh pernah menghilangkan tombol darurat.
>
> Shift memakai LS klik (bukan tombol muka) supaya A/B/X/Y tetap bebas. Profil
> lama memakai `shiftButton: 0` — tombol A — sehingga A praktis tidak bisa dipakai.

### 1.5 Daftar aksi yang sah

`no_function`, `arm`, `disarm`, `e_stop`, `mode_manual`, `mode_stabilize`,
`mode_depth_hold`, `mode_acro`, `grip_open`, `grip_close`, `grip_neutral`,
`light_toggle`, `gain_inc`, `gain_dec`.

`mode_acro` sengaja **tidak** punya binding default: ke-16 tombol pad sudah
terpakai, dan menggeser binding yang sudah dihafal operator demi mode paling
berisiko adalah pertukaran yang buruk. Bind manual lewat halaman **Joystick**
kalau memang dibutuhkan saat trial.

Aksi lama (`mount_tilt_*`, `mount_center`, `actuator1_*`, `lights_brighter/dimmer`,
`input_hold_set`) **sudah dihapus** — wahana tidak punya hardware-nya dan
`rov_agent.py` tidak pernah punya handler-nya, jadi tombolnya diam-diam mati.
Profil tersimpan yang masih memuatnya dimigrasikan otomatis saat dimuat.

---

## 2. Respons stik: deadzone → expo → gain → rate limit

Empat parameter, semuanya bisa disetel di halaman **Joystick** dan ikut tersimpan
di profil. Implementasi murni ada di
[`public/js/axis-shaping.js`](public/js/axis-shaping.js).

```
raw(-1..1) → deadzone → expo → skala min/max(±1000) → × gain → rate limit → kirim
```

| Parameter | Default | Guna |
|---|---|---|
| **Deadzone** | `0.12` | Drift stik di sekitar tengah jadi **tepat 0**. Memakai *rescale*, jadi keluaran mulai dari 0 di tepi zona — tidak melompat. |
| **Expo** | `0.35` | Melandaikan bagian tengah untuk koreksi halus. Defleksi penuh **tetap** ±1000. |
| **Gain** | 6 langkah `25%…100%`, mulai di `40%` | Membatasi thrust maksimum. Diubah saat operasi lewat **LB/RB**, tampil di HUD. Berlaku untuk keyboard juga. |
| **Rate limit** | `4000 /detik` | Meredam hentakan stik jadi lonjakan arus baterai. Sapuan penuh ≈ 0,5 detik. |

Preview di halaman Joystick memanggil **fungsi yang sama persis** dengan jalur
kirim (`axisOutputFor`), jadi angka di layar dijamin identik dengan yang diterima ROV.

---

## 3. Keyboard (cadangan)

Hanya aktif saat tab controller = **Keyboard**.

| Key | Axis | | Key | Aksi |
|---|---|---|---|---|
| `W` / `S` | surge maju / mundur | | `H` | Gripper OPEN |
| `A` / `D` | sway kiri / kanan | | `G` | Gripper CLOSE |
| `Q` / `E` | yaw CCW / CW | | `Space` | **E-Stop** |
| `R` / `F` | heave naik / turun | | | |

Besar langkah = `CONFIG.CONTROL.KEY_AXIS_STEP` (default **400**) dikali gain.
Keyboard tunduk pada gerbang otoritas yang sama dengan gamepad: input ditolak
saat mode Autonomous atau E-Stop terkunci. `Space` dan `H`/`G` aktif tanpa
memandang tab controller.

---

## 4. Mode kontrol

### 4.1 Mode ArduSub (`pilot_mode`)

Peta nama mode punya **satu sumber kebenaran** per bahasa —
[`rov_modes.PILOT_MODE_MAP`](rov_modes.py) di sisi Python dan
[`shared/rov-modes.js`](shared/rov-modes.js) (`PILOT_MODE_MAP`,
`ARDUSUB_MODE_TO_TAB`, `RISKY_ARDUSUB_MODES`, `ACRO_CONFIRM`) di sisi JS —
diimpor langsung oleh `public/js/app.js`, bukan didefinisikan ulang di sana.
Tambah mode baru di kedua file.

| Tab GUI | D-Pad | Perintah | Mode Pixhawk |
|---|---|---|---|
| Manual | ↓ | `pilot_mode="manual"` | `MANUAL` |
| Stabilize | ← | `pilot_mode="stabilize"` | `STABILIZE` |
| Depth Hold | ↑ | `pilot_mode="depth_hold"` | `ALT_HOLD` |
| Acro | — (bind manual) | `pilot_mode="acro"` | `ACRO` |

Sorotan tab **tidak** diset lokal saat diklik — sumbernya hanya string mode dari
HEARTBEAT di telemetry. Karena itu tab GUI dan D-Pad selalu sinkron, dan tab
tidak pernah membohongi operator kalau Pixhawk menolak perpindahan mode (mis.
`ALT_HOLD` ditolak saat sumber kedalaman belum sehat). Tab yang menunggu
konfirmasi tampil putus-putus; setelah 2 detik tanpa konfirmasi muncul peringatan.

Mode di luar keempat tab (`SURFACE`, `POSHOLD`, …) tetap terbaca pada badge di
sebelah kanan tab bar.

`ACRO` tidak ada di semua build/frame ArduSub. `rov_agent.py` memeriksanya lewat
`master.mode_mapping()` — yang berasal dari firmware yang benar-benar terpasang
— dan **menolak** perintah bila mode tidak ada, alih-alih mengirim `set_mode`
yang akan diabaikan diam-diam. Gejalanya di GUI: tab Acro tetap putus-putus dan
muncul peringatan 2 detik kemudian.

### 4.2 Konvensi throttle per mode

`MANUAL_CONTROL.z` ArduSub adalah **0..1000 dengan 500 = diam** — berbeda dari
tiga axis lain yang −1000..1000. Konversinya dilakukan
[`rov_axes.to_mavlink_z()`](rov_axes.py) di sisi Pi.

| Mode | Arti `z = 500` |
|---|---|
| MANUAL | Nol thrust vertikal |
| STABILIZE | Nol thrust vertikal, attitude diratakan |
| ALT_HOLD | **Tahan kedalaman** |
| ACRO | Nol thrust vertikal — **TIDAK** menahan kedalaman |

Netral tetap bermakna benar di keempatnya (diam / tidak mendorong), jadi jalur
axis tidak butuh penanganan khusus per-mode. Yang **berbeda** di ACRO adalah
konsekuensinya: tidak ada yang menahan wahana, jadi netral berarti melayang
mengikuti daya apung, bukan diam di kedalaman.

### 4.2.1 ACRO — apa yang berubah dan pengamannya

Di ACRO tidak ada stabilisasi attitude: stik memerintahkan **rate** (kecepatan
sudut), bukan sudut. Wahana tidak akan meratakan dirinya sendiri. Di kolam
dangkal KKI (≈0.9 m) ini mode yang paling mudah membuat ROV terguling.

Tiga pengaman yang dipasang:

1. **Depth hold dinonaktifkan.** `ACRO` tidak masuk
   [`rov_modes.DEPTH_HOLD_MODES`](rov_modes.py), sehingga `depth_hold_active()`
   bernilai false: `gain_inc`/`gain_dec` ditolak dengan log, dan
   `apply_depth_hold_bias()` mengembalikan paket apa adanya. Tanpa ini, bias
   throttle akan mendorong wahana tanpa satu pun umpan balik yang menstabilkan.
2. **Konfirmasi di GUI, seragam di semua jalur input.** Klik tab **Acro**
   maupun tombol gamepad `mode_acro` sama-sama lewat `requestPilotMode()`
   (`public/js/app.js`), yang menampilkan dialog `ACRO_CONFIRM` sebelum
   mengirim apa pun. Membatalkan = tidak ada command yang dikirim sama
   sekali, dari jalur mana pun.
   > Riwayat: versi awal sengaja melewati dialog untuk jalur gamepad
   > (alasannya: tombol fisik hanya bisa ditekan operator yang sedang
   > memegang pad). Audit berikutnya menandai ini sebagai gerbang keamanan
   > yang tidak seragam antar jalur input, jadi diseragamkan — sekarang
   > gamepad juga wajib konfirmasi seperti tab GUI.
3. **Peringatan visual.** Selama HEARTBEAT melaporkan `ACRO`, badge
   `⚠ ACRO — TANPA STABILISASI` tampil di tab bar dan tab Acro diberi warna
   amber. Peringatan mengikuti mode **aktual**, bukan yang diminta.

**Cakupan test:** ketiga pengaman di atas punya unit test otomatis —
`test_rov_modes.py` (Python, gating depth-hold) dan
`server/test/mode-gating.test.mjs` (JS, pemetaan mode/risky/teks konfirmasi),
keduanya ikut jalan di `python3 -m unittest test_rov_modes` dan `npm test`
di `server/`.

### 4.3 Manual vs Autonomous (`control_mode`)

Gerbang otoritas GUI. Saat Autonomous, thruster dan gripper dari GUI diblokir di
sisi klien; FSM yang memegang kendali. Di luar cakupan trial ini.

---

## 5. Frame & mixing

Wahana memakai **BlueROV1** (6 thruster, 6-DoF) — `FRAME_CONFIG = 0`.

**Tidak ada mixing di repo ini.** GUI hanya mengirim `MANUAL_CONTROL` (x/y/z/r);
ArduSub di Pixhawk yang membagi ke keenam motor sesuai `FRAME_CONFIG`. Satu-satunya
kendali level motor dari dashboard adalah pembalik arah (`MOT_n_DIRECTION`) di
halaman Setup.

Gripper: servo **channel 10** (`SERVO10_FUNCTION = 7`), PWM buka `1900` / tutup
`1100` / netral `1500`, dengan rate-limit + EMA di
[`rov_gripper.py`](rov_gripper.py) supaya tidak menyentak.

### 5.1 Calibration & Thruster Layout

Layout resmi frame `bluerov` (FRAME_TYPE 0), tampak dari atas. Sumber:
[ArduSub — Sub Frame Configurations](https://ardupilot.org/sub/docs/sub-frames.html),
[Blue Robotics — Building a Vehicle Frame](https://www.ardusub.com/quick-start/vehicle-frame.html),
dicocokkan dengan `MOT_1..6_DIRECTION` default di
[`parameters_ardusub.params`](parameters_ardusub.params) (`-1,-1,-1,1,-1,1`).
3-2-1: **2 surge+yaw** (T1, T2), **3 heave** (T3, T4, T5), **1 lateral/sway**
(T6) — sway hanya satu thruster tanpa pasangan penyeimbang, jadi secara
mekanik ikut membangkitkan roll kecil saat dipakai (bukan bug, melainkan
konsekuensi tata letak 3-2-1 itu sendiri — lihat faktor Roll = −0.25 di T6
pada tabel di bawah).

```
              ▲ depan
   T4 o------------o T3      (heave, depan)
      |            |
  T2 o    T6 o    o T1        (surge+yaw kiri/kanan · lateral tengah)
      |            |
      o-----T5-----o          (heave, belakang-tengah)
```

| Motor | Posisi (top-down)        | Roll | Pitch | Yaw   | Throttle | Forward | Lateral | Kontribusi axis     |
|-------|--------------------------|------|-------|-------|----------|---------|---------|---------------------|
| T1    | Tengah-kanan, horizontal | 0    | 0     | −1.0  | 0        | 1.0     | 0       | Surge + Yaw (kanan) |
| T2    | Tengah-kiri, horizontal  | 0    | 0     | +1.0  | 0        | 1.0     | 0       | Surge + Yaw (kiri)  |
| T3    | Depan-kanan, vertikal    | +0.5 | +0.5  | 0     | 0.45     | 0       | 0       | Heave (depan)       |
| T4    | Depan-kiri, vertikal     | −0.5 | +0.5  | 0     | 0.45     | 0       | 0       | Heave (depan)       |
| T5    | Belakang-tengah, vertikal| 0    | −1.0  | 0     | 1.0      | 0       | 0       | Heave (belakang)    |
| T6    | Tengah, horizontal       | −0.25| 0     | 0     | 0        | 0       | 1.0     | Lateral / Sway      |

Kolom faktor di atas adalah kontribusi axis per ArduSub, **bukan** klaim arah
putar CW/CCW mutlak per motor — arah putar fisik tergantung juga pitch
baling-baling & polaritas kabel. Panel Thruster Test di halaman Setup
mewarnai thruster berpasangan (T2↔T4, T1↔T3, T6↔T5) yang seharusnya
counter-rotate saat digerakkan bersamaan, meniru pewarnaan hijau/biru di
diagram resmi Blue Robotics — bukan tabel absolut CW/CCW.

**Cara pakai dengan panel Thruster Test** (halaman Setup): putar tiap thruster
satu per satu dengan throttle rendah, lalu bandingkan arah putaran baling-baling
yang terlihat dengan kolom "Kontribusi axis" di atas. Kalau terasa/terlihat
salah arah, jangan ubah tabel ini — cukup toggle "Reverse arah thruster"
untuk thruster tersebut di kartu THRUSTER SETUP, lalu klik Apply. Ini
menghilangkan tebak-tebak yang biasanya berulang tiap sesi trial.

---

## 6. Safety & failsafe

| Skenario | Respons | Diimplementasi di |
|---|---|---|
| Axis berhenti sampai ke Pi > 0,5 dtk | Pi **streaming NEUTRAL** (`z=500`, sisanya 0) dan menandai `cmd_link: "stale"`; dashboard menampilkan banner merah | [`rov_axes.resolve_manual_packet`](rov_axes.py), `joystick_sender` di [`rov_agent.py`](rov_agent.py) |
| WebSocket putus | E-Stop dikunci, semua axis dinetralkan | `app.js` |
| Gamepad dicabut | Semua axis dinetralkan seketika | `gamepaddisconnected` |
| `Space` / Back / tombol STOP | Netralkan seluruh thruster + disarm, joystick terkunci | `btnStop` |
| Mode Autonomous | Thruster & gripper dari GUI diblokir | gerbang di `pollGamepad`, keydown, dan `sendGripper` |
| ARM ulang | Melepas kunci E-Stop | `btnArm` |

**Kenapa Pi tetap mengirim saat stale, bukan diam?** ArduSub mengharapkan aliran
`MANUAL_CONTROL` yang kontinu. Kalau ground station diam, failsafe pilot-input
Pixhawk yang jalan dan perilakunya tergantung parameter. Streaming netral jauh
lebih bisa diprediksi: diam di tempat, dan di ALT_HOLD berarti tahan kedalaman.

**Heartbeat axis.** Dashboard mengirim ulang nilai axis saat ini ~15 Hz tanpa
memandang jenis controller — dan tetap mengirim **nol eksplisit** saat E-Stop
aktif. Dengan begitu `stale` benar-benar berarti link/GUI mati, bukan sekadar
operator sedang tidak menyentuh stik.

### Urutan darurat

1. **Back** (gamepad) / **Spasi** / tombol **STOP** → thruster netral seketika.
2. Bila tidak respons, **ARM** untuk disarm.
3. Setelah aman, **Start** (ARM ulang) untuk mengaktifkan kembali kontrol.

---

## 7. Alur perintah

```
Gamepad / Keyboard
  app.js · joystick-state.js (deadzone → expo → gain → rate limit)
        │  WebSocket :8080   {type:"cmd", name, value}
  server/server.js  (clamp ±1000, tap rekaman)
        │  UDP JSON :14550   {name, value, t}
  rov_agent.py  (clamp ulang, fail-safe idle, heave → z 0..1000)
        │  pymavlink MANUAL_CONTROL / MAV_CMD_DO_SET_SERVO
  Pixhawk ArduSub  (mixing BlueROV1 → 6 thruster)
```
Telemetry balik: Pixhawk → `rov_agent.py` → UDP :14551 → server → WS → dashboard.

---

## 8. Reference: perintah UDP

| `name` | `value` | Keterangan |
|---|---|---|
| `arm` | `true`/`false` | ARM / DISARM |
| `stop` | `true` | Failsafe: disarm |
| `surge` `sway` `yaw` `heave` | −1000..1000 | Axis gerak (netral 0) |
| `pilot_mode` | `"manual"`/`"stabilize"`/`"depth_hold"`/`"acro"` | Mode ArduSub |
| `control_mode` | `"manual"`/`"autonomous"` | Gerbang otoritas GUI |
| `gripper` | `"open"`/`"close"`/`-1000..1000` | Posisi gripper |
| `light` | `true`/`false` | Lampu (belum terhubung hardware) |
| `thruster_config` | `{motors:{1..6: ±1}}` | `MOT_n_DIRECTION` |
| `motor_test` | `{motor:1-6, throttle:%, duration:s, direction}` | Uji spin satu thruster (`MAV_CMD_DO_MOTOR_TEST`, lihat §5.1) |

Diterima tanpa aksi (murni state dashboard): `controller`, `set_surface`,
`snapshot`, `record`. Selain daftar di atas akan tercatat sebagai
`unknown command` di log Pi — itu memang sinyal ada yang perlu diperiksa.

Telemetry balik menambahkan `cmd_link: "ok" | "stale"` selain `heading`, `depth`,
`roll`, `pitch`, `temp`, `voltage`, `armed`, `light`, `mode`.
