#!/usr/bin/env python3
"""Color-enhanced IR image (cold cloud tops highlighted) for an Elektro-L3 IR channel.
usage: elektro_ir_enhanced.py L3_<ch>_*.png out.png [ne_dir] [partner.png]
Meant for channel 4 (3.8um) and 9 (10.7um). Raw SatDump IR PNGs are warm = bright,
so the scale is inverted: warm surface dark, cloud white, and the coldest tops
(lowest counts) go through cyan->blue, green->yellow, orange->red. Coastlines and
borders are drawn on top via elektro_borders.py (style "ir").
LUT reverse-engineered from the first hand-made 15:30 UTC ch4/ch9 images.

partner.png is the other IR channel of the same slot (ch9 for ch4 and vice versa).
With it, lost-data strips are repaired: rows that are flat, or that stop agreeing
with the partner and break vertical continuity at their edges, are rebuilt from the
partner through a value mapping learned on rows where both channels are good. Rows
bad in both channels are left black (no data) rather than shown as fake cloud tops.
"""
import sys, os
import numpy as np
from PIL import Image
import elektro_borders as eb

def build_lut():
    lut = np.zeros((256, 3), np.uint8)                       # 0 -> black (space)
    for v in range(1, 15):                                    # red -> orange
        lut[v] = (255, round(v * 165 / 14), 0)
    for v in range(15, 28):                                   # yellow -> green
        lut[v] = (round(255 - (v - 15) * 255 / 12), 255, 0)
    for v in range(28, 41):                                   # blue -> cyan
        lut[v] = (0, round((v - 28) * 255 / 12), 255)
    for v in range(41, 256):                                  # inverted grey
        lut[v] = round(255 * (255 - v) / 214)
    return lut

def disk_mask(shape):
    # Raw 0 inside the Earth disk is saturated cold (cloud tops, limb), not space.
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    r_pol_px = eb.R_EQ_PX * eb.R_POL / eb.R_EQ
    return ((xx - eb.CX) / eb.R_EQ_PX) ** 2 + ((yy - eb.CY) / r_pol_px) ** 2 <= 1

def inner_mask(shape):
    # Stats only well inside the disk: the limb is noisy and saturates.
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    return (xx - eb.CX) ** 2 + (yy - eb.CY) ** 2 < (0.95 * eb.R_EQ_PX) ** 2

def rowcorr(a, b, m):
    a, b = np.where(m, a, np.nan), np.where(m, b, np.nan)
    a = a - np.nanmean(a, axis=1, keepdims=True); b = b - np.nanmean(b, axis=1, keepdims=True)
    return np.nansum(a*b, axis=1) / np.sqrt(np.nansum(a*a, axis=1) * np.nansum(b*b, axis=1) + 1e-9)

def runs(mask):
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(int), 0]))
    return list(zip(edges[::2], edges[1::2]))              # [y0, y1)

def bad_rows(a, b, m):
    """Rows of a and of b holding lost/garbage data, given the two IR channels."""
    valid = m.sum(axis=1) >= 200
    std = lambda x: np.nanstd(np.where(m, x, np.nan), axis=1)
    flat_a, flat_b = valid & (std(a) < 2), valid & (std(b) < 2)
    bad_a, bad_b = flat_a.copy(), flat_b.copy()

    # Rows where the two channels stop agreeing (normally r ~0.75-0.95)...
    dis = valid & ~flat_a & ~flat_b & (rowcorr(a, b, m) < 0.5)
    dis = np.convolve(dis, np.ones(7), 'same') > 0          # catch ragged strip edges
    # ...belong to the channel that breaks continuity with the rows around them:
    # a garbage strip is smooth inside but jumps at both ends, the good channel doesn't.
    cont = lambda x: np.r_[rowcorr(x[:-1], x[1:], m[:-1] & m[1:]), 0]   # row y vs y+1
    ca, cb = cont(a), cont(b)
    for y0, y1 in runs(dis & valid):
        edge = lambda c: np.mean([c[y] for y in (y0 - 1, y1 - 1) if 0 <= y < len(c)])
        ea, eb_ = edge(ca), edge(cb)
        if ea < 0.5 and eb_ < 0.5 and abs(ea - eb_) < 0.2:
            bad_a[y0:y1] = bad_b[y0:y1] = True
        elif ea < eb_:
            bad_a[y0:y1] = True
        else:
            bad_b[y0:y1] = True
    return bad_a, bad_b

def repair(raw, partner):
    """Return (repaired raw, no-data mask)."""
    m = inner_mask(raw.shape)
    a, b = raw.astype(float), partner.astype(float)
    bad, bad_p = bad_rows(a, b, m)

    # partner value -> median raw value, learned where both rows are good
    good = m & ~bad[:, None] & ~bad_p[:, None] & (a > 0) & (b > 0)
    pv, rv = partner[good], raw[good]
    xs = [v for v in range(256) if (pv == v).sum() >= 20]
    lut = np.interp(np.arange(256), xs, [np.median(rv[pv == v]) for v in xs]) if xs else None

    disk = disk_mask(raw.shape)
    out = raw.copy()
    fill = bad & ~bad_p
    if lut is not None:
        for y0, y1 in runs(fill):
            pred = lut[partner[y0:y1]]
            # The global mapping is off by a few counts locally (different
            # physics at 3.8 vs 10.7um), which shows as a seam. Measure the
            # per-column error on the good rows just above/below the band,
            # smooth it horizontally and blend it across the band.
            def edge_err(ys):
                ys = [y for y in ys if 0 <= y < raw.shape[0] and not bad[y] and not bad_p[y]]
                if not ys:
                    return None
                w = m[ys].astype(float)
                e = ((a[ys] - lut[partner[ys]]) * w).sum(0)
                k = np.ones(151)
                return np.convolve(e, k, 'same') / np.maximum(np.convolve(w.sum(0), k, 'same'), 1)
            top, bot = edge_err(range(y0 - 8, y0)), edge_err(range(y1, y1 + 8))
            if top is None: top = bot
            if bot is None: bot = top
            if top is not None:
                t = np.linspace(0, 1, y1 - y0)[:, None]
                pred = pred + (1 - t) * top + t * bot
            out[y0:y1] = np.where(disk[y0:y1], np.round(pred).clip(0, 255), 0).astype(np.uint8)
    nodata = (bad & (bad_p | (lut is None)))[:, None] & disk
    print(f"repaired {fill.sum() if lut is not None else 0} rows from partner, {nodata.any(axis=1).sum()} rows no data")
    return out, nodata

if __name__ == "__main__":
    ne_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(os.path.abspath(__file__))
    raw = np.asarray(Image.open(sys.argv[1]).convert("L"))
    nodata = np.zeros(raw.shape, bool)
    if len(sys.argv) > 4:
        raw, nodata = repair(raw, np.asarray(Image.open(sys.argv[4]).convert("L")))
    rgb = build_lut()[raw]
    rgb[(raw == 0) & disk_mask(raw.shape)] = (255, 0, 0)
    rgb[nodata] = 0
    eb.draw(Image.fromarray(rgb), ne_dir, style="ir").save(sys.argv[2])
    print(sys.argv[2])
