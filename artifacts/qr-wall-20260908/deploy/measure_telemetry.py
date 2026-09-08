import socket,json,time,sys,statistics
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('0.0.0.0',14551));s.settimeout(2)
rows=[];start=time.monotonic()
while time.monotonic()-start<float(sys.argv[2]):
 try:b,_=s.recvfrom(65535);d=json.loads(b)
 except socket.timeout:continue
 if 'armed' not in d:continue
 rows.append(dict(at=time.monotonic()-start,armed=d.get('armed'),mode=d.get('mode'),control_mode=d.get('control_mode'),pwm=d.get('thrusters_pwm'),temp=d.get('pi_temp'),fc_link=d.get('fc_link'),receipts=d.get('vision_receipts'),qr=d.get('qr_vision'),cpu=d.get('pi_cpu')))
def stats(a):
 if not a:return None
 a=sorted(a);return dict(n=len(a),median=statistics.median(a),p95=a[round((len(a)-1)*.95)],max=max(a))
summary=dict(samples=len(rows),interval_ms=stats([(b['at']-a['at'])*1000 for a,b in zip(rows,rows[1:])]),all_disarmed=bool(rows) and all(r['armed'] is False for r in rows),all_neutral=bool(rows) and all(r['pwm']==[1500]*6 for r in rows),fc_link_ok=bool(rows) and all(r['fc_link']=='ok' for r in rows),qr_decoded=sum(bool(r['qr']) for r in rows),temperature=stats([r['temp'] for r in rows if isinstance(r['temp'],(int,float))]))
json.dump(dict(summary=summary,rows=rows),open(sys.argv[1],'w'),indent=2);print(json.dumps(summary))
