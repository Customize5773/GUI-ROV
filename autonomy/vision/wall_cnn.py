"""
vision/wall_cnn.py — Fallback klasifikasi sisi kolam saat decode_qr() GAGAL
==========================================================================
Saat air keruh/beriak mematahkan `decode_qr()`, payload JSON/string tak terbaca — tapi
ROV sebenarnya hanya perlu tahu **sisi kolam (A/B/C/D)**. Membedakan 4 pola QR tetap
jauh lebih mudah daripada mendekode data arbitrer, jadi CNN kecil masih bisa
menjawabnya di frame yang decode-nya sudah menyerah.

Rantai kerja (lihat VisionPipeline._run_camera):
    decode_qr() gagal → cv2.QRCodeDetector.detect() cari letak QR (TANPA decode)
                      → crop + normalisasi → CNN → sisi kolam + confidence

Kenapa numpy, bukan torch: model ini ~61 ribu parameter (≈250 KB), sedangkan torch
≈733 MB. ROV (Raspberry Pi/Jetson) tak perlu dependency raksasa untuk itu — numpy sudah
jadi dependency wajib repo ini. Bobot diekspor sekali di laptop lewat
`tools/export_wall_cnn.py` (.pt → .npz), lalu dijalankan di sini dgn numpy murni.
Kesetaraan hasil torch vs numpy diuji di tests/test_wall_cnn.py.

PERINGATAN OPERASIONAL — model ini memberi `wall`, BUKAN payload:
  • `Mission5FSM._is_target_payload()` tak bisa memvalidasinya (tak ada mission/type).
  • Angka akurasinya berasal dari SIMULASI dgn 4 template bersih yang sama di train &
    test; QR cetakan yang difoto sungguhan akan berbeda (tinta, perspektif, glare).
  Karena itu perlakukan sebagai petunjuk BERKEYAKINAN RENDAH: butuh beberapa frame
  konsisten (lihat WallVoter) sebelum dipercaya, dan jangan dipakai memicu aksi
  ireversibel (GRAB/RELEASE) tanpa konfirmasi decode sungguhan.
"""

import logging
import os

import numpy as np

log = logging.getLogger(__name__)

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

# Lokasi bobot bawaan (hasil tools/export_wall_cnn.py)
DEFAULT_WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wall_cnn.npz')

# Crop = CROP_MARGIN x sisi QR dari cv2.detect(). JANGAN samakan dgn angka 1.15 di
# notebook: di sana crop dihitung dari `qr_px` = lebar PNG segno TERMASUK quiet zone
# (29 modul), sedangkan cv2.detect() mengembalikan sudut simbol SAJA (~21 modul, diukur
# di tengah modul finder). Rasio terukur qr_px/sisi_detect = 1.295, jadi padanannya
# 1.295 x 1.15 = 1.49. Salah nilai di sini = akurasi runtuh DIAM-DIAM: pada 1.15 akurasi
# jatuh ke 0.7% (dari 100%) karena model melihat QR pada skala yang tak pernah dilatih.
# Terukur 100% pada rentang 1.4-1.8 → 1.49 berada di tengah dataran aman itu.
CROP_MARGIN = 1.49
BN_EPS = 1e-5         # default torch BatchNorm2d


# ── Operasi numpy (setara torch dlm mode eval) ────────────────────────────────

def _conv2d_3x3(x, w, b):
    """Konvolusi 3x3 stride 1 padding 1 via im2col. x=(C,H,W) → (O,H,W)."""
    C, H, W = x.shape
    O = w.shape[0]
    xp = np.pad(x, ((0, 0), (1, 1), (1, 1)))
    # jendela 3x3 tiap posisi → (C,3,3,H,W) tanpa menyalin data
    s = xp.strides
    win = np.lib.stride_tricks.as_strided(
        xp, shape=(C, 3, 3, H, W), strides=(s[0], s[1], s[2], s[1], s[2]))
    col = win.reshape(C * 9, H * W)
    return (w.reshape(O, C * 9) @ col).reshape(O, H, W) + b.reshape(O, 1, 1)


def _batchnorm(x, gamma, beta, mean, var):
    """BatchNorm2d mode eval: pakai running stats."""
    scale = gamma / np.sqrt(var + BN_EPS)
    return x * scale.reshape(-1, 1, 1) + (beta - mean * scale).reshape(-1, 1, 1)


def _maxpool2(x):
    """MaxPool2d(2) — dimensi ganjil dipotong, sama seperti torch."""
    C, H, W = x.shape
    H2, W2 = H // 2 * 2, W // 2 * 2
    return x[:, :H2, :W2].reshape(C, H2 // 2, 2, W2 // 2, 2).max(axis=(2, 4))


def _softmax(v):
    e = np.exp(v - v.max())
    return e / e.sum()


class WallClassifier:
    """Klasifikasi sisi kolam A/B/C/D dari crop QR — numpy murni, tanpa torch.

    Contoh:
        clf = WallClassifier()                 # muat vision/wall_cnn.npz
        wall, conf = clf.predict_frame(frame)  # None bila QR tak bisa dilokalisasi
    """

    def __init__(self, weights=None):
        path = weights or DEFAULT_WEIGHTS
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"bobot wall_cnn tak ditemukan: {path}\n"
                "Buat dulu di laptop: python tools/export_wall_cnn.py "
                "--ckpt wall_cnn_fixed.pt --out vision/wall_cnn.npz")
        z = np.load(path, allow_pickle=False)
        self.w = {k: z[k] for k in z.files}
        self.walls = [str(x) for x in self.w['walls']]
        self.img_size = int(self.w['img_size'])
        self._detector = cv2.QRCodeDetector() if CV2_OK else None

    # ── inferensi ────────────────────────────────────────────────────────────

    def forward(self, crop):
        """crop (H,W) float32 ternormalisasi → probabilitas per kelas (numpy)."""
        x = crop[None, :, :].astype(np.float32)     # (1,H,W)
        for i in range(4):                          # 4 blok konvolusi
            x = _conv2d_3x3(x, self.w[f'f.{i}.0.weight'], self.w[f'f.{i}.0.bias'])
            x = _batchnorm(x, self.w[f'f.{i}.1.weight'], self.w[f'f.{i}.1.bias'],
                           self.w[f'f.{i}.1.running_mean'], self.w[f'f.{i}.1.running_var'])
            x = np.maximum(x, 0.0)                  # ReLU
            x = _maxpool2(x)
        x = x.mean(axis=(1, 2))                     # AdaptiveAvgPool2d(1) + Flatten
        # Dropout = identitas saat eval
        logits = self.w['head.3.weight'] @ x + self.w['head.3.bias']
        return _softmax(logits)

    def predict_crop(self, crop):
        p = self.forward(crop)
        i = int(np.argmax(p))
        return self.walls[i], float(p[i])

    # ── pra-proses ───────────────────────────────────────────────────────────

    def make_crop(self, gray, pts):
        """Potong QR dari frame grayscale memakai 4 sudut `pts`, lalu normalisasi.

        Normalisasi per-gambar (kurangi mean, bagi std) membuang bias kecerahan/kontras
        global — persis yang dilakukan backscatter bawah air — jadi model hanya melihat
        polanya. HARUS sama dgn preprocessing training (notebook), kalau tidak akurasi
        runtuh diam-diam."""
        p = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
        cx, cy = float(p[:, 0].mean()), float(p[:, 1].mean())
        side = float(max(p[:, 0].max() - p[:, 0].min(), p[:, 1].max() - p[:, 1].min()))
        side = max(8.0, side * CROP_MARGIN)
        h, w = gray.shape[:2]
        x0 = int(round(cx - side / 2)); y0 = int(round(cy - side / 2))
        x1 = int(round(cx + side / 2)); y1 = int(round(cy + side / 2))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        crop = gray[y0:y1, x0:x1]
        crop = cv2.resize(crop, (self.img_size, self.img_size),
                          interpolation=cv2.INTER_AREA).astype(np.float32)
        return (crop - crop.mean()) / (crop.std() + 1e-6)

    def locate(self, gray):
        """Cari letak QR TANPA decode (cv2.QRCodeDetector.detect).

        Terukur pada dataset simulasi: berhasil di ~94% frame yang decode-nya GAGAL,
        dgn galat pusat median ~1.4px — jauh di dalam toleransi model (±10px). Inilah
        yang membuat fallback ini mungkin: decode boleh gagal, lokalisasi tetap jalan."""
        if self._detector is None:
            return None
        try:
            found, pts = self._detector.detect(gray)
        except cv2.error:
            return None
        if not found or pts is None:
            return None
        return np.asarray(pts, dtype=np.float32).reshape(-1, 2)

    def predict_frame(self, frame, pts=None):
        """frame BGR/gray → ('A'|'B'|'C'|'D', confidence), atau None bila QR tak
        ditemukan. `pts` boleh diberikan bila letak QR sudah diketahui (mis. dari
        deteksi frame sebelumnya) sehingga lokalisasi dilewati."""
        if not CV2_OK:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        if pts is None:
            pts = self.locate(gray)
            if pts is None:
                return None
        crop = self.make_crop(gray, pts)
        if crop is None:
            return None
        return self.predict_crop(crop)


class WallVoter:
    """Kumpulkan tebakan antar-frame; hanya percaya bila stabil.

    Alasan: satu frame dari fallback CNN TIDAK tervalidasi payload (tak ada mission/type
    utk dicek `_is_target_payload`). Menuntut `need` tebakan berturut-turut yang sama +
    confidence minimum menekan false-positive sebelum FSM bertindak. Pola loss-of-lock-nya
    sengaja dibuat mirip `latest_qr(max_age=...)` yang sudah dipakai FSM."""

    def __init__(self, need=3, min_conf=0.8):
        self.need = need
        self.min_conf = min_conf
        self._wall = None
        self._n = 0

    def push(self, wall, conf):
        """Kembalikan wall bila sudah stabil `need` frame berturut-turut, else None."""
        if wall is None or conf < self.min_conf:
            self._wall, self._n = None, 0
            return None
        if wall == self._wall:
            self._n += 1
        else:
            self._wall, self._n = wall, 1
        return self._wall if self._n >= self.need else None

    def reset(self):
        self._wall, self._n = None, 0
