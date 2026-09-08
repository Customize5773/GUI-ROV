"""QR proposals in a gripper ROI, always returned in full-frame coordinates."""
import math

DEFAULT_ROI = (0.25, 0.10, 0.75, 0.80)


def _decode_live_candidate(frame, quad):
    """Fixed ZXing workload on a bounded candidate crop; no exhaustive cascade."""
    import cv2
    import numpy as np
    from vision.qr_detect import _zxing_qr, _stretch
    q = np.asarray(quad)
    lo, hi = q.min(axis=0), q.max(axis=0)
    pad = .9 * max(hi - lo)
    h, w = frame.shape[:2]
    xa, ya = max(0, int(lo[0]-pad)), max(0, int(lo[1]-pad))
    xb, yb = min(w, int(hi[0]+pad)), min(h, int(hi[1]+pad))
    crop = frame[ya:yb, xa:xb]
    if min(crop.shape[:2]) < 24:
        return []
    factor = min(1.0, 640.0 / max(crop.shape[:2]))
    if factor < 1:
        crop = cv2.resize(crop, None, fx=factor, fy=factor)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    channels = [gray] if crop.ndim == 2 else [gray, crop[:, :, 1], cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)[:, :, 0]]
    for channel in channels:
        for scale in (1, 2):
            candidate = channel if scale == 1 else _stretch(channel)
            if scale > 1:
                candidate = cv2.resize(candidate, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            found = _zxing_qr(candidate, scale)
            if found:
                for item in found:
                    item['pts'] = item['pts'] / factor + (xa, ya)
                return found
    return []


def parse_roi(value):
    values = tuple(float(v) for v in value.split(','))
    if (len(values) != 4 or not all(math.isfinite(v) for v in values)
            or not 0 <= values[0] < values[2] <= 1
            or not 0 <= values[1] < values[3] <= 1):
        raise ValueError('ROI must be x1,y1,x2,y2 fractions in 0..1')
    return values


def _detect_yolo_gripper_qr(detector, frame, roi=DEFAULT_ROI, region_conf=0.6, live=False):
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
    decoded = (_decode_live_candidate(crop, quad) if live else
               _decode_tracked_roi(crop, quad, full_cascade=True)) or []
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


def detect_gripper_qr(detector, frame, roi=DEFAULT_ROI, region_conf=0.6, live=False):
    """Fast verified decode, YOLO, then full ROI decode for missed proposals.

    A decoded fallback is never reported as a YOLO region or YOLO confidence.
    Multiple QR symbols are ambiguous: do not pick an arbitrary grab target.
    """
    import cv2
    import numpy as np
    from vision.qr_detect import decode_qr, ZXING_OK, _zxing_qr, _stretch
    if live and not ZXING_OK:
        raise RuntimeError('Live gripper QR requires zxing-cpp; refusing slow decode cascade')
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [round(v*s) for v,s in zip(roi,(w,h,w,h))]
    if x2 <= x1 or y2 <= y1:
        return None, []
    crop = frame[y1:y2, x1:x2]
    # One cheap checksum/quiet-zone-verified pass avoids paying YOLO and its
    # failed decode cascade for a plainly readable QR on Raspberry Pi.
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    candidates = _zxing_qr(gray) if ZXING_OK else []
    detection = None
    if not candidates:
        detection, decoded = _detect_yolo_gripper_qr(detector, frame, roi, region_conf, live=live)
        if decoded:
            detection['method'] = 'yolo_qr'
            return detection, decoded
        # Live control has a fixed, short decode workload. Exhaustive recovery
        # can take seconds on a failed frame and starve fresh vision updates.
        candidates = (_zxing_qr(_stretch(gray)) if live else decode_qr(crop)) or []
    if len(candidates) > 1:
        return None, []
    if not candidates:
        return detection, []
    item = dict(candidates[0])
    pts = np.asarray(item.get('pts'), dtype=np.float32)
    if (pts.shape != (4,2) or not np.isfinite(pts).all()
            or not isinstance(item.get('data'), str) or not item['data']
            or np.any(pts < 0) or np.any(pts[:,0] >= x2-x1)
            or np.any(pts[:,1] >= y2-y1)):
        return None, []
    pts = pts + (x1,y1)
    area = float(cv2.contourArea(pts.astype(np.float32)))
    if area <= 0:
        return None, []
    item['pts'] = pts
    x,y,bw,bh = cv2.boundingRect(pts.astype(np.float32))
    return dict(method='qr_decode', confidence=0.0, bbox=(x,y,bw,bh),
                center=tuple(pts.mean(axis=0)), area=area,
                frame_w=w, frame_h=h), [item]
