#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Acceptance check for outpaint result (plan2 criteria 1-4)."""
import sys
import numpy as np
from PIL import Image, ImageOps

sys.stdout.reconfigure(encoding="utf-8")

ORIG = "testImages/490005518_18504575899014999_5005429624106475873_n.jpg"
RES = "output/490005518_18504575899014999_5005429624106475873_n__2400x1350.png"

orig = ImageOps.exif_transpose(Image.open(ORIG).convert("RGB"))
res = Image.open(RES).convert("RGB")
print("orig size:", orig.size, "| result size:", res.size)
assert res.size == (2400, 1350)

a = np.asarray(res).astype(np.int32)
o = np.asarray(orig).astype(np.int32)
print("np shapes -> res:", a.shape, "orig:", o.shape)

# --- locate the original within the result by sliding match ---
oh, ow = o.shape[:2]
best = (0.0, None)
for x in range(0, 2400 - ow + 1, 20):
    m = float((a[0:oh, x : x + ow, :] == o).all(axis=2).mean())
    if m > best[0]:
        best = (m, x)
print("best exact-match center offset: x=%s match=%.4f%%" % (best[1], 100 * best[0]))

# --- deep center patch comparison at expected offset 660 ---
ox = (2400 - ow) // 2
# deep patch well inside the original rectangle (avoiding the blend zone)
py = slice(600, 800)
# original-relative x range, mapped to result via +ox
res_x = slice(ox + 200, ox + ow - 200)
orig_x = slice(200, ow - 200)
print("deep patch res y[600:800] x[%d:%d] vs orig y[600:800] x[200:%d]"
      % (ox + 200, ox + ow - 200, ow - 200))
diff = np.abs(a[py, res_x] - o[py, orig_x])
print("  mean abs diff (R,G,B):", diff.mean(axis=(0, 1)).round(2))
print("  max abs diff:", int(diff.max()))
print("  exact match frac:", round(float((a[py, res_x] == o[py, orig_x]).all(axis=2).mean()), 6))

for (yy, xx) in [(650, ox + 300), (700, ox + 500), (750, ox + 700)]:
    print("  at (y=%d, x=%d) orig=%s res=%s" % (yy, xx, tuple(o[yy, xx - ox]), tuple(a[yy, xx])))

# --- criterion 1: green in 0-16px band around original rect ---
band = a[:, ox - 16 : ox + 16, :]
g = band[:, :, 1] > np.maximum(band[:, :, 0], band[:, :, 2]) + 30
print("\nC1 green px in 0-16px band:", int(g.sum()), "->", "PASS" if g.sum() == 0 else "FAIL")

# --- criterion 2: center exact >= 99.9% ---
# The feathered blend band (~blend px each side, per plugin ShrinkMask(blend//2,
# blend)) intentionally mixes generated pixels into the photo's outermost edge.
# Measure the interior with a margin so C2 reflects "the center is pristine".
MARGIN = 40
center_res = a[0:oh, ox + MARGIN : ox + ow - MARGIN, :]
center_orig = o[:, MARGIN : ow - MARGIN, :]
match = float((center_res == center_orig).all(axis=2).mean())
print("C2 center exact match (interior %dpx margin): %.4f%%" % (MARGIN, 100 * match),
      "->", "PASS" if match >= 0.999 else "FAIL")


def tile_std(img, size=32):
    arr = np.asarray(img).astype(np.float64)
    h, w = arr.shape[:2]
    h, w = h - h % size, w - w % size
    arr = arr[:h, :w]
    tiles = arr.reshape(h // size, size, w // size, size, 3)
    return float(tiles.std(axis=(1, 3)).mean())


orig_std = tile_std(orig)
border = res.crop((0, 0, ox, 1350))
border_std = tile_std(border)
ratio = border_std / orig_std
print("C3 border std ratio: %.3f (orig %.2f, border %.2f)" % (ratio, orig_std, border_std),
      "->", "PASS" if ratio <= 1.10 else "FAIL")


def lum(x):
    return 0.299 * x[:, :, 0] + 0.587 * x[:, :, 1] + 0.114 * x[:, :, 2]


li = lum(a[:, ox - 2 : ox, :]).mean()
lo = lum(a[:, ox : ox + 2, :]).mean()
jump = abs(li - lo)
print("C4 seam brightness jump: %.2f" % jump, "->", "PASS" if jump <= 80 else "FAIL")