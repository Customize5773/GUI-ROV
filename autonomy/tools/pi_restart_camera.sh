#!/usr/bin/env bash
# tools/pi_restart_camera.sh — Ubah resolusi stream kamera di Pi via ustreamer.
#
# Dipanggil dari rov_agent.py (command "camera_resolution" dari GUI Setup page),
# TAPI juga bisa dijalankan manual (SSH ke Pi) utk debug tanpa lewat GUI:
#   bash tools/pi_restart_camera.sh 0 1280x720   # CAM 1 / BOTTOM -> port 8081
#   bash tools/pi_restart_camera.sh 1 640x480    # CAM 2 / WALL   -> port 8080
#
# DIKONFIRMASI LANGSUNG DI PI LOMBA (bukan tebakan/dokumentasi generik) --
# software streaming BUKAN mjpg-streamer (nama umum di internet), tapi
# uStreamer (github.com/pikvm/ustreamer), dikelola systemd dgn Restart=always:
#   ustreamer-cam1.service -> port 8080, /dev/video0  (role WALL   = CAMERAS[1])
#   ustreamer-cam2.service -> port 8081, /dev/video4  (role BOTTOM = CAMERAS[0])
# uStreamer TIDAK punya API ganti resolusi saat jalan (-r cuma dibaca saat
# start) -- satu-satunya cara adalah edit ExecStart lalu restart service.
# Restart=always berarti bunuh proses manual (pkill) SIA-SIA: systemd
# langsung menghidupkannya lagi dgn config lama dlm hitungan detik.
#
# Pemetaan camera_id (dari GUI, index CONFIG.CAMERAS) -> service SENGAJA
# terbalik dari penomoran service (cam_id 0 -> cam2.service) -- ikuti tabel
# di atas, jangan "diperbaiki" jadi 0->cam1 tanpa cek ulang port kamera BOTTOM
# yang sesungguhnya aktif.
set -u

CAMERA="${1:-}"
RESOLUTION="${2:-}"

if [ -z "$CAMERA" ] || [ -z "$RESOLUTION" ]; then
    echo "Pemakaian: bash $0 <camera_id 0|1> <resolution WxH>" >&2
    echo "  contoh: bash $0 0 1280x720" >&2
    exit 1
fi

if ! [[ "$RESOLUTION" =~ ^[0-9]+x[0-9]+$ ]]; then
    echo "FAIL:bad_resolution:$RESOLUTION" >&2
    exit 1
fi

case "$CAMERA" in
    0) SERVICE="ustreamer-cam2.service" ;;  # BOTTOM (QR docking), port 8081
    1) SERVICE="ustreamer-cam1.service" ;;  # WALL (hook detection), port 8080
    *) echo "FAIL:bad_camera_id:$CAMERA" >&2; exit 1 ;;
esac

UNIT_FILE="/etc/systemd/system/${SERVICE}"
if [ ! -f "$UNIT_FILE" ]; then
    echo "FAIL:unit_not_found:$UNIT_FILE" >&2
    exit 1
fi

# Ganti nilai -r di ExecStart (satu-satunya baris "-r WxH" di file ini).
sudo -n sed -i -E "s/-r [0-9]+x[0-9]+/-r ${RESOLUTION}/" "$UNIT_FILE" || {
    echo "FAIL:sed_failed:$UNIT_FILE" >&2
    exit 1
}
sudo -n systemctl daemon-reload
sudo -n systemctl restart "$SERVICE"

sleep 2
if systemctl is-active --quiet "$SERVICE"; then
    echo "OK:camera${CAMERA}:${RESOLUTION}"
else
    echo "FAIL:camera${CAMERA}:service_not_active (lihat: journalctl -u ${SERVICE} -n 30)" >&2
    exit 1
fi
