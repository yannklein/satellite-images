#!/usr/bin/env python3
"""Day/night blend for an Elektro-L3 natural-color image (GeoColor-style).
usage: elektro_night.py natural_color.png out.png ne_dir L3_9_*.png [L3_4_*.png]

The visible channels see nothing at night (only sensor noise and, around satellite
midnight, stray sunlight inside the telescope). Pixel by pixel, from the solar zenith
angle at scan time:
  day      -> the natural-color pixel, unchanged
  night    -> 10.7um IR clouds (cold = white) over NASA Black Marble 2016 city lights
  twilight -> smooth blend of the two (solar zenith 83..93 deg)
Applied only to night passes (21:30, 00:30, 03:30 UTC); other passes are left as is.
The clouds are this pass's real IR data; the city lights are a STATIC map
(VIIRS Day/Night Band composite, BlackMarble_2016_3km.jpg in ne_dir), reprojected
once to Elektro's view and cached as ne_dir/blackmarble_elektro.png. A caption says so.
IR lost-data strips are repaired from ch4 when given (elektro_ir_enhanced.repair);
rows lost in both IR channels stay black on the night side.
"""
import sys, os, re
from datetime import datetime, timezone
import warnings; warnings.simplefilter("ignore", RuntimeWarning)
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
Image.MAX_IMAGE_PIXELS = None
import elektro_borders as eb
import elektro_ir_enhanced as eir

SZA_DAY, SZA_NIGHT = 83.0, 93.0         # full day below, full night above (deg)
# Only night passes get the blend: the sun must be this far below the horizon at the
# sub-satellite point (0N 14.5W). All year that selects the 21:30, 00:30 and 03:30 UTC
# passes (sub-satellite solar zenith >= ~124 deg) and leaves 06:30 / 18:30 (<= ~99 deg)
# and the daytime passes as plain natural color.
SZA_PASS_MIN = 110.0

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

def black_marble(ne_dir, lat, lon, hit):
    cache = os.path.join(ne_dir, "blackmarble_elektro.png")
    if os.path.exists(cache):
        return np.asarray(Image.open(cache).convert("RGB"), dtype=np.float32) / 255
    src = Image.open(os.path.join(ne_dir, "BlackMarble_2016_3km.jpg")).convert("RGB")
    src = src.filter(ImageFilter.BoxBlur(1))                 # ~Elektro's 4 km pixel, no sparkle
    bm = np.asarray(src)
    h, w = bm.shape[:2]
    c = np.clip(((lon + 180) / 360 * w).astype(int), 0, w - 1)
    r = np.clip(((90 - lat) / 180 * h).astype(int), 0, h - 1)
    out = np.where(hit[..., None], bm[r, c], 0).astype(np.uint8)
    Image.fromarray(out).save(cache)
    return out.astype(np.float32) / 255

def ir_clouds(ir9, ir4, disk):
    raw = np.asarray(Image.open(ir9).convert("L"))
    nodata = np.zeros(raw.shape, bool)
    if ir4:
        raw, nodata = eir.repair(raw, np.asarray(Image.open(ir4).convert("L")))
    v = raw.astype(np.float32)
    inner = eir.inner_mask(raw.shape) & ~nodata
    vals = v[inner & (v > 0) & (v < 255)]
    warm, cold = np.percentile(vals, [90, 2])                # raw IR: warm = bright
    # the first ~12% above the warm reference is clear ground cooling at night, not cloud
    a = np.clip(((warm - v) / max(warm - cold, 1) - 0.12) / 0.88, 0, 1)
    a[(raw == 0) & disk] = 1                                 # saturated cold tops
    return a, nodata

def main(nat_path, out_path, ne_dir, ir9, ir4=None):
    day = np.asarray(Image.open(nat_path).convert("RGB"), dtype=np.float32) / 255
    m = re.search(r"(\d{8}T\d{6})Z", os.path.basename(ir9))
    t = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    sza0 = float(solar_zenith(np.array(0.0), np.array(eb.LON0), t))
    if sza0 < SZA_PASS_MIN:                                  # not a night pass: unchanged
        Image.fromarray((day * 255).astype(np.uint8)).save(out_path)
        print(f"{out_path}  not a night pass (sub-satellite solar zenith {sza0:.0f} deg), unchanged"); return
    lat, lon, hit = latlon(day.shape[:2])
    disk = hit & eir.disk_mask(day.shape[:2])
    sza = np.where(hit, solar_zenith(lat, lon, t), 180)
    w = np.clip((SZA_NIGHT - sza) / (SZA_NIGHT - SZA_DAY), 0, 1)
    w = w * w * (3 - 2 * w)                                  # smoothstep
    night_frac = (1 - w)[disk].mean()
    if night_frac < 0.02:                                   # full daylight: unchanged
        Image.fromarray((day * 255).astype(np.uint8)).save(out_path)
        print(f"{out_path}  day only"); return

    bg = black_marble(ne_dir, lat, lon, hit) ** 1.5          # darker ground, lights stay bright
    a, nodata = ir_clouds(ir9, ir4, disk)
    cloud = (0.30 + 0.65 * a)[..., None]                     # colder = brighter white
    night = bg * (1 - a[..., None]) + cloud * a[..., None]
    night[nodata] = 0

    out = w[..., None] * day + (1 - w[..., None]) * night
    out[~disk] = 0
    im = Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))

    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=38)
    except TypeError:
        font = ImageFont.load_default()
    d.text((40, 36), "Night side: this pass's 10.7 um IR clouds over", fill=(200, 200, 200), font=font)
    d.text((40, 82), "NASA Black Marble 2016 city lights (static map)", fill=(200, 200, 200), font=font)
    im.save(out_path)
    print(f"{out_path}  night {night_frac:.0%} of disk, sub-solar {subsolar(t)[0]:.1f}N {subsolar(t)[1]:.1f}E")

if __name__ == "__main__":
    main(*sys.argv[1:6])
