# CONTROL-MAPPING.md

GUI-ROV HYDROSHIP — Input Mapping Reference
Version: 2.0 | Last Updated: 2026-07-30 | Wahana: **BlueROV1 frame, 6 thruster 6-DoF** (`FRAME_CONFIG = 0`)

> Sumber kebenaran mapping default: [`public/js/joystick-defaults.json`](public/js/joystick-defaults.json).
> File itu dibaca oleh server (`require`) **dan** browser (`fetch`), jadi tidak ada
> lagi tabel default yang disalin ganda. Profil aktif operator tersimpan di
> [`server/config/joystick-profile.json`](server/config/joystick-profile.json)
> dan menang atas default. Dokumen ini menjelaskan isi keduanya — kalau berbeda,
> **file JSON yang benar**.

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
`mode_depth_hold`, `grip_open`, `grip_close`, `grip_neutral`, `light_toggle`,
`gain_inc`, `gain_dec`.

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

| Tab GUI | D-Pad | Perintah | Mode Pixhawk |
|---|---|---|---|
| Manual | ↓ | `pilot_mode="manual"` | `MANUAL` |
| Stabilize | ← | `pilot_mode="stabilize"` | `STABILIZE` |
| Depth Hold | ↑ | `pilot_mode="depth_hold"` | `ALT_HOLD` |

Sorotan tab **tidak** diset lokal saat diklik — sumbernya hanya string mode dari
HEARTBEAT di telemetry. Karena itu tab GUI dan D-Pad selalu sinkron, dan tab
tidak pernah membohongi operator kalau Pixhawk menolak perpindahan mode (mis.
`ALT_HOLD` ditolak saat sumber kedalaman belum sehat). Tab yang menunggu
konfirmasi tampil putus-putus; setelah 2 detik tanpa konfirmasi muncul peringatan.

Mode di luar ketiga tab (`SURFACE`, `POSHOLD`, …) tetap terbaca pada badge di
sebelah kanan tab bar.

### 4.2 Konvensi throttle di ketiga mode

`MANUAL_CONTROL.z` ArduSub adalah **0..1000 dengan 500 = diam** — berbeda dari
tiga axis lain yang −1000..1000. Konversinya dilakukan
[`rov_axes.to_mavlink_z()`](rov_axes.py) di sisi Pi.

| Mode | Arti `z = 500` |
|---|---|
| MANUAL | Nol thrust vertikal |
| STABILIZE | Nol thrust vertikal, attitude diratakan |
| ALT_HOLD | **Tahan kedalaman** |

Karena netral bermakna benar di ketiganya, tidak ada penanganan khusus per-mode
di jalur axis.

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
| `pilot_mode` | `"manual"`/`"stabilize"`/`"depth_hold"` | Mode ArduSub |
| `control_mode` | `"manual"`/`"autonomous"` | Gerbang otoritas GUI |
| `gripper` | `"open"`/`"close"`/`-1000..1000` | Posisi gripper |
| `light` | `true`/`false` | Lampu (belum terhubung hardware) |
| `thruster_config` | `{motors:{1..6: ±1}}` | `MOT_n_DIRECTION` |

Diterima tanpa aksi (murni state dashboard): `controller`, `set_surface`,
`snapshot`, `record`. Selain daftar di atas akan tercatat sebagai
`unknown command` di log Pi — itu memang sinyal ada yang perlu diperiksa.

Telemetry balik menambahkan `cmd_link: "ok" | "stale"` selain `heading`, `depth`,
`roll`, `pitch`, `temp`, `voltage`, `armed`, `light`, `mode`.
