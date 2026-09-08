"""Offline paired benchmark; requires the lossless frame cache from the initial replay.

Frames: /tmp/wall_qr_before/000.png ... 228.png.
Timestamps: /tmp/wall_qr_after.json. No camera/network/vehicle IO.
Use replay_gripper_qr.py --samples-from ../comparison.csv for normal video replay.
"""
import sys,time,json
sys.path.insert(0,'autonomy')
import cv2,numpy as np
import vision.qr_gripper as qr
from vision.yolo_hook import make_detector
cv2.setNumThreads(2);det=make_detector('autonomy/vision/best_new.onnx',conf=.1,imgsz=640)
recover=qr._decode_wechat_candidate;source=json.load(open('/tmp/wall_qr_after.json'));rows=[]
for i,b in enumerate(source):
 f=cv2.imread(f'/tmp/wall_qr_before/{i:03}.png');row={'sample':i,'time_s':b['t']}
 for name,fn in [('before',lambda *a:[]),('after',recover)]:
  qr._decode_wechat_candidate=fn;times=[]
  for _ in range(3):
   t=time.perf_counter();d,q=qr.detect_gripper_qr(det,f,live=True);times.append(1000*(time.perf_counter()-t))
  row[name]=q[0]['data'] if q else '';row[name+'_ms']=float(np.median(times))
 rows.append(row)
 if i%50==0:print(i,flush=True)
json.dump(rows,open('/tmp/qr_paired.json','w'),indent=2)
print({name:{'decoded':sum(bool(r[name]) for r in rows),'median_ms':float(np.median([r[name+'_ms'] for r in rows])),'p95_ms':float(np.percentile([r[name+'_ms'] for r in rows],95))} for name in ['before','after']},flush=True)
