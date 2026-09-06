"""
tools/run_log.py — Logger JSONL satu-file per RUN misi autonomous.

fsm/mission5.py tadinya tidak menulis apa pun yang terstruktur: transisi state,
skor, dan telemetri docking cuma hidup di memori lalu hilang saat proses selesai.
Akibatnya tiap trial di kolam tak bisa dianalisis setelahnya. Modul ini merekam
run ke satu file .jsonl (1 event = 1 baris JSON), yang lalu dibaca
tools/analyze_run.py dan panel "Run terakhir" di GUI.

Kenapa JSONL bukan CSV (beda dgn detection_log.py): event di sini heterogen —
`transition` punya from/to, `sample` punya depth/offset, `end` punya skor —
sehingga satu header CSV tetap tak muat semuanya tanpa kolom kosong di mana-mana.

Jenis event:
    config     — sekali di awal: file config yang dipakai + konstanta hasil override
    transition — tiap perpindahan state FSM
    sample     — cuplikan periodik (~2 Hz) depth/heading/offset docking/QR
    reject     — alasan gate vision menolak deteksi, SAAT BERUBAH saja (edge)
    end        — sekali di akhir: skor, state akhir, durasi, pemakaian fallback

Pemakaian:
    rl = RunLogger("autonomy/logs/run_20260817_101500.jsonl")
    rl.event("transition", frm="M5_REDIVE", to="M5_DOCK")
    rl.close(...)
"""
import json
import os
import time
from datetime import datetime


class RunLogger:
    """Tulis event run ke JSONL. Tahan-gagal: kegagalan I/O tak boleh menjatuhkan misi."""

    def __init__(self, path):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        self._f = open(path, "w", encoding="utf-8")
        self._t0 = time.time()
        self.n = 0

    def event(self, kind, **fields):
        """Tulis satu event. Field None dibuang agar file tetap ringkas."""
        rec = {
            "t": round(time.time() - self._t0, 3),
            "t_iso": datetime.now().isoformat(timespec="milliseconds"),
            "kind": kind,
        }
        rec.update({k: v for k, v in fields.items() if v is not None})
        try:
            self._f.write(json.dumps(rec, default=str) + "\n")
            self._f.flush()   # trial di kolam bisa berakhir dgn Ctrl+C/kabel dicabut
        except Exception:
            return None       # log rusak jangan sampai membatalkan misi
        self.n += 1
        return rec

    def close(self, **end_fields):
        """Tulis event `end` lalu tutup file."""
        self.event("end", durasi_s=round(time.time() - self._t0, 3), **end_fields)
        try:
            self._f.close()
        except Exception:
            pass
        print(f"[runlog] {self.path}: {self.n} event")
        return self.path


def read_run(path) -> list:
    """Baca file JSONL → list event. Baris rusak dilewati (run bisa terpotong
    saat trial dihentikan paksa — file setengah jadi tetap harus bisa dianalisis)."""
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _selfcheck():
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "sub", "run.jsonl")
    rl = RunLogger(p)
    rl.event("config", files=["a.yaml"], overrides={"HOOK_DEPTH": 0.415})
    rl.event("transition", frm="IDLE", to="M5_DOCK")
    rl.event("sample", depth=0.4, offset_x=None, qr_data="A")   # None dibuang
    rl.close(state_akhir="DONE", skor={"m5": 40, "total": 40})

    ev = read_run(p)
    assert [e["kind"] for e in ev] == ["config", "transition", "sample", "end"], ev
    assert ev[1]["frm"] == "IDLE" and ev[1]["to"] == "M5_DOCK"
    assert "offset_x" not in ev[2] and ev[2]["qr_data"] == "A"
    assert ev[3]["skor"]["total"] == 40 and "durasi_s" in ev[3]
    assert all(e["t"] >= 0 for e in ev)

    # baris rusak di tengah tak boleh menggagalkan pembacaan
    with open(p, "a", encoding="utf-8") as f:
        f.write("{rusak\n")
        f.write(json.dumps({"t": 9.0, "kind": "sample"}) + "\n")
    assert len(read_run(p)) == 5
    print("run_log selfcheck OK")


if __name__ == "__main__":
    _selfcheck()
