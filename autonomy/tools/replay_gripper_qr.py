"""Offline replay of the worker gripper detector; never sends vehicle commands."""
import argparse
import csv
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
from vision.qr_gripper import DEFAULT_ROI, parse_roi, detect_gripper_qr
from vision.yolo_hook import make_detector


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('video')
    ap.add_argument('--output', required=True)
    ap.add_argument('--roi', type=parse_roi, default=DEFAULT_ROI)
    ap.add_argument('--step', type=float, default=1.0)
    ap.add_argument('--model', default=str(Path(__file__).resolve().parents[1] / 'vision/best_new_320.onnx'))
    ap.add_argument('--imgsz', type=int, default=320)
    ap.add_argument('--live', action='store_true', help='use the bounded production decoder')
    ap.add_argument('--samples-from', help='CSV time_s column for exact sequential replay of prior samples')
    args = ap.parse_args()
    if args.step <= 0:
        ap.error('--step must be positive')
    sample_times = None
    if args.samples_from:
        with open(args.samples_from, newline='') as source:
            sample_times = [float(row['time_s']) for row in csv.DictReader(source)]
        if (not sample_times or any(not math.isfinite(t) or t < 0 for t in sample_times)
                or any(b <= a for a, b in zip(sample_times, sample_times[1:]))):
            ap.error('sample timestamps must be finite, nonnegative and strictly increasing')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    detector = make_detector(args.model, conf=0.1, imgsz=args.imgsz)
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise RuntimeError('Cannot open video')
    rows, next_time = [], sample_times[0] if sample_times else 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if t + 1e-6 < next_time:
                continue
            next_time = t + args.step
            detection, decoded = detect_gripper_qr(detector, frame, args.roi, live=args.live)
            row = dict(time_s=round(t, 3), detected=bool(detection), decoded=bool(decoded),
                       data=decoded[0]['data'] if decoded else '', confidence='', center_x='', area_frac='')
            if detection:
                x, y, w, h = detection['bbox']
                row.update(confidence=detection['confidence'], center_x=detection['center'][0],
                           area_frac=detection['area'] / (frame.shape[0]*frame.shape[1]))
                cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)
            fh, fw = frame.shape[:2]
            a,b,c,d = [round(v*s) for v,s in zip(args.roi,(fw,fh,fw,fh))]
            cv2.rectangle(frame,(a,b),(c,d),(255,200,0),2)
            cv2.putText(frame, f'{t:.2f}s QR: {row["data"] or "none"}', (20,35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,255), 2)
            cv2.imwrite(str(output / f'frame_{len(rows):04d}.jpg'),frame)
            rows.append(row)
            if sample_times:
                if len(rows) == len(sample_times):
                    break
                next_time = sample_times[len(rows)]
    finally:
        cap.release()
    if not rows:
        raise RuntimeError('No readable frames')
    if sample_times and len(rows) != len(sample_times):
        raise RuntimeError('Video ended before all requested samples were read')
    with (output/'results.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary=dict(samples=len(rows), detected=sum(r['detected'] for r in rows),
                 decoded=sum(r['decoded'] for r in rows), roi=args.roi, model=args.model, live=args.live, imgsz=args.imgsz, samples_from=args.samples_from)
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
