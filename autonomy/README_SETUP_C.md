# Setup C — Integrasi manual GUI ↔ `rov_link.py` ↔ mock/SITL

Jalur **manual per-terminal**: menyalakan tiap komponen sendiri-sendiri, dengan
kriteria sukses di tiap langkah. Gunanya bukan untuk pemakaian sehari-hari —
untuk itu ada `tools/launch_sitl.py` yang menggabungkan semuanya jadi satu
perintah — melainkan untuk **mendiagnosa saat launcher gagal**: kalau satu
komponen tak mau tersambung, di sinilah ketahuan yang mana.

| Dokumen | Untuk apa |
|---|---|
| **Setup C (file ini)** | jalur manual, komponen per komponen, buat debugging |
| `SITL_KKI_QUICKSTART.md` | pemakaian normal via `launch_sitl.py` + skenario A/B/C |
| `SITL_SETUP.md` | membangun ArduSub SITL sungguhan di WSL2 |
| `TEST_CHECKLIST.md` | checklist verifikasi yang harus dicentang |

Topologi yang dibangun (tiga port UDP, tak boleh bentrok):

```
browser ──WS:8080── server.js ──cmd JSON :14550──► rov_link ──MANUAL_CONTROL──► vehicle
                              ◄──telem JSON :14551──        ◄──ATTITUDE/PRESSURE──  (mock/SITL)
                                                       MAVLink terpisah :14555
                              └── fan-out telem :14552 ──► Mission5FSM (bila FSM dipakai)
```

---

## Sebelum mulai — empat jebakan yang memakan waktu

Keempatnya pernah benar-benar menyesatkan debugging di proyek ini. Baca dulu.

### 1. `RPI_ADDR` WAJIB di-set ke `127.0.0.1`

Default `server.js` adalah `192.168.2.2`, yaitu **ROV asli**. Kalau lupa
di-set, telemetri tetap terlihat normal di GUI — karena server mem-*bind*
:14551 dan menerima dari siapa pun — tapi **semua command dikirim ke alamat
yang salah dan hilang tanpa jejak**. Gejalanya: GUI tampak hidup, ROV/mock
tidak bereaksi sama sekali, tanpa satu pun pesan error.

### 2. Pakai `npm start`, JANGAN `npm run sim`

`npm run sim` membangkitkan telemetri palsu sendiri tiap 100 ms dan
menyiarkannya bersamaan dengan telemetri UDP asli. Angka di GUI jadi campuran
dua sumber — mustahil dipercaya saat verifikasi.

### 3. `PYTHONPATH=` di depan tiap perintah Python (mesin dengan ROS)

Kalau ROS Humble ter-*source*, `PYTHONPATH`-nya membuat pytest gagal
mengumpulkan test (`ModuleNotFoundError: lark`) dan bisa mengacaukan import.
Mengosongkannya per-perintah sudah cukup:

```bash
PYTHONPATH= python3 rov_link.py ...
```

### 4. Cek port bebas dulu

```bash
ss -tulnp | grep -E "8080|14550|14551|14552|14555"   # harus kosong
```

Sisa proses dari run sebelumnya yang masih memegang port adalah penyebab
"heartbeat timeout" paling sering.

---

## Terminal 1 — Vehicle (mock)

Mock cukup untuk menguji seluruh rantai command/telemetri tanpa WSL2.
Untuk fisika ArduSub sungguhan, ganti langkah ini dengan `SITL_SETUP.md`.

```bash
cd autonomy
PYTHONPATH= python3 sitl_mock.py --mavlink udpout:127.0.0.1:14555
```

**Kriteria sukses:** muncul `[MOCK] mengirim sebagai vehicle…` dan heartbeat
mengalir. Belum ada yang menyambung — itu normal.

---

## Terminal 2 — Jembatan `rov_link.py`

```bash
cd autonomy
PYTHONPATH= python3 rov_link.py \
    --server 127.0.0.1 \
    --mavlink udpin:0.0.0.0:14555 \
    --telem-extra 127.0.0.1:14552        # hanya bila FSM akan dipakai
```

**Kriteria sukses:**

```
[MAV] connecting: udpin:0.0.0.0:14555
[MAV] menunggu heartbeat dari vehicle… (timeout 10s)
[MAV] terhubung: system=1 component=0
[JSON] dengar command di :14550
[OK] rov_link berjalan. Ctrl+C untuk berhenti.
```

(Nomor `component` ikut vehicle-nya — mock dan ArduSub SITL bisa berbeda; yang
penting baris `terhubung:` muncul.)

Kalau berhenti di baris "menunggu heartbeat": Terminal 1 mati, atau port
`14555` dipakai proses lain. `--hb-timeout` menaikkan batas tunggu.

`--telem-extra` membuat telemetri di-*fan-out* ke FSM di :14552, supaya GUI
(:14551) dan FSM tidak berebut port yang sama.

---

## Terminal 3 — GUI

```bash
cd server
RPI_ADDR=127.0.0.1 npm start          # BUKAN npm run sim (lihat jebakan #2)
```

Buka `http://localhost:8080`.

**Kriteria sukses:** depth/heading/attitude bergerak, badge terhubung menyala,
dan menggerakkan axis di GUI memunculkan `[CMD]` di Terminal 2. Kalau
telemetri masuk tapi command tak muncul di Terminal 2 → hampir pasti
`RPI_ADDR` (jebakan #1).

---

## Terminal 4 — Mission5 FSM (opsional)

Dua cara menyalakan FSM, dan keduanya **berbeda**:

**(a) Langsung, tanpa menunggu toggle GUI** — untuk uji rantai misi:

```bash
cd autonomy
PYTHONPATH= python3 fsm/mission5.py \
    --server 127.0.0.1 --telem-port 14552 \
    --vision mock --start-state DIVE --no-wait-autonomous
```

**(b) Lewat toggle GUI** — jalur yang dipakai saat lomba. Jangan jalankan
`fsm/mission5.py` sendiri; `rov_link.py` yang menyalakannya saat menerima
`control_mode=autonomous`. Sumber visi FSM ditentukan flag `--fsm-vision-source`
**milik `rov_link.py`**, bukan `--vision` milik FSM:

```bash
PYTHONPATH= python3 rov_link.py --server 127.0.0.1 --mavlink udpin:0.0.0.0:14555 \
    --telem-extra 127.0.0.1:14552 --fsm-vision-source mock
```

Lupa `--fsm-vision-source` berarti FSM diam-diam memakai default `usb` dan
menggantung di mesin tanpa kamera.

**Kriteria sukses:** rantai transisi tuntas sampai `DONE` dengan skor penuh:

```
[FSM] IDLE → DIVE → SCAN_QR → GRAB → NAV_WALL → HANG → SURFACE → DOCK
[FSM] → M5_REDIVE → M5_DOCK → M5_ENGAGE → M5_UNHOOK → M5_ASCEND → DONE
[FSM]  TOTAL               : 100/100
```

---

## Setara satu perintah

Setelah paham potongan-potongannya, keempat terminal di atas sama dengan:

```bash
cd autonomy
PYTHONPATH= python3 tools/launch_sitl.py --fsm --vision mock \
    --start-state DIVE --no-wait-autonomous
```

Tambahkan `--no-gui` bila `server.js` sudah jalan sendiri di terminal lain
(launcher tidak akan berebut :8080/:14551).

---

## Dua hal yang TIDAK bisa diverifikasi lewat jalur ini

- **Kill-switch joystick.** Butuh joystick fisik. Logikanya sendiri sudah
  dikunci `tests/test_rov_link.py`, tapi rangkaian nyata stik→browser→server→
  `rov_link` hanya terbukti di hardware. Catatan: jangan setel deadzone
  joystick ke 0 — yang menyaring drift stik adalah deadzone sisi-GUI, bukan
  `KILL_SWITCH_DEADZONE`.
- **Verifikasi visual GUI.** Gerak ROV 3D, badge mode, dan console F12 bersih
  butuh mata. Daftarnya ada di `TEST_CHECKLIST.md` §"Yang masih butuh mata".

Handoff Manual↔Autonomous dan tombol STOP **sudah** terverifikasi otomatis —
jalankan `node tools/verify_handoff.mjs` (menyalakan stack-nya sendiri di port
terpisah, jadi tak perlu setup di atas).
