"""Offline replay of the worker gripper detector; never sends vehicle commands."""
import argparse
import csv
import json
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
    args = ap.parse_args()
    if args.step <= 0:
        ap.error('--step must be positive')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    detector = make_detector(args.model, conf=0.1, imgsz=320)
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise RuntimeError('Cannot open video')
    rows, next_time = [], 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if t < next_time:
                continue
            next_time = t + args.step
            detection, decoded = detect_gripper_qr(detector, frame, args.roi)
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
    finally:
        cap.release()
    if not rows:
        raise RuntimeError('No readable frames')
    with (output/'results.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary=dict(samples=len(rows), detected=sum(r['detected'] for r in rows),
                 decoded=sum(r['decoded'] for r in rows), roi=args.roi, model=args.model)
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
