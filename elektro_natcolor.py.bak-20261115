#!/usr/bin/env python3
"""Natural-color composite for Elektro-L LRIT/HRIT visible channels.
usage: natcolor.py ch1.png ch2.png ch3.png out.png
"""
import sys
import numpy as np
from PIL import Image, ImageEnhance
from scipy.ndimage import median_filter

c = [np.asarray(Image.open(p), dtype=np.float32) for p in sys.argv[1:4]]
# Earth = pixels where at least 2 of the 3 channels have data (lost strips are 0 or flat grey in one channel)
disk = ((c[0] > 0).astype(int) + (c[1] > 0) + (c[2] > 0)) >= 2

def norm(a):
    lo, hi = np.percentile(a[disk & (a > 0)], [0.5, 99.7])
    return np.clip((a - lo) / (hi - lo), 0, 1)

c = [norm(a) for a in c]

# 1. lost strips: per row, a channel whose texture does not correlate with the two others
#    (which correlate with each other) is garbage; rebuild that row from the two good channels.
def rowcorr(a, b, m):
    a, b = np.where(m, a, np.nan), np.where(m, b, np.nan)
    a = a - np.nanmean(a, axis=1, keepdims=True); b = b - np.nanmean(b, axis=1, keepdims=True)
    return np.nansum(a*b, axis=1) / np.sqrt(np.nansum(a*a, axis=1) * np.nansum(b*b, axis=1) + 1e-9)
lit = disk & ((c[0] + c[1] + c[2]) > 0.15)          # ignore the night side
ok_rows = lit.sum(axis=1) > 100
R = {(i, j): rowcorr(c[i], c[j], lit) for i in range(3) for j in range(i+1, 3)}
r = lambda i, j: R[(min(i, j), max(i, j))]
bad = {}
for i in range(3):
    j, k = [x for x in range(3) if x != i]
    bad[i] = ok_rows & (r(i, j) < 0.6) & (r(i, k) < 0.6) & (r(j, k) > 0.8)
    bad[i] = np.convolve(bad[i], np.ones(9), 'same') > 0      # widen by a few rows
for i in range(3):
    j, k = [x for x in range(3) if x != i]
    c[i][bad[i]] = ((c[j] + c[k]) / 2)[bad[i]]
    print(f"ch{i+1}: rebuilt {bad[i].sum()} rows")

# 2. single-pixel bit-error dashes: replace outliers by a vertical median
for i in range(3):
    med = median_filter(c[i], size=(5, 1))
    bad = np.abs(c[i] - med) > 0.15
    c[i][bad] = med[bad]

# 3. channel mix: R = ch2 (red), G = blend of ch1 and ch3 (NIR, lights up vegetation), B = ch1
g = 0.7
r = c[1] ** g
b = c[0] ** g
gg = (0.55 * c[0] + 0.45 * c[2]) ** g
rgb = np.dstack([r, gg, b])
rgb[~disk] = 0

im = Image.fromarray((rgb * 255).astype(np.uint8))
im = ImageEnhance.Color(im).enhance(1.35)
im = ImageEnhance.Contrast(im).enhance(1.05)
im.save(sys.argv[4])
print(sys.argv[4])
