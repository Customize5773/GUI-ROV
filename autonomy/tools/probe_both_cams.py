#!/usr/bin/env python3
"""probe_both_cams.py — ukur KEDUA kamera SEKALIGUS (kondisi GUI sesungguhnya).

Kenapa ada, padahal sudah ada probe_stream.py: probe_stream mengukur SATU
stream, dan satu stream saja SELALU terlihat sehat. Masalah 6 Sep 2026 hanya
muncul saat keduanya jalan bersamaan — 1080p30 = ~45 Mbps per kamera, dua-duanya
melebihi tether 100 Mbps, satu stream kelaparan sampai timeout dan ping ke Pi
kehilangan 20% paket. probe_stream.py juga pakai modul `resource` (POSIX), jadi
tidak jalan di laptop Windows; ini murni stdlib.

Pemakaian:
    python autonomy/tools/probe_both_cams.py
    python autonomy/tools/probe_both_cams.py --seconds 15 --min-fps 12
"""
import argparse
import statistics
import sys
import threading
import time
import urllib.request

# PENTING -- BACA SEBELUM PERCAYA ANGKANYA:
# Default di bawah menembak Pi LANGSUNG, jadi probe ini menjadi KONSUMEN
# TAMBAHAN di samping GUI dan kedua vision worker. Di tether yang sudah padat,
# konsumen ke-4 itu sendiri bisa kelaparan dan melaporkan fps rendah PALSU
# (terukur 6 Sep 2026: probe langsung bilang 2,9 fps sementara jalur GUI yang
# sesungguhnya dapat 14,6 fps pada saat yang sama).
#
# Untuk mengukur APA YANG BENAR-BENAR DILIHAT GUI, arahkan ke proxy /cam
# server -- di sana banyak penonton BERBAGI satu koneksi upstream
# (camStreamKey() di server/server.js), persis seperti browser:
#   python autonomy/tools/probe_both_cams.py #     --bottom "http://localhost:8080/cam?url=http%3A%2F%2F192.168.2.2%3A8081%2Fstream" #     --wall   "http://localhost:8080/cam?url=http%3A%2F%2F192.168.2.2%3A8080%2Fstream"
# Pakai URL Pi langsung hanya saat GUI MATI, utk menguji kamera secara terpisah.

BOTTOM = "http://192.168.2.2:8081/stream"
WALL = "http://192.168.2.2:8080/stream"


def probe(url, seconds, out):
    """Baca stream multipart MJPEG, catat batas tiap frame. Hanya MEMBACA."""
    try:
        t_open = time.monotonic()
        r = urllib.request.urlopen(url, timeout=8)
        open_s = time.monotonic() - t_open
        boundary = r.headers.get("Content-Type", "").split("boundary=")[-1].strip('"')
        if not boundary:
            out[url] = {"error": "bukan multipart (boundary kosong)"}
            return
        delim = ("--" + boundary).encode()
        buf, stamps, sizes = b"", [], []
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            chunk = r.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                # cari dari 1: delimiter di posisi 0 adalah milik frame ini
                i = buf.find(delim, 1)
                if i == -1:
                    break
                stamps.append(time.monotonic())
                sizes.append(i)
                buf = buf[i:]
        r.close()
        if len(stamps) < 3:
            out[url] = {"error": f"cuma {len(stamps)} frame dalam {seconds}s"}
            return
        dur = stamps[-1] - stamps[0]
        gaps = sorted(stamps[i] - stamps[i - 1] for i in range(1, len(stamps)))
        out[url] = {
            "open_s": open_s, "frames": len(stamps), "fps": (len(stamps) - 1) / dur,
            "p50_ms": 1000 * statistics.median(gaps),
            "p95_ms": 1000 * gaps[int(0.95 * (len(gaps) - 1))],
            "max_ms": 1000 * gaps[-1],
            "kb": statistics.median(sizes) / 1024,
            "mbps": 8 * sum(sizes) / dur / 1e6,
        }
    except Exception as e:                      # noqa: BLE001 - laporkan apa pun
        out[url] = {"error": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser(description="Ukur kedua kamera bersamaan (pasif)")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--min-fps", type=float, default=10.0,
                    help="fps minimum yang dianggap lulus (default 10)")
    ap.add_argument("--bottom", default=BOTTOM)
    ap.add_argument("--wall", default=WALL)
    args = ap.parse_args()

    out = {}
    threads = [threading.Thread(target=probe, args=(u, args.seconds, out))
               for u in (args.bottom, args.wall)]
    print(f"mengukur kedua stream bersamaan, {args.seconds:.0f}s ...")
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    gagal = []
    for label, url in (("BOTTOM", args.bottom), ("WALL  ", args.wall)):
        r = out.get(url, {"error": "tidak ada hasil"})
        if "error" in r:
            print(f"{label} {url}\n   GAGAL: {r['error']}")
            gagal.append(label.strip())
            continue
        print(f"{label} {url}\n"
              f"   open {r['open_s']:.2f}s | FPS {r['fps']:.1f} | "
              f"gap p50 {r['p50_ms']:.0f}ms p95 {r['p95_ms']:.0f}ms max {r['max_ms']:.0f}ms | "
              f"frame {r['kb']:.0f} KB | {r['mbps']:.1f} Mbps")
        if r["fps"] < args.min_fps:
            gagal.append(f"{label.strip()} fps {r['fps']:.1f} < {args.min_fps}")

    total = sum(v["mbps"] for v in out.values() if "mbps" in v)
    print(f"\ntotal bitrate: {total:.1f} Mbps"
          f"{'  ! di atas ~60 Mbps: tether 100 Mbps mulai kehilangan paket' if total > 60 else ''}")

    if gagal:
        print("\nGAGAL - TIDAK LULUS: " + "; ".join(gagal))
        print("  Turunkan beban di Pi (SSH):")
        print("    sudo sed -i -E 's/-f [0-9]+/-f 15/' /etc/systemd/system/ustreamer-cam{1,2}.service")
        print("    sudo systemctl daemon-reload && sudo systemctl restart ustreamer-cam1 ustreamer-cam2")
        return 1
    print("\nOK - LULUS: kedua kamera jalan bersamaan di atas ambang")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
