# Bug & Issue Report — GUI ROV

Daftar temuan masalah/bug dari review update terbaru ("save konfigurasi joystick" & "Thruster reverse from GUI done").

---

## [CRITICAL] Fail-safe timeout hilang pada joystick_sender()

**File:** `rov_agent.py`

Sebelumnya, `manual_control_sender()` thread terpisah mengirim MANUAL_CONTROL 15 Hz dengan timeout 0.5s — jika tidak ada axis baru selama 0.5s, mengirim satu perintah netral lalu berhenti sampai ada perintah manual lagi. Sekarang `joystick_sender()` mengirim 20 Hz terus-menerus tanpa timeout. Jika joystick dicabut atau GUI crash, Pi akan terus mengirim nilai terakhir ke Pixhawk, berpotensi membuat ROV bergerak tak terkendali.

**Rekomendasi:** Pulihkan fail-safe timeout atau tambahkan mekanisme deteksi idle di `joystick_sender()`.

---

## [CRITICAL] command_listener thread diblokir 3 detik saat ARM

**File:** `rov_agent.py:108`

`master.recv_match(type="COMMAND_ACK", blocking=True, timeout=3)` memblokir thread `command_listener` selama hingga 3 detik saat ARM. Selama periode ini, command lain (termasuk STOP/emergency) yang masuk via UDP tidak diproses.

**Rekomendasi:** Pindah ACK handling ke non-blocking atau gunakan timeout lebih pendek dengan retry.

---

## [BUG] Tidak ada validasi range di sisi Pi untuk nilai axis manual

**File:** `rov_agent.py:158-159`

Server `server.js` sudah clamp axis ke range valid (-1000..1000 / 0..1000), tapi `rov_agent.py` mengirim nilai mentah ke `manual_control_send()` tanpa validasi. `manual_control.py` yang sebelumnya handle clamp sudah tidak dipakai.

**Rekomendasi:** Tambahkan validasi range di sisi Pi sebelum mengirim ke `manual_control_send()`.

---

## [BUG] Debug logs belum dihapus dari production code

**File:**
- `rov_agent.py:81` — `print("RAW UDP:", msg)`
- `rov_agent.py:89` — `print("FULL CMD =", msg)`
- `rov_agent.py:139` — `print("[DEBUG] Motors received:", motors)`
- `public/js/app.js` — `console.log("[APP]", next)` di `pollGamepad()`
- `public/js/joystick-state.js:236` — `console.log("[MAPPED]", joystickState.mapped)`
- `server/server.js:483` — `console.log("[SERVER BEFORE UDP]", msg.name, msg.value)`

**Rekomendasi:** Hapus atau ganti dengan sistem logging bertingkat (debug/info/warn/error) yang bisa dimatikan di production.

---

## [BUG] Komentar typo di server/server.js

**File:** `server/server.js:454`

```
// ================= COMMAND KE ROV ================f=
```

Huruf `f=` di akhir komentar adalah typo.

---

## [BUG] manual_control.py sekarang dead code

**File:** `manual_control.py`

File dibuat di commit "save konfigurasi joystick" tapi import-nya dihapus di commit "Thruster reverse from GUI done". File masih ada di repo tapi tidak dipakai oleh `rov_agent.py` maupun modul lain.

**Rekomendasi:** Hapus file atau pertahankan sebagai utility terpisah jika nanti akan dipakai kembali.

---

## [BUG] Indentation tidak konsisten di public/js/app.js

**File:** `public/js/app.js`

Setelah perubahan, beberapa blok kode mencampur 2-space dan 4-space indentation (terutama di `setAxis` event listener, `getMappedJoystickAxes()`, dan `pollGamepad()`).

**Rekomendasi:** Normalisasi indentation ke format yang konsisten (2-space atau 4-space).

---

## [BUG] clampAxis() server vs tidak ada clamping di Pi

**File:** `server/server.js:36-55`, `rov_agent.py:158-159`

`clampAxis()` di server memetakan `heave` ke 0..1000 dan `surge/sway/yaw` ke -1000..1000. Tapi `rov_agent.py` tidak melakukan clamp sebelum mengirim ke `manual_control_send()`. Jika ada koneksi langsung atau modifikasi di tengah jalan, Pi tidak ada proteksi range.

---

## [BUG] shiftButton di joystick-profile.json tidak sesuai default

**File:** `server/config/joystick-profile.json:3`

Nilai `shiftButton` saat ini adalah `0`, sedangkan `defaultJoystickConfig()` di `server.js` menetapkan `shiftButton: 5`. Perubahan ini bisa tidak disengaja dan mempengaruhi perilaku shift layer di GUI.

---

## [BUG] axisPreviewValue() vs readAssignedAxis() — inkonsistensi reverse mapping

**File:** `public/js/pages/joystick.js:123-135`, `public/js/joystick-state.js:89-113`

Kedua fungsi menangani reverse (min > max) secara berbeda:
- `joystick.js` `axisPreviewValue()`: flip dulu (`v *= -1`), lalu map ke output range
- `joystick-state.js` `readAssignedAxis()`: flip dulu, lalu map ke `[low, high]`

Perhitungan matematisnya berbeda untuk kasus `min=1000, max=0`:
- `joystick.js`: `1000 + ((v+1)/2)*(0-1000)` → 1000 saat v=-1, 500 saat v=0, 0 saat v=1
- `joystick-state.js`: `0 + ((v+1)/2)*(1000-0)` → 0 saat v=-1, 500 saat v=0, 1000 saat v=1 (lalu dibalik)

Hasilnya bisa berbeda untuk nilai tertentu. Perlu diverifikasi konsistensinya.

---

## [PERFORMANCE] Polling gamepad tetap berjalan setiap frame meski tidak ada perubahan

**File:** `public/js/app.js`

`pollGamepad()` dipanggil setiap `requestAnimationFrame` (~60 Hz), meski axis tidak berubah. `GP_SEND_INTERVAL` throttling mengurangi pengiriman ke server ke 15 Hz, tapi loop polling tetap berjalan penuh.

**Rekomendasi:** Pertimbangkan `requestIdleCallback` atau kurangi polling rate saat tidak ada perubahan state.

---

## [BUG] neutralizeGamepadAxes() — fungsi tidak diverifikasi

**File:** `public/js/app.js`

`neutralizeGamepadAxes()` dipanggil di `btnStop.onclick` saat E-Stop aktif, tapi fungsi ini tidak terlihat di diff dan perlu diverifikasi implementasinya. Jika tidak ada atau tidak mengirim nilai netral ke server, axis tidak akan dinolkan saat E-Stop.

**Rekomendasi:** Verifikasi implementasi `neutralizeGamepadAxes()` dan pastikan mengirim nilai 0 untuk semua axis ke server.

---

## [BUG] estopLatched logic — E-Stop tidak mereset jika GUI disconnect

**File:** `public/js/app.js`

`estopLatched` di-set `true` saat STOP ditekan dan `false` saat ARM ditekan. Tapi jika GUI disconnect/reconnect, `estopLatched` tidak direset — state bisa tersimpan tidak konsisten antara GUI dan server.

**Rekomendasi:** Reset `estopLatched` saat koneksi WS ditutup/dibuka, atau sinkronkan state via WS.
