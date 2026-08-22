"""rov_drift.py — Konversi pixel-flow kamera BOTTOM -> kecepatan drift (m/s).

Kenapa modul terpisah, murni tanpa cv2/pymavlink
    Sama alasan rov_modes.py/rov_heading.py: satu-satunya bagian yang punya
    aturan (rumus proyeksi lubang-jarum, penjagaan altitude/focal tak valid)
    dipisah dari cv2 (autonomy/vision/optical_flow.py) dan MAVLink
    (rov_agent.py) supaya bisa diuji dengan angka biasa, tanpa kamera atau FC.

Rumus
    Kamera lubang-jarum menghadap ke bawah: perpindahan `p` piksel pada jarak
    fokus `f` piksel, dilihat dari ketinggian `h` meter di atas dasar,
    berpadanan dengan perpindahan dunia nyata `p * h / f` meter — kesebangunan
    segitiga yang sama dengan estimasi jarak QR/hook di autonomy/vision/.
    Kecepatan tinggal membagi dengan selang waktu antar-frame `dt`:

        v_dunia = (px / dt) * (altitude / focal_px)

Yang TIDAK dijamin modul ini
    Arah dx/dy adalah arah PIKSEL KAMERA, belum tentu selaras dengan
    surge/sway ROV — tergantung orientasi kamera terpasang, perlu
    dicek/dikalibrasi di kolam, bukan diasumsikan di sini. `altitude_m`
    dipanggil dengan `pool_depth - depth` sebagai pengganti rangefinder
    sungguhan (lihat pemanggil di rov_agent.py) — kalau dasar kolam tidak
    rata atau pool_depth salah, hasilnya ikut meleset.

    integrate_accel() (penambal celah IMU, lihat di bawah) MENGASUMSIKAN
    accel body-frame (x=depan, y=kanan) sudah SATU bingkai dengan (vx, vy)
    dari flow_to_velocity() — sama-sama asumsi identitas kamera-vs-body yang
    belum divalidasi. Kalau kalibrasi kolam nanti menemukan kamera terpasang
    berputar/tercermin terhadap body, KEDUA fungsi ini butuh rotasi/tukar
    sumbu yang sama, bukan cuma salah satu.
"""


def flow_to_velocity(dx_px, dy_px, dt, altitude_m, focal_px):
    """Pixel flow (dx, dy) selama `dt` detik -> (vx, vy) m/s dunia nyata.

    Mengembalikan (0.0, 0.0) untuk masukan yang tak bisa dipercaya (dt,
    altitude, atau focal <= 0 atau None) alih-alih melempar — pemanggil
    (thread drift di rov_agent.py) harus tetap jalan walau kamera/altitude
    sedang tak valid, sama filosofi dengan depth_bias_engaged() di
    rov_modes.py.
    """
    if dt is None or dt <= 0:
        return (0.0, 0.0)
    if altitude_m is None or altitude_m <= 0:
        return (0.0, 0.0)
    if focal_px is None or focal_px <= 0:
        return (0.0, 0.0)

    scale = altitude_m / focal_px
    vx = (dx_px / dt) * scale
    vy = (dy_px / dt) * scale
    return (vx, vy)


# Celah flow di atas ini (detik) -> berhenti percaya integrasi IMU, jatuhkan
# ke 0 alih-alih terus berkembang tanpa batas. Snap-back ke flow (lihat
# drift_estimator_thread di rov_agent.py) biasanya jauh lebih cepat dari ini —
# batas ini murni jaring pengaman kalau flow benar-benar HILANG lama (kamera
# freeze, bukan cuma satu-dua frame buram).
IMU_GAP_FILL_MAX_S = 1.0


def integrate_accel(ax, ay, dt, prev_vx, prev_vy):
    """Integrasi TUNGGAL akselerasi body-frame (m/s^2, x=depan/surge,
    y=kanan/sway — konvensi FRD Pixhawk/MAVLink) -> kecepatan (m/s).

    HANYA untuk menambal celah SINGKAT saat optical flow kosong (lihat
    drift_estimator_thread) — accelerometer MEMS hanyut cepat kalau
    diintegrasi tanpa koreksi eksternal berkelanjutan (tidak ada suku
    penahan/decay di sini). JANGAN dipakai untuk dead-reckoning
    berkepanjangan; IMU_GAP_FILL_MAX_S di atas adalah pagar keras untuk itu,
    bukan penanda "aman sampai situ".

    ax/ay diasumsikan SUDAH dalam bingkai yang sama dengan (vx, vy) yang mau
    ditambal — lihat catatan bingkai kamera-vs-body di docstring modul: ini
    juga baru asumsi identitas sampai dikalibrasi di kolam.
    """
    if dt is None or dt <= 0:
        return (prev_vx, prev_vy)
    return (prev_vx + ax * dt, prev_vy + ay * dt)
