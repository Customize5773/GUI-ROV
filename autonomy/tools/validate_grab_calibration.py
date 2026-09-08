"""Replay collected calibration frames offline. Never sends vehicle commands."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from vision.qr_gripper import detect_gripper_qr
from vision.yolo_hook import make_detector


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', default='logs/grab-calibration')
    args = ap.parse_args()
    root = Path(args.root)
    candidate = json.loads((root/'calibration-candidate.json').read_text())
    valid_names = ('far', 'near', 'left', 'right', 'middle', 'middle_recheck')
    # This envelope summarizes user-confirmed positions; it is not an
    # independently validated tolerance at all combinations of X and Y.
    positions = []
    for name in valid_names:
        records = json.loads((root/candidate['sources'][name]/'measurements.json').read_text())
        positions.extend([[q['center'][0]/1280,q['center'][1]/720]
                          for r in records for q in r['direct']])
    xy = np.asarray(positions)
    roi = [float(xy[:,0].min()),float(xy[:,1].min()),
           float(xy[:,0].max()),float(xy[:,1].max())]
    cv2.setNumThreads(2)
    detector = make_detector(str(Path(__file__).resolve().parents[1]/'vision/best_new_320.onnx'),
                             conf=.1, imgsz=320)
    summaries, details = {}, []
    for name in (*valid_names, 'outside'):
        rows = []
        for path in sorted((root/candidate['sources'][name]).glob('*.jpg')):
            frame = cv2.imread(str(path))
            h,w = frame.shape[:2]
            started = time.perf_counter()
            detection, decoded = detect_gripper_qr(detector, frame, live=True)
            row = dict(position=name,file=path.name,method=detection.get('method') if detection else None,
                       ms=round((time.perf_counter()-started)*1000,2),data=None,geometry_gate=False)
            if decoded:
                q = decoded[0]
                x,y = q['pts'].mean(axis=0)/(w,h)
                area = float(cv2.contourArea(q['pts'].astype(np.float32)))/(w*h)
                row.update(data=q['data'],x=float(x),y=float(y),area=area,
                           geometry_gate=bool(roi[0]<=x<=roi[2] and roi[1]<=y<=roi[3]
                                              and area>candidate['close_area_frac_decoded']))
            rows.append(row)
        summaries[name] = dict(samples=len(rows),decoded=sum(r['data'] is not None for r in rows),
                               fallback=sum(r['method']=='qr_decode' for r in rows),
                               geometry_gate=sum(r['geometry_gate'] for r in rows),
                               median_ms=round(float(np.median([r['ms'] for r in rows])),2))
        details.extend(rows)
    report = dict(roi_observed=roi,threshold=candidate['close_area_frac_decoded'],
                  summaries=summaries,details=details,
                  limitation='Offline replay of calibration data, not held-out accuracy or physical grab validation')
    (root/'fallback-validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(dict(roi=roi,threshold=report['threshold'],summaries=summaries),indent=2))


if __name__ == '__main__':
    main()
