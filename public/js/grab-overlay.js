// Visual guide only. QR coordinates come from the displayed camera's browser decoder.
export function drawGrabOverlay(canvas, img, qr, roi) {
  const ctx = canvas.getContext('2d');
  const box = img.getBoundingClientRect();
  canvas.width = Math.round(box.width);
  canvas.height = Math.round(box.height);
  const sw = img.naturalWidth, sh = img.naturalHeight;
  if (!sw || !sh || !box.width || !box.height) return;
  const scale = Math.max(box.width / sw, box.height / sh);
  const ox = (box.width - sw * scale) / 2, oy = (box.height - sh * scale) / 2;
  const point = (x, y) => [ox + x * sw * scale, oy + y * sh * scale];
  const [left, top] = point(roi[0], roi[1]);
  const [right, bottom] = point(roi[2], roi[3]);
  const tx = (left + right) / 2, ty = (top + bottom) / 2;
  ctx.strokeStyle = '#f5c518'; ctx.fillStyle = '#f5c518'; ctx.lineWidth = 2;
  ctx.setLineDash([8, 5]);
  ctx.strokeRect(left, top, right-left, bottom-top);
  ctx.setLineDash([]);
  ctx.beginPath(); ctx.moveTo(tx-10,ty); ctx.lineTo(tx+10,ty);
  ctx.moveTo(tx,ty-10); ctx.lineTo(tx,ty+10); ctx.stroke();
  ctx.font = '12px monospace';
  ctx.fillText('AREA GRAB · PRATINJAU', left, Math.max(18,top-9));
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
      status = `${inside ? 'QR dalam area pratinjau' : 'QR di luar area'} · X ${(nx*100).toFixed(1)}% Y ${(ny*100).toFixed(1)}%`;
    }
  }
  ctx.fillStyle='rgba(0,0,0,.72)'; ctx.fillRect(12,48,Math.min(box.width-24,460),46);
  ctx.fillStyle='#fff'; ctx.fillText(status,20,66);
  ctx.fillStyle='#f5c518'; ctx.fillText('Belum dikalibrasi · bukan status siap grab',20,84);
}
