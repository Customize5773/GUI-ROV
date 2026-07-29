"""Filter attitude (roll/pitch/yaw) dari data mentah ATTITUDE Pixhawk.

Dipisah dari rov_agent.py supaya logika filter bisa di-unit-test tanpa
bergantung pada pymavlink, socket, atau hardware (lihat rov_axes.py untuk
pola yang sama).

Kenapa complementary filter + EMA?
    ATTITUDE membawa dua sumber info: sudut absolut (roll/pitch/yaw, akurat
    jangka panjang tapi berisik) dan kecepatan sudut gyro (rollspeed/
    pitchspeed/yawspeed, halus jangka pendek tapi drift kalau diintegralkan
    sendirian). Complementary filter menggabungkan keduanya:

        angle = alpha * (angle_prev + rate * dt) + (1 - alpha) * angle_raw

    alpha mendekati 1 -> lebih percaya gyro (halus, tapi bisa drift kalau
    terlalu besar). alpha mendekati 0 -> lebih percaya sudut mentah (akurat,
    tapi berisik). Lapisan EMA di atasnya meredam noise frekuensi tinggi yang
    tersisa sebelum dikirim ke telemetry.

Konvensi:
    Input/output method update() dalam derajat (sesuai state dict di
    rov_agent.py). Perhitungan internal pakai radian.
"""

import math

# Complementary filter: rasio kepercayaan gyro vs sudut mentah.
# alpha=1 -> murni integrasi gyro (drift), alpha=0 -> murni sudut mentah (berisik).
# Rekomendasi: 0.95-0.99 untuk attitude ROV.
COMPLEMENTARY_ALPHA = 0.98

# Low-pass EMA di atas hasil complementary filter, per axis.
# 0.0 = tanpa smoothing, mendekati 1.0 = sangat lembam (respons lambat).
EMA_ALPHA_ROLL = 0.3
EMA_ALPHA_PITCH = 0.3
EMA_ALPHA_YAW = 0.5

# Batas dt (detik) supaya pesan yang datang dobel/telat/burst tidak
# menghasilkan lonjakan atau integrasi gyro yang liar.
DT_MIN = 0.001
DT_MAX = 0.5


def _wrap_pi(angle_rad):
    """Normalisasi sudut (radian) ke rentang [-pi, pi]."""
    return (angle_rad + math.pi) % (2 * math.pi) - math.pi


class AttitudeFilter:
    """Complementary filter + EMA untuk roll, pitch, yaw.

    update() dipanggil sekali per pesan ATTITUDE. Panggilan pertama hanya
    menyemai (seed) state dari nilai mentah tanpa ekstrapolasi.
    """

    def __init__(
        self,
        complementary_alpha=COMPLEMENTARY_ALPHA,
        ema_alpha_roll=EMA_ALPHA_ROLL,
        ema_alpha_pitch=EMA_ALPHA_PITCH,
        ema_alpha_yaw=EMA_ALPHA_YAW,
        dt_min=DT_MIN,
        dt_max=DT_MAX,
    ):
        self.alpha = complementary_alpha
        self.ema_alpha_roll = ema_alpha_roll
        self.ema_alpha_pitch = ema_alpha_pitch
        self.ema_alpha_yaw = ema_alpha_yaw
        self.dt_min = dt_min
        self.dt_max = dt_max

        self._initialized = False
        # State complementary filter (radian).
        self._comp_roll = 0.0
        self._comp_pitch = 0.0
        self._comp_yaw = 0.0
        # State EMA (radian).
        self._ema_roll = 0.0
        self._ema_pitch = 0.0
        self._ema_yaw = 0.0

    def update(
        self,
        roll_deg,
        pitch_deg,
        yaw_deg,
        rollspeed_deg_s,
        pitchspeed_deg_s,
        yawspeed_deg_s,
        dt,
    ):
        """Filter satu sample ATTITUDE. Semua sudut/rate dalam derajat.

        Return (roll_deg, pitch_deg, yaw_deg) hasil filter. yaw_deg
        dinormalisasi ke [0, 360).
        """
        roll_raw = math.radians(roll_deg)
        pitch_raw = math.radians(pitch_deg)
        yaw_raw = math.radians(yaw_deg)

        if not self._initialized:
            self._comp_roll = self._ema_roll = roll_raw
            self._comp_pitch = self._ema_pitch = pitch_raw
            self._comp_yaw = self._ema_yaw = yaw_raw
            self._initialized = True
            return (
                roll_deg,
                pitch_deg,
                math.degrees(yaw_raw) % 360.0,
            )

        dt = max(self.dt_min, min(self.dt_max, dt))

        rollspeed = math.radians(rollspeed_deg_s)
        pitchspeed = math.radians(pitchspeed_deg_s)
        yawspeed = math.radians(yawspeed_deg_s)

        # Roll/pitch: tidak wrap-around dalam operasi normal ROV.
        self._comp_roll = self.alpha * (self._comp_roll + rollspeed * dt) + (
            1 - self.alpha
        ) * roll_raw
        self._comp_pitch = self.alpha * (self._comp_pitch + pitchspeed * dt) + (
            1 - self.alpha
        ) * pitch_raw

        # Yaw: wrap selisih raw-vs-integrasi ke [-pi, pi] dulu supaya
        # transisi 359 -> 1 derajat tidak melompat -358 derajat.
        yaw_integrated = self._comp_yaw + yawspeed * dt
        yaw_error = _wrap_pi(yaw_raw - yaw_integrated)
        self._comp_yaw = _wrap_pi(
            yaw_integrated + (1 - self.alpha) * yaw_error
        )

        # EMA di atas hasil complementary filter.
        self._ema_roll = (
            self.ema_alpha_roll * self._ema_roll
            + (1 - self.ema_alpha_roll) * self._comp_roll
        )
        self._ema_pitch = (
            self.ema_alpha_pitch * self._ema_pitch
            + (1 - self.ema_alpha_pitch) * self._comp_pitch
        )
        ema_yaw_error = _wrap_pi(self._comp_yaw - self._ema_yaw)
        self._ema_yaw = _wrap_pi(
            self._ema_yaw + (1 - self.ema_alpha_yaw) * ema_yaw_error
        )

        return (
            math.degrees(self._ema_roll),
            math.degrees(self._ema_pitch),
            math.degrees(self._ema_yaw) % 360.0,
        )
