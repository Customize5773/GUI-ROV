#!/usr/bin/env python3
"""
tools/export_wall_cnn.py — Konversi checkpoint PyTorch → .npz (numpy murni)
==========================================================================
Dijalankan SEKALI di laptop (yang punya torch), hasilnya dipakai ROV TANPA torch.

Kenapa: WallCNN cuma ~61 ribu parameter, tapi torch = ~733 MB. Memasang torch di
Raspberry Pi/Jetson demi model sekecil itu tak masuk akal — dan requirements.txt repo
ini sengaja ramping (numpy/cv2/pyzbar). vision/wall_cnn.py menjalankan inferensinya
dgn numpy saja (numpy SUDAH jadi dependency), jadi ROV tak perlu dependency baru.

Pakai (dari autonomy/):
    pip install torch --index-url https://download.pytorch.org/whl/cpu   # laptop saja
    python tools/export_wall_cnn.py --ckpt wall_cnn_fixed.pt --out vision/wall_cnn.npz

Verifikasi kesetaraan torch vs numpy ada di tests/test_wall_cnn.py.
"""
import argparse
import sys

import numpy as np


def main():
    ap = argparse.ArgumentParser(description='Ekspor wall_cnn .pt → .npz numpy')
    ap.add_argument('--ckpt', required=True, help='checkpoint .pt hasil notebook Colab')
    ap.add_argument('--out', default='vision/wall_cnn.npz', help='tujuan .npz')
    args = ap.parse_args()

    try:
        import torch
    except ImportError:
        sys.exit("torch tak terpasang — skrip ini hanya dijalankan di laptop, sekali:\n"
                 "  pip install torch --index-url https://download.pytorch.org/whl/cpu")

    ck = torch.load(args.ckpt, map_location='cpu', weights_only=False)
    sd = ck['state_dict'] if isinstance(ck, dict) and 'state_dict' in ck else ck

    out = {k: v.detach().cpu().numpy() for k, v in sd.items()
           if hasattr(v, 'detach')}
    meta = {
        'walls': np.array(ck.get('walls', list('ABCD'))),
        'img_size': np.array(ck.get('img_size', 96)),
        'canvas': np.array(ck.get('canvas', [640, 480])),
    }
    out.update(meta)
    np.savez_compressed(args.out, **out)

    n = sum(v.size for k, v in out.items() if k not in meta)
    print(f"OK: {args.ckpt} → {args.out}")
    print(f"  parameter : {n}")
    print(f"  walls     : {list(meta['walls'])}")
    print(f"  img_size  : {meta['img_size']}  canvas: {list(meta['canvas'])}")
    print("\nVerifikasi: python -m pytest tests/test_wall_cnn.py -v")


if __name__ == '__main__':
    main()
