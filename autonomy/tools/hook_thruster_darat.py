#!/usr/bin/env python3
"""
tools/hook_thruster_darat.py — Uji darat: YOLO laptop -> thruster, TANPA ROV.

Menjawab pertanyaan yang tidak bisa dijawab dengan menonton overlay kamera saja
(TEST_VISION_DARAT.md): "kalau YOLO ini yang menyetir, seberapa agresif
thruster-nya?" Rantainya SAMA dengan produksi, tidak ada logika yang ditiru ulang:

    YOLOHookDetector (laptop)                 vision/yolo_hook.py
      -> rov_agent._validate_hook_vision      batas jaringan laptop -> Pi
      -> Mission5FSM M5_YOLO_SEARCH/M5_HOOK_ALIGN
      -> perintah thruster

Bedanya hanya di ujung: perintah DICATAT, tidak dikirim ke Pixhawk. Aman
dijalankan dengan ROV mati atau propeller terpasang.

    python3 tools/hook_thruster_darat.py --camera http://192.168.2.2:8080/stream
    python3 tools/hook_thruster_darat.py --camera rekaman.avi --frames 120

Vonis dihitung dari perintah NYATA hasil deteksi nyata:
  * tiap sumbu <= SERVO_MAX_SPEED            (pagar kecepatan)
  * lonjakan antar-tick <= SERVO_SLEW*dt     (anti-sentak; sentak -> ROV miring)
  * surge tergerbang selagi belum center     (center dulu, baru maju)

Yang TIDAK divalidasi di darat: arah/tanda sumbu terhadap air, depth-hold, dan
X/Y global — semuanya butuh loop fisik di kolam.
"""
import argparse
import os
import sys
import time

import cv2

_AUTONOMY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTONOMY)
sys.path.insert(0, os.path.dirname(_AUTONOMY))       # rov_agent.py ada di root repo

import fsm.mission5 as m5
from fsm.mission5 import Mission5FSM, State
from vision.yolo_hook import YOLOHookDetector
from control.visual_servo import _approach_gate
from rov_agent import _validate_hook_vision


class RecordingCmd:
    """Pengganti CommandSender: perintah dicatat, Pixhawk tidak disentuh."""

    def __init__(self):
        self.log = []          # (t, surge, sway, yaw, vert)

    def send(self, surge=0, sway=0, yaw=0, vert=0, gripper=None):
        self.log.append((time.time(), surge, sway, yaw, vert))

    def stop_all(self):
        self.send()

    def arm(self, on=True):
        pass

    def emergency_stop(self):
        self.send()

    def close(self):
        pass


class StubTelem:
    def __init__(self, depth):
        self._d = {"depth": depth, "heading": 0.0, "roll": 0.0, "pitch": 0.0,
                   "control_mode": "autonomous"}

    def get(self):
        return dict(self._d)


def main():
    ap = argparse.ArgumentParser(description="Uji darat rantai YOLO -> thruster")
    ap.add_argument("--camera", required=True, help="URL stream / file video / indeks device")
    ap.add_argument("--model", default=os.path.join(_AUTONOMY, "vision", "best_pose.pt"))
    ap.add_argument("--frames", type=int, default=80)
    ap.add_argument("--fps", type=float, default=10.0, help="samakan dengan worker (dt servo nyata)")
    ap.add_argument("--conf", type=float, default=0.20)
    ap.add_argument("--depth", type=float, default=None, help="kedalaman palsu (default: HOOK_DEPTH)")
    args = ap.parse_args()

    detector = YOLOHookDetector(args.model, conf=args.conf, enhance_underwater=True)
    source = int(args.camera) if args.camera.isdigit() else args.camera
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"[FAIL] kamera tidak bisa dibuka: {args.camera}")
        return 3

    cmd = RecordingCmd()
    fsm = Mission5FSM(cmd=cmd, telem=StubTelem(args.depth or m5.HOOK_DEPTH), vision=None)
    transitions = []
    original_transition = fsm._transition
    fsm._transition = lambda s: (transitions.append(s.name), original_transition(s))[1]
    fsm._transition(State.M5_YOLO_SEARCH)
    telem = fsm.telem.get()

    servo = fsm.hook_servo
    interval = 1.0 / max(0.1, args.fps)
    hits, samples = 0, []       # samples: (surge, sway, yaw, vert, ex, ey)
    for _ in range(args.frames):
        started = time.monotonic()
        ok, frame = cap.read()
        if not ok:
            break
        det = detector.detect(frame)
        raw = det and {"status": "ok", "method": "yolov8",
                       "confidence": det["confidence"], "bbox": list(det["bbox"]),
                       "frame_w": det["frame_w"], "frame_h": det["frame_h"]}
        # Persis yang dilihat Pi: validator membuang keypoint & field tak dipercaya.
        value = _validate_hook_vision(raw) if raw else None
        hits += value is not None
        fsm._yolo_source = lambda v=value: v

        before, state = len(cmd.log), fsm._state
        if fsm._state == State.M5_YOLO_SEARCH:
            fsm._state_m5_yolo_search(telem)
        elif fsm._state == State.M5_HOOK_ALIGN:
            fsm._state_m5_hook_align(telem)
        else:
            break
        for t, su, sw, yw, vt in cmd.log[before:]:
            samples.append((t, state, su, sw, yw, vt,
                            fsm.telemetry_out.get("offset_x"), fsm.telemetry_out.get("offset_y")))
        time.sleep(max(0.0, interval - (time.monotonic() - started)))
    cap.release()

    if not samples:
        print("[FAIL] tidak ada perintah tercatat — kamera kosong atau tak ada deteksi")
        return 1

    axes = ("surge", "sway", "yaw", "vert")
    peak = [max(abs(s[i + 2]) for s in samples) for i in range(4)]
    # Laju perubahan diukur HANYA di dalam M5_HOOK_ALIGN, satu-satunya state yang
    # perintahnya keluar dari servo. Di M5_YOLO_SEARCH perintahnya memang
    # berundak (maju SEARCH_SPEED -> stop_all saat hook terlihat): itu keputusan
    # state, bukan servo, dan arahnya selalu MENUJU nol.
    align = [s for s in samples if s[1] == State.M5_HOOK_ALIGN]
    rates = [[abs(b[i + 2] - a[i + 2]) / max(0.02, min(0.5, b[0] - a[0]))
              for a, b in zip(align, align[1:])] for i in range(4)]
    jumps = [max(r, default=0.0) for r in rates]

    print(f"\ndeteksi lolos validator Pi : {hits}/{len(samples)} tick")
    print(f"transisi state             : {' -> '.join(['M5_YOLO_SEARCH'] + transitions[1:]) or '-'}")
    print(f"tick servo (M5_HOOK_ALIGN) : {len(align)}")
    print(f"{'sumbu':<7}{'|puncak| %':>12}{'laju maks %/s':>16}")
    for i, name in enumerate(axes):
        print(f"{name:<7}{peak[i]:>12.1f}{jumps[i]:>16.1f}")
    print(f"pagar                      : max {m5.SERVO_MAX_SPEED:.0f} %, "
          f"slew {m5.SERVO_SLEW:.0f} %/s")

    verdicts = []
    verdicts.append(("kecepatan <= SERVO_MAX_SPEED", max(peak) <= m5.SERVO_MAX_SPEED + 1e-6,
                     f"puncak {max(peak):.1f} %"))
    # Toleransi 10%: dt servo diukur di dalam FSM, dt di sini diukur di luarnya —
    # keduanya jam nyata, beda beberapa milidetik.
    verdicts.append(("tanpa sentakan (slew)", max(jumps) <= m5.SERVO_SLEW * 1.1,
                     f"laju terbesar {max(jumps):.1f} %/s vs pagar {m5.SERVO_SLEW:.0f} %/s"))

    gated = [(abs(su), _approach_gate(max(abs(ex), abs(ey)) / servo.tol_norm, servo.approach_floor))
             for _, _, su, _, _, _, ex, ey in align if ex is not None]
    off_center = [(s, g) for s, g in gated if g < 1.0]
    verdicts.append(("surge tergerbang saat melenceng",
                     all(s <= m5.SERVO_MAX_SPEED * g + 1e-6 for s, g in off_center),
                     f"{len(off_center)} tick belum center, "
                     f"gerbang terkecil {min([g for _, g in off_center], default=1.0):.2f}"))

    print()
    for name, ok, detail in verdicts:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
    return 0 if all(v[1] for v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
