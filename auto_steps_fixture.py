"""Tabel AUTO_STEPS uji. Test membaca file ini, BUKAN tabel misi asli di
server/control_main.py, supaya angka misi boleh diubah tanpa merusak test."""


# ============================================================
# AUTONOMOUS - FULL COUNTER
# ============================================================

# Depth angka di kode diprioritaskan; None memakai DEPTH DASAR GUI.
depth_auto = 1.0  # Target kedalaman autonomous (meter).
AUTO_STEPS = [
    # duration, surge, sway, yaw, heave, gripper, depth
    (3.0, 400, 600, 0, 0, None, depth_auto), # maju
    (1.0, 0, 0, 90, 0, None, depth_auto), # putar kanan
    # contoh struktur command non-motion
    (6.0, 0, 0, 180, 0, None, depth_auto), # putar balik
    (10.0, 0, -800, 180, 0, None, depth_auto), # bergerak ke kiri (sway) , waktu (paling kiri) disesuaikan

    (2.0, 0, 0, 180, 0, 1580, depth_auto), # buka gripper
    (6.0, 250, 0, 180, 0, 1580, depth_auto), # maju ke hook
    (2.0, 150, 0, 180, 0, 1500, depth_auto), # hold gripper
    (4.0, 0, 0, 180, 0, 1350, depth_auto), # tutup gripper
    (4.0, -400, 0, 180, 0, 1350, 0.0), # mundur, depth 0.0 = naik ke permukaan
    (4.0, 400, 0, 180, 0, 1350, 0.0), # maju / last motion
    (4.0, 0, 0, 180, 0, 1350, 0.0), # finish

]
