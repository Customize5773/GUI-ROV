"""Compare QR models on identical original video frames, without vehicle IO."""
import argparse
import csv
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from vision.qr_gripper import DEFAULT_ROI, detect_gripper_qr
from vision.qr_detect import decode_qr
from vision.yolo_hook import make_detector


class Probe:
    def __init__(self, detector):
        self.detector = detector
        self.last = None

    def detect(self, frame):
        self.last = self.detector.detect(frame)
        return self.last


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('video')
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    cv2.setNumThreads(2)
    cap = cv2.VideoCapture(args.video)
    frames, target = [], 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC)/1000
            if t >= target:
                frames.append((t, frame))
                target = t+1
    finally:
        cap.release()
    if not frames:
        raise RuntimeError('No video frames')
    rows, summaries, failed_tiles = [], [], []
    # Decode-only diagnostic is independent of YOLO; no result controls the ROV.
    direct = []
    for t, frame in frames:
        h,w = frame.shape[:2]
        x1,y1,x2,y2 = [round(v*s) for v,s in zip(DEFAULT_ROI,(w,h,w,h))]
        decoded = decode_qr(frame[y1:y2,x1:x2])
        direct.append('|'.join(d['data'] for d in decoded))
    for size, name in [(320,'best_new_320.onnx'),(416,'best_new_416.onnx'),(640,'best_new.onnx')]:
        model = Path(__file__).resolve().parents[1]/'vision'/name
        probe = Probe(make_detector(str(model), conf=.1, imgsz=size))
        detect_gripper_qr(probe, frames[0][1])  # warm-up excluded
        model_rows = []
        for i,(t,frame) in enumerate(frames):
            timings=[]
            for _ in range(3):
                started=time.perf_counter()
                detection, decoded=detect_gripper_qr(probe,frame)
                timings.append((time.perf_counter()-started)*1000)
            stage = 'decoded' if decoded else ('region_only' if detection else
                    ('no_yolo_proposal' if probe.last is None else 'decode_or_bbox_gate'))
            row=dict(model=size,time_s=round(t,3),stage=stage,
                     data=decoded[0]['data'] if decoded else '',
                     direct_roi_decode=direct[i],
                     confidence=probe.last['confidence'] if probe.last else '',
                     median_ms=round(float(np.median(timings)),2))
            rows.append(row)
            model_rows.append(row)
            if size==320 and detection is None:
                tile=cv2.resize(frame,(384,216))
                cv2.putText(tile,f'{t:.2f}s direct={direct[i] or "none"}',(8,20),
                            cv2.FONT_HERSHEY_SIMPLEX,.5,(0,255,255),1)
                failed_tiles.append(tile)
        summaries.append(dict(model=size,samples=len(frames),
                         decoded=sum(r['stage']=='decoded' for r in model_rows),
                         region_only=sum(r['stage']=='region_only' for r in model_rows),
                         no_proposal=sum(r['stage']=='no_yolo_proposal' for r in model_rows),
                         decode_or_bbox_gate=sum(r['stage']=='decode_or_bbox_gate' for r in model_rows),
                         median_ms=round(float(np.median([r['median_ms'] for r in model_rows])),2),
                         p95_ms=round(float(np.percentile([r['median_ms'] for r in model_rows],95)),2)))
    with (out/'comparison.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary=dict(models=summaries,direct_roi_decoded=sum(bool(d) for d in direct),
                 timing='Local CPU, OpenCV threads=2, median of 3 warm runs per frame; excludes video IO')
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    if failed_tiles:
        while len(failed_tiles)%4:
            failed_tiles.append(np.zeros_like(failed_tiles[0]))
        sheet=np.vstack([np.hstack(failed_tiles[i:i+4]) for i in range(0,len(failed_tiles),4)])
        cv2.imwrite(str(out/'failed_320.jpg'),sheet)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
