#!/usr/bin/env python3
"""Day/night blend for an Elektro-L3 natural-color image (GeoColor-style).
usage: elektro_night.py natural_color.png out.png ne_dir L3_9_*.png [L3_4_*.png]

The visible channels see nothing at night (only sensor noise and, around satellite
midnight, stray sunlight inside the telescope). Pixel by pixel, from the solar zenith
angle at scan time:
  day      -> the natural-color pixel, unchanged
  night    -> this pass's 10.7um IR clouds, dimmed and blue-grey ("moonlit"), over
              near-black ground
  twilight -> smooth blend of the two (solar zenith 83..93 deg)
Applied to every pass that has any night on the disk (incl. the half-lit 06:30 and
18:30 UTC ones), so the terminator sweeps smoothly through a movie instead of the
night style switching on and off. Everything shown is real data from this pass: no
static map layer (the earlier Black Marble city lights were dropped as a visible
discontinuity), and no caption burned in (elektro-movie.html / index.html say it).
IR lost-data strips are repaired from ch4 when given (elektro_ir_enhanced.repair);
rows lost in both IR channels stay black on the night side.
"""
import sys, os, re
from datetime import datetime, timezone
import warnings; warnings.simplefilter("ignore", RuntimeWarning)
import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
import elektro_borders as eb
import elektro_ir_enhanced as eir

SZA_DAY, SZA_NIGHT = 83.0, 93.0         # full day below, full night above (deg)
# Fixed raw 10.7um count references (raw IR: warm = bright). Measured on night disks
# Nov 2026: p90 ~242, p2 ~11, stable frame to frame. Fixed rather than per-frame
# percentiles so night cloud brightness doesn't flicker through a movie (half-lit
# passes' percentiles are skewed by warm sunlit ground).
IR_WARM, IR_COLD = 243.0, 10.0
GROUND = np.array([0.012, 0.016, 0.026], np.float32)   # night ground: near-black, cool
MOONLIT = np.array([0.74, 0.80, 0.94], np.float32)     # night cloud tint

def latlon(shape):
    """Geodetic lat/lon (deg) of every pixel, inverse of elektro_borders.project."""
    rows, cols = np.mgrid[:shape[0], :shape[1]].astype(np.float64)
    x = (cols - eb.CX) / eb.PX_PER_RAD                       # scan angles (rad)
    y = (eb.CY - rows) / eb.PX_PER_RAD
    a, b = eb.R_EQ, eb.R_POL
    # ray from the satellite S=(H,0,0): P = (H - t cy cx, t cy sx, t sy)
    dx, dy, dz = -np.cos(y) * np.cos(x), np.cos(y) * np.sin(x), np.sin(y)
    A = (dx**2 + dy**2) / a**2 + dz**2 / b**2
    B = 2 * eb.H * dx / a**2
    C = eb.H**2 / a**2 - 1
    disc = B**2 - 4 * A * C
    hit = disc >= 0
    t = (-B - np.sqrt(np.where(hit, disc, 0))) / (2 * A)
    px, py, pz = eb.H + t * dx, t * dy, t * dz
    lon = np.degrees(np.arctan2(py, px)) + eb.LON0
    lon = (lon + 180) % 360 - 180
    lat = np.degrees(np.arctan((a**2 / b**2) * pz / np.sqrt(px**2 + py**2)))
    return lat, lon, hit

def subsolar(t):
    """Sub-solar point (lat, lon deg) at UTC datetime t (NOAA low-precision formulas)."""
    n = (t - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400
    L = np.radians((280.460 + 0.9856474 * n) % 360)
    g = np.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + np.radians(1.915) * np.sin(g) + np.radians(0.020) * np.sin(2 * g)
    eps = np.radians(23.439 - 4e-7 * n)
    dec = np.arcsin(np.sin(eps) * np.sin(lam))
    ra = np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam))
    gmst = np.radians((280.46061837 + 360.98564736629 * n) % 360)
    lon = (np.degrees(ra - gmst) + 180) % 360 - 180
    return np.degrees(dec), lon

def solar_zenith(lat, lon, t):
    slat, slon = np.radians(subsolar(t))
    la, ha = np.radians(lat), np.radians(lon) - slon
    cz = np.sin(la) * np.sin(slat) + np.cos(la) * np.cos(slat) * np.cos(ha)
    return np.degrees(np.arccos(np.clip(cz, -1, 1)))

def ir_clouds(ir9, ir4, disk):
    raw = np.asarray(Image.open(ir9).convert("L"))
    nodata = np.zeros(raw.shape, bool)
    if ir4:
        raw, nodata = eir.repair(raw, np.asarray(Image.open(ir4).convert("L")))
    v = raw.astype(np.float32)
    # the first ~12% above the warm reference is clear ground cooling at night, not cloud
    a = np.clip(((IR_WARM - v) / (IR_WARM - IR_COLD) - 0.12) / 0.88, 0, 1)
    a[(raw == 0) & disk] = 1                                 # saturated cold tops
    return a, nodata

def main(nat_path, out_path, ne_dir, ir9, ir4=None):
    day = np.asarray(Image.open(nat_path).convert("RGB"), dtype=np.float32) / 255
    m = re.search(r"(\d{8}T\d{6})Z", os.path.basename(ir9))
    t = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    lat, lon, hit = latlon(day.shape[:2])
    disk = hit & eir.disk_mask(day.shape[:2])
    sza = np.where(hit, solar_zenith(lat, lon, t), 180)
    w = np.clip((SZA_NIGHT - sza) / (SZA_NIGHT - SZA_DAY), 0, 1)
    w = w * w * (3 - 2 * w)                                  # smoothstep
    night_frac = (1 - w)[disk].mean()
    if night_frac < 0.02:                                   # full daylight: unchanged
        Image.fromarray((day * 255).astype(np.uint8)).save(out_path)
        print(f"{out_path}  day only"); return

    a, nodata = ir_clouds(ir9, ir4, disk)
    a3 = a[..., None]
    # colder tops = brighter; thin/low cloud fades into the dark ground
    night = GROUND * (1 - a3) + MOONLIT * (0.18 + 0.52 * a3) * a3
    night[nodata] = 0

    out = w[..., None] * day + (1 - w[..., None]) * night
    out[~disk] = 0
    im = Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))
    im.save(out_path)
    print(f"{out_path}  night {night_frac:.0%} of disk, sub-solar {subsolar(t)[0]:.1f}N {subsolar(t)[1]:.1f}E")

if __name__ == "__main__":
    main(*sys.argv[1:6])
