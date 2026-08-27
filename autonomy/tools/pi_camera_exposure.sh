#!/usr/bin/env bash
# tools/pi_camera_exposure.sh — Uji exposure/gain manual kamera fisik (V4L2, di Pi).
#
# KENAPA INI ADA: cv2.CAP_PROP_EXPOSURE/GAIN di VisionPipeline (laptop) TIDAK
# berpengaruh apa-apa — kamera dibaca via cv2.VideoCapture(url) terhadap stream
# MJPEG dari mjpg-streamer (lihat README.md "## Kamera"), bukan device V4L2
# langsung. Properti exposure cv2 cuma bekerja utk device lokal, no-op utk
# stream jaringan yang sudah di-encode. Satu-satunya titik yang BISA mengatur
# exposure sungguhan adalah V4L2 driver DI PI, sebelum mjpg-streamer
# membacanya — makanya script ini HARUS dijalankan di Pi (via SSH), bukan di
# laptop, dan TAK BISA diuji dari sesi pengembangan biasa (butuh kamera fisik).
#
# KENAPA INI RELEVAN utk QR: foto trial nyata yang gagal decode (lihat
# tests/fixtures/real_hard_cases/) tampak washed-out/overexposed terhadap air
# terang di sekitar QR — mungkin auto-exposure menyesuaikan ke keseluruhan
# frame (didominasi air putih terang), bukan ke area QR yang lebih gelap,
# sehingga detail QR "terpotong" sebelum sempat jadi JPEG 8-bit. Ini BEDA
# kelas dari CLAHE/percentile-stretch/dsb di decode_qr() — itu semua bekerja
# SESUDAH detail hilang; script ini menguji apakah detailnya bisa DISELAMATKAN
# saat capture.
#
# PEMAKAIAN (SSH ke Pi dulu):
#   ssh pi@<ip-raspi>
#   bash tools/pi_camera_exposure.sh                 # lihat device & kontrol yang ada
#   bash tools/pi_camera_exposure.sh /dev/video0                     # device spesifik
#   bash tools/pi_camera_exposure.sh /dev/video0 set 100              # coba manual, nilai 100
#   bash tools/pi_camera_exposure.sh /dev/video0 auto                 # kembalikan ke auto
#
# CATATAN: mjpg-streamer mungkin masih pegang device saat kontrol diubah --
# perubahan V4L2 biasanya berlaku LANGSUNG ke stream berjalan, tapi kalau
# tak kelihatan efeknya, restart mjpg-streamer setelah set/auto.
#
# Butuh v4l-utils (`sudo apt install v4l-utils`) -- gagal-lunak, cuma pesan
# error kalau tak ada, tak merusak apa pun.
set -u

if ! command -v v4l2-ctl >/dev/null 2>&1; then
    echo "v4l2-ctl tidak ditemukan. Instal dulu: sudo apt install v4l-utils" >&2
    exit 1
fi

DEV="${1:-}"
ACTION="${2:-}"
VALUE="${3:-}"

if [ -z "$DEV" ]; then
    echo "=== Device kamera tersedia ==="
    v4l2-ctl --list-devices
    echo
    echo "Jalankan ulang dgn device spesifik, mis.:"
    echo "  bash $0 /dev/video0"
    exit 0
fi

if [ -z "$ACTION" ]; then
    echo "=== Kontrol yang didukung $DEV ==="
    v4l2-ctl -d "$DEV" --list-ctrls
    echo
    echo "Cari baris 'exposure'/'auto_exposure'/'gain' di atas -- nama & range PERSIS"
    echo "beda antar modul kamera (UVC lama vs baru), makanya tak di-hardcode di sini."
    echo
    echo "Coba manual:  bash $0 $DEV set <nilai>"
    echo "Kembali auto: bash $0 $DEV auto"
    exit 0
fi

# Nama kontrol auto-exposure beda antar driver ('exposure_auto' ioctl lama vs
# 'auto_exposure' baru) -- deteksi dari --list-ctrls, bukan ditebak.
AUTO_CTRL=$(v4l2-ctl -d "$DEV" --list-ctrls | grep -oE '^\s*(exposure_auto|auto_exposure)' | head -1 | tr -d ' ')
MANUAL_CTRL=$(v4l2-ctl -d "$DEV" --list-ctrls | grep -oE '^\s*(exposure_absolute|exposure_time_absolute)' | head -1 | tr -d ' ')

if [ -z "$AUTO_CTRL" ] || [ -z "$MANUAL_CTRL" ]; then
    echo "Tak ketemu kontrol exposure standar di $DEV -- jalankan tanpa ACTION" >&2
    echo "(bash $0 $DEV) utk lihat daftar kontrol mentah, cari namanya manual." >&2
    exit 1
fi

case "$ACTION" in
    auto)
        # Nilai '3' = aperture priority/auto (standar UVC); '1' pada sebagian driver.
        v4l2-ctl -d "$DEV" --set-ctrl="${AUTO_CTRL}=3" 2>/dev/null || \
        v4l2-ctl -d "$DEV" --set-ctrl="${AUTO_CTRL}=1"
        echo "[OK] $DEV -> auto-exposure aktif lagi ($AUTO_CTRL)"
        ;;
    set)
        if [ -z "$VALUE" ]; then
            echo "Butuh nilai: bash $0 $DEV set <nilai>" >&2
            exit 1
        fi
        # '1' = manual (standar UVC).
        v4l2-ctl -d "$DEV" --set-ctrl="${AUTO_CTRL}=1"
        v4l2-ctl -d "$DEV" --set-ctrl="${MANUAL_CTRL}=${VALUE}"
        echo "[OK] $DEV -> manual, $MANUAL_CTRL=$VALUE"
        echo "     Cek hasil di GUI Control/Camera (QR readout), atau ambil snapshot"
        echo "     dan coba: python -c \"import cv2; from vision.qr_detect import decode_qr; ...\""
        ;;
    *)
        echo "ACTION tak dikenal: $ACTION (pakai 'set <nilai>' atau 'auto')" >&2
        exit 1
        ;;
esac
