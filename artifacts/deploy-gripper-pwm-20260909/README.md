# Deploy PWM gripper — 2026-09-09

Pi diperbarui dengan patch sempit dari file remote aktif: handler gripper_pwm
dan GripperController.set_pwm. Perubahan lain pada Pi dipertahankan.
Backup: /home/hydroships/rov-agent/backups/gripper-pwm-20260909/.

Hash terpasang, cocok dengan artefak lokal:
- rov_agent.py: fc978b30afe4baa16b4b914f29a024ad26ff627e2ec037774db3895d41d055e8
- gripper_controller.py: 8327453ff63d046e071108f92cfc56b137cbd5fa66697ad6588341fd08aa47ae

rov-agent Pi dan gui-rov-counter laptop direstart; keduanya active, NRestarts=0.
Laptop kini memuat depth universal, yaw derajat, reload AUTO_STEPS saat mulai,
dan pengiriman gripper_pwm. Refresh browser diperlukan untuk JS terbaru.
72 tes lokal lulus; sintaks staging Pi valid.

Telemetri akhir: armed=false, mode=ALT_HOLD, control_mode=manual,
T1–T6 PWM semuanya 1500. Pi 84.2 C, throttled=0xe0008.
Tidak ada uji servo/gerak/autonomous. Telemetri SERVO7 tidak tersedia;
hash dan layanan aktif tidak membuktikan output PWM atau gerakan fisik.
