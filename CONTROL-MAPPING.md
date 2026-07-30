# CONTROL-MAPPING.md

GUI-ROV HYDROSHIP — Input Mapping Reference
Version: 1.0 | Last Updated: 2026-07-30

---

## 1. Keyboard Controls

### 1.1 Movement (Thrust)

| Key | Axis | Value | Direction |
|-----|------|-------|-----------|
| `W` | `surge` | +50 | Maju (forward) |
| `S` | `surge` | −50 | Mundur (backward) |
| `D` | `sway` | +50 | Kanan (right) |
| `A` | `sway` | −50 | Kiri (left) |
| `E` | `yaw` | +50 | Rotasi searah jarum jam |
| `Q` | `yaw` | −50 | Rotasi berlawanan jarum jam |
| `R` | `heave` | +50 | Naik (ascend) |
| `F` | `heave` | −50 | Turun (descend) |

- Tahan key untuk gerakan terus-menerus (±50).
- Lepas key → axis dinetralkan ke `0`.
- Hanya aktif saat tab controller = **Keyboard**.

### 1.2 Gripper / Manipulator

| Key | Action | Command |
|-----|--------|---------|
| `H` | Gripper OPEN | `gripper="open"` |
| `G` | Gripper CLOSE | `gripper="close"` |

### 1.3 System Toggles

| Key | Action | Command |
|-----|--------|---------|
| `Space` | Emergency STOP (failsafe) | `stop=true` |

- `Space` aktif **di semua mode** controller, tidak peduli apakah Keyboard atau Gamepad.
- E-Stop mengunci joystick sampai operator ARM ulang.

---

## 2. Logitech Gamepad F310 — X-Input Mode (Default)

F310 harus di-set ke mode **X-Input** (switch di belakang controller → posisi `X`).
Dalam mode ini, controller terlihat seperti Xbox 360 controller oleh browser.

### 2.1 Axis Mapping (Movement)

| F310 Input | Assigned GUI Axis | GUI Nilai | Arah |
|------------|-------------------|-----------|------|
| Left Stick X (`axis 0`) | `surge` | −1000 ↔ +1000 | ↔ Maju/Mundur |
| Left Stick Y (`axis 1`) | `heave` | +1000 ↔ −1000 | ↕ Naik/Turun |
| Right Stick X (`axis 2`) | `yaw` | −1000 ↔ +1000 | ↔ Rotasi CW/CCW |
| Right Stick Y (`axis 3`) | `sway` | +1000 ↔ −1000 | ↕ Kanan/Kiri |
| LT/RT (`axis 4`) | `no_function` | −1 ↔ +1 | — |

- Deadzone: `GP_DEADZONE = 0.12` (12% stick offset diabaikan).
- Nilai di-clamp ke −1000..1000 oleh server sebelum diteruskan ke UDP.
- Saat gamepad disconnect, semua axis dinetralkan otomatis.

### 2.2 Button Mapping — Regular Layer

| F310 Button | Action | Mode | Perintah yang Dikirim |
|-------------|--------|------|----------------------|
| `A` (Btn 0) | `arm` | toggle | `arm=true/false` |
| `B` (Btn 1) | `disarm` | toggle | `arm=false` |
| `X` (Btn 2) | `mode_manual` | toggle | `pilot_mode="manual"` |
| `Y` (Btn 3) | `mode_stabilize` | toggle | `pilot_mode="stabilize"` |
| `LB` (Btn 4) | `mode_depth_hold` | toggle | `pilot_mode="depth_hold"` |
| `LT` (Btn 5) | `mount_tilt_up` | hold | `mount_tilt={dir:"up",hold:true}` |
| `RT` (Btn 6) | `mount_tilt_down` | hold | `mount_tilt={dir:"down",hold:true}` |
| `Back` (Btn 7) | `mount_center` | toggle | `mount_center=true` |
| `Start` (Btn 8) | `actuator1_inc` | hold | `actuator1={dir:"inc",hold:true}` |
| `LS` (Btn 9) | `actuator1_dec` | hold | `actuator1={dir:"dec",hold:true}` |
| `RS` (Btn 10) | `lights_brighter` | hold | `light_level={dir:"up",hold:true}` |
| `RS` (Btn 11) | `lights_dimmer` | hold | `light_level={dir:"down",hold:true}` |

### 2.3 Button Mapping — Shift Layer

Shift layer pada F310 (tombol `Guide` / tombol tengah) saat ini **belum dimapping** — semua tombol di shift layer bernilai `no_function`. Shift layer dapat dikonfigurasi ulang di `server/config/joystick-profile.json`.

### 2.4 D-Pad (Hat Switch)

| Arah | Biasanya | Status |
|------|----------|--------|
| Up | — | Tidak terpakai (default `no_function`) |
| Down | — | Tidak terpakai |
| Left | — | Tidak terpakai |
| Right | — | Tidak terpakai |

D-Pad dapat dimapping dengan mengedit `joystick-profile.json`.

---

## 3. F310 X-Input vs DirectInput

### 3.1 Perbedaan Utama

| Aspek | X-Input Mode | DirectInput Mode |
|-------|-------------|-----------------|
| Switch di belakang | Posisi `X` | Posisi `D` |
| Terlihat oleh browser | Sebagai Xbox 360 controller | Sebagai perangkat input generik |
| Jumlah axis | 6 (0–5) | 8 (0–7) |
| Jumlah tombol | 11 (0–10) + D-Pad | 12 (0–11) |
| Nomorasi button | A=0, B=1, X=2, Y=3, LB=4, RB=5, Back=6, Start=7, LS=8, RS=9, Guide=10 | Berbeda — tergantung driver |
| Nomorasi axis | Konsisten (Xbox-style) | Berbeda — tergantung driver |
| Kompatibilitas | Tinggi (standar gaming) | Legacy (kompatibel dengan game lama) |

### 3.2 Konfigurasi di `joystick-profile.json`

File `server/config/joystick-profile.json` berisi mapping yang **saat ini aktif** untuk mode X-Input.

Untuk beralih ke DirectInput, ubah `axisConfig` dan `buttonConfig` sesuai nomorasi DirectInput:

```json
{
  "axisConfig": [
    { "input": "axis 0", "assigned": "Axis X",  "min": -1000, "max": 1000, "direction": "↔" },
    { "input": "axis 1", "assigned": "Axis Y",  "min": 1000,  "max": -1000, "direction": "↕" },
    { "input": "axis 2", "assigned": "Axis R",  "min": -1000, "max": 1000, "direction": "↔" },
    { "input": "axis 3", "assigned": "Axis Z",  "min": 1000,  "max": -1000, "direction": "↕" },
    { "input": "axis 4", "assigned": "No function", "min": -1, "max": 1, "direction": "↕" },
    { "input": "axis 5", "assigned": "No function", "min": -1, "max": 1, "direction": "↕" },
    { "input": "axis 6", "assigned": "No function", "min": -1, "max": 1, "direction": "↕" },
    { "input": "axis 7", "assigned": "No function", "min": -1, "max": 1, "direction": "↕" }
  ],
  "buttonConfig": {
    "regular": [
      { "action": "no_function", "button": 0, "mode": "toggle" },
      ...
    ]
  }
}
```

> **Catatan:** Setelah mengubah `joystick-profile.json`, restart `rov-agent` di RPI (`sudo systemctl restart rov-agent`) dan restart server Node.js (`Ctrl+C` → `npm start`).

### 3.3 Cara Mendeteksi Mode F310

Buka browser → dashboard → tab **Controller** → badge akan menampilkan nama gamepad yang terdeteksi:
- X-Input: `Logitech Gamepad F310` (terlihat sebagai Xbox 360 controller)
- DirectInput: `Logitech Gamepad F310` (terlihat sebagai perangkat DirectInput)

Atau gunakan `navigator.getGamepads()` di console browser untuk melihat `gamepad.id`.

---

## 4. Command Flow (Input → ROV)

```
┌─────────────────────────────────────────────────────────────────────┐
│  INPUT (Keyboard / Gamepad)                                        │
│  app.js / joystick-state.js                                        │
│  ├─ Keyboard → KEY_AXIS → sendCmd(axis, val)                      │
│  └─ Gamepad → pollGamepad → executeJoystickAction → sendCmd()     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ WebSocket (ws://localhost:8080)
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SERVER (Node.js / server.js)                                      │
│  ├─ Terima command via WebSocket                                   │
│  ├─ Clamp axis ke −1000..1000                                     │
│  ├─ Format UDP JSON: { name, value, t }                           │
│  └─ Kirim via UDP ke RPI                                           │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ UDP JSON :14550
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  RASPBERRY PI (rov_agent.py)                                       │
│  ├─ Terima UDP di port 14550                                      │
│  ├─ Decode JSON → command name + value                             │
│  ├─ Clamp & validasi ulang (rov_axes.clamp_axis)                  │
│  ├─ Encode ke MAVLink MANUAL_CONTROL (pymavlink)                  │
│  └─ Kirim ke Pixhawk via serial (/dev/ttyACM0)                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Safety & Failsafe

| Skenario | Respons Otomatis |
|----------|-----------------|
| WebSocket putus | E-Stop dikunci (`estopLatched`), semua axis dinetralkan |
| Gamepad disconnect | Semua axis dinetralkan, thruster berhenti |
| Tombol `Space` ditekan | Failsafe: semua thruster netral, ROV disarm |
| Tidak ada axis baru > 0.5 s (Pi) | Pi kirim perintah netral, berhenti kirim (mencegah fail-safe timeout) |
| Mode Autonomous | Joystick dinonaktifkan secara otomatis |
| ARM ulang setelah E-Stop | Kunci E-Stop dilepas, joystick aktif kembali |

### Urutan Darurat

1. Tekan **STOP** (tombol header) atau **Spasi** → semua thruster netral seketika.
2. Jika tidak respons, tekan **ARM** untuk disarm.
3. Setelah aman, ARM ulang untuk mengaktifkan kembali kontrol.

---

## 6. Reference: UDP Command Structure

Perintah yang dikirim dari server ke RPI via UDP (JSON):

| `name` | `value` | Keterangan |
|--------|---------|------------|
| `arm` | `true`/`false` | ARM / DISARM |
| `light` | `true`/`false` | Lampu on/off |
| `stop` | `true` | Failsafe: netralkan semua thruster |
| `surge` | −1000..1000 | Maju/mundur |
| `sway` | −1000..1000 | Kiri/kanan |
| `yaw` | −1000..1000 | Rotasi |
| `heave` | −1000..1000 | Naik/turun |
| `pilot_mode` | `"manual"`/`"stabilize"`/`"depth_hold"` | Mode kontrol |
| `control_mode` | `"manual"`/`"autonomous"` | Manual vs Autonomous |
| `gripper` | `"open"`/`"close"` | Gripper |
| `mount_tilt` | `"up"`/`"down"`/`{dir,hold}`/`{dir:"stop"}` | Gimbal mount |
| `actuator1` | `"inc"`/`"dec"`/`{dir,hold}`/`{dir:"stop"}` | Aktuator |
| `light_level` | `"up"`/`"down"`/`{dir,hold}` | Level lampu |
| `gain` | `"inc"`/`"dec"`/`{dir,hold}` | Gain PID |
| `input_hold_set` | `true` | Set input hold |
| `set_surface` | `true` | Set depth = 0 |
| `snapshot` | `true` | Ambil foto |
| `record` | `true`/`false` | Mulai/hentikan rekam |
