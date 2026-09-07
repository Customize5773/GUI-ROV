"""QR proposals in a gripper ROI, always returned in full-frame coordinates."""
import math

DEFAULT_ROI = (0.25, 0.10, 0.75, 0.80)


def parse_roi(value):
    values = tuple(float(v) for v in value.split(','))
    if (len(values) != 4 or not all(math.isfinite(v) for v in values)
            or not 0 <= values[0] < values[2] <= 1
            or not 0 <= values[1] < values[3] <= 1):
        raise ValueError('ROI must be x1,y1,x2,y2 fractions in 0..1')
    return values


def detect_gripper_qr(detector, frame, roi=DEFAULT_ROI, region_conf=0.6):
    from vision.qr_detect import _decode_tracked_roi
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(round(v * size)) for v, size in zip(roi, (w, h, w, h)))
    if x2 <= x1 or y2 <= y1:
        return None, []
    crop = frame[y1:y2, x1:x2]
    detection = detector.detect(crop)
    if detection is None:
        return None, []
    detection = dict(detection)
    x, y, bw, bh = detection['bbox']
    quad = [[x, y], [x+bw, y], [x+bw, y+bh], [x, y+bh]]
    decoded = _decode_tracked_roi(crop, quad, full_cascade=True) or []
    # A nearby QR in the padded decode crop must not validate a different box.
    decoded = [dict(d) for d in decoded
               if x <= d['pts'][:, 0].mean() <= x+bw
               and y <= d['pts'][:, 1].mean() <= y+bh]
    if not decoded and detection['confidence'] < region_conf:
        return None, []
    for item in decoded:
        item['pts'] = item['pts'].copy() + (x1, y1)
    detection.update(bbox=(x+x1, y+y1, bw, bh),
                     center=(x+x1+bw/2, y+y1+bh/2), frame_w=w, frame_h=h)
    return detection, decoded
