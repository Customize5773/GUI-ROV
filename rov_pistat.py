"""Status hardware Raspberry Pi (beban CPU + suhu SoC) dari /proc dan /sys.

Dipakai rov_agent.py untuk mengisi readout PI di strip telemetri. Pi menurunkan
clock sendiri saat SoC panas; tanpa angka ini, throttling terlihat oleh operator
sebagai "ROV mendadak lag" tanpa penyebab yang kelihatan di GUI mana pun.

Sengaja TANPA dependency (bukan psutil): requirements.txt menyatakan dirinya
"Sengaja MINIMAL", dan /proc + /sys sudah ada di setiap Pi OS.

Semua fungsi mengembalikan None kalau sumbernya tidak bisa dibaca — BUKAN 0.0.
Nol adalah pembacaan yang sah (CPU idle, air 0 °C), jadi memakainya sebagai
penanda "tidak ada data" persis bug yang baru saja diperbaiki di field
temp/voltage rov_agent.py. GUI menggambar "—" untuk null.

Tidak pernah mengembalikan NaN/Infinity: send_to_gui() memakai
json.dumps(allow_nan=False), jadi satu nilai non-finite mematikan SELURUH paket
telemetri, bukan cuma field ini.

    python3 -m unittest test_rov_pistat -v
"""

import os

THERMAL_ROOT = "/sys/class/thermal"

# Nama zone termal SoC di Raspberry Pi OS. Sengaja dicocokkan per nama, BUKAN
# pakai thermal_zone0: di laptop dev zone0 adalah "INT3400 Thermal" (chipset)
# yang melaporkan 20 °C konstan. Fallback ke zone pertama berarti mengirim angka
# bohong yang tak bisa dibedakan operator dari suhu asli.
_SOC_ZONE_TYPES = ("cpu-thermal", "cpu_thermal", "soc_thermal")


def _read_text(path):
    """Isi file sysfs/procfs sebagai str, atau None kalau tidak terbaca."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return None


def read_soc_temp():
    """Suhu SoC dalam °C, atau None kalau zone CPU tidak ada di mesin ini.

    None di laptop dev adalah hasil yang BENAR — di sana tidak ada cpu-thermal.
    """
    try:
        zones = sorted(os.listdir(THERMAL_ROOT))
    except OSError:
        return None

    for zone in zones:
        if not zone.startswith("thermal_zone"):
            continue
        ztype = _read_text(os.path.join(THERMAL_ROOT, zone, "type"))
        if ztype is None or ztype.lower() not in _SOC_ZONE_TYPES:
            continue

        raw = _read_text(os.path.join(THERMAL_ROOT, zone, "temp"))
        if raw is None:
            return None
        try:
            milli = int(raw)
        except ValueError:
            return None
        return round(milli / 1000.0, 1)

    return None


def parse_cpu_snapshot(stat_line):
    """Baris "cpu ..." /proc/stat -> (total, idle) dalam jiffies, atau None.

    idle = idle + iowait: keduanya waktu CPU tidak mengerjakan apa pun, dan
    menghitung iowait sebagai "sibuk" membuat Pi terlihat 100% saat cuma
    menunggu kartu SD.
    """
    if not stat_line:
        return None
    # Ambil baris pertama saja: /proc/stat lanjut dengan cpu0/cpu1/... dan
    # intr/ctxt, jadi split() atas seluruh isi file ikut menyeret token "cpu0".
    parts = stat_line.splitlines()[0].split()
    # "cpu" + minimal user/nice/system/idle/iowait
    if len(parts) < 6 or parts[0] != "cpu":
        return None
    try:
        values = [int(v) for v in parts[1:]]
    except ValueError:
        return None

    total = sum(values)
    idle = values[3] + values[4]   # idle + iowait
    return (total, idle)


def read_cpu_percent(prev):
    """(persen, snapshot_baru) dari delta dua sampel /proc/stat.

    Stateless: snapshot sebelumnya masuk sebagai argumen, yang baru dikembalikan
    — tak ada global, dan logikanya bisa diuji tanpa menunggu waktu nyata.

    /proc/stat berisi counter kumulatif sejak boot, jadi satu sampel saja tidak
    berarti apa-apa; panggilan pertama (prev=None) selalu mengembalikan None.
    Itu sebabnya bukan /proc/loadavg: loadavg panjang antrean, bukan persen.
    """
    snapshot = parse_cpu_snapshot(_read_text("/proc/stat"))
    if snapshot is None:
        return (None, prev)
    if prev is None:
        return (None, snapshot)

    d_total = snapshot[0] - prev[0]
    d_idle = snapshot[1] - prev[1]

    # d_total <= 0: dua sampel identik (dipanggil terlalu cepat) atau counter
    # mundur setelah reboot. Bukan error, cuma belum ada yang bisa dihitung —
    # dan pembagiannya akan ZeroDivisionError.
    if d_total <= 0:
        return (None, snapshot)

    pct = 100.0 * (d_total - d_idle) / d_total
    # Clamp: d_idle bisa sesaat melebihi d_total saat counter per-CPU di-hotplug.
    pct = max(0.0, min(100.0, pct))
    return (round(pct, 1), snapshot)


if __name__ == "__main__":
    import time

    print("suhu SoC:", read_soc_temp(), "°C  (None = bukan Pi / tak ada cpu-thermal)")
    pct, snap = read_cpu_percent(None)
    print("CPU sampel pertama:", pct, "(harus None)")
    time.sleep(1.0)
    pct, snap = read_cpu_percent(snap)
    print("CPU sampel kedua:", pct, "%")
