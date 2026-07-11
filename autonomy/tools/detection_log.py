"""
tools/detection_log.py — Logger CSV deteksi QR untuk webcam test (Fase 3 tuning).

Merekam 1 baris per frame (terdeteksi/tidak + pose/servo) ke CSV, lalu mencetak
RINGKASAN detection-rate + rentang jarak/area saat ditutup. Dipakai bersama oleh
servo_webcam_test.py & pose_webcam_test.py untuk mengkuantifikasi isu "QR terdeteksi
hanya jarak dekat / sensitif cahaya" — data mentahnya bisa diplot (rate vs jarak).
"""
import csv
import time
from datetime import datetime

FIELDS = ["t_iso", "elapsed_s", "frame", "detected", "data",
          "x", "y", "z", "dist", "yaw",
          "cx", "cy", "area", "ex", "ey", "ea",
          "surge", "sway", "vert", "aligned"]


class DetectionCsvLogger:
    """Tulis CSV per-frame + akumulasi statistik detection-rate.

    Contoh:
        log = DetectionCsvLogger("run.csv")
        log.log(detected=True, data="...", z=0.4, dist=0.4, surge=10, aligned=False)
        ...
        log.close()   # cetak & kembalikan ringkasan
    """

    def __init__(self, path):
        self.path = path
        self._f = open(path, "w", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._f, fieldnames=FIELDS)
        self._w.writeheader()
        self._t0 = time.time()
        self.frames = 0
        self.detected = 0
        self._dists = []       # jarak (m) saat terdeteksi (PBVS)
        self._areas = []       # luas (px²) saat terdeteksi (IBVS)

    def log(self, detected, data="", x=None, y=None, z=None, dist=None, yaw=None,
            cx=None, cy=None, area=None, ex=None, ey=None, ea=None,
            surge=None, sway=None, vert=None, aligned=None):
        self.frames += 1
        if detected:
            self.detected += 1
            if dist is not None:
                self._dists.append(float(dist))
            if area is not None:
                self._areas.append(float(area))
        row = {k: "" for k in FIELDS}
        row.update(t_iso=datetime.now().isoformat(timespec="milliseconds"),
                   elapsed_s=round(time.time() - self._t0, 3),
                   frame=self.frames, detected=int(bool(detected)), data=data,
                   x=x, y=y, z=z, dist=dist, yaw=yaw,
                   cx=cx, cy=cy, area=area, ex=ex, ey=ey, ea=ea,
                   surge=surge, sway=sway, vert=vert,
                   aligned="" if aligned is None else int(bool(aligned)))
        # bersihkan None → string kosong (CSV rapi)
        self._w.writerow({k: ("" if v is None else v) for k, v in row.items()})
        return row

    def summary(self) -> dict:
        rate = (100.0 * self.detected / self.frames) if self.frames else 0.0
        s = {"frames": self.frames, "detected": self.detected,
             "rate_pct": round(rate, 1)}
        if self._dists:
            s["dist_min"] = round(min(self._dists), 3)
            s["dist_max"] = round(max(self._dists), 3)
        if self._areas:
            s["area_min"] = round(min(self._areas), 1)
            s["area_max"] = round(max(self._areas), 1)
        return s

    def close(self):
        s = self.summary()
        try:
            self._f.close()
        except Exception:
            pass
        print(f"[csv] {self.path}: {s['detected']}/{s['frames']} frame terdeteksi "
              f"({s['rate_pct']}%)"
              + (f", jarak {s.get('dist_min')}–{s.get('dist_max')} m" if 'dist_min' in s else "")
              + (f", area {s.get('area_min')}–{s.get('area_max')} px²" if 'area_min' in s else ""))
        return s
