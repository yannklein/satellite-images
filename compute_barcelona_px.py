#!/usr/bin/env python3
"""
compute_barcelona_px.py — compute Barcelona's pixel position in each MSU-MR pass.
Adds 'barcelonaPx': {row, col, distKm} to each pass's meta.json.

Usage:
    python3 compute_barcelona_px.py [decoded_dir ...]
    # No args = process all decoded_* in current directory
"""

import sys, os, json, math, glob
import cbor2
from sgp4.api import Satrec

BARCELONA_LAT = 41.38
BARCELONA_LON = 2.17

def fix_tle(line):
    body = line[:-1]
    checksum = sum(int(c) if c.isdigit() else (1 if c == '-' else 0) for c in body) % 10
    return body + str(checksum)

def eci_to_latlon(r, ts):
    x, y, z = r
    jd = 2440587.5 + ts / 86400.0
    t_ut1 = (jd - 2451545.0) / 36525.0
    gmst = (67310.54841 + (876600*3600 + 8640184.812866)*t_ut1 +
            0.093104*t_ut1**2 - 6.2e-6*t_ut1**3) % 86400
    gmst_rad = gmst * 2 * math.pi / 86400.0
    xe = x * math.cos(-gmst_rad) - y * math.sin(-gmst_rad)
    ye = x * math.sin(-gmst_rad) + y * math.cos(-gmst_rad)
    ze = z
    lat = math.degrees(math.atan2(ze, math.sqrt(xe**2 + ye**2)))
    lon = math.degrees(math.atan2(ye, xe))
    return lat, lon

def compute_barcelona_pixel(cbor_path):
    with open(cbor_path, 'rb') as f:
        data = cbor2.load(f)

    tle_raw = data.get('tle')
    if not tle_raw:
        return None

    images = data.get('images', [])
    if not images:
        return None

    img0 = images[0]
    timestamps = img0.get('timestamps', [])
    ifov_y = img0.get('ifov_y', 1)
    if not timestamps:
        return None

    pcfg = data.get('projection_cfg', {})
    img_w = pcfg.get('corr_swath')   # actual image width in pixels (1 km/px)
    if not img_w:
        return None

    try:
        sat = Satrec.twoline2rv(fix_tle(tle_raw['line1']), fix_tle(tle_raw['line2']))
    except Exception:
        return None

    best_i, best_dist = None, 1e9
    best_lat = best_lon = None

    for i, ts in enumerate(timestamps):
        jd = 2440587.5 + ts / 86400.0
        try:
            e, r, _ = sat.sgp4(int(jd), jd - int(jd))
        except Exception:
            continue
        if e != 0:
            continue
        lat, lon = eci_to_latlon(r, ts)
        dist = abs(lat - BARCELONA_LAT)
        if dist < best_dist:
            best_dist = dist
            best_i = i
            best_lat, best_lon = lat, lon

    if best_i is None:
        return None

    # Cross-track distance: longitude difference projected at Barcelona's latitude
    half_swath = img_w / 2.0  # km = pixels (1 km/px)
    dlon_km = (BARCELONA_LON - best_lon) * 111.0 * math.cos(math.radians(BARCELONA_LAT))

    if abs(dlon_km) > half_swath + 100:
        return None  # Barcelona not in swath

    col = int((dlon_km / half_swath + 1.0) / 2.0 * img_w)
    col = max(0, min(img_w - 1, col))
    row = best_i * ifov_y

    # Great-circle distance for diagnostics
    dlat = math.radians(BARCELONA_LAT - best_lat)
    dlonr = math.radians(BARCELONA_LON - best_lon)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(best_lat)) * math.cos(math.radians(BARCELONA_LAT)) * math.sin(dlonr/2)**2
    dist_km = round(2 * math.asin(math.sqrt(a)) * 6371, 1)

    return {'row': row, 'col': col, 'distKm': dist_km}

def process_dir(decoded_dir):
    cbor_path = os.path.join(decoded_dir, 'MSU-MR', 'product.cbor')
    meta_path = os.path.join(decoded_dir, 'meta.json')

    if not os.path.exists(cbor_path):
        return False

    result = compute_barcelona_pixel(cbor_path)
    name = os.path.basename(decoded_dir)

    if result is None:
        print(f'  {name}: Barcelona not in frame or no TLE data')
        return False

    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    meta['barcelonaPx'] = result
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)

    print(f'  {name}: row={result["row"]}, col={result["col"]}, dist={result["distKm"]}km')
    return True

if __name__ == '__main__':
    dirs = sys.argv[1:] if len(sys.argv) > 1 else sorted(glob.glob('decoded_*'))
    dirs = [d for d in dirs if os.path.isdir(d)]
    print(f'Processing {len(dirs)} directories...')
    ok = sum(1 for d in dirs if process_dir(d))
    print(f'Done: {ok}/{len(dirs)} passes have Barcelona in frame')
