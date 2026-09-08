// Visual guide only. QR coordinates come from the displayed camera's browser decoder.
export function drawGrabOverlay(canvas, img, qr, roi, calibration = null, backend = null) {
  const ctx = canvas.getContext('2d');
  const box = img.getBoundingClientRect();
  canvas.width = Math.round(box.width);
  canvas.height = Math.round(box.height);
  if (!Array.isArray(roi) || roi.length !== 4 || !roi.every(Number.isFinite)) return;
  const sw = img.naturalWidth, sh = img.naturalHeight;
  if (!sw || !sh || !box.width || !box.height) return;
  const scale = Math.max(box.width / sw, box.height / sh);
  const ox = (box.width - sw * scale) / 2, oy = (box.height - sh * scale) / 2;
  const point = (x, y) => [ox + x * sw * scale, oy + y * sh * scale];
  const [left, top] = point(roi[0], roi[1]);
  const [right, bottom] = point(roi[2], roi[3]);
  const tx = calibration ? point(calibration.target_x_norm, 0)[0] : (left + right) / 2;
  const ty = (top + bottom) / 2;
  ctx.strokeStyle = '#f5c518'; ctx.fillStyle = '#f5c518'; ctx.lineWidth = 2;
  ctx.setLineDash([8, 5]);
  ctx.strokeRect(left, top, right-left, bottom-top);
  ctx.setLineDash([]);
  ctx.beginPath(); ctx.moveTo(tx-10,ty); ctx.lineTo(tx+10,ty);
  ctx.moveTo(tx,ty-10); ctx.lineTo(tx,ty+10); ctx.stroke();
  ctx.font = '12px monospace';
  ctx.fillText('AREA GRAB · KALIBRASI MEJA', left, Math.max(18,top-9));
  const loc = qr?.location;
  let status = 'QR tidak terlihat';
  if (loc) {
    const corners = [loc.topLeftCorner,loc.topRightCorner,loc.bottomRightCorner,loc.bottomLeftCorner];
    const scanScale = Math.min(1,1280/Math.max(sw,sh));
    const scanW = Math.max(1,Math.round(sw*scanScale)), scanH = Math.max(1,Math.round(sh*scanScale));
    if (corners.every(p => p && Number.isFinite(p.x) && Number.isFinite(p.y))) {
      const nx = corners.reduce((s,p)=>s+p.x,0)/4/scanW;
      const ny = corners.reduce((s,p)=>s+p.y,0)/4/scanH;
      const [cx,cy] = point(nx,ny);
      const inside = nx>=roi[0] && nx<=roi[2] && ny>=roi[1] && ny<=roi[3];
      ctx.strokeStyle = '#41d9ff'; ctx.fillStyle = '#41d9ff';
      ctx.beginPath();
      corners.forEach((p,i)=> { const [x,y]=point(p.x/scanW,p.y/scanH); i ? ctx.lineTo(x,y) : ctx.moveTo(x,y); });
      ctx.closePath(); ctx.stroke();
      ctx.beginPath(); ctx.arc(cx,cy,5,0,Math.PI*2); ctx.fill();
      ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(tx,ty); ctx.stroke();
      status = `${inside ? 'QR dalam area XY' : 'QR di luar area'} · X ${(nx*100).toFixed(1)}% Y ${(ny*100).toFixed(1)}%`;
    }
  }
  if (backend) {
    const [x,y,w,h] = backend.bbox;
    const [bx,by] = point(x/backend.frame_w,y/backend.frame_h);
    const [ex,ey] = point((x+w)/backend.frame_w,(y+h)/backend.frame_h);
    const decoded = typeof backend.data === 'string' && backend.data.length > 0;
    const color = decoded ? '#2ee6a6' : '#f5c518';
    ctx.strokeStyle = color; ctx.fillStyle = color;
    ctx.strokeRect(bx,by,ex-bx,ey-by);
    const source = backend.method === 'qr_decode' ? 'QR' : 'YOLO QR';
    const confidence = backend.method === 'qr_decode' ? '' : ` ${Math.round(backend.confidence*100)}%`;
    ctx.fillText(`${source}${confidence}`,Math.max(4,bx),Math.max(16,by-6));
    status = decoded ? `${source} terbaca: ${backend.data.slice(0,60)}` : 'YOLO QR terdeteksi · belum terbaca';
  }
  ctx.fillStyle='rgba(0,0,0,.72)'; ctx.fillRect(12,48,Math.min(box.width-24,460),46);
  ctx.fillStyle='#fff'; ctx.fillText(status,20,66);
  ctx.fillStyle='#f5c518'; ctx.fillText('Acuan meja · grab tetap menunggu gate kontrol',20,84);
}

// Pi computes observation age; local elapsed time also expires a disconnected feed.
export function freshWallQr(telemetry, elapsedSeconds = 0) {
  return ['qr_vision', 'qr_region'].map(channel => {
    const det = telemetry?.[channel], receipt = telemetry?.vision_receipts?.[channel];
    if (!det || det.active_cam !== 'WALL' || !Number.isFinite(receipt?.age)
        || receipt.age < 0 || receipt.age + elapsedSeconds > 1
        || !Number.isFinite(det.frame_w) || !Number.isFinite(det.frame_h)
        || det.frame_w <= 0 || det.frame_h <= 0
        || !Array.isArray(det.bbox) || det.bbox.length !== 4
        || !det.bbox.every(Number.isFinite) || det.bbox[2] <= 0 || det.bbox[3] <= 0) return null;
    return {det, age: receipt.age};
  }).filter(Boolean).sort((a,b) => a.age-b.age)[0]?.det || null;
}
